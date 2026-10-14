"""Render a factual event-location replay and as-of Polymarket prices to H.264."""
from pathlib import Path
import bisect, collections, datetime as dt, json, math, re, subprocess, sys
import os, shutil
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(os.environ.get('RIN_REPLAY_OUTPUT', ROOT / 'artifacts' / 'world-cup-replay'))
HERE.mkdir(parents=True, exist_ok=True)
RAW = ROOT / 'data' / 'examples' / 'world-cup' / 'czechia-south-africa'
W, H, FPS = 1600, 900, 24
BG = '#F3F0E7'; INK = '#172F34'; MUTED = '#697A79'; LINE = '#D5DBD2'
BLUE = '#2863C3'; ORANGE = '#C06933'; DRAW = '#758583'; WHITE = '#FCFBF5'
def font_path(bold=False, mono=False):
    mac = Path('/System/Library/Fonts/Supplemental')
    face = 'Andale Mono.ttf' if mono else 'Arial Bold.ttf' if bold else 'Arial.ttf'
    if (mac / face).exists():
        return str(mac / face)
    face = 'DejaVuSansMono.ttf' if mono else 'DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'
    for folder in ['/usr/share/fonts/truetype/dejavu', '/usr/share/fonts/dejavu']:
        path = Path(folder) / face
        if path.exists():
            return str(path)
    raise RuntimeError('Install DejaVu Sans and DejaVu Sans Mono fonts to render the replay.')
fonts = {}
def font(size, bold=False, mono=False):
    key = (size, bold, mono)
    if key not in fonts:
        fonts[key] = ImageFont.truetype(font_path(bold, mono), size)
    return fonts[key]
def ts(s): return dt.datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
def utc(t): return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%H:%M:%S')
def label(d, xy, text, size=18, color=INK, bold=False, mono=False, anchor=None):
    d.text(xy, text, font=font(size,bold,mono), fill=color, anchor=anchor)
def wrap(d, text, xy, width, size=22, color=INK, bold=False, limit=4):
    lines=[]; line=''
    for word in text.split():
        test=(line+' '+word).strip()
        if d.textlength(test,font=font(size,bold))>width and line:
            lines.append(line);line=word
        else:line=test
    if line:lines.append(line)
    for i,line in enumerate(lines[:limit]): label(d,(xy[0],xy[1]+i*(size+7)),line,size,color,bold)
    return len(lines[:limit])*(size+7)
def load(name): return json.loads((RAW/(name+'.json')).read_text())

events = load('fifa_400021440_timeline')['Event']
for e in events:
    e['_t']=ts(e['Timestamp']);e['_type']=e['TypeLocalized'][0]['Description']
    e['_desc']=next((s['Description'] for s in e['EventDescription']), e['_type'])
    e['_color']=BLUE if e.get('IdTeam')=='43995' else ORANGE if e.get('IdTeam')=='43883' else MUTED
etimes=[e['_t'] for e in events]
starts=[e['_t'] for e in events if e['_type']=='Start Time']
half_end=next(e['_t'] for e in events if e['_type']=='End Time')
end=events[-1]['_t'];start=starts[0]-3;finish=end+40
markets=load('fifa_poly_400021440')['markets']
prices=[load('fifa_prices_'+m['id'])['history'] for m in markets]
ptimes=[[p['t'] for p in ps] for ps in prices]
names=['CZECHIA','DRAW','SOUTH AFRICA'];colors=[BLUE,DRAW,ORANGE]
grouped=collections.defaultdict(list)
for e in events:
    if e['_t']>=start:grouped[e['_t']].append(e)
priority={'Goal!':100,'Penalty Goal':100,'Match end':90,'Start Time':80,'End Time':80,'Attempt at Goal':60,'Yellow card':50}
segments=[];movie=0.0
def segment(duration,a,b):
    global movie
    segments.append((movie,movie+duration,a,b));movie+=duration
segment(3,start,start)
prev=start
for t,es in sorted(grouped.items()):
    segment(max(.12,min(3.4,(t-prev)/110)),prev,t)
    typ=max(es,key=lambda e:priority.get(e['_type'],0))['_type']
    segment(3 if typ in ['Goal!','Penalty Goal'] else 1.2 if typ in ['Start Time','End Time','Match end'] else .52,t,t)
    prev=t
segment(1.4,prev,finish);segment(4,finish,finish)
duration=movie
segment_ends=[s[1] for s in segments]

def replay_time(mt):
    s=segments[min(bisect.bisect_right(segment_ends,mt),len(segments)-1)]
    q=min(1,max(0,(mt-s[0])/(s[1]-s[0])))
    return s[2]+q*(s[3]-s[2])

