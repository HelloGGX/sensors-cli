"""Tests for UTC persistence and local display helpers."""

from datetime import datetime, timezone

from sensors.time_util import (
    format_local_short,
    parse_timestamp,
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
