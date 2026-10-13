"""Exploratory risk controls applied to frozen development forecasts, without inference."""
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import statistics

from app.trade_env.config import OUTCOMES
from app.trade_env.costs import buy_cost
from app.trade_env.replay import by_match
from app.trade_env.tasks import read_tasks
from experiments.forecast_only.core import digest, guard


@dataclass(frozen=True)
class RiskPolicy:
    shares: float = 0.25
    minimum_edge: float = 0.05
    maximum_cost: float = 0.65

    def __post_init__(self):
        if not (0 < self.shares <= 1 and 0 <= self.minimum_edge < 1 and 0 < self.maximum_cost <= 1):
            raise ValueError('Invalid risk limits')


def choose_entry(probs, displayed, config, policy):
    """Only contemporaneous probabilities and prices reach the decision function."""
    if probs is None:
        return None
    candidates = [o for o in OUTCOMES
                  if buy_cost(displayed[o], config, o) <= policy.maximum_cost
                  and probs[o] - buy_cost(displayed[o], config, o) > policy.minimum_edge]
    return max(candidates, key=lambda o: probs[o] - buy_cost(displayed[o], config, o)) if candidates else None


def replay_match(tasks, predictions, config, policy):
    """One entry maximum; hold to settlement, with a cost-capped delayed order.

    A submitted order is cancelled if its recorded delayed fill exceeds the cap.
    This is a limit-order approximation, not an assertion of order-book liquidity.
    Quantities are fixed before seeing the fill; fractional shares are assumed.
    """
    attempts = []
    for task in sorted((t for t in tasks if t['position'] == 'flat'), key=lambda t: t['t']):
        scoring = task['scoring']
        pricing = {**config, 'market_fee_rates': scoring.get('fee_rates', {})}
        side = choose_entry(predictions[task['id']], scoring['shown_prices'], pricing, policy)
        if side is None:
            continue
        # The execution simulator sees future fills only after an order is selected.
        fill = buy_cost(scoring['fill_prices'][side], pricing, side)
        accepted = fill <= policy.maximum_cost
        attempts.append({'decision': task['decision'], 'side': side, 'accepted': accepted, 'cost_per_share': fill})
        if accepted:
            debit = policy.shares * fill
            return {'match_id': task['match_id'], 'traded': True, 'stake': debit,
                    'pnl': policy.shares * float(scoring['result'] == side) - debit,
                    'attempts': attempts}
    return {'match_id': tasks[0]['match_id'], 'traded': False, 'stake': 0.0, 'pnl': 0.0, 'attempts': attempts}


def summary(rows):
    winners = [r['pnl'] for r in rows if r['pnl'] > 0]
    losers = [r['pnl'] for r in rows if r['pnl'] < 0]
    traded = sum(r['traded'] for r in rows)
    return {'matches': len(rows), 'traded_matches': traded,
            'win_rate_on_traded_matches': len(winners) / traded if traded else None,
            'total_pnl': math.fsum(r['pnl'] for r in rows),
            'average_win': statistics.mean(winners) if winners else None,
            'average_loss': statistics.mean(losers) if losers else None,
            'worst_match_loss': min(r['pnl'] for r in rows),
            'maximum_stake': max(r['stake'] for r in rows)}


def main():
    source = Path('artifacts/forecast-only')
    protocol = json.loads((source / 'protocol.json').read_text())
    tasks = [t for t in read_tasks(source / 'tasks.jsonl.gz') if t['split'] == 'val']
    before = guard(protocol['ledger'], [t['match_id'] for t in tasks])
    predictions = {}
    for line in (source / 'predictions.jsonl').read_text().splitlines():
        row = json.loads(line)
        if row['model'] != 'base':
            if row['task_id'] in predictions:
                raise ValueError('Duplicate final-model prediction')
            predictions[row['task_id']] = row['probs']
    policies = {'original': RiskPolicy(1, .02, 1), 'smaller_only': RiskPolicy(.25, .02, 1),
                'stricter_entries_only': RiskPolicy(1, .05, .65), 'conservative': RiskPolicy()}
    report = {'scope': 'Post-hoc exploration on 16 previously inspected development matches; no inference, training or holdout evaluation.',
              'assumptions': 'Fractional shares; one position per match; no averaging down or re-entry; recorded delayed fills with fees/spread. Limit fills are an approximation. No portfolio-wide concurrent-exposure simulation.',
              'source_protocol': protocol['protocol_sha256'], 'policies': {}}
    for name, policy in policies.items():
        rows = [replay_match(ts, predictions, protocol['config'], policy) for _, ts in sorted(by_match(tasks).items())]
        report['policies'][name] = {'config': asdict(policy), 'summary': summary(rows), 'matches': rows}
    # Ensure this alternative simulator reproduces the frozen one-share baseline.
    original = json.loads((source / 'evaluation-matches.json').read_text())['final']
    for a, b in zip(report['policies']['original']['matches'], original):
        assert a['match_id'] == b['match_id'] and abs(a['pnl'] - b['pnl']) < 1e-9
    assert guard(protocol['ledger'], [t['match_id'] for t in tasks]) == before
    out = Path('artifacts/risk-policy'); out.mkdir(exist_ok=True)
    (out / 'comparison.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v['summary'] for k, v in report['policies'].items()}, indent=2))


if __name__ == '__main__':
    main()
