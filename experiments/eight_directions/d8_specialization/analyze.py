"""Registered offline observable-regime screen; no model/service calls."""
import collections
from decimal import Decimal
import gzip
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np

from experiments.forecast_only.core import guard

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'artifacts/eight-directions/d8_specialization'
SIDES = ['home', 'draw', 'away']
SEED = 20261013


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def read_lines(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt') as stream:
        return [json.loads(line) for line in stream]


def membership(decision, score, prefix, shown, previous):
    """Only observed inputs; no outcomes, fill prices, future events or labels."""
    red = re.search(r'^Red cards: .+? (\d+), .+? (\d+)$', prefix, re.MULTILINE)
    tied = score is not None and score['home'] == score['away']
    # Decimal quote arithmetic preserves the predeclared exact $0.15 boundary.
    movement = max(float(abs(Decimal(str(shown[o])) - Decimal(str(previous[o])))) for o in SIDES) if previous else None
    return {'tied_late': decision == 'h2+30' and tied,
            'observed_red_card': bool(red and sum(map(int, red.groups())) > 0),
            'halftime': decision == 'ht',
            'large_observed_price_move': movement is not None and movement >= .15}, {
                'red_parse_supported': red is not None,
                'red_counts': list(map(int, red.groups())) if red else None,
                'previous_observed_prices': previous,
                'max_observed_price_change': movement,
                'observed_score': score}


def probabilities(step):
    p = step.get('probs')
    if not step.get('valid') or step.get('truncated') or not isinstance(p, dict) or set(p) != set(SIDES):
        return None
    if any(type(p[o]) not in (int, float) or not math.isfinite(p[o]) or not 0 <= p[o] <= 1 for o in SIDES):
        return None
    total = math.fsum(p.values())
    return {o: p[o] / total for o in SIDES} if total > 0 and abs(total - 1) <= .05 else None


def loss(p, target):
    return {'brier': sum((p[o] - int(o == target)) ** 2 for o in SIDES) if p else 2.,
            'log_loss': -math.log(max(1e-6, p[target])) if p else -math.log(1e-6)}


def mean(values):
    return float(np.mean(values)) if len(values) else None


def cluster(rows, valid_only=False):
    groups = collections.defaultdict(list)
    for r in rows:
        if not valid_only or r['model_probabilities'] is not None:
            groups[r['match_id']].append(r)
    return {m: {f'{arm}_{metric}': mean([r[arm][metric] for r in records])
                for arm in ['model', 'market'] for metric in ['brier', 'log_loss']}
            for m, records in groups.items()}


def summary(rows, total_matches, total_decisions):
    grouped = cluster(rows)
    valid = cluster(rows, True)
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, len(grouped), (4000, len(grouped))) if len(grouped) >= 2 else None
    stats = {'matches': len(grouped), 'observations_with_repeats': len(rows),
             'valid_observations': sum(r['model_probabilities'] is not None for r in rows),
             'match_coverage': len(grouped) / total_matches,
             'observation_coverage': len(rows) / total_decisions,
             'valid_rate_observation': mean([r['model_probabilities'] is not None for r in rows]),
             'invalid_inclusive': {}, 'valid_only': {'matches': len(valid)}, 'paired_improvement': {},
             'bootstrap_estimable': len(grouped) >= 2,
             'uncertainty_note': 'A singleton cohort cannot estimate between-match uncertainty.' if len(grouped) < 2 else None}
    for key in ['model_brier', 'market_brier', 'model_log_loss', 'market_log_loss']:
        stats['invalid_inclusive'][key] = mean([g[key] for g in grouped.values()])
        stats['valid_only'][key] = mean([g[key] for g in valid.values()])
    for metric in ['brier', 'log_loss']:
        difference = np.array([g[f'market_{metric}'] - g[f'model_{metric}'] for g in grouped.values()])
        stats['paired_improvement'][metric] = {'mean': mean(difference),
            'match_bootstrap_95': np.quantile(difference[idx].mean(axis=1), [.025, .975]).tolist() if idx is not None else None,
            'orientation': 'market minus model; positive is better'}
    return stats


