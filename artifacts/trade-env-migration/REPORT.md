# Release migration and environment verification

Compared all 484 earlier match records against AK's frozen `data-2026-10-13-v1` release. This is a comparison of recorded data, not a claim about historical execution or live feed availability.

## Every difference

[Match-by-match CSV](matches.csv) has one row for each match, including zero-change fields and each outcome's fee rate. [Full comparison](comparison.json) enumerates every added timestamp and price, plus the explanation for each match. The earlier compressed records have SHA-256 `e6db1d090536b0ccbb7d21c0b20644b07fbea7f4b6c748f88a2497ba80f8acbb`.

- All 484 matches have identical projected match metadata, team identities, event rows/order, period boundaries, regulation results, and three market/token mappings.
- No old price sample is removed or changed. The release adds 6,575 samples across the 484 matches: 6,568 are beyond the old request's `int(h2_end + 120)` endpoint; seven land exactly on that endpoint, which the earlier response excluded. All additions occur after regulation and cannot change a decision, fill, or settlement label. The differences are request-window tails, not revised prices.
- The new `fee_rates` field comes from each selected contract's recorded `feesEnabled` and `feeSchedule.rate`. Of 1,452 contracts, 927 have fees disabled (0), 513 have rate 0.03, and 12 have rate 0.05. Only exponent 1 is supported; missing or unsupported fee metadata is rejected. These are release-snapshot fee schedules; the release does not establish a historical time series of fee changes.
- Parquet price timestamps are floating-point epoch seconds while the earlier JSON stored integer seconds. Values compare exactly; the default tasks remain identical.

The loader reads enriched matches and events from `fifa-markets` and sampled prices from `fifa-market-history`. It additionally reads the latter's market-metadata table because the three main tables do not contain fee schedules. It selects the release's verified regulation YES tokens, without re-resolving fixtures or fetching data. [Release file hashes](release-hashes.json) were checked against the distributed checksum lists.

## Byte-equivalence and rebuild

Before switching inputs, rendering every earlier task with strict Jinja templates produced the exact existing gzip bytes. After switching to AK's release, the default rebuild again produced the same bytes:

`c75246b689d3abcd2b0906e4a60eb35927249bd29865f2647cf9c1e94ebede65`

[Task comparison](task-changes.json): zero added, removed, or changed tasks. [Rebuild](rebuild.json): 484 matches, 2,362 decision points, 9,448 position-conditioned tasks. Train/validation/test: 380/72/32 matches and 7,372/1,436/640 tasks. Dropped decisions: 25 incoherent fills, 31 incoherent shown quotes, two stale quotes. The three demo matches remain test-only.

## Baselines

Recomputed from the release under [default uniform fees](baselines.json) and [recorded market fees](baselines-market.json). Values below are mean simulated cents per match; each row's complete match-bootstrap interval, trade count, and costs are in the linked JSON.

| Policy | Train, default | Val, default | Test, default | Train, market | Val, market | Test, market |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Do nothing | 0 | 0 | 0 | 0 | 0 | 0 |
| Random legal | -7.136 | -3.819 | 2.063 | -3.638 | -2.506 | 3.615 |
| Buy favourite, hold | -5.689 | -4.280 | 4.390 | -4.694 | -3.910 | 4.781 |
| Buy leader / draw, hold | 0.358 | 4.911 | -1.924 | 1.210 | 5.229 | -1.589 |
| Buy home, hold | -3.788 | -0.537 | -10.712 | -2.901 | -0.229 | -10.368 |
| Hindsight ceiling | 74.949 | 63.450 | 83.756 | 77.210 | 64.346 | 84.850 |

Hindsight uses future data and is only a ceiling. These baseline results do not establish model performance.

## Verification and cleanup

