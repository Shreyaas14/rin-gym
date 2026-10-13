"""Exploratory original-trade-tape overlays; no inference or holdout readout."""
import gzip
import hashlib
import json
from pathlib import Path
import numpy as np
from app.trade_env.costs import buy_cost, sell_proceeds
from experiments.forecast_only.core import guard
from experiments.forecast_only.earlier_run_risk import stats, LEDGER

OUT = Path('artifacts/loss-audit')
POLICIES = {'original': {}}
POLICIES.update({f'entry_cost_max_{v}': {'cap': v} for v in [.75, .85, .95]})
POLICIES.update({f'entry_edge_min_{v}': {'edge': v} for v in [0, .02]})
POLICIES.update({f'stop_loss_{v}': {'stop': v} for v in [.15, .25, .35]})
POLICIES.update({f'take_profit_{v}': {'take': v} for v in [.10, .20]})
POLICIES['exit_below_liquidation_value'] = {'belief_exit': True}
POLICIES['skip_switch_entries'] = {'skip_switch': True}
# Follow-up hypotheses after inspecting the first sweep: combined bracket and late exposure.
POLICIES['followup_take_0.10_stop_0.25'] = {'take': .10, 'stop': .25}
POLICIES['followup_exit_h2_30'] = {'exit_decision': 'h2+30'}


def read(path):
    return [json.loads(s) for s in Path(path).read_text().splitlines()]


def overlay(trade, policy, config):
    """Signals use displayed quotes and saved beliefs, never future fill/outcome."""
    if (trade['shown_cost'] > policy.get('cap', 1)
        or trade['edge'] < policy.get('edge', -2)
        or (policy.get('skip_switch') and trade['entry_action'].startswith('switch_'))):
        return None
    for point in trade['checkpoints']:
        liquidation = sell_proceeds(point['shown'], config, trade['side'])
        gain = liquidation - trade['entry_cost']
        if ((policy.get('exit_decision') == point['decision'])
            or ('stop' in policy and gain <= -policy['stop'])
            or ('take' in policy and gain >= policy['take'])
            or (policy.get('belief_exit') and point['prob'] < liquidation)):
            return sell_proceeds(point['fill'], config, trade['side']) - trade['entry_cost']
    return trade['pnl_per_share']


def reconstruct(case, predictions, tasks, config, quantity):
    trades, active = [], None
    for step in case['steps']:
        task = tasks[step['task_id']]; s = task['scoring']
        pred = predictions[(case['repeat'], step['task_id'])]
        action = step['action']
        if active:
            side = active['side']
            active['checkpoints'].append({'decision': task['decision'], 'shown': s['shown_prices'][side],
                'fill': s['fill_prices'][side], 'prob': pred['probs'][side], 'action': action})
            if action == 'close' or action.startswith('switch_'):
                active.update(exit_decision=task['decision'], exit_type=action,
                    exit_value=sell_proceeds(s['fill_prices'][side], config, side))
                trades.append(active); active = None
        if action.startswith(('buy_', 'switch_')):
            assert active is None
            side = action.split('_', 1)[1]
            shown = buy_cost(s['shown_prices'][side], config, side)
            active = {'match_id': case['match_id'], 'repeat': case['repeat'],
                'match': task['messages'][1]['content'].splitlines()[0], 'side': side,
                'entry_decision': task['decision'], 'entry_action': action,
                'shown_cost': shown, 'entry_cost': buy_cost(s['fill_prices'][side], config, side),
                'prob': pred['probs'][side], 'edge': pred['probs'][side]-shown,
                'reason': pred['completion'], 'checkpoints': []}
        if s['final_chunk'] and active:
            active.update(exit_decision='settlement', exit_type='settlement',
                          exit_value=float(s['result'] == active['side']))
            trades.append(active); active = None
    assert active is None
    for trade in trades:
        trade['pnl_per_share'] = trade['exit_value'] - trade['entry_cost']
        trade['pnl'] = quantity * trade['pnl_per_share']
    assert abs(sum(t['pnl'] for t in trades)-case['pnl']) < 1e-9, case
    return trades


