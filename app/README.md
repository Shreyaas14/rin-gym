# Application code

Visualization, dashboard and simulation application code belongs here.

`replay/` renders the Czechia–South Africa World Cup event timeline with historical Polymarket probabilities. It reconstructs recorded actions; it is not a learned simulator or a continuous tracking feed. See the [application guide](../docs/application/README.md).

`market/` adds an anonymous picker for the top 10 dataset games by regulation event density and interactive browser replay with a paper order book, two model-backed trading strategies, human trades and settlement. Run `python3 -m app.market`; see the [market demo guide](../docs/application/market-demo.md) for catalog import, local simulation, optional live Jev and recorded valuations.
