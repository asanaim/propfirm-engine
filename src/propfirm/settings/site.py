"""The public website's text (rules AC07 and AC08, decided 8 Nov 2026).

Each section of the home page has fixed fields and, where the section has
them, a list of items (the "How it works" steps). A change is a settings
proposal approved by a second person (rule AC00); the site then shows the
latest approved text of each section, and its own wording where none is.

AC08: a figure about the firm's products (a profit split, a price, an
account size, a loss limit, a payout term) is never typed: the text names
it with a token, such as {max_split}, and the token is filled from the
products and policy in force when the page is read. Text that types a
money amount or a percentage is refused.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from propfirm.policy import Policy, PolicyError, percent_text

SITE_SECTION = "site_section"


@dataclass(frozen=True, slots=True)
class Shape:
    fields: Mapping[str, int]  # field -> its longest text
    items: Mapping[str, int] = field(default_factory=dict)  # item field -> longest
    max_items: int = 0


SECTIONS: dict[str, Shape] = {
    "hero": Shape({"title_line_1": 80, "title_line_2": 80, "lead": 300, "button": 40}),
    "challenges": Shape({"heading": 80}),
    "how": Shape({"heading": 80, "footnote": 300}, {"title": 60, "text": 200}, 6),
}

TOKEN = re.compile(r"\{([a-z_]+)\}")
# a typed figure: a money amount or a percentage written as digits
TYPED_FIGURE = re.compile(
    r"[$€£]\s?\d|\d\s?(%|percent\b|per cent\b|usd\b|dollars?\b)|\d[\d,.]*\s?[kKmM]\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SiteSection:
    section: str
    fields: Mapping[str, str]
    items: tuple[Mapping[str, str], ...] = ()

    def __post_init__(self) -> None:
        shape = SECTIONS.get(self.section)
        if shape is None:
            raise PolicyError(f"section must be one of {sorted(SECTIONS)}")
        if set(self.fields) != set(shape.fields):
            raise PolicyError(f"{self.section}: fields must be {sorted(shape.fields)}")
        texts = [(k, v, shape.fields[k]) for k, v in self.fields.items()]
        if len(self.items) > shape.max_items:
            raise PolicyError(f"{self.section}: at most {shape.max_items} items")
        for item in self.items:
            if set(item) != set(shape.items):
                raise PolicyError(f"{self.section}: each item has {sorted(shape.items)}")
            texts += [(k, v, shape.items[k]) for k, v in item.items()]
        for key, text, longest in texts:
            check_text(key, text, longest)


def check_text(key: str, text: str, longest: int) -> None:
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= longest:
        raise PolicyError(f"{key}: 1 to {longest} characters")
    if TYPED_FIGURE.search(TOKEN.sub("", text)):
        raise PolicyError(
            f"{key}: a figure is not typed; use a token such as {{max_split}} (rule AC08)"
        )
    for name in TOKEN.findall(text):
        if name not in TOKENS:
            raise PolicyError(f"{key}: unknown token {{{name}}}; known: {sorted(TOKENS)}")


def _money(cents: int) -> str:
    """Whole dollars when exact, else dollars and cents: "$100,000", "$49.50"."""
    dollars, rest = divmod(cents, 100)
    return f"${dollars:,}" if rest == 0 else f"${dollars:,}.{rest:02d}"


def _percent(text: str | None) -> str:
    return "" if text is None else f"{text}%"


def token_values(policy: Policy) -> dict[str, str]:
    """Each token's value from the products and policy in force."""
    products = policy.products
    splits = [p.profit_split for p in products]
    prices = [c for p in products for _, c in p.prices_cents]
    sizes = [s for p in products for s, _ in p.prices_cents]
    evaluation = [r for p in products for r in p.rule_sets if r.profit_target is not None]
    first = evaluation[0] if evaluation else None
    return {
        "max_split": _percent(percent_text(max(splits))) if splits else "",
        "scaled_split": _percent(percent_text(policy.scaling.split_after)),
        "min_price": _money(min(prices)) if prices else "",
        "min_size": _money(min(sizes)) if sizes else "",
        "max_size": _money(max(sizes)) if sizes else "",
        "max_allocation": _money(policy.scaling.max_allocation_cents),
        "first_target": _percent(percent_text(first.profit_target)) if first else "",
        "first_daily_loss": _percent(percent_text(first.daily_loss)) if first else "",
        "first_max_loss": _percent(percent_text(first.max_loss)) if first else "",
        "payout_minimum": _money(policy.payouts.minimum_cents),
        "first_payout_days": str(policy.payouts.first_after_days),
        "payout_interval_days": str(policy.payouts.interval_days),
        "product_count": str(len(products)),
    }


TOKENS = frozenset(
    {
        "max_split",
        "scaled_split",
        "min_price",
        "min_size",
        "max_size",
        "max_allocation",
        "first_target",
        "first_daily_loss",
        "first_max_loss",
        "payout_minimum",
        "first_payout_days",
        "payout_interval_days",
        "product_count",
    }
)


def render(text: str, values: Mapping[str, str]) -> str:
    return TOKEN.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def section_json(s: SiteSection) -> dict[str, Any]:
    return {"section": s.section, "fields": dict(s.fields), "items": [dict(i) for i in s.items]}


def section_from_json(value: Mapping[str, Any]) -> SiteSection:
    fields, items = value.get("fields"), value.get("items", [])
    if not isinstance(fields, dict) or not isinstance(items, list):
        raise PolicyError("a section has fields and items")
    return SiteSection(
        str(value["section"]), dict(fields), tuple(dict(i) for i in items if isinstance(i, dict))
    )


def rendered(s: SiteSection, values: Mapping[str, str]) -> dict[str, Any]:
    return {
        "fields": {k: render(v, values) for k, v in s.fields.items()},
        "items": [{k: render(v, values) for k, v in i.items()} for i in s.items],
    }
