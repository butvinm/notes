"""Tests for the schedule grammar, canonical form, occurrence math, and relative input."""

import re
from datetime import UTC, datetime, timedelta, timezone

import pytest

from notes import schedule
from notes.schedule import OneShot, Recurring

MSK = timezone(timedelta(hours=3))
ANCHOR_TEXT = "2026-09-02T10:00:00+03:00"
ANCHOR = datetime(2026, 9, 2, 10, 0, tzinfo=MSK)
EVERY_3_DAYS = f"every 3 days from {ANCHOR_TEXT}"


# Grammar: parse and format


def test_one_shot_round_trip() -> None:
    parsed = schedule.parse(f"at {ANCHOR_TEXT}")

    assert isinstance(parsed, OneShot)
    assert parsed == OneShot(ANCHOR)
    assert parsed.at.utcoffset() == timedelta(hours=3)
    assert schedule.format(parsed) == f"at {ANCHOR_TEXT}"


def test_recurring_round_trip() -> None:
    parsed = schedule.parse(EVERY_3_DAYS)

    assert parsed == Recurring(3, "days", ANCHOR)
    assert schedule.format(parsed) == EVERY_3_DAYS


@pytest.mark.parametrize(
    ("unit", "plural"),
    [("minute", "minutes"), ("hour", "hours"), ("day", "days"), ("week", "weeks")],
)
def test_singular_units_are_accepted_and_written_plural(unit: str, plural: str) -> None:
    parsed = schedule.parse(f"every 1 {unit} from {ANCHOR_TEXT}")

    assert parsed == Recurring(1, plural, ANCHOR)
    assert schedule.format(parsed) == f"every 1 {plural} from {ANCHOR_TEXT}"
    assert schedule.parse(f"every 2 {plural} from {ANCHOR_TEXT}") == Recurring(2, plural, ANCHOR)


@pytest.mark.parametrize("unit", ["minutes", "hours", "days", "weeks"])
def test_period_per_unit(unit: str) -> None:
    assert Recurring(5, unit, ANCHOR).period == timedelta(**{unit: 5})


def test_zulu_offset_becomes_plus_zero() -> None:
    parsed = schedule.parse("at 2026-09-09T10:00:00Z")

    assert parsed == OneShot(datetime(2026, 9, 9, 10, 0, tzinfo=UTC))
    assert schedule.format(parsed) == "at 2026-09-09T10:00:00+00:00"


def test_omitted_seconds_are_written_back() -> None:
    assert schedule.format(schedule.parse("at 2026-09-09T10:00+03:00")) == "at 2026-09-09T10:00:00+03:00"
    assert schedule.format(schedule.parse("every 2 weeks from 2026-09-02T10:00+03:00")) == (
        f"every 2 weeks from {ANCHOR_TEXT}"
    )


def test_surrounding_whitespace_is_ignored() -> None:
    assert schedule.parse(f"  at {ANCHOR_TEXT}\n") == OneShot(ANCHOR)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("at 2026-09-09T10:00:00", "needs a UTC offset"),
        ("every 3 days from 2026-09-02T10:00", "needs a UTC offset"),
        ("every 0 days from " + ANCHOR_TEXT, "count `0` must be a positive whole number"),
        ("every -3 days from " + ANCHOR_TEXT, "count `-3` must be a positive whole number"),
        ("every 1.5 days from " + ANCHOR_TEXT, "count `1.5` must be a positive whole number"),
        ("every three days from " + ANCHOR_TEXT, "count `three` must be a positive whole number"),
        (
            "every 3 fortnights from " + ANCHOR_TEXT,
            "unknown unit `fortnights` (expected minutes, hours, days, or weeks)",
        ),
        (
            "every 1000000000 days from " + ANCHOR_TEXT,
            "`1000000000 days` is a longer period than a schedule can name",
        ),
        ("at 2026-13-40T10:00:00+03:00", "not a real date, time, or offset"),
        ("at 2026-09-09T10:00:00+25:00", "not a real date, time, or offset"),
        ("at 2026-09-09", "must look like 2026-09-09T10:00:00+03:00"),
        ("at 2026-09-09T10:00:00.5+03:00", "must look like 2026-09-09T10:00:00+03:00"),
        ("at 2026-09-09 10:00:00+03:00", "must look like 2026-09-09T10:00:00+03:00"),
        ("2026-09-09T10:00:00+03:00", "is not a schedule: write `at <timestamp>`"),
        ("every 3 days", "is not a schedule"),
        ("in 3 days", "is not a schedule"),
        ("At " + ANCHOR_TEXT, "is not a schedule"),
        ("at", "is not a schedule"),
        ("", "is not a schedule"),
    ],
)
def test_grammar_rejections(text: str, expected: str) -> None:
    with pytest.raises(ValueError, match=re.escape(expected)):
        schedule.parse(text)