# Paper texture and pitch are rendered once; no synthetic movement is added.
rng=np.random.default_rng(23)
base_rgb=np.array([243,240,231],dtype=np.int16)
grain=rng.normal(0,.65,(H,W,1))
base=Image.fromarray(np.uint8(np.clip(base_rgb+grain,0,255)))
d=ImageDraw.Draw(base)
label(d,(48,25),'FIELD / FORECAST',16,INK,True)
label(d,(1552,25),'WORLD CUP 2026   /   18 JUN   /   GROUP A',14,MUTED,mono=True,anchor='ra')
label(d,(48,59),'Czechia',47,BLUE,True)
label(d,(250,73),'×',30,MUTED)
label(d,(293,59),'South Africa',47,ORANGE,True)
label(d,(49,115),'Recorded actions on the pitch. Market expectations through time.',18,MUTED)
d.line((48,258,1552,258),fill=LINE,width=1)
label(d,(48,274),'01  /  PITCH EVENTS',14,INK,True)
label(d,(936,274),'Raw FIFA coordinates  ·  no inferred movement',13,MUTED,anchor='ra')
label(d,(990,274),'02  /  MATCH FEED',14,INK,True)

PX,PY,PW,PH=76,320,840,544
def point(e):return PX+float(e['PositionX'])/100*PW,PY+float(e['PositionY'])/100*PH
field='#E7ECE0';fieldline='#A7B6A7'
d.rectangle((PX,PY,PX+PW,PY+PH),fill=field,outline=fieldline,width=2)
for i in range(10):
    if i%2==0:d.rectangle((PX+i*PW/10,PY+2,PX+(i+1)*PW/10,PY+PH-2),fill='#E2E9DA')
d.rectangle((PX,PY,PX+PW,PY+PH),outline=fieldline,width=2)
d.line((PX+PW/2,PY,PX+PW/2,PY+PH),fill=fieldline,width=2)
r=PW*9.15/105;cx=PX+PW/2;cy=PY+PH/2
d.ellipse((cx-r,cy-r,cx+r,cy+r),outline=fieldline,width=2)
d.ellipse((cx-3,cy-3,cx+3,cy+3),fill=fieldline)
for side in [0,1]:
    x=PX if side==0 else PX+PW;sign=1 if side==0 else -1
    for dep,hei in [(16.5,40.32),(5.5,18.32)]:
        xx=x+sign*PW*dep/105;hh=PH*hei/68
        d.rectangle((min(x,xx),cy-hh/2,max(x,xx),cy+hh/2),outline=fieldline,width=2)
    pen=x+sign*PW*11/105;d.ellipse((pen-3,cy-3,pen+3,cy+3),fill=fieldline)
    goal=PH*7.32/68;gx=x-sign*12
    d.rectangle((min(x,gx),cy-goal/2,max(x,gx),cy+goal/2),outline=fieldline,width=2)
label(d,(PX+12,PY+12),'EVENT LOCATIONS',11,MUTED,mono=True)
label(d,(PX+PW-12,PY+PH-20),'Not continuous player tracking',12,MUTED,anchor='ra')
label(d,(48,881),'SOURCES  FIFA timeline + Polymarket price history',11,MUTED,mono=True)
label(d,(1552,881),'EVENT-TIMED REPLAY  /  NONLINEAR TIME COMPRESSION',11,MUTED,mono=True,anchor='ra')

def symbol(draw,x,y,typ,color,r=7,width=2):
    if typ=='Foul':
        draw.line((x-r,y-r,x+r,y+r),fill=color,width=width)
        draw.line((x-r,y+r,x+r,y-r),fill=color,width=width)
    elif typ=='Corner': draw.polygon([(x,y-r),(x+r,y),(x,y+r),(x-r,y)],outline=color,width=width)
    elif typ=='Yellow card':draw.rectangle((x-r*.6,y-r,x+r*.6,y+r),fill='#B5942E')
    elif typ in ['Goal!','Penalty Goal']:
        pts=[]
        for k in range(10):
            a=k*math.pi/5-math.pi/2;rr=r if k%2==0 else r*.44;pts.append((x+rr*math.cos(a),y+rr*math.sin(a)))
        draw.polygon(pts,fill=color)
    else:draw.ellipse((x-r,y-r,x+r,y+r),outline=color,width=width)

def actor(e):
    text=e['_desc']
    if text.startswith('Assisted by '):return text.removeprefix('Assisted by ').rstrip('.').title()
    name=text.split(' (')[0]
    if len(name)>34 or not e.get('IdPlayer'):return ''
    return name.title()

