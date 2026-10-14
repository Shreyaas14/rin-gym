# Czechia–South Africa: pitch events and market probabilities

An approximately two-minute silent video replay of the June 18, 2026 World Cup match, rendered at 1600 × 900 and 24 fps.

## Visual encoding

- Blue: Czechia. Orange: South Africa. Gray: the draw market.
- Named markers identify the player associated with the current recorded action, where coordinates exist.
- Circles: shots/actions. Crosses: fouls. Diamonds: corners. Stars: goals. Small rectangles: yellow cards.
- Faint marks are earlier actions, not simultaneous positions of different players.
- No continuous movement, passes, ball flight, formations or unobserved player positions are synthesized.
- Raw FIFA coordinate axes are retained; teams change ends as reflected in the data. Units/orientation have not been independently calibrated to stadium geometry.
- Events lacking coordinates remain in the match feed and receive no invented pitch marker. The late penalty goal is one such event.
- Match time is compressed nonlinearly, with pauses on recorded events and longer holds on goals. The displayed UTC time is the data time, not the video time.

## Probabilities

The three cards and chart show Polymarket's sampled historical Yes prices for the regulation-time home-win, draw and away-win contracts. They are market prices, not outputs from a forecasting model. Values are not normalized to sum to one.

Each video frame uses only samples timestamped at or before its displayed replay time. Cards display each sample's time and age. The chart uses steps rather than future interpolation. FIFA timestamps describe archived event timing; publication/receipt timing has not been established. The visualization therefore shows temporal association, not proof of causal market response or an executable betting opportunity.

## Source files

- FIFA match: `400021440`, 94 events; 63 have pitch coordinates.
- Polymarket contracts: `1897106`, `1897107`, `1897108`.
- Cached input: `data/examples/world-cup/czechia-south-africa/fifa_400021440_timeline.json`, `fifa_poly_400021440.json` and `fifa_prices_1897106.json` through `fifa_prices_1897108.json`.
- Source URLs and SHA-256 hashes are preserved in adjacent `.meta.json` files.

Visual inspiration: [Zeh Fernandes's World Cup posters](https://zehfernandes.com/posts/how-i-turned-world-cup-data-into-posters). His poster places events on a chronological grid; this adaptation uses recorded pitch coordinates and animated market history, with original programmatic graphics.

## Re-render

Requires Python with Pillow and NumPy, macOS Arial/Andale or Linux DejaVu fonts and FFmpeg with libx264. FFmpeg is resolved from the system PATH.

```sh
python3 -m app.replay.render
```

Pass `--preview` to render only still previews. `render-manifest.json` records the final duration and encoding parameters.