- 134 environment, release-adapter and Tinker tests passed with no skips; 27 FIFA ingestion tests passed.
- Ruff checks/formatting and `git diff --check` passed.
- A two-step standalone dry run on the rebuilt release tasks completed through final validation at step 2. [Verification details](verification.json) record its metrics. It used a fake service and learned no weights; no paid training was started.
- Existing regression tests exercise full-batch metrics, task fingerprints, settings-mismatch refusal, final-evaluation idempotence, invalid action words and two stop tokens. Added checks cover task config hashes, credentials, finite numeric settings, unsafe dry-run symlinks, strict templates, invalid decision configs, per-outcome fees and offline Parquet projection.
- `train_tinker.py` is 542 lines, down from 609 despite the added checks. Fake service/scripted policy and dry-run execution moved into tests; `flat_share` and repeated replay execution are removed.
- Removed the old provider-acquisition modules/command, `data/raw/trade_env/`, the old full records file, the empty `.claude` tree, and repository Ruff caches. Retained the earlier compact records summary with the comparison evidence. `/runs/` is ignored.

## RL and leakage review — 2026-10-13

The local adapter is a coherent **single-decision, group-relative policy-gradient baseline**. It is not full-match RL, and an offline wiring test cannot establish that a remotely trained model learns a profitable strategy.

### Research and the actual objective

