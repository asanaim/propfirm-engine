"""The public site for search engines and link previews (review D10, AC11)."""

from typing import Any

import pytest

from propfirm.policy import PolicyError
from propfirm.seo import (
    FAQ,
    PAGES,
    STEPS,
    ProductText,
    body_html,
    dollars,
    head_html,
    meta,
    public,
    robots_txt,
    sitemap_xml,
)
from propfirm.settings.site import SiteSection
from tests.test_settings import OWNERS, MemoryProposals, MemoryRecords, _approve, api

BASE = "https://pipridge.example"
LITE = ProductText(
    "Lite", "80", (("$10,000", "$44"),), "6% profit target, 3% daily loss, 6% maximum loss"
)


def test_public_pages_and_fallbacks() -> None:
    assert public("/", []) and public("/shop", []) and public("/legal/terms", ["terms"])
    assert not public("/legal/terms", []) and not public("/accounts", [])
    built_in = meta("/shop", None, [], {})
    assert built_in.title == PAGES["/shop"][0] and built_in.robots == "index"
    seo = {
        "fields": {
            "site_title": "pipridge prop firm",
            "description": "Site wide words",
            "image": "",
        },
        "items": [{"path": "/shop", "title": "Our challenges", "description": "", "robots": ""}],
    }
    shop = meta("/shop", seo, [], {})
    assert (shop.title, shop.description, shop.robots) == (
        "Our challenges",
        "Site wide words",
        "index",
    )
    assert (
        meta("/", seo, [], {}).title == "pipridge prop firm"
    )  # the home page takes the site title
    assert meta("/tools", seo, [], {}).title == PAGES["/tools"][0]
    terms = meta("/legal/terms", None, ["terms"], {"terms": "Terms of use"})
    assert terms.title == "Terms of use | pipridge"
    private = meta("/accounts/ACC00000001", seo, [], {})
    assert (private.robots, private.description) == ("noindex", "")


def test_the_head_is_escaped_and_complete() -> None:
    m = meta(
        "/shop",
        {
            "fields": {},
            "items": [{"path": "/shop", "title": 'A "<b>" & co', "description": "d", "robots": ""}],
        },
        [],
        {},
    )
    head = head_html("/shop", m, BASE + "/")
    assert "<title>A &quot;&lt;b&gt;&quot; &amp; co</title>" in head
    assert '<link rel="canonical" href="https://pipridge.example/shop" />' in head
    assert 'content="https://pipridge.example/brand/og-card.png"' in head
    hidden = head_html("/accounts", meta("/accounts", None, [], {}), BASE)
    assert 'content="noindex"' in hidden and "canonical" not in hidden


def test_the_page_text_before_scripts() -> None:
    home = body_html("/", {}, [LITE], "80", None, [])
    assert "Trade the evaluation. Keep 80% of the profit." in home
    assert "$10,000 account: $44" in home and FAQ[0][0] in home and STEPS[0][0] in home
    edited = {"hero": {"fields": {"title_line_1": "<Edited>", "lead": "Lead"}, "items": []}}
    assert "&lt;Edited&gt;" in body_html("/", edited, [], "80", None, [])
    terms = body_html("/legal/terms", {}, [], "", ("Terms", "One.\n\nTwo <x>."), ["terms"])
    assert terms.count("<p>") == 2 and "Two &lt;x&gt;." in terms
    assert body_html("/accounts", {}, [LITE], "80", None, []) == ""
    assert "Lite" in body_html("/shop", {}, [LITE], "80", None, [])


def test_sitemap_and_robots() -> None:
    xml = sitemap_xml(BASE, ["terms", "privacy"], ["/tools/reset"])
    assert "<loc>https://pipridge.example/legal/privacy</loc>" in xml
    assert "/tools/reset" not in xml and xml.count("<url>") == len(PAGES) - 1 + 2
    assert "Sitemap: https://pipridge.example/sitemap.xml" in robots_txt(BASE)
    assert dollars(4_950) == "$49.50" and dollars(1_000_000) == "$10,000"


def section(**items: Any) -> SiteSection:
    item = {"path": "/shop", "title": "", "description": "", "robots": "", **items}
    return SiteSection("seo", {"site_title": "", "description": "", "image": ""}, (item,))


def test_the_seo_section_checks_pages_and_robots() -> None:
    section()  # every override left empty: allowed
    section(robots="noindex", title="Challenges")
    for bad in ({"path": "/accounts"}, {"robots": "nofollow"}, {"title": "Win $500"}):
        with pytest.raises(PolicyError):
            section(**bad)
    with pytest.raises(PolicyError, match="one entry per page"):
        item = {"path": "/", "title": "", "description": "", "robots": ""}
        SiteSection("seo", {"site_title": "", "description": "", "image": ""}, (item, item))


def test_the_endpoints_serve_what_editors_approved() -> None:
    records = MemoryRecords()
    kw: dict[str, Any] = {
        "records": records,
        "proposals": MemoryProposals(records),
        "tiers": OWNERS,
    }
    head = api("GET", "/seo/head?path=/shop", signed_in=False, **kw)
    assert head.status_code == 200 and head.headers["content-type"].startswith("text/html")
    assert PAGES["/shop"][0] in head.text
    body = {
        "fields": {"site_title": "", "description": "", "image": ""},
        "items": [
            {"path": "/shop", "title": "All challenges", "description": "", "robots": "noindex"}
        ],
        "reason": "seo",
    }
    put = api("PUT", "/admin/site/seo", body, user="admin-1", **kw)
    assert put.status_code == 200, put.text
    _approve(kw, put.json()["proposal"]["id"])
    approved = api("GET", "/seo/head?path=/shop", signed_in=False, **kw).text
    assert "<title>All challenges</title>" in approved and 'content="noindex"' in approved
    sitemap = api("GET", "/sitemap.xml", signed_in=False, **kw).text
    assert "/shop<" not in sitemap and "/tools<" in sitemap
    shop = api("GET", "/seo/body?path=/shop", signed_in=False, **kw).text
    assert "Lite" in shop and "Classic" not in shop  # only what is on sale
    odd = api("GET", "/seo/head?path=/x%20y", signed_in=False, **kw)
    assert odd.status_code == 200 and 'content="noindex"' in odd.text  # never an error
    assert "Disallow: /api/" in api("GET", "/robots.txt", signed_in=False, **kw).text
