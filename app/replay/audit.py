"""Verify every imported match can be replayed without score or price leakage."""

from .model import Replay
from .store import connect, list_matches, load_match


def verify_database(database):
    with connect(database) as con:
        total = con.execute("SELECT count(*) FROM matches").fetchone()[0]
    counts = dict(
        matches=0, events=0, with_odds=0, extra_time=0, shootout=0, score_mismatches=[]
    )
    for match in list_matches(database, limit=max(1, total)):
        replay = Replay(load_match(database, match["match_id"]))
        state = replay.state(replay.finish)
        counts["matches"] += 1
        counts["events"] += len(replay.events)
        counts["with_odds"] += bool(replay.markets)
        counts["extra_time"] += 7 in replay.period_starts
        counts["shootout"] += 11 in replay.period_starts
        expected = match["final_home_score"], match["final_away_score"]
        if (
            None not in expected
            and state["phase"] == "FULL TIME"
            and state["score"] != expected
        ):
            counts["score_mismatches"].append(match["match_id"])
        for timestamp in (replay.start, replay.start + 1, replay.end, replay.finish):
            for side in replay.markets:
                point, _ = replay.odds(side, timestamp)
                if point and not (0 < timestamp - point["t"] <= replay.max_price_age):
                    raise ValueError("Price chronology failure: " + match["match_id"])
    if counts["score_mismatches"]:
        raise ValueError("Final score mismatches: " + str(counts["score_mismatches"]))
    return counts
