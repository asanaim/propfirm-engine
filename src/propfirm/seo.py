"""The public site as search engines and link previews see it (review D10,
rule AC11; 12 Nov 2026).

The portal draws its pages in the browser, so a crawler that runs no
script saw an empty page with one title. The web server (Caddy) now asks
the API, for every page it serves, for:

- the page's head: title, description, robots, canonical address and the
  social card (Open Graph) image, from the `seo` website section: one
  entry per public page, each empty field falling back to the site wide
  one (AC11);
- the page's text: the home page, the challenges with their figures, the
  tools, and each policy page, written into the page before any script
  runs. The app then draws over it as before.

and serves a sitemap of the public pages and a robots.txt. Everything put
into HTML is escaped here. Pages behind sign-in get `noindex` and no text.

The home page's built-in wording is kept in step with the portal's own
(`portal/src/pages/OverviewPage.tsx`; a portal test compares them).
"""

import html
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from xml.sax.saxutils import escape as xml_escape

# public pages: path -> (built-in title, built-in description)
PAGES: dict[str, tuple[str, str]] = {
    "/": (
        "pipridge: trade the evaluation, keep most of the profit",
        "Prove your trading on a simulated account. Pass the evaluation and trade a "
        "funded account with a share of every profit.",
    ),
    "/shop": (
        "Challenges and prices | pipridge",
        "Each pipridge challenge with its account sizes, prices, profit targets and loss limits.",
    ),
    "/tools": (
        "Trading calculators | pipridge",
        "Free position size, pip value, margin and profit calculators for every symbol.",
    ),
    "/tools/symbols": (
        "Symbols and contract sizes | pipridge",
        "Every symbol traded at pipridge with its contract size, leverage and pip size.",
    ),
    "/tools/reset": (
        "Daily reset time converter | pipridge",
        "When the 17:00 New York trading day reset falls in your own time zone.",
    ),
    "/tools/simulator": (
        "Equity simulator | pipridge",
        "Simulate an account's equity from a win rate, a reward to risk ratio and a risk "
        "per trade.",
    ),
}
LEGAL = "/legal/"
SITE_NAME = "pipridge"

# the portal's built-in wording (OverviewPage.tsx), used when a section has
# no approved text
HERO = (
    "Trade the evaluation.",
    "Keep most of the profit.",
    "Prove your trading on a simulated account. Pass, and trade a funded account with a "
    "share of every profit.",
)
STEPS: tuple[tuple[str, str], ...] = (
    ("Choose a challenge", "Pick the evaluation and account size that suit how you trade."),
    (
        "Pass the evaluation",
        "Reach the profit target without breaking the daily or maximum loss.",
    ),
    ("Trade a funded account", "Same rules, simulated capital, no evaluation target."),
    ("Get paid", "Request a payout of your share of the profit."),
)
FOOTNOTE = "All trading here is simulated. Each challenge's rules are shown before you buy."
FAQ: tuple[tuple[str, str], ...] = (
    (
        "Is the trading real?",
        "No. Every account here, in the evaluation and when funded, is simulated. You trade "
        "real market prices without risking your own money beyond the challenge fee.",
    ),
    (
        "How do I join?",
        "Press Get started, create your account with your email and a password, and add an "
        "authenticator app. Then choose a challenge and pay for it.",
    ),
    (
        "What are the rules?",
        "Each challenge shows its profit target, daily loss limit and maximum loss before you "
        "buy, and your account page shows them while you trade. The trading day resets at "
        "17:00 New York time.",
    ),
    (
        "What happens when I pass?",
        "We check your identity with our provider (a photo ID and a selfie), review the "
        "account, and you sign the funded agreement. Then your funded account opens.",
    ),
    (
        "How am I paid?",
        "From a funded account you request a payout of your share of the profit. It is sent "
        "to your USDT wallet on the TRON network (TRC-20).",
    ),
    (
        "Can I trade during news?",
        "On funded accounts, trading around the restricted high-impact releases shown in the "
        "economic calendar is not allowed. Evaluations are not restricted.",
    ),
)


@dataclass(frozen=True, slots=True)
class Meta:
    title: str
    description: str
    robots: str  # "index" or "noindex"
    image: str  # a path on the site, or ""


def dollars(cents: int) -> str:
    """Whole dollars when exact, else dollars and cents: "$100,000", "$49.50"
    (integer arithmetic, no rounding)."""
    whole, rest = divmod(cents, 100)
    return f"${whole:,}" if rest == 0 else f"${whole:,}.{rest:02d}"


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def public(path: str, slugs: Sequence[str]) -> bool:
    """Whether `path` is a public page (one the sitemap lists)."""
    return path in PAGES or (path.startswith(LEGAL) and path[len(LEGAL) :] in slugs)


def meta(
    path: str,
    seo: Mapping[str, object] | None,
    slugs: Sequence[str],
    page_titles: Mapping[str, str],
) -> Meta:
    """The page's title, description, robots and image: the page's own
    entry in the `seo` section, else the site wide one, else built in."""
    fields: Mapping[str, str] = {}
    items: Sequence[Mapping[str, str]] = ()
    if seo is not None:
        raw_fields, raw_items = seo.get("fields"), seo.get("items")
        fields = raw_fields if isinstance(raw_fields, Mapping) else {}
        items = raw_items if isinstance(raw_items, list) else ()
    own = next((i for i in items if i.get("path") == path), {})
    if not public(path, slugs):
        return Meta(fields.get("site_title") or SITE_NAME, "", "noindex", "")
    if path in PAGES:
        title, description = PAGES[path]
    else:
        page = page_titles.get(path[len(LEGAL) :], "Policy")
        title, description = f"{page} | {SITE_NAME}", f"The {page} of {SITE_NAME}."
    return Meta(
        title=own.get("title") or (fields.get("site_title") if path == "/" else "") or title,
        description=own.get("description") or fields.get("description") or description,
        robots=own.get("robots") or "index",
        image=fields.get("image") or "/brand/og-card.png",
    )


