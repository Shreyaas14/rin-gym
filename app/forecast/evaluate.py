from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import csv
import datetime as dt
import hashlib
import json
import numpy as np
from .data import MANIFESTS, ARTIFACTS, ROOT, OUTPUT, read, write
from .model import Simulator, temperature_scale
from .replay import build_cases
from .metrics import scores, by_game, summarize, paired_delta, calibration

MODEL_NAMES = ['climatology', 'pregame_market', 'poisson_training', 'poisson_pregame', 'poisson_calibrated', 'polymarket_live']


def select_temperature(probabilities, labels, ids, splits, config):
    """The selection function masks to validation before consulting any target."""
    from .governance import assert_development_matches
    assert_development_matches(ids)
    if not (len(probabilities) == len(labels) == len(ids) == len(splits)):
        raise ValueError('Predictions, labels, match IDs and splits must align')
    membership = {}
    for mid, split in zip(ids, splits):
        if split not in ['train', 'validation', 'test']:
            raise ValueError('Unknown development split')
        if mid in membership and membership[mid] != split:
            raise ValueError('A whole match must belong to exactly one split')
        membership[mid] = split
    val = np.asarray(splits) == 'validation'
    if not val.any():
        raise ValueError('Temperature selection requires validation matches')
    tuning = []
    for temperature in config['temperature_candidates']:
        p = temperature_scale(np.asarray(probabilities)[val], temperature)
        metric = scores(p, np.asarray(labels)[val], config['log_loss_clip'])['brier']
        loss = float(np.mean(list(by_game(metric, np.asarray(ids)[val]).values())))
        tuning.append({'temperature': temperature, 'validation_brier': loss})
    selected = min(tuning, key=lambda r: (r['validation_brier'], abs(r['temperature'] - 1)))['temperature']
    return {'objective': 'equal-match-weighted validation Brier at zero observation delay', 'selected_temperature': selected, 'candidates': tuning, 'selection_match_ids': sorted(set(np.asarray(ids)[val])), 'test_used_for_selection': False}