def test_format_timestamp_requires_an_aware_datetime() -> None:
    with pytest.raises(ValueError, match="timestamp must be timezone-aware"):
        schedule.format(OneShot(datetime(2026, 9, 9, 10, 0)))


# Occurrences


def test_one_shot_is_due_from_its_timestamp_on() -> None:
    one_shot = OneShot(ANCHOR)

    assert schedule.latest_due(one_shot, ANCHOR - timedelta(seconds=1)) is None
    assert schedule.latest_due(one_shot, ANCHOR) == ANCHOR
    assert schedule.latest_due(one_shot, ANCHOR + timedelta(days=400)) == ANCHOR


def test_recurring_before_the_anchor_is_not_due() -> None:
    assert schedule.latest_due(Recurring(3, "days", ANCHOR), ANCHOR - timedelta(minutes=1)) is None


def test_recurring_at_the_anchor_is_due_at_the_anchor() -> None:
    assert schedule.latest_due(Recurring(3, "days", ANCHOR), ANCHOR) == ANCHOR


def test_recurring_mid_period_returns_the_period_start() -> None:
    recurring = Recurring(3, "days", ANCHOR)

    assert schedule.latest_due(recurring, ANCHOR + timedelta(days=2, hours=23)) == ANCHOR
    assert schedule.latest_due(recurring, ANCHOR + timedelta(days=3)) == ANCHOR + timedelta(days=3)
    assert schedule.latest_due(recurring, ANCHOR + timedelta(days=4)) == ANCHOR + timedelta(days=3)


def test_recurring_after_many_periods_returns_only_the_latest() -> None:
    recurring = Recurring(3, "days", ANCHOR)

    due = schedule.latest_due(recurring, ANCHOR + timedelta(days=100, hours=5))

    assert due is not None
    assert due == ANCHOR + timedelta(days=99)
    assert schedule.format_timestamp(due) == "2026-12-10T10:00:00+03:00"


@pytest.mark.parametrize(
    ("unit", "every", "elapsed", "expected"),
    [
        ("minutes", 15, timedelta(minutes=47), timedelta(minutes=45)),
        ("hours", 6, timedelta(hours=25), timedelta(hours=24)),
        ("weeks", 2, timedelta(weeks=5, days=3), timedelta(weeks=4)),
    ],
)
def test_recurring_units(unit: str, every: int, elapsed: timedelta, expected: timedelta) -> None:
    assert schedule.latest_due(Recurring(every, unit, ANCHOR), ANCHOR + elapsed) == ANCHOR + expected


def test_now_in_another_timezone_compares_by_instant() -> None:
    recurring = Recurring(1, "days", ANCHOR)
    now_utc = datetime(2026, 9, 3, 6, 59, tzinfo=UTC)  # 09:59+03:00, one minute before the second occurrence

    assert schedule.latest_due(recurring, now_utc) == ANCHOR
    due = schedule.latest_due(recurring, now_utc + timedelta(minutes=1))
    assert due == ANCHOR + timedelta(days=1)
    assert due is not None and due.utcoffset() == timedelta(hours=3)


def test_latest_due_rejects_a_naive_now() -> None:
    with pytest.raises(ValueError, match="now must be timezone-aware"):
        schedule.latest_due(OneShot(ANCHOR), datetime(2026, 9, 9, 10, 0))


