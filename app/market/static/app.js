"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const SVG = "http://www.w3.org/2000/svg";
  const COLORS = { home: "#2863C3", away: "#C06933", neutral: "#697A79" };
  const money = (cents) => cents == null || !Number.isFinite(Number(cents)) ? "—" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(Number(cents) / 100);
  const number = (value) => Number(value || 0).toLocaleString("en-US", { maximumFractionDigits: 1 });
  const cents = (value) => value == null ? "—" : `${number(value)}¢`;
  const owner = (id) => ({ anchor: "Anchor", pulse: "Pulse", user: "You" }[id] || String(id || "—"));
  const settlement = (value) => value === 1 || value === "YES" ? "YES" : value === 0 || value === "NO" ? "NO" : "—";
  let state = null;
  let pending = false;
  let network = null;
  let outcome = "YES";
  let side = "buy";
  let priceEdited = false;
  let noticeTimer = null;
  let offline = false;
  let serverError = false;
  let connectionError = false;
  let lastDecisionError = null;
  const renderCache = {};

  function node(tag, text, className) {
    const el = document.createElement(tag);
    if (text != null) el.textContent = String(text);
    if (className) el.className = className;
    return el;
  }
  function svg(tag, attrs = {}) {
    const el = document.createElementNS(SVG, tag);
    for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
    return el;
  }
  function text(id, value) { $(id).textContent = value == null ? "—" : String(value); }
  function timestamp(value) {
    if (value == null) return null;
    if (typeof value === "number") return value > 1e12 ? value / 1000 : value;
    const numeric = Number(value);
    if (Number.isFinite(numeric) && String(value).trim()) return numeric > 1e12 ? numeric / 1000 : numeric;
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed / 1000 : null;
  }
  function time(value) {
    const t = timestamp(value);
    if (t == null) return "—";
    const elapsed = Math.round(t - Number(state?.replay.time_origin ?? 3600));
    const absolute = Math.abs(elapsed);
    return `${elapsed < 0 ? "−" : ""}${String(Math.floor(absolute / 60)).padStart(2, "0")}:${String(absolute % 60).padStart(2, "0")}`;
  }
  function shortDescription(value) {
    if (value == null) return "Waiting for the next decision.";
    if (typeof value === "string") return value;
    if (value.description || value.message || value.action) return String(value.description || value.message || value.action);
    return JSON.stringify(value);
  }
  function showNotice(message, error = false, persistent = false) {
    clearTimeout(noticeTimer);
    const el = $("notice");
    el.hidden = !message;
    el.textContent = message || "";
    el.classList.toggle("error", error);
    el.setAttribute("role", error ? "alert" : "status");
    if (message && !persistent) noticeTimer = setTimeout(() => { el.hidden = true; }, 6500);
  }
  function active(order) {
    return Number(order.remaining) > 0 && !["filled", "cancelled", "canceled", "rejected", "expired"].includes(String(order.status || "").toLowerCase());
  }
  function bestPrice() {
    if (!state) return 50;
    const bids = (state.exchange.bids || []).filter(active);
    const asks = (state.exchange.asks || []).filter(active);
    const bid = bids.length ? Math.max(...bids.map((o) => o.price)) : null;
    const ask = asks.length ? Math.min(...asks.map((o) => o.price)) : null;
    const price = outcome === "YES" ? (side === "buy" ? ask : bid) : (side === "buy" ? (bid == null ? null : 100 - bid) : (ask == null ? null : 100 - ask));
    return Math.max(1, Math.min(99, price == null ? Math.round(state.market.midpoint == null ? 50 : outcome === "YES" ? state.market.midpoint : 100 - state.market.midpoint) : price));
  }
  function updateTicket() {
    const size = Number($("size").value);
    const price = Number($("price").value);
    const valid = Number.isInteger(size) && size > 0 && size <= 100000 && Number.isInteger(price) && price >= 1 && price <= 99;
    text("cost-label", side === "buy" ? "Maximum purchase cost" : "Maximum short exposure");
    text("cost", valid ? money(size * (side === "buy" ? price : 100 - price)) : "—");
    previewLiquidity(size, price, valid);
    text("submit-order", `${side === "buy" ? "Buy" : "Sell"} ${Number.isFinite(size) && size > 0 ? number(size) : ""} ${outcome} shares`);
    $("submit-order").disabled = !state || pending || offline || !!state.exchange.settled || !valid;
    text("ticket-hint", state?.exchange.settled ? `Market settled ${settlement(state.exchange.outcome)}. Reset to trade a new replay.` : side === "buy" ? "Paper funds only. Orders execute at your limit or better. A 100-share position limit includes resting orders." : "Selling can open a short position. Maximum loss is collateralized; a 100-share position limit includes resting orders.");
  }
  function previewLiquidity(size, price, valid) {
    if (!state || !valid || state.exchange.settled) { text("liquidity-preview", state?.exchange.settled ? `Final payout: ${settlement(state.exchange.outcome) === "YES" ? "YES" : "NO"} $1 per share.` : "Enter a price and share quantity to preview fills."); return; }
    const yesSide = outcome === "YES" ? side : side === "buy" ? "sell" : "buy";
    const limit = outcome === "YES" ? price : 100 - price;
    const orders = [...(yesSide === "buy" ? state.exchange.asks || [] : state.exchange.bids || [])].filter(active).sort((a, b) => (yesSide === "buy" ? a.price - b.price : b.price - a.price) || a.sequence - b.sequence);
    let remaining = size, filled = 0, cost = 0, selfBlocked = false;
    for (const order of orders) {
      if (!remaining || (yesSide === "buy" ? order.price > limit : order.price < limit)) break;
      if (order.owner === "user") { selfBlocked = true; break; }
      const quantity = Math.min(remaining, Number(order.remaining));
      filled += quantity; remaining -= quantity;
      cost += quantity * (outcome === "YES" ? order.price : 100 - order.price);
    }
    const rest = selfBlocked ? "Your own resting order blocks further fills." : remaining > 0 ? `${number(remaining)} shares would ${$("tif").value === "IOC" ? "cancel" : "rest"}.` : "";
    text("liquidity-preview", filled ? `At this book: ${number(filled)} shares at ${cents(Math.round(cost / filled * 10) / 10)} average (${money(cost)} ${side === "buy" ? "cost" : "proceeds"}). ${rest}` : `No immediate fill at this limit. ${rest}`);
  }
  function updateControls() {
    const ready = !!state && !pending && !offline;
    const settled = !!state?.exchange.settled;
    $("play").disabled = !ready || settled;
    $("next-event").disabled = !ready || settled;
    $("speed").disabled = !ready || settled;
    $("reset").disabled = !ready;
    $("mode").disabled = !ready;
    $("game-picker").disabled = !ready || !(state?.games?.length);
    $("close-position").disabled = !ready || settled || !state.exchange.accounts.user.position;
    for (const el of $("ticket").querySelectorAll("input,select,button")) el.disabled = !ready || settled;
    for (const el of $("user-orders").querySelectorAll("button")) el.disabled = !ready || settled;
    for (const el of $("notice").querySelectorAll("button")) el.disabled = !ready || settled;
    updateTicket();
  }
  async function request(url, options) {
    const response = await fetch(url, { cache: "no-store", ...options });
    let data;
    try { data = await response.json(); } catch { throw new Error("The replay server returned an unreadable response. Check the server and retry."); }
    if (data.replay && data.exchange) render(data);
    else if (data.state?.replay) render(data.state);
    if (!response.ok) throw new Error(data.command_error || data.error || data.message || `Replay request failed (${response.status}).`);
    offline = false;
    return data;
  }
  async function poll() {
    if (network || pending) return;
    network = request("/api/state");
    try { await network; }
    catch (error) {
      offline = true;
      connectionError = true;
      text("connection", "Connection interrupted · retrying");
      $("connection").className = "connection offline";
      showNotice(error.message, true, true);
    } finally { network = null; updateControls(); }
  }
  async function command(action, parameters = {}) {
    if (pending || !state) return;
    pending = true;
    updateControls();
    try {
      if (network) await network.catch(() => {});
      network = request("/api/command", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ epoch: state.epoch, command_id: crypto.randomUUID(), action, ...parameters }) });
      const data = await network;
      if (data.message) showNotice(data.message);
      else if (action === "reset") showNotice("Replay restarted. Paper accounts and the order book have been reset.");
      else if (action === "select_game") showNotice(`${state.replay.game?.label || "Game"} selected. Replay paused and paper accounts reset.`);
      else if (action === "order") showNotice("Order processed. Check your position, resting orders, and recent trades for fills.");
    } catch (error) { showNotice(error.message, true); }
    finally {
      network = null; pending = false;
      if (action === "select_game") $("game-picker").value = state.game_id || state.replay.game?.id || "";
      updateControls();
    }
  }
  function renderGames() {
    const games = (state.games || []).slice(0, 10);
    const density = (game) => Number.isFinite(Number(game.event_density)) ? Number(game.event_density).toFixed(3) : "—";
    const signature = JSON.stringify(games.map((game) => [game.id, game.label, game.rank, game.event_density, game.event_count, game.active_event_count, game.active_minutes]));
    const picker = $("game-picker");
    if (renderCache.games !== signature) {
      renderCache.games = signature;
      picker.replaceChildren(...(games.length ? games.map((game, index) => {
        const option = node("option", `${game.rank ?? index + 1}. ${game.label} · ${density(game)} events/min · ${number(game.active_event_count ?? game.event_count)} regulation events`);
        option.value = game.id;
        return option;
      }) : [node("option", "No games available")]));
    }
    picker.value = state.game_id || state.replay.game?.id || "";
    text("dataset-count", `Top ${number(games.length)} game${games.length === 1 ? "" : "s"}`);
    const selected = games.find((game) => game.id === picker.value);
    text("game-metadata", selected ? `${density(selected)} events/min · ${number(selected.active_event_count ?? selected.event_count)} regulation events · ${selected.has_reference ? "Historical reference available" : "No historical reference"}` : "Two agents. A market you can trade.");
    const home = state.replay.teams?.home || "Team A";
    const away = state.replay.teams?.away || "Team B";
    text("home-team", home); text("home-legend", home);
    text("away-team", away); text("away-legend", away);
    text("chart-heading", `${home} win probability`);
    text("settlement-rule", `YES pays $1 if ${home} wins. A draw or ${away} win settles NO.`);
    $("chart").setAttribute("aria-label", `${home} win probability: market midpoint, trades, and historical reference over replay time`);
  }
  function render(next) {
    const previousEpoch = state?.epoch;
    state = next;
    offline = false;
    if (previousEpoch != null && previousEpoch !== state.epoch) {
      priceEdited = false;
      outcome = "YES"; side = "buy"; $("size").value = "10";
      for (const option of document.querySelectorAll("[data-outcome], [data-side]")) {
        const selected = option.dataset.outcome ? option.dataset.outcome === outcome : option.dataset.side === side;
        option.classList.toggle("selected", selected); option.setAttribute("aria-pressed", String(selected));
      }
    }
    const replay = state.replay;
    renderGames();
    $("score").replaceChildren(document.createTextNode(String(replay.score?.[0] ?? 0) + " "), node("span", ":"), document.createTextNode(" " + String(replay.score?.[1] ?? 0)));
    text("clock", replay.clock || "00:00");
    text("phase", replay.phase || "Kickoff");
    text("play", state.playing ? "Ⅱ Pause replay" : "▶ Play replay");
    $("speed").value = String(state.speed || 1);
    $("mode").value = state.mode || "local";
    $("mode").querySelector('[value="live"]').disabled = !state.capabilities?.live;
    $("mode").querySelector('[value="recorded"]').disabled = !state.capabilities?.recorded;
    const progress = Math.round(Math.max(0, Math.min(1, Number(replay.progress) || 0)) * 1000) / 10;
    $("progress").style.width = `${progress}%`;
    $("progress").parentElement.setAttribute("aria-valuenow", String(progress));
    const modeName = { local: "Local simulation", live: "Live Jev", recorded: "Recorded Jev" }[state.mode] || state.mode;
    text("connection", state.exchange.settled ? `Settled ${settlement(state.exchange.outcome)} · ${modeName}` : `${state.playing ? "Replaying" : "Paused"} · ${modeName}`);
    $("connection").className = `connection${state.playing ? " active" : ""}`;
    text("mode-note", state.mode === "local" ? "Deterministic local agents · paper funds" : state.mode === "recorded" ? "Recorded Jev valuations · interactive orders" : "Live Jev decisions · paper funds");
    text("question", state.market.question || `Will ${replay.teams?.home || "Team A"} win?`);
    const decisionError = state.error || state.agents?.find((agent) => agent.error)?.error;
    if (decisionError) {
      serverError = true;
      const errorText = shortDescription(decisionError);
      const changed = lastDecisionError !== errorText;
      if (changed) showNotice(errorText, true, true);
      lastDecisionError = errorText;
      if (!state.exchange.settled && changed) {
        const retry = node("button", "Retry decision", "retry-button");
        retry.type = "button";
        retry.disabled = pending;
        retry.addEventListener("click", () => command("retry"));
        $("notice").append(document.createTextNode(" "), retry);
      }
    } else if (serverError || connectionError) { $("notice").hidden = true; serverError = false; connectionError = false; lastDecisionError = null; }
    renderPitch(replay);
    renderBook();
    renderAccount();
    renderAgents();
    renderTrades();
    renderChart();
    if (!priceEdited) $("price").value = String(bestPrice());
    updateControls();
  }
  function eventSymbol(event, current) {
    const rawX = Number(event.x), rawY = Number(event.y);
    if (event.x == null || event.y == null || !Number.isFinite(rawX) || !Number.isFinite(rawY)) return null;
    const x = Math.max(0, Math.min(100, rawX)) * 1.05;
    const y = Math.max(0, Math.min(100, rawY)) * .68;
    const color = COLORS[event.team] || COLORS.neutral;
    const group = svg("g", { opacity: current ? 1 : .32 });
    const type = String(event.type || "").toLowerCase();
    const r = current ? 1.05 : .55;
    if (current) group.append(svg("circle", { cx: x, cy: y, r: 2.35, fill: "none", stroke: color, "stroke-width": .18 }), svg("circle", { cx: x, cy: y, r: 1.55, fill: "#FCFBF5" }));
    if (type.includes("foul")) group.append(svg("path", { d: `M${x-r} ${y-r}L${x+r} ${y+r}M${x-r} ${y+r}L${x+r} ${y-r}`, stroke: color, "stroke-width": .35 }));
    else if (type.includes("yellow")) group.append(svg("rect", { x: x-r*.65, y: y-r, width: r*1.3, height: r*2, fill: "#B5942E" }));
    else if (type === "goal!" || type.includes("penalty goal")) {
      const points = Array.from({ length: 10 }, (_, i) => { const a = i * Math.PI / 5 - Math.PI / 2; const radius = (i % 2 ? .46 : 1) * r * 1.4; return `${x + Math.cos(a) * radius},${y + Math.sin(a) * radius}`; }).join(" ");
      group.append(svg("polygon", { points, fill: color }));
    } else if (type.includes("corner")) group.append(svg("path", { d: `M${x} ${y-r}L${x+r} ${y}L${x} ${y+r}L${x-r} ${y}Z`, fill: "none", stroke: color, "stroke-width": .3 }));
    else group.append(svg("circle", { cx: x, cy: y, r, fill: "none", stroke: color, "stroke-width": current ? .35 : .22 }));
    const title = svg("title"); title.textContent = `${event.minute || ""} ${event.type || "Event"}: ${event.description || ""}`; group.append(title);
    return group;
  }
  function renderPitch(replay) {
    const marks = [];
    for (const event of replay.events || []) {
      if (event.id === replay.current?.id) continue;
      const mark = eventSymbol(event, false);
      if (mark) marks.push(mark);
    }
    if (replay.current) { const mark = eventSymbol(replay.current, true); if (mark) marks.push(mark); }
    $("event-marks").replaceChildren(...marks);
    const event = replay.current;
    text("event-minute", event?.minute || "—");
    text("event-type", event?.type || "Ready for kickoff");
    $("event-type").style.color = COLORS[event?.team] || COLORS.neutral;
    text("event-description", event?.description || "Play the replay or advance to the next event to open the market.");
    const latest = (replay.events || []).slice(-3).reverse();
    $("event-feed").replaceChildren(...latest.map((e) => {
      const row = node("div", null, "feed-row");
      const label = node("span", e.type || "Event", "feed-type");
      label.style.color = COLORS[e.team] || COLORS.neutral;
      label.title = e.description || e.type || "";
      row.append(node("span", e.minute || "—", "feed-time"), label, node("span", time(e.time), "feed-utc"));
      return row;
    }));
  }
  function renderBookSide(id, orders, isAsk) {
    const signature = JSON.stringify(orders);
    if (renderCache[id] === signature) return;
    renderCache[id] = signature;
    const sorted = orders.filter(active).sort((a, b) => (isAsk ? a.price - b.price : b.price - a.price) || a.sequence - b.sequence).slice(0, 10);
    let total = 0;
    const rows = sorted.map((order) => ({ order, total: total += Number(order.remaining) }));
    const max = total || 1;
    if (isAsk) rows.reverse();
    $(id).replaceChildren(...(rows.length ? rows.map(({ order, total: cumulative }) => {
      const row = node("div", null, `book-row ${isAsk ? "ask" : "bid"}${order.owner === "user" ? " mine" : ""}`);
      row.style.setProperty("--depth", `${cumulative / max * 100}%`);
      row.title = `${owner(order.owner)} · order ${order.id} · ${number(order.remaining)} shares at ${cents(order.price)}`;
      const price = node("button", cents(order.price), "book-price");
      price.type = "button";
      price.title = "Use this price in the paper ticket";
      price.addEventListener("click", () => { $("price").value = String(outcome === "YES" ? order.price : 100 - order.price); priceEdited = true; updateTicket(); });
      row.append(price, node("span", number(order.remaining)), node("span", number(cumulative)), node("span", owner(order.owner), "book-owner"));
      return row;
    }) : [node("div", isAsk ? "No sell orders yet" : "No buy orders yet", "empty")]));
  }
  function renderBook() {
    const exchange = state.exchange;
    renderBookSide("asks", exchange.asks || [], true);
    renderBookSide("bids", exchange.bids || [], false);
    const activeBids = (exchange.bids || []).filter(active), activeAsks = (exchange.asks || []).filter(active);
    const count = activeBids.length + activeAsks.length;
    text("book-count", `${count} resting order${count === 1 ? "" : "s"}`);
    document.querySelector(".book-footnote").textContent = `${activeBids.length > 10 || activeAsks.length > 10 ? "Best ten orders per side shown. " : ""}Individual resting orders. Total = cumulative shares from the best price. Prices come from the book.`;
    const spread = activeBids.length && activeAsks.length ? Math.min(...activeAsks.map((o) => o.price)) - Math.max(...activeBids.map((o) => o.price)) : null;
    text("spread", cents(spread));
    text("book-midpoint", cents(state.market.midpoint));
    text("last-price", cents(state.market.last_trade?.price ?? exchange.trades?.at(-1)?.price));
  }
  function signedPosition(q) { return `${number(Math.abs(q || 0))} ${Number(q) < 0 ? "NO" : "YES"}`; }
  function pnlStyle(el, value) { el.classList.toggle("positive", value > 0); el.classList.toggle("negative", value < 0); }
  function renderAccount() {
    const account = state.exchange.accounts.user;
    text("available", `${money(account.available)} available`);
    text("cash", `Cash ${money(account.cash)}`);
    text("reserved", `Reserved ${money((account.reserved || 0) + (account.collateral || 0))}`);
    text("position", signedPosition(account.position));
    text("equity", money(account.equity));
    text("pnl", `${account.pnl > 0 ? "+" : ""}${money(account.pnl)}`);
    pnlStyle($("pnl"), account.pnl);
    const orders = (state.exchange.orders || []).filter((o) => o.owner === "user" && active(o));
    text("user-order-count", orders.length);
    const signature = JSON.stringify(orders);
    if (renderCache.userOrders === signature) return;
    renderCache.userOrders = signature;
    $("user-orders").replaceChildren(...(orders.length ? orders.map((o) => {
      const row = node("div", null, "user-order");
      row.append(node("span", `${String(o.side).toUpperCase()} ${number(o.remaining)} YES @ ${cents(o.price)}`));
      const cancel = node("button", "Cancel"); cancel.type = "button";
      cancel.setAttribute("aria-label", `Cancel order for ${number(o.remaining)} shares at ${cents(o.price)}`);
      cancel.addEventListener("click", () => command("cancel", { order_id: o.id }));
      row.append(cancel); return row;
    }) : [node("p", "Your resting orders will appear here.", "empty")]));
  }
  function renderAgents() {
    text("agent-source", state.mode === "local" ? "Local strategies · not Jev" : state.mode === "recorded" ? "Recorded Jev valuations" : "Live Jev decisions");
    $("agents").replaceChildren(...(state.agents || []).map((agent) => {
      const account = state.exchange.accounts[agent.id] || {};
      const card = node("article", null, "agent");
      const top = node("div", null, "agent-top");
      const name = node("div", null, "agent-name");
      name.append(node("span", String(agent.name || agent.id).slice(0, 1), "agent-monogram"), node("span", agent.name || owner(agent.id)));
      top.append(name, node("span", state.mode === "local" ? "Local simulation" : state.mode === "recorded" ? "Recorded Jev" : "Live Jev", "agent-source"));
      const values = node("div", null, "agent-values");
      for (const [label, value] of [["Belief · Team A win", agent.valuation == null ? "—" : `${number(agent.valuation * 100)}%`], ["Position", signedPosition(account.position)], ["P&L", `${account.pnl > 0 ? "+" : ""}${money(account.pnl)}`]]) {
        const pair = node("div"); const valueEl = node("strong", value);
        if (label === "P&L") pnlStyle(valueEl, account.pnl);
        pair.append(node("span", label), valueEl); values.append(pair);
      }
      const action = node("div", agent.error ? `Decision unavailable: ${shortDescription(agent.error)}` : shortDescription(agent.last_action), "agent-action");
      if (agent.error) action.classList.add("negative");
      card.append(top, values, action); return card;
    }));
  }
  function renderTrades() {
    const trades = [...(state.exchange.trades || [])].sort((a, b) => b.sequence - a.sequence).slice(0, 10);
    if (!trades.length) { const row = node("tr"); const cell = node("td", "No trades yet. Play the replay to let the agents start quoting.", "empty"); cell.colSpan = 6; row.append(cell); $("trades").replaceChildren(row); return; }
    $("trades").replaceChildren(...trades.map((trade) => {
      const row = node("tr", null, trade.buyer === "user" || trade.seller === "user" ? "user-trade" : "");
      for (const value of [time(trade.time), cents(trade.price), number(trade.size), owner(trade.buyer), owner(trade.seller), `${owner(trade.maker)} → ${owner(trade.taker)}`]) row.append(node("td", value));
      return row;
    }));
  }
  function renderChart() {
    if (!state) return;
    const market = state.market;
    const chart = $("chart");
    const showReference = $("reference-toggle").checked;
    $("reference-key").hidden = !showReference;
    const midpoint = state.exchange.settled ? (settlement(state.exchange.outcome) === "YES" ? 100 : 0) : market.midpoint;
    $("midpoint").replaceChildren(document.createTextNode(midpoint == null ? "—" : number(midpoint)), node("small", "%"));
    text("quote-label", state.exchange.settled ? `Settled ${settlement(state.exchange.outcome)} · ${midpoint === 100 ? "YES" : "NO"} pays $1` : market.mark_stale && state.exchange.accounts.user.equity != null ? "No two-sided quote · equity uses last mark" : midpoint == null ? "Waiting for a two-sided book" : "Midpoint of best bid + ask");
    text("reference-age", showReference && market.reference_price != null ? `Polymarket ${cents(market.reference_price)} · ${number(market.reference_age)}s old` : "Only observations available by replay time");
    text("chart-now", state.replay.clock || "Now");
    const history = market.history || [];
    const reference = showReference ? market.reference || [] : [];
    const times = [...history, ...reference].map((p) => timestamp(p.time)).filter((t) => t != null);
    const now = timestamp(state.replay.timestamp) ?? timestamp(state.replay.utc) ?? (times.length ? Math.max(...times) : 0);
    const earliest = times.length ? Math.min(...times) : now;
    const end = Math.max(earliest + 1, now);
    const x = (t) => 32 + Math.max(0, Math.min(1, (t - earliest) / (end - earliest))) * 696;
    const y = (p) => 143 - Math.max(0, Math.min(100, Number(p))) * 1.3;
    const children = [];
    for (const value of [0, 50, 100]) {
      children.push(svg("line", { x1: 32, x2: 728, y1: y(value), y2: y(value), stroke: "#D5DBD2", "stroke-width": 1 }));
      const label = svg("text", { x: 22, y: y(value)+3, "text-anchor": "end", fill: "#697A79", "font-size": 9, "font-family": "Arial, sans-serif" }); label.textContent = String(value); children.push(label);
    }
    function line(points, field, color, dashed = false) {
      let d = "", started = false, lastY = null;
      for (const point of points) {
        const t = timestamp(point.time), value = point[field];
        if (t == null || t > now) continue;
        if (value == null) { started = false; lastY = null; continue; }
        const px = x(t), py = y(value);
        d += started ? `H${px}V${py}` : `M${px},${py}`;
        started = true; lastY = py;
      }
      if (lastY != null && started) d += `H${x(now)}`;
      if (d) children.push(svg("path", { d, stroke: color, fill: "none", "stroke-width": dashed ? 1.6 : 2.3, "stroke-linejoin": "round", ...(dashed ? { "stroke-dasharray": "4 3" } : {}) }));
    }
    line(reference, "price", COLORS.away, true);
    line(history, "midpoint", COLORS.home);
    for (const trade of state.exchange.trades || []) {
      const t = timestamp(trade.time);
      if (t == null || t > now || t < earliest) continue;
      const dot = svg("circle", { cx: x(t), cy: y(trade.price), r: 2.7, fill: "#FCFBF5", stroke: "#172F34", "stroke-width": 1.2 });
      const title = svg("title"); title.textContent = `${time(trade.time)} · ${number(trade.size)} shares at ${cents(trade.price)} · ${owner(trade.buyer)} bought from ${owner(trade.seller)}`;
      dot.append(title); children.push(dot);
    }
    if (state.exchange.settled) children.push(svg("circle", { cx: x(now), cy: y(midpoint), r: 3.2, fill: COLORS.home }));
    if (!history.some((p) => p.midpoint != null) && !reference.length) {
      const label = svg("text", { x: 380, y: 80, "text-anchor": "middle", fill: "#697A79", "font-size": 12, "font-family": "Arial, sans-serif" }); label.textContent = "Market history appears as orders arrive"; children.push(label);
    }
    chart.replaceChildren(...children);
  }

  $("play").addEventListener("click", () => command(state.playing ? "pause" : "play"));
  $("next-event").addEventListener("click", () => command("next_event"));
  $("speed").addEventListener("change", () => command("speed", { speed: Number($("speed").value) }));
  $("reset").addEventListener("click", () => command("reset", { mode: $("mode").value }));
  $("mode").addEventListener("change", () => command("reset", { mode: $("mode").value }));
  $("game-picker").addEventListener("change", () => {
    const gameId = $("game-picker").value;
    if (gameId && gameId !== (state?.game_id || state?.replay.game?.id)) command("select_game", { game_id: gameId });
  });
  $("reference-toggle").addEventListener("change", renderChart);
  $("price").addEventListener("input", () => { priceEdited = true; updateTicket(); });
  $("size").addEventListener("input", updateTicket);
  $("tif").addEventListener("change", updateTicket);
  for (const button of document.querySelectorAll("[data-outcome], [data-side]")) button.addEventListener("click", () => {
    if (button.dataset.outcome) outcome = button.dataset.outcome;
    if (button.dataset.side) side = button.dataset.side;
    for (const option of document.querySelectorAll("[data-outcome], [data-side]")) {
      const selected = option.dataset.outcome ? option.dataset.outcome === outcome : option.dataset.side === side;
      option.classList.toggle("selected", selected); option.setAttribute("aria-pressed", String(selected));
    }
    priceEdited = false; $("price").value = String(bestPrice()); updateTicket();
  });
  $("ticket").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!$("ticket").reportValidity() || $("submit-order").disabled) return;
    command("order", { outcome, side, price: Number($("price").value), size: Number($("size").value), tif: $("tif").value });
  });
  $("close-position").addEventListener("click", () => command("close"));
  poll();
  setInterval(poll, 750);
})();
