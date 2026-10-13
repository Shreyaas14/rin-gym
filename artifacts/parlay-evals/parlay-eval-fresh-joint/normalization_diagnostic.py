"""Explicitly post-hoc normalization diagnostic; never replaces primary scores."""
import json
import math

import numpy as np
from experiment import OUT, KEYS
from score import loss, product


def run():
    predictions=[json.loads(line) for line in (OUT/'predictions.jsonl').read_text().splitlines()]
    at={(r['case_id'],r['repeat'],r['arm']):r for r in predictions}
    cases=json.loads((OUT/'cases.json').read_text());masses=[];rows=[];missing=0
    for c in cases:
        actual='_'.join(c['evaluation']['results'])
        for rep in range(3):
            r=at[c['id'],rep,'joint'];p=None
            if not r['truncated'] and r['completion'] is not None:
                try:
                    obj=json.loads(r['completion']);values=obj['joint']
                    if set(obj)=={'joint'} and set(values)==set(KEYS) and all(type(v) in (float,int) and math.isfinite(v) and 0<=v<=1 for v in values.values()):
                        mass=math.fsum(values.values())
                        if mass>0:masses.append(mass);p={k:values[k]/mass for k in KEYS}
                except (ValueError,TypeError,KeyError):pass
            if p is None:missing+=1;continue
            singles=product(at[c['id'],rep,'single_a']['probs'],at[c['id'],rep,'single_b']['probs'])
            rows.append({'case_id':c['id'],'repeat':rep,'strict_valid':r['probs'] is not None,
                'repaired_joint_brier':loss(p,actual)['brier'],'market_brier':loss(product(*c['evaluation']['market_probs']),actual)['brier'],
                'single_product_brier':loss(singles,actual)['brier']})
    result={'scope':'Post-hoc debugging only. Unconditionally normalizes positive-mass, full-schema numeric joint outputs; violates frozen primary tolerance and must not be reported as primary performance.',
        'repairable_repeat_cases':len(rows),'unrepairable_repeat_cases':missing,
        'raw_probability_mass':{'min':min(masses),'max':max(masses),'mean':float(np.mean(masses))} if masses else None,
        'conditional_brier_same_repairable_cases':{key:float(np.mean([r[key] for r in rows])) for key in ['repaired_joint_brier','market_brier','single_product_brier']} if rows else None}
    (OUT/'normalization-diagnostic.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':run()
