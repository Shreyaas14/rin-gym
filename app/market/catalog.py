"""Hash-verified portable match catalog with no database runtime dependency."""

import copy
import hashlib
import json
from pathlib import Path

from .data import Fixture, POLICY_VERSION, ROOT


DEFAULT_FOLDER = ROOT / 'data/processed/market-demo'
PUBLIC_FIELDS = ('id', 'label', 'event_count', 'has_reference', 'rank',
                 'active_event_count', 'active_minutes', 'event_density')


class Catalog:
    def __init__(self, folder=None):
        self.folder = Path(folder) if folder is not None else DEFAULT_FOLDER
        self._fallback = None
        self._fixtures = {}
        if not (self.folder / 'manifest.json').is_file():
            if folder is not None:
                raise ValueError('Dataset manifest was not found')
            fixture = Fixture()
            self._fallback = fixture
            item = {'id': fixture.game_id, 'label': fixture.label,
                    'event_count': len(fixture.events), 'has_reference': bool(fixture.prices),
                    **fixture.activity_metrics()}
            self._rank([item], {fixture.game_id: fixture})
            return
        try:
            manifest = json.loads((self.folder / 'manifest.json').read_text())
            if (manifest.get('schema') != 'market-catalog/v1'
                    or manifest.get('policy_version') != POLICY_VERSION):
                raise ValueError('Unsupported anonymous dataset manifest')
            candidates = {}
            for item in manifest['games']:
                game_id = item['id']
                if (not isinstance(game_id, str) or len(game_id) != 17 or game_id[0] != 'g'
                        or any(c not in '0123456789abcdef' for c in game_id[1:]) or game_id in candidates):
                    raise ValueError('Duplicate or invalid catalog game ID')
                if (not isinstance(item['label'], str) or not item['label'].startswith('Game ')
                        or not item['label'][5:].isdigit() or type(item['event_count']) is not int
                        or item['event_count'] < 1 or type(item['has_reference']) is not bool):
                    raise ValueError('Invalid anonymous game metadata')
                self._path(item['file'])
                if (not isinstance(item['sha256'], str) or len(item['sha256']) != 64
                        or any(c not in '0123456789abcdef' for c in item['sha256'])):
                    raise ValueError('Invalid catalog content hash')
                candidates[game_id] = dict(item)
            if manifest['default_id'] not in candidates:
                raise ValueError('Dataset default game is missing')
            fixtures = {}
            for game_id, item in candidates.items():
                fixture = self._load_candidate(item)
                # Metrics are derived from the verified game, never trusted
                # from optional manifest fields or a previous ranking.
                item.update(fixture.activity_metrics())
                fixtures[game_id] = fixture
            self._rank(list(candidates.values()), fixtures)
        except (OSError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
            raise ValueError('Malformed dataset manifest') from exc

    def _rank(self, candidates, fixtures):
        ranked = sorted(candidates, key=lambda item: (-item['event_density'], -item['active_event_count'],
                                                       -item['event_count'], item['id']))
        self.candidate_count = len(ranked)
        self._ranking = [{**item, 'rank': index + 1, 'selected': index < 10}
                         for index, item in enumerate(ranked)]
        self._games = {item['id']: item for item in self._ranking[:10]}
        self._fixtures = {game_id: fixtures[game_id] for game_id in self._games}
        self.default_id = ranked[0]['id']

    def _path(self, filename):
        if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith('.json'):
            raise ValueError('Catalog paths must be local JSON filenames')
        path = (self.folder / filename).resolve()
        if path.parent != self.folder.resolve():
            raise ValueError('Catalog file escapes dataset folder')
        return path

    def list_games(self):
        return [{key: item[key] for key in PUBLIC_FIELDS}
                for item in self._games.values()]

    def ranking_report(self):
        """Anonymous full-candidate audit; the picker exposes list_games only."""
        return [{**{key: item[key] for key in PUBLIC_FIELDS}, 'selected': item['selected']}
                for item in self._ranking]

    def _read_candidate(self, item):
        raw = self._path(item['file']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('Game content hash mismatch')
        return raw

    def _load_candidate(self, item):
        fixture = Fixture.from_payload(json.loads(self._read_candidate(item)))
        if (fixture.game_id != item['id'] or fixture.label != item['label']
                or len(fixture.events) != item['event_count']
                or bool(fixture.prices) != item['has_reference']):
            raise ValueError('Game metadata disagrees with catalog')
        return fixture

    def load(self, game_id):
        if not isinstance(game_id, str) or game_id not in self._games:
            raise ValueError('Unknown game selection')
        if self._fallback is not None:
            return copy.deepcopy(self._fallback)
        item = self._games[game_id]
        try:
            # Recheck disk integrity even when the parsed top-ten fixture is
            # memoized. Each caller receives its own mutable replay object.
            self._read_candidate(item)
            return copy.deepcopy(self._fixtures[game_id])
        except (OSError, KeyError, TypeError, AttributeError, StopIteration, json.JSONDecodeError) as exc:
            raise ValueError('Game bundle is missing or malformed') from exc
