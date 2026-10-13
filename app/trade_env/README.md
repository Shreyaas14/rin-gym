# trade_env

An RL environment in which a language model trades one in-play soccer market: the 1X2 result at the end of regulation time, priced by Polymarket YES contracts.

The core is plain Python (standard library only, 3.11 or newer) and knows nothing about any trainer. A trainer reads `tasks.jsonl.gz`, sends `messages` to the model and calls `score`.

## The shape

- One task is one decision: a match, a decision point and a held position (`flat`, `home`, `draw` or `away`). The position is given, so every task is a single prompt, a single completion and one number back.
- A full-match replay walks the decision points in order and carries the position the policy chose. It is for evaluation and demos. The step P&Ls add up to the match P&L.
- The size is always 1 share.

## The rules

Every number below is a default in `config.py`.

**Clock.** Five decision points per match, in epoch seconds: `h1+15` and `h1+30` (15 and 30 minutes after the first-half kickoff), `ht` (the first-half whistle), `h2+15` and `h2+30`. `pre` (60 s before kickoff) can be added. A point at or after the end of its half is left out.

**What the model sees.**

- Events are shown only up to 60 s before the decision (`event_delay_s`).
- Each price shown is the last sample at or before the decision. If any of the three is older than 90 s the point is dropped (`stale_price`). If the three differ from a sum of 1 by more than 0.05 it is dropped (`price_sum`); a sum exactly 0.05 away is kept.
- Prices are printed to four decimals, because quotes near 0 and 1 move in steps of 0.0005.
- The price table lists the kickoff quote and the quote at each earlier decision point, oldest first. A row reads `n/a` when its quote fails the two tests above.
- `shots` counts every attempt on goal. The World Cup feed has a shot row for each one. The Premier League feed has a shot row only for a miss, so there a goal and an opponent's save or block count too (`split_shot_competitions`).
- The score is the highest score seen on any row so far, because single rows can be stale. A goal is credited to the side whose score rose, because an own-goal row names the conceding team.
- `regulation`, `extra_time`, later period times and anything after the cutoffs never reach a prompt. `scoring` is never rendered.

**Trading.**

- An order fills at the first price sample at least 60 s after the decision (`fill_delay_s`). With none within a further 120 s the point is dropped (`no_fill`). If the three fill prices differ from a sum of 1 by more than 0.05 the point is dropped (`fill_sum`).
- Prices are sampled about once a minute, so `fill_delay_s` is a floor, not the lag. Measured on all 484 matches, the fill lands a median 32 s after the decision at `fill_delay_s=0`, 93 s at 60 and 154 s at 120 (maximum 79, 144 and 213 s). The system prompt says "at least 60 seconds after you decide, usually 60 to 120 seconds later".
- For a mid `p`: `fee = 0.05 * p * (1 - p)`. A buy costs `max(p, min(0.999, p + 0.01)) + fee`, at most 1. A sell returns `min(p, max(0.001, p - 0.01)) - fee`, at least 0. Settlement pays 1 or 0 with no fee.
- Flat, the legal actions are `buy_home`, `buy_draw`, `buy_away` and `do_nothing`. Holding X they are `keep`, `close` and `switch_<Y>` for the other two outcomes.

**Reward.** A chunk runs from a decision to the next usable decision; the last one runs to the end of regulation. Wealth is cash plus the position at the mid, so costs are charged only when a trade happens. With `s` the mid shown at the decision, `f` the fill mid and `e` the mid at the chunk end (or the 1/0 settlement):

| position | action | reward |
| --- | --- | --- |
| flat | `do_nothing` | 0 |
| flat | `buy_X` | `e(X) - buy_cost(f(X))` |
| X | `keep` | `e(X) - s(X)` |
| X | `close` | `sell_proceeds(f(X)) - s(X)` |
| X | `switch_Y` | `close` + `e(Y) - buy_cost(f(Y))` |

An answer that cannot be parsed, names an illegal action or gives bad probabilities holds still and scores that reward minus 0.04.

All action rewards are worked out when the tasks are built and stored in the hidden `scoring` block, so scoring is a lookup.

Two terms are logged with every score and have weight 0: `log_score_edge` (log of the model's probability of the result minus the market's) and `missed` (best legal reward minus the chosen one). `reward = pnl + w_log * log_score_edge - w_missed * missed`.

**Splits, by whole match.** Premier League 2025/26 is train. World Cup 2026 group games are validation. World Cup knockouts are test. The three demo matches are flagged `demo` and are always test. A World Cup record without `stage_name` is placed by its date.

## Deviations from design

The first three were choices made while building. The rest came out of review, and each has a test.

