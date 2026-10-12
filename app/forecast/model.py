"""A small independent-Poisson continuation simulator, with exact outcome scoring."""
from __future__ import annotations

import argparse
import json
import numpy as np
from .data import MANIFESTS, ARTIFACTS, ROOT, OUTPUT, FIFA, read, epoch


def poisson_pmf(rate, maximum=40):
    rate = np.atleast_1d(np.asarray(rate, dtype=float))
    if np.any(rate < 0) or np.any(~np.isfinite(rate)):
        raise ValueError('Invalid Poisson rate')
    p = np.empty((len(rate), maximum + 1))
    p[:, 0] = np.exp(-rate)
    for k in range(1, maximum + 1):
        p[:, k] = p[:, k-1] * rate / k
    if np.any(1 - p.sum(axis=1) > 1e-8):
        raise ValueError('Truncation tail too large; increase maximum goals')
    return p / p.sum(axis=1, keepdims=True)


def outcome_probabilities(home_rate, away_rate, score_difference=0, maximum=40):
    h, a, diff = np.broadcast_arrays(np.asarray(home_rate, float), np.asarray(away_rate, float), np.asarray(score_difference, int))
    h, a, diff = h.ravel(), a.ravel(), diff.ravel()
    ph, pa = poisson_pmf(h, maximum), poisson_pmf(a, maximum)
    cdf = np.cumsum(pa, axis=1)
    goals = np.arange(maximum + 1)[None, :]
    threshold = goals + diff[:, None]
    ridx = np.arange(len(h))[:, None]
    draw = np.sum(ph * np.where((threshold >= 0) & (threshold <= maximum), pa[ridx, np.clip(threshold, 0, maximum)], 0), axis=1)
    less = threshold - 1
    win = np.sum(ph * np.where(less < 0, 0, np.where(less >= maximum, 1, cdf[ridx, np.clip(less, 0, maximum)])), axis=1)
    result = np.maximum(0, np.column_stack([win, draw, 1-win-draw]))
    return result / result.sum(axis=1, keepdims=True)


def temperature_scale(probabilities, temperature):
    logits = np.log(np.clip(probabilities, 1e-12, 1)) / temperature
    logits -= logits.max(axis=-1, keepdims=True)
    p = np.exp(logits)
    return p / p.sum(axis=-1, keepdims=True)


