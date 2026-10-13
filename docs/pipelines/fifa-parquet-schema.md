# FIFA Parquet schema v1

Implemented in `pipelines/fifa/` after schema review. Scope: FIFA v3 match calendars and timelines for both the Premier League and World Cup, written to local Parquet with the same schema. Retain original JSON for reproducibility.

## How much data to start with

The already downloaded cohorts contain:

| Cohort | Matches | Event rows | Timeline JSON, uncompressed |
| --- | ---: | ---: | ---: |
| Premier League 2025/26 | 380 | 31,173 | 22.30 MB |
| World Cup 2026 | 104 | 9,675 | 4.32 MB |
| Combined | 484 | 40,848 | 26.62 MB |

Calendars bring the source JSON total to about 28.6 MB. Compressing individual timeline JSON responses with gzip produces 3.17 MB total. This is a measured compression reference, not a Parquet benchmark. The implemented snapshot measures 145,839 bytes for matches and 7,470,649 bytes for events (7.62 MB combined), including lossless source-record JSON columns. Raw evidence, Parquet, and manifests should comfortably fit within 100 MB for one snapshot. Repeated snapshot retention adds storage.

All 484 matches are useful for ingestion, replay, validation, and initial forecasting baselines. For stronger forecasting experiments, an initial collection target is 3–5 Premier League seasons (1,140–1,900 matches; approximately 94,000–156,000 event rows at the audited density), subject to verifying those seasons' API coverage. This is a planning target, not a statistically established minimum. Use learning curves and match-level uncertainty to decide whether more data helps. Split by whole matches and time; reserve a later season for testing. Treat World Cup matches as a separate domain evaluation, not interchangeable league training examples.

Market-aligned forecasting additionally needs market histories for those matches; only two FIFA/Polymarket match joins have been verified in this repository. More FIFA seasons cannot supply missing tracking or complete pass sequences.

## Output layout

Two Parquet tables, with both competitions combined in each:

```text
data/processed/fifa/
  current.json
  snapshots/<dataset_version>/
    matches.parquet
    events.parquet
    manifest.json
data/raw/fifa/<response_sha256>.json.gz
```

`matches.parquet` contains one row per match. `events.parquet` contains one row per source event. Join on `(source, match_id)`. Both files share a dataset version. A manifest maps source hashes to URLs, original fetch times, local raw files, validation results, and selected competition/season scopes.

At this volume, two combined files are simpler than hundreds of per-match files. If collection grows substantially, the same logical schemas can later be partitioned by competition and season. The writer uses Zstandard compression and explicit Arrow schemas; do not rely on inference from nullable fields or numeric-looking IDs.

Write and validate a new snapshot before atomically updating `current.json`. Failed refreshes leave the published snapshot usable. Reruns contain one current row per logical event, not appended duplicates. Older immutable snapshots preserve changes and deletions; a deleted event disappears from the new full-match snapshot rather than surviving an append-only upsert. Old snapshots are retained; automatic pruning is deliberately not implemented.

## Common conventions

- Every identifier is a string, including numeric-looking FIFA IDs. Premier League IDs can be alphanumeric. Team/player IDs remain source IDs; no speculative cross-provider entity matching.
- Timestamps use Arrow `timestamp[us, tz=UTC]`. Missing values remain null. `?` below means nullable; unspecified fields are required.
- Match metadata and final outcomes are labels/context, not automatically safe in-game model inputs.
- Source event timestamps describe occurrence, not historical publication time. Fetch times describe our collection, not when the provider first published an event. Cache imports retain the original recorded fetch time; unavailable original times remain null.
- Preserve raw event codes and labels alongside a versioned normalized category. Unknown codes remain `unknown`, generate a warning, and are retained.
- Raw JSON retains fields not projected into typed columns. No interpolation or synthetic positions are generated.

## matches.parquet

Grain: one match in the published snapshot. Primary key: `(source, match_id)`.