- **Held positions are marked at the decision-time mid, not the fill mid.** The design note writes `keep = e(X) - m0(X)` with `m0` the fill mid, but its chunk-end mark is the mid at the next decision. Those two do not chain: the price move between a decision and its fill would be counted nowhere, and step P&Ls would not sum to cash-flow P&L. Marking at `s` fixes that. The gap between any two actions in a task is the same either way. `--set start_mark=fill` gives the literal formulas, where `close` is exactly minus the exit cost.
- **A chunk skips dropped points.** If the next decision point is dropped for bad prices, the chunk runs on to the next usable one, so a replay never has a gap and the chunk-end mark is always a fresh, coherent quote.
- **The price table has a kickoff row.** It is the only sign of pre-match team strength at the first decision. `--set show_kickoff_price=false` removes it.
- **Fill prices must sum to about 1.** The design note tests only the shown prices. On the real feed one leg is sometimes quoted off an empty book for a few minutes: in Manchester United v Burnley the away price reads 0.09, then 0.49 for three samples, then 0.105, with no goal. Filling there paid `close` +35c and charged `buy_away` 48c for a price nobody could trade. Such points are now dropped as `fill_sum`: 25 points (100 tasks) under the defaults.
- **Earlier rows of the price table pass the same tests as the current price.** The design note asks only for the history up to now. A stale or incoherent earlier quote, including the quote of a dropped decision point, now prints `n/a` instead of a number that looks like a price move. Rows are in time order, so `pre-match` comes before `kickoff`.
- **Costs never go through the mid.** The design formulas are `min(0.999, p + spread) + fee` and `max(0.001, p - spread) - fee`. At a mid of 0.9995 the first is a buy below the mid, and at 0.0005 the second is a sell above it, so a trade had a negative cost (94 tasks). With `half_spread=0.05` a sell could return less than 0 and a buy cost more than 1. The quote is now held at the mid in those tails and cash per share stays between 0 and 1. Nothing changes for mids between 0.001 and 0.999 under the defaults.
- **`shots` follows each feed's convention.** The design note counts `shot_attempt` rows. The Premier League feed logs those only for misses, so its count was 9.3 a match against 24.0 for the World Cup, and 53 team lines showed more goals than shots. Counting goals and the opponent's saves and blocks for that feed gives 24.8 against 24.0.
- **A think tag inside the JSON answer is text.** The design note says to strip `<think>...</think>` and read the last object. Stripping first cut a valid answer whose `reason` mentioned a tag. The parser now finds the JSON objects first and ignores tags inside them. Tags match in any case. Text after a `<think>` that is never closed is still treated as unfinished reasoning, so `I will not use <think> tags. {...}` scores as invalid: it cannot be told from a draft in reasoning that was cut off.

`mark=liquidation` values an open position at sell proceeds at each chunk end. It is conservative, does not sum to match P&L and is for sensitivity runs only.

## Run it

```sh
uv venv --python 3.12 .venv
.venv/bin/python -m app.trade_env build-records        # data side; needs network
.venv/bin/python -m app.trade_env build-tasks          # records -> tasks.jsonl.gz + tasks.meta.json
.venv/bin/python -m app.trade_env show 400021440:h2+15:home [--reveal]
.venv/bin/python -m app.trade_env baselines            # per-split table, whole-match bootstrap CIs
.venv/bin/python -m app.trade_env baselines --sensitivity --records data/processed/trade_env/records.jsonl.gz
.venv/bin/python -m app.trade_env stats                # prompt sizes, single-step reward table
.venv/bin/python -m unittest discover -s tests/trade_env -v
```

Files live in `data/processed/trade_env/` (ignored by Git). Any default can be changed with `--set KEY=VALUE`; a value must have the type of its default (`true` and `false` in any case, JSON for lists). Settings that shape tasks need a rebuild. The config used is written next to the task file: `tasks.jsonl.gz` gets `tasks.meta.json`, and `tasks.spread03.jsonl.gz` gets `tasks.spread03.meta.json`.

The same records and config give the same task bytes on Python 3.11 and 3.12.

## Known limits

- **The event feed is the final, corrected one.** A decision that was later overturned has no row, and the Premier League feed has no VAR rows at all. A prompt can therefore show a price jump with nothing to explain it, which tells the reader it will be undone. A search for a team price up 0.10 or more on five minutes earlier, back within 0.04 six minutes later, with no goal, red card or penalty row nearby, finds 2 of 2,362 decision points (Newcastle United v Arsenal `h1+15` and Bournemouth v Everton `ht`) and 47 of 41,788 in-play minutes. This needs a feed captured live to fix; nothing is filtered.
- **`n/a` rows appear only in training prompts.** 452 of 7,372 Premier League tasks have at least one, 224 of them the kickoff row (12 matches). No World Cup task has one.
- **The parser scans every `{`.** A normal completion takes under a millisecond. A completion of tens of thousands of nested open braces takes seconds.

## Use it from a trainer

```python
from app.trade_env.config import make_config
from app.trade_env.tasks import read_tasks
from app.trade_env.reward import score
from app.trade_env.replay import by_match, replay_match

config = make_config()
tasks = read_tasks("data/processed/trade_env/tasks.jsonl.gz")
out = score(tasks[0], completion_text, config)   # out["reward"], out["valid"], out["log_score_edge"], ...

# evaluation: policy(task) returns a completion (or a legal action); pass only task["messages"] to a model
for match in by_match(tasks).values():
    result = replay_match(match, lambda task: sample(task["messages"]), config)
```

The model must end its answer with

```json
{"probs": {"home": 0.5, "draw": 0.3, "away": 0.2}, "action": "buy_home", "reason": "one sentence"}
```

The answer is the last JSON object after the model's reasoning: everything up to the last `</think>` is reasoning, and so is everything after a `<think>` that is never closed. Probabilities must be three numbers in [0, 1] summing to 1 within 0.05; they are renormalised. `score` never raises on a completion: anything it cannot read is an invalid answer.

## Files

| file | holds |
| --- | --- |
| `config.py` | defaults, `--set` parsing, `config_hash` |
| `clock.py` | decision points |
| `costs.py` | fee, buy cost, sell proceeds |
| `state.py` | as-of prices, delayed events, score, counts |
| `prompt.py` | system and user messages |
| `parse.py` | answer parser |
| `reward.py` | legal actions, per-action rewards, `score` |
| `tasks.py` | records to tasks, drop accounting, read and write |
| `splits.py` | `split_for` |
| `replay.py` | full-match replay |
| `baselines.py` | no-model policies, sensitivity grid |
| `stats.py` | bootstrap, single-step table, prompt sizes |
| `records.py`, `build_records.py` | the match record format and its builder (data side) |
