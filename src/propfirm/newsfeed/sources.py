"""Parsers for the publishers' own release schedules (M13, CC-5).

The restricted list follows FTMO's (read 31 Oct 2026); each event's date and
time comes from the body that publishes it, never from a third-party
calendar (whose display would need a licence; CC-5). Each parser turns one
page or feed into `Collected` events; fetching is the collector's job, so
these are pure functions, tested against saved excerpts of the real pages.

Event ids are `<publisher's local date>-<key>`, the same scheme as the
calendar files, so a release found by both is one event (the engine refuses
an id twice).

Times stated by the source are used; where a page gives dates only, the
publisher's fixed release time is a constant here, with where it is stated:
- Statistics Canada: 08:30 Eastern ("each working day at 8:30 a.m.
  Eastern", its release calendar);
- Reserve Bank of Australia: 14:30 Sydney on the second meeting day (RBA
  media release 2025-02);
- Bank of England: 12:00 London; ECB: 14:15 Frankfurt (decision days; the
  project outline, CC-5).

Refused automated reads when checked from the build host (31 Oct 2026):
BLS (US jobs and CPI) and the Reserve Bank of New Zealand. The collector
still tries them on every run; while they cannot be read, it alerts staff
and admins to fill those events by hand (the monthly files carry them too).
The BLS parser reads the standard iCalendar layout and was not seen against
the live feed; the RBNZ page has no parser, so it always alerts.
"""

import html
import itertools
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from propfirm.rules.calendar import utc_ms
from propfirm.rules.news import CURRENT_MAP, EventScope, RestrictedEvent

NEW_YORK, TORONTO = "America/New_York", "America/Toronto"
LONDON, FRANKFURT, ZURICH = "Europe/London", "Europe/Berlin", "Europe/Zurich"
SYDNEY, AUCKLAND = "Australia/Sydney", "Pacific/Auckland"

# key -> (title, currency or None for the oil report); FTMO's restricted events
KINDS: dict[str, tuple[str, str | None]] = {
    "fomc-decision": ("Federal Funds Rate & Statement (FOMC)", "USD"),
    "fomc-minutes": ("FOMC Meeting Minutes", "USD"),
    "us-employment": ("US Non-Farm Employment Change, Unemployment Rate & Wages", "USD"),
    "us-cpi": ("US CPI y/y", "USD"),
    "us-gdp-advance": ("US Advance GDP q/q", "USD"),
    "us-oil-inventory": ("Crude Oil Inventories (EIA)", None),
    "ecb-decision": ("ECB Main Refinancing Rate", "EUR"),
    "boe-decision": ("Bank of England Official Bank Rate & MPC Votes", "GBP"),
    "uk-cpi": ("UK CPI y/y", "GBP"),
    "boc-decision": ("Bank of Canada Overnight Rate & Rate Statement", "CAD"),
    "ca-cpi": ("Canada CPI m/m", "CAD"),
    "ca-employment": ("Canada Employment Change & Unemployment Rate", "CAD"),
    "rba-decision": ("RBA Cash Rate & Statement", "AUD"),
    "au-employment": ("Australia Employment Change & Unemployment Rate", "AUD"),
    "au-cpi": ("Australia CPI", "AUD"),
    "au-gdp": ("Australia GDP q/q", "AUD"),
    "rbnz-decision": ("RBNZ Official Cash Rate & Rate Statement", "NZD"),
    "nz-employment": ("New Zealand Employment Change & Unemployment Rate", "NZD"),
    "nz-cpi": ("New Zealand CPI q/q", "NZD"),
    "nz-gdp": ("New Zealand GDP q/q", "NZD"),
    "snb-decision": ("SNB Policy Rate", "CHF"),
}

_MONTHS = {
    m: n
    for n, m in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        start=1,
    )
}


class SourceError(ValueError):
    """A page that no longer has the shape its parser expects."""


@dataclass(frozen=True, slots=True)
class Collected:
    event: RestrictedEvent
    title: str
    local: str  # the publisher's local time, YYYY-MM-DDTHH:MM
    zone: str
    source: str  # the page or feed it was read from


def collected(key: str, day: date, hhmm: str, zone: str, source: str) -> Collected:
    title, currency = KINDS[key]
    local = f"{day.isoformat()}T{hhmm}"
    scope = EventScope.CURRENCY if currency is not None else EventScope.OIL_INVENTORY
    event = RestrictedEvent(
        f"{day.isoformat()}-{key}",
        utc_ms(local, zone),
        scope,
        currency,
        instrument_map=CURRENT_MAP,
    )
    return Collected(event, title, local, zone, source)


