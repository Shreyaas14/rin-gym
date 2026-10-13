"""Recompute halftime goal labels from raw FIFA feeds, independently of saved tasks."""
import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import statistics

from app.trade_env.forecast import targets, brier
from app.trade_env.config import make_config


def epoch(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def audit(raw_dir, replays_path):
    replays = [json.loads(line) for line in Path(replays_path).read_text().splitlines()]
    halftime = defaultdict(list)
    for replay in replays:
        if replay['split'] != 'val':
            raise ValueError('Only development validation records are allowed')
        for step in replay['steps']:
            if step['decision'] == 'ht':
                halftime[replay['match_id']].append(step)
    rows = []
    for mid, steps in sorted(halftime.items()):
        raw = (Path(raw_dir) / f'fifa_{mid}_timeline.json').read_bytes()
        source = json.loads(raw)['Event']
        def boundary(kind, period):
            matches = [epoch(e['Timestamp']) for e in source if e['TypeLocalized'][0]['Description'] == kind and e['Period'] == period]
            if len(matches) != 1:
                raise ValueError(f'{mid}: ambiguous boundary')
            return matches[0]
        t = boundary('End Time', 3)
        restart = boundary('Start Time', 5)
        end = boundary('End Time', 5)
        events = [{'t': epoch(e['Timestamp']), 'home_goals': e['HomeGoals'], 'away_goals': e['AwayGoals']} for e in source]
        def score_at(cutoff):
            return tuple(max((e[s+'_goals'] for e in events if e['t'] <= cutoff), default=0) for s in ['home','away'])
        # Independent boundary score check; no model probability determines labels.
        before, after = score_at(t), score_at(t+300)
        increases = [s for i,s in enumerate(['home','away']) if after[i] > before[i]]
        record = {'events': events, 'periods': {'h2_end':end}, 'prices': {s:[] for s in ['home','draw','away']}}
        computed = targets(record,t,{},make_config({'forecast_rewards':True}))['next_goal']
        errors = []
        if restart <= t+300: errors.append('restart_within_horizon')
        if increases: errors.append('score_increase_within_horizon')
        if computed != 'none': errors.append('computed_label_not_none')
        for step in steps:
            probs=json.loads(step['completion'])['next_goal']
            if abs(brier(probs,'none')-step['forecast']['goal_brier'])>1e-9:
                errors.append('saved_brier_disagrees_with_none_label')
            if step['forecast']['goal_available'] != 1: errors.append('unavailable_label')
        rows.append({'match_id':mid,'halftime_t':t,'horizon_end_t':t+300,'second_half_start_t':restart,
            'break_seconds':restart-t,'score_before':before,'score_after_horizon':after,
            'recomputed_label':computed,'responses':len(steps),'source_sha256':hashlib.sha256(raw).hexdigest(),
            'mean_predicted_any_goal':statistics.mean(1-json.loads(s['completion'])['next_goal']['none'] for s in steps),
            'errors':errors})
    return {'matches':len(rows),'responses':sum(r['responses'] for r in rows),
        'passed':bool(rows) and not any(r['errors'] for r in rows),
        'break_seconds_min':min(r['break_seconds'] for r in rows),'break_seconds_max':max(r['break_seconds'] for r in rows),
        'mean_predicted_any_goal':statistics.mean(1-json.loads(s['completion'])['next_goal']['none'] for ss in halftime.values() for s in ss),
        'rows':rows,'scope':'Final-feed retrospective verification; does not attest historical availability or rule out later feed corrections.'}

if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--raw',required=True);p.add_argument('--replays',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();report=audit(args.raw,args.replays)
    Path(args.output).write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='rows'},indent=2))
    raise SystemExit(0 if report['passed'] else 1)
