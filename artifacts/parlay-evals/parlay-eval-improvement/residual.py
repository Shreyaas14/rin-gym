"""Second exploratory stage: nested-fit, regularized probability residuals."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from run import ROOT, REPO, OUT, SIDES, SEEDS, indices, scores, guard, read_tasks, snapshot

RIDGES = [.03, .1, .3, 1., 3., 1000.]


def project(p):
    """Euclidean projection onto the probability simplex, row by row."""
    shape = p.shape
    v = p.reshape(-1, 3)
    u = np.sort(v, axis=1)[:, ::-1]
    css = np.cumsum(u, axis=1)-1
    rho = (u-css/np.arange(1, 4) > 0).sum(axis=1)-1
    theta = css[np.arange(len(v)), rho]/(rho+1)
    return np.maximum(v-theta[:, None], 0).reshape(shape)


def features(model, market, score, include_model):
    pieces = [np.ones(market.shape[:-1]+(1,)), market[..., [0]]-market[..., [1]],
              market[..., [2]]-market[..., [1]], np.broadcast_to(score[None, :, None]/2, market.shape[:-1]+(1,))]
    if include_model:
        pieces += [model[..., [0]]-market[..., [0]], model[..., [1]]-market[..., [1]]]
    return np.concatenate(pieces, axis=-1)


def fit(x, market, labels, members, ridge):
    xx = x[:, members].reshape(-1, x.shape[-1])
    yy = (np.eye(3)[labels[members]][None]-market[:, members]).reshape(-1, 3)
    # Fixed feature scaling, repeated predictions average within match naturally.
    return np.linalg.solve(xx.T@xx/len(xx)+ridge*np.eye(xx.shape[1]), xx.T@yy/len(xx))


def predict(x, market, beta):
    return project(market+x@beta)


def run():
    prior = json.loads((OUT/'protocol.json').read_text())
    task_path = REPO/'artifacts/earlier-run-risk/tasks.jsonl.gz'
    saved_path = ROOT/'training-results/run-3-results/forecast-rules-run-3/repeated-validation/matches.jsonl'
    tasks = sorted([t for t in read_tasks(task_path) if t['position']=='flat' and t['decision']=='h1+15'], key=lambda t:t['match_id'])
    ids = [t['match_id'] for t in tasks]; lookup = {mid:i for i,mid in enumerate(ids)}
    assert len(ids)==72 and all(t['split']=='val' and not t['demo'] for t in tasks)
    ledger = ROOT/'rin-gym-simulation/data/evals/ledger.sqlite3'; before = guard(ledger, ids)
    labels = np.array([SIDES.index(t['scoring']['result']) for t in tasks])
    visible_score = np.array([t['scoring']['observed_score']['home']-t['scoring']['observed_score']['away'] for t in tasks])
    market = np.broadcast_to(np.array([[t['scoring']['market_probs'][s] for s in SIDES] for t in tasks])[None], (3,72,3)).copy()
    model = np.full_like(market, np.nan)
    for line in saved_path.read_text().splitlines():
        r=json.loads(line);s,=[s for s in r['steps'] if s['decision']=='h1+15']
        assert r['split']=='val' and not r['demo'] and s['valid'] and s['task_id'].endswith(':flat')
        model[r['repeat'],lookup[r['match_id']]]=[s['probs'][side] for side in SIDES]
    assert np.isfinite(model).all()
    metadata = pq.read_table(snapshot(REPO/'data/raw/release-data-2026-10-13-v1','fifa-markets')/'matches.parquet',
        columns=['match_id','home_team_id','away_team_id'],filters=[('match_id','in',ids)]).to_pylist()
    byid={r['match_id']:{r['home_team_id'],r['away_team_id']} for r in metadata}; teams=[byid[mid] for mid in ids]
    protocol = {'stage': 'Second exploratory stage after first-stage results were inspected; not a preregistered confirmatory trial.',
        'source_protocol_sha256':hashlib.sha256((OUT/'protocol.json').read_bytes()).hexdigest(),
        'source_hashes':prior['sources_sha256'], 'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'fold_membership':prior['fold_membership'], 'ridges':RIDGES,
        'features':'Constant, market home-minus-draw, market away-minus-draw, visible score difference/2; model arm adds home and draw model-minus-market probability residuals.',
        'fit':'Ridge least squares for observed one-hot result minus market probability; project corrected forecasts onto probability simplex.',
        'selection':'Three inner whole-game folds within each outer fit fold; tune ridge by inner evaluation pair Brier, then refit on outer fit games only.',
        'control':'Same correction with market/visible-score features only; raw market also reported.',
        'scope':'Same historical development cohort; no inference, acquisition, or protected holdout use.', 'holdout_before':before}
    (OUT/'residual-protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    cases, receipts = [], []
    for design in prior['fold_membership']:
        seed,fold=design['seed'],design['fold'];train=[lookup[m] for m in design['fit_matches']];test=[lookup[m] for m in design['evaluation_matches']]
        assert not set(train)&set(test)
        inner=[list(map(int,g)) for g in np.array_split(np.random.default_rng(seed+fold*100).permutation(train),3)]
        tp=indices(test,teams)
        detail={'market':scores(market,labels,tp,market)}
        for name, include in [('ridge_market_context',False),('ridge_model_context',True)]:
            x=features(model,market,visible_score,include)
            losses=[]
            for ridge in RIDGES:
                inner_losses=[]
                for validation in inner:
                    fitting=sorted(set(train)-set(validation));assert not set(fitting)&set(validation)
                    beta=fit(x,market,labels,fitting,ridge)
                    inner_losses.extend(scores(predict(x,market,beta),labels,indices(validation,teams),market)['brier'].mean(axis=0))
                losses.append(float(np.mean(inner_losses)))
            chosen=min(range(len(RIDGES)),key=lambda i:(losses[i],-RIDGES[i]))
            ridge=RIDGES[chosen];beta=fit(x,market,labels,train,ridge)
            detail[name]=scores(predict(x,market,beta),labels,tp,market)
            receipts.append({'seed':seed,'fold':fold,'recipe':name,'ridge':ridge,'beta':beta.tolist(),
                'inner_validation_brier':losses[chosen],'evaluation_brier':float(detail[name]['brier'].mean()),
                'fit_matches':design['fit_matches'],'evaluation_matches':design['evaluation_matches']})
        for i,(a,b) in enumerate(tp):
            cases.append({'seed':seed,'fold':fold,'matches':[ids[a],ids[b]],
                'scores':{name:{key:float(v[:,i].mean()) for key,v in values.items()} for name,values in detail.items()}})
    results=[]
    for seed in SEEDS:
        selected=[r for r in cases if r['seed']==seed]
        for name in ['market','ridge_market_context','ridge_model_context']:
            results.append({'seed':seed,'recipe':name,'pairs':len(selected),
                **{key:float(np.mean([r['scores'][name][key] for r in selected])) for key in ['brier','log_loss','favorite_hit','common_ticket_brier']}})
    after=guard(ledger,ids);assert after==before
    summary={'cost_usd':0,'results':results,'parameter_receipts':receipts,'holdout_before_after':after}
    (OUT/'residual-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    (OUT/'residual-cases.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in cases))
    print(json.dumps(results,indent=2))


if __name__=='__main__':run()
