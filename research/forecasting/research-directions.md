# Research directions

The primary question is whether spatial game-event information improves live outcome forecasts beyond score, time and pregame expectations, and how those forecasts compare with prediction markets.

| Direction | Research question | Deliverable |
| --- | --- | --- |
| Live outcome forecasting | Can game-state models improve probability accuracy and calibration? | Home-win/draw/away-win forecasts through each match |
| Spatial information | Do located shots, corners and fouls improve on score/time features? | Ablation comparing the same model with and without spatial inputs |
| Market response | How do sampled probabilities evolve around observed events? | Event-aligned descriptive analysis; no causal claim from alignment alone |
| Model comparison | How do statistical, language and decision models perform under the same information cutoff? | Common accuracy, calibration, latency and cost measurements |
| Sports simulation | Can a model generate plausible future event distributions? | Held-out next-event and trajectory evaluation |
| RL environment | Can an agent improve sequential decisions under explicit state and reward definitions? | A later environment; execution assumptions require separate validation |

For a two-day prototype, prioritize a reproducible joined dataset, forecast evaluation and a visual event replay. Betting decisions are outside the current forecasting scope. A replay of recorded events is not a learned simulator and cannot establish the truth of counterfactual player actions.

The initial FIFA dataset supports event locations rather than full pitch states. The two-match Polymarket join is a feasibility demonstration; more independent matches are needed for a credible forecasting benchmark.
