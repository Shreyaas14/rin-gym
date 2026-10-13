"""Offline full-position forecast/action audit and locked frozen-tape executor.

Run from the research checkout. Decision functions never receive scoring outcomes
or delayed fill evidence. These signals remain conditioned on original positions.
"""
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics

import numpy as np

from app.trade_env.config import OUTCOMES
from app.trade_env.costs import buy_cost, sell_proceeds
from app.trade_env.reward import legal_actions, noop, next_position
from app.trade_env.replay import Replay, by_match
from app.trade_env.tasks import read_tasks
from experiments.forecast_only.core import guard

OUT = Path('artifacts/eight-directions/d1_consistency')
EPS = 1e-9


def values(probs, prices, position, pricing):
    """Terminal expected cash+position change relative to holding current assets.

    Historic acquisition costs are sunk and cancel in all choices. A switch
    includes both sale proceeds minus held probability and new buy net edge.
    """
    result = {noop(position): 0.0}
    if position == 'flat':
        result.update({'buy_' + o: probs[o] - buy_cost(prices[o], pricing, o)
                       for o in OUTCOMES})
    else:
        sell_edge = sell_proceeds(prices[position], pricing, position) - probs[position]
        result['close'] = sell_edge
        result.update({'switch_' + o: sell_edge + probs[o] - buy_cost(prices[o], pricing, o)
                       for o in OUTCOMES if o != position})
    return result


def select(probs, shown_prices, position, pricing, margin):
    if probs is None:
        return noop(position)
    table = values(probs, shown_prices, position, pricing)
    best = max(legal_actions(position), key=lambda a: table[a])
    return best if table[best] > margin else noop(position)


def paired_ci(deltas, seed, reps=5000):
    a = np.asarray(deltas, dtype=float)
    rng = np.random.default_rng(seed)
    boot = a[rng.integers(0, len(a), size=(reps, len(a)))].mean(axis=1)
    return {'mean': float(a.mean()), 'match_cluster_95': np.quantile(boot, [.025, .975]).tolist(),
            'unique_matches': len(a)}


def clusters(rows, field):
    grouped = defaultdict(list)
    for row in rows:
        if row.get(field) is not None:
            grouped[row['match_id']].append(row[field])
    return {mid: statistics.mean(v) for mid, v in grouped.items()}


def audit_row(source, case, step, task, pricing):
    probs = step['probs'] if step['valid'] else None
    pos = task['position']
    action = step['action']
    scoring = task['scoring']
    target = scoring['result']
    row = {'source': source, 'match_id': case['match_id'], 'repeat': case['repeat'],
           'task_id': task['id'], 'decision': task['decision'], 'position_before': pos,
           'action': action, 'valid': probs is not None, 'probs': probs,
           'market_brier': sum((scoring['market_probs'][o] - float(o == target))**2 for o in OUTCOMES),
           'market_log_loss': -math.log(max(1e-4, scoring['market_probs'][target]))}
    row['model_brier_penalized'] = sum((probs[o] - float(o == target))**2 for o in OUTCOMES) if probs else 2.0
    row['model_log_loss_penalized'] = -math.log(max(1e-4, probs[target])) if probs else -math.log(1e-4)
    if probs is None:
        return row
    shown = values(probs, scoring['shown_prices'], pos, pricing)
    delayed = values(probs, scoring['fill_prices'], pos, pricing)
    realized = values({o: float(o == target) for o in OUTCOMES}, scoring['fill_prices'], pos, pricing)
    v, f, r = shown[action], delayed[action], realized[action]
    row.update(shown_action_ev=v, delayed_action_ev=f, full_action_ev_table=shown,
               realized_terminal_action_delta=r, execution_shift=f-v, forecast_residual=r-f,
               value_decreasing=float(v < -EPS),
               ev_regret=max(shown.values())-v,
               regret_over_margin=float(max(shown.values())-v > .02 + EPS),
               adverse_execution_flip=float(v >= -EPS and f < -EPS),
               bought_contract_edge=(probs[action.split('_', 1)[1]] - buy_cost(
                   scoring['shown_prices'][action.split('_', 1)[1]], pricing, action.split('_', 1)[1]))
                   if action.startswith(('buy_', 'switch_')) else None)
    assert abs(r - (v + row['execution_shift'] + row['forecast_residual'])) < EPS
    return row


