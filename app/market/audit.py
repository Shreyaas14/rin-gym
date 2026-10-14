"""Offline verification of an exported session's deterministic exchange ledger.

This verifies orders, executions, reservations, and balances. It does not certify
the model's forecasts or the provenance of the source match data.
"""

import argparse
import json
from pathlib import Path
import sys

from .engine import Exchange


def _canonical(value):
    """Compare JSON types as well as values (e.g. True must not equal 1)."""
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':'))


def _journal_entry(entry):
    result = dict(entry)
    if result.get('type') == 'cancel' and result.get('reason') in ('owner', 'refresh'):
        result['reason'] = 'owner_or_refresh'
    return result


def _snapshot(snapshot):
    result = dict(snapshot)
    result['trades'] = [{key: value for key, value in trade.items() if key != 'time'}
                        for trade in snapshot['trades']]
    return result


def verify(document):
    """Replay an export and return counts, or raise ValueError on inconsistency.

    Orders reproduce their own fills and immediate cancellations. A recorded
    fill is never applied directly to the ledger, so omitted or forged fills
    cannot manufacture cash or inventory. Refresh and owner cancellations share
    the same exchange effect and are the sole normalized journal distinction.
    """
    try:
        _canonical(document)
        if not isinstance(document, dict) or document.get('schema') != 'market-session/v1':
            raise ValueError('Expected a market-session/v1 export')
        journal = document['exchange']
        state = document['state']
        if not isinstance(journal, list) or not isinstance(state, dict):
            raise ValueError('Malformed exchange journal or final state')
        replay = Exchange()
        index = 0
        while index < len(journal):
            entry = journal[index]
            if not isinstance(entry, dict):
                raise ValueError(f'Malformed exchange entry at index {index}')
            before = len(replay.journal)
            kind = entry.get('type')
            if kind == 'order':
                order = entry['order']
                replay.submit(order['owner'], order['side'], order['price'], order['size'], entry['tif'])
            elif kind == 'cancel':
                if entry.get('reason') not in ('owner', 'refresh'):
                    raise ValueError(f'Unexpected standalone cancellation at index {index}')
                replay.cancel(entry['owner'], entry['order_id'])
            elif kind == 'settlement':
                replay.settle(entry['outcome'])
            else:
                raise ValueError(f'Unexpected exchange entry at index {index}')
            generated = replay.journal[before:]
            recorded = journal[index:index + len(generated)]
            if not generated or _canonical([_journal_entry(e) for e in generated]) != _canonical(
                    [_journal_entry(e) for e in recorded]):
                raise ValueError(f'Exchange journal mismatch at index {index}')
            index += len(generated)

        market = state['market']
        midpoint = market['midpoint']
        # A current quote is independently determined by the replayed book.
        book = replay.snapshot()
        actual_midpoint = ((book['bids'][0]['price'] + book['asks'][0]['price']) / 2
                           if book['bids'] and book['asks'] else None)
        if midpoint != actual_midpoint or isinstance(midpoint, bool):
            raise ValueError('Final market midpoint disagrees with the replayed book')
        mark = midpoint
        if mark is None:
            history = market['history']
            if not isinstance(history, list):
                raise ValueError('Malformed market mark history')
            for point in reversed(history):
                if point['midpoint'] is not None:
                    mark = point['midpoint']
                    break
        reproduced = replay.snapshot(mark=mark)
        if _canonical(_snapshot(state['exchange'])) != _canonical(reproduced):
            raise ValueError('Final exchange state disagrees with the replayed journal')
        return {
            'valid': True, 'orders': sum(e['type'] == 'order' for e in journal),
            'fills': len(replay.trades), 'settled': replay.settled,
            'total_cash': sum(account.cash for account in replay.accounts.values()),
        }
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError('Malformed market session export') from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description='Replay and verify a saved paper-market exchange journal')
    parser.add_argument('journal', type=Path, help='Downloaded market-session/v1 JSON export')
    args = parser.parse_args(argv)
    try:
        report = verify(json.loads(args.journal.read_text()))
    except (OSError, ValueError) as exc:
        print(json.dumps({'valid': False, 'error': str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
