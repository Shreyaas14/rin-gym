"""Render verified bracket replay results without further model calls."""
from collections import Counter
import hashlib
import json
from experiments.forecast_only.bracket_original import OUT


def main():
    p=json.loads((OUT/'protocol.json').read_text())
    r=json.loads((OUT/'summary.json').read_text())
    v=json.loads((OUT/'verification.json').read_text());assert v['passed']
    preds=[json.loads(x) for x in (OUT/'predictions.jsonl').read_text().splitlines()]
    triggers=Counter(x['execution_rejection'] for x in preds if x['execution_rejection'])
    overrides=sum(x['advisory_action']!=x['executed_action'] for x in preds)
    r['bracket_execution']={'trigger_counts':dict(triggers),'overridden_model_actions':overrides}
    (OUT/'summary.json').write_text(json.dumps(r,indent=2)+'\n')
    base=r['summaries']['original_one_share_previous_fresh'];new=r['summaries']['original_bracket_fresh']
    ci=r['paired_pnl']['original_one_share_previous_fresh']['match_cluster_bootstrap_95']
    lines=['# Fresh closed-loop bracket-exit replay','',
        f"The new policy returned ${new['total_pnl']:.2f} net P&L with a {new['win_rate_traded']:.1%} win rate, compared with ${base['total_pnl']:.2f} and {base['win_rate_traded']:.1%} for the preceding verified original-policy replay.", '',
        '| Policy | Net P&L | Winning / traded cases | Win rate | Average win | Average loss | Worst case |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for label,s in [('Original, previous fresh replay',base),('Fresh combined bracket exits',new)]:
        lines.append(f"| {label} | ${s['total_pnl']:.2f} | {s['winning_cases']}/{s['traded_cases']} | {s['win_rate_traded']:.1%} | ${s['average_win']:.4f} | ${s['average_loss']:.4f} | ${s['worst_case_pnl']:.4f} |")
    lines += ['',f"Net P&L change: ${new['total_pnl']-base['total_pnl']:.2f}. Win-rate change: {100*(new['win_rate_traded']-base['win_rate_traded']):+.2f} percentage points. The paired 95% game-cluster bootstrap interval for total P&L change is ${216*ci[0]:.2f} to ${216*ci[1]:.2f}; for win-rate change it is {100*r['win_rate_difference']['match_cluster_bootstrap_95'][0]:+.2f} to {100*r['win_rate_difference']['match_cluster_bootstrap_95'][1]:+.2f} percentage points.", '',
        '## Fixed experiment design','',p['design'],'',
        'Both runs use one-share positions, the same trained checkpoint, the same 72 development games × three repeats, matched sampling seeds, temperature 1 and a 256-token output limit. The original baseline was reused, not resampled in this experiment. All 1,077 responses for the new arm are fresh. Model instructions are unchanged; the bracket is enforced externally, with executed positions passed into the next prompt. No extra dialogue memory or entry filters were introduced.', '',
        'A gain is displayed net selling proceeds after spread and fees minus the actual all-in entry cost. The +10¢ and −25¢ thresholds are checked only at available checkpoints. Orders fill at recorded delayed quotes, so exit outcomes can differ from the trigger price. Threshold exits override switches as well as holds; later re-entry is permitted and resets the entry cost. Normal model exits remain available when neither threshold triggers.', '',
        f"Triggers: {dict(triggers)}. Model actions overridden: {overrides}. Output validity: {r['valid_rate']:.1%}.", '',
        '## Cost and safeguards','',
        f"Logged-token cost estimate: ${r['usage']['full_rate_cost_estimate_usd']:.4f}. Maximum-token preflight estimate: ${p['upper_cost_estimate_usd']:.4f}; request reservation ceiling: $2.50, below the authorized $5. These are full-rate token estimates, not a provider invoice, and exclude cache discounts. Rates checked against [Tinker pricing](https://tinker-docs.thinkingmachines.ai/tinker/models/models_and_pricing/).", '',
        'Five new execution tests and existing replay/budget checks passed before launch. The post-run verifier checked every request receipt, prompt hash, actual position, entry-cost state, bracket action, sampling seed, reconstructed P&L and cost, and pinned baseline/source/code hashes. The holdout ledger was checked read-only and remained unchanged; enrollment is still pending. No training, holdout evaluation, commit or push was performed.', '',
        '## Interpretation limits','',
        'This is a fresh closed-loop execution test on previously inspected development matches, not an unseen-game generalization test. Three repeats are clustered within each of 72 games in uncertainty estimates. Policy thresholds were chosen after earlier development analysis; confidence intervals do not correct that selection. Checkpoint monitoring cannot establish minute-by-minute stop performance or prevent reversals after the final checkpoint.', '',
        '```sh',
        'HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m unittest experiments.forecast_only.test_bracket_original -v',
        'HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m experiments.forecast_only.bracket_original',
        'HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -u -m experiments.forecast_only.bracket_original --live',
        'HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m experiments.forecast_only.verify_bracket_original',
        'HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m experiments.forecast_only.report_bracket_original',
        '```']
    (OUT/'REPORT.md').write_text('\n'.join(lines)+'\n')
    (OUT/'SHA256SUMS.json').write_text(json.dumps({f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(OUT.iterdir()) if f.is_file() and f.name!='SHA256SUMS.json'},indent=2)+'\n')
    print(json.dumps({'triggers':dict(triggers),'overridden':overrides,'summary':new,'cost':r['usage']},indent=2))

if __name__=='__main__':main()
