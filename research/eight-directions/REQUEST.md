Paste this into the main thread. It authorizes **up to $5 per direction, $40 total**—not $5 total.

```text
Explore eight research directions for this soccer forecasting/trading project using subagents.

The objective is to identify improvements in forecasting quality and net P&L, while understanding why the original policy wins many games but loses money overall.

Budget
- Each direction has a separate hard spending limit of $5.
- Total authorized external-service spending is $40.
- These limits include training, inference, evaluation, retries, and paid data/API access.
- Prefer free analysis of existing artifacts first. Spending the allocation is not a goal.
- Before paid work, verify pricing and calculate a conservative maximum-token estimate.
- Maintain a persistent per-direction spending ledger. Reserve the maximum estimated cost before each request; leave headroom for uncertainty.
- Never automatically restart an interrupted paid run. Reconcile completed requests first.
- Stop when the remaining budget cannot safely cover the next operation.
- Distinguish token-cost estimates from verified billing.

Coordination and workspace safety
- Inspect the repository, applicable AGENTS.md instructions, current Git status, available artifacts, and active work before starting.
- Use subagents, with no more than three research workers running concurrently.
- Assign one clearly scoped direction to each worker. Workers must not spawn additional agents.
- Give each worker ownership of its own experiment directory and artifact directory.
- Tell every worker that others are working in the repository: do not revert others’ changes or modify shared code without coordination.
- Prefer an isolated research worktree. Do not interfere with active training/replay processes or mutate existing experiment artifacts.
- Do not commit, push, open PRs, or deploy anything.
- Do not read or print credentials. Use existing authenticated SDKs and normal credential routing.

Existing context
- Inspect the current artifacts rather than assuming every previously discussed run completed.
- Relevant work is under:
  /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only
- Earlier training artifacts are under:
  /Users/alexkan/Desktop/frontiers/training-results
- The earlier final checkpoint is:
  tinker://d2426bc1-ec71-559d-bb81-e17da9372e58:train:0/sampler_weights/final
- Verify checkpoint availability and provenance before using it.
- Existing results include a five-point replay, a small forecasting-only pilot, frozen-forecast policy comparisons, and fresh closed-loop comparisons.
- The conservative policy changed selection and exit behavior as well as sizing. Do not attribute its entire effect to smaller bets.

Evaluation rules for every direction
- Keep the protected holdout sealed. Do not enroll fixtures, query protected outcomes, score it, or use it for selection.
- Use the existing exclusion guard and verify protection before and after work.
- Existing inspected games are development data, regardless of their historical split names.
- Before examining experiment results, save the hypothesis, cohort IDs, intervention, comparison, primary metrics, seeds, and stopping rule.
- Separate fitting/calibration/selection games from the later development games used to assess an intervention.
- Split by whole match and time. All decisions and repeated trajectories from a match stay together.
- Aggregate metrics by match. Bootstrap paired comparisons by match, keeping repeats together.
- Clearly label post-hoc analyses and acknowledge repeated development-set use and shared-team dependence.
- Preserve feed delays and information cutoffs. Do not expose future events, final scores, later corrections, or execution fills to the decision function.
- Distinguish frozen-signal counterfactuals from fresh closed-loop inference.
- Compare against appropriate market, no-trade, and existing-policy baselines.
- Do not optimize for win rate alone. Report net P&L, gross losses, average win/loss, worst loss, exposure, turnover, and trading coverage where relevant.
- A small pilot or inconclusive result is acceptable. Never keep changing thresholds until results turn positive.

Assign these directions:

1. Forecast–action consistency
Question: Does the model make trades that contradict its own probabilities?
Start with existing logs. Audit buys, closes, and switches against contemporaneous probabilities and fee-adjusted prices. Account for the full action and position, not just the purchased contract.
Separate inconsistent actions, inaccurate forecasts, and adverse execution.
If useful within budget, test one predeclared consistency intervention against the original policy.
Do not treat stated probabilities as ground truth.

2. Market-prior forecasting
Question: Can the model improve market probabilities through small adjustments?
Compare market-only, standalone model, a calibrated blend, and a market-conditioned forecast.
Fit blend parameters only on fitting/calibration games; assess on later development games.
Keep prompts, execution, and evaluation cohorts controlled.
Require evidence of incremental value beyond the market baseline, not merely better results than the standalone model.

3. Probability calibration
Question: Are stated probabilities empirically reliable?
Audit calibration, Brier score, log loss, and output validity.
Use prespecified slices such as outcome, price range, and match phase, with sample sizes.
Fit a simple calibration method on separate development folds and evaluate out of sample.
Keep invalid-output handling consistent. Do not silently normalize invalid forecasts in just one arm.
Report whether any gains come from formatting, calibration, or genuine discrimination.

4. Position-induced forecast bias
Question: Does owning a contract change the forecast despite identical match information?
Present identical observed states with flat, home, draw, and away positions, using matched sampling settings.
Measure probability shifts, calibration effects, and whether changes systematically favor the held contract.
Consider a comparison where forecasting receives no portfolio information and a separate execution component receives the position.
Keep each matched set together in analysis. Do not confuse legitimate action changes with unjustified forecast changes.

5. Information timing and memory
Question: Do denser observations or a compact history improve decisions?
Compare the current checkpoints with minute-by-minute or event-triggered updates.
Treat cadence and memory as separate factors; change one at a time.
Keep a common-time forecasting comparison so dense sampling cannot improve scores simply by adding easier late-game observations.
For execution, hold risk rules and costs fixed and report increased turnover.
Use only information available at each timestamp. If budget limits coverage, freeze a smaller paired cohort before inference.

6. Training objective and scale
Question: Was the forecasting-only pilot too small, or is its objective ineffective?
Inspect learning curves and formatting failures first.
Design a feasible comparison involving base, supervised fine-tuning, and/or supervised fine-tuning followed by RL.
Use training-only targets and appropriate probabilistic objectives; do not imply that a single realized outcome supplies the true conditional probability.
Choose a scientifically interpretable subset of comparisons that fits $5.
Use multiple seeds if feasible. If the budget cannot support a meaningful scaling study, run an integration pilot and explicitly report that learning efficacy remains unresolved.
Do not launch a broad sweep.

7. Execution realism
Question: Do apparent improvements survive plausible trading frictions?
Use existing signals where possible to stress-test latency, quote age, spreads, fees, partial fills, minimum sizes, and unavailable liquidity.
Separate actual observed execution evidence from assumed sensitivity scenarios.
Do not invent order-book data or claim realistic fills from midpoint prices.
Identify break-even assumptions and whether fractional-share policies are feasible.
Keep future fills out of order selection; model limit-order acceptance only in the execution simulator.

8. Narrow-regime specialization
Question: Is there a repeatable advantage in specific match situations?
Predefine a small number of interpretable slices: tied late games, red cards, halftime, or large observed price changes.
Use one development partition to identify candidates and a later partition to assess them.
Report coverage, match counts, uncertainty, and multiple-comparison limitations.
Compare slice performance with the market baseline and overall policy.
Do not promote a slice selected from the same results used to claim success.

Required output from each worker
- A concise report with hypothesis, design, exact intervention, cohort provenance, budget, findings, and limitations.
- Reproducible code/configuration and raw prediction or execution receipts where applicable.
- A comparison table with effect sizes and uncertainty.
- A classification: promising, negative, inconclusive, or blocked.
- A recommendation for the next discriminating experiment.
- Confirmation that the holdout remained unused.
- Cost estimate and actual billing evidence, if available.

Orchestrator’s final output
- Check each worker’s conclusions against its artifacts.
- Produce one ranked research matrix covering all eight directions.
- Separate evidence-backed findings from hypotheses and implementation checks.
- Explain which directions deserve more budget and which should stop.
- Report spending by direction and in total.
- Do not claim profitability or generalization from development-set success.
```