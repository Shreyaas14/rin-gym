# Direction 4: position-induced forecast sensitivity

Classification: **promising**. Matched position/menu/selling-proceeds prompt-bundle sensitivity, not trading outcome or isolated psychological bias.

The hypothesis is that holding a contract shifts its forecast probability upward relative to a flat position with identical observed match information. We used the earlier verified final Qwen3-8B checkpoint without training.

The frozen cohort is the first 12 later-assessment development matches by first observation time: 400021480, 400021494, 400021492, 400021491, 400021499, 400021503, 400021506, 400021511, 400021501, 400021451, 400021448, 400021452. These historically inspected games are not a protected holdout. All 60 existing decision states were crossed with four positions and two matched seeds, for 480 maximum requests. There is no fitted method or data-based threshold selection.

The system prompt and user match events, score, clock, price history and buying costs were checked identical across all 60 sets. Only the existing position, legal-action and current selling-proceeds lines differ. Associated prompts are part of the intervention; this design does not isolate an endowment mechanism. Information delays remain event 60s, fill 60s, price maximum age 90s. Prepared request messages contain no outcome labels or future fills. No execution occurs.

Completed 480/480 requests. Primary complete-valid four-arm sets: 120 across 12 matches. Strict schema/auxiliary validity and probs-only sensitivity are separate.

| Comparison | Effect / level | Match bootstrap 95% |
|---|---:|---|
| uplift_home | 0.027583 | [0.012333, 0.043583] |
| brier_delta_home | -0.008832 | [-0.032311, 0.015540] |
| uplift_draw | 0.050141 | [0.034789, 0.064078] |
| brier_delta_draw | 0.052249 | [0.020415, 0.082232] |
| uplift_away | 0.008557 | [-0.002654, 0.018466] |
| brier_delta_away | -0.014955 | [-0.036135, 0.004113] |
| primary_held_uplift | 0.028761 | [0.021955, 0.036289] |
| mean_brier_delta_held_vs_flat | 0.009487 | [-0.008532, 0.022707] |
| mean_log_loss_delta_held_vs_flat | 0.011796 | [-0.011106, 0.028532] |
| mean_absolute_component_shift | 0.047147 | [0.040894, 0.053332] |

| Arm | Full valid | Probability valid | Brier | Log loss | Five-bin ECE |
|---|---:|---:|---:|---:|---:|
| flat | 1 | 1 | 0.418589 | 0.732378 | 0.141444 |
| home | 1 | 1 | 0.409757 | 0.716025 | 0.126667 |
| draw | 1 | 1 | 0.470838 | 0.806622 | 0.162646 |
| away | 1 | 1 | 0.403634 | 0.709874 | 0.137071 |
| market | n/a | n/a | 0.322545 | 0.554997 | 0.113007 |

Probs-only sensitivity primary effect: {'mean': 0.028760534481380717, 'match_bootstrap_95': [0.02195531562616186, 0.03628877965238904], 'match_means': {'400021448': 0.04478451178451179, '400021451': 0.04769696969696969, '400021452': 0.01866666666666667, '400021480': 0.02166666666666667, '400021491': 0.04866666666666666, '400021492': 0.013363636363636359, '400021494': 0.025020202020202015, '400021499': 0.036333333333333336, '400021501': 0.016980392156862745, '400021503': 0.028, '400021506': 0.010614035087719293, '400021511': 0.03333333333333333}}. Lower Brier/log loss is better; log loss floors the realized probability at 0.0001. Calibration uses five fixed bins and equal-match weighting; its uncertainty is not estimated. Complete-case results and full invalid-penalty scores are exposed in results.json.

The primary averages held-side shifts over sides, decision times and seeds within each match, then weights matches equally. The bootstrap resamples whole matches 4,000 times with seed 202610134. Positive uplift measures ownership-linked prompt sensitivity. It does not by itself mean the forecast became less accurate. Action distributions are descriptive because legal menus differ.

Maximum uncached token cost planned: $0.230084; retained 2x maximum reservations: $0.460167; completed logged-token estimate: $0.184916. Direction cap $5 with $0.50 unallocated headroom. No training/storage cost; verified invoice billing is unavailable. Pending reservations remain encumbered and automatic retries/restarts are disabled.

Limitations: Only 12 repeatedly inspected development matches; assessment is chronological but not untouched. Position line, legal menu and sell information move together; cannot identify their individual causes. Complete-case primary may select valid outputs; sensitivity and invalid penalties exposed. Two seeds; paired seeds reduce noise but do not guarantee identical random draws after prompt differences. Whole-match bootstrap does not model shared-team dependence. Actions legitimately depend on positions; no execution, P&L or profitability claim. Fixed-bin calibration uses a small sample and is descriptive.

Next discriminating experiment: preregister a larger newly enrolled cohort, cross a neutral forecast-only role with positions, and vary legal-action/selling prompts independently to separate ownership wording from action-context effects. Preserve paired observed states and report validity before causal interpretation.

Guard before/after identical: True; ledger head `2f6051410929bd283e984574ecce2a944e82aa12911a13d24c194f48dc5a995d`; state `pending_enrollment` with 0 protected matches. Enrollment is pending; no protected holdout was tested.

Reproduce offline analysis:
```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=. /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python experiments/eight_directions/d4_position_bias/analyze.py
```
Paid inference was run once with `study.py --live`; the start marker prevents automatic resumption. Raw request messages, observation hashes, seeds, completions and output token IDs are preserved.
