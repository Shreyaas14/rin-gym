"""Verified anonymous Parquet catalog with density-ranked top-ten selection."""

import copy
import hashlib
from pathlib import Path

from .data import Fixture, ROOT
from .storage import read_catalog


DEFAULT_PATH = ROOT / 'data/processed/market-demo.parquet'
PUBLIC_FIELDS = ('id', 'label', 'event_count', 'has_reference', 'rank',
                 'active_event_count', 'active_minutes', 'event_density')


class Catalog:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else DEFAULT_PATH
        self._file_hash = None
        if not self.path.is_file():
            if path is not None:
                raise ValueError('Dataset Parquet file was not found')
            games = [Fixture()]
        else:
            self._file_hash = self._hash()
            games = [Fixture.from_payload(payload) for payload in read_catalog(self.path)]
            if self._hash() != self._file_hash:
                raise ValueError('Game catalog changed while loading')
        fixtures, candidates = {}, []
        for fixture in games:
            game_id = fixture.game_id
            if (len(game_id) != 17 or game_id[0] != 'g'
                    or any(c not in '0123456789abcdef' for c in game_id[1:]) or game_id in fixtures):
                raise ValueError('Duplicate or invalid catalog game ID')
            if not fixture.label.startswith('Game ') or not fixture.label[5:].isdigit():
                raise ValueError('Invalid anonymous game metadata')
            fixtures[game_id] = fixture
            candidates.append({'id': game_id, 'label': fixture.label,
                               'event_count': len(fixture.events), 'has_reference': bool(fixture.prices),
                               **fixture.activity_metrics()})
        self._rank(candidates, fixtures)

    def _hash(self):
        try:
            with self.path.open('rb') as source:
                return hashlib.file_digest(source, 'sha256').hexdigest()
        except OSError as exc:
            raise ValueError('Game catalog is missing or malformed') from exc

    def _rank(self, candidates, fixtures):
        ranked = sorted(candidates, key=lambda item: (-item['event_density'], -item['active_event_count'],
                                                       -item['event_count'], item['id']))
        self.candidate_count = len(ranked)
        self._ranking = [{**item, 'rank': index + 1, 'selected': index < 10}
                         for index, item in enumerate(ranked)]
        self._games = {item['id']: item for item in self._ranking[:10]}
        self._fixtures = {game_id: fixtures[game_id] for game_id in self._games}
        self.default_id = ranked[0]['id']

    def list_games(self):
        return [{key: item[key] for key in PUBLIC_FIELDS}
                for item in self._games.values()]

    def ranking_report(self):
        """Anonymous full-candidate audit; the picker exposes list_games only."""
        return [{**{key: item[key] for key in PUBLIC_FIELDS}, 'selected': item['selected']}
                for item in self._ranking]

    def load(self, game_id):
        if not isinstance(game_id, str) or game_id not in self._games:
            raise ValueError('Unknown game selection')
        if self._file_hash is not None and self._hash() != self._file_hash:
            raise ValueError('Game catalog content hash mismatch')
        return copy.deepcopy(self._fixtures[game_id])
