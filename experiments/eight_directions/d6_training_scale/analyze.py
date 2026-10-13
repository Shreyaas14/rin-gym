"""Bounded D6 offline audit and train-only integration fixture; no service clients."""
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from experiments.forecast_only.core import guard, messages, parse

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'artifacts/eight-directions/d6_training_scale'
LABELS = ('home', 'draw', 'away')
SEED = 20261013


def read(path):
    path = ROOT / path
    if path.suffix == '.json':
        return json.loads(path.read_text())
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt') as stream:
        return [json.loads(line) for line in stream]


def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def extract(record, tolerance=1e-6):
    """Keep exact output schema for both policies; tolerance is diagnostic only."""
    if record.get('truncated'):
        return None, 'truncated'
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate')
            result[key] = value
        return result
    try:
        obj = json.loads(record['completion'], object_pairs_hook=unique)
        if not isinstance(obj, dict) or set(obj) != {'probs'}:
            return None, 'schema'
        probs = obj['probs']
        if not isinstance(probs, dict) or set(probs) != set(LABELS):
            return None, 'schema'
        values = [probs[k] for k in LABELS]
        if any(type(v) not in (float, int) or not math.isfinite(v) or not 0 <= v <= 1 for v in values):
            return None, 'range'
        total = math.fsum(values)
        if total <= 0 or abs(total - 1) > tolerance:
            return None, 'sum'
        return np.array(values, dtype=float) / total, None
    except (ValueError, KeyError, TypeError):
        return None, 'json_or_duplicate'


def serialize(probs):
    """Quantize with a residual component, avoiding sum-of-decimals failures."""
    values = np.array(probs, dtype=float, copy=True)
    if values.shape != (3,) or not np.isfinite(values).all() or (values < 0).any() or values.sum() <= 0:
        raise ValueError('Invalid probabilities')
    values /= values.sum()
    # Put the residual on the largest component: rounding the two smaller
    # components cannot make the residual negative.
    pivot = int(values.argmax())
    fixed = [None] * 3
    for index in range(3):
        if index != pivot:
            fixed[index] = Decimal(str(values[index])).quantize(Decimal('.000001'), rounding=ROUND_HALF_UP)
    fixed[pivot] = Decimal(1) - sum(v for v in fixed if v is not None)
    return '{"probs":{' + ','.join('"' + key + '":' + format(value, 'f') for key, value in zip(LABELS, fixed)) + '}}'


def proper_probability_loss(logits, target, kind='nll'):
    """Local integration reference, not a Tinker model/head implementation.

    Targets are categorical distributions, including training outcome one-hots.
    NLL and Brier are proper scores in expectation over outcomes, whereas JSON
    answer-token cross entropy is a separate formatting objective.
    """
    z, y = np.asarray(logits, dtype=float), np.asarray(target, dtype=float)
    if z.shape != (3,) or y.shape != (3,) or not np.isfinite(z).all() or not np.isfinite(y).all() or (y < 0).any() or abs(y.sum() - 1) > 1e-6:
        raise ValueError('Invalid logits/target')
    logp = z - z.max() - math.log(np.exp(z - z.max()).sum())
    p = np.exp(logp)
    if kind == 'nll':
        return float(-(y * logp).sum()), p - y
    if kind == 'brier':
        residual = p - y
        return float(np.square(residual).sum()), 2 * p * (residual - np.dot(p, residual))
    raise ValueError('Unsupported score')


def summarize(rows, key):
    grouped = defaultdict(list)
    for row in rows:
        p = row[key]
        y = np.eye(3)[row['y']]
        grouped[row['mid']].append({'brier': float(np.square(p - y).sum()) if p is not None else 2.,
                                    'log_loss': -math.log(max(1e-6, p[row['y']])) if p is not None else -math.log(1e-6),
                                    'valid_rate': float(p is not None)})
    return {mid: {metric: float(np.mean([v[metric] for v in values])) for metric in ('brier', 'log_loss', 'valid_rate')}
            for mid, values in grouped.items()}


def aggregate(rows, key):
    grouped = summarize(rows, key)
    return {'matches': len(grouped), 'decisions': len(rows), 'valid_decisions': sum(r[key] is not None for r in rows),
            **{metric: float(np.mean([v[metric] for v in grouped.values()])) for metric in ('brier', 'log_loss', 'valid_rate')}}


