"""Trading costs per share. Prices are mids in dollars; settlement is free."""


def fee(p, config, outcome=None):
    rate = config["fee_rate"]
    if rate == "market":
        rate = config["market_fee_rates"][outcome]
    return rate * p * (1 - p)


def buy_cost(p, config, outcome=None):
    """Cash paid: never less than the mid, never more than the $1 payoff."""
    quote = max(p, min(0.999, p + config["half_spread"]))
    return min(1.0, quote + fee(p, config, outcome))


def sell_proceeds(p, config, outcome=None):
    """Cash received: never more than the mid, never less than nothing."""
    quote = min(p, max(0.001, p - config["half_spread"]))
    return max(0.0, quote - fee(p, config, outcome))
