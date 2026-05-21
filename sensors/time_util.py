"""UTC persistence and local-time display helpers for sensors timestamps."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utc_now() -> datetime:
    """Current time as timezone-aware UTC."""
    return datetime.now(timezone.utc)


def to_utc_iso(dt: datetime) -> str:
    """Serialize a datetime to UTC ISO-8601 with a Z suffix (for history.jsonl).

    Naive datetimes are treated as UTC (used in tests and explicit UTC values).
    """
    dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    text = dt.isoformat(timespec="microseconds")
    if text.endswith(".000000+00:00"):
        text = text[: -len(".000000+00:00")] + "+00:00"
    return text.replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO timestamp from JSON (history or state).

    Z-suffixed and offset-aware values become timezone-aware UTC.
    Naive strings are legacy local wall clock (pre-UTC history writes).
    """
    normalized = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        return dt.replace(tzinfo=local_tz).astimezone(timezone.utc)
    return dt.astimezone(timezone.utc)


def as_local_for_display(dt: datetime) -> datetime:
    """Convert a stored timestamp to local wall time for user-facing output.

    Naive datetimes (state file snapshot/runner times) are already local wall clock.
    Aware UTC values (history.jsonl) are converted to the system timezone.
    """
    if dt.tzinfo is None:
        return dt
    return dt.astimezone()


def format_local_short(dt: datetime) -> str:
    """Format as HH:MM:SS with optional yesterday/date prefix in local time."""
    local = as_local_for_display(dt)
    now = datetime.now().astimezone()
    time_str = local.strftime("%H:%M:%S")
    if local.date() == now.date():
        return time_str
    yesterday = (now - timedelta(days=1)).date()
    if local.date() == yesterday:
        return f"yesterday {time_str}"
    return local.strftime("%b %-d ") + time_str


def format_local_datetime(dt: datetime) -> str:
    """Format as YYYY-MM-DD HH:MM:SS in local time."""
    return as_local_for_display(dt).strftime("%Y-%m-%d %H:%M:%S")
