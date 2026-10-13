# Detailed soccer play-by-play

Build an interactive event replay and a complete JSON state export with the installed uv tool:

```sh
rin-gym play-by-play
rin-gym --workspace /path/to/workspace play-by-play --match 3943043
```

The default is Spain–England, Euro 2024 final. `--competition` and `--season` select another StatsBomb catalog (defaults 55 and 282); `--match` selects a game. `--output` overrides the generated bundle directory. `--market-slug` adds a separate, unsynchronized Polymarket UTC chart; the default final market is included automatically. Public downloads require internet on first run and are then cached with URL/hash verification.

Open `artifacts/play-by-play/3943043/index.html` directly in a browser, or serve your workspace. The HTML is standalone, including its data and scripts. `state.json` contains every original event, reconstructed state, linked player snapshots, source hashes and market histories. `manifest.json` summarizes coverage and score checks. These large generated bundles and raw downloads are ignored by Git.

## State at each recorded action

- Period, fractional-second period clock, event type, actor, recipient, related events and all original action qualifiers.
- Pass/carry/shot start and end coordinates, recorded duration, outcome, pressure and counterpress flags; pass height/length and shot xG when available.
- Provider possession sequence and team, distinguishing a new sequence from a team change.
- Score before/after the record, separate shootout score, cards, substitutions, current lineup and last observed formation.
- Current event's 360 player positions and visible camera polygon; named shot freeze frames are a fallback when 360 is missing. Anonymous 360 players remain anonymous.
- Last on-ball event with its timestamp, reset across periods. Defensive pressure locations do not become ball positions.

Player profiles use lineup names, nicknames and jersey numbers only. Final playing intervals and card summaries from lineup files are not used to construct earlier state. The viewer normalizes team directions consistently (home attacks right, away left), including all snapshot coordinates and camera polygons.

## What the replay does not establish

This is a retrospective record of completed actions. Pass/carry endpoints, outcomes, duration, pressure annotations and possession labels may rely on later information; the export explicitly sets `forecast_ready` to `false`. It must not be used directly as a live forecast observation.

There is no continuous player or ball tracking. Arrows connect endpoints, without asserting an observed trajectory. Player locations are never carried into an event without its own snapshot, and identities are not inferred by matching nearby anonymous players. Lineup membership can be known while a player's position is unobserved.

Market histories retain their UTC timestamps and contract labels. Without verified UTC anchors for each game period, they are displayed separately and are not joined to events.

## Checks

```sh
uv run python -m unittest discover -s tests -v
uv build
```

The default match reconstructs 2–1 from 3,304 records, with 147 possession sequences, 96 possession-team changes and 2,882 linked 360 snapshots. Unit tests cover event prefixes, missing spatial observations, possession changes, substitutions, dismissals, own goals, shootouts and anonymous identities.

Data: [StatsBomb Open Data](https://github.com/hudl/open-data). Definitions: [event and 360 schemas](https://github.com/hudl/open-data/tree/master/doc).
