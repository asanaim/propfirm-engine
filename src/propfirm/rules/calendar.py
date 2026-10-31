"""The firm's restricted-events calendar files (M13, CC-5).

One TOML file per month in `config/news/` (the operator builds it from the
publishers' own schedules and updates it monthly; git keeps every version):

    version = "news-2026-11-v1"     # unique per file and per change
    month = "2026-11"
    checked = "2026-10-31"          # when the sources were last read

    [[event]]                       # a release at a fixed time
    id = "2026-11-06-us-employment" # becomes the engine's event id
    title = "US employment situation (October)"
    scope = "currency"              # or "oil_inventory" (no currency)
    currency = "USD"
    local = "2026-11-06T08:30"      # the publisher's local time
    zone = "America/New_York"
    source = "https://..."          # where the date and time come from

    [[unscheduled]]                 # no fixed time (Bank of Japan): shown to
    date = "2026-12-18"             # staff; the window opens when the risk
    title = "Bank of Japan rate decision"  # desk marks it published
    currency = "JPY"
    source = "https://..."

Local times convert to UTC here, at the edge, with the IANA time-zone
database; a local time that does not exist or occurs twice (a daylight
saving change) is refused rather than guessed. Every window is the
[CONFIRMED] 2 minutes before to 2 minutes after; files cannot change it.
"""

import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from propfirm.rules.news import CURRENT_MAP, EventScope, RestrictedEvent

DEFAULT_NEWS_DIR = Path(__file__).resolve().parents[3] / "config" / "news"
EVENT_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
CURRENCIES = frozenset({"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"})
_VERSION = re.compile(r"news-\d{4}-\d{2}-v[1-9][0-9]*")
_MONTH = re.compile(r"\d{4}-\d{2}")
_LOCAL = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


class CalendarError(ValueError):
    """A calendar file that cannot be used as written."""


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    event: RestrictedEvent
    title: str
    local: str  # as written, with its zone, for people
    zone: str
    source: str


@dataclass(frozen=True, slots=True)
class Unscheduled:
    date: str
    title: str
    currency: str
    source: str


@dataclass(frozen=True, slots=True)
class Calendar:
    version: str
    month: str
    checked: str
    events: tuple[CalendarEvent, ...]  # by time, then id
    unscheduled: tuple[Unscheduled, ...]


def utc_ms(local: str, zone: str) -> int:
    """`local` ("YYYY-MM-DDTHH:MM") in `zone` as ms UTC; refuses times a
    daylight-saving change skips or repeats."""
    if not _LOCAL.fullmatch(local):
        raise CalendarError(f"{local!r}: write the local time as YYYY-MM-DDTHH:MM")
    try:
        tz = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CalendarError(f"unknown time zone {zone!r}") from exc
    naive = datetime.fromisoformat(local)
    first, second = naive.replace(tzinfo=tz, fold=0), naive.replace(tzinfo=tz, fold=1)
    if first.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != naive:
        raise CalendarError(f"{local} {zone} does not exist (clocks go forward)")
    if first.utcoffset() != second.utcoffset():
        raise CalendarError(f"{local} {zone} occurs twice (clocks go back)")
    return int(first.timestamp()) * 1000


