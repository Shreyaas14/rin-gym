"""Offline installed-tool workflow across acquisition, odds and rendered replay.

Run this file with an installed wheel's Python from outside the checkout. The
fixture uses the real cache writer and subprocess commands, never HTTP mocks or
network acquisition; all provider responses are synthetic and pre-cached.
"""
from datetime import datetime, timedelta, timezone
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import urlencode

from PIL import Image
from app.play_by_play.build import BASE as STATSBOMB
from pipelines.fifa.client import BASE, Client, dumps
from pipelines.fifa.contracts import REGISTRY
from pipelines.fifa.markets import GAMMA, MarketClient


def game_fixture():
    # Reuse the normalization contract fixture without adding the checkout to
    # sys.path: all application imports still come from the installed package.
    path = Path(__file__).parent / "fifa" / "test_pipeline.py"
    spec = importlib.util.spec_from_file_location("_tool_game_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.fixture()[:2]


class ToolWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.workspace = self.root / "selected-workspace"
        self.cwd = self.root / "unrelated-current-directory"
        self.cwd.mkdir()
        self.ignored = self.root / "ignored-env-workspace"
        self.match, self.timeline = game_fixture()
        self.kickoff = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
        self.started = int(self.kickoff.timestamp())
        self.finished = self.started + 112 * 60
        self.seed_game_cache()

    def invoke(self, *arguments):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        for key in ["http_proxy", "https_proxy", "all_proxy", "no_proxy"]:
            env.pop(key, None)
        env["RIN_GYM_WORKSPACE"] = str(self.ignored)
        # Fail accidental cache misses immediately rather than contacting a
        # provider from this synthetic, offline integration test.
        env.update(HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9",
                   ALL_PROXY="http://127.0.0.1:9", NO_PROXY="")
        result = subprocess.run(
            [sys.executable, "-m", "rin_gym", "--workspace", str(self.workspace),
             *arguments], cwd=self.cwd, env=env, capture_output=True, text=True,
            timeout=90,
        )
        self.assertEqual(result.returncode, 0,
                         f"{arguments}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        # Neither implicit relative paths nor the environment's other workspace
        # may receive artifacts from a command with an explicit workspace.
        self.assertEqual(list(self.cwd.iterdir()), [])
        self.assertFalse(self.ignored.exists())
        return result.stdout

    def json_result(self, output):
        decoder = json.JSONDecoder()
        for index, character in enumerate(output):
            if character != "{":
                continue
            try:
                result, length = decoder.raw_decode(output[index:])
            except ValueError:
                continue
            if not output[index + length:].strip():
                return result
        self.fail("Command did not finish with a JSON result: " + output)

    def seed_game_cache(self):
        cache = Client(self.workspace / "data/raw/fifa", offline=True)
        url = BASE + "calendar/matches?" + urlencode(dict(
            language="en", count=500, idCompetition="17", idSeason="2026"))
        fetched = "2026-10-01T00:00:00+00:00"
        cache.store(url, dumps({"Results": [self.match]}).encode(), fetched)
        cache.store(BASE + "timelines/match-A?language=en",
                    dumps(self.timeline).encode(), fetched)

    def seed_market_cache(self):
        cache = MarketClient(self.workspace / "data/raw/polymarket", offline=True)
        fetched = "2026-10-01T00:00:00+00:00"

        def store(url, response, **extra):
            cache.store(url, dumps(response).encode(), fetched, **extra)

        questions = ["Will Home win on 2026-06-01?",
                     "Will Home vs. Away end in a draw?",
                     "Will Away win on 2026-06-01?"]
        terms = ['If Home wins, this market will resolve to "Yes".',
                 'If the game ends in a draw, this market will resolve to "Yes".',
                 'If Away wins, this market will resolve to "Yes".']
        event = dict(id="10", slug="fifwc-home-away-2026-06-01",
                     title="Home vs. Away", series=[{"id": "11433"}], cyom=False)
        event["markets"] = [dict(
            id=str(index), slug=event["slug"] + "-" + str(index),
            question=questions[index - 1], sportsMarketType="moneyline",
            gameStartTime=self.match["Date"], outcomes='["Yes","No"]',
            clobTokenIds=json.dumps([str(index * 10 + 1), str(index * 10 + 2)]),
            conditionId="0x" + format(index, "064x"),
            questionID="0x" + format(index + 100, "064x"), approved=True,
            cyom=False, archived=False, enableOrderBook=True, negRisk=False,
            description=terms[index - 1] + "\nThis market refers only to the outcome "
                        "within the first 90 minutes of regular play plus stoppage time.",
        ) for index in range(1, 4)]
        params = dict(tag_id=102232, limit=100, offset=0, order="id", ascending="true",
                      end_date_min=(self.kickoff - timedelta(days=7)).isoformat(),
                      end_date_max=(self.kickoff + timedelta(days=7)).isoformat())
        store(GAMMA + "events?" + urlencode(params), [event])
        records, histories = [], {}
        sample_times = [self.started + minute * 60 - 30 for minute in [0, 10, 47, 62, 112]]
        for market in event["markets"]:
            tokens = json.loads(market["clobTokenIds"])
            for index, (token, outcome) in enumerate(zip(tokens, ["Yes", "No"])):
                records.append(dict(
                    token_id=token, condition_id=market["conditionId"], module="v1_ctf",
                    clob_index=index, outcome=outcome, opposite_token_id=tokens[1-index],
                    title=market["question"], market_slug=market["slug"],
                    event_id=event["id"], event_slug=event["slug"], neg_risk=False,
                ))
                histories[token] = [dict(t=t, p=(.45 + .04*j) if index == 0 else (.55 - .03*j))
                                    for j, t in enumerate(sample_times)]
            params = dict(market=tokens[0], startTs=self.started-3600,
                          endTs=self.finished+120, fidelity=1)
            store("https://clob.polymarket.com/prices-history?" + urlencode(params),
                  {"history": histories[tokens[0]]})
        store(REGISTRY + urlencode({"token_id": ",".join(sorted(histories))}),
              {"data": records})
        body = dict(markets=list(histories), start_ts=self.started-3600,
                    end_ts=self.finished+300, fidelity=1)
        url = "https://clob.polymarket.com/batch-prices-history"
        store(url, {"history": histories}, cache_key=url + "#POST:" + dumps(body),
              request_method="POST", request_body=body)

    def test_offline_feed_odds_and_preview_work_in_a_separate_workspace(self):
        ingested = self.json_result(self.invoke(
            "pipeline", "fifa", "ingest", "--scope", "17:2026", "--offline"))
        self.assertTrue(Path(ingested["snapshot"]).is_relative_to(self.workspace))
        checked = self.json_result(self.invoke(
            "pipeline", "fifa", "verify", "--cache", "data/raw/fifa"))
        self.assertEqual((checked["matches"], checked["events"]), (1, 6))
        self.invoke("pipeline", "fifa", "export-replay", "--match", "match-A",
                    "--output", "artifacts/exported-source")
        exported = self.workspace / "artifacts/exported-source/fifa_match-A_timeline.json"
        self.assertEqual(json.loads(exported.read_text())["Event"], self.timeline["Event"])

        self.seed_market_cache()
        self.invoke("pipeline", "markets", "--dataset", "data/processed/fifa", "--offline")
        self.invoke("pipeline", "market-history", "--offline")
        checked = self.json_result(self.invoke(
            "pipeline", "verify-markets", "--report", "artifacts/market-verification.json"))
        self.assertTrue(checked["verified"])
        self.assertEqual((checked["retained_matches"], checked["certified_contracts"]), (1, 3))
        self.assertEqual((checked["price_tokens"], checked["price_points"]), (6, 30))
        self.assertTrue((self.workspace / "artifacts/market-verification.json").exists())

        database = "data/processed/replay/replay.duckdb"
        self.invoke("replay", "import", "--dataset", "data/processed/fifa",
                    "--markets", "data/processed/fifa-market-history", "--database", database)
        checked = self.json_result(self.invoke("replay", "verify", "--database", database))
        self.assertEqual((checked["matches"], checked["events"], checked["with_odds"]), (1, 6, 1))
        self.assertFalse(checked["score_mismatches"])
        rendered = self.json_result(self.invoke(
            "replay", "render", "--database", database, "--match", "match-A",
            "--output", "artifacts/replay-preview", "--preview"))
        self.assertTrue(rendered["preview"])
        self.assertIsNone(rendered["video"])
        output = self.workspace / "artifacts/replay-preview"
        for name in ["poster.png", "final.png", "preview-goal-1.png"]:
            with Image.open(output / name) as image:
                self.assertEqual(image.size, (1600, 900))
                image.verify()
        self.assertTrue((output / "render-manifest.json").exists())
        self.assertTrue((self.workspace / database).exists())

    def test_relative_play_by_play_output_uses_selected_workspace(self):
        match = dict(match_id=1, match_date="2024-07-14", home_score=0, away_score=0,
                     home_team=dict(home_team_id=1, home_team_name="Home"),
                     away_team=dict(away_team_id=2, away_team_name="Away"))
        event = dict(id="1", index=1, type={"name": "Pass"}, team={"id": 1},
                     player={"id": 10, "name": "Player"}, period=1,
                     timestamp="00:00:01.000", minute=0, second=1, possession=1,
                     possession_team={"id": 1}, play_pattern={"name": "Regular Play"},
                     location=[40, 30], **{"pass": {"end_location": [60, 30]}})
        frame = dict(event_uuid="1", visible_area=[], freeze_frame=[
            dict(actor=True, teammate=True, keeper=False, location=[40, 30])])
        cache = self.workspace / "data/raw/play-by-play"
        cache.mkdir(parents=True)
        for name, endpoint, payload in [
            ("matches_1_1", "matches/1/1.json", [match]),
            ("events_1", "events/1.json", [event]),
            ("frames_1", "three-sixty/1.json", [frame]),
            ("lineups_1", "lineups/1.json", []),
        ]:
            body = dumps(payload).encode()
            (cache / (name + ".json")).write_bytes(body)
            (cache / (name + ".meta.json")).write_text(json.dumps(dict(
                url=STATSBOMB + endpoint, sha256=hashlib.sha256(body).hexdigest(),
                retrieved_utc="2026-10-01T00:00:00+00:00")))
        self.invoke("play-by-play", "--match", "1", "--competition", "1",
                    "--season", "1", "--output", "artifacts/detailed")
        output = self.workspace / "artifacts/detailed"
        state = json.loads((output / "state.json").read_text())
        self.assertEqual(state["summary"]["events"], 1)
        self.assertEqual(state["events"][0]["source_event"], event)
        self.assertEqual(state["summary"]["events_with_360"], 1)
        self.assertFalse(state["forecast_ready"])
        self.assertTrue((output / "index.html").is_file())
        self.assertTrue((output / "manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
