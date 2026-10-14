"""Honest local baselines and the optional live TypeSafe valuation boundary."""
import hashlib
import json
import math
import os
from pathlib import Path
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait


STRATEGIES = {
    'anchor': {'name': 'Anchor', 'spread': 1, 'size': 12, 'edge': 1, 'depth': (8, 12, 16, 20),
               'instructions': 'Emphasize score, time remaining and pre-match strength. Consider all supplied events.'},
    'pulse': {'name': 'Pulse', 'spread': 2, 'size': 10, 'edge': 1, 'depth': (6, 10, 14, 18),
              'instructions': 'Emphasize recent attacking events and disciplinary changes alongside score and time.'},
}
QUESTION = ('Given only this anonymous match state, will the home team win at the end of regulation, '
            'including stoppage time and excluding extra time and penalties? Estimate the probability. ')


class ValuationError(ValueError):
    """A sanitized message safe for the browser and journal."""


def local_value(state, agent):
    """Independent Poisson continuation baseline, not a Jev recording."""
    remaining = max(.005, (94 - state['minute']) / 90)
    home, away = 1.6, 1.1
    if agent == 'pulse':
        for event in state['recent_events']:
            if event['type'] in ('Attempt at Goal', 'Goal!', 'Penalty Goal', 'Corner'):
                weight = .34 * math.exp(-event['age_seconds'] / 300)
                if event['side'] == 'home':
                    home += weight
                elif event['side'] == 'away':
                    away += weight
            if event['type'] == 'Penalty Awarded':
                if event['side'] == 'home':
                    home += .8
                elif event['side'] == 'away':
                    away += .8
    for side in ('home', 'away'):
        reds = sum(state['counts'][side].get(kind, 0)
                   for kind in ('Red card', 'Second Caution - Red Card', 'Second yellow card'))
        if side == 'home':
            home *= .65 ** reds
        else:
            away *= .65 ** reds
    lh, la = home * remaining, away * remaining
    ph = [math.exp(-lh) * lh ** k / math.factorial(k) for k in range(20)]
    pa = [math.exp(-la) * la ** k / math.factorial(k) for k in range(20)]
    value = sum(a * b for h, a in enumerate(ph) for v, b in enumerate(pa)
                if state['score'][0] + h > state['score'][1] + v)
    return max(0, min(1, value))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValuationError('TypeSafe redirect refused')


class Valuations:
    def __init__(self, source_hash, recorded=None, model='jev-1.13.0'):
        self.source_hash, self.model = source_hash, model
        self.cache = {}
        self.receipts = []
        self.recorded = None
        if recorded:
            self.recorded = json.loads(Path(recorded).read_text())
            if (self.recorded.get('schema') != 'market-valuations/v1'
                    or self.recorded.get('source_hash') != source_hash
                    or self.recorded.get('model') != model):
                raise ValueError('Recording schema or fixture hash mismatch')

    @property
    def live_available(self):
        return bool(os.environ.get('TYPESAFE_API_KEY'))

    def evaluate(self, state, mode):
        snapshot_id = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
        if mode == 'live':
            # A shared wall-clock deadline also handles a server that dribbles
            # bytes without reaching a socket timeout. Late responses may be
            # retained as receipts but cannot publish orders into the session.
            pool = ThreadPoolExecutor(max_workers=2)
            futures = [pool.submit(self._evaluate_one, state, mode, snapshot_id, a) for a in STRATEGIES]
            done, _ = wait(futures, timeout=10)
            items = [f.result() if f in done else {
                'value': None, 'model': self.model, 'snapshot_id': snapshot_id,
                'error': 'TypeSafe round deadline reached; retry this round'} for f in futures]
            pool.shutdown(wait=False, cancel_futures=True)
        else:
            items = [self._evaluate_one(state, mode, snapshot_id, a) for a in STRATEGIES]
        return dict(zip(STRATEGIES, items))

    def _evaluate_one(self, state, mode, snapshot_id, agent):
        key = (mode, self.model, snapshot_id, agent)
        if key in self.cache:
            return dict(self.cache[key])
        try:
            if mode == 'local':
                value, model = local_value(state, agent), 'Poisson baseline v1'
            elif mode == 'recorded':
                if self.recorded is None:
                    raise ValuationError('No compatible model recording configured')
                item = self.recorded['valuations'][snapshot_id][agent]
                value, model = item['value'], item['model']
            elif mode == 'live':
                value, model = self._live(state, agent)
            else:
                raise ValuationError('Unknown valuation mode')
            if (isinstance(value, bool) or not isinstance(value, (float, int))
                    or not math.isfinite(value) or not 0 <= value <= 1):
                raise ValuationError('Invalid model probability')
            if mode != 'local' and model != self.model:
                raise ValuationError('Model version mismatch')
            item = {'value': value, 'model': model, 'snapshot_id': snapshot_id, 'error': None}
            self.cache[key] = item
            return dict(item)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            # Unexpected transport/parser errors can embed response text or
            # headers. Only our own sanitized messages leave this boundary.
            error = str(exc) if isinstance(exc, ValuationError) else 'Valuation unavailable; retry this round'
            return {'value': None, 'model': self.model, 'snapshot_id': snapshot_id, 'error': error}

    def _live(self, state, agent):
        key = os.environ.get('TYPESAFE_API_KEY')
        if not key:
            raise ValuationError('Live Jev requires TYPESAFE_API_KEY in the server environment')
        payload = {'model': self.model, 'state': state, 'questions': {
            'home_win': {'type': 'noul', 'instructions': QUESTION + STRATEGIES[agent]['instructions']}}}
        request = urllib.request.Request('https://api.typesafe.ai/v1/systemone',
            data=json.dumps(payload).encode(), method='POST',
            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=10) as response:
                raw = response.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise ValuationError('TypeSafe response exceeded size limit')
                answer = json.loads(raw)
        except urllib.error.HTTPError as exc:
            raise ValuationError(f'TypeSafe request failed (HTTP {exc.code}); retry this round') from None
        except (urllib.error.URLError, TimeoutError):
            raise ValuationError('TypeSafe request timed out or could not connect; retry this round') from None
        try:
            item = answer['answers']['home_win']
            if item['type'] != 'noul':
                raise ValuationError('TypeSafe returned an unexpected answer type')
            self.receipts.append({'agent': agent, 'request': payload, 'response': answer})
            return item['noul'], answer['model']
        except (KeyError, TypeError):
            raise ValuationError('TypeSafe returned an invalid response') from None

    def recording(self):
        values = {}
        for (mode, model, snapshot_id, agent), item in self.cache.items():
            if mode == 'live':
                values.setdefault(snapshot_id, {})[agent] = item
        return {'schema': 'market-valuations/v1', 'source_hash': self.source_hash,
                'model': self.model, 'valuations': values}
