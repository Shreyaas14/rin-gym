"""Authoritative replay, agent refreshes and browser commands for one session."""
from bisect import bisect_right
from collections import OrderedDict
import math
import threading
import time
import uuid

from .agents import STRATEGIES, Valuations
from .engine import Exchange


class Session:
    def __init__(self, fixture, valuations, mode='local', catalog=None):
        self.fixture, self.valuations = fixture, valuations
        self.catalog = catalog
        self.providers = {fixture.source_hash: valuations}
        self.lock = threading.RLock()
        self.reset(mode)

    def reset(self, mode):
        if mode not in ('local', 'live', 'recorded'):
            raise ValueError('Unknown valuation mode')
        if mode == 'live' and not self.valuations.live_available:
            raise ValueError('Set TYPESAFE_API_KEY in the server environment to enable Live Jev')
        if mode == 'recorded' and self.valuations.recorded is None:
            raise ValueError('No compatible Jev recording configured')
        self.epoch = str(uuid.uuid4())
        self.mode, self.playing, self.speed = mode, False, 1
        self.movie = self.fixture.movie_at(self.fixture.kickoff)
        self.cursor = self.fixture.kickoff
        self.last_wall = time.monotonic()
        self.version, self.round_number = 0, 0
        self.exchange = Exchange()
        self.history, self.journal = [], []
        self.commands = OrderedDict()
        self.error, self.message, self.mark = None, '', None
        self.round_observation = None
        self.round_index = bisect_right(self.fixture.rounds, self.cursor)
        self.agents = {agent: {'id': agent, 'name': config['name'], 'valuation': None,
                              'source': '', 'last_action': 'Waiting for a valuation', 'error': None}
                       for agent, config in STRATEGIES.items()}
        self.refresh()

    def _stamp_trades(self):
        for trade in self.exchange.trades:
            trade.setdefault('time', self.cursor)

    def _order(self, owner, side, price, size, tif):
        if not size or not 1 <= price <= 99:
            return None
        try:
            result = self.exchange.submit(owner, side, price, size, tif)
            self._stamp_trades()
            return result
        except ValueError:
            return None

    def _capacity(self, owner, side, price, desired):
        account = self.exchange.snapshot()['accounts'][owner]
        pending = sum(o.remaining for o in self.exchange.orders.values()
                      if o.owner == owner and o.side == side and o.status == 'open')
        capacity = 100 - account['position'] - pending if side == 'buy' else 100 + account['position'] - pending
        cost = price if side == 'buy' else 100 - price
        return max(0, min(desired, capacity, int(account['available'] // cost)))

    def _batch(self, agent):
        info, config = self.agents[agent], STRATEGIES[agent]
        if info['valuation'] is None:
            return
        self.exchange.cancel_all(agent)
        position = self.exchange.accounts[agent].position
        center = max(1, min(99, info['valuation'] * 100 - 3 * position / 100))
        book = self.exchange.snapshot()
        direction, edge_price = None, None
        if book['asks'] and center - book['asks'][0]['price'] >= config['edge']:
            direction, edge_price = 'buy', math.floor(center - config['edge'])
        elif book['bids'] and book['bids'][0]['price'] - center >= config['edge']:
            direction, edge_price = 'sell', math.ceil(center + config['edge'])
        if direction and 1 <= edge_price <= 99:
            result = self._order(agent, direction, edge_price,
                                 self._capacity(agent, direction, edge_price, config['size']), 'IOC')
            if result and result['fills']:
                info['last_action'] = f'{"Bought" if direction == "buy" else "Sold"} {sum(t["size"] for t in result["fills"])} YES; price met valuation edge'
        position = self.exchange.accounts[agent].position
        center = max(1, min(99, info['valuation'] * 100 - 3 * position / 100))
        for level, size in enumerate(config['depth']):
            distance = config['spread'] + 2 * level
            for side, price in [('buy', math.floor(center - distance)),
                                ('sell', math.ceil(center + distance))]:
                if 1 <= price <= 99:
                    self._order(agent, side, price, self._capacity(agent, side, price, size), 'post_only')
        if not direction:
            info['last_action'] = 'Quoting around valuation, adjusted for inventory'

    def _publish(self):
        book = self.exchange.snapshot()
        mid = (book['bids'][0]['price'] + book['asks'][0]['price']) / 2 if book['bids'] and book['asks'] else None
        if mid is not None:
            self.mark = mid
        point = {'time': self.cursor, 'midpoint': mid}
        if not self.history or point != self.history[-1]:
            self.history.append(point)
        self.version += 1

    def refresh(self, retry=False):
        if self.exchange.settled:
            return
        failed = {agent for agent, info in self.agents.items() if info['error']}
        if retry and not failed:
            raise ValueError('No failed agent valuation to retry')
        if not retry:
            self.exchange.cancel_all()
        if not retry:
            self.round_observation = self.fixture.observation(self.cursor)
        observation = self.round_observation
        values = self.valuations.evaluate(observation, self.mode)
        if not retry:
            self.round_number += 1
        for agent, result in values.items():
            if retry and agent not in failed:
                continue
            self.agents[agent].update(valuation=result['value'], source=result['model'], error=result['error'])
            if result['error']:
                self.agents[agent]['last_action'] = 'Unavailable this round'
        self.journal.append({'type': 'retry' if retry else 'valuation', 'time': self.cursor,
                             'observation': observation, 'answers': values})
        self.error = None
        order = ('anchor', 'pulse') if self.round_number % 2 else ('pulse', 'anchor')
        for agent in order:
            if not retry or agent in failed:
                self._batch(agent)
        if all(info['valuation'] is None for info in self.agents.values()):
            self.playing = False
            self.error = 'Both agents are unavailable. Retry the round or reset in Local simulation mode.'
        self._publish()
        # Network time is not replay time.
        self.last_wall = time.monotonic()

    def advance(self, target):
        if self.exchange.settled or self.error:
            return
        when = self.fixture.time_at(min(target, self.fixture.duration))
        while self.round_index < len(self.fixture.rounds) and self.fixture.rounds[self.round_index] <= when:
            self.cursor = self.fixture.rounds[self.round_index]
            self.movie = self.fixture.movie_at(self.cursor)
            self.round_index += 1
            self.refresh()
            if self.error:
                return
        self.movie, self.cursor = min(target, self.fixture.duration), when
        if when >= self.fixture.end:
            score = self.fixture.state(self.fixture.end)['score']
            self.exchange.settle(int(score[0] > score[1]))
            self.playing = False
            self.message = 'Regulation ended. All paper positions have settled.'
            self.journal.append({'type': 'settlement', 'time': self.cursor, 'score': score})
            self._publish()

    def tick(self):
        now = time.monotonic()
        if self.playing:
            # A suspended browser cannot jump through an entire game on reconnect.
            self.advance(self.movie + min(2, max(0, now - self.last_wall)) * self.speed)
        self.last_wall = time.monotonic()

    def command(self, body):
        if body.get('epoch') != self.epoch:
            raise ValueError('Session changed. Refresh before submitting another command.')
        command_id = body.get('command_id')
        if not isinstance(command_id, str) or not 1 <= len(command_id) <= 128:
            raise ValueError('A command ID is required')
        if command_id in self.commands:
            self.message = self.commands[command_id]
            return
        action = body.get('action')
        # A reset/selection should not consume another old-game model round.
        if action not in ('reset', 'select_game'):
            self.tick()
        if action != 'select_game':
            self.message = ''
        if action == 'reset':
            self.reset(body.get('mode', self.mode))
        elif action == 'select_game':
            if self.catalog is None:
                raise ValueError('No game catalog is configured')
            game_id = body.get('game_id')
            if not isinstance(game_id, str):
                raise ValueError('Select a game from the catalog')
            fixture = self.catalog.load(game_id)
            if self.mode == 'recorded' and fixture.source_hash != self.fixture.source_hash:
                raise ValueError('This recording belongs to another game. Switch to Local simulation or Live Jev before selecting a different game.')
            provider = self.providers.get(fixture.source_hash)
            if provider is None:
                provider = Valuations(fixture.source_hash, model=self.valuations.model)
            # Validate every selection before replacing the existing game.
            self.fixture, self.valuations = fixture, provider
            self.providers[fixture.source_hash] = provider
            self.reset(self.mode)
            self.message = f'{fixture.label} loaded. Paper accounts and the order book have been reset.'
        elif action in ('play', 'pause'):
            if action == 'play' and (self.exchange.settled or self.error):
                raise ValueError('Reset a settled session, or retry a failed valuation round.')
            self.playing = action == 'play'
        elif action == 'speed':
            speed = body.get('speed')
            if type(speed) is not int or speed not in (1, 2, 4):
                raise ValueError('Replay speed must be 1, 2 or 4')
            self.speed = speed
        elif action in ('step', 'next_event'):
            self.playing = False
            times = self.fixture.rounds if action == 'step' else self.fixture.times
            index = bisect_right(times, self.cursor)
            when = times[index] if index < len(times) else self.fixture.end
            self.advance(self.fixture.movie_at(when) + 1e-7)
        elif action == 'retry':
            self.refresh(retry=True)
        elif action in ('order', 'close'):
            if self.error:
                raise ValueError('Retry the failed valuation round before trading')
            if action == 'close':
                position = self.exchange.accounts['user'].position
                if not position:
                    raise ValueError('No open position to close')
                side, price, size, tif = ('sell', 1, position, 'IOC') if position > 0 else ('buy', 99, -position, 'IOC')
            else:
                outcome, side = body.get('outcome'), body.get('side')
                price, size, tif = body.get('price'), body.get('size'), body.get('tif')
                if outcome not in ('YES', 'NO') or side not in ('buy', 'sell'):
                    raise ValueError('Select a valid outcome and side')
                if type(price) is not int or not 1 <= price <= 99:
                    raise ValueError('Limit price must be whole cents from 1 to 99')
                if tif not in ('IOC', 'GTC'):
                    raise ValueError('Use a quick order or a limit order')
                if outcome == 'NO':
                    side, price = ('sell' if side == 'buy' else 'buy'), 100 - price
            result = self.exchange.submit('user', side, price, size, tif)
            self._stamp_trades()
            filled = sum(t['size'] for t in result['fills'])
            self.message = f'{filled} shares filled. {result["order"]["remaining"]} shares {"resting until refresh" if result["order"]["status"] == "open" else "unfilled"}.'
            if filled:
                for agent in ('anchor', 'pulse'):
                    self._batch(agent)
            self._publish()
        elif action == 'cancel':
            self.exchange.cancel('user', body.get('order_id'))
            self.message = 'Order cancelled.'
            self._publish()
        else:
            raise ValueError('Unknown command')
        self.last_wall = time.monotonic()
        self.commands[command_id] = self.message
        self.journal.append({'type': 'command', 'time': self.cursor, 'command': dict(body)})
        self.version += 1

    def snapshot(self):
        exchange = self.exchange.snapshot(mark=self.mark)
        mid = (exchange['bids'][0]['price'] + exchange['asks'][0]['price']) / 2 if exchange['bids'] and exchange['asks'] else None
        return {'epoch': self.epoch, 'version': self.version, 'playing': self.playing,
                'game_id': getattr(self.fixture, 'game_id', 'game-default'),
                'games': self.catalog.list_games() if self.catalog else [],
                'games_candidate_count': getattr(self.catalog, 'candidate_count',
                    len(self.catalog.list_games()) if self.catalog else 1),
                'speed': self.speed, 'mode': self.mode, 'error': self.error, 'message': self.message,
                'status': 'Settled' if exchange['settled'] else 'Paused' if not self.playing else 'Replaying',
                'replay': self.fixture.state(self.cursor),
                'market': {'question': 'Will Team A win in regulation time?', 'midpoint': mid,
                           'last_trade': exchange['trades'][-1] if exchange['trades'] else None,
                           'history': [dict(point) for point in self.history],
                           'mark_stale': mid is None and not exchange['settled'], **self.fixture.reference(self.cursor)},
                'exchange': exchange, 'agents': [dict(info) for info in self.agents.values()],
                'capabilities': {'live': self.valuations.live_available, 'recorded': self.valuations.recorded is not None}}

    def export(self):
        return {'schema': 'market-session/v1', 'epoch': self.epoch, 'mode': self.mode,
                'game_id': getattr(self.fixture, 'game_id', 'game-default'),
                'source_hash': self.fixture.source_hash, 'model': self.valuations.model,
                'events': self.journal, 'exchange': self.exchange.journal,
                'state': self.snapshot(), 'recording': self.valuations.recording(),
                'provider_receipts': list(self.valuations.receipts)}