def frame(mt):
    t=replay_time(mt);im=base.copy();d=ImageDraw.Draw(im)
    idx=bisect.bisect_right(etimes,t)-1
    past=events[:idx+1]
    curr=past[-1] if past else events[0]
    same=[e for e in past if e['_t']==curr['_t']]
    curr=max(same,key=lambda e:priority.get(e['_type'],0)) if same else curr
    final=t>=end
    phase='FULL TIME' if final else 'SECOND HALF' if t>=starts[1] else 'HALF TIME' if t>=half_end else 'FIRST HALF' if t>=starts[0] else 'KICKOFF'
    score=f"{curr['HomeGoals']}  :  {curr['AwayGoals']}"
    label(d,(1552,54),score,48,INK,True,mono=True,anchor='ra')
    label(d,(1552,110),phase+'   /   '+utc(t)+' UTC',14,MUTED,mono=True,anchor='ra')
    # Raw market probabilities are independent and deliberately not normalized.
    for j in range(3):
        x=48+j*507;col=colors[j];pidx=bisect.bisect_right(ptimes[j],t)-1
        p=prices[j][pidx] if pidx>=0 else None
        d.rounded_rectangle((x,153,x+490,242),radius=12,fill=WHITE,outline=LINE,width=1)
        d.rounded_rectangle((x,153,x+5,242),radius=2,fill=col)
        label(d,(x+20,166),names[j]+'  /  WIN' if j!=1 else 'DRAW  /  REGULATION',13,col,True)
        label(d,(x+20,188),f"{p['p']*100:.1f}%" if p else '—',35,col,True)
        label(d,(x+469,173),'POLYMARKET',10,MUTED,mono=True,anchor='ra')
        label(d,(x+469,204),f"sample {utc(p['t'])} · {int(t-p['t'])}s old" if p else 'no preceding sample',12,MUTED,anchor='ra')
    # Prior events persist faintly as history, with no connecting trajectories.
    overlay=Image.new('RGBA',(W,H),(0,0,0,0));od=ImageDraw.Draw(overlay)
    for e in past:
        if e.get('PositionX') is None or e.get('PositionY') is None:continue
        x,y=point(e);age=t-e['_t'];rgb=tuple(int(e['_color'][k:k+2],16) for k in [1,3,5])
        alpha=190 if e['_type'] in ['Goal!','Penalty Goal'] else max(28,int(120*math.exp(-age/250)))
        symbol(od,x,y,e['_type'],rgb+(alpha,),r=9 if e['_type']=='Goal!' else 4,width=2)
    im=Image.alpha_composite(im.convert('RGBA'),overlay).convert('RGB');d=ImageDraw.Draw(im)
    current_loc=curr.get('PositionX') is not None and curr.get('PositionY') is not None
    if current_loc:
        x,y=point(curr);col=curr['_color'];pulse=19+5*math.sin(mt*4)
        d.ellipse((x-pulse,y-pulse,x+pulse,y+pulse),outline=col,width=2)
        d.ellipse((x-12,y-12,x+12,y+12),fill=WHITE)
        symbol(d,x,y,curr['_type'],col,10,3)
        name=actor(curr) or curr['_type'];tw=d.textlength(name,font=font(17,True))+24
        lx=min(PX+PW-tw-8,max(PX+8,x-tw/2));ly=y+29 if y<PY+PH-70 else y-53
        d.rounded_rectangle((lx,ly,lx+tw,ly+31),radius=6,fill=WHITE,outline=LINE)
        label(d,(lx+12,ly+6),name,17,col,True)
    else:
        notice='No pitch location in the source for this event'
        if curr['_type'] in ['Start Time','End Time','Match end','Coin Toss']:notice='Period boundary · no player position recorded'
        d.rounded_rectangle((PX+150,PY+PH-65,PX+PW-150,PY+PH-35),radius=6,fill=WHITE)
        label(d,(PX+PW/2,PY+PH-60),notice,13,MUTED,anchor='ma')
    # Current event and a compact chronological feed.
    x=990;minute=curr['MatchMinute'] or 'HT'
    label(d,(x,304),minute.replace("'",'′'),42,INK,True)
    label(d,(1552,317),utc(curr['_t'])+' UTC',14,MUTED,mono=True,anchor='ra')
    typ={'Attempt at Goal':'SHOT','Goal!':'GOAL','Penalty Goal':'PENALTY GOAL','Goal Prevention':'SAVE'}.get(curr['_type'],curr['_type'].upper())
    label(d,(x,363),typ,24,curr['_color'],True)
    wrap(d,curr['_desc'],(x,401),550,21,INK,False,3)
    label(d,(x,490),'RECENT ACTIONS',11,MUTED,True)
    for k,e in enumerate(list(reversed(past))[:4]):
        yy=515+k*28;label(d,(x,yy),e['MatchMinute'] or 'HT',12,MUTED,mono=True)
        text=(actor(e)+' · ' if actor(e) else '')+e['_type']
        while d.textlength(text,font=font(14))>455:text=text[:-2]
        label(d,(x+95,yy),text,14,e['_color'])
    # Step history: show samples only after their own timestamp.
    label(d,(x,647),'03  /  MARKET HISTORY',14,INK,True)
    gx,gy,gw,gh=x+35,690,510,136
    for val in [0,.5,1]:
        yy=gy+gh*(1-val);d.line((gx,yy,gx+gw,yy),fill=LINE,width=1)
        label(d,(gx-9,yy-6),str(int(val*100)),10,MUTED,mono=True,anchor='ra')
    def chartx(tt):return gx+gw*(tt-start)/(finish-start)
    hx1,hx2=chartx(half_end),chartx(starts[1])
    d.rectangle((hx1,gy,hx2,gy+gh),fill='#E4E5DC')
    label(d,((hx1+hx2)/2,gy+5),'HT',10,MUTED,anchor='ma')
    for j,ps in enumerate(prices):
        q=bisect.bisect_right(ptimes[j],t);seen=[p for p in ps[:q] if p['t']>=start]
        # Include the preceding sample at chart origin without future interpolation.
        initial=bisect.bisect_right(ptimes[j],start)-1
        if initial>=0:seen=[{'t':start,'p':ps[initial]['p']}]+seen
        pts=[]
        for p in seen:
            xx=chartx(p['t']);yy=gy+gh*(1-p['p'])
            if pts:pts.append((xx,pts[-1][1]))
            pts.append((xx,yy))
        if pts:
            pts.append((chartx(t),pts[-1][1]));d.line(pts,fill=colors[j],width=3)
    xt=chartx(t);d.line((xt,gy-4,xt,gy+gh+4),fill=INK,width=1)
    label(d,(gx,gy+gh+9),'KICKOFF',10,MUTED,mono=True)
    label(d,(gx+gw,gy+gh+9),'FULL TIME',10,MUTED,mono=True,anchor='ra')
    # Data-encoding legend stays legible above the field.
    for xx,kind,word in [(PX+12,'Attempt at Goal','shot / action'),(PX+210,'Foul','foul'),(PX+333,'Corner','corner'),(PX+480,'Goal!','goal')]:
        symbol(d,xx,PY-15,kind,MUTED,5,2);label(d,(xx+12,PY-22),word,12,MUTED)
    # Short opening and closing captions, never obscuring the pitch.
    if mt<2.8:
        d.rounded_rectangle((1020,697,1510,801),radius=12,fill=INK)
        label(d,(1040,714),'A match, reconstructed from events.',22,WHITE,True)
        label(d,(1040,753),'Player markers appear only at observed actions.',16,'#BDCFC9')
    if final:
        label(d,(x,612),'FINAL RESULT  ·  1–1 DRAW',17,INK,True)
    return im