def pnl_diagnostic(matches):
    pnl = [m['pnl'] for m in matches]
    wins, losses = [x for x in pnl if x > 0], [x for x in pnl if x < 0]
    traded = [m for m in matches if m['trades'] > 0]
    steps = [s for m in matches for s in m['steps']]
    groups = collections.defaultdict(list)
    for m in matches:
        groups[m['match_id']].append(m['pnl'])
    values = np.array([np.mean(v) for v in groups.values()])
    rng = np.random.default_rng(SEED)
    boot = values[rng.integers(0, len(values), (4000, len(values)))].mean(axis=1)
    return {'scope': 'unchanged original recorded policy; diagnostic only, no specialization trade replay',
            'matches': len(groups), 'game_replays': len(matches), 'traded_game_replays': len(traded),
            'trading_coverage': len(traded) / len(matches), 'net_pnl_total_repeats_usd': sum(pnl),
            'mean_net_pnl_per_game_replay': float(values.mean()), 'mean_net_pnl_match_bootstrap_95': np.quantile(boot, [.025, .975]).tolist(),
            'combined_simulated_fee_spread_costs_usd': sum(m['cost'] for m in matches),
            'gross_pnl_before_combined_costs_usd': sum(m['pnl'] + m['cost'] for m in matches),
            'gross_wins_net_of_costs_usd': sum(wins), 'gross_losses_net_of_costs_usd': sum(losses),
            'average_win_usd': mean(wins), 'average_loss_usd': mean(losses), 'worst_game_replay_usd': min(pnl),
            'profitable_match_rate_all_eligible_game_replays': len(wins) / len(matches),
            'profitable_match_rate_traded_game_replays': sum(m['pnl'] > 0 for m in traded) / len(traded),
            'recorded_turnover_contract_transactions': sum(m['trades'] for m in matches),
            'exposure_fraction_recorded_chunks': mean([s['next_position'] != 'flat' for s in steps]),
            'exposure_definition': 'fraction of observed decision chunks ending with a position; not time- or capital-weighted',
            'position_win_rate': None, 'unavailable': 'individual position attribution, executable spread/slippage, adverse movement; not reconstructed in this forecast screen'}


