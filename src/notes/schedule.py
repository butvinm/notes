"""Schedules: the canonical grammar written in frontmatter, occurrence math for `tick`, and relative input for people.

Canonical text is `at <timestamp>` for a one-shot schedule and `every <N> <unit> from <timestamp>` for a recurring one,
where the timestamp is ISO 8601 with seconds and a `+HH:MM` offset, for example `at 2026-09-09T10:00:00+03:00`.
`parse` is slightly lenient (singular unit names, `Z`, omitted seconds) and `format` always writes the canonical text,
so a schedule reconstructed from the database means the same instant it meant in the file.
Relative forms such as `in 3 days` are accepted only by `normalize_input`,
which turns them into canonical text at the moment a note is created or a draft is saved.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo

UNITS = ("minutes", "hours", "days", "weeks")
# The time of day for `tomorrow` and bare dates given without a time: a morning notification, not a midnight one.
DEFAULT_TIME = time(9, 0)

_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?)(Z|[+-]\d{2}:\d{2})?$")
_COUNT_RE = re.compile(r"^[0-9]+$")
_CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_AT_RE = re.compile(r"^at\s+(.+)$")
_EVERY_RE = re.compile(r"^every\s+(\S+)\s+(\S+)\s+from\s+(.+)$")
_IN_RE = re.compile(r"^in\s+(\S+)\s+(\S+)$")
_TOMORROW_RE = re.compile(r"^tomorrow(?:\s+(\S+))?$")
_TODAY_RE = re.compile(r"^today\s+(\S+)$")
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:\s+(\S+))?$")
_EVERY_SHORT_RE = re.compile(r"^every\s+(\S+)\s+(\S+)$")

GRAMMAR_HINT = "write `at <timestamp>` or `every <N> <minutes|hours|days|weeks> from <timestamp>`"
INPUT_HINT = (
    "write `at <timestamp>`, `every <N> <unit> from <timestamp>`, `in <N> <unit>`, `tomorrow [HH:MM]`, "
    "`today HH:MM`, `YYYY-MM-DD [HH:MM]`, or `every <N> <unit>` (units: minutes, hours, days, weeks)"
)


@dataclass(frozen=True)
class OneShot:
    """`at <timestamp>`: due once, at `at`."""

    at: datetime


@dataclass(frozen=True)
class Recurring:
    """`every <every> <unit> from <anchor>`: due at the anchor and then every period after it, forever."""

    every: int
    unit: str
    anchor: datetime

    def __post_init__(self) -> None:
        """A count whose period `timedelta` cannot hold is refused here, where the caller expects a `ValueError`.

        Without the check such a schedule would parse and validate, and `tick` would then raise `OverflowError` on
        `period` and abort before delivering the other reminders.
        """
        _period(self.every, self.unit)

    @property
    def period(self) -> timedelta:
        return _period(self.every, self.unit)


type Schedule = OneShot | Recurring


def parse(text: str) -> Schedule:
    """The schedule written as `text`; `ValueError` with a user-facing message when it does not follow the grammar.

    Keywords are lowercase; the count must be a positive whole number; units may be singular; the timestamp must carry
    a UTC offset (`Z` is accepted) and may omit the seconds.
    """
    stripped = text.strip()
    if match := _AT_RE.match(stripped):
        return OneShot(parse_timestamp(match.group(1)))
    if match := _EVERY_RE.match(stripped):
        count, unit, anchor = match.groups()
        return Recurring(_parse_count(count), _parse_unit(unit), parse_timestamp(anchor))
    raise ValueError(f"`{stripped}` is not a schedule: {GRAMMAR_HINT}")


def format(schedule: Schedule) -> str:
    """The canonical text of `schedule`: plural unit, seconds always present, `+HH:MM` offset."""
    if isinstance(schedule, OneShot):
        return f"at {format_timestamp(schedule.at)}"
    return f"every {schedule.every} {schedule.unit} from {format_timestamp(schedule.anchor)}"


def parse_timestamp(text: str) -> datetime:
    """An aware datetime from ISO 8601 text with a mandatory UTC offset; the offset stays fixed, never a named zone."""
    match = _TIMESTAMP_RE.match(text)
    if match is None:
        raise ValueError(f"timestamp `{text}` must look like 2026-09-09T10:00:00+03:00")
    if match.group(2) is None:
        raise ValueError(f"timestamp `{text}` needs a UTC offset such as +03:00 (a naive time is ambiguous)")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"timestamp `{text}` is not a real date, time, or offset") from None


def format_timestamp(value: datetime) -> str:
    """`YYYY-MM-DDTHH:MM:SS+HH:MM`; sub-second precision is dropped."""
    _require_aware(value, "timestamp")
    return value.isoformat(timespec="seconds")


def latest_due(schedule: Schedule, now: datetime) -> datetime | None:
    """The most recent occurrence at or before `now`, or None when the schedule has not started yet.

    A one-shot schedule is due from its timestamp on. A recurring one advances from its anchor in whole periods,
    so a machine that was off for a long time sees exactly one occurrence, the latest, and never the missed ones.
    """
    _require_aware(now, "now")
    if isinstance(schedule, OneShot):
        return schedule.at if schedule.at <= now else None
    if now < schedule.anchor:
        return None
    elapsed = (now - schedule.anchor) // schedule.period
    return schedule.anchor + elapsed * schedule.period


def next_due(schedule: Schedule, now: datetime) -> datetime | None:
    """The first occurrence after `now`, or None for a one-shot schedule that has already fired."""
    _require_aware(now, "now")
    if isinstance(schedule, OneShot):
        return schedule.at if schedule.at > now else None
    if now < schedule.anchor:
        return schedule.anchor
    elapsed = (now - schedule.anchor) // schedule.period
    return schedule.anchor + (elapsed + 1) * schedule.period


def describe(schedule: Schedule, now: datetime) -> str:
    """The schedule as a person reads it in a listing: `at 2026-09-09 10:00` or `every 3 days, next 2026-09-05 10:00`.

    Times are shown to the minute in the offset the schedule was written in, without the offset itself. A period of
    one unit reads `every day`, not `every 1 days`.
    """
    if isinstance(schedule, OneShot):
        return f"at {_clock_text(schedule.at)}"
    period = schedule.unit[:-1] if schedule.every == 1 else f"{schedule.every} {schedule.unit}"
    upcoming = next_due(schedule, now)
    assert upcoming is not None  # a recurring schedule always has a next occurrence
    return f"every {period}, next {_clock_text(upcoming)}"


def _clock_text(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


def normalize_input(text: str, now: datetime, tz: tzinfo | None = None) -> str:
    """Canonical text for what a person typed: canonical or leniently written grammar, or one of the relative forms.

    Relative forms are `in <N|a|an> <unit>`, `tomorrow [HH:MM]`, `today HH:MM`, `YYYY-MM-DD [HH:MM]`,
    and `every <N> <unit>` anchored at `now`. Wall-clock times are read in `tz`, by default the timezone of `now`,
    and the result carries that offset. A form the function does not know raises `ValueError` listing the accepted ones.
    """
    _require_aware(now, "now")
    local = now.astimezone(now.tzinfo if tz is None else tz).replace(microsecond=0)
    stripped = " ".join(text.split())
    lowered = stripped.lower()
    if match := _IN_RE.match(lowered):
        count, unit = match.groups()
        delta = _period(1 if count in ("a", "an") else _parse_count(count), _parse_unit(unit))
        try:
            return format(OneShot(local + delta))
        except OverflowError:
            raise ValueError(f"`{stripped}` lands beyond the last date a schedule can name") from None
    if match := _TOMORROW_RE.match(lowered):
        return format(OneShot(_combine(local.date() + timedelta(days=1), match.group(1), local.tzinfo)))
    if match := _TODAY_RE.match(lowered):
        return format(OneShot(_combine(local.date(), match.group(1), local.tzinfo)))
    if match := _DATE_RE.match(lowered):
        return format(OneShot(_combine(_parse_date(match.group(1)), match.group(2), local.tzinfo)))
    if match := _EVERY_SHORT_RE.match(lowered):
        count, unit = match.groups()
        return format(Recurring(_parse_count(count), _parse_unit(unit), local))
    if lowered.startswith("at ") or " from " in lowered:
        return format(parse(stripped))
    raise ValueError(f"`{stripped}` is not a schedule: {INPUT_HINT}")


def _parse_count(text: str) -> int:
    if not _COUNT_RE.match(text) or int(text) <= 0:
        raise ValueError(f"count `{text}` must be a positive whole number")
    return int(text)


def _period(count: int, unit: str) -> timedelta:
    """`count` units as a `timedelta`; a count too large for one is a `ValueError`, never an `OverflowError`."""
    try:
        return timedelta(**{unit: count})
    except OverflowError:
        raise ValueError(f"`{count} {unit}` is a longer period than a schedule can name") from None


def _parse_unit(text: str) -> str:
    unit = text.lower()
    if unit in UNITS:
        return unit
    if unit + "s" in UNITS:
        return unit + "s"
    raise ValueError(f"unknown unit `{text}` (expected minutes, hours, days, or weeks)")


def _parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"date `{text}` does not exist") from None


def _parse_clock(text: str) -> time:
    match = _CLOCK_RE.match(text)
    try:
        if match is None:
            raise ValueError
        return time(int(match.group(1)), int(match.group(2)))
    except ValueError:
        raise ValueError(f"time `{text}` must look like HH:MM") from None


def _combine(day: date, clock: str | None, tz: tzinfo | None) -> datetime:
    moment = DEFAULT_TIME if clock is None else _parse_clock(clock)
    return datetime.combine(day, moment, tzinfo=tz)


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
