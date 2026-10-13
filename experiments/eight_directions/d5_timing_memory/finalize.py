"""Finalize frozen scoring plus explicit provenance/limitation annotations."""
import json, statistics
from collections import Counter
from .analyze import run as analyze,bootstrap
from .verify import run as verify
from .study import OUT,write

def run():
    analyze();verify()
    rows=json.loads((OUT/'match_metrics.json').read_text());result=json.loads((OUT/'results.json').read_text());ids=result['cohort'];supp={}
    # Supplementary baseline uncertainty, using the already-declared market metrics.
    for view in ['original_sparse_checkpoints','shared_minute_grid']:
        supp[view]={}
        for arm in result['forecast_summaries'][view]:
            supp[view][arm]={}
            for metric in ['brier','log_loss']:
                delta=[]
                for mid in ids:
                    rr=[r for r in rows if r['match_id']==mid and r['arm']==arm and r['view']==view]
                    delta.append(statistics.mean(r[metric]-r['market_'+metric] for r in rr))
                supp[view][arm][metric]=bootstrap(delta)
    predictions=[json.loads(x) for x in (OUT/'predictions.jsonl').read_text().splitlines()]
    audit={}
    for arm in sorted(set(r['arm'] for r in predictions)):
        rr=[r for r in predictions if r['arm']==arm];c=Counter(json.dumps(r['probs'],sort_keys=True) for r in rr)
        audit[arm]={'predictions':len(rr),'distinct_forecasts':len(c),'most_common':c.most_common(5),'exact_schema_example_rate':sum(r['probs']=={'home':.4,'draw':.3,'away':.3} for r in rr)/len(rr)}
    write('output_audit.json',audit)
    result['output_audit']=audit
    result['interpretation']='Inconclusive cadence/memory efficacy:97.4% of minute predictions,98.75% sparse controls and100% memory repeat numeric schema example(.4,.3,.3). Validity100% does not imply useful forecasting. All model arms worse than market. Consistent with example anchoring or output collapse under compact prompt; causal mechanism untested, no paid follow-up.'
    trade_rows=json.loads((OUT/'execution_receipts.json').read_text());concentration=[]
    for mid in ids:
        def bm(a):return statistics.mean(r['brier'] for r in rows if r['match_id']==mid and r['view']=='shared_minute_grid' and r['arm']==a)
        def pm(a):return statistics.mean(r['pnl'] for r in trade_rows if r['match_id']==mid and r['arm']==a)
        concentration.append({'match_id':mid,'cadence_brier_delta':bm('minute_no_memory')-bm('sparse_no_memory'),'memory_brier_delta':bm('sparse_memory')-bm('sparse_no_memory'),'cadence_pnl_delta':pm('minute_no_memory')-pm('sparse_no_memory')})
    write('effect_concentration.json',concentration)
    result['effect_concentration']=concentration
    write('results.json',result)
    write('market_comparison.json',{'label':'Supplementary uncertainty for predeclared contemporaneous market baseline; computed after primary analysis, no model/intervention selection.','paired_vs_market':supp})
    report=OUT/'REPORT.md'
    body=report.read_text().replace('memory collection cadence stays sparse at sparse cadence','memory collection uses only original sparse checkpoints')
    note='Observed forecasts largely repeat the numeric schema example: sparse control79/80 (98.75%), sparse memory80/80 (100%), minute1163/1194 (97.4%) output exactly home0.40/draw0.30/away0.30. This is consistent with example anchoring or output collapse after switching to a compact forecast-only prompt; the mechanism is untested. Validity100% does not establish useful forecasting. Minute Brier0.6272 is far worse than contemporaneous market0.3224 (paired excess0.30476;95%CI[0.14450,0.44489]). The efficacy of cadence/memory remains unresolved, and no profitability/generalization follows from the positive fixed-executor pilot P&L.'
    body=body.replace('No checkpoint training or protected evaluation.\n','No checkpoint training or protected evaluation.\n\n'+note+'\n',1)
    report.write_text(body)
    with report.open('a') as f:
        f.write('\nMetadata correction: France–Iraq is match `400021492`, with a source halftime gap of 7912.041 seconds (131.87 minutes); the frozen protocol limitation accidentally wrote `400021511`, which is Panama–Croatia. `timing_metadata_correction.json` and `timing_audit.json` document this label correction. Both games remain included in every arm; cohort and scoring are unchanged.\n')
        f.write('\nBudget amendment: latest user cap is $5 combined across the entire task. This frozen direction adds maximum-token cost $0.243916 and durable doubled reservations $0.487832; no additional paid work is undertaken. The root aggregate ledger governs the combined cap.\n')
        f.write('\nEffect concentration: all nonzero Brier differences come from Portugal–Uzbekistan `400021503`; all net P&L differences come from Norway–Senegal `400021491`. Seven of eight match-level differences are exactly zero for each main effect. This reinforces the small-pilot uncertainty; these matches were never selected or excluded after seeing results.\n')
        f.write('\nNext immediate free design check: audit the checkpoint on the original observation template and inspect template-example repetition. A future approved matched pilot should remove numeric schema examples or use the original supported template before spending on more cadence/memory inference; no additional calls were made here.\n')
        f.write('\nMemory intervention also adds a history label and increases prompt length, including an empty history at the first point. This pilot tests the complete compact-history prompt intervention; it does not isolate content from instruction/length effects.\n')
        f.write('\nSupplementary match-cluster uncertainty against the contemporaneous market baseline is in `market_comparison.json`; negative score deltas favor the model. Baseline comparison does not change the two primary contrasts or classifications.\n')
        f.write('\nComplete offline reproduction, including annotations and verification: `PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m experiments.eight_directions.d5_timing_memory.finalize`.\n')
if __name__=='__main__':run()