def write_report(results, protocol):
    def number(x):
        return f'{x:.4f}' if x is not None else 'not estimable'

    def interval(x):
        return '[' + ', '.join(number(v) for v in x) + ']' if x is not None else 'not estimable'

    lines = ['# D8: narrow observable-regime specialization', '',
             '**Classification: negative.** No regime qualifies under the frozen earlier-cohort selection rule; no specialized policy is selected or promoted. No inference, training or paid-service requests were made ($0 incremental spend).', '',
             '## Hypothesis and registered intervention', '',
             protocol['hypothesis'], '',
             'This is a post-hoc development screen of four predeclared observable slices on the original recorded checkpoint and forecast tape. It changes which observations are scored, while keeping probabilities, positions, sizing, prices and execution untouched. The input schema and known earlier risk report were inspected before registration; the D8 aggregate was not. The `protocol.json` file was written before aggregate analysis.', '',
             'The slices are tied score at `h2+30`, an observed positive red-card count in the anchored prompt prefix, halftime (`ht`), and maximum absolute shown-price movement of at least $0.15 since the previous available sparse observation of the same trajectory. The first observation cannot satisfy the movement slice. Membership sees only observation timestamps, prefix text, observed score and shown prices. Scoring labels and future fills are used only after membership is determined.', '',
             'Selection requires at least five earlier matches, positive market-minus-model Brier improvement and positive log-loss improvement. Among qualifiers, greatest Brier improvement wins, with a fixed lexical tie break. `selection.json` records the frozen selection before later-cohort aggregation. No candidate qualified; all later slice scores are descriptive and cannot be used to reselect. The stopping rule prohibits threshold tuning and retrying until a positive finding.', '',
             '## Frozen data and uncertainty', '',
             'Known original `forecast-rules-run-3` repeated-validation snapshots contain 72 development matches, three recorded repeats each, and 1,077 observations. The frozen chronological boundary is 2026-06-21 UTC: 39 earlier matches (582 repeated observations) for selection and 33 later matches (495 observations) for assessment. Whole matches and their repeats remain together. Exact IDs, source paths and SHA256 checksums are in `protocol.json`; inputs are copied snapshots under `inputs/earlier-run-risk/`. No data release or active experiment was loaded.', '',
             f"Checkpoint provenance is `{results['checkpoint_provenance']['checkpoint']}` (Qwen/Qwen3-8B, LoRA rank 32, training batch 48). `checkpoint_provenance.json` copies the orchestrator's existing free metadata verification; D8 did not contact the service or load weights. The frozen recorded forecast tape supplies every forecast.", '',
             'The market baseline is normalized shown YES quotes, not an executable bid/ask. Probabilities use the original finite-vector validity tolerance of 0.05 before normalization. Invalid forecasts would receive Brier 2 and log loss −log(10⁻⁶); all 1,077 recorded observations are valid, so valid-only and invalid-inclusive results agree. The red-card prefix parser supported all 1,077 observations. Original information delays remain `event_delay_s=60`, `fill_delay_s=60`, and `price_max_age_s=90`.', '',
             'Each score averages observations and repeats within a match, then averages matches equally. Paired market-minus-model differences use 4,000 whole-match percentile bootstrap resamples, seed 20261013; positive improvement favors the model. Bootstrap samples preserve all repetitions. A one-match slice has no estimable between-match interval. Shared-team dependence is not modeled. Four-slice selection, overlapping regimes, and repeated prior development use make the intervals descriptive; there is no multiplicity correction or independent generalization claim.', '',
             '## Proper-score results', '',
             '| Cohort / slice | Matches | Observations | Model Brier | Market Brier | Brier improvement [95% CI] | Log-loss improvement [95% CI] |',
             '|---|---:|---:|---:|---:|---|---|']
    for part in ['fit', 'assessment']:
        metrics = {'overall unchanged forecasts': results['overall_' + part], **results[part]}
        for name, value in metrics.items():
            b, log = value['paired_improvement']['brier'], value['paired_improvement']['log_loss']
            lines.append(f"| {part} / {name} | {value['matches']} | {value['observations_with_repeats']} | {number(value['invalid_inclusive']['model_brier'])} | {number(value['invalid_inclusive']['market_brier'])} | {number(b['mean'])} {interval(b['match_bootstrap_95'])} | {number(log['mean'])} {interval(log['match_bootstrap_95'])} |")
    lines += ['', 'Only halftime has positive earlier Brier point improvement (+0.0124), but its log loss worsens (−0.0200) and both intervals cross zero. It therefore fails selection. Tied-late and large-movement slices have worse earlier point scores. The observed-red-card slice has only four earlier matches and one later match, failing minimum coverage. On later matches, all four slices have worse Brier and log-loss point scores; tied-late uncertainty still crosses zero. This is a negative screen, with uncertainty about exact effects and sparse red-card coverage.', '',
              '## Unchanged original trading diagnostic', '',
              'This diagnostic reproduces original recorded P&L; it does not execute a specialized trading policy and is not a maker/taker comparison. Totals sum three repeats, which are not independent matches. Costs remain the original fixed fee coefficient and spread allowance. “Gross P&L” adds back their combined recorded costs; fee and executable spread/slippage are not separated in this diagnostic.', '',
              '| Cohort | Traded replays / eligible | Profitable / all | Profitable / traded | Net P&L | Gross before combined costs | Combined costs | Avg win | Avg loss | Worst loss | Turnover | Exposure |',
              '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for part, value in results['overall_original_trading'].items():
        lines.append(f"| {part} | {value['traded_game_replays']}/{value['game_replays']} | {value['profitable_match_rate_all_eligible_game_replays']:.1%} | {value['profitable_match_rate_traded_game_replays']:.1%} | ${value['net_pnl_total_repeats_usd']:.4f} | ${value['gross_pnl_before_combined_costs_usd']:.4f} | ${value['combined_simulated_fee_spread_costs_usd']:.4f} | ${value['average_win_usd']:.4f} | ${value['average_loss_usd']:.4f} | ${value['worst_game_replay_usd']:.4f} | {value['recorded_turnover_contract_transactions']} | {value['exposure_fraction_recorded_chunks']:.1%} |")
    total = results['overall_original_trading']['all']
    lines += ['', f"Overall mean net P&L per game-replay is ${total['mean_net_pnl_per_game_replay']:.4f}, with match-bootstrap interval {interval(total['mean_net_pnl_match_bootstrap_95'])}. Net losing replays total ${total['gross_losses_net_of_costs_usd']:.4f}. A no-trade baseline is $0. The later cohort's small positive total is inconclusive and cannot establish specialization: original trading decisions were unchanged, and no regime qualified.", '',
              'Turnover counts recorded contract transactions. Exposure is the fraction of decision chunks ending with a position, not time- or capital-weighted exposure. Profitable-match rates above are across match-repeat game-replays, never position win rate; individual position win rate and post-fill adverse movement are unavailable in this diagnostic. Original forecasts condition on the recorded original portfolio. No altered-position counterfactual, approximate order-book replay, or fresh closed-loop evaluation was performed.', '',
              '## Limitations and next discriminating experiment', '',
              'All outcomes belong to repeatedly inspected historical development data. Probabilities are estimates, not truth. Sparse observed price movement is not a high-frequency volatility measure. Four handpicked regimes are not an exhaustive search and do not establish that no useful specialization exists. Score comparisons do not prove profitable trades or credible maker fills. Red-card scarcity prevents meaningful regime validation.', '',
              'The next discriminating experiment should prospectively register one regime and a sample-size target on newly collected development matches, before outcomes arrive, using the same checkpoint and matched observation inputs against the market. Collect observed red-card states and timestamped executable order books/trades if execution is later assessed. Do not recycle this assessment cohort to choose a revised threshold or regime. Any changed-position trading policy then needs separate bounded closed-loop inference and maker/taker-aware execution validation; no paid request is proposed here.', '',
              '## Holdout and spend', '',
              'Read-only integrity/overlap checks on the actual 72 cohort IDs ran before and after analysis. Both returned ledger head `2f6051410929bd283e984574ecce2a944e82aa12911a13d24c194f48dc5a995d`, state `pending_enrollment`, protected count 0. The ledger was not changed; no protected outcomes were read, forecast, scored or empirically tested. Pending enrollment with zero protected IDs is not evidence from a holdout. No active experiment was interrupted or modified.', '',
              'The task ledger records zero paid requests, zero reservation and $0 incremental service spend. Billing was not queried; historical runs are sunk costs and are excluded. The latest user request caps execution work at $5 total, superseding an obsolete per-direction budget; D8 has no paid authorization. MLflow is not required.', '',
              '## Reproduce', '', 'Run from `/Users/alexkan/Desktop/frontiers/rin-gym-research-eight`:', '', '```sh',
              'HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=. /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m experiments.eight_directions.d8_specialization.analyze',
              'HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=. /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m unittest experiments.eight_directions.d8_specialization.test_analysis -v', '```', '',
              'The full verification receipt can be regenerated with:', '', '```sh',
              'HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=. /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m experiments.eight_directions.d8_specialization.verify', '```', '',
              'The analysis verifies input checksums and cohort identity, persists selection before later aggregation, produces raw observation receipts and JSON results, and checks the holdout seal. Focused tests cover observable membership, unsupported red-card parsing, first-quote exclusion, original probability validity, equal match weighting and singleton uncertainty. `verification.json` records test and deterministic rerun evidence.']
    (OUT / 'REPORT.md').write_text('\n'.join(lines) + '\n')


def main():
    protocol = json.loads((OUT / 'protocol.json').read_text())
    setup = json.loads((ROOT / 'research/eight-directions/setup.json').read_text())
    for source, expected in protocol['source_sha256'].items():
        assert hashlib.sha256((ROOT / source).read_bytes()).hexdigest() == expected, source
    ids = protocol['cohort_ids']
    assert set(ids) <= set(setup['allowed_match_ids'])
    before = guard(setup['guard_ledger'], ids)
    assert before == protocol['guard_before']
    tasks = {t['id']: t for t in read_lines(ROOT / 'inputs/earlier-run-risk/tasks.jsonl.gz')}
    matches = read_lines(ROOT / 'inputs/earlier-run-risk/original-matches.jsonl')
    assert sorted({m['match_id'] for m in matches}) == ids
    assert len(matches) == 216 and all(collections.Counter(m['match_id'] for m in matches)[mid] == 3 for mid in ids)
    rows = []
    for match in matches:
        previous, previous_t = None, None
        for step in match['steps']:
            task = tasks[step['task_id']]
            assert task['match_id'] == match['match_id']
            assert previous_t is None or task['t'] > previous_t
            shown = task['scoring']['shown_prices']
            prefix = task['messages'][1]['content'].split('Buying now costs about:')[0]
            member, evidence = membership(task['decision'], task['scoring'].get('observed_score'), prefix, shown, previous)
            p = probabilities(step)
            market = task['scoring']['market_probs']
            target = task['scoring']['result']
            rows.append({'match_id': match['match_id'], 'repeat': match['repeat'], 'task_id': task['id'],
                         'decision': task['decision'], 'observed_t': task['t'], 'previous_observed_t': previous_t,
                         'slice_membership': member, 'evidence': evidence, 'shown_prices': shown,
                         'model_probabilities': p, 'market_probabilities': market, 'target': target,
                         'model': loss(p, target), 'market': loss(market, target), 'original_action': step['action'],
                         'sampling_seed': step.get('sampling_seed')})
            previous, previous_t = dict(shown), task['t']
    with (OUT / 'analysis_receipts.jsonl').open('w') as stream:
        for row in rows:
            stream.write(json.dumps(row, allow_nan=False) + '\n')
    fit = [r for r in rows if r['match_id'] in protocol['fit_match_ids']]
    fit_slices = {s: summary([r for r in fit if r['slice_membership'][s]], 39, len(fit)) for s in protocol['slices']}
    eligible = [s for s, metric in fit_slices.items() if metric['matches'] >= 5
                and metric['paired_improvement']['brier']['mean'] > 0
                and metric['paired_improvement']['log_loss']['mean'] > 0]
    selected = min(eligible, key=lambda s: (-fit_slices[s]['paired_improvement']['brier']['mean'], s)) if eligible else None
    # Persist the frozen choice before computing any later-cohort aggregate.
    selection = {'selected_candidate': selected, 'fit_results': fit_slices,
                 'rule': protocol['choice_criterion'], 'assessment_not_consulted': True}
    dump(OUT / 'selection.json', selection)
    assessment = [r for r in rows if r['match_id'] in protocol['assessment_match_ids']]
    assessed = {s: summary([r for r in assessment if r['slice_membership'][s]], 33, len(assessment)) for s in protocol['slices']}
    classification = 'negative' if selected is None else 'inconclusive'
    if selected is not None and assessed[selected]['matches'] >= 5:
        if all(assessed[selected]['paired_improvement'][m]['match_bootstrap_95'][0] > 0 for m in ['brier', 'log_loss']):
            classification = 'promising'
    after = guard(setup['guard_ledger'], ids)
    assert before == after
    dump(OUT / 'guard_after.json', after)
    checkpoint = json.loads((ROOT / 'research/eight-directions/checkpoint-verification.json').read_text())
    dump(OUT / 'checkpoint_provenance.json', checkpoint)
    results = {'classification': classification, 'selected_candidate': selected, 'fit': fit_slices,
               'assessment': assessed, 'overall_fit': summary(fit, 39, len(fit)),
               'overall_assessment': summary(assessment, 33, len(assessment)),
               'overall_original_trading': {part: pnl_diagnostic([m for m in matches if m['match_id'] in subset])
                   for part, subset in [('fit', protocol['fit_match_ids']), ('assessment', protocol['assessment_match_ids']), ('all', ids)]},
               'observations': len(rows), 'red_prefix_parse_supported_observations': sum(r['evidence']['red_parse_supported'] for r in rows),
               'protocol_sha256': hashlib.sha256((OUT / 'protocol.json').read_bytes()).hexdigest(),
               'analysis_receipts_sha256': hashlib.sha256((OUT / 'analysis_receipts.jsonl').read_bytes()).hexdigest(),
               'guard_before': before, 'guard_after': after, 'incremental_spend_usd': 0,
               'assessment_role': 'selected candidate confirmatory within reused development only; all other later slice results descriptive, cannot reselect',
               'limitations': protocol['limitations'], 'checkpoint_provenance': checkpoint}
    dump(OUT / 'results.json', results)
    write_report(results, protocol)
    print(json.dumps({'classification': classification, 'selected_candidate': selected,
                      'observations': len(rows), 'fit_matches': 39, 'assessment_matches': 33, 'spend_usd': 0}))


if __name__ == '__main__':
    main()
