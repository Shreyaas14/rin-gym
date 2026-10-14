---
title: Interactive replay market implementation
type: plan
status: complete
owner: alexkan
created: 2026-10-13
---

## Summary

Implement the approved binary-market demo while preserving the original replay's pitch, palette, event feed and historical-price comparison. User authorized implementation on October 13, explicitly requesting visible order-book data.

## Evidence

`app/replay/render.py` is the existing Pillow/video renderer, with no interactive server. The bundled fixture includes a second-half End Time at 17:58:24.487 UTC, before the administrative Match end. No compatible recordings or TypeSafe key are available in this process.

## Implementation Plan

### Steps

1. Add a dependency-free Python matching engine with integer-cent collateral accounting and conservation tests in `app/market/engine.py` and `tests/market/`.
2. Add an as-of fixture adapter, anonymous agent observations, explicit local Poisson baseline, optional pinned live Jev adapter, and compatible valuation-recording support.
3. Add isolated server-owned sessions, replay/refresh/settlement sequencing, bounded agent order batches, idempotent browser commands, and exportable session journals.
4. Add a browser UI under `app/market/static/` retaining original replay styling, with pitch, feed, chart, actual order depth/owners, paper ticket, account and agents.
5. Verify matching, replay boundaries, model failure behavior, transport, and browser user flows. Document launch commands and actual evidence.

## Decisions

- Standard-library Python server and plain HTML/CSS/JS keep the initial demo install-free.
- Preserve the original renderer and fixture unchanged; add a browser application alongside them.
- Default to explicitly labelled local simulation when no compatible real-model recording exists. Live Jev is selectable only when the server environment contains its key. Never silently substitute models after a failure.
- This first fixture has Polymarket comparison data only. Do not fabricate a Kalshi line.
- User authorization covers local implementation; no commits, pushes, external tickets or deployment.

## Test Plan

Unit tests for the engine, state prefix handling, strategy and provider boundary; local HTTP tests; browser inspection at desktop/mobile and actual order/close/cancel/reset/settlement flows. Capture screenshots to the market demo artifacts directory.

## Verification Checklist

- Real resting depth and executions share one canonical book.
- Opening and settlement balances reconcile to 3,000 paper dollars.
- NO orders map correctly to fully collateralized YES sells.
- UI retains original replay design and works while paused.
- Major events refresh orders; the regulation whistle settles exactly once.
- Mode labels accurately distinguish simulation from Jev.
- Existing user changes remain intact; no Git commit or push.

## Verification results

All 40 unit/integration tests pass, along with Ruff, JavaScript syntax, Python compilation and whitespace checks. Browser checks covered desktop/mobile layout, YES/NO fills, partial-depth previews, close/cancel/reset, invalid-risk rejection, recorded events and complete regulation settlement. The actual browser-exported session independently regenerated 438 orders and 37 fills, settled correctly, and reconciled to 300,000 cents in total cash. See `artifacts/market-demo/verification.json` and `browser-session.json`.

Live Jev transport is implemented and mock-tested. After the user authorized the credential source, a four-request real pilot completed successfully with the pinned model, two-sided quotes, a human fill and a passing journal audit. Evidence is in `artifacts/market-demo/live-pilot/`. Its recording covers two rounds, not the complete match. Local simulation remains the offline default; the running browser can select Live Jev. Forecast quality and full-match live liquidity remain unvalidated. Browser screenshots were inspected during testing; no screenshot file is bundled.