def main():
    goal_times=[e['_t'] for e in events if e['_type'] in ['Goal!','Penalty Goal']]
    for i,gt in enumerate(goal_times):
        target=next((s[0]+min(.7,(s[1]-s[0])/2) for s in segments if s[2]==gt and s[3]==gt),duration/2)
        frame(target).save(HERE/f'preview-goal-{i+1}.png')
    frame(1.8).save(HERE/'poster.png')
    if '--preview' in sys.argv:
        print('Previews generated. Planned duration:',round(duration,2));return
    out=HERE/'czechia-south-africa-replay.mp4'
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg: raise RuntimeError('Install FFmpeg with libx264 support to render a video.')
    cmd=[ffmpeg,'-y','-hide_banner','-loglevel','error','-f','rawvideo','-vcodec','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','-','-an','-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p','-movflags','+faststart',str(out)]
    proc=subprocess.Popen(cmd,stdin=subprocess.PIPE)
    total=math.ceil(duration*FPS)
    for i in range(total):
        proc.stdin.write(frame(i/FPS).tobytes())
        if i%(FPS*10)==0:print(f'Rendered {i//FPS}s / {duration:.1f}s',flush=True)
    proc.stdin.close()
    if proc.wait()!=0:raise RuntimeError('ffmpeg failed')
    (HERE/'render-manifest.json').write_text(json.dumps({'match_id':'400021440','duration_seconds':total/FPS,'fps':FPS,'resolution':[W,H],'events':len(events),'coordinate_events':sum(e.get('PositionX') is not None for e in events),'market_ids':[m['id'] for m in markets],'time_mapping':'UTC replay with compressed gaps and event holds','position_policy':'Only observed raw FIFA event coordinates; no inferred positions or movement','price_policy':'Latest sample at or before current replay UTC; no future interpolation','audio':False},indent=2))
    print(out)

if __name__=='__main__':main()
