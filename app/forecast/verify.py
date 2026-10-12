"""Audit the completed run, including exported rows and independent score recomputation."""
import collections
import csv
import hashlib
import math
from .data import MANIFESTS, ROOT, RAW, OUTPUT, FIFA, read, write, epoch
from .evaluate import MODEL_NAMES


def main():
    manifest = read(MANIFESTS / 'manifest.json')
    rows = {r['match_id']: r for r in manifest if r['status'] == 'ok'}
    config = read(ROOT / 'config.json')
    model = read(OUTPUT / 'model.json')
    selection = read(OUTPUT / 'selection.json')
    run = read(OUTPUT / 'run_manifest.json')
    metrics = read(OUTPUT / 'metrics.json')
    train = {r['match_id'] for r in rows.values() if r['split'] == 'train'}
    val = {r['match_id'] for r in rows.values() if r['split'] == 'validation'}
    test = {r['match_id'] for r in rows.values() if r['split'] == 'test'}
    assert not (train & val or train & test or val & test)
    assert set(model['training_match_ids']) == train
    assert set(selection['selection_match_ids']) <= val and not selection['test_used_for_selection']
    assert max(epoch(rows[i]['regulation_end_utc']) for i in train) < min(epoch(rows[i]['kickoff_utc']) for i in val)
    assert max(epoch(rows[i]['regulation_end_utc']) for i in val) < min(epoch(rows[i]['kickoff_utc']) for i in test)
    assert hashlib.sha256((ROOT / 'config.json').read_bytes()).hexdigest() == run['config_sha256']
    assert hashlib.sha256((MANIFESTS / 'manifest.json').read_bytes()).hexdigest() == run['data_manifest_sha256']
    for name, digest in run['code_sha256'].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
    verified = 0
    for path in RAW.glob('*.meta.json'):
        meta = read(path)
        source = path.with_name(path.name.replace('.meta.json', '.json'))
        assert hashlib.sha256(source.read_bytes()).hexdigest() == meta['sha256'], str(source)
        verified += 1
    for row in rows.values():
        source = FIFA / f"fifa_{row['match_id']}_timeline.json"
        assert hashlib.sha256(source.read_bytes()).hexdigest() == row['fifa_sha256']
    counts = collections.Counter()
    sums = collections.defaultdict(lambda: [0., 0., 0])
    snapshots = collections.defaultdict(set)
    with (OUTPUT / 'predictions.csv').open() as file:
        for row in csv.DictReader(file):
            mid, split, delay = row['match_id'], row['split'], row['delay_seconds']
            t, cutoff = epoch(row['decision_utc']), epoch(row['observation_cutoff_utc'])
            assert abs(t - cutoff - int(delay)) < 1e-6
            assert epoch(row['last_observed_event_utc']) <= cutoff <= t
            assert t < epoch(rows[mid]['regulation_end_utc'])
            assert split == rows[mid]['split'] and int(row['target']) == rows[mid]['target']
            key = (mid, t)
            assert key not in snapshots[delay]
            snapshots[delay].add(key)
            for outcome in ['home', 'draw', 'away']:
                assert 0 <= t - epoch(row['market_sample_utc_' + outcome]) <= config['price_max_age_seconds']
                preg = epoch(row['pregame_sample_utc_' + outcome])
                preg_cut = epoch(rows[mid]['kickoff_utc']) - config['pregame_cutoff_before_actual_kickoff_seconds']
                assert 0 <= preg_cut - preg <= config['pregame_price_max_age_seconds']
            counts[(split, delay)] += 1
            target = int(row['target'])
            for name in MODEL_NAMES:
                p = [float(row[name + '_' + k]) for k in ['home', 'draw', 'away']]
                assert all(0 <= v <= 1 for v in p) and abs(sum(p)-1) < 1e-8
                brier = sum((v - int(i == target))**2 for i, v in enumerate(p))
                logloss = -math.log(max(config['log_loss_clip'], p[target]))
                accum = sums[(split, delay, name, mid)]
                accum[0] += brier
                accum[1] += logloss
                accum[2] += 1
    assert all(snapshot == snapshots['0'] for snapshot in snapshots.values())
    for split in ['train', 'validation', 'test']:
        for delay in map(str, config['observation_delay_seconds']):
            for name in MODEL_NAMES:
                games = [v for k, v in sums.items() if k[:3] == (split, delay, name)]
                for j, metric in enumerate(['brier', 'log_loss']):
                    independently_scored = sum(v[j]/v[2] for v in games)/len(games)
                    assert abs(independently_scored - metrics[split][delay][name][metric]) < 1e-10
    result = {'games': len(rows), 'verified_market_source_hashes': verified, 'verified_fifa_hashes': len(rows), 'prediction_rows': sum(counts.values()), 'identical_snapshots_across_delays': True, 'chronological_labels_available_before_next_split': True, 'future_price_or_event_violations': 0, 'independently_recomputed_metrics_agree': True}
    write(OUTPUT / 'verification.json', result)
    print(result)


if __name__ == '__main__':
    main()