def head_html(path: str, m: Meta, base_url: str) -> str:
    """The tags for the page's <head>."""
    url = base_url.rstrip("/") + path
    tags = [
        f"<title>{_e(m.title)}</title>",
        f'<meta name="robots" content="{_e(m.robots)}" />',
    ]
    if m.description:
        tags.append(f'<meta name="description" content="{_e(m.description)}" />')
    if m.robots == "index":
        tags += [
            f'<link rel="canonical" href="{_e(url)}" />',
            '<meta property="og:type" content="website" />',
            f'<meta property="og:site_name" content="{_e(SITE_NAME)}" />',
            f'<meta property="og:title" content="{_e(m.title)}" />',
            f'<meta property="og:description" content="{_e(m.description)}" />',
            f'<meta property="og:url" content="{_e(url)}" />',
            '<meta name="twitter:card" content="summary_large_image" />',
        ]
        if m.image:
            image = base_url.rstrip("/") + m.image
            tags.append(f'<meta property="og:image" content="{_e(image)}" />')
    return "\n".join(tags)


@dataclass(frozen=True, slots=True)
class ProductText:
    name: str
    split: str  # "80"
    sizes: tuple[tuple[str, str], ...]  # ("$10,000", "$44")
    rules: str  # "6% target, 3% daily loss, 6% maximum loss"


def _section(
    site: Mapping[str, object], name: str
) -> tuple[Mapping[str, str], list[Mapping[str, str]]]:
    raw = site.get(name)
    if not isinstance(raw, Mapping):
        return {}, []
    fields, items = raw.get("fields"), raw.get("items")
    return (
        fields if isinstance(fields, Mapping) else {},
        [i for i in items if isinstance(i, Mapping)] if isinstance(items, list) else [],
    )


def _products(products: Sequence[ProductText]) -> str:
    out = []
    for p in products:
        rows = "".join(f"<li>{_e(size)} account: {_e(price)}</li>" for size, price in p.sizes)
        out.append(
            f"<article><h3>{_e(p.name)}</h3><p>{_e(p.split)}% profit split. {_e(p.rules)}.</p>"
            f"<ul>{rows}</ul></article>"
        )
    return "".join(out)


def body_html(
    path: str,
    site: Mapping[str, object],
    products: Sequence[ProductText],
    split: str,
    page: tuple[str, str] | None,
    slugs: Sequence[str],
) -> str:
    """The page's text, before any script runs (empty for pages behind
    sign-in). `site`: the approved sections with figures filled in;
    `split`: the highest profit split; `page`: a policy page's title and
    text."""
    if not public(path, slugs):
        return ""
    if path.startswith(LEGAL):
        if page is None:
            return ""
        title, text = page
        paragraphs = "".join(f"<p>{_e(p)}</p>" for p in text.split("\n\n") if p.strip())
        return f'<main class="prerendered"><h1>{_e(title)}</h1>{paragraphs}</main>'
    if path == "/shop":
        return (
            '<main class="prerendered"><h1>Challenges</h1>'
            "<p>Pass the evaluation, then trade a funded account and keep most of the profit.</p>"
            f"{_products(products)}</main>"
        )
    if path.startswith("/tools"):
        title, description = PAGES[path]
        return (
            f'<main class="prerendered"><h1>{_e(title.split(" | ")[0])}</h1>'
            f"<p>{_e(description)}</p></main>"
        )
    hero, _ = _section(site, "hero")
    how, steps = _section(site, "how")
    line_2 = hero.get("title_line_2") or (f"Keep {split}% of the profit." if split else HERO[1])
    step_list = [(s.get("title", ""), s.get("text", "")) for s in steps] or list(STEPS)
    return (
        '<main class="prerendered">'
        f"<h1>{_e(hero.get('title_line_1') or HERO[0])} {_e(line_2)}</h1>"
        f"<p>{_e(hero.get('lead') or HERO[2])}</p>"
        f"<h2>Challenges</h2>{_products(products)}"
        f"<h2>{_e(how.get('heading') or 'How it works')}</h2><ol>"
        + "".join(f"<li><h3>{_e(t)}</h3><p>{_e(x)}</p></li>" for t, x in step_list)
        + f"</ol><p>{_e(how.get('footnote') or FOOTNOTE)}</p>"
        "<h2>Questions</h2>"
        + "".join(f"<h3>{_e(q)}</h3><p>{_e(a)}</p>" for q, a in FAQ)
        + "</main>"
    )


def sitemap_xml(base_url: str, slugs: Sequence[str], seo_noindex: Sequence[str] = ()) -> str:
    """Every public page except those an editor set to noindex."""
    base = base_url.rstrip("/")
    paths = [p for p in [*PAGES, *(LEGAL + s for s in sorted(slugs))] if p not in seo_noindex]
    urls = "".join(f"<url><loc>{xml_escape(base + p)}</loc></url>" for p in paths)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    )


def robots_txt(base_url: str) -> str:
    """Crawl the public pages; not the API or the pages behind sign-in."""
    return (
        "User-agent: *\n"
        "Disallow: /api/\n"
        "Disallow: /accounts\n"
        "Disallow: /payouts\n"
        "Disallow: /funded\n"
        "Disallow: /security\n"
        f"Sitemap: {base_url.rstrip('/')}/sitemap.xml\n"
    )