def paired(rows, left, right):
    a, b = summarize(rows, left), summarize(rows, right)
    ids = sorted(set(a) & set(b))
    index = np.random.default_rng(SEED).integers(0, len(ids), (4000, len(ids)))
    return {metric: {'mean': float((delta := np.array([a[mid][metric] - b[mid][metric] for mid in ids])).mean()),
                     'match_bootstrap_95': np.quantile(delta[index].mean(axis=1), [.025, .975]).tolist()}
            for metric in ('brier', 'log_loss', 'valid_rate')}


def proposed_budget():
    """Two-seed future plan; conservative prompt/output limits, no execution."""
    pricing = read('research/eight-directions/pricing.json')
    rates = pricing['usd_per_million_tokens']
    seeds, train_decisions, val_decisions, group_size, prompt_limit, output_limit = 2, 80, 80, 4, 2048, 128
    # Shared base evaluation plus SFT and SFT+RL evaluation for every seed.
    evaluation_requests = val_decisions * (1 + 2 * seeds)
    rollout_requests = train_decisions * group_size * seeds
    sft_examples = train_decisions * seeds
    input_tokens = (evaluation_requests + rollout_requests) * prompt_limit
    sample_tokens = (evaluation_requests + rollout_requests) * output_limit
    training_tokens = (rollout_requests + sft_examples) * (prompt_limit + output_limit)
    upper = (input_tokens * rates['prefill'] + sample_tokens * rates['sample'] + training_tokens * rates['train']) / 1e6
    return {'status': 'proposal only; no allocation or paid calls', 'seeds': [20261013, 20261014],
            'model': pricing['model'], 'pricing_source': pricing['source'], 'rates_verified_on': pricing['verified_on'],
            'published_usd_per_million': rates, 'max_prompt_tokens': prompt_limit, 'max_output_tokens': output_limit,
            'max_evaluation_requests': evaluation_requests, 'max_rl_rollout_requests': rollout_requests,
            'max_sft_examples_one_epoch': sft_examples, 'max_prefill_tokens': input_tokens, 'max_sample_tokens': sample_tokens,
            'max_training_tokens': training_tokens, 'full_uncached_token_upper_usd': upper,
            'double_token_upper_usd': 2 * upper, 'storage_reserve_usd': .05, 'unallocated_headroom_usd': .25,
            'required_unreserved_combined_budget_usd': 2 * upper + .05 + .25,
            'storage_assumption': 'At most 0.5 GB-month billed at $0.10/GB-month; verify billing size/TTL before saving.',
            'admission_rule': 'Do not run now. A future run must verify current official rates, exact rendered prompt and completion limits, SDK retry semantics, training accounting and storage bound; require combined recorded completed spend + all reservations + this bound <= latest user total cap. Any unknown bound blocks calls; no automatic retries.',
            'config': {'lora_rank': 32, 'learning_rate': 1e-5, 'sft_epochs': 1, 'rl_steps': 4, 'rl_groups_per_batch': 4,
                       'rl_group_size': 4, 'rl_train_temperature': 1., 'eval_temperature': 0.,
                       'rl_reward': 'negative match-mean multiclass Brier; exact-schema invalid=2',
                       'checkpoint_choice': 'Final only, no intermediate selection', 'execution': 'Frozen forecasts only; no execution change'}}