# Relative input


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("in a week", "at 2026-09-09T10:00:00+03:00"),
        ("in 3 days", "at 2026-09-05T10:00:00+03:00"),
        ("in an hour", "at 2026-09-02T11:00:00+03:00"),
        ("in 90 minutes", "at 2026-09-02T11:30:00+03:00"),
        ("in 1 day", "at 2026-09-03T10:00:00+03:00"),
        ("tomorrow 09:30", "at 2026-09-03T09:30:00+03:00"),
        ("tomorrow 9:30", "at 2026-09-03T09:30:00+03:00"),
        ("tomorrow", "at 2026-09-03T09:00:00+03:00"),
        ("today 18:00", "at 2026-09-02T18:00:00+03:00"),
        ("2026-10-01", "at 2026-10-01T09:00:00+03:00"),
        ("2026-10-01 14:15", "at 2026-10-01T14:15:00+03:00"),
        ("every 2 weeks", f"every 2 weeks from {ANCHOR_TEXT}"),
        ("every 1 day", f"every 1 days from {ANCHOR_TEXT}"),
        ("Tomorrow 09:30", "at 2026-09-03T09:30:00+03:00"),
        ("In 3 Days", "at 2026-09-05T10:00:00+03:00"),
        ("  in   3   days  ", "at 2026-09-05T10:00:00+03:00"),
    ],
)
def test_relative_forms_use_the_offset_of_now(text: str, expected: str) -> None:
    assert schedule.normalize_input(text, ANCHOR) == expected


def test_canonical_text_passes_through_unchanged() -> None:
    assert schedule.normalize_input(f"at {ANCHOR_TEXT}", ANCHOR) == f"at {ANCHOR_TEXT}"
    assert schedule.normalize_input(EVERY_3_DAYS, ANCHOR) == EVERY_3_DAYS


def test_lenient_grammar_is_canonicalized() -> None:
    assert schedule.normalize_input("at 2026-09-09T10:00Z", ANCHOR) == "at 2026-09-09T10:00:00+00:00"
    assert schedule.normalize_input("every 1 day from 2026-09-02T10:00+03:00", ANCHOR) == (
        f"every 1 days from {ANCHOR_TEXT}"
    )


def test_explicit_timezone_reads_wall_clock_forms_in_it() -> None:
    now_utc = datetime(2026, 9, 2, 7, 0, tzinfo=UTC)  # the same instant as ANCHOR

    assert schedule.normalize_input("tomorrow 09:30", now_utc, MSK) == "at 2026-09-03T09:30:00+03:00"
    assert schedule.normalize_input("in 3 days", now_utc, MSK) == "at 2026-09-05T10:00:00+03:00"
    assert schedule.normalize_input("every 2 weeks", now_utc, MSK) == f"every 2 weeks from {ANCHOR_TEXT}"
    assert schedule.normalize_input("tomorrow 09:30", now_utc) == "at 2026-09-03T09:30:00+00:00"


def test_sub_second_precision_of_now_is_dropped() -> None:
    assert schedule.normalize_input("in 1 hour", ANCHOR.replace(microsecond=123456)) == "at 2026-09-02T11:00:00+03:00"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("tomorrow 25:00", "time `25:00` must look like HH:MM"),
        ("today 9", "time `9` must look like HH:MM"),
        ("today", "is not a schedule"),
        ("2026-13-01", "date `2026-13-01` does not exist"),
        ("in 0 days", "count `0` must be a positive whole number"),
        ("in three days", "count `three` must be a positive whole number"),
        ("in 3 fortnights", "unknown unit `fortnights`"),
        ("every 0 weeks", "count `0` must be a positive whole number"),
        ("in 1000000000 days", "`1000000000 days` is a longer period than a schedule can name"),
        ("in 999999999 days", "`in 999999999 days` lands beyond the last date a schedule can name"),
        ("every 99999999999 weeks", "`99999999999 weeks` is a longer period than a schedule can name"),
        ("next week", "is not a schedule: write `at <timestamp>`, `every <N> <unit> from <timestamp>`"),
        ("at 2026-09-09T10:00:00", "needs a UTC offset"),
        ("", "is not a schedule"),
    ],
)
def test_relative_input_rejections(text: str, expected: str) -> None:
    with pytest.raises(ValueError, match=re.escape(expected)):
        schedule.normalize_input(text, ANCHOR)


def test_normalize_input_rejects_a_naive_now() -> None:
    with pytest.raises(ValueError, match="now must be timezone-aware"):
        schedule.normalize_input("in 3 days", datetime(2026, 9, 2, 10, 0))
