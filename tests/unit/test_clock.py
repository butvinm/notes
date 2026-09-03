"""Tests for the clock seam."""

from datetime import datetime

from notes import clock
from tests.conftest import FROZEN_NOW


def test_now_is_the_aware_local_time() -> None:
    before = datetime.now().astimezone()
    value = clock.now()
    after = datetime.now().astimezone()

    assert value.tzinfo is not None and value.utcoffset() is not None
    assert before <= value <= after
    assert value.utcoffset() == before.utcoffset()


def test_frozen_now_fixture_replaces_the_clock(frozen_now: datetime) -> None:
    assert clock.now() is FROZEN_NOW
    assert frozen_now == FROZEN_NOW
    assert frozen_now.isoformat() == "2026-09-02T12:00:00+03:00"