def main():
    protocol = read('artifacts/eight-directions/d6_training_scale/protocol.json')
    setup = read('research/eight-directions/setup.json')
    cohorts = protocol['cohorts']
    before = guard(setup['guard_ledger'], cohorts['train'] + cohorts['val'])
    assert before == read('artifacts/eight-directions/d6_training_scale/guard_before.json')
    tasks = read('inputs/forecast-only/tasks.jsonl.gz')
    assert {str(t['match_id']) for t in tasks} == set(cohorts['train'] + cohorts['val'])
    flat = {t['id']: t for t in tasks if t['position'] == 'flat'}
    predictions = read('inputs/forecast-only/predictions.jsonl')
    model_labels = sorted({r['model'] for r in predictions})
    assert 'base' in model_labels and len(model_labels) == 2
    final_label = next(m for m in model_labels if m != 'base')
    joined = defaultdict(dict)
    error_counts = {'base': Counter(), 'final': Counter()}
    for record in predictions:
        task = flat[record['task_id']]
        assert task['split'] == 'val'
        label = 'base' if record['model'] == 'base' else 'final'
        strict, error = extract(record)
        tolerant, _ = extract(record, .05)
        joined[record['task_id']].update({'mid': str(task['match_id']), 'y': LABELS.index(task['scoring']['result']),
                                        label: strict, label + '_tolerant': tolerant,
                                        'market': np.array([task['scoring']['market_probs'][side] for side in LABELS])})
        if error:
            error_counts[label][error] += 1
    rows = list(joined.values())
    assert len(rows) == 80 and all('base' in r and 'final' in r for r in rows)
    common = [r for r in rows if r['base'] is not None and r['final'] is not None]
    strict = {key: aggregate(rows, key) for key in ('base', 'final', 'market')}
    tolerant = {key: aggregate(rows, key + '_tolerant') for key in ('base', 'final')}
    curves, rolled, receipts = [], [], []
    template = np.array([.4, .3, .3])
    posthoc = {'status': 'Post-hoc confound diagnostic; see separate posthoc_prompt_diagnostic_protocol.json',
               'template_probabilities': template.tolist(),
               'validation_template_echo': {key: {'exact_vector_count': sum(r[key + '_tolerant'] is not None and np.array_equal(r[key + '_tolerant'], template) for r in rows),
                                                  'decisions': len(rows)} for key in ('base', 'final')},
               'training_batches': [],
               'interpretation': 'A discovered echo confound can motivate an example-free SYSTEM ablation, but these frozen counts cannot establish its causal impact. Format self-distillation can reproduce the same bias.'}
    metric_rows = read('inputs/forecast-only/training/metrics.jsonl')
    for iteration in range(4):
        path = f'inputs/forecast-only/training/iteration_{iteration:06d}/train_rollout_summaries.jsonl'
        trajectories = read(path)
        steps = [step for trajectory in trajectories for step in trajectory['steps']]
        mids = {step['logs']['task_id'].split(':')[0] for step in steps}
        assert mids <= set(cohorts['train'])
        errors = Counter()
        values = []
        for step in steps:
            tid = step['logs']['task_id']
            task = flat[tid]
            assert task['split'] == 'train'
            p, err = extract(step['logs'])
            if err:
                errors[err] += 1
            values.append(float(np.square(p - np.eye(3)[LABELS.index(task['scoring']['result'])]).sum()) if p is not None else 2.)
            rolled.append({'task_id': tid, 'iteration': iteration, **step['logs']})
        grouped = Counter(step['logs']['task_id'].split(':')[0] for step in steps)
        assert len(trajectories) == 16 and len(steps) == 80 and len(mids) == 4
        metric = metric_rows[iteration]
        assert abs(np.mean(values) - metric['env/all/brier']) < 1e-9
        rewards = defaultdict(list)
        for trajectory in trajectories:
            rewards[trajectory['group_idx']].append(trajectory['total_reward'])
        tolerant_values = [extract(step['logs'], .05)[0] for step in steps]
        posthoc['training_batches'].append({'iteration': iteration, 'decisions': len(steps),
                                           'exact_vector_count': sum(p is not None and np.array_equal(p, template) for p in tolerant_values),
                                           'total_reward_std_within_group': [float(np.std(v)) for _, v in sorted(rewards.items())],
                                           'zero_reward_variance_groups': sum(float(np.std(v)) == 0 for v in rewards.values()),
                                           'groups': len(rewards),
                                           'note': 'Each group has four same-match stochastic trajectories. Zero total-reward variance supplies no reward-ranking signal within that group. Nonzero variance may reflect invalidity as well as forecast content.'})
        curves.append({'iteration': iteration, 'sampling_checkpoint_step': trajectories[0]['sampling_client_step'],
                       'unique_matches': len(mids), 'match_ids': sorted(mids), 'trajectories': len(trajectories),
                       'decisions': len(steps), 'brier': metric['env/all/brier'], 'valid_rate': metric['env/all/valid'],
                       'format_failures': dict(errors), 'logged_entropy': metric['optim/entropy'],
                       'logged_kl_sample_train_v2': metric['optim/kl_sample_train_v2'],
                       'mixed_reward_group_fraction': metric['env/all/by_group/frac_mixed'],
                       'note': 'Different training matches in each batch; training curves do not measure same-cohort improvement.'})
    # Select one earliest sum-tolerant training completion per observation,
    # without filtering by correctness/reward; no validation rows or labels.
    teacher = {}
    for record in rolled:
        p, _ = extract(record, .05)
        if p is not None and record['task_id'] not in teacher:
            teacher[record['task_id']] = serialize(p)
    training_records = []
    for task in sorted((t for t in flat.values() if t['split'] == 'train'), key=lambda t: (t['t'], t['id'])):
        target = np.eye(3)[LABELS.index(task['scoring']['result'])].tolist()
        obs = messages(task)
        training_records.append({'id': task['id'], 'match_id': task['match_id'], 'split': 'train',
                                 'observation_t': task['t'], 'messages': obs,
                                 'training_only_class_target': target,
                                 'match_weight': 1 / (len(cohorts['train']) * 5),
                                 'format_sft_target': teacher.get(task['id']),
                                 'format_target_kind': 'Canonicalized train rollout self-distillation, not calibrated truth or proper probability supervision'})
    assert len(training_records) == 80 and {r['match_id'] for r in training_records} == set(cohorts['train'])
    assert all(parse(r['format_sft_target']) is not None for r in training_records if r['format_sft_target'])
    (OUT / 'train_only_integration.jsonl').write_text(''.join(json.dumps(r, allow_nan=False) + '\n' for r in training_records))
    d3 = read('artifacts/eight-directions/d3_calibration/results.json')
    d3_summary = {arm: {parser: {'raw': values['raw'], 'valid_only_minus_market': values['raw_valid_only_minus_market_same_rows']}
                        for parser, values in d3['arms'][arm].items()} for arm in ('pilot/base', 'pilot/final')}
    source_paths = ['inputs/MANIFEST.json', 'research/eight-directions/pricing.json', 'inputs/forecast-only/protocol.json',
                    'inputs/forecast-only/tasks.jsonl.gz', 'inputs/forecast-only/predictions.jsonl',
                    'inputs/forecast-only/training/config.json', 'inputs/forecast-only/training/metrics.jsonl',
                    'inputs/forecast-only/summary.json', 'inputs/forecast-only/usage.json',
                    'artifacts/eight-directions/d3_calibration/results.json']
    source_paths += [f'inputs/forecast-only/training/iteration_{i:06d}/train_rollout_summaries.jsonl' for i in range(4)]
    for path in source_paths:
        receipts.append({'path': path, 'sha256': hashlib.sha256((ROOT / path).read_bytes()).hexdigest()})
    write('data_receipts.json', receipts)
    write('scaling_proposal.json', proposed_budget())
    write('results.json', {'classification': 'inconclusive for RL benefit; negative vs market; model-training efficacy unresolved',
                           'analysis_kind': protocol['analysis_kind'], 'historical_training_seed_count': 1,
                           'historical_training_seed': SEED, 'historical_train_matches': 16, 'historical_val_matches': 16,
                           'historical_rl_updates': 4, 'historical_rollout_trajectories': 64, 'historical_rollout_decisions': 320,
                           'strict': strict, 'strict_final_minus_base': paired(rows, 'final', 'base'),
                           'strict_final_minus_market': paired(rows, 'final', 'market'),
                           'tolerance_0_05_diagnostic': tolerant,
                           'tolerant_final_minus_base': paired(rows, 'final_tolerant', 'base_tolerant'),
                           'common_strict_valid': {'count': len(common), 'base': aggregate(common, 'base'), 'final': aggregate(common, 'final'),
                                                  'final_minus_base': paired(common, 'final', 'base')},
                           'format_failures': {k: dict(v) for k, v in error_counts.items()},
                           'prediction_changes': {'same_tolerant_probability_vector': sum(np.array_equal(r['base_tolerant'], r['final_tolerant']) for r in rows),
                                                  'decisions': len(rows), 'newly_strict_valid': sum(r['base'] is None and r['final'] is not None for r in rows),
                                                  'lost_strict_valid': sum(r['base'] is not None and r['final'] is None for r in rows)},
                           'training_curves': curves, 'posthoc_prompt_template_diagnostic': posthoc,
                           'offline_integration': {'status': 'data/loss/serialization reference only, no trained model or API integration',
                                                   'train_records': len(training_records), 'format_sft_records': len(teacher),
                                                   'target_uses': 'Outcome one-hot remains outside prompts, for proper class loss/reward only; numeric serialized self-distillation is formatting assistance, not a proper forecasting loss.'},
                           'd3_supporting_posthoc': d3_summary, 'historical_usage_estimate': read('inputs/forecast-only/usage.json'),
                           'budget': {'actual_incremental_usd': 0., 'reserved_incremental_usd': 0., 'paid_calls': 0},
                           'seal_before': before, 'seal_after': guard(setup['guard_ledger'], cohorts['train'] + cohorts['val'])})
    after = guard(setup['guard_ledger'], cohorts['train'] + cohorts['val'])
    assert after == before
    write('guard_after.json', after)
    print(json.dumps({'strict_final_minus_base': paired(rows, 'final', 'base')['brier'],
                      'tolerant_final_minus_base': paired(rows, 'final_tolerant', 'base_tolerant')['brier'],
                      'train_records': len(training_records), 'paid_calls': 0, 'guard_unchanged': after == before}))


if __name__ == '__main__':
    main()
