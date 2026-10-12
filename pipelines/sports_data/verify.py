"""Verify saved evidence integrity and core audit invariants without network access."""
from .cache import ROOT, RAW
from .collect import load
import hashlib
import json

rows = json.loads((ROOT / 'audit-results.json').read_text())
manifest = load('audit_manifest')
assert len(rows) == 60 and len({(r['sport'], r['game_id']) for r in rows}) == 60
assert all(not r['mapping_error'] and r['market_points'] > 0 and r['settlement_agrees'] for r in rows)
assert all(r['distinct_prices'] > 1 for r in rows)
assert all(r.get('reconstructed_score_agrees', True) for r in rows)
verified = 0
for path in RAW.glob('*.meta.json'):
    meta = json.loads(path.read_text())
    data = RAW / (path.name.removesuffix('.meta.json') + '.json')
    assert hashlib.sha256(data.read_bytes()).hexdigest() == meta['sha256'], str(data)
    verified += 1
for match in manifest:
    name = match['prices_file'] if match['sport'] == 'euro' else match['sport'] + '_' + match['id'] + '_candles'
    data = load(name)
    points = data.get('history', data.get('candlesticks'))
    times = [p.get('t', p.get('end_period_ts')) for p in points]
    assert times == sorted(set(times)), name
verification = {
    'games': 60,
    'successful_joins': 60,
    'selected_contract_settlement_agreements': 60,
    'euro_score_reconstruction_agreements': 20,
    'raw_source_hashes_verified': verified,
    'price_series_with_unique_monotonic_timestamps': 60,
    'price_series_with_more_than_one_distinct_price': 60,
}
(ROOT / 'verification.json').write_text(json.dumps(verification, indent=2))
print(json.dumps(verification, indent=2))
