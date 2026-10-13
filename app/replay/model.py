"""Replay state from recorded events only; never infer movement between actions."""

from bisect import bisect_left, bisect_right
from collections import defaultdict
import math

PRIORITY = {
    "goal": 100,
    "own_goal": 100,
    "match_end": 90,
    "period_start": 80,
    "period_end": 80,
    "shot_attempt": 60,
    "red_card": 55,
    "yellow_card": 50,
}
PERIODS = {
    3: "FIRST HALF",
    5: "SECOND HALF",
    7: "EXTRA TIME · FIRST HALF",
    9: "EXTRA TIME · SECOND HALF",
    11: "PENALTY SHOOTOUT",
}
BREAKS = {
    3: "HALF TIME",
    5: "REGULATION END",
    7: "EXTRA TIME · BREAK",
    9: "EXTRA TIME END",
    11: "SHOOTOUT END",
}


def has_location(event):
    return event["location_status"] == "present_unverified" and all(
        isinstance(event[k], (int, float))
        and math.isfinite(event[k])
        and 0 <= event[k] <= 100
        for k in ("x_raw", "y_raw")
    )


class Replay:
    def __init__(self, data, max_price_age=90):
        self.match, self.markets = data["match"], data["markets"]
        self.odds_status, self.database = data["odds_status"], data["database"]
        self.source_metadata = data.get("source_metadata", {})
        self.max_price_age = max_price_age
        self.events = sorted(
            (dict(e, t=e["event_time_utc"].timestamp()) for e in data["events"]),
            key=lambda e: (e["t"], e["source_index"]),
        )
        self.times = [e["t"] for e in self.events]
        self.start, self.end = self.times[0] - 3, self.times[-1]
        self.finish = self.end + 4
        self.regulation_end = next(
            (
                e["t"]
                for e in self.events
                if e["period_code"] == 5 and e["event_kind"] == "period_end"
            ),
            None,
        )
        self.period_starts = {
            e["period_code"]: e["t"]
            for e in self.events
            if e["event_kind"] == "period_start"
        }
        self.price_times = {
            side: [p["t"] for p in m["prices"]] for side, m in self.markets.items()
        }
        grouped = defaultdict(list)
        for e in self.events:
            grouped[e["t"]].append(e)
        self.segments = []

        def add(seconds, a, b):
            offset = self.segments[-1][1] if self.segments else 0
            self.segments.append((offset, offset + seconds, a, b))

        add(3, self.start, self.start)
        previous = self.start
        for at, events in sorted(grouped.items()):
            add(max(0.12, min(3.4, (at - previous) / 110)), previous, at)
            kind = max(events, key=lambda e: PRIORITY.get(e["event_kind"], 0))[
                "event_kind"
            ]
            add(
                3
                if kind in ("goal", "own_goal")
                else 1.2
                if kind in ("period_start", "period_end", "match_end")
                else 0.52,
                at,
                at,
            )
            previous = at
        add(1.4, previous, self.finish)
        add(3, self.finish, self.finish)
        self.segment_ends = [s[1] for s in self.segments]
        self.duration = self.segment_ends[-1]

    def utc_time(self, movie_time):
        segment = self.segments[
            min(bisect_right(self.segment_ends, movie_time), len(self.segments) - 1)
        ]
        a, b, start, end = segment
        fraction = max(0, min(1, (movie_time - a) / (b - a)))
        return start + (end - start) * fraction

    def movie_time(self, timestamp):
        return next(
            (
                a + min(0.7, (b - a) / 2)
                for a, b, start, end in self.segments
                if start == end == timestamp
            ),
            self.duration - 0.1,
        )

    def phase(self, timestamp):
        phase = "BEFORE KICKOFF"
        for e in self.events[: bisect_right(self.times, timestamp)]:
            if e["event_kind"] == "match_end":
                phase = "FULL TIME"
            elif e["event_kind"] == "period_start":
                phase = PERIODS.get(e["period_code"], "PLAY")
            elif e["event_kind"] == "period_end":
                phase = BREAKS.get(e["period_code"], "BREAK")
        return phase

    def state(self, timestamp):
        past = self.events[: bisect_right(self.times, timestamp)]
        batch = [e for e in past if e["t"] == past[-1]["t"]] if past else []
        current = (
            max(batch, key=lambda e: PRIORITY.get(e["event_kind"], 0))
            if batch
            else None
        )
        # FIFA can put a pre-goal and post-goal score in the same UTC batch.
        # Collapse that batch only; never read a future event or final match row.
        score = tuple(
            max((e[k] for e in batch if e[k] is not None), default=0)
            for k in ("home_score", "away_score")
        )
        penalties = tuple(
            max((e[k] for e in batch if e[k] is not None), default=0)
            for k in ("home_shootout_score", "away_shootout_score")
        )
        return dict(
            past=past,
            current=current,
            score=score,
            penalties=penalties,
            phase=self.phase(timestamp),
        )

    def odds(self, side, timestamp):
        if side not in self.markets:
            return None, "No mapped odds"
        if self.regulation_end is None:
            return None, "Regulation boundary unavailable"
        if timestamp > self.regulation_end:
            return None, "Regulation market ended"
        index = bisect_left(self.price_times[side], timestamp) - 1
        if index < 0:
            return None, "No preceding sample"
        price = self.markets[side]["prices"][index]
        age = timestamp - price["t"]
        if age > self.max_price_age:
            return None, f"Stale sample · {int(age)}s old"
        return price, f"{int(age)}s old"

    def manifest(self):
        return dict(
            match_id=self.match["match_id"],
            home=self.match["home_team_name"],
            away=self.match["away_team_name"],
            competition=self.match["competition_name"],
            database=self.database,
            source_metadata=self.source_metadata,
            events=len(self.events),
            located_events=sum(map(has_location, self.events)),
            suppressed_location_events=sum(
                e["x_raw"] is not None and not has_location(e) for e in self.events
            ),
            duration_seconds=self.duration,
            odds_status=self.odds_status,
            market_ids={side: m["market_id"] for side, m in self.markets.items()},
            price_policy=f"Strictly preceding samples, max age {self.max_price_age}s; regulation odds end at regulation end.",
            position_policy="Observed event coordinates only; suppress missing, ambiguous, placeholder and invalid locations. No inferred movement.",
            time_policy="Recorded UTC events with nonlinear gap compression and holds.",
            original_dataset_version=self.match["dataset_version"],
        )