def execute_case(tasks, case, pricing, intervention=False):
    replay = Replay(tasks, pricing)
    cash, gross_buy, gross_sell, capital_peak = 0., 0., 0., 0.
    exposed, shown_exposure, receipts = [], [], []
    tape = {s['decision']: s for s in case['steps']}
    while (task := replay.next_task()) is not None:
        original_step = tape[task['decision']]
        probs = original_step['probs'] if original_step['valid'] else None
        pos = replay.position
        scoring = task['scoring']
        pconfig = {**pricing, 'market_fee_rates': scoring.get('fee_rates', {})}
        # No scoring labels/fills can reach select().
        action = select(probs, scoring['shown_prices'], pos, pconfig, .02) if intervention else original_step['action']
        if not intervention:
            assert task['id'] == original_step['task_id']
        shown_exposure.append(scoring['shown_prices'][pos] if pos != 'flat' else 0.)
        if action == 'close' or action.startswith('switch_'):
            proceeds = sell_proceeds(scoring['fill_prices'][pos], pconfig, pos)
            cash += proceeds
            gross_sell += proceeds
        if action.startswith(('buy_', 'switch_')):
            side = action.split('_', 1)[1]
            debit = buy_cost(scoring['fill_prices'][side], pconfig, side)
            cash -= debit
            gross_buy += debit
        capital_peak = max(capital_peak, -cash)
        replay.step(action)
        if not intervention:
            assert abs(replay.steps[-1]['pnl'] - original_step['pnl']) < EPS
        exposed.append(float(replay.position != 'flat'))
        receipts.append({'task_id': task['id'], 'original_forecast_position': original_step['task_id'].rsplit(':', 1)[1],
                         'actual_position': pos, 'action': action, 'probs': probs,
                         'scored_chunk_pnl': replay.steps[-1]['pnl'],
                         'shown_action_values': values(probs, scoring['shown_prices'], pos, pconfig) if probs else None})
    final = replay.result()
    settlement = float(final['final_position'] == tasks[0]['scoring']['result']) if final['final_position'] != 'flat' else 0.
    # Verify cash-and-settlement accounting independently of chunk reward summation.
    assert abs(final['pnl'] - cash - settlement) < EPS
    if not intervention:
        assert abs(final['pnl'] - case['pnl']) < EPS
    return {'match_id': case['match_id'], 'repeat': case['repeat'], 'pnl': final['pnl'],
            'traded': final['trades'] > 0, 'transaction_legs': final['trades'],
            'gross_purchase_capital': gross_buy, 'gross_sale_proceeds': gross_sell,
            'cashflow_turnover': gross_buy+gross_sell, 'peak_funded_capital': capital_peak,
            'decision_exposure_fraction': statistics.mean(exposed),
            'mean_shown_held_market_exposure': statistics.mean(shown_exposure),
            'peak_share_exposure': int(any(exposed)), 'execution_cost': final['cost'], 'receipts': receipts}


def trade_summary(rows, seed):
    pnl = [r['pnl'] for r in rows]
    wins, losses = [v for v in pnl if v > EPS], [v for v in pnl if v < -EPS]
    result = {'cases': len(rows), 'unique_matches': len({r['match_id'] for r in rows}),
              'net_pnl': math.fsum(pnl), 'gross_wins': math.fsum(wins), 'gross_losses': math.fsum(losses),
              'average_win': statistics.mean(wins) if wins else None,
              'average_loss': statistics.mean(losses) if losses else None,
              'worst_loss': min(pnl), 'winning_cases': len(wins), 'losing_cases': len(losses),
              'trading_coverage': statistics.mean(r['traded'] for r in rows),
              'win_rate_traded': len(wins)/sum(r['traded'] for r in rows) if any(r['traded'] for r in rows) else None,
              'mean_pnl_match_repeat': paired_ci(list(clusters(rows, 'pnl').values()), seed)}
    for k in ['gross_purchase_capital', 'gross_sale_proceeds', 'cashflow_turnover', 'transaction_legs', 'execution_cost']:
        result[k] = math.fsum(r[k] for r in rows)
    for k in ['peak_funded_capital', 'peak_share_exposure']:
        result['maximum_' + k] = max(r[k] for r in rows)
    for k in ['decision_exposure_fraction', 'mean_shown_held_market_exposure']:
        result[k] = statistics.mean(r[k] for r in rows)
    return result