class Simulator:
    def __init__(self, parameters, config):
        self.parameters = parameters
        self.config = config
        grid = np.linspace(config['rate_grid_min'], config['rate_grid_max'], config['rate_grid_points'])
        gh, ga = np.meshgrid(grid, grid, indexing='ij')
        self.rate_pairs = np.column_stack([gh.ravel(), ga.ravel()])
        self.grid_probabilities = outcome_probabilities(gh.ravel(), ga.ravel(), maximum=config['poisson_max_goals'])

    @classmethod
    def fit(cls, training_rows, config):
        if not training_rows or any(row['split'] != 'train' for row in training_rows):
            raise ValueError('Fit must receive training matches only')
        durations = [[], []]
        goals = np.zeros(2)
        counts = np.zeros(3)
        ids = []
        for row in training_rows:
            events = read(FIFA / f"fifa_{row['match_id']}_timeline.json")['Event']
            for j, period in enumerate([3, 5]):
                start = next(epoch(e['Timestamp']) for e in events if e['Period'] == period and e['TypeLocalized'][0]['Description'] == 'Start Time')
                end = next(epoch(e['Timestamp']) for e in events if e['Period'] == period and e['TypeLocalized'][0]['Description'] == 'End Time')
                durations[j].append((end - start) / 60)
            goals += row['final_score']
            counts[row['target']] += 1
            ids.append(row['match_id'])
        exposure = sum(map(sum, durations))
        parameters = {'training_match_ids': ids, 'training_goals': goals.tolist(), 'active_period_exposure_minutes': exposure, 'goal_rates_per_minute': (goals / exposure).tolist(), 'half_duration_minutes': durations, 'mean_total_minutes': sum(np.mean(ds) for ds in durations), 'climatology': ((counts + 0.5) / (counts.sum() + 1.5)).tolist()}
        return cls(parameters, config)

    def remaining_minutes(self, observation):
        first, second = self.parameters['half_duration_minutes']
        def residual(durations, elapsed):
            survivors = [d - elapsed for d in durations if d > elapsed]
            # When outside training support, retain one minute of uncertainty while play is ongoing.
            return float(np.mean(survivors)) if survivors else 1.0
        if observation.phase == 'pregame':
            return float(np.mean(first) + np.mean(second))
        if observation.phase == 'first_half':
            return residual(first, observation.elapsed_period_minutes) + float(np.mean(second))
        if observation.phase == 'halftime':
            return float(np.mean(second))
        if observation.phase == 'second_half':
            return residual(second, observation.elapsed_period_minutes)
        return 0.0

    def fit_pregame(self, prior):
        errors = np.sum((self.grid_probabilities - np.asarray(prior)) ** 2, axis=1)
        idx = int(np.argmin(errors))
        return self.rate_pairs[idx], float(errors[idx])

    def intensities(self, observation, pregame_rates=None):
        remaining = self.remaining_minutes(observation)
        if pregame_rates is None:
            return np.asarray(self.parameters['goal_rates_per_minute']) * remaining
        return np.asarray(pregame_rates) * remaining / self.parameters['mean_total_minutes']

    def forecast(self, observation, pregame_rates=None):
        rates = self.intensities(observation, pregame_rates)
        return outcome_probabilities(rates[0], rates[1], observation.home_goals - observation.away_goals, self.config['poisson_max_goals'])[0]

    def simulate(self, observation, n=10000, seed=0, pregame_rates=None):
        if n <= 0:
            raise ValueError('Simulation count must be positive')
        rates = self.intensities(observation, pregame_rates)
        rng = np.random.default_rng(seed)
        additions = rng.poisson(rates, size=(n, 2))
        scores = additions + [observation.home_goals, observation.away_goals]
        delta = scores[:, 0] - scores[:, 1]
        probabilities = np.array([(delta > 0).mean(), (delta == 0).mean(), (delta < 0).mean()])
        # Conditional on event counts, homogeneous Poisson event times are uniform in the remaining horizon.
        horizon = self.remaining_minutes(observation)
        example = []
        for counts in additions[:3]:
            goals = sorted([(float(t), 'home') for t in rng.uniform(0, horizon, int(counts[0]))] + [(float(t), 'away') for t in rng.uniform(0, horizon, int(counts[1]))])
            example.append(goals)
        return {'runs': n, 'seed': seed, 'remaining_active_minutes': horizon, 'expected_remaining_goals': rates.tolist(), 'monte_carlo_probabilities': probabilities.tolist(), 'analytic_probabilities': self.forecast(observation, pregame_rates).tolist(), 'monte_carlo_standard_errors': np.sqrt(probabilities * (1-probabilities) / n).tolist(), 'example_goal_timelines_minutes_from_now': example}


def demo(argv):
    from .replay import load_game, market_vector
    parser = argparse.ArgumentParser()
    parser.add_argument('--match', required=True)
    parser.add_argument('--utc', required=True)
    parser.add_argument('--runs', type=int, default=10000)
    args = parser.parse_args(argv)
    config = read(ROOT / 'config.json')
    model = Simulator(read(OUTPUT / 'model.json'), config)
    row = next(r for r in read(MANIFESTS / 'manifest.json') if r['match_id'] == args.match and r['status'] == 'ok')
    replay, histories = load_game(row)
    observation = replay.observe(epoch(args.utc))
    if epoch(args.utc) < epoch(row['kickoff_utc']):
        raise ValueError('Demo requires an in-game timestamp')
    prior = market_vector(histories, epoch(row['kickoff_utc']) - config['pregame_cutoff_before_actual_kickoff_seconds'], config['pregame_price_max_age_seconds'])
    if prior is None:
        raise ValueError('No fresh pregame market')
    rates, _ = model.fit_pregame(prior['probabilities'])
    print(json.dumps(model.simulate(observation, args.runs, config['seed'], rates), indent=2))
