"""Render D6 offline report from saved receipts/results."""
import json

from experiments.eight_directions.d6_training_scale.analyze import OUT, read


def main():
    r = read('artifacts/eight-directions/d6_training_scale/results.json')
    p = read('artifacts/eight-directions/d6_training_scale/scaling_proposal.json')
    strict = r['strict']
    diagnostic = r['tolerance_0_05_diagnostic']
    delta = r['strict_final_minus_base']['brier']
    td = r['tolerant_final_minus_base']['brier']
    common = r['common_strict_valid']
    table = '\n'.join(f'| {arm} | {v["brier"]:.6f} | {v["log_loss"]:.6f} | {v["valid_decisions"]}/{v["decisions"]} |' for arm, v in strict.items())
    curve = '\n'.join(f'| {v["iteration"]} | {v["unique_matches"]} | {v["trajectories"]} | {v["decisions"]} | {v["brier"]:.6f} | {v["valid_rate"]:.1%} | {v["logged_entropy"]:.6f} |' for v in r['training_curves'])
    change = r['prediction_changes']
    echo = r['posthoc_prompt_template_diagnostic']
    echo_base = echo['validation_template_echo']['base']['exact_vector_count']
    echo_final = echo['validation_template_echo']['final']['exact_vector_count']
    echo_train = sum(v['exact_vector_count'] for v in echo['training_batches'])
    report = f'''# Direction 6: training objective and scale

RL benefit is **inconclusive**, performance against the market is **negative**, and new SFT/RL learning efficacy is **unresolved**. This direction completed an offline integration fixture; no new model training, inference or paid service requests ran. Latest authorization caps the combined current task at $5 and overrides the old worker brief's $5 per-direction allowance. Incremental D6 spend and reservations are both **$0**.

## Hypothesis and design

Four updates of a proper forecast reward may mainly improve output validity without improving discrimination. We audited the frozen one-seed Qwen/Qwen3-8B forecast-only pilot, separated strict formatting failure from normalized-probability performance, and implemented a local proper-probability loss and canonical serialization fixture. `protocol.json` was saved before this analysis. Prior base/final/market Brier values and the crossing-zero confidence interval were already known; D3 calibration output was also already available. This is a registered reanalysis of repeatedly inspected development evidence, not a preregistration of the original pilot and not an untouched assessment.

All outcomes came from pinned `inputs/forecast-only`, with receipts in `data_receipts.json` and provenance in `inputs/MANIFEST.json`. Original training has **16 Premier League matches, 80 flat observations**, and original validation has **16 World Cup matches, 80 flat observations**. Whole-match training/validation cohorts in `protocol.json` remain unchanged. Training observations precede the World Cup validation period, but competition shift, fixture memorization and reused development labels limit generalization. Original seed is **20261013**, LoRA rank 32, learning rate 1e-5, training temperature 1, evaluation temperature 0, max completion 128 tokens, four batches of four matches with four trajectories per match. This is **one training seed**, not four independent seeds: 64 trajectories and 320 training decisions in total. Seed variance cannot be estimated from it.

Exact original reward is `-multiclass_Brier / decisions_in_match`, invalid output Brier=2; the Tinker recipe uses `importance_sampling` policy-gradient training on these rewards. Forecasting contains no action output and sees only delayed observed messages and prices. Labels are accessible only in scoring/reward. Entry/exit execution and P&L did not inform the forecast reward. NLL and Brier are proper probability objectives in expectation over categorical outcomes; token cross entropy on decimal strings is a formatting/distillation loss and does not itself train calibrated outcome probabilities.

## Frozen pilot evidence

Metrics give each match equal aggregate weight. Strict validity requires exact schema, finite numeric probabilities and sum within 1e-6; invalid rows receive Brier=2 and clipped worst log loss. Probabilities are not ground truth.

| Arm | Invalid-inclusive Brier ↓ | Log loss ↓ | Strict valid decisions |
|---|---:|---:|---:|
{table}

Final minus base Brier is **{delta['mean']:.6f}**, paired whole-match bootstrap 95% interval **[{delta['match_bootstrap_95'][0]:.6f}, {delta['match_bootstrap_95'][1]:.6f}]** (4,000 draws, seed 20261013). The interval crosses zero. Final minus market Brier is **{r['strict_final_minus_market']['brier']['mean']:.6f}**, 95% interval **[{r['strict_final_minus_market']['brier']['match_bootstrap_95'][0]:.6f}, {r['strict_final_minus_market']['brier']['match_bootstrap_95'][1]:.6f}]**, so the market wins on this reused development cohort.

All **21 base** and **19 final** invalid rows fail probability sum checks; they are not missing schema or truncated forecasts. {change['newly_strict_valid']} rows become strict-valid and {change['lost_strict_valid']} lose validity. {change['same_tolerant_probability_vector']}/{change['decisions']} normalized probability vectors are exactly unchanged. A *diagnostic only* 0.05 sum tolerance, retaining the exact schema and normalizing without clipping, gives 80/80 valid rows for both arms. Brier then becomes **{diagnostic['base']['brier']:.6f} base** and **{diagnostic['final']['brier']:.6f} final**: final minus base **{td['mean']:.6f}**, 95% interval **[{td['match_bootstrap_95'][0]:.6f}, {td['match_bootstrap_95'][1]:.6f}]**. That interval also touches/crosses zero and its point estimate is worse. This diagnostic does not change the original valid-rate definition or claim that repaired forecasts were deployed.

On the **{common['count']} identical rows strictly valid for both models**, equal-match Brier changes **{common['base']['brier']:.6f} → {common['final']['brier']:.6f}**, delta **{common['final_minus_base']['brier']['mean']:.6f}**, interval **[{common['final_minus_base']['brier']['match_bootstrap_95'][0]:.6f}, {common['final_minus_base']['brier']['match_bootstrap_95'][1]:.6f}]**. This selected subset is a diagnostic, not an unbiased replacement for invalid-inclusive scoring.

D3 independently reports strict valid-only losses and tolerant diagnostics: each remains worse than market on the same valid rows. Its post-hoc calibration/discrimination work does not establish an SFT or RL effect. D3 output is referenced and checksummed, not refitted here. The strict aggregate improvement is consistent with a small format-repair effect; predictive improvement is unsupported.

## Training curve audit

| Batch | Distinct training matches | Trajectories | Decisions | Brier | Strict valid | Logged entropy |
|---|---:|---:|---:|---:|---:|---:|
{curve}

Each batch covers a **different four training matches**. Lower loss in later batches is not evidence of convergence on a common cohort. Reward-group mixed fractions and logged sample/train KL appear in `results.json`; entropy is the SDK training diagnostic, not the entropy of the reported outcome distribution. No held-out learning curve or per-seed variance is available. Four optimizer steps on 16 matches provide little evidence about scaling and no basis for extrapolating profitable returns.

### Discovered post-hoc prompt confound

After the main analysis, the orchestrator relayed a D5 discovery: its forecast-only SYSTEM example probabilities were echoed frequently. The original D6 protocol was preserved; `posthoc_prompt_diagnostic_protocol.json` separately records this follow-up. The shared SYSTEM explicitly demonstrates `{{"probs":{{"home":0.40,"draw":0.30,"away":0.30}}}}`. Exact normalized-vector counts here are **{echo_base}/80 base**, **{echo_final}/80 final** and **{echo_train}/320 training decisions**. Counts of identical vectors alone do not identify causal prompt anchoring, but make an example-free/abstract-schema prompt ablation a relevant prerequisite to larger training runs. The same confound also limits the proposed self-distillation targets. Per-group reward standard deviations are saved in `results.json`; those groups contain four trajectories of the same training match, and nonzero reward variance may come from format failure rather than useful forecast discrimination. No causal prompt intervention or new prompt result is claimed.

## Offline integration delivered

`train_only_integration.jsonl` contains **80 training-only records**: sanitized observed messages, one-hot categorical targets stored separately from prompts, and weights summing to 1/16 for each whole match. It contains no validation examples, trade future-fill fields, or embedded scoring dictionaries. Targets are observed *training outcomes*, not future observations allowed into the prompt and not authoritative probability estimates.

For a format-SFT fixture, we canonicalize one earliest sum-tolerant saved training completion per task (no filtering by correctness or reward), preserving its normalized soft forecast. All 80 resulting target strings satisfy the exact sum rule. This is self-distillation of prior forecasts and can preserve forecast bias; it is not proper-probability supervision. The separate local reference accepts categorical logits and one-hot labels and computes NLL/Brier gradients; it does not pretend to be a Tinker-compatible trained probability head. Five tests check expected proper-score minimization, analytical gradients against finite differences, 502+ randomized/extreme serialization cases, rejection of duplicate/schema/boolean failures, and training-only label/prompt separation with equal match weight.

The fixture is reproducible data/loss/serialization integration evidence. **No measured improvement in a model** follows from passing those tests. Canonical target serialization guarantees target sums, not that a trained generative model will emit valid sums.

## Next discriminating experiment and bounded scale proposal

Use a fixed **base → format SFT → proper-score RL** sequence on the original training matches. Base evaluation is shared; compare SFT and SFT+RL final checkpoints on the same untouched-by-training development observations. Independently repeat model updates with seeds **20261013 and 20261014**, separate from/bootstrap sampling seed. Keep observations, forecast renderer, sizing and policy fixed. One SFT epoch, no hyperparameter selection, followed by four RL updates with the original group settings, no checkpoint selection. Report strict invalid-inclusive scores as primary; tolerant scores and common-valid rows explain formatting. Report per-seed paired deltas and match bootstrap intervals, and explicitly state two seeds remain insufficient for reliable training variance. Stop on any cost/request bound failure or service error; do not retry or widen the study automatically.

This staged comparison distinguishes a formatting effect from an RL probability effect; it is not a broad scale sweep. A later learning-scale experiment needs more independent training matches and at least three model-training seeds, after fresh cohort/protocol registration and budget authorization. It must keep matched temporal observations in the same split and never tune on the protected cohort.

`scaling_proposal.json` records conservative limits: **{p['max_evaluation_requests']} evaluation requests**, **{p['max_rl_rollout_requests']} RL rollout requests**, **{p['max_sft_examples_one_epoch']} SFT examples**, max 2,048 prompt and 128 output tokens. Published uncached Qwen/Qwen3-8B rates were recorded on 2026-10-13 from [official Tinker pricing]({p['pricing_source']}): prefill $0.195/M, sample $0.60/M, train $0.44/M. The proposal estimates **${p['full_uncached_token_upper_usd']:.6f}** token cost, doubles it to **${p['double_token_upper_usd']:.6f}**, and adds $0.05 storage plus $0.25 headroom: **${p['required_unreserved_combined_budget_usd']:.6f}** unreserved combined budget required. No funds are reserved. Cached discounts are ignored.

The proposal is **not launched**. It fits the latest $5 total cap only if all other completed spend and reservations leave at least this amount; this direction does not assume that headroom exists. Before any future calls, reverify official rates, SDK billing/retry semantics, rendered prompt maxima and checkpoint storage/TTL. The storage line assumes no more than 0.5 GB-month at the recorded $0.10/GB-month; unknown size or retention blocks saving/calls until bounded. Training-token accounting must also be confirmed against the live API. Existing historical pilot usage estimates **$0.181574** at full rates (not invoice billing), separate from current incremental $0. No automatic fresh checkpoint availability is assumed.

## Reproduction and limitations

From `/Users/alexkan/Desktop/frontiers/rin-gym-research-eight`:

```sh
PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m experiments.eight_directions.d6_training_scale.analyze
PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m unittest experiments.eight_directions.d6_training_scale.test_integration -v
PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m experiments.eight_directions.d6_training_scale.report
```

Analysis reproduced the original strict Brier delta/interval; five tests passed. Source data, active experiment artifacts, checkpoints, ledger and shared code were not modified. No commit, push, PR, service client, MLflow requirement or active-run operation.

The uncertainty estimate resamples whole matches, preserving repeated decisions, but does not account for shared-team dependence. All validation is repeatedly inspected historical development, subject to fixture memorization and competition shift. No fresh closed-loop feedback, verified maker returns, prospective profitability or learning efficacy was measured here. Changing tolerance is an accounting/format diagnostic only, not a new model result.

Guard before/after is unchanged: ledger head `{r['seal_after']['ledger_head']}`, state **{r['seal_after']['state']}**, **{r['seal_after']['protected_matches']} enrolled protected IDs**. The initialized holdout policy is intact; enrollment is pending. No protected outcomes were loaded or scored, and no holdout evaluation is claimed.
'''
    (OUT / 'REPORT.md').write_text(report)
    print(str(OUT / 'REPORT.md'))


if __name__ == '__main__':
    main()
