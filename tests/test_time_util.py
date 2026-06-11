"""Tests for UTC persistence and local display helpers."""

from datetime import datetime, timezone

import pytest

from sensors.time_util import (
    format_local_short,
    parse_timestamp,
    seconds_ago,
    to_utc_iso,
    utc_now,
)


def test_to_utc_iso_from_naive():
    assert to_utc_iso(datetime(2026, 1, 1, 12, 0, 0)) == "2026-01-01T12:00:00Z"


def test_to_utc_iso_from_aware_utc():
    dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert to_utc_iso(dt) == "2026-01-01T12:00:00Z"


def test_parse_timestamp_z_suffix():
    dt = parse_timestamp("2026-05-21T13:18:07.539494Z")
    assert dt.tzinfo == timezone.utc
    assert dt.hour == 13


def test_parse_timestamp_naive_legacy_local():
    """Naive timestamps are legacy local wall clock, normalized to UTC."""
    dt = parse_timestamp("2026-05-21T15:26:06.167063")
    assert dt.tzinfo == timezone.utc


def test_utc_now_is_aware():
    assert utc_now().tzinfo == timezone.utc


def test_format_local_short_does_not_raise_for_utc_aware():
    dt = parse_timestamp("2026-05-21T13:18:07Z")
    text = format_local_short(dt)
    assert ":" in text


def test_seconds_ago_naive_state_time_and_utc_now():
    """sensors check: state.lastUpdated is naive local, now is UTC-aware.

    Direct subtraction raised TypeError before we used .timestamp().
    """
    naive_last_updated = datetime(2026, 5, 21, 15, 26, 6)
    now = utc_now()

    result = seconds_ago(naive_last_updated, now)

    assert isinstance(result, str)
    assert result.endswith("ago") or result == "just now"


def test_seconds_ago_mixed_naive_and_aware_fixed_instant():
    """Fixed pair that triggered the original bug (session UTC vs history local)."""
    naive = datetime(2026, 5, 21, 15, 26, 6)
    aware = datetime(2026, 5, 21, 13, 18, 7, tzinfo=timezone.utc)

    result = seconds_ago(naive, aware)

    assert isinstance(result, str)
    assert len(result) > 0


def test_direct_datetime_subtraction_still_fails_for_mixed_tz():
    """Documents why seconds_ago must not use (now - dt)."""
    naive = datetime(2026, 5, 21, 15, 26, 6)
    aware = datetime(2026, 5, 21, 13, 18, 7, tzinfo=timezone.utc)

    with pytest.raises(TypeError):
        (aware - naive).total_seconds()