| Columns | Type | Meaning |
| --- | --- | --- |
| `source` | string | `fifa` |
| `match_id` | string | FIFA v3 `IdMatch`; canonical join ID |
| `competition_id`, `competition_name` | string | Competition identity and English display name |
| `season_id`, `season_name` | string | Season/tournament edition identity and display name |
| `stage_id`, `stage_name`, `group_id`, `group_name`, `matchday_raw` | string? | League round or tournament context; preserve source matchday text |
| `scheduled_kickoff_utc` | timestamp? | Calendar `Date`, not assumed actual kickoff |
| `home_team_id`, `away_team_id` | string? | Nullable for unresolved future tournament fixtures |
| `home_team_name`, `away_team_name` | string? | Source display labels |
| `home_placeholder`, `away_placeholder` | string? | Preserve unresolved bracket slots |
| `match_status_code` | int32? | Raw provider status |
| `match_status` | string | Only observed codes mapped: 0 → completed, 1 → scheduled; other codes → unknown |
| `calendar_home_score`, `calendar_away_score` | int32? | Raw reported score; do not silently reinterpret score scope |
| `regulation_home_score`, `regulation_away_score` | int32? | Derived only from an unambiguous regulation-end event; includes stoppage time, excludes extra time/shootout |
| `final_home_score`, `final_away_score` | int32? | Completed match score excluding shootout, reconciled against timeline; null if ambiguous |
| `shootout_home_score`, `shootout_away_score` | int32? | Separate shootout result; null when not applicable or unknown |
| `went_to_extra_time`, `went_to_shootout` | bool? | Timeline evidence; null if unavailable |
| `winner_team_id`, `result_type_code` | string?, int32? | Raw source outcome metadata; advancement winner can differ from regulation winner |
| `ifes_match_id`, `statsperform_match_id` | string? | Optional alternate namespaces from calendar Properties; neither replaces `match_id` |
| `timeline_status` | string | `ok`, `empty`, `fetch_error`, `invalid`; calendar row remains represented even when timeline fails |
| `event_count`, `located_event_count` | int32? | Snapshot counts; located count includes present_unverified, excludes ambiguous_zero/placeholder; null if unavailable |
| `quality_flags` | list<string> | E.g. score conflict, missing period boundary, ambiguous regulation score, unknown code |
| `calendar_response_sha256`, `timeline_response_sha256` | string, string? | Raw source provenance |
| `calendar_fetched_at_utc`, `timeline_fetched_at_utc` | timestamp? | Original recorded fetch times, separate because requests happen independently |
| `dataset_version`, `schema_version`, `normalization_version` | string, int32, string | Snapshot and mapping version |
| `source_match_json` | string | Lossless original calendar record, including unprojected metadata |

Do not derive regulation score by simply taking the last record with Period 5 when an explicit regulation-end marker is missing or contradictory. Mark that label unavailable. Preserve source discrepancies in raw JSON and quality flags.

## events.parquet

Grain: one provider event in the current match timeline snapshot.

Logical identity is `(source, match_id, team_id, event_id)`, with a real null team component for neutral events. In Premier League 2025/26 there were 670 repeated EventId occurrences across opposing teams; the full composite was unique. It is also unique in the cached World Cup cohort.

| Columns | Type | Meaning |
| --- | --- | --- |
| `source`, `match_id`, `competition_id`, `season_id` | string | Match join and common filtering columns |
| `event_uid` | string | SHA-256 of a canonical JSON array `[source, match_id, team_id, event_id]`; null is distinct from an empty string |
| `event_id` | string | Source `EventId` |
| `source_index` | int32 | Original zero-based response position; stable ordering within this snapshot, not cross-refresh identity |
| `event_time_utc` | timestamp? | Source `Timestamp` parsed to UTC; invalid input retained in raw JSON and flagged |
| `period_code` | int32? | Raw source `Period` |
| `phase` | string | first_half, halftime, second_half, extra_time_first_half, extra_time_break, extra_time_second_half, shootout, pre_match, break, finished, unknown |
| `match_minute_raw` | string? | Exact source clock label, e.g. `45'+3'` or an empty string |
| `clock_minute`, `stoppage_minute` | int32? | Parsed label components, not fabricated sub-minute precision |
| `period_elapsed_ms` | int64? | UTC elapsed time since a validated start marker in this period, including stoppages; null outside recognized play periods or without an unambiguous start |
| `event_type_code`, `event_type_label` | int32?, string? | Preserve raw code and English source label |
| `event_kind` | string | Versioned coarse category: goal, own_goal, assist, shot_attempt, goal_prevention, corner, foul, offside, penalty_awarded, penalty_conceded, penalty_missed, yellow_card, red_card, second_yellow, substitution, var, period_start, period_end, match_end, pause, resume, coin_toss, unknown |
| `event_description` | string? | Original English commentary |
| `team_id`, `player_id`, `sub_player_id`, `sub_team_id`, `person_id` | string? | Raw source references; retain both substitution participants without assuming in/out semantics until mapped and tested |
| `home_score`, `away_score` | int32? | Source event's reported game score, excluding shootout tally |
| `home_shootout_score`, `away_shootout_score` | int32? | Source event's penalty tally; retain raw zeros where provided, phase indicates applicability |
| `x_raw`, `y_raw` | float64? | Raw PositionX/Y; no conversion to meters or automatic attack-direction flipping |
| `location_status` | string | present_unverified, missing, placeholder, ambiguous_zero, out_of_range |
| `goal_gate_x_raw`, `goal_gate_y_raw` | float64? | World Cup GoalGatePositionX/Y; separate from pitch coordinates and PL qualifier fields |
| `foul_party_code` | int32? | Optional Premier League FoulParty |
| `qualifiers_json` | string? | Preserve source Qualifiers array losslessly as JSON; `[]` distinct from missing/null |
| `var_notification_json` | string? | Preserve optional World Cup VarNotificationData object |
| `quality_flags` | list<string> | Invalid timestamp, ambiguous coordinates, unknown code, etc. |
| `source_response_sha256`, `fetched_at_utc` | string, timestamp? | Match timeline payload and original recorded fetch time |
| `dataset_version`, `schema_version`, `normalization_version` | string, int32, string | Same dataset version as match table |
| `source_event_json` | string | Lossless original event; lets replay operate from Parquet without raw cache |

