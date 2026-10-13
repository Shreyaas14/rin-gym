# Parlay and combo experiments using the existing evaluation data

We can build this as a small experiment adapter and offline scorer without changing the core harness or acquiring data. Start with two-leg cross-match result forecasts, then test direct same-game joint forecasts. Real parlay profitability is not identifiable from the existing single-contract prices: any parlay payout schedule must be explicitly hypothetical. A portfolio of separate singles is executable within the current simulator assumptions and is a useful separate control.

## Context from the evals chat

Reviewed “Research eval harness options” (`01a132ae-0f6e-76f2-9c78-e9a7c258e422`). Its latest authorized work is a fresh, paired, position-conditioned replay of original and conservative policies on 72 historical development games. Earlier work separated forecast quality from trading, audited halftime labels, and ran a forecasting-only pilot. The pilot did not demonstrate a convincing improvement; the existing validation matches have already been inspected and remain development data.

This investigation does not spend the other chat's inference budget or modify its active files. No new inference, training, download, protected-cohort enrollment or evaluation was performed here. The ledger integrity/exclusion guard was unchanged before and after the audit; its state is `pending_enrollment`, with zero enrolled protected matches. “Keep the holdout sealed” currently means preserving that state and excluding the existing test/demo games, not claiming an enrolled independent holdout exists.

## What is already available

| Resource | Verified coverage | Use |
|---|---|---|
| Earlier run reconstructed tasks | 72 validation games, 359 unique decision points | Result labels, next-goal labels, displayed prices, delayed fills, safe messages |
| Run 3 repeated validation | 216 game replays, 1,077 valid saved responses with result probabilities | Free composition baseline; three repeats are repeated measurements of the same games |
| Forecast-only pilot | 16 validation games, 80 responses each for base and final | Paired comparison with forecasts generated from flat observations |
| Frozen release | Development schedule, teams, event prefixes, regulation scores and single-contract histories | Common-cutoff reconstruction and new derived event labels without ingestion |
| Existing forecast simulator | Poisson score continuations | Optional model-based baseline for result/total-goals/BTTS joints; not ground-truth simulation |

The pilot has 21 invalid base responses and 19 invalid final responses out of 80 each. Preserve those failures in coverage and failure-inclusive metrics rather than silently dropping them. The old run's saved probabilities were conditioned on its original positions; composition tests those saved signals, not how the model would react to a parlay position. Use the first flat checkpoint for the cleanest old-run comparison, or the pilot's flat forecasts across its fixed checkpoints.

The frozen release projection already exposes regulation home/away scores. This permits derived labels for both teams to score (BTTS), over/under totals and winner-plus-total combinations. It does not give the model corresponding probabilities or supply prices for those contracts.

## Experiments in order

### 1. Offline composition, with no model calls

Freeze a seeded list of distinct-match pairs from validation only, independent of outcomes and forecast errors. Exclude shared-team pairs for the main independence baseline. At a predeclared phase (start with `h1+15`), take each match's result vector and form its nine-cell joint table:

`P(A=i, B=j) = P(A=i) * P(B=j)`

The nine cells sum to one; the observed joint label is the pair of regulation results. Evaluate the full table using multiclass Brier and log loss, then evaluate selected AND tickets with binary Brier, calibration and hit rate. Report the independence assumption explicitly; distinct matches can still share tournament incentives or latent conditions. Preserve a sensitivity slice including shared-team fixtures.

Compare model product versus normalized-market product on exactly the same pairs and source checkpoints. Use raw prices only for pricing scenarios, not normalized probabilities as executable quotes. For the pilot, compare base product and final product on the same cases. For the older run, pair the same repeat index across legs and average repeated scores at the fixture-pair level; do not count three repeats as three independent samples.

There are 2,556 distinct match pairs, of which 2,412 have no shared team. Combining every phase produces 12,709 pair-checkpoint rows; this is extensive reuse of 72 games, not 12,709 independent examples. Use the full enumeration as a descriptive diagnostic, or a small frozen sample for review. Avoid a giant post-hoc search for apparently winning combinations.

This answers whether the model's existing marginal forecasts compose well. It does not test new model reasoning about combos.

### 2. Fresh direct joint forecasting, with the same checkpoint

