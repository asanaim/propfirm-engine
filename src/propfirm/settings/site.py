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

AC17 (8 Nov 2026): the maintenance page and the registration closed page
are two more sections, each with a title, a message and an optional image
(a path on the site: the site's content security policy loads images only
from itself). Whether either is shown is the
site mode, a separate setting with the same two-person approval: in
maintenance the firm takes no registrations and sells no challenges;
trading, payouts and sign in carry on, so no trader is ever kept from
managing an open position.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from propfirm.policy import Policy, PolicyError, percent_text

SITE_SECTION = "site_section"
SITE_MODE = "site_mode"


@dataclass(frozen=True, slots=True)
class Shape:
    fields: Mapping[str, int]  # field -> its longest text
    items: Mapping[str, int] = field(default_factory=dict)  # item field -> longest
    max_items: int = 0
    optional: frozenset[str] = frozenset()  # fields that may be left empty
    images: frozenset[str] = frozenset()  # fields holding an image address
    figures: frozenset[str] = frozenset()  # item fields where a typed figure is the point
    private: frozenset[str] = frozenset()  # item fields kept off the public site
    item_optional: frozenset[str] = frozenset()  # item fields that may be left empty
    # item fields whose value must match a pattern (a page path, a robots value)
    patterns: Mapping[str, re.Pattern[str]] = field(default_factory=dict)


# a public page of the portal (review D10)
PUBLIC_PATH = re.compile(
    r"^/(shop|tools(/(symbols|reset|simulator))?|legal/[a-z0-9][a-z0-9-]{0,39})?$"
)
ROBOTS = re.compile(r"^(index|noindex)$")

SECTIONS: dict[str, Shape] = {
    "hero": Shape({"title_line_1": 80, "title_line_2": 80, "lead": 300, "button": 40}),
    "challenges": Shape({"heading": 80}),
    "how": Shape({"heading": 80, "footnote": 300}, {"title": 60, "text": 200}, 6),
    # rule AC15 (10 Nov 2026): identity checks run at the provider (document
    # and selfie); staff edit only the words traders see at each state
    "kyc": Shape(
        {
            "required_title": 60,
            "required_text": 400,
            "pending_title": 60,
            "pending_text": 400,
            "declined_text": 300,
        }
    ),
    # rule AC09 (10 Nov 2026): figures such as traders served or amount paid
    # appear only with the evidence for each on file. The evidence (what
    # record backs the figure, and where it is kept) is required, shown to
    # staff and approvers, never on the public site. No items: the strip is
    # not shown.
    "stats": Shape(
        {"heading": 60},
        {"label": 40, "value": 16, "evidence": 300},
        4,
        figures=frozenset({"value"}),
        private=frozenset({"evidence"}),
    ),
    # rule AC11 and review D10 (12 Nov 2026): the title and description
    # search engines and link previews show. Site wide (empty: built in), and
    # one entry per page that overrides it; an empty field falls back.
    "seo": Shape(
        {"site_title": 60, "description": 160, "image": 300},
        {"path": 60, "title": 60, "description": 160, "robots": 7},
        20,
        optional=frozenset({"site_title", "description", "image"}),
        images=frozenset({"image"}),
        item_optional=frozenset({"title", "description", "robots"}),
        patterns={"path": PUBLIC_PATH, "robots": ROBOTS},
    ),
    "maintenance": Shape(
        {"title": 80, "message": 600, "image": 300},
        optional=frozenset({"image"}),
        images=frozenset({"image"}),
    ),
    "registration_closed": Shape(
        {"title": 80, "message": 600, "image": 300},
        optional=frozenset({"image"}),
        images=frozenset({"image"}),
    ),
}
# an image on the site itself ("/brand/closed.svg")
IMAGE = re.compile(r"^/[A-Za-z0-9][A-Za-z0-9/._-]*\.(svg|png|jpe?g|webp)$")

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
        texts = []
        for k, v in self.fields.items():
            if k in shape.images:
                check_image(k, v, shape.fields[k])
            elif not (k in shape.optional and v == ""):
                texts.append((k, v, shape.fields[k]))
        if len(self.items) > shape.max_items:
            raise PolicyError(f"{self.section}: at most {shape.max_items} items")
        for item in self.items:
            if set(item) != set(shape.items):
                raise PolicyError(f"{self.section}: each item has {sorted(shape.items)}")
            for k, v in item.items():
                if k in shape.patterns:
                    if not (v == "" and k in shape.item_optional) and not (
                        isinstance(v, str) and shape.patterns[k].match(v)
                    ):
                        raise PolicyError(f"{self.section}: {k} {v!r} is not allowed")
                elif k in shape.figures:
                    check_figure(k, v, shape.items[k])
                elif not (k in shape.item_optional and v == ""):
                    texts.append((k, v, shape.items[k]))
            if "path" in shape.patterns:
                paths = [i.get("path") for i in self.items]
                if len(set(paths)) != len(paths):
                    raise PolicyError(f"{self.section}: one entry per page")
        for key, text, longest in texts:
            check_text(key, text, longest)


FIGURE = re.compile(r"^[$€£]?[0-9][0-9.,]*\s?(%|[KMB]\+?|\+)?$|^[0-9]+(\.[0-9]+)?/[0-9]+$|^24/7$")


def check_figure(key: str, value: str, longest: int) -> None:
    """A statistic's figure: digits with an optional currency sign, K, M or B,
    plus or percent ("$500M+", "140+", "4.8/5"); nothing else."""
    if not isinstance(value, str) or not 1 <= len(value) <= longest:
        raise PolicyError(f"{key}: 1 to {longest} characters")
    if not FIGURE.match(value):
        raise PolicyError(f"{key}: a figure such as $500M+, 140+, 98% or 4.8/5")


def check_image(key: str, address: str, longest: int) -> None:
    """An image field: empty, or the path of an image on the site."""
    if not isinstance(address, str) or len(address) > longest:
        raise PolicyError(f"{key}: at most {longest} characters")
    if address and (".." in address or not IMAGE.match(address)):
        raise PolicyError(
            f"{key}: the path of an svg, png, jpg or webp image on the site, such as /brand/a.svg"
        )


@dataclass(frozen=True, slots=True)
class SiteMode:
    """Whether the maintenance page is up and registration is open (AC17)."""

    maintenance: bool = False
    registration_open: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.maintenance, bool) or not isinstance(self.registration_open, bool):
            raise PolicyError("maintenance and registration_open are true or false")

    @property
    def registering(self) -> bool:
        """Whether a new trader may register now."""
        return self.registration_open and not self.maintenance

    @property
    def selling(self) -> bool:
        """Whether challenges are on sale now."""
        return not self.maintenance


def mode_json(m: SiteMode) -> dict[str, Any]:
    return {"maintenance": m.maintenance, "registration_open": m.registration_open}


def mode_from_json(value: Mapping[str, Any]) -> SiteMode:
    return SiteMode(value["maintenance"], value["registration_open"])


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
    """A section as the public site shows it: tokens filled, private fields
    (a statistic's evidence) left out."""
    hidden = SECTIONS[s.section].private
    return {
        "fields": {k: render(v, values) for k, v in s.fields.items()},
        "items": [{k: render(v, values) for k, v in i.items() if k not in hidden} for i in s.items],
    }
