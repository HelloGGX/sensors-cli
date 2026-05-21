"""Regression tests for _seconds_ago (sensors check display)."""

from datetime import datetime, timezone

import pytest

from sensors.cli import _seconds_ago
from sensors.time_util import utc_now


def test_seconds_ago_naive_state_time_and_utc_now():
    """sensors check: state.lastUpdated is naive local, now is UTC-aware.

    Direct subtraction raised TypeError before we used .timestamp().
    """
    naive_last_updated = datetime(2026, 5, 21, 15, 26, 6)
    now = utc_now()

    result = _seconds_ago(naive_last_updated, now)

    assert isinstance(result, str)
    assert result.endswith("ago") or result == "just now"


def test_seconds_ago_mixed_naive_and_aware_fixed_instant():
    """Fixed pair that triggered the original bug (session UTC vs history local)."""
    naive = datetime(2026, 5, 21, 15, 26, 6)
    aware = datetime(2026, 5, 21, 13, 18, 7, tzinfo=timezone.utc)

    result = _seconds_ago(naive, aware)

    assert isinstance(result, str)
    assert len(result) > 0


def test_direct_datetime_subtraction_still_fails_for_mixed_tz():
    """Documents why _seconds_ago must not use (now - dt)."""
    naive = datetime(2026, 5, 21, 15, 26, 6)
    aware = datetime(2026, 5, 21, 13, 18, 7, tzinfo=timezone.utc)

    with pytest.raises(TypeError):
        (aware - naive).total_seconds()
