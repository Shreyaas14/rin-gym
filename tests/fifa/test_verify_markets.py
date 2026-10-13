import copy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

from pipelines.fifa.market_history import PRICES
from pipelines.fifa.verify_markets import verify_prices


class RawPriceVerificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        raw = {
            "history": {
                "a": [{"t": 90, "p": 0.2}, {"t": 110, "p": 0.3}],
                "b": [{"t": 90, "p": 0.8}, {"t": 110, "p": 0.7}],
            }
        }
        body = json.dumps(raw).encode()
        self.digest = hashlib.sha256(body).hexdigest()
        (self.root / (self.digest + ".json.gz")).write_bytes(gzip.compress(body))
        self.sources = [
            {
                "url": "https://clob.polymarket.com/batch-prices-history",
                "sha256": self.digest,
                "request_body": {"markets": ["a", "b"]},
            }
        ]
        self.markets = [
            {
                "match_id": "m",
                "market_id": "k",
                "tokens": [
                    {
                        "token_id": token,
                        "status": "included",
                        "outcome_index": i,
                        "outcome": name,
                        "points": 2,
                        "in_game_points": 1,
                        "history_sha256": self.digest,
                    }
                    for i, (token, name) in enumerate([("a", "Yes"), ("b", "No")])
                ],
            }
        ]
        self.rows = [
            dict(
                match_id="m",
                market_id="k",
                token_id=token,
                outcome_index=i,
                outcome=name,
                timestamp=point["t"],
                price=point["p"],
                in_game=point["t"] >= 100,
                source_response_sha256=self.digest,
            )
            for i, (token, name) in enumerate([("a", "Yes"), ("b", "No")])
            for point in raw["history"][token]
        ]

    def check(self, rows=None, markets=None, sources=None):
        path = self.root / "prices.parquet"
        # Regenerate a valid Parquet file: semantic checks must catch a writer
        # bug even when its own manifest checksum would be consistent.
        pq.write_table(
            pa.Table.from_pylist(rows or self.rows, schema=PRICES),
            path,
            row_group_size=1,
        )
        return verify_prices(
            path,
            markets or self.markets,
            self.root,
            sources or self.sources,
            {"m": (100, 120)},
        )

    def test_batch_token_history_matches_across_parquet_batches(self):
        self.assertEqual(self.check(), (4, 2))

    def test_price_and_timestamp_changes_fail(self):
        for field, value in (("price", 0.999), ("timestamp", 91)):
            rows = copy.deepcopy(self.rows)
            rows[0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError, "differs from raw history"
            ):
                self.check(rows)

    def test_swapped_in_game_flags_fail_despite_unchanged_counts(self):
        rows = copy.deepcopy(self.rows)
        rows[0]["in_game"], rows[1]["in_game"] = True, False
        with self.assertRaisesRegex(ValueError, "in-game flag"):
            self.check(rows)

    def test_incomplete_raw_history_coverage_fails(self):
        markets = copy.deepcopy(self.markets)
        markets[0]["tokens"][0]["points"] = 1
        with self.assertRaisesRegex(ValueError, "Raw history sample count"):
            self.check(markets=markets)

    def test_single_history_request_must_own_token(self):
        raw = {"history": [{"t": 90, "p": 0.2}, {"t": 110, "p": 0.3}]}
        body = json.dumps(raw).encode()
        digest = hashlib.sha256(body).hexdigest()
        (self.root / (digest + ".json.gz")).write_bytes(gzip.compress(body))
        rows = copy.deepcopy(self.rows[:2])
        for row in rows:
            row["source_response_sha256"] = digest
        markets = copy.deepcopy(self.markets)
        markets[0]["tokens"] = markets[0]["tokens"][:1]
        markets[0]["tokens"][0]["history_sha256"] = digest
        source = [
            {
                "url": "https://clob.polymarket.com/prices-history?market=a",
                "sha256": digest,
            }
        ]
        self.assertEqual(self.check(rows, markets, source), (2, 1))
        source[0]["url"] = "https://clob.polymarket.com/prices-history?market=b"
        with self.assertRaisesRegex(ValueError, "request token mismatch"):
            self.check(rows, markets, source)
