"""Frozen active-play slices and event-gated calibration of saved forecasts."""
from collections import defaultdict
import hashlib
import itertools
import json
from pathlib import Path
import sys

import numpy as np
import pyarrow.parquet as pq

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'outputs/parlay-eval-improvement'))
from run import REPO, SIDES, SEEDS, FAMILIES, transform, scores, choose, read_tasks, snapshot, guard
OUT=Path(__file__).resolve().parent
PHASES=['h1+15','h1+30','h2+15','h2+30']
GROUPS=['all_active','first_half','second_half','both_tied','recent_goal_any','red_visible_any','uncertain_any','quiet']
MIN_FIT_MATCHES=20
MIN_FIT_PAIRS=30


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def visible_features(rows,cutoff):
    """Prefix-only score changes and red cards, using event-delay cutoff."""
    seen=sorted([r for r in rows if r['event_time_utc'].timestamp()<=cutoff],key=lambda r:(r['event_time_utc'],r['source_index']))
    home=away=0;last_goal=None;red=False
    for r in seen:
        nh=max(home,r['home_score']);na=max(away,r['away_score'])
        if (nh,na)!=(home,away):last_goal=r['event_time_utc'].timestamp()
        home,away=nh,na
        red |= r['event_kind'] in ('red_card','second_yellow')
    return {'visible_home':home,'visible_away':away,'recent_goal':last_goal is not None and cutoff-300<last_goal<=cutoff,
            'red_visible':bool(red),'last_visible_event':seen[-1]['event_time_utc'].timestamp() if seen else None,
            'event_cutoff':cutoff}


def member(group,a,b,features,market):
    fa,fb=features[a],features[b]
    if group=='all_active':return True
    if group=='first_half':return fa['phase'].startswith('h1')
    if group=='second_half':return fa['phase'].startswith('h2')
    if group=='both_tied':return fa['visible_home']==fa['visible_away'] and fb['visible_home']==fb['visible_away']
    if group=='recent_goal_any':return fa['recent_goal'] or fb['recent_goal']
    if group=='red_visible_any':return fa['red_visible'] or fb['red_visible']
    if group=='uncertain_any':return market[0,a].max()<.6 or market[0,b].max()<.6
    if group=='quiet':return not (fa['recent_goal'] or fb['recent_goal'] or fa['red_visible'] or fb['red_visible'])
    raise ValueError(group)


def load():
    task_path=REPO/'artifacts/earlier-run-risk/tasks.jsonl.gz'
    saved_path=ROOT/'training-results/run-3-results/forecast-rules-run-3/repeated-validation/matches.jsonl'
    comparison_path=REPO/'artifacts/earlier-run-risk/comparison.json'
    config=json.loads(comparison_path.read_text())['config']
    tasks=read_tasks(task_path)
    assert all(t['split']=='val' and not t['demo'] for t in tasks)
    ids=sorted({t['match_id'] for t in tasks});lookup={mid:i for i,mid in enumerate(ids)}
    ledger=ROOT/'rin-gym-simulation/data/evals/ledger.sqlite3';seal=guard(ledger,ids)
    data=snapshot(REPO/'data/raw/release-data-2026-10-13-v1','fifa-markets')
    metadata=pq.read_table(data/'matches.parquet',columns=['match_id','home_team_id','away_team_id'],filters=[('match_id','in',ids)]).to_pylist()
    byid={r['match_id']:{r['home_team_id'],r['away_team_id']} for r in metadata}
    event_columns=['match_id','event_time_utc','source_index','event_kind','home_score','away_score']
    events=defaultdict(list)
    for r in pq.read_table(data/'events.parquet',columns=event_columns,filters=[('match_id','in',ids)]).to_pylist():events[r['match_id']].append(r)
    at={(t['match_id'],t['decision']):t for t in tasks if t['position']=='flat'}
    n=len(ids)*len(PHASES);market=np.zeros((3,n,3));model=np.full((3,n,3),np.nan);labels=np.zeros(n,dtype=int)
    feat={};point_index={};teams={}
    for pi,phase in enumerate(PHASES):
        for mid in ids:
            key=(mid,phase)
            if key not in at:continue
            i=pi*len(ids)+lookup[mid];point_index[key]=i;t=at[key];teams[i]=byid[mid]
            labels[i]=SIDES.index(t['scoring']['result'])
            market[:,i]=[t['scoring']['market_probs'][s] for s in SIDES]
            f=visible_features(events[mid],t['t']-config['event_delay_s'])
            assert (f['visible_home'],f['visible_away'])==(t['scoring']['observed_score']['home'],t['scoring']['observed_score']['away'])
            feat[i]={**f,'phase':phase,'match_id':mid,'decision_t':t['t']}
    positions=defaultdict(list)
    for line in saved_path.read_text().splitlines():
        r=json.loads(line);assert r['split']=='val' and not r['demo']
        for s in r['steps']:
            key=(r['match_id'],s['decision'])
            if key not in point_index:continue
            assert s['valid'];i=point_index[key];model[r['repeat'],i]=[s['probs'][side] for side in SIDES]
            positions[s['decision']].append(s['task_id'].rsplit(':',1)[-1])
    assert np.isfinite(model[:,sorted(feat)]).all()
    return {'ids':ids,'market':market,'model':model,'labels':labels,'features':feat,'teams':teams,
        'at':at,'events':events,'config':config,'positions':positions,'seal':seal,'ledger':ledger,
        'sources':[task_path,saved_path,comparison_path,data/'events.parquet',data/'matches.parquet']}


