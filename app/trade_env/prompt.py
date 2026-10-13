"""Strict text templates: only observed state and public trading rules are rendered."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .config import OUTCOMES
from .costs import buy_cost, sell_proceeds
from .state import SIDES

TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).with_name("templates")),
    undefined=StrictUndefined,
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)
TEMPLATES.filters.update(
    money=lambda n: f"{n:.4f}",
    general=lambda n: f"{n:g}",
    compact=lambda n: f"{n:.4g}",
    left=lambda s: f"{s:<17}",
    right=lambda s: f"{s:>7}",
    buy=buy_cost,
    sell=sell_proceeds,
)


def system_prompt(config):
    return TEMPLATES.get_template("system.j2").render(config=config)


def user_prompt(state, config):
    return TEMPLATES.get_template("user.j2").render(
        **state, config=config, outcomes=OUTCOMES, sides=SIDES
    )


def render(state, config):
    return [
        {"role": "system", "content": system_prompt(config)},
        {"role": "user", "content": user_prompt(state, config)},
    ]
