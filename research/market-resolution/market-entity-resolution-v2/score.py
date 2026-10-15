"""Score supplied independently reviewed labels; never invent reference labels."""
from collections import Counter,defaultdict
from pathlib import Path
import argparse
import csv
import hashlib
import json
import math
import random
from evaluate import ROOT,dump,verify

def wilson(correct,total,z=1.959963984540054):
    if not total:return None
    p=correct/total;d=1+z*z/total;c=(p+z*z/(2*total))/d
    half=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/d
    return [max(0,c-half),min(1,c+half)]

def identity_metric(labels,pred):
    ids=set(labels)&set(pred);known=[i for i in ids if labels[i]['identity_label']!='unknown']
    accepted=[i for i in known if pred[i]['identity']=='match']
    positives=[i for i in known if labels[i]['identity_label']=='match']
    tp=sum(labels[i]['identity_label']=='match' for i in accepted);fp=len(accepted)-tp
    correct=sum(pred[i]['identity']==labels[i]['identity_label'] for i in known)
    review=sum(pred[i]['identity']=='review' for i in ids)
    return {'reviewed_pairs':len(labels),'completed_reviewed_pairs':len(ids),
            'missing_reviewed_predictions':len(labels)-len(ids),'known_identity_references':len(known),
            'unknown_identity_references':len(ids)-len(known),
            'true_matches':tp,'false_matches':fp,'positive_references':len(positives),
            'precision':tp/len(accepted) if accepted else None,'precision_wilson_95':wilson(tp,len(accepted)),
            'recall':tp/len(positives) if positives else None,
            'correct_automatic_decisions':correct,'correct_automation_fraction':correct/len(labels) if labels else None,
            'reviews':review,'decision_coverage':(len(ids)-review)/len(labels) if labels else None,
            'accepted_on_unknown_reference':sum(labels[i]['identity_label']=='unknown' and pred[i]['identity']=='match' for i in ids),
            'interval_note':'Wilson interval assumes independent pairs; event-cluster dependence requires clustered analysis.'}

def settlement_metric(labels,pred):
    ids=set(labels)&set(pred);compatible=[i for i in ids if labels[i]['settlement_label']=='compatible']
    different=[i for i in ids if labels[i]['settlement_label']=='different']
    accepted=[i for i in ids if pred[i]['settlement']=='compatible']
    tp=sum(i in compatible for i in accepted);fp=sum(i in different for i in accepted)
    return {'compatible_references':len(compatible),'different_references':len(different),
            'unknown_references':sum(labels[i]['settlement_label']=='unknown' for i in ids),
            'compatible_accepts':len(accepted),'known_true_compatible_accepts':tp,'known_false_compatible_accepts':fp,
            'compatible_precision_on_known':tp/(tp+fp) if tp+fp else None,
            'compatible_recall':tp/len(compatible) if compatible else None,
            'compatible_accepts_on_unknown':sum(labels[i]['settlement_label']=='unknown' for i in accepted),
            'documented_conflicts_detected':sum(pred[i]['settlement']=='different' for i in different)}

def paired_correct_gain(labels,pred_a,pred_b,pairs):
    # Resample entire anchor event groups. Secondary candidate events and shared
    # templates may add dependence; this does not prove template generalization.
    ids=set(labels)&set(pred_a)&set(pred_b)
    ids={i for i in ids if labels[i]['identity_label']!='unknown'}
    groups=defaultdict(list)
    for i in sorted(ids):
        gold=labels[i]['identity_label']
        groups[pairs[i]['event_group']].append(int(pred_b[i]['identity']==gold)-int(pred_a[i]['identity']==gold))
    if not groups:return None
    blocks=list(groups.values());rng=random.Random(20261013);samples=[]
    for _ in range(2000):
        chosen=[rng.choice(blocks) for _ in blocks];samples.append(sum(sum(x) for x in chosen)/sum(len(x) for x in chosen))
    samples.sort();return {'common_known_pairs':len(ids),'anchor_event_groups':len(groups),
        'correct_automation_gain':sum(sum(x) for x in blocks)/len(ids),
        'anchor_event_bootstrap_95':[samples[49],samples[1949]],
        'limit':'Secondary candidate-event and rule-template dependencies remain; small group counts are fragile.'}

def read_labels(path,pair_ids,sample=False):
    labels={}
    with Path(path).open(newline='') as f:
        for row in csv.DictReader(f):
            ident=row.get('pair_id')
            if ident not in pair_ids or ident in labels:raise ValueError('Unknown or duplicate review ID: '+str(ident))
            if not row.get('identity_label') and not row.get('settlement_label'):continue
            if row.get('identity_label') not in ('match','nonmatch','unknown') or row.get('settlement_label') not in ('compatible','different','unknown'):
                raise ValueError('Both labels required with valid values: '+ident)
            if not row.get('reviewer'):raise ValueError('Reviewer identity required: '+ident)
            if not row.get('identity_evidence') or not row.get('settlement_counterexample_or_proof'):
                raise ValueError('Evidence or uncertainty explanation required for both labels: '+ident)
            labels[ident]=row
    if not labels:raise ValueError('No reviewed labels supplied')
    if not sample and set(labels)!=pair_ids:raise ValueError('Partial review; explicitly use --sample to score an adjudicated sample')
    return labels

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--labels',required=True);ap.add_argument('--sample',action='store_true');args=ap.parse_args()
    verify();pairs={r['id']:r for r in json.loads((ROOT/'pairs.json').read_text())}
    labels=read_labels(args.labels,set(pairs),args.sample)
    predictions={p.stem:json.loads(p.read_text()) for p in (ROOT/'predictions').glob('*.json')}
    results={'labels_sha256':hashlib.sha256(Path(args.labels).read_bytes()).hexdigest(),
             'reviewed_pairs':len(labels),'sample_only':args.sample,
             'reviewers':sorted({r['reviewer'] for r in labels.values()}),'methods':{},'paired_vs_heuristics':{}}
    for m,pred in predictions.items():
        results['methods'][m]={'identity':identity_metric(labels,pred),'settlement':settlement_metric(labels,pred),
          'per_family':{family:identity_metric({i:r for i,r in labels.items() if pairs[i]['family']==family},pred)
                        for family in sorted({pairs[i]['family'] for i in labels})}}
        if m!='heuristics':results['paired_vs_heuristics'][m]=paired_correct_gain(labels,predictions['heuristics'],pred,pairs)
    four={'heuristics','jev','llm','hybrid'}
    results['four_way_comparison_complete']=four.issubset(predictions) and all(set(labels)<=set(predictions[m]) for m in four)
    results['gold_limit']='Reviewer identities/evidence supplied externally; second-reviewer independence and adjudication remain a human responsibility.'
    dump(ROOT/'adjudicated-metrics.json',results)
    print(json.dumps({'reviewed_pairs':len(labels),'four_way_complete':results['four_way_comparison_complete']}))

if __name__=='__main__':main()