def run():
    data=load();ids=data['ids'];market,model,labels=data['market'],data['model'],data['labels'];features=data['features'];teams=data['teams']
    prior=json.loads((ROOT/'outputs/parlay-eval-improvement/protocol.json').read_text())
    all_pairs=np.array([(a,b) for a,b in itertools.combinations(sorted(features),2)
                        if features[a]['phase']==features[b]['phase'] and not teams[a]&teams[b]],dtype=int)
    group_pairs={g:np.array([(a,b) for a,b in all_pairs if member(g,a,b,features,market)],dtype=int).reshape(-1,2) for g in GROUPS}
    def event_pair(a,b):return features[a]['recent_goal'] or features[b]['recent_goal'] or features[a]['red_visible'] or features[b]['red_visible']
    protocol={'approach':'Context-specific two-leg probability evaluation and an event-gated calibrated blend.',
        'scope':'Retrospective historical development; no inference or new acquisition.',
        'groups':GROUPS,'active_phases':PHASES,'recent_goal_window_s':300,'event_cutoff':'decision t minus recorded event_delay_s',
        'red_definition':'Any red_card or second_yellow visible in prefix; persists thereafter.',
        'uncertain_definition':'At least one leg with normalized market maximum probability <0.6.',
        'group_rules':'Same-phase distinct-match pairs with no shared team; boolean OR for either-leg event flags; groups overlap.',
        'minimum_fit_unique_matches':MIN_FIT_MATCHES,'minimum_fit_pair_points':MIN_FIT_PAIRS,
        'fallback':'Use raw market if either fitting threshold is unmet; never fill a sparse group from its evaluation results.',
        'primary_recipe':'Event-gated blend: raw market for quiet pairs, calibrated blend fit on event-pair training points otherwise.',
        'primary_metric':'All-active repeat-averaged two-leg Brier; raw market and identically calibrated market-only event gate are controls.',
        'secondary':'Within-group calibrated market and calibrated blend; fit each only on that group in fit folds.',
        'candidate_families':{k:FAMILIES[k] for k in ['calibrated_market','calibrated_blend']},
        'split_unit':'Whole matches; both evaluation legs in same evaluation fold; same three partition seeds and folds as approach A.',
        'fold_membership':prior['fold_membership'],'later_position_caveat':'Saved later forecasts were conditioned on original positions; no fresh response to a changed policy.',
        'weighting':'Equal pair-point weight, repeats averaged; sparse missing checkpoints slightly alter phase membership. Games/pairs/phases are dependent.',
        'holdout_before':data['seal'],'source_hashes':{str(p.relative_to(ROOT)):digest(p) for p in data['sources']+[Path(__file__)]}}
    (OUT/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    (OUT/'features.json').write_text(json.dumps(features,indent=2)+'\n')
    cases=[];receipts=[];counts=[]
    for group,pairs in group_pairs.items():
        counts.append({'group':group,'pair_points_all_games':len(pairs),'unique_matches':len({features[i]['match_id'] for i in pairs.flat}),
                       'unique_pair_fixtures':len({tuple(sorted([features[a]['match_id'],features[b]['match_id']])) for a,b in pairs})})
    for design in prior['fold_membership']:
        seed,fold=design['seed'],design['fold'];train=set(design['fit_matches']);test=set(design['evaluation_matches']);assert not train&test
        def subset(pairs,members):return np.array([(a,b) for a,b in pairs if features[a]['match_id'] in members and features[b]['match_id'] in members],dtype=int).reshape(-1,2)
        fit_events=subset(np.array([(a,b) for a,b in all_pairs if event_pair(a,b)],dtype=int),train)
        fit_labels=labels.copy()
        for i,f in features.items():
            if f['match_id'] in test:fit_labels[i]=-999
        event_params={}
        for recipe in ['calibrated_market','calibrated_blend']:
            unique={features[i]['match_id'] for i in fit_events.flat}
            fallback=len(unique)<MIN_FIT_MATCHES or len(fit_events)<MIN_FIT_PAIRS
            params=(0.,1.,1.) if fallback else choose(model,market,fit_labels,fit_events,FAMILIES[recipe])[0]
            event_params[recipe]=params
            receipts.append({'seed':seed,'fold':fold,'group':'event_gate_fit','recipe':recipe,'params':params,'fallback':fallback,
                'fit_pair_points':len(fit_events),'fit_unique_matches':len(unique)})
        for group,pairs in group_pairs.items():
            fit_pairs=subset(pairs,train);test_pairs=subset(pairs,test)
            if not len(test_pairs):continue
            detail={'market':scores(market,labels,test_pairs,market),'model':scores(model,labels,test_pairs,market)}
            unique={features[i]['match_id'] for i in fit_pairs.flat}
            fallback=len(unique)<MIN_FIT_MATCHES or len(fit_pairs)<MIN_FIT_PAIRS
            for recipe in ['calibrated_market','calibrated_blend']:
                params=(0.,1.,1.) if fallback else choose(model,market,fit_labels,fit_pairs,FAMILIES[recipe])[0]
                detail[recipe]=scores(transform(model,market,params),labels,test_pairs,market)
                receipts.append({'seed':seed,'fold':fold,'group':group,'recipe':recipe,'params':params,'fallback':fallback,
                    'fit_pair_points':len(fit_pairs),'fit_unique_matches':len(unique)})
            if group=='all_active':
                active=np.array([event_pair(a,b) for a,b in test_pairs])
                for base_recipe,name in [('calibrated_market','event_gated_market'),('calibrated_blend','event_gated_blend')]:
                    adjusted=scores(transform(model,market,event_params[base_recipe]),labels,test_pairs,market)
                    detail[name]={key:np.where(active[None],adjusted[key],detail['market'][key]) for key in adjusted}
            for j,(a,b) in enumerate(test_pairs):
                cases.append({'seed':seed,'fold':fold,'group':group,'phase':features[a]['phase'],
                    'matches':[features[a]['match_id'],features[b]['match_id']],'point_indices':[int(a),int(b)],
                    'scores':{recipe:{key:float(v[:,j].mean()) for key,v in values.items()} for recipe,values in detail.items()}})
    summary=[]
    for seed in SEEDS:
        for group in GROUPS:
            rows=[r for r in cases if r['seed']==seed and r['group']==group]
            if not rows:continue
            for recipe in rows[0]['scores']:
                summary.append({'seed':seed,'group':group,'recipe':recipe,'pair_points':len(rows),
                    'unique_fixtures':len({m for r in rows for m in r['matches']}),
                    **{metric:float(np.mean([r['scores'][recipe][metric] for r in rows])) for metric in ['brier','log_loss','favorite_hit','common_ticket_brier']}})
    after=guard(data['ledger'],ids);assert after==data['seal']
    positions={phase:{p:data['positions'][phase].count(p) for p in sorted(set(data['positions'][phase]))} for phase in PHASES}
    result={'cost_usd':0,'results':summary,'group_coverage':counts,'parameter_receipts':receipts,
            'saved_forecast_positions':positions,'holdout_before_after':after}
    (OUT/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    (OUT/'cases.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in cases))
    print(json.dumps({'group_coverage':counts,'primary_results':[r for r in summary if r['group']=='all_active']},indent=2))


if __name__=='__main__':run()