def run():
    config = read(ROOT / 'config.json')
    manifest = read(MANIFESTS / 'manifest.json')
    from .governance import assert_development_matches
    assert_development_matches(row['match_id'] for row in manifest)
    training = [r for r in manifest if r['split'] == 'train' and r['status'] == 'ok']
    simulator = Simulator.fit(training, config)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write(OUTPUT / 'model.json', simulator.parameters)
    cases, coverage = build_cases(manifest, config)
    if not all(any(c['split'] == split for c in cases) for split in ['train', 'validation', 'test']):
        raise ValueError('All three splits must contain usable matches')
    print('Replay cases:', dict(Counter(c['split'] for c in cases)), flush=True)
    write(OUTPUT / 'coverage.json', coverage)
    fits = {}
    for case in cases:
        if case['match_id'] not in fits:
            rates, error = simulator.fit_pregame(case['prior']['probabilities'])
            fits[case['match_id']] = {'full_match_goal_rates': rates.tolist(), 'probability_fit_squared_error': error}
    write(OUTPUT / 'pregame_fits.json', fits)

    predictions = {}
    for delay in config['observation_delay_seconds']:
        out = {name: [] for name in MODEL_NAMES if name != 'poisson_calibrated'}
        for case in cases:
            obs = case['observations'][delay]
            if obs.latest_event_timestamp > obs.cutoff or obs.cutoff > case['decision_time']:
                raise AssertionError('Future event leakage')
            rates = fits[case['match_id']]['full_match_goal_rates']
            out['climatology'].append(simulator.parameters['climatology'])
            out['pregame_market'].append(case['prior']['probabilities'])
            out['polymarket_live'].append(case['market']['probabilities'])
            out['poisson_training'].append(simulator.forecast(obs))
            out['poisson_pregame'].append(simulator.forecast(obs, rates))
        predictions[delay] = {name: np.asarray(p) for name, p in out.items()}
        print('Forecasted observation delay:', delay, flush=True)

    ids = np.array([c['match_id'] for c in cases])
    labels = np.array([c['target'] for c in cases])
    splits = np.array([c['split'] for c in cases])
    selection = select_temperature(predictions[0]['poisson_pregame'], labels, ids, splits, config)
    selected = selection['selected_temperature']
    write(OUTPUT / 'selection.json', selection)
    print('Validation selected temperature:', selected, flush=True)
    for delay in predictions:
        predictions[delay]['poisson_calibrated'] = temperature_scale(predictions[delay]['poisson_pregame'], selected)

    metrics = {}
    for split in ['train', 'validation', 'test']:
        mask = splits == split
        metrics[split] = {}
        for delay, out in predictions.items():
            evaluated = {name: summarize(out[name][mask], labels[mask], ids[mask], config) for name in MODEL_NAMES}
            for name in MODEL_NAMES:
                evaluated[name]['brier_delta_vs_live'] = paired_delta(evaluated[name], evaluated['polymarket_live'], 'brier', config)
            metrics[split][str(delay)] = evaluated
    write(OUTPUT / 'metrics.json', metrics)
    test = splits == 'test'
    reliability = {name: calibration(predictions[0][name][test], labels[test], ids[test]) for name in MODEL_NAMES}
    write(OUTPUT / 'calibration.json', reliability)

    breakdowns = {}
    # Within each slice, average by game first; slice samples are still correlated.
    for slice_name, predicate in {
        'first_half': lambda c: c['phase'] == 'first_half',
        'second_half_45_to_75': lambda c: c['phase'] == 'second_half' and c['phase_minute'] < 30,
        'second_half_75_plus': lambda c: c['phase'] == 'second_half' and c['phase_minute'] >= 30,
        'level_score': lambda c: c['observations'][0].home_goals == c['observations'][0].away_goals,
        'one_goal_margin': lambda c: abs(c['observations'][0].home_goals - c['observations'][0].away_goals) == 1,
        'two_plus_margin': lambda c: abs(c['observations'][0].home_goals - c['observations'][0].away_goals) >= 2,
    }.items():
        mask = test & np.array([predicate(c) for c in cases])
        if mask.any():
            breakdowns[slice_name] = {name: summarize(predictions[0][name][mask], labels[mask], ids[mask], config) for name in MODEL_NAMES}
    write(OUTPUT / 'breakdowns.json', breakdowns)

    with (OUTPUT / 'predictions.csv').open('w') as file:
        fields = ['match_id', 'split', 'decision_utc', 'delay_seconds', 'observation_cutoff_utc', 'last_observed_event_utc', 'observed_phase', 'observed_home_goals', 'observed_away_goals', 'elapsed_period_minutes', 'expected_remaining_minutes', 'target', 'market_raw_mass', 'market_max_age_seconds']
        fields += [f'{prefix}_{outcome}' for prefix in ['market_raw', 'market_sample_utc', 'pregame_sample_utc'] for outcome in ['home', 'draw', 'away']]
        fields += [f'{name}_{outcome}' for name in MODEL_NAMES for outcome in ['home', 'draw', 'away']]
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        iso = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()
        for delay, out in predictions.items():
            for i, case in enumerate(cases):
                obs = case['observations'][delay]
                row = {'match_id': case['match_id'], 'split': case['split'], 'decision_utc': iso(case['decision_time']), 'delay_seconds': delay, 'observation_cutoff_utc': iso(obs.cutoff), 'last_observed_event_utc': iso(obs.latest_event_timestamp), 'observed_phase': obs.phase, 'observed_home_goals': obs.home_goals, 'observed_away_goals': obs.away_goals, 'elapsed_period_minutes': obs.elapsed_period_minutes, 'expected_remaining_minutes': simulator.remaining_minutes(obs), 'target': case['target'], 'market_raw_mass': case['market']['mass'], 'market_max_age_seconds': case['market']['max_age']}
                for j, outcome in enumerate(['home', 'draw', 'away']):
                    row[f'market_raw_{outcome}'] = case['market']['raw'][j]
                    row[f'market_sample_utc_{outcome}'] = iso(case['market']['times'][j])
                    row[f'pregame_sample_utc_{outcome}'] = iso(case['prior']['times'][j])
                    for name in MODEL_NAMES:
                        row[f'{name}_{outcome}'] = float(out[name][i, j])
                writer.writerow(row)
    write(OUTPUT / 'run_manifest.json', {
        'config': config,
        'config_sha256': hashlib.sha256((ROOT / 'config.json').read_bytes()).hexdigest(),
        'data_manifest_sha256': hashlib.sha256((MANIFESTS / 'manifest.json').read_bytes()).hexdigest(),
        'code_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(ROOT.glob('*.py'))},
        'numpy_version': np.__version__,
        'run_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'planned_games': dict(Counter(r['split'] for r in manifest)),
        'included_games': {s: len(set(ids[splits == s])) for s in ['train', 'validation', 'test']},
        'snapshots': dict(Counter(splits)),
        'timestamp_assumption': 'FIFA event time is treated as observation time plus scenario delay; actual publication/receipt times are unavailable.',
        'score_batch_policy': 'maximum score across simultaneous events; never future batches',
        'market_normalization': 'home/draw/away binary Yes prices divided by their sum; raw inputs exported',
    })
    from .report import create_report
    create_report(manifest, cases, predictions, metrics, selection, coverage, reliability, breakdowns)
    print('Held-out Brier (lower is better):')
    for name in MODEL_NAMES:
        print(name, round(metrics['test']['0'][name]['brier'], 6))
    print(ARTIFACTS / 'report.html')
