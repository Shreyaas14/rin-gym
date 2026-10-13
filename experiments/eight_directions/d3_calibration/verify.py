"""Meaningful checks of invalid handling, partitions, and deterministic results."""
import hashlib
import json
from pathlib import Path

import numpy as np

from experiments.eight_directions.d3_calibration.analyze import OUT, ROOT, extract, temperature

r = json.loads((OUT / 'results.json').read_text())
p = json.loads((OUT / 'protocol.json').read_text())
assert hashlib.sha256((OUT / 'protocol.json').read_bytes()).hexdigest() == r['protocol_sha256']
for file, digest in r['input_checksums'].items():
    assert hashlib.sha256((ROOT / file).read_bytes()).hexdigest() == digest
assert set(p['chronological_partition']['fit_match_ids']).isdisjoint(p['chronological_partition']['assessment_match_ids'])
assert r['guard_before'] == r['guard_after'] == p['guard_before']
assert extract({'completion': '{"probs":{"home":0.4,"draw":0.3,"away":0.29}}'}, 1e-6)[0] is None
assert extract({'completion': '{"probs":{"home":0.4,"draw":0.3,"away":0.29}}'}, .05)[0] is not None
assert extract({'completion': '{"probs":{"home":0.4,"home":0.4,"draw":0.3,"away":0.3}}'}, .05)[0] is None
assert temperature(None, .7) is None
for arm in ['closed_loop/original', 'closed_loop/conservative']:
    for mode in ['strict', 'tolerance_0.05']:
        v = r['arms'][arm][mode]
        assert v['fit']['fit_valid_matches'] == 39
        assert v['raw']['matches'] == 33
        assert v['raw']['invalid_inclusive']['valid_rate'] == v['calibrated']['invalid_inclusive']['valid_rate']
        assert v['raw_calibration_and_discrimination']['top_class_accuracy'] == v['calibrated_calibration_and_discrimination']['top_class_accuracy']
        assert v['calibrated_minus_raw']['brier']['mean'] < 0
for line in (OUT / 'analysis_receipts.jsonl').read_text().splitlines():
    x = json.loads(line)
    if 'calibrated' in x:
        assert (x['p'] is None) == (x['calibrated'] is None)
        if x['p'] is not None:
            assert np.argmax(x['p']) == np.argmax(x['calibrated'])
            assert abs(sum(x['calibrated']) - 1) < 1e-9
result = {'passed': True, 'checks': ['source/protocol hashes', 'disjoint whole-match chronology', 'unchanged holdout guard', 'uniform strict/tolerance invalid handling', 'duplicate rejection', 'temperature preserves invalidity and argmax', '39-fit/33-assessment match counts', 'reported direction of Brier effect'], 'paid_requests': 0}
(OUT / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result))