def text_lines(page: str) -> list[str]:
    """The visible text of an HTML page, one non-empty line per element."""
    page = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", page)
    page = html.unescape(re.sub(r"(?s)<[^>]+>", "\n", page))
    page = page.replace("\xa0", " ")  # no-break spaces read as spaces
    return [line.strip() for line in page.split("\n") if line.strip()]


def _month(name: str) -> int:
    try:
        return _MONTHS[name.strip().lower()]
    except KeyError:
        raise SourceError(f"not a month: {name!r}") from None


def _clock(text: str) -> str:
    """ "2:00 p.m." / "11:30am" / "8:30 AM" / "09:45" -> "HH:MM"."""
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})\s*([ap])?\.?\s*m?\.?", text.strip().lower())
    if m is None:
        raise SourceError(f"not a time: {text!r}")
    hour, minute = int(m[1]), int(m[2])
    if m[3] == "p" and hour != 12:
        hour += 12
    elif m[3] == "a" and hour == 12:
        hour = 0
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise SourceError(f"not a time: {text!r}")
    return f"{hour:02d}:{minute:02d}"


# --- Federal Reserve: monthly events calendar --------------------------------------------

FED_MONTH = "https://www.federalreserve.gov/newsevents/{year}-{month}.htm"

_FED_ROW = re.compile(
    r'<div class="col-xs-2">\s*<p>([^<]+)</p>\s*</div>\s*'
    r'<div class="col-xs-7">\s*<p>(?:<a[^>]*>)?\s*([^<]+?)\s*(?:</a>)?\s*</p>.*?'
    r'<div class="col-xs-3">\s*<p>(\d{1,2})</p>',
    re.S,
)


def fed_month_url(year: int, month: int) -> str:
    name = next(m for m, n in _MONTHS.items() if n == month)
    return FED_MONTH.format(year=year, month=name)


def parse_fed_month(page: str, year: int, month: int, source: str) -> list[Collected]:
    """FOMC statements ("FOMC Meeting", released 2:00 p.m. on the decision
    day) and minutes ("FOMC Minutes") of one month's events page."""
    if "FOMC" not in page:
        raise SourceError("no FOMC section")
    out = []
    for time_text, title, day in _FED_ROW.findall(page):
        key = {"FOMC Meeting": "fomc-decision", "FOMC Minutes": "fomc-minutes"}.get(title.strip())
        if key is not None:
            out.append(
                collected(key, date(year, month, int(day)), _clock(time_text), NEW_YORK, source)
            )
    return out


# --- Statistics Canada: key indicators schedule (JSON) ------------------------------------

STATCAN = "https://www150.statcan.gc.ca/n1/dai-quo/ssi/homepage/schedule-key_indicators-eng.json"


def parse_statcan(text: str, source: str = STATCAN) -> list[Collected]:
    """Consumer Price Index and Labour Force Survey release days; The Daily
    publishes at 08:30 Eastern."""
    data = json.loads(text)
    if not isinstance(data, list):
        raise SourceError("expected a list")
    out = []
    for item in data:
        title = str(item.get("title", "")).strip()
        key = {"Consumer Price Index": "ca-cpi", "Labour Force Survey": "ca-employment"}.get(title)
        if key is None:
            continue
        day = date.fromisoformat(str(item["date"])[:10])
        out.append(collected(key, day, "08:30", TORONTO, source))
    return out


# --- Stats NZ: release calendar (iCalendar) ------------------------------------------------

STATS_NZ = "https://www.stats.govt.nz/release-calendar/calendar-export/"


def parse_stats_nz(text: str, source: str = STATS_NZ) -> list[Collected]:
    """Quarterly labour market, GDP and CPI releases (FTMO: CPI q/q; the
    monthly CPI is not restricted)."""
    unfolded = text.replace("\r\n", "\n").replace("\n ", "")
    if "BEGIN:VCALENDAR" not in unfolded:
        raise SourceError("not an iCalendar file")
    out = []
    for block in unfolded.split("BEGIN:VEVENT")[1:]:
        summary = re.search(r"^SUMMARY[^:\n]*:(.*)$", block, re.M)
        start = re.search(r"^DTSTART;TZID=([^:\n]+):(\d{8})T(\d{4})", block, re.M)
        if summary is None or start is None:
            continue
        title = summary[1].strip()
        if re.match(r"Labour market statistics: \w+ \d{4} quarter$", title):
            key = "nz-employment"
        elif re.match(r"Gross domestic product: \w+ \d{4} quarter$", title):
            key = "nz-gdp"
        elif re.match(r"Consumers price index: \w+ \d{4} quarter$", title):
            key = "nz-cpi"
        else:
            continue
        stamp = start[2]
        day = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
        out.append(collected(key, day, f"{start[3][:2]}:{start[3][2:]}", start[1], source))
    return out


