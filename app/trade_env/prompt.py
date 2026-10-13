"""Render a state as chat messages. Nothing here may read a record or a scoring block."""

from .config import OUTCOMES
from .costs import buy_cost, sell_proceeds
from .state import SIDES

OUTPUT = (
    '{"probs": {"home": 0.xx, "draw": 0.xx, "away": 0.xx}, '
    '"action": "<one legal action>", "reason": "<one sentence>"}'
)


def money(value):
    # four places: quotes near 0 and 1 move in steps of 0.0005
    return f"{value:.4f}"


def lag(seconds, live, delayed):
    return live if not seconds else delayed.format(seconds=seconds)


def fill_rule(seconds):
    """Quotes come about once a minute and an order takes the next one due."""
    if not seconds:
        return (
            "Your order fills at the next price quoted, usually within a minute of "
            "your decision."
        )
    return (
        f"Your order fills at the first price quoted at least {seconds:g} seconds "
        f"after you decide, usually {seconds:g} to {seconds + 60:g} seconds later."
    )


def system_prompt(config):
    half, spread = 0.5, config["half_spread"]
    buy, sell = buy_cost(half, config), sell_proceeds(half, config)
    lines = [
        "You are trading one soccer match in play. The market is the 1X2 result at the "
        "end of regulation time.",
        "There are three contracts: home, draw and away. A contract pays $1 per share "
        "if its outcome is the result after 90 minutes plus stoppage time, otherwise "
        "$0. A level score is a draw. Extra time and penalty shootouts do not count.",
        "Prices are dollars per share between 0 and 1 and are roughly the market's "
        "probability of each outcome. The three prices need not sum to exactly 1.",
        "You hold either nothing or 1 share of one contract.",
        f"Costs: buying 1 share costs price + {spread:g} + fee and selling returns "
        f"price - {spread:g} - fee, where fee = {config['fee_rate']:g} x price x "
        f"(1 - price). At a price of 0.50 a buy costs {buy:.4g} and a sell returns "
        f"{sell:.4g}, so buying and selling back loses {buy - sell:.4g}. A share "
        "held to the final whistle settles at $1 or $0 with no exit cost.",
        fill_rule(config["fill_delay_s"])
        + " "
        + lag(
            config["event_delay_s"],
            "The events you see are live.",
            "The events you see run about {seconds} seconds behind live play.",
        ),
        "Your result is the change in value of what you hold, net of costs, up to the "
        "next decision point or the final whistle.",
        "The market already reflects the score and the clock. Trade only when you "
        "believe a price is wrong by more than the cost. Doing nothing, or keeping "
        "what you hold, is a valid choice and is often the best one.",
        "",
        "You may think first. Finish with exactly one JSON object and nothing after it:",
        OUTPUT,
        "probs are your probabilities for the regulation-time result and must sum to "
        "1. action must be one of the legal actions listed in the message.",
    ]
    return "\n".join(lines)


def stat_line(name, totals, window):
    parts = [f"{key} {totals[key]} ({window[key]})" for key in totals]
    return f"{name}: " + ", ".join(parts)


def event_line(row):
    line = f"{row['minute'] or '-'} {row['kind']}"
    if row["team"]:
        line += f" [{row['team']}]"
    if row["text"]:
        line += f" {row['text']}"
    if row["note"]:
        line += f" -> {row['note']}"
    return line


def price_row(label, prices):
    cells = [money(prices[o]) if prices else "n/a" for o in OUTCOMES]
    return f"{label:<17}" + "".join(f"{cell:>7}" for cell in cells)


def user_prompt(state, config):
    names, score, prices = state["names"], state["score"], state["prices"]
    home, away = names["home"], names["away"]
    since = "the start" if state["first"] else "the previous decision point"
    title = state["competition"] or "Soccer match"
    if state["stage"]:
        title += f", {state['stage']}"
    lines = [
        f"{title}: {home} (home) v {away} (away)",
        f"Clock: {state['phase']}",
        f"Score: {home} {score['home']}-{score['away']} {away}",
        "Red cards: "
        + ", ".join(f"{names[s]} {state['totals'][s]['red']}" for s in SIDES),
        "",
        f"Match totals (in brackets: since {since})",
    ]
    lines += [
        stat_line(names[s], state["totals"][s], state["window"][s]) for s in SIDES
    ]
    lines += ["", f"Events since {since}"]
    lines += [event_line(row) for row in state["events"]] or ["(none)"]
    lines += ["", "Prices ($ per share)"]
    lines.append(f"{'':<17}" + "".join(f"{o:>7}" for o in OUTCOMES))
    lines += [price_row(row["label"], row["prices"]) for row in state["history"]]
    lines.append(price_row("now", prices))
    lines.append(
        "Buying now costs about: "
        + ", ".join(f"{o} {money(buy_cost(prices[o], config))}" for o in OUTCOMES)
    )
    position = state["position"]
    if position == "flat":
        lines += ["", "Position: none"]
    else:
        lines.append(
            f"Selling {position} now returns about "
            f"{money(sell_proceeds(prices[position], config))}"
        )
        team = {"home": home, "away": away}.get(position)
        held = position + (f" ({team})" if team else "")
        lines += ["", f"Position: 1 share of {held}"]
    lines.append("Legal actions: " + ", ".join(state["legal_actions"]))
    return "\n".join(lines)


def render(state, config):
    return [
        {"role": "system", "content": system_prompt(config)},
        {"role": "user", "content": user_prompt(state, config)},
    ]