Keep qualifiers as JSON in v1 because their values vary in shape (IDs, numeric strings, arrays). The Premier League has useful goalkeeper, goalmouth, blocked-shot, and related-event attributes there; cached World Cup qualifiers are empty. Frequently used qualifier fields can be promoted to typed columns in a later version without losing the originals. Access to both participant IDs does not provide player names or lineups; those need a separately verified endpoint if required.

Use `source_index` to break ties when ordering equal timestamps. Do not key events by timestamp, description, row number, or event ID alone. If a missing ID or composite collision prevents safe normalization, retain the raw response and mark the match invalid rather than silently dropping or inventing event identities.

## World Cup / Premier League differences to retain

| Concern | Shared-schema treatment |
| --- | --- |
| Competition and season IDs | WC: `17` / `285023`; EPL: `2000000000` / `51r6ph2woavlbbpk8f29nynf8`, all strings |
| Numeric versus alphanumeric IDs | Strings throughout |
| Extra time and shootout | Explicit phase and separate score fields; shootout goals are not regulation goals |
| World Cup VAR and assists | Preserve as events and retain source VAR object |
| Several codes share a label | Keep both raw code and label; mapping tests cover both cohorts |
| Alternate match IDs | Optional IFES and StatsPerform columns; no dependency on an IFES ID for EPL |
| Different coordinate fields | Pitch, goal-gate, and qualifier geometries remain distinct |
| `(0,0)` | EPL cards/substitutions/clock markers are confirmed placeholders. WC contains 82 corner events at `(0,0)`; preserve these as ambiguous until their convention is confirmed |
| Regulation forecasting target | Derive regulation result separately from extra-time result and advancement winner |

Observed play-period codes include 3/5 for the two regulation halves, 7/9 for extra-time halves, and 11 for shootouts. Preserve all other codes and classify only supported mappings. Do not extrapolate period-clock values through breaks or assume a 15-minute halftime.

## Ingestion behavior

1. Resolve configured competition/season IDs, fetch all calendar pages, and validate requested scopes and unique match IDs. Do not assume count=500 always prevents pagination.
2. Fetch timelines with bounded concurrency, retry transient errors with backoff, honor rate limits, and save content-addressed raw responses plus acquisition metadata. Reuse cached historical payloads by default; explicit refresh checks for source corrections.
3. Normalize both feeds to the explicit schemas, retaining nulls, source identities, qualifiers, and unknown codes.
4. Validate event uniqueness, match foreign keys, ordering/timestamps, coordinates, period boundaries, and score consistency. Handle regulation, extra time, and shootout separately. Report structural errors separately from warnings; do not enforce PL-only invariants on World Cup matches.
5. Produce both Parquet files and a manifest with coverage/error counts. Default strict mode does not publish a snapshot with failed/invalid required timelines; an explicit allow-partial mode must retain every calendar match and its failure status. Future/unplayed fixtures may legitimately have no events.
6. Round-trip read the Parquet files, verify schemas and row counts, then atomically publish the snapshot pointer. A run is reproducible from its raw hashes and normalization version.

The default configuration ingests the already verified EPL 2025/26 and World Cup 2026 cohorts and supports adding other competition/season IDs through configuration. It does not alter the existing demo renderer or market joins as part of ingestion.

## Design decision

Two shared Parquet tables (`matches` and `events`), original raw JSON retained, JSON qualifiers in v1, and explicit regulation/extra-time/shootout handling. An event-only single-file export could be added later, but flattening final match labels into every event by default would increase duplication and make forecast leakage easier.
