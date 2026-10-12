# Forecast evaluation proposal

This document specifies intended experiments; it does not report trained-model results.

- Predict `P(home win)`, `P(draw)` and `P(away win)` under the contract's regulation-time rules. Probabilities sum to one.
- Issue forecasts at minute checkpoints during play, using only available history. Keep observation delay and missingness explicit.
- Use multiclass Brier score as the primary metric, with log loss and calibration as secondary diagnostics.
- Compare a frozen pregame prior, a score/time/prior baseline, that baseline plus event/spatial features, and contemporaneous market probabilities.
- Initially hold live market prices out of model inputs and use them as an external benchmark. Later test whether adding features improves over a market-only model.
- Split chronologically by whole game. Freeze test membership before model selection.
- Average checkpoint losses within each game and then across games. Bootstrap whole games for uncertainty, not correlated timestamps.
- Report game-phase and score-difference slices. Do not claim a single outcome verifies a single predicted probability.

Raw market prices for separate outcomes may not sum to one. A multiclass comparison must predeclare how to transform them into a probability simplex, retain the original prices, and report exclusions or missing values. Use identical checkpoints and information cutoffs for models and baselines.

Historical event time is not guaranteed first-publication time. Evaluate delay sensitivity and exclude unresolved timestamp anomalies. Reconstructed final scores, future events, full-match aggregates and later price samples must never enter a checkpoint's features.

Reference: [Gneiting and Raftery, proper scoring rules](https://stat.uw.edu/research/tech-reports/strictly-proper-scoring-rules-prediction-and-estimation-revised).