def audit_summary(rows, seed):
    valid = [r for r in rows if r['valid']]
    result = {'decisions': len(rows), 'valid_decisions': len(valid),
              'valid_rate': statistics.mean(r['valid'] for r in rows),
              'action_counts': dict(Counter(r['action'] for r in rows)), 'by_action': {}}
    for k in ['value_decreasing', 'regret_over_margin', 'adverse_execution_flip', 'shown_action_ev',
              'execution_shift', 'forecast_residual', 'model_brier_penalized', 'market_brier',
              'model_log_loss_penalized', 'market_log_loss']:
        result[k] = paired_ci(list(clusters(rows, k).values()), seed)
    for action in sorted({r['action'] for r in rows}):
        sub = [r for r in valid if r['action'] == action]
        if sub:
            result['by_action'][action] = {'valid_decisions': len(sub),
                'value_decreasing_count': sum(r['value_decreasing'] for r in sub),
                'regret_over_margin_count': sum(r['regret_over_margin'] for r in sub),
                'adverse_execution_flip_count': sum(r['adverse_execution_flip'] for r in sub),
                'mean_shown_action_ev': statistics.mean(r['shown_action_ev'] for r in sub),
                'positive_bought_edge_negative_full_action_count': sum(
                    r.get('bought_contract_edge') is not None and r['bought_contract_edge'] > 0
                    and r['value_decreasing'] for r in sub)}
    for model, market, name in [('model_brier_penalized', 'market_brier', 'brier_model_minus_market'),
                                ('model_log_loss_penalized', 'market_log_loss', 'logloss_model_minus_market')]:
        m, p = clusters(rows, model), clusters(rows, market)
        result[name] = paired_ci([m[mid]-p[mid] for mid in sorted(m)], seed)
    return result


