# trade_env

An RL environment in which a language model trades one in-play soccer market: the 1X2 result at the end of regulation time, priced by Polymarket YES contracts.

The core is Python 3.11+ with Jinja2 templates and a PyArrow release reader and knows nothing about any trainer. A trainer reads `tasks.jsonl.gz`, sends `messages` to the model and calls `score`.

## The shape

- One task is one decision: a match, a decision point and a held position (`flat`, `home`, `draw` or `away`). All four positions are precomputed. During training, the preceding action selects which position variant is used next.
- A full-match replay walks the decision points in order and carries the position the policy chose. The trainer uses the same sequence for whole-match episodes; replay also supports evaluation and demos. The step P&Ls add up to the match P&L.
- The size is always 1 share.

## The rules

Every number below is a default in `config.py`.

**Input.** AK's frozen release at `data/raw/release-data-2026-10-13-v1/` is the default. `dataset.py` reads enriched `fifa-markets` matches/events and `fifa-market-history` prices, selecting only the verified regulation home/draw/away YES tokens. The history's market-metadata table supplies fee schedules. There is no data download or provider join command in this package. The small JSON/JSONL reader in `tasks.py` supports offline fixtures through `--records`.

**Templates.** All wording lives in `templates/system.j2` and `templates/user.j2`. `prompt.py` renders them with Jinja `StrictUndefined`: missing variables raise an error. Number formatting is implemented as Python filters. The migration rebuilt all 9,448 default tasks byte-identically; see [the comparison report](../../artifacts/trade-env-migration/REPORT.md).

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
- Default fees remain `fee = 0.05 * p * (1 - p)`. `--set fee_rate=market` uses each outcome contract's recorded release rate instead (0, 0.03, or 0.05 in this release). Disabled fees mean zero; missing or unsupported schedules fail. The release does not provide a history of fee changes. For a mid `p`: A buy costs `max(p, min(0.999, p + 0.01)) + fee`, at most 1. A sell returns `min(p, max(0.001, p - 0.01)) - fee`, at least 0. Settlement pays 1 or 0 with no fee.
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
uv sync --locked
# Extract AK's release under data/raw/release-data-2026-10-13-v1/ first.
uv run --locked python -m app.trade_env build-tasks          # release -> tasks.jsonl.gz + tasks.meta.json
uv run --locked python -m app.trade_env show 400021440:h2+15:home [--reveal]
uv run --locked python -m app.trade_env baselines            # per-split table, whole-match bootstrap CIs
uv run --locked python -m app.trade_env baselines --sensitivity --release data/raw/release-data-2026-10-13-v1/
uv run --locked python -m app.trade_env stats                # prompt sizes, single-step reward table
uv run --locked --extra train python -m unittest discover -s tests/trade_env -v
```

Files live in `data/processed/trade_env/` (ignored by Git). Any default can be changed with `--set KEY=VALUE`; a value must have the type of its default (`true` and `false` in any case, JSON for lists). Settings that shape tasks need a rebuild. The config used is written next to the task file: `tasks.jsonl.gz` gets `tasks.meta.json`, and `tasks.spread03.jsonl.gz` gets `tasks.spread03.meta.json`.

The same records and config give the same task bytes on Python 3.11 and 3.12.

## Code review follow-ups

- [ ] Test an optional `pre` decision against the current in-game-only schedule. Assess whether the pre-match observation gives the model enough information to choose an initial position. With `pre_lead_s=60` and `fill_delay_s=60`, execution occurs at kickoff or later; choose timing explicitly if the experiment requires a pre-kickoff fill. Evaluate position carry-forward in full-match replay, as in trajectory training. Keep the current defaults until this experiment is reviewed.

- [ ] Compare single-decision training with whole-match returns before claiming a sequentially optimal policy.
- [ ] Replace future-fill-based checkpoint exclusion with explicit failed-execution behavior in a separate experiment; quantify the selection bias.
- [ ] Capture event receipt timestamps and revisions to evaluate historically available information.

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
| `dataset.py` | offline reader for AK's frozen release |
| `templates/*.j2` | strict system and user prompt wording |
| `train_tinker.py` | Tinker adapter, launcher, estimates and replay evaluation |

## Tinker training

Install the trainer with `uv sync --locked --extra train` (Python 3.12). Cache the tokenizer once with `uv run --locked --extra train hf download Qwen/Qwen3-8B config.json tokenizer.json tokenizer_config.json vocab.json merges.txt`. Trainer tests fail if dependencies or that tokenizer are missing; they never silently skip. Set `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1` to run tests without network.

The three trainer commands are:

```sh
uv run --locked --extra train python -m app.trade_env.train_tinker                    # estimate only
uv run --locked --extra train python -m app.trade_env.train_tinker dry_run=true       # two fake-service steps; no billing
uv run --env-file .env --locked --extra train python -m app.trade_env.train_tinker confirm=true  # real training; billed
```

Put `TINKER_API_KEY=your_key` in the gitignored root `.env`. The real-training command above loads it through uv; the launcher does not load `.env` automatically. Keep the key out of commands, logs, and commits.

Defaults: Qwen3-8B, non-thinking renderer, rank-32 LoRA, eight matches per batch, eight whole-match rollouts per match, one epoch, learning rate 1e-5, 256 completion tokens per decision. Every group stays in the batch, so metrics cover every rollout. The constant-group filtering option was removed; tied groups have zero policy advantage. `reward_scale=1.0` leaves reward in dollars per share; uniform scaling does not set Adam's update size (epsilon, clipping and regularization can break exact scale invariance). Each trajectory starts flat at the first usable checkpoint. Later positions come from its own actions; position variants are never sampled independently for training.

Validation task metrics use `val/`; position-carrying match metrics use `replay/val/`. Evaluation runs before scheduled steps and once on final weights. A rerun of completed weights does not repeat that final evaluation. Replay advances one decision at a time; it never resamples an earlier decision. Bare action words and malformed/two-stop-token completions are invalid just as in training.

The default run name fingerprints settings, task-file bytes, environment/trainer code, and the dependency lockfile. Repeating it resumes; changing settings starts another run. An explicit `name=` refuses mismatched settings or data. `max_steps` may be extended without changing identity. Dry runs use reserved `dry-` directories and reject symlinks; real runs cannot use that prefix. All `/runs/` output, including the cookbook's working-tree diff capture, is ignored by Git. Final evaluation exports rollout summaries through the same cookbook path as scheduled evaluation.

Numeric settings, split presence and task config hashes are checked before the estimate. Real training also checks credentials, renderer/tokenizer availability and known model pricing before creating any Tinker client. Unknown-price models are refused. Estimates use the dated price table in the launcher, not a live quote.

For nondefault task-build settings, explicitly provide the matching metadata, for example:

```sh
uv run --locked python -m app.trade_env build-tasks --set fee_rate=market --output data/processed/trade_env/tasks.market.jsonl.gz
uv run --locked --extra train python -m app.trade_env.train_tinker tasks_path=data/processed/trade_env/tasks.market.jsonl.gz config_path=data/processed/trade_env/tasks.market.meta.json
```

The trainer requires `mark=mid`, `start_mark=decision`, and `w_log=w_missed=0`, so summed P&L equals cash-flow profit. `invalid_penalty` remains configurable without rebuilding. The trainer refuses a task hash that disagrees with its expected config. Evaluation can be disabled with `eval_every=0`; otherwise the requested held-out split must be nonempty and differ from training.


## RL audit

See the [research and code review](../../artifacts/trade-env-migration/REPORT.md#rl-and-leakage-review--2026-10-13) for the GRPO objective, fixes, evidence, and remaining limits.

Training now uses whole-match trajectories: each GRPO group contains independent runs of the same match, starting flat. Step rewards sum without discounting; the total return minus the group's mean is assigned to every generated answer in that trajectory. Settlement is included in the final step only. Invalid or truncated answers hold the position, pay the penalty, and continue to the next checkpoint.

Each checkpoint receives a fresh observation and the carried position, not the previous response text or hidden rewards. The loss remains generated-token importance sampling with one update per fresh batch and no base-model KL penalty. `loss_fn=ppo` is the supported clipped alternative.

Future-value mutation tests protect the prompt boundary. They cannot remove retrospective corrections in the source feed or selection bias from requiring valid future fills. The trainer also rejects matches shared across splits and broken replay states before creating a service client.

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --locked --extra train python -m unittest discover -s tests/trade_env -q
uv run --locked python -m tests.trade_env.audit_release  # full local release required
```

Training `env/all/reward/total` is mean whole-match penalized return (scaled only if `reward_scale` changes). `env/all/pnl_per_match` and `cost_per_match` are unscaled dollars. Existing `pnl`, `valid`, and `truncated` metrics remain per-decision averages. `val/` is a single-decision diagnostic; `replay/val/pnl_per_match_c` measures the full-match policy and is the primary P&L evaluation. Estimates distinguish `train_matches`, `train_trajectories`, and `train_rollouts` (individual model calls). Old single-decision checkpoints cannot resume under this training mode.


## Forecast-shaped trajectory experiment

This is an opt-in experiment; the existing P&L-only task file and finished run are preserved. Build a separate task file:

```sh
uv run --locked python -m app.trade_env build-tasks --set forecast_rewards=true --output data/processed/trade_env/tasks.forecast.jsonl.gz
uv run --locked --extra train python -m app.trade_env.train_tinker tasks_path=data/processed/trade_env/tasks.forecast.jsonl.gz config_path=data/processed/trade_env/tasks.forecast.meta.json
```

The second command estimates only. `dry_run=true` exercises the fake service. Billed training still requires explicit `confirm=true` and `uv run --env-file .env`; no new paid run is part of this change. Forecast estimates assume 180 answer tokens rather than 80. The previous $12.33 measured estimate is not a quote for this larger prompt/schema.

Keep `probs`, `action`, and optional `reason`. Add `next_goal` (home/away/none probabilities summing to one) and `future_prices` (three expected YES prices in [0,1], not necessarily summing to one). Both forecast objects are required in this mode. The horizon is 300 wall-clock seconds **from the decision timestamp**, not the delayed event cutoff or next checkpoint. `probs` still predicts the regulation result. Goals before or exactly at the decision are not future targets; own goals are credited from score changes. If both scores first increase at one timestamp, goal order is unknown and that target is masked. A horizon past regulation end masks both short-horizon targets; missing/stale price observations mask price scoring. Forecast availability never drops a trading checkpoint. These are final-feed labels with the historical-correction limits described above.

For each match with N usable checkpoints, the training return is:

```
net_match_pnl - invalid_penalty * invalid_answers
+ (w_result / N) * sum(market_Brier - model_result_Brier)
+ (w_goal / N) * sum(2/3 - model_next_goal_Brier)       # available labels only
+ (w_price / N) * sum(persistence_MSE - model_price_MSE) # available labels only
```

Brier here is the sum of squared errors over three classes; price MSE is the mean across the three contracts. Result and goal Brier range [0,2], price MSE [0,1]. The goal reference is uniform, a fixed centering term—not a fitted football baseline. Predicting current prices earns zero price bonus. Baselines depend only on the task and labels, so they do not change ranking between answers to that task. Default `w_result=w_goal=w_price=0.1` are provisional coefficient choices, **not tuned weights or actual dollars**. They can be overridden independently at the trainer CLI; setting all three to zero removes shaping, but the enabled forecast schema still applies. Shaped reward is not P&L. Invalid forecasts hold the current position, incur the existing penalty, and receive worst-case forecast errors for available labels. Correct explanations alone earn nothing.

Training metrics include separate `forecast/result_reward`, `goal_reward`, `price_reward`, their per-match totals, result/market Brier, goal Brier, price/persistence MSE, and target availability. Masked losses are logged as zero with availability zero: divide aggregated goal/price loss by its availability fraction when reporting accuracy. P&L and fees remain independently logged, with no forecast bonuses in `replay/val/pnl_per_match_c`. Run fingerprints prevent old checkpoints from resuming under this changed objective.

### Evaluation handoff to AK

- Update the answer parser to accept and validate `next_goal` and `future_prices`; retain the same action execution, fees, delays, one-share sizing, settlement and match splits. Malformed forecasts are invalid in the new mode. Existing P&L-only models need the original schema.
- **Profit remains the headline:** whole-match net P&L versus always-flat, gross P&L, fees, turnover, drawdown, and match-bootstrap confidence intervals. Never report shaped return as profit. Use repeated sampling seeds and paired match comparisons against the original checkpoint.
- Report regulation Brier/log loss versus normalized market prices; next-goal Brier/log loss versus a training-only fitted time/score baseline; five-minute price MSE versus unchanged prices. Include target coverage and keep masked labels out of accuracy denominators.
- Test full-state, prices-only, and game-only inputs on the same held-out matches, plus internally consistent score/red-card counterfactuals with prices fixed. Forecast accuracy alone does not establish game-state use or causal understanding. These ablations are an evaluation requirement, not implemented paid experiments here.
- Save per-match and per-decision results. Our replay evaluator now puts a `match_replays` JSON payload in each evaluation's logtree JSON/HTML, including P&L, costs, actions, probabilities and forecast scores. This supports paired uncertainty and trade audits; decode the JSON text entry in `eval_1_logtree.json` when both evaluators are enabled.
- Select coefficients/checkpoints using validation only. Keep the 32-match test split untouched until the final comparison. Retain both P&L-only and shaped-reward runs as separate experiments.


### Football-rules prompt comparison

`football_rules=true` includes `templates/football_rules.j2` in the system prompt. Default false preserves the control prompt. This concise reference covers clock/score, goals, player numbers, cards, penalties and substitutions; it is not the full rulebook or a betting strategy. The common rules were checked against IFAB [Law 3](https://www.theifab.com/laws/latest/the-players/), [Law 7](https://www.theifab.com/laws/latest/the-duration-of-the-match/), [Law 10](https://www.theifab.com/laws/latest/determining-the-outcome-of-a-match/) and [Law 12](https://www.theifab.com/laws/latest/fouls-and-misconduct/) on 2026-10-13. No season-specific substitution quota or new timing protocol is assumed.

```sh
uv run --locked python -m app.trade_env build-tasks --set forecast_rewards=true --set football_rules=true --output data/processed/trade_env/tasks.forecast.rules.jsonl.gz
```

Use `tasks_path=data/processed/trade_env/tasks.forecast.rules.jsonl.gz config_path=data/processed/trade_env/tasks.forecast.rules.meta.json` for this variant. Compare with the corrected no-rules forecast prompts, not the stopped run's split-JSON prompt. Hold checkpoint, tasks, sampling settings and seeds fixed for an inference comparison before considering two training runs. Inspect factual correctness and support in the visible state of each rationale, as well as forecasts, actions, net P&L and validity; merely mentioning more football terms is not evidence of understanding. Labels, rewards and user-state messages are identical across variants, except the config fingerprint. Added input tokens increase cost. No paired model comparison has run yet.
