"""Render review evidence from saved metrics (optional Pillow dependency)."""
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from verify_results import document,DATA

fresh=document('parlay-eval-fresh-joint','summary.json')
expanded=document('parlay-eval-fresh-singles-expanded','summary.json')
diagnostic=document('parlay-eval-fresh-singles-expanded','diagnostics.json')
font_path='/System/Library/Fonts/Supplemental/Arial.ttf'
def font(size):return ImageFont.truetype(font_path,size)
img=Image.new('RGB',(1500,870),'#f7f8fa');d=ImageDraw.Draw(img)
d.text((55,45),'Two-leg forecasts: no established market advantage',font=font(38),fill='#172337')
d.text((55,104),'Existing development games | Brier: lower is better | No live Combo-price or P&L claim',font=font(23),fill='#58687c')
rows=[('Fresh direct joint',fresh['results']['joint']['brier'],fresh['results']['market']['brier'],'Only 7/72 joint outputs valid'),
 ('Fresh single products',fresh['results']['single_product']['brier'],fresh['results']['market']['brier'],'Small gain; interval includes zero'),
 ('Expanded singles: frozen primary',expanded['aggregates']['additional_primary']['equal_phase_metrics']['single_product']['brier'],
 expanded['aggregates']['additional_primary']['equal_phase_metrics']['market']['brier'],'2,146 added combinations; failures retained'),
 ('Expanded singles: fallback diagnostic',diagnostic['invalid_leg_market_fallback']['model_equal_phase_brier'],
 diagnostic['invalid_leg_market_fallback']['market_same_cases_equal_phase_brier'],'Post-hoc only; interval includes zero')]
d.rectangle((55,170,1445,225),fill='#172337')
for x,label in [(75,'Experiment'),(775,'Model'),(1020,'Market')]:d.text((x,183),label,font=font(25),fill='white')
for i,(label,model,market,note) in enumerate(rows):
 y=225+i*112;d.rectangle((55,y,1445,y+112),fill='white' if i%2==0 else '#eaf0f5')
 d.text((75,y+19),label,font=font(25),fill='#172337')
 d.text((75,y+62),note,font=font(20),fill='#58687c')
 d.text((775,y+25),f'{model:.4f}',font=font(31),fill='#172337')
 d.text((1020,y+25),f'{market:.4f}',font=font(31),fill='#172337')
d.text((55,712),'Estimated full-rate token cost: $0.1006 across both paid runs; each below its $5 budget.',font=font(24),fill='#172337')
d.text((55,756),'Protected-cohort guards unchanged. Additional combinations heavily reuse the same games.',font=font(22),fill='#58687c')
d.text((55,800),'Protocols, original completions, receipt scores and independent offline verification are published.',font=font(22),fill='#58687c')
img.save(DATA/'result-summary.png')