def main():
    config = json.loads(Path('artifacts/earlier-run-risk/comparison.json').read_text())['config']
    task_path = Path('artifacts/earlier-run-risk/tasks.jsonl.gz')
    with gzip.open(task_path, 'rt') as f: tasks = {t['id']: t for t in map(json.loads, f)}
    mids = {t['match_id'] for t in tasks.values()}; seal = guard(LEDGER, mids)
    paths = [task_path, Path('artifacts/earlier-run-risk/comparison.json')]
    report = {'cost_usd': 0, 'policies': POLICIES, 'datasets': {}, 'holdout_before': seal,
        'method': 'Frozen original entry/exit tape; filters skip positions, early exits stay flat until the next original entry. Switches close the old position even when the new entry is filtered. Signals at recorded checkpoints only; delayed fills and fees applied. No new model responses. All policy comparisons are exploratory on previously inspected development games.'}
    OUT.mkdir(exist_ok=True)
    for name, folder, arm, quantity in [('original', 'closed-loop-risk', 'original', 1),
            ('quarter_fresh', 'quarter-original-live', 'original_quarter_fresh', .25)]:
        mp = Path('artifacts')/folder/'matches.jsonl'; pp = mp.with_name('predictions.jsonl')
        paths.extend([mp, pp]); cases = [r for r in read(mp) if r['arm'] == arm]
        predictions = {(r['repeat'], r['task_id']): r for r in read(pp) if r['arm'] == arm}
        assert len(cases) == 216 and {r['match_id'] for r in cases} == mids
        all_trades = []; results = {p: [] for p in POLICIES}
        for case in cases:
            trades = reconstruct(case, predictions, tasks, config, quantity); all_trades.extend(trades)
            for policy, params in POLICIES.items():
                values = [overlay(t, params, config) for t in trades]
                values = [v for v in values if v is not None]
                results[policy].append({'match_id': case['match_id'], 'repeat': case['repeat'],
                    'traded': bool(values), 'pnl': quantity*sum(values)})
        ids = sorted(mids); rng = np.random.default_rng(20261013)
        idx = rng.integers(0, len(ids), (4000, len(ids)))
        summaries = {}
        base = results['original']
        for policy, rows in results.items():
            by_game = np.array([sum(r['pnl'] for r in rows if r['match_id']==mid) for mid in ids])
            delta = np.array([sum(a['pnl']-b['pnl'] for a,b in zip(rows,base) if a['match_id']==mid) for mid in ids])
            s = stats(rows)
            s.update(pnl_95_game_cluster=np.quantile(by_game[idx].sum(1), [.025,.975]).tolist(),
                pnl_delta_95_game_cluster=np.quantile(delta[idx].sum(1), [.025,.975]).tolist(),
                leave_one_game_out_total_range=[float(by_game.sum()-by_game.max()), float(by_game.sum()-by_game.min())],
                original_winning_cases_lost=sum(b['pnl']>1e-10 and a['pnl']<=1e-10 for a,b in zip(rows,base)),
                losing_cases_rescued=sum(b['pnl']<-1e-10 and a['pnl']>1e-10 for a,b in zip(rows,base)))
            summaries[policy] = s
        expensive = [t for t in all_trades if t['shown_cost'] >= .75]
        negative_edge = [t for t in all_trades if t['edge'] < 0]
        report['datasets'][name] = {'quantity': quantity, 'positions': len(all_trades),
            'expensive_positions': {'count': len(expensive), 'pnl': sum(t['pnl'] for t in expensive)},
            'negative_forecast_edge_positions': {'count':len(negative_edge), 'pnl':sum(t['pnl'] for t in negative_edge)},
            'policies': summaries, 'worst_positions': sorted(all_trades, key=lambda t:t['pnl'])[:12]}
        (OUT/f'{name}-positions.json').write_text(json.dumps(all_trades, indent=2)+'\n')
        (OUT/f'{name}-counterfactuals.json').write_text(json.dumps(results, indent=2)+'\n')
    assert guard(LEDGER,mids) == seal
    report['holdout_after'] = seal
    report['source_sha256'] = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    (OUT/'audit.json').write_text(json.dumps(report,indent=2)+'\n')
    for name, dataset in report['datasets'].items():
        print(name, 'positions', dataset['positions'], 'expensive', dataset['expensive_positions'], 'negative_edge',dataset['negative_forecast_edge_positions'])
        for policy,s in dataset['policies'].items():
            print(policy, round(s['total_pnl'],3), round(100*s['win_rate_traded'],1), s['traded_cases'], 'lost wins',s['original_winning_cases_lost'])

if __name__ == '__main__': main()