Create a new experiment-local prompt by taking only each task's safe `messages`, stripping single-match trading instructions and presenting labeled observations A and B. Do not serialize the task's `scoring` object. Reuse the Tinker sampling client, renderer, checkpoint, output receipts and cost preflight already present in `experiments/forecast_only/pilot.py` or `closed_loop.py`.

Ask for a normalized nine-cell result joint distribution. Use a separate strict parser in the experiment; the existing three-outcome parser cannot accept this response. Derive all candidate ticket probabilities and marginal vectors from the same table, avoiding separate inconsistent forecasts for each ticket.

Compare direct joint output against (a) the product of the same checkpoint's single-match forecasts, and (b) market products. A matched fresh single-match arm at the same temperature and observation cutoffs is needed to isolate the effect of the joint prompt; old saved forecasts are an exploratory reference. Also inspect direct-joint marginals versus standalone marginals. A joint prompt may change the marginals without learning useful dependence.

Use fixed candidates or a frozen deterministic selector first. A separate later selector prompt can return a ticket ID or `abstain`, with the joint table supplying its probability. That cleanly distinguishes forecasting from ticket selection. New outputs should be labeled retrospective development, not prospective receipts under the existing three-outcome enrollment contract.

### 3. Same-game combos

The least expensive new target is **regulation result AND next scoring side in the next 300 wall-clock seconds**. The old tasks already carry both labels; ask for a nine-cell joint over `home/draw/away` result and `home/away/none` next goal. Score the table and individual combo events. Do not multiply the existing result and next-goal vectors: the events are dependent. A result-only pilot checkpoint needs fresh output for this target.

Preserve the horizon and masking rules in `app/trade_env/forecast.py`: exclude censored horizons from this joint cohort rather than assigning a false `none` label. The halftime audit showed a real prompt/target mismatch, not incorrect no-goal labels. Begin with active-play phases; keep halftime as a separately reported slice so trivial no-goal windows cannot dominate.

A later variant can derive result × total-goals or result × BTTS labels from regulation scores, using a new local table schema and fresh forecasts. All final-score legs settle on regulation plus stoppage time, excluding extra time and penalties. Optional Poisson simulation can supply a coherent joint baseline by counting both events on the same sampled continuation; validate sampling error and use training-frozen parameters. Existing simulation outputs alone cannot establish model calibration.

Include exact logical controls: `home AND away` in the same match has probability zero; `home OR draw` equals the sum of mutually exclusive marginals. Such controls test coherence but should not inflate the headline combo-performance score.

## Timing: broad composition versus a historical slate

Matching two games at minute 15 on different days is a valid synthetic composition task, but does not describe one historical decision at which both quotes were available. The audit found 48 singleton scheduled kickoff groups and 12 two-game groups; there are no scheduled three-game groups in this development cohort. At `h1+15` and `h1+30`, 12 pairs have task times within 60 seconds. At `h2+15`, only five do. None match exactly.

For a stronger two-game slate experiment, freeze an actual UTC decision time per simultaneous fixture pair and rebuild both prefix observations from the frozen release. Reuse the existing prefix state builder and backward price joins, retaining event delay, quote freshness and recorded fill delays. Make sure both games are unresolved, each model prediction is available by the chosen order time, and the relevant contracts have eligible prices. Do not use later-match forecasts in an earlier slate. A stale saved prediction is usable only under a separately specified forecast-age rule; the existing 90-second quote-age limit is not automatically a forecast-age rule.

The 12 kickoff pairs are candidates, not guaranteed usable common-cutoff cases. Missing source coverage may reduce them further. Three-leg evaluations can still be synthetic retrospective tasks; realistic asynchronous three-game tickets need pregame or previously issued forecasts for every leg, which the saved in-play rows do not necessarily provide.

## Pricing and risk

For a selected AND event with probability `q` and declared decimal return `D` including stake, hypothetical expected profit per dollar staked is `q*D - 1`; realized profit is `D*y - 1`, where `y` is one only when every leg wins. Use fixed, predeclared pricing scenarios and payout haircuts. Product-of-leg implied odds is an independence pricing baseline, not a historical sportsbook or Polymarket parlay quote. Multiplying fee-adjusted single-contract costs does not establish an executable combo price or the fee schedule of a joint contract.

