---
title: Anonymous dataset game selection
type: plan
status: complete
owner: alexkan
created: 2026-10-13
---

## Summary

Replace the demo's hardcoded game with a picker over the 10 games with the highest event density among 484 real games. Use Team A/Team B and stable per-game player aliases throughout browser state, event descriptions, exports and model inputs. Retain the original replay layout and executable paper exchange.

## Implementation Plan

### Steps

1. Export the existing read-only replay database into a portable anonymous catalog. Remove names, source IDs, competition labels, calendar dates and free-text provider commentary; generate descriptions from structured events. Preserve event order, observed locations, source score updates, explicit regulation boundaries, and verified home-win price mappings.
2. Generalize the replay adapter to normalized per-game payloads and expose anonymous game metadata only. Shift each game's timeline to a common synthetic origin.
3. Add server/session game selection with input validation, fresh session epochs, paused playback, empty positions, reset balances and separate valuation caches per source hash. Reject incompatible recorded valuations explicitly.
4. Add the anonymous game picker and replace all hardcoded identities in the browser, including titles, legends, market rules, accessible labels, tooltips and event feed.
5. Test anonymity at every event prefix and API/export boundary, switching after orders, stale-command rejection, settlement across real games, and browser desktop/mobile flows. Keep the original source datasets and video renderer intact.

## Test Plan

Existing exchange/audit tests plus catalog schema/hash/path tests, anonymous player/template tests, valid/invalid game selection, mixed-session isolation, all-game regulation settlement verification, and browser switching/trading checks. No paid model calls are required for anonymous catalog generation or local-mode verification.

## Verification Checklist

- All 484 eligible source games are scored; only the top 10 by active-regulation events/minute are selectable, without publishing names or final outcomes in the picker.
- Team/player identities and real fixture dates are absent from browser payloads and model requests.
- Game selection resets paper state and blocks stale prior-game commands.
- References and settlement correspond to the selected game's regulation contract.
- Live/local mode is preserved; a different-game recording fails explicitly.
- Existing tests, lint, syntax checks and browser checks pass; no commits or pushes.

## Verification results

Published 484 anonymous games with 40,342 regulation-prefix events and 86,258 reference observations. All 484 were independently compared against source regulation scores, elapsed durations, event counts and 29,218 valid observed locations; no explicit team names appeared in full public replay states. All bundles also load with site packages disabled, confirming a standard-library-only runtime.

All 484 complete offline sessions executed a human paper fill, settled at regulation and passed independent exchange-journal regeneration, with 300,000 cents conserved per session. After handling the dataset's second-caution dismissal label, all 16 affected games were replayed and audited again. Reports are saved in `artifacts/market-demo/anonymous-catalog/`.

The final 59 automated tests pass, along with Ruff, JavaScript syntax, Python compilation and whitespace checks. Browser checks confirmed multiple game selections, account reset after a trade, anonymous Player B01 event text, reference changes, and a 390-pixel mobile layout with no horizontal overflow. The Polymarket-only importer rejects Kalshi/mixed venue tags instead of mislabelling their references. The original database and renderer remain intact; no commit or push was made.

## Approved density-ranking update

Rank all 484 hash-verified candidates by recorded events per active regulation minute, including stoppage time and excluding halftime, pre-match activity, extra time and shootouts. Count events within each half’s explicit start/end boundaries, including the boundary events. Break ties by active event count, total event count, then anonymous ID. Expose and accept only the top 10, open the densest game by default, and show each rank, event density and count in the picker. Keep the full anonymous ranking as an offline audit artifact.

Density validation: all 484 counts, active durations, densities and the entire ranking match an independent read-only SQL calculation against the source database. Only ranks 1–10 are exposed; rank 11 is rejected. Game 032 leads with 116 regulation events over 103.562 active minutes (1.120104 events/minute). All 67 automated tests pass, with Ruff, JavaScript syntax, Python compilation and whitespace checks clean. Browser verification confirmed exactly 10 ranked options, selection of rank 10, and switching back to rank 1 with playback paused and reset balances. No browser warnings/errors, paid calls, commits or pushes were needed for this update.

## Single-file Parquet refactor

Replaced the 485 catalog JSON files (9,861,363 bytes) with `data/processed/market-demo.parquet` (992,328 bytes), using typed nested event/price columns and Zstandard compression. PyArrow is already a locked repository dependency. All 484 payload hashes, complete terminal states, density rankings and selected top-ten metadata match the prior catalog. The importer publishes a single Parquet file atomically and refuses replacement; the loader checks schema, row hashes and file integrity. Large generated journals and ranking dumps remain locally available but are ignored by Git, substantially reducing the PR diff. No existing published history is rewritten.