def main():
    protocol = json.loads((OUT/'protocol.json').read_text())
    setup = json.loads(Path('research/eight-directions/setup.json').read_text())
    ids = set(protocol['cohort_ids'])
    assert ids <= set(setup['allowed_match_ids'])
    before = guard(setup['guard_ledger'], ids)
    assert before == protocol['guard_before']
    manifest = json.loads(Path('inputs/MANIFEST.json').read_text())
    hashes = {}
    for p in ['inputs/closed-loop-risk/predictions.jsonl', 'inputs/closed-loop-risk/matches.jsonl',
              'inputs/earlier-run-risk/tasks.jsonl.gz', 'inputs/earlier-run-risk/original-matches.jsonl']:
        got = hashlib.sha256(Path(p).read_bytes()).hexdigest()
        source_key = setup['additional_snapshot']['source'] if p == setup.get('additional_snapshot', {}).get('destination') else None
        candidates = [v for k,v in manifest.items() if k.endswith(str(Path(p).relative_to('inputs'))) or k == source_key]
        assert got in candidates, f'Unpinned input: {p}'
        hashes[p] = got
    config = json.loads(Path('inputs/closed-loop-risk/protocol.json').read_text())['config']
    assert config == json.loads(Path('inputs/earlier-run-risk/comparison.json').read_text())['config']
    assert all(config[k] == v for k,v in protocol['config_constraints'].items())
    tasks = read_tasks('inputs/earlier-run-risk/tasks.jsonl.gz')
    assert {t['match_id'] for t in tasks} == ids
    grouped = by_match(tasks)
    lookup = {t['id']: t for t in tasks}
    predictions = [json.loads(line) for line in Path('inputs/closed-loop-risk/predictions.jsonl').read_text().splitlines()]
    pred = {(r['repeat'], r['task_id']): r for r in predictions if r['arm'] == 'original'}
    closed = [json.loads(line) for line in Path('inputs/closed-loop-risk/matches.jsonl').read_text().splitlines()]
    closed = [c for c in closed if c['arm'] == 'original']
    for case in closed:
        for step in case['steps']:
            p = pred[(case['repeat'], step['task_id'])]
            assert step['action'] == p['executed_action']
            step['probs'], step['valid'] = p['probs'], p['valid']
    earlier = [json.loads(line) for line in Path('inputs/earlier-run-risk/original-matches.jsonl').read_text().splitlines()]
    all_audit, all_replays = [], {}
    results = {'classification': 'inconclusive', 'scope': 'Retrospective development audit and frozen original-signal counterfactual; no fresh inference',
               'sources': {}, 'budget_usd': 0, 'guard_before': before, 'protocol_sha256': hashlib.sha256((OUT/'protocol.json').read_bytes()).hexdigest()}
    for source, cases in [('closed_loop_original', closed), ('earlier_original', earlier)]:
        assert len(cases) == 216 and {c['match_id'] for c in cases} == ids
        assert len({(c['match_id'], c['repeat']) for c in cases}) == len(cases)
        audits, originals, interventions = [], [], []
        for case in cases:
            originals.append(execute_case(grouped[case['match_id']], case, config))
            interventions.append(execute_case(grouped[case['match_id']], case, config, True))
            for step in case['steps']:
                task = lookup[step['task_id']]
                pricing = {**config, 'market_fee_rates': task['scoring'].get('fee_rates', {})}
                audits.append(audit_row(source, case, step, task, pricing))
        source_result = {}
        for partition, pids in [('earlier_39', protocol['partitions']['fit_match_ids']),
                                 ('later_33', protocol['partitions']['assessment_match_ids']), ('all_72', list(ids))]:
            a = [r for r in audits if r['match_id'] in pids]
            o = [r for r in originals if r['match_id'] in pids]
            c = [r for r in interventions if r['match_id'] in pids]
            om, cm = clusters(o, 'pnl'), clusters(c, 'pnl')
            zero = [{**r, 'pnl': 0., 'traded': False, **{k: 0. for k in r if k not in ['match_id', 'repeat', 'pnl', 'traded', 'receipts']}} for r in o]
            source_result[partition] = {'audit': audit_summary(a, protocol['seeds']['bootstrap']),
                'trade': {'original': trade_summary(o, protocol['seeds']['bootstrap']),
                          'consistency': trade_summary(c, protocol['seeds']['bootstrap']),
                          'no_trade': trade_summary(zero, protocol['seeds']['bootstrap'])},
                'consistency_minus_original': paired_ci([cm[mid]-om[mid] for mid in sorted(om)], protocol['seeds']['bootstrap'])}
        results['sources'][source] = source_result
        all_audit.extend(audits)
        all_replays[source] = {'original': originals, 'consistency': interventions}
    after = guard(setup['guard_ledger'], ids)
    assert before == after
    results['guard_after'] = after
    results['input_sha256'] = hashes
    (OUT/'results.json').write_text(json.dumps(results, indent=2, allow_nan=False)+'\n')
    (OUT/'audit-receipts.jsonl').write_text(''.join(json.dumps(r, allow_nan=False)+'\n' for r in all_audit))
    (OUT/'execution-receipts.json').write_text(json.dumps(all_replays, indent=2, allow_nan=False)+'\n')
    (OUT/'guard_after.json').write_text(json.dumps(after, indent=2)+'\n')
    print(json.dumps({source: {'later_33_delta': result['later_33']['consistency_minus_original'],
                              'later_33_original': result['later_33']['trade']['original']['net_pnl'],
                              'later_33_consistency': result['later_33']['trade']['consistency']['net_pnl'],
                              'all_72_audit': result['all_72']['audit']['value_decreasing']}
                      for source, result in results['sources'].items()}, indent=2))


if __name__ == '__main__':
    main()
