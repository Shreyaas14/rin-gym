"""Proper scores with equal match weights and paired match-level uncertainty."""
import numpy as np


def scores(probabilities, targets, clip=1e-6):
    p = np.asarray(probabilities, float)
    y = np.asarray(targets, int)
    if p.shape != (len(y), 3) or np.any(~np.isfinite(p)) or np.any(p < 0) or not np.allclose(p.sum(axis=1), 1):
        raise ValueError('Expected normalized home/draw/away probabilities')
    onehot = np.eye(3)[y]
    return {'brier': np.sum((p-onehot)**2, axis=1), 'log_loss': -np.log(np.clip(p[np.arange(len(y)), y], clip, 1)), 'accuracy': (p.argmax(axis=1) == y).astype(float)}


def by_game(values, ids):
    ids = np.asarray(ids)
    return {str(mid): float(np.mean(np.asarray(values)[ids == mid])) for mid in sorted(set(ids))}


def bootstrap_interval(game_values, replicates, seed):
    values = np.asarray(list(game_values.values()), float)
    if len(values) == 0:
        return [None, None]
    indices = np.random.default_rng(seed).integers(0, len(values), (replicates, len(values)))
    means = values[indices].mean(axis=1)
    return np.quantile(means, [.025, .975]).tolist()


def summarize(probabilities, targets, ids, config):
    result = {'games': len(set(ids)), 'snapshots': len(ids)}
    for metric, values in scores(probabilities, targets, config['log_loss_clip']).items():
        games = by_game(values, ids)
        result[metric] = float(np.mean(list(games.values())))
        result[metric + '_ci95'] = bootstrap_interval(games, config['bootstrap_replicates'], config['seed'])
        result[metric + '_by_game'] = games
    return result


def paired_delta(model, reference, metric, config):
    a, b = model[metric + '_by_game'], reference[metric + '_by_game']
    if set(a) != set(b):
        raise ValueError('Paired comparison needs identical games')
    differences = {mid: a[mid] - b[mid] for mid in a}
    return {'mean': float(np.mean(list(differences.values()))), 'ci95': bootstrap_interval(differences, config['bootstrap_replicates'], config['seed'])}


def calibration(probabilities, targets, ids):
    p, y, ids = np.asarray(probabilities), np.asarray(targets), np.asarray(ids)
    weights = np.zeros(len(ids))
    for mid in set(ids):
        mask = ids == mid
        weights[mask] = 1 / (len(set(ids)) * mask.sum())
    result = {}
    for k, name in enumerate(['home', 'draw', 'away']):
        bins = []
        for i in range(10):
            mask = (p[:, k] >= i / 10) & ((p[:, k] < (i+1) / 10) if i < 9 else (p[:, k] <= 1))
            if not mask.any():
                continue
            w = weights[mask]
            bins.append({'lower': i/10, 'upper': (i+1)/10, 'snapshots': int(mask.sum()), 'games': len(set(ids[mask])), 'weight': float(w.sum()), 'predicted': float(np.average(p[mask, k], weights=w)), 'observed': float(np.average(y[mask] == k, weights=w))})
        result[name] = bins
    return result