def _text(table: Mapping[str, Any], key: str, where: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise CalendarError(f"{where}: {key} must be non-empty text")
    return value


def _keys(table: Mapping[str, Any], allowed: set[str], where: str) -> None:
    extra = set(table) - allowed
    if extra:
        raise CalendarError(f"{where}: unknown keys {sorted(extra)}")


def _currency(table: Mapping[str, Any], where: str) -> str:
    currency = _text(table, "currency", where)
    if currency not in CURRENCIES:
        raise CalendarError(f"{where}: currency must be one of {sorted(CURRENCIES)}")
    return currency


def _event(table: Mapping[str, Any], month: str, n: int) -> CalendarEvent:
    where = f"event {n}"
    _keys(table, {"id", "title", "scope", "currency", "local", "zone", "source"}, where)
    event_id = _text(table, "id", where)
    if not EVENT_ID.fullmatch(event_id):
        raise CalendarError(f"{where}: id must match {EVENT_ID.pattern}")
    local, zone = _text(table, "local", where), _text(table, "zone", where)
    if not local.startswith(month):
        raise CalendarError(f"{where}: {local} is not in {month}")
    scope_text = _text(table, "scope", where)
    try:
        scope = EventScope(scope_text)
    except ValueError as exc:
        raise CalendarError(f"{where}: scope must be currency or oil_inventory") from exc
    currency = _currency(table, where) if scope is EventScope.CURRENCY else None
    if scope is EventScope.OIL_INVENTORY and "currency" in table:
        raise CalendarError(f"{where}: an oil inventory event has no currency")
    source = _text(table, "source", where)
    if not source.startswith("https://"):
        raise CalendarError(f"{where}: source must be an https:// address")
    restricted = RestrictedEvent(
        event_id, utc_ms(local, zone), scope, currency, instrument_map=CURRENT_MAP
    )
    return CalendarEvent(restricted, _text(table, "title", where), local, zone, source)


def _unscheduled(table: Mapping[str, Any], month: str, n: int) -> Unscheduled:
    where = f"unscheduled {n}"
    _keys(table, {"date", "title", "currency", "source"}, where)
    day = _text(table, "date", where)
    try:
        date.fromisoformat(day)
    except ValueError as exc:
        raise CalendarError(f"{where}: date must be YYYY-MM-DD") from exc
    if not day.startswith(month):
        raise CalendarError(f"{where}: {day} is not in {month}")
    return Unscheduled(
        day, _text(table, "title", where), _currency(table, where), _text(table, "source", where)
    )


def parse_calendar(text: str) -> Calendar:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise CalendarError(f"not TOML: {exc}") from exc
    _keys(data, {"version", "month", "checked", "event", "unscheduled"}, "calendar")
    version, month = _text(data, "version", "calendar"), _text(data, "month", "calendar")
    if not _VERSION.fullmatch(version) or not version.startswith(f"news-{month}-"):
        raise CalendarError("version must be news-<month>-v<n>")
    if not _MONTH.fullmatch(month):
        raise CalendarError("month must be YYYY-MM")
    checked = _text(data, "checked", "calendar")
    try:
        date.fromisoformat(checked)
    except ValueError as exc:
        raise CalendarError("checked must be YYYY-MM-DD") from exc
    raw_events, raw_unscheduled = data.get("event", []), data.get("unscheduled", [])
    if not isinstance(raw_events, list) or not isinstance(raw_unscheduled, list):
        raise CalendarError("event and unscheduled must be arrays of tables")
    events = [_event(t, month, n) for n, t in enumerate(raw_events, start=1)]
    ids = [e.event.event_id for e in events]
    if len(set(ids)) != len(ids):
        raise CalendarError("event ids must be unique")
    unscheduled = [_unscheduled(t, month, n) for n, t in enumerate(raw_unscheduled, start=1)]
    events.sort(key=lambda e: (e.event.ts, e.event.event_id))
    return Calendar(version, month, checked, tuple(events), tuple(unscheduled))


def load_calendars(directory: Path) -> tuple[Calendar, ...]:
    """Every `YYYY-MM.toml` in `directory`, by month; one file per month,
    event ids unique across all of them."""
    calendars = []
    for path in sorted(directory.glob("*.toml")):
        if not _MONTH.fullmatch(path.stem):
            raise CalendarError(f"{path.name}: name calendar files YYYY-MM.toml")
        try:
            calendar = parse_calendar(path.read_text(encoding="utf-8"))
        except CalendarError as exc:
            raise CalendarError(f"{path.name}: {exc}") from exc
        if calendar.month != path.stem:
            raise CalendarError(f"{path.name}: month {calendar.month} differs from the name")
        calendars.append(calendar)
    _unique(calendars)
    return tuple(calendars)


def _unique(calendars: Iterable[Calendar]) -> None:
    seen: set[str] = set()
    for calendar in calendars:
        for item in calendar.events:
            if item.event.event_id in seen:
                raise CalendarError(f"event id {item.event.event_id} is in two files")
            seen.add(item.event.event_id)