# --- Office for National Statistics: release calendar API ---------------------------------

ONS = (
    "https://api.beta.ons.gov.uk/v1/search/releases?limit=500&release-type=type-upcoming"
    "&fromDate={start}&toDate={end}"
)


def parse_ons(text: str, source: str) -> list[Collected]:
    """ "Consumer price inflation, UK: <month>" (not the time-series release);
    the API gives the release time in UTC."""
    data = json.loads(text)
    out = []
    for release in data.get("releases", []):
        desc = release.get("description", {})
        if desc.get("cancelled"):
            continue
        if not re.fullmatch(r"Consumer price inflation, UK: \w+ \d{4}", desc.get("title", "")):
            continue
        at = datetime.strptime(desc["release_date"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
        local = at.astimezone(ZoneInfo(LONDON))
        out.append(collected("uk-cpi", local.date(), local.strftime("%H:%M"), LONDON, source))
    return out


# --- Australian Bureau of Statistics: future releases by month ------------------------------

ABS = "https://www.abs.gov.au/release-calendar/future-releases/{yyyymm}/{theme}"
ABS_THEMES = ("rcc_economy", "rcc_labour")
_ABS_TITLES = {
    "Consumer Price Index, Australia": "au-cpi",
    "Labour Force, Australia": "au-employment",
    "Australian National Accounts: National Income, Expenditure and Product": "au-gdp",
}
_ABS_WHEN = re.compile(r"\w+day (\d{1,2}) (\w+) (\d{4}) (\d{1,2}:\d{2}[ap]m) A[EC][DS]T")


def parse_abs(page: str, source: str) -> list[Collected]:
    """Each release is a line "Wednesday 25 November 2026 11:30am AEDT"
    followed by its title; all times are Canberra time."""
    lines = text_lines(page)
    if not any("Canberra time" in line for line in lines):
        raise SourceError("ABS calendar page not recognised")
    out = []
    for when, title in itertools.pairwise(lines):
        m = _ABS_WHEN.fullmatch(when)
        key = _ABS_TITLES.get(title)
        if m is None or key is None:
            continue
        day = date(int(m[3]), _month(m[2]), int(m[1]))
        out.append(collected(key, day, _clock(m[4]), SYDNEY, source))
    return out


# --- Bank of Canada: upcoming events --------------------------------------------------------

BOC = "https://www.bankofcanada.ca/press/upcoming-events/"


def parse_boc(page: str, source: str = BOC) -> list[Collected]:
    """ "December 9, 2026" / "Interest Rate Announcement" / "09:45 (ET)"."""
    lines = text_lines(page)
    out = []
    for i, line in enumerate(lines):
        if line != "Interest Rate Announcement" or i == 0 or i + 1 >= len(lines):
            continue
        m = re.fullmatch(r"(\w+) (\d{1,2}), (\d{4})", lines[i - 1])
        t = re.fullmatch(r"(\d{1,2}:\d{2}) \(ET\)", lines[i + 1])
        if m is None or t is None:
            raise SourceError(f"announcement without a date and time near {lines[i - 1]!r}")
        day = date(int(m[3]), _month(m[1]), int(m[2]))
        out.append(collected("boc-decision", day, _clock(t[1]), TORONTO, source))
    return out


# --- Reserve Bank of Australia: board meeting schedules -------------------------------------

RBA = "https://www.rba.gov.au/schedules-events/board-meeting-schedules.html"
_RBA_TABLE = re.compile(r"Board meeting schedules (\d{4})\s*</caption>(.*?)</table>", re.S)
_RBA_ROW = re.compile(r'<th scope="row">\w+</th>\s*<td>([^<]*)</td>', re.S)


def parse_rba(page: str, source: str = RBA) -> list[Collected]:
    """Monetary Policy Board meetings ("2&ndash;3 November"): the outcome is
    announced at 14:30 Sydney on the second day."""
    tables = _RBA_TABLE.findall(page)
    if not tables:
        raise SourceError("no board meeting schedule")
    out = []
    for year, body in tables:
        for cell in _RBA_ROW.findall(body):
            text = html.unescape(cell).strip()
            if not text:
                continue
            m = re.fullmatch(r"(?:\d{1,2}\s*\w*\s*[\u2013-]\s*)?(\d{1,2}) (\w+)", text)
            if m is None:
                raise SourceError(f"meeting dates not understood: {text!r}")
            day = date(int(year), _month(m[2]), int(m[1]))
            out.append(collected("rba-decision", day, "14:30", SYDNEY, source))
    return out


# --- Swiss National Bank: event schedule ----------------------------------------------------

SNB = "https://www.snb.ch/en/services-events/digital-services/event-schedule"


def parse_snb(page: str, source: str = SNB) -> list[Collected]:
    """ "10.12.2026" / "09:30" / "Monetary policy assessment of ... (press
    release)": the decision is the press release."""
    lines = text_lines(page)
    out = []
    for i in range(2, len(lines)):
        title = lines[i]
        if not (title.startswith("Monetary policy assessment of") and "(press release)" in title):
            continue
        d, t = lines[i - 2], lines[i - 1]
        m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", d)
        if m is None:
            raise SourceError(f"no date before {title!r}")
        day = date(int(m[3]), int(m[2]), int(m[1]))
        out.append(collected("snb-decision", day, _clock(t), ZURICH, source))
    return out


# --- Bank of England: Monetary Policy Committee dates ---------------------------------------

BOE = "https://www.bankofengland.co.uk/monetary-policy/upcoming-mpc-dates"


def parse_boe(page: str, source: str = BOE) -> list[Collected]:
    """ "2026 confirmed dates" then "Thursday 5 November" rows; decisions at
    12:00 London."""
    out = []
    for year, body in re.findall(
        r"<h2>(\d{4}) confirmed dates</h2>\s*<table>(.*?)</table>", page, re.S
    ):
        for cell in re.findall(r"<tr>\s*<td>([^<]+)</td>", body):
            m = re.fullmatch(r"\w+day\s+(\d{1,2})\s+(\w+)", html.unescape(cell).strip())
            if m is None:
                raise SourceError(f"MPC date not understood: {cell!r}")
            day = date(int(year), _month(m[2]), int(m[1]))
            out.append(collected("boe-decision", day, "12:00", LONDON, source))
    if not out:
        raise SourceError("no MPC dates")
    return out


# --- European Central Bank: Governing Council meetings --------------------------------------

ECB = "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html"


def parse_ecb(page: str, source: str = ECB) -> list[Collected]:
    """ "17/12/2026" then "...monetary policy meeting in Frankfurt (Day 2),
    followed by press conference": the decision day; 14:15 Frankfurt."""
    lines = text_lines(page)
    out = []
    for i in range(1, len(lines)):
        if "monetary policy meeting" not in lines[i] or "press conference" not in lines[i]:
            continue
        m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", lines[i - 1])
        if m is None:
            raise SourceError(f"no date before {lines[i]!r}")
        day = date(int(m[3]), int(m[2]), int(m[1]))
        out.append(collected("ecb-decision", day, "14:15", FRANKFURT, source))
    return out


# --- US Bureau of Economic Analysis: release schedule ---------------------------------------

BEA = "https://www.bea.gov/news/schedule"


def parse_bea(page: str, source: str = BEA) -> list[Collected]:
    """Advance GDP estimates only (FTMO: "Advance GDP q/q"): "Year 2026",
    then "October 29" / "8:30 AM" / ... / "GDP (Advance Estimate), ..."."""
    lines = text_lines(page)
    out = []
    year: int | None = None
    when: tuple[int, int, str] | None = None
    for i, line in enumerate(lines):
        y = re.fullmatch(r"Year (\d{4})", line)
        if y is not None:
            year = int(y[1])
            continue
        m = re.fullmatch(r"(\w+) (\d{1,2})", line)
        if m is not None and m[1].lower() in _MONTHS and i + 1 < len(lines):
            if re.fullmatch(r"\d{1,2}:\d{2} [AP]M", lines[i + 1]):
                when = (_month(m[1]), int(m[2]), _clock(lines[i + 1]))
            continue
        if line.startswith("GDP (Advance Estimate)") and when is not None and year is not None:
            month, day, hhmm = when
            out.append(collected("us-gdp-advance", date(year, month, day), hhmm, NEW_YORK, source))
    return out


# --- US Energy Information Administration: Weekly Petroleum Status Report -------------------

EIA = "https://www.eia.gov/petroleum/supply/weekly/schedule.php"
_EIA_ROW = re.compile(
    r'<th scope="row">([^<]+)</th>\s*<td[^>]*>([^<]+)</td>\s*<td>[^<]*</td>\s*<td>([^<]+)</td>',
    re.S,
)


def _us_date(text: str) -> date:
    return datetime.strptime(text.strip(), "%B %d, %Y").date()


def parse_eia(page: str, start: date, end: date, source: str = EIA) -> list[Collected]:
    """Every week's report from `start` to `end`: Wednesday 10:30 Eastern,
    except the holiday weeks the page lists (data week ending -> alternate
    date and time)."""
    if "10:30 a.m. eastern time on Wednesday" not in re.sub(r"\s+", " ", page):
        raise SourceError("EIA standard schedule not found")
    moved: dict[date, tuple[date, str]] = {}
    for week_ending, alternate, at in _EIA_ROW.findall(page):
        moved[_us_date(week_ending)] = (_us_date(alternate), _clock(at))
    out = []
    friday = start - timedelta(days=(start.weekday() - 4) % 7 + 7)  # a data week ending
    while friday <= end:
        day, hhmm = moved.get(friday, (friday + timedelta(days=5), "10:30"))
        if start <= day <= end:
            out.append(collected("us-oil-inventory", day, hhmm, NEW_YORK, source))
        friday += timedelta(days=7)
    return out


# --- US Bureau of Labor Statistics: release calendar (iCalendar) ---------------------------

BLS = "https://www.bls.gov/schedule/news_release/bls.ics"


def parse_bls(text: str, source: str = BLS) -> list[Collected]:
    """ "Employment Situation" (jobs, unemployment rate and earnings in one
    release) and "Consumer Price Index" events. DTSTART with a TZID, in UTC
    (Z) or floating (read as New York time)."""
    unfolded = text.replace("\r\n", "\n").replace("\n ", "")
    if "BEGIN:VCALENDAR" not in unfolded:
        raise SourceError("not an iCalendar file")
    out = []
    for block in unfolded.split("BEGIN:VEVENT")[1:]:
        summary = re.search(r"^SUMMARY[^:\n]*:(.*)$", block, re.M)
        start = re.search(
            r"^DTSTART(?:;TZID=([^:;\n]+))?[^:\n]*:(\d{8})T(\d{4})\d{0,2}(Z?)", block, re.M
        )
        if summary is None or start is None:
            continue
        title = summary[1].strip()
        if title.startswith("Employment Situation"):
            key = "us-employment"
        elif re.match(r"Consumer Price Index\b", title):
            key = "us-cpi"
        else:
            continue
        zone = "UTC" if start[4] else (start[1] or NEW_YORK)
        at = datetime.strptime(start[2] + start[3], "%Y%m%d%H%M").replace(tzinfo=ZoneInfo(zone))
        local = at.astimezone(ZoneInfo(NEW_YORK))
        out.append(collected(key, local.date(), local.strftime("%H:%M"), NEW_YORK, source))
    if not out:
        raise SourceError("no Employment Situation or CPI release found")
    return out


# --- Reserve Bank of New Zealand -----------------------------------------------------------

RBNZ = "https://www.rbnz.govt.nz/news-and-events/how-we-release-information/ocr-decision-dates"


def parse_rbnz(page: str, source: str = RBNZ) -> list[Collected]:
    """No parser: the page refused automated reads when checked, so its
    layout is unknown. Staff add RBNZ decisions by hand (alerted)."""
    raise SourceError("the RBNZ page is not read automatically: add its decisions by hand")


def unique(events: Iterable[Collected]) -> list[Collected]:
    """One per event id (the first), soonest first."""
    seen: dict[str, Collected] = {}
    for item in events:
        seen.setdefault(item.event.event_id, item)
    return sorted(seen.values(), key=lambda c: (c.event.ts, c.event.event_id))


def as_json(item: Collected) -> dict[str, Any]:
    return {
        "event_id": item.event.event_id,
        "title": item.title,
        "ts": item.event.ts,
        "local": item.local,
        "zone": item.zone,
        "source": item.source,
    }