The original [DeepSeekMath GRPO paper](https://arxiv.org/html/2402.03300v3) uses rewards centered and divided by their group standard deviation, a clipped policy objective, and a reference-policy KL penalty. Those details are not all present here. Calling our implementation the exact original GRPO algorithm would be inaccurate.

Installed `tinker-cookbook==0.5.7` computes `advantage = reward - group_mean`. Its data assembler assigns that advantage to generated tokens, including the stop token, and assigns zero advantage to prompt tokens. It preserves sampling log-probabilities and aligns next-token targets. The launcher uses one fresh synchronous rollout batch followed by one optimizer step and a refreshed sampler. It does not train on validation rollouts. These paths were checked in the installed library, not inferred from the latest online documentation.

Mean centering is intentional in [Tinker's RL guide](https://tinker-docs.thinkingmachines.ai/tutorials/advanced/rl-hyperparams/). The [Dr. GRPO paper](https://arxiv.org/html/2503.20783v2) discusses separate biases introduced by per-response length normalization and per-group standard-deviation normalization. The latter would also reweight our trading states: a tiny-dollar opportunity could receive as much normalized reward spread as a large one. Keeping dollar differences is a defensible choice here. Mean centering with the sample itself included has a finite-group scaling factor; it should not be described as an exactly unbiased leave-one-out baseline.

The default [Tinker importance-sampling loss](https://tinker-docs.thinkingmachines.ai/tinker/losses/importance-sampling/) is `-sum(exp(train_logprob - sample_logprob) * advantage)`. There is no per-completion length division in our adapter. With one fresh update this is a reasonable policy-gradient baseline; it has neither PPO clipping nor a base-model KL anchor. `loss_fn=ppo` remains available. No research result proves that the current learning rate, group size, or absence of KL regularization is optimal for this task. Check reward diversity, validity, truncation, sampler/learner KL, and held-out match P&L before extending a run. The existing sampler/learner KL measures implementation/policy mismatch, not drift from the base model.

[LoRA research from Thinking Machines](https://thinkingmachines.ai/blog/lora/) supports low-rank RL fine-tuning as a practical alternative to full fine-tuning; it does not establish profitability on this dataset. Rank 32 is a reasonable baseline to measure, not a necessary minimum. SFT is optional: it can teach the output format if the initial model cannot produce valid answers. Since our reward table supplies all four counterfactual action rewards, supervised action classification or regression is also a useful comparison. Hindsight-best actions are noisy training labels, not a forecasting oracle at inference time.

### What is being learned

Each task is one independently sampled state and supplied holding. Reward ends at the next usable decision or settlement. Full-match replay carries positions, but training does not propagate later-match returns back through earlier choices. Therefore the objective is a contextual-bandit approximation to sequential trading. Transaction costs and future holding choices can make its optimum differ from the whole-match optimum. Changing that requires a deliberate multi-step training experiment, not merely renaming the current loop.

With `w_log=0`, probabilities must be syntactically valid but are not directly trained for calibration. `w_missed` is largely redundant for valid responses: `pnl - w * (best - pnl) = (1+w)*pnl - w*best`, and the state-constant term cancels under group centering. It changes scaling relative to invalid penalties and other terms, not which legal action is best. Defaults stay at zero. Group reward ties correctly give zero advantage. If every sampled answer is invalid or every answer selects the same action, that group supplies no action-learning signal.

### Repairs and simplification

- Removed the constant-reward filtering option and its position-tag workaround. Every rollout contributes to reported metrics; constant groups still have zero policy advantage.
- Restricted loss selection to compatible importance-sampling/PPO objectives. The launcher explicitly uses one optimizer step per batch and temperature 1.
- Added pre-client checks for duplicate tasks, matches crossing split boundaries, demo matches outside test, and missing/duplicate position variants needed by replay.
- Added code and dependency-lock fingerprints to run identity, so changed training/scoring code cannot silently resume an old run.
- Final evaluation now uses the cookbook's evaluation/export path and records its checkpoint and step. An unrelated metrics row at that step no longer suppresses it; extending training evaluates the new final checkpoint.
- Fixed estimates when the completion cap is below 80 tokens or both evaluators are disabled.
- Cross-checked outcome, match, market, raw YES-token identity, and contract team title. Explicit team-name aliases handle the frozen release; unknown names fail rather than fuzzy-match. Swapping home/away now fails even when it preserves the three-outcome set. This is independent of the enriched match's side label, but still trusts the raw contract description and upstream fixture association.
- Zero-total probability answers now return invalid even with an overly permissive tolerance, instead of dividing by zero.

### Leakage checks and limits

`state.py` reads event timestamps at or before `decision - event_delay_s` and observed prices at or before the decision. It summarizes the visible game and lists events since the previous scheduled checkpoint. It does not use settlement, later prices, later events, or future period boundaries to compose a prompt. Keeping the previous **scheduled** checkpoint also avoids making the prompt window depend on future fill-based filtering. It means a skipped decision's event details are not replayed later; their counts survive in totals.

All 484 matches and 1,452 selected contracts passed the stricter identity checks. All 9,448 tasks rebuilt byte-identically. For every checkpoint with valid shown prices, changing future events, future prices, settlement and future period boundaries left all four position prompts unchanged: **9,548 comparisons**. The trainer's model input also stays identical when hidden task identifiers and scoring data are replaced with sentinels. Train/val/test contain 380/72/32 disjoint matches. Reproduce with `uv run --locked python -m tests.trade_env.audit_release`; detailed counts are in [rl-audit.json](rl-audit.json).

These checks establish the explicit model-input boundary, **not a guarantee of historically available information**:

- The release is a final corrected feed. Event occurrence times are not publication times, and delaying them cannot undo later corrections or retrospective text. Live feed capture with receipt timestamps is needed for that guarantee.
- Task inclusion depends on later fill data. The default build excludes 25 decisions for incoherent future fill prices (plus 31 for shown-price coherence and 2 for stale shown prices). This is retrospective sample selection, even though future prices do not enter prompts. Test an explicit failed-execution/no-trade transition before treating this as a deployable trading simulation.
- A snapshot of fee settings is not a historical fee-change series. Sampled prices plus a fixed spread are not executable order-book fills; separate legs can have different sample times.
- EPL training versus World Cup validation/test measures competition and feed transfer as well as generalization. Repeatedly tuning on test destroys its held-out status. Base-model memorization of named historical games is a separate risk; these tests do not audit pretraining data.

### Verification boundary

Tests exercise the real cookbook's rollout, advantage, token-assembly, training-loop, checkpoint/resume and evaluation paths using a fake service. An additional local four-action Torch policy verifies that the documented loss, applied to our rewards and cookbook advantages, increases expected reward and gives the best action the correct gradient sign. This does **not** execute Tinker's remote backward pass or establish real-model convergence. No paid training was started.

Final local verification: **146 environment/trainer tests + 27 pipeline tests passed**, with no skipped tests. Ruff checks, formatting checks, and `git diff --check` passed. The full-release audit passed separately. Tests include real cookbook execution against the fake service, final-evaluation export/retry/extension, hidden-label isolation, and the local gradient-sign check.

## Trajectory training implemented — 2026-10-13

This supersedes the single-decision-training conclusion above. Checkpoints remain decision times **within** an episode. The trainer now batches matches, creates independent `MatchEnv` instances starting flat, carries each rollout's selected position through the available checkpoints, and terminates only after the last chunk settles.

The existing per-step rewards telescope to net match P&L; group-level reward adds zero to avoid double counting. Cookbook advantages use the entire trajectory return relative to other trajectories of that same match, and every generated turn receives that advantage. Returns are undiscounted. `mark=mid`, `start_mark=decision`, `w_log=0`, and `w_missed=0` are required; malformed answers keep the holding, incur `invalid_penalty`, and continue. The policy receives a fresh state at each checkpoint, not prior response text or hidden rewards.

Defaults are 8 matches per batch, 8 trajectories per match, one epoch. `max_tokens` applies per decision. Estimates count matches, trajectories, and individual model calls separately. Training reports unscaled `pnl_per_match` and `cost_per_match` alongside penalized/scaled total reward. Single-decision validation remains a diagnostic; whole-match validation replay is the main P&L comparison. Run identity includes `match_trajectory/v1`, so previous training cannot silently resume into the new objective.

Validation checks the reward chain across every position variant before a client is created. Tests check independent positions, valid/invalid/truncated transitions, cash-flow reconciliation including settlement exactly once, zero group-final reward, prompt masking, and total-return credit on all five answers. Changing only a final-step reward changes the first decision's trajectory advantage.

**152 environment/trainer tests passed.** A separate two-step fake-service run on the real tasks produced 4 trajectories × 5 decisions per optimizer step and evaluated the final checkpoint. Details: [trajectory-verification.json](trajectory-verification.json). Ruff and whitespace checks passed. No paid training ran. Historical-feed and future-fill-selection limitations remain unchanged.

## Default-size rehearsal and token cost — 2026-10-13

The real task file passed a two-step offline rehearsal with the default 8 matches × 8 trajectories per batch: 64 trajectories/304 decisions, then 64 trajectories/320 decisions. Some matches have fewer usable checkpoints. Initial and final evaluation each ran 200 diagnostic tasks and 72 match replays. The final checkpoint was saved and evaluated at step 2. This exercises the cookbook with a fake service; it does not verify remote gradients or real-model learning and incurred no charges. Evidence: [dry-run-default-verification.json](dry-run-default-verification.json).

The cost audit tokenized all 9,448 actual prompts using the configured Qwen3 tokenizer and renderer. A default one-epoch run covers 380 matches × 8 trajectories = 3,040 trajectories, 14,744 training decisions, and 48 optimizer steps. Six evaluations (steps 0, 10, 20, 30, 40, 48) add 3,354 sampling calls. Prompt lengths are 792–1,599 tokens. Total sampling prefill is 19.88–20.13 million tokens, depending on carried positions.

[Official Tinker pricing](https://tinker-docs.thinkingmachines.ai/tinker/models/models_and_pricing/), checked October 13: Qwen3-8B costs $0.195/M prefill tokens, $0.60/M generated tokens, and $0.44/M training tokens; cached prefill is $0.039/M. At an assumed 80 tokens per answer, equal-weighting possible carried positions gives:

| Component | Tokens | Cost |
| --- | ---: | ---: |
| Sampling prefill, training and evaluation | 20,050,752.5 | $3.91 |
| Generated answers, training and evaluation | 1,447,840 | $0.87 |
| Training input, including masked prompt positions | 17,487,262 | $7.69 |
| Total | | **$12.47** |

The half-token is an average across possible position states, not an actual request. Formula: `(prefill_tokens × 0.195 + generated_tokens × 0.60 + training_input_tokens × 0.44) / 1,000,000`. Training inputs include the prompt and shifted completion (answer length minus one). Counting masked prompt tokens as billable is a conservative billing assumption.

At 64/128/256 answer tokens, the equal-position estimates are $12.20/$13.31/$15.53. Taking the longest possible prompt at every decision and all answers at the 256-token cap gives **$15.58**. These include scheduled and final validation, assume no cache discount, and exclude retries, extra experiments, and checkpoint storage ($0.10/GB/month). Answer lengths are scenarios, not measured real-model outputs; scripted dry-run lengths are not evidence of real answer length. A $20 allowance leaves some headroom for one epoch, but is not an enforced spending cap. Full token counts, task fingerprint, assumptions and scenario arithmetic: [cost-estimate.json](cost-estimate.json).

The README now loads the gitignored root `.env` explicitly with `uv run --env-file .env` for billed training. The launcher itself reads `TINKER_API_KEY` from the process environment. No credentials were printed and no paid training was launched.
