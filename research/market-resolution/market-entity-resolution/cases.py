"""Hand-authored synthetic contracts; these are not exchange rule snapshots."""

CASES = []


def add(case_id, gold, a, b, reason, title_a=None, title_b=None):
    CASES.append({
        "id": case_id,
        "gold": gold,
        "a": {"title": title_a or "Will the specified event happen?", "rules": a},
        "b": {"title": title_b or title_a or "Will the specified event happen?", "rules": b},
        "reason": reason,
    })


BTC = "YES pays $1 iff Feed A's BTC/USD observation at 2026-12-31 16:00 UTC is strictly above $100,000; NO pays the remainder of $1. If the observation is unavailable, both sides pay $0.50."
CPI = "YES pays $1 iff the first BLS release of headline US CPI year-over-year for November 2026 is strictly above 3%; NO pays the remainder of $1. If the release never occurs, both sides pay $0.50."
GAME = "YES pays $1 iff the Boston Celtics win the NBA game against the New York Knicks scheduled for 2026-12-01, including overtime; NO pays the remainder of $1. If canceled or not completed on that date, both sides pay $0.50. The NBA official final result is authoritative."
ELECTION = "YES pays $1 iff the certified result names Alice Smith the winner of the 2028 US presidential election; NO pays the remainder of $1. If no certified result exists by 2029-01-20 12:00 UTC, both sides pay $0.50."

add("identical_rules", "equivalent", BTC, BTC, "Identical complete simplified rules.", "BTC above $100k at year-end?", "Bitcoin > $100,000 on Dec 31?")
add("paraphrased_rules", "equivalent", BTC,
    "Use the BTC/USD value from Feed A at 16:00 UTC on December 31, 2026. YES receives one dollar if that value exceeds 100000 USD, otherwise zero. NO receives one dollar minus the YES payout. If the value is unavailable, each side receives fifty cents.",
    "Paraphrase preserves source, instant, comparison, and fallback.", "BTC above $100k?", "Bitcoin exceeds 100,000 USD?")
add("timezone_conversion", "equivalent", BTC, BTC.replace("2026-12-31 16:00 UTC", "2026-12-31 08:00 UTC-08:00"), "Two representations of the same instant.", "BTC at 4pm UTC?", "BTC at 8am UTC-8?")
add("percentage_units", "equivalent", CPI, CPI.replace("strictly above 3%", "strictly above 0.03 when represented as a fraction"), "3 percent equals a fraction of 0.03.", "CPI above 3%?", "CPI fraction above 0.03?")
add("participant_alias", "equivalent", GAME, GAME.replace("Boston Celtics", "Boston Celtics (BOS)").replace("New York Knicks", "New York Knicks (NYK)"), "Explicit aliases leave game and settlement unchanged.", "Celtics beat Knicks?", "BOS beats NYK?")
add("price_threshold_units", "equivalent", BTC, BTC.replace("$100,000", "100 thousand US dollars"), "Same threshold expressed with a multiplier.", "BTC above $100k?", "Bitcoin above 100 thousand dollars?")
add("opposite_sides", "complementary", BTC, BTC.replace("strictly above", "less than or equal to"), "YES of one maps to NO of the other, including the symmetric $0.50 fallback.", "BTC > $100k?", "BTC <= $100k?")
add("election_negation", "complementary", ELECTION, ELECTION.replace("names Alice Smith the winner", "names someone other than Alice Smith the winner"), "Certified winners partition the outcome; fallback is identical.", "Alice Smith wins?", "Someone other than Alice Smith wins?")
add("strict_boundary", "related", BTC, BTC.replace("strictly above", "greater than or equal to"), "At exactly $100,000, YES payouts differ.", "BTC above $100k?", "BTC at least $100k?")
add("different_threshold", "related", BTC, BTC.replace("$100,000", "$110,000"), "Different thresholds.", "BTC above $100k?", "BTC above $110k?")
add("different_instant", "related", BTC, BTC.replace("16:00 UTC", "17:00 UTC"), "Different observation times.", "BTC above $100k?", "BTC above $100k?")
add("different_source", "related", BTC, BTC.replace("Feed A", "Feed B"), "Two price feeds can disagree.", "BTC above $100k?", "BTC above $100k?")
add("spot_vs_average", "related", BTC, BTC.replace("observation at 2026-12-31 16:00 UTC", "arithmetic mean of its 60 one-minute observations over [2026-12-31 15:00 UTC, 2026-12-31 16:00 UTC)"), "A spot value and an hourly mean can straddle the strike.", "BTC above $100k?", "BTC above $100k?")
add("first_vs_revised", "related", CPI, CPI.replace("the first BLS release", "the last BLS revision available on 2027-02-01"), "Different data vintages.", "November inflation above 3%?", "November inflation above 3%?")
add("headline_vs_core", "related", CPI, CPI.replace("headline US CPI", "core US CPI excluding food and energy"), "Different statistical series.", "Inflation above 3%?", "Inflation above 3%?")
add("annual_vs_monthly", "related", CPI, CPI.replace("year-over-year", "month-over-month"), "Different measurement periods.", "CPI above 3%?", "CPI above 3%?")
add("projection_vs_certification", "related", ELECTION, ELECTION.replace("certified result", "Associated Press projection"), "Projection can differ from certification.", "Alice Smith wins election?", "Alice Smith wins election?")
add("winning_vs_taking_office", "related", ELECTION,
    "YES pays $1 iff Alice Smith is sworn in as US president by 2029-01-20 12:00 UTC, as recorded by Congress; otherwise YES pays $0 and NO pays $1. If Congress never supplies a record, both sides pay $0.50.",
    "Winning certification and being sworn in are different predicates.", "Alice Smith wins?", "Alice Smith becomes president?")
add("overtime_exclusion", "related", GAME, GAME.replace("including overtime", "based only on the score at the end of regulation; a tied regulation score pays YES $0"), "Regulation winner and full-game winner can differ.", "Celtics win?", "Celtics win?")
add("cancellation_fallback", "related", GAME, GAME.replace("both sides pay $0.50", "YES pays $0 and NO pays $1"), "Cancellation produces different payouts.", "Celtics win?", "Celtics win?")
add("missing_source", "insufficient", BTC,
    "YES pays $1 iff BTC/USD is strictly above $100,000 at 2026-12-31 16:00 UTC; NO pays the remainder of $1. The price source and unavailable-data payout have not been supplied.",
    "Missing material terms prevent equivalence verification.", "BTC above $100k?", "BTC above $100k?")
add("missing_overtime", "insufficient", GAME,
    "YES pays $1 iff Boston Celtics beat New York Knicks in the NBA game scheduled for 2026-12-01. Overtime, postponement, cancellation and source rules were not supplied.",
    "Game identity alone is insufficient.", "Celtics win?", "Celtics win?")
add("different_topic", "unrelated", BTC,
    "YES pays $1 iff NOAA reports New York City's maximum temperature strictly above 90 degrees Fahrenheit on 2026-12-31; NO pays the remainder of $1. Missing data pays each side $0.50.",
    "Different entities and underlying event.", "Bitcoin above $100k?", "NYC above 90F?")
add("different_competition", "unrelated", GAME,
    "YES pays $1 iff Arsenal beat Chelsea in their English Premier League game scheduled for 2026-12-01, according to the official league result; NO pays the remainder of $1. Cancellation pays each side $0.50.",
    "Distinct sport, competition, participants, and event.", "Celtics beat Knicks?", "Arsenal beat Chelsea?")