Report probability scores as the primary result. Any synthetic P&L table must state its payout assumptions prominently. For a comparable risk experiment use equal total stake across singles, two-leg and three-leg tickets, a fixed slate exposure cap, abstention, no re-entry and a maximum ticket payout. Do not give every overlapping combination its own full stake. The 65-cent single-contract cap and 5-cent marginal edge from the conservative policy do not transfer automatically to a parlay; define ticket rules separately before scoring outcomes.

As an executable-simulator control, buy a basket of separate existing contracts and sum the existing per-leg delayed-fill, fee and settlement P&Ls, subject to a shared cash budget. This pays on each successful leg separately; it cannot replicate a contract that pays only when all legs succeed. Keep basket and parlay results labeled separately.

## Minimal implementation boundary

Proposed new files under `experiments/parlays/`:

- `prepare.py`: filter train/validation membership, guard all underlying match IDs, freeze leg selection, common cutoffs, source hashes and cohort metadata.
- `forecast.py`: compose cached forecasts; optionally issue fresh joint calls with experiment-local prompts, strict validation and inference receipts.
- `score.py`: derive joint labels, calculate proper scores, simulate declared payouts or singles baskets, and write per-ticket/per-slate results.
- `protocol.json` and a report in a new artifact directory: leg count, event definitions, invalid-output handling, selector, payout assumptions, budget and uncertainty method fixed before inference.

Reuse `read_tasks`, `forecast.brier`, `core.guard`, `core.digest`, release snapshots, cost functions and Tinker client code. The one-match `Replay` class holds exactly one of `flat/home/draw/away`; leave it intact. No new actions, training reward or multi-position trading environment are needed for these first experiments. The existing governance target is hard-coded to regulation H/D/A: use its exclusion guard for all component IDs and independent experiment receipts rather than pretending the existing scorer/enrollment schema supports a nine-cell target.

## Metrics and uncertainty

Report joint Brier and log loss against matched baselines; ticket binary Brier and calibration by leg count; validity/coverage; abstention and ticket count; realized hit rate; and hypothetical return, losses and drawdown only for declared payout scenarios. Predeclare probability clipping for log loss and a worst-case score for invalid responses. Include both an all-case penalty-inclusive score and a clearly labeled valid-case diagnostic on common coverage.

Do not bootstrap overlapping tickets as independent rows. For non-overlapping simultaneous slates, average phases/repeats within each slate and resample slates or matchdays; only 12 candidate slates means weak inference. For all-pairs diagnostics, uncertainty needs resampling underlying matches or an appropriate multi-membership clustering method. The existing single-match bootstrap cannot be applied unchanged to pair rows. A simpler first result is descriptive all-pairs metrics plus uncertainty from a separately frozen set of disjoint pairs. Shared-team dependence remains a limitation even with disjoint fixture IDs.

Three-leg exact joint output has 27 cells and can exceed the old response token cap. Defer it until the two-leg parser and budget preflight work. Do not tune payout haircuts, selection thresholds or checkpoint choice against the same report and call it validation.

## Recommended first run

1. Free offline two-leg composition at `h1+15`: older-run product versus market product on the frozen pairs, plus paired base/final/market results on the 16-game pilot. Explicitly include invalid pilot outputs.
2. Prepare common-cutoff observations for the 12 candidate simultaneous pairs, inspect exclusions, and freeze a small fresh direct-joint protocol with matched single-match calls. Calculate its own inference budget rather than borrowing the active replay's cap.
3. Fresh same-game result × next-goal forecasting on development active-play observations, using existing labels and the same checkpoint. Include independence products only as a diagnostic baseline.

The investigation produced feasibility evidence and an implementation design, not a measured parlay model result. The first run is fully offline; subsequent fresh-output tests incur inference costs but require neither new source data nor a core harness rewrite.

## Reproduction of this investigation

From `/Users/alexkan/Desktop/frontiers`:

```sh
rin-gym-forecast-only/.venv/bin/python outputs/parlay-eval-investigation/audit.py
```

The audit projects schedule/team metadata for development IDs only, counts saved task/forecast coverage and candidate timing, records SHA-256 fingerprints, and verifies the ledger guard before/after. `feasibility.json` contains its exact results. It does not call acquisition commands or any inference service.
