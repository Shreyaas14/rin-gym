"""Forecast-only experiment: strict outputs, proper reward and frozen execution."""
import hashlib
import json
import math
from pathlib import Path
import sqlite3

from app.trade_env.config import OUTCOMES
from app.trade_env.costs import buy_cost

SYSTEM = '''Forecast the regulation-time result of this soccer match using only the supplied observations. Regulation includes stoppage time, excludes extra time and penalties, and may end in a draw. Prices are noisy evidence, not certain outcomes. Return only JSON with this exact schema: {"probs":{"home":0.40,"draw":0.30,"away":0.30}}. Use finite probabilities between 0 and 1 summing to 1. Do not select a trade or include explanations.'''


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def guard(ledger, ids):
    """Read-only integrity/exclusion check; never resolves or scores the holdout."""
    path = Path(ledger).resolve()
    if not path.is_file():
        raise ValueError('Required holdout ledger is missing')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    previous, protected, count, policy, cohort = '0'*64, set(), 0, False, False
    try:
        for seq, body, checksum in db.execute('SELECT seq,body,sha256 FROM events ORDER BY seq'):
            event = json.loads(body)
            if seq != count+1 or event['previous'] != previous or digest(event) != checksum:
                raise ValueError('Ledger integrity failure')
            previous, count = checksum, count+1
            if event['kind'] == 'policy': policy = True
            if event['kind'] == 'cohort':
                cohort = True
                protected.update(str(m['match_id']) for m in event['payload']['matches'])
    finally:
        db.close()
    if not policy: raise ValueError('Ledger has no initialized policy')
    if protected & set(map(str, ids)): raise ValueError('Protected cohort overlaps experiment')
    return {'ledger_head':previous, 'state':'enrolled' if cohort else 'pending_enrollment', 'protected_matches':len(protected)}


def messages(task):
    body = task['messages'][1]['content']
    if body.count('Buying now costs about:') != 1:
        raise ValueError('Unexpected observation template')
    body = body.split('Buying now costs about:')[0].rstrip()
    return [{'role':'system','content':SYSTEM}, {'role':'user','content':body}]


def parse(text):
    def unique(pairs):
        out = {}
        for k,v in pairs:
            if k in out: raise ValueError('Duplicate JSON key')
            out[k] = v
        return out
    try:
        obj = json.loads(text, object_pairs_hook=unique)
        if set(obj) != {'probs'} or set(obj['probs']) != set(OUTCOMES): return None
        p = obj['probs']
        if any(type(v) not in (int,float) or not math.isfinite(v) or not 0 <= v <= 1 for v in p.values()): return None
        total = sum(p.values())
        if abs(total-1) > 1e-6: return None
        return {k:p[k]/total for k in OUTCOMES}
    except (ValueError,TypeError,KeyError):
        return None


def brier(p, target):
    return sum((p[o] - float(o == target))**2 for o in OUTCOMES) if p else 2.0


def reward(p, target, points):
    # Equal weight per match; no access to P&L, prices, goal or price labels.
    return -brier(p, target)/points


def action(probs, shown_prices, position, config, margin=0.02):
    """One share; buy only with >2c predicted net edge, then hold to settlement.

    Selector receives observed prices only, never future fills or results.
    """
    if position != 'flat': return 'keep'
    if probs is None: return 'do_nothing'
    side = max(OUTCOMES, key=lambda o:probs[o]-buy_cost(shown_prices[o],config,o))
    return 'buy_'+side if probs[side]-buy_cost(shown_prices[side],config,side) > margin else 'do_nothing'
