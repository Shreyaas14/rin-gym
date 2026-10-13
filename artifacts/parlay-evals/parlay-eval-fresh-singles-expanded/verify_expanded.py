"""Independent score/receipt checks for the expanded single-forecast experiment."""
import hashlib
import itertools
import json
import math

import numpy as np
from expanded import ROOT,OUT,OLD,LEDGER,guard,digest,parse,observation
from run_context import load


def main():
    p=json.loads((OUT/'protocol.json').read_text());checksum=p.pop('sha256');assert digest(p)==checksum;p['sha256']=checksum
    points=json.loads((OUT/'observations.json').read_text());pairs=json.loads((OUT/'pairs.json').read_text())
    assert digest(points)==p['points_sha256'] and digest(pairs)==p['pairs_sha256']
    for path,sha in p['source_hashes'].items():assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==sha
    data=load();oldcases=json.loads((OLD/'cases.json').read_text())
    original={(tuple(sorted(c['match_ids'])),c['phase']) for c in oldcases}
    for point in points:
        task=data['at'][point['match_id'],point['phase']]
        assert task['split']=='val' and not task['demo']
        assert point['messages']==observation(task,task,'single_a')
        assert digest(point['messages'])==point['observation_sha256']
        assert point['visible_score']==[task['scoring']['observed_score']['home'],task['scoring']['observed_score']['away']]
        assert point['visible_score'][0]==point['visible_score'][1]
        assert point['evaluation']['result']==task['scoring']['result']
        assert point['evaluation']['market_probs']==task['scoring']['market_probs']
    point_features={(f['match_id'],f['phase']):i for i,f in data['features'].items()}
    for pair in pairs:
        a,b=[points[i] for i in pair['indices']]
        assert a['phase']==b['phase']==pair['phase'] and a['match_id']!=b['match_id']
        assert not data['teams'][point_features[a['match_id'],a['phase']]]&data['teams'][point_features[b['match_id'],b['phase']]]
        assert pair['in_original_24']==((tuple(sorted(pair['match_ids'])),pair['phase']) in original)
    predictions=[json.loads(line) for line in (OUT/'predictions.jsonl').read_text().splitlines()]
    requests=[json.loads(line) for line in (OUT/'requests.jsonl').read_text().splitlines()]
    key=lambda r:(r['observation_id'],r['repeat'])
    at={key(r):r for r in predictions};req={key(r):r for r in requests};pointat={point['id']:point for point in points}
    assert len(at)==len(req)==len(predictions)==len(requests)==p['maximum_requests']
    assert set(at)==set(req)=={(point['id'],repeat) for point in points for repeat in range(3)}
    for k,r in at.items():
        point=pointat[r['observation_id']]
        for field in ['input_tokens','seed','observation_sha256','protocol_sha256','reserved_max_cost_usd']:assert r[field]==req[k][field]
        assert r['input_tokens']==point['prompt_tokens'] and r['protocol_sha256']==checksum
        assert r['observation_sha256']==point['observation_sha256']
        assert r['probs']==(None if r['truncated'] or r['completion'] is None else parse(r['completion'],'single_a'))
        if r['output_tokens'] is not None:assert 0<=r['output_tokens']<=p['max_tokens']
    scored=json.loads((OUT/'scored-pairs.json').read_text());summary=json.loads((OUT/'summary.json').read_text())
    assert len(scored)==len(pairs)
    checks=0
    for r,pair in zip(scored,pairs):
        assert r['indices']==pair['indices'] and r['in_original_24']==pair['in_original_24']
        a,b=[points[i] for i in pair['indices']]
        actual=(a['evaluation']['result'],b['evaluation']['result'])
        for rep in range(3):
            pa,pb=at[a['id'],rep]['probs'],at[b['id'],rep]['probs']
            for recipe,xx,yy in [('single_product',pa,pb),('market',a['evaluation']['market_probs'],b['evaluation']['market_probs'])]:
                if xx is None or yy is None:bs=2.;ll=-math.log(1e-6)
                else:
                    probs={(x,y):xx[x]*yy[y] for x,y in itertools.product(['home','draw','away'],repeat=2)}
                    assert abs(sum(probs.values())-1)<1e-9
                    bs=sum((v-float(label==actual))**2 for label,v in probs.items());ll=-math.log(max(probs[actual],1e-6))
                assert abs(bs-r['repeat_scores'][rep][recipe]['brier'])<1e-10
                assert abs(ll-r['repeat_scores'][rep][recipe]['log_loss'])<1e-10;checks+=1
    additional=[r for r in scored if not r['in_original_24']]
    phases=sorted({r['phase'] for r in scored})
    for recipe in ['single_product','market']:
        for metric in ['brier','log_loss']:
            phase_means=[np.mean([r['scores'][recipe][metric] for r in additional if r['phase']==phase]) for phase in phases]
            assert abs(np.mean(phase_means)-summary['aggregates']['additional_primary']['equal_phase_metrics'][recipe][metric])<1e-10
    # Recompose bootstrap tickets from sampled underlying fixture multiplicities.
    game_ids=sorted({point['match_id'] for point in points});lookup={mid:i for i,mid in enumerate(game_ids)}
    sampled=np.random.default_rng(p['seed']).multinomial(len(game_ids),np.full(len(game_ids),1/len(game_ids)),size=4000)
    for metric in ['brier','log_loss']:
        replicates=np.zeros(4000);valid=np.ones(4000,dtype=bool)
        for phase in phases:
            selected=[r for r in additional if r['phase']==phase]
            # Each column is a distinct eligible pair; multiplicities come from games, not rows.
            weights=np.column_stack([sampled[:,lookup[r['match_ids'][0]]]*sampled[:,lookup[r['match_ids'][1]]] for r in selected])
            deltas=np.array([r['scores']['single_product'][metric]-r['scores']['market'][metric] for r in selected])
            den=weights.sum(1);valid &= den>0
            replicates+=np.divide((weights*deltas[None]).sum(1),den,out=np.zeros(4000),where=den>0)/len(phases)
        interval=np.quantile(replicates[valid],[.025,.975])
        assert np.allclose(interval,summary['primary_paired_uncertainty'][metric]['game_bootstrap_95'],atol=1e-10,rtol=0)
    budget=sum(r['reserved_max_cost_usd'] for r in requests)
    assert abs(budget-p['max_token_cost_estimate_usd'])<1e-10
    assert summary['usage']['full_rate_token_cost_estimate_usd']<=budget<p['request_reservation_ceiling_usd']<p['budget_usd']
    # Check post-hoc fallback scores directly from the original single outputs.
    diagnostic_path=OUT/'diagnostics.json'
    if diagnostic_path.exists():
        diagnostic=json.loads(diagnostic_path.read_text());fallback_by_phase={phase:[] for phase in phases}
        valid_by_phase={phase:[] for phase in phases};valid_market_by_phase={phase:[] for phase in phases}
        for row in additional:
            a,b=[points[i] for i in row['indices']];truth=(a['evaluation']['result'],b['evaluation']['result'])
            fallback=[]
            for rep in range(3):
                aa=at[a['id'],rep]['probs'] or a['evaluation']['market_probs']
                bb=at[b['id'],rep]['probs'] or b['evaluation']['market_probs']
                fallback.append(sum((aa[x]*bb[y]-float((x,y)==truth))**2 for x,y in itertools.product(['home','draw','away'],repeat=2)))
            fallback_by_phase[row['phase']].append(float(np.mean(fallback)))
            if all(r['single_product']['valid'] for r in row['repeat_scores']):
                valid_by_phase[row['phase']].append(row['scores']['single_product']['brier'])
                valid_market_by_phase[row['phase']].append(row['scores']['market']['brier'])
        assert abs(np.mean([np.mean(v) for v in fallback_by_phase.values()])-diagnostic['invalid_leg_market_fallback']['model_equal_phase_brier'])<1e-10
        assert abs(np.mean([np.mean(v) for v in valid_by_phase.values()])-diagnostic['common_valid']['model_equal_phase_brier'])<1e-10
        assert abs(np.mean([np.mean(v) for v in valid_market_by_phase.values()])-diagnostic['common_valid']['market_same_cases_equal_phase_brier'])<1e-10
    ids={point['match_id'] for point in points};assert guard(LEDGER,ids)==summary['holdout_before_after']==p['holdout_before']
    result={'status':'passed','independent_joint_scores':checks,'receipt_pairs':len(requests),
        'safe_source_observations_verified':len(points),'same_phase_tied_no_shared_team_pairs_verified':len(pairs),
        'original_case_exclusion_verified':sum(r['in_original_24'] for r in scored),'phase_weighted_primary_aggregates_verified':True,
        'underlying_game_bootstrap_recomputed':True,
        'posthoc_validity_and_fallback_scores_verified':diagnostic_path.exists(),
        'budget_and_source_hashes_verified':True,'holdout_unchanged':True}
    (OUT/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
