"""Trading costs per share. Prices are mids in dollars; settlement is free."""


def fee(p, config):
    return config["fee_rate"] * p * (1 - p)


def buy_cost(p, config):
    """Cash paid: never less than the mid, never more than the $1 payoff."""
    quote = max(p, min(0.999, p + config["half_spread"]))
    return min(1.0, quote + fee(p, config))


def sell_proceeds(p, config):
    """Cash received: never more than the mid, never less than nothing."""
    quote = min(p, max(0.001, p - config["half_spread"]))
    return max(0.0, quote - fee(p, config))
