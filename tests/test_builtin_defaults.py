"""Tests for built-in runner default injection in the config loader."""

import pytest

from sensors.config.loader import (
    _BUILTIN_RUNNER_DEFAULTS,
    ConfigLoadError,
    _apply_builtin_defaults,
    load_config,
)


def test_builtin_defaults_map_empty():
    assert _BUILTIN_RUNNER_DEFAULTS == {}


def test_apply_builtin_defaults_noop_for_non_builtin_parser():
    raw = {
        "version": 1,
        "runners": [
            {"name": "ruff", "parser": "ruff", "enabled": True, "command": "ruff ."},
        ],
    }
    before = dict(raw["runners"][0])
    _apply_builtin_defaults(raw)
    assert raw["runners"][0] == before


def test_runner_without_parser_field_untouched():
    raw = {"version": 1, "runners": [{"name": "mystery", "enabled": True}]}
    before = dict(raw["runners"][0])
    _apply_builtin_defaults(raw)
    assert raw["runners"][0] == before


def test_empty_runners_list():
    raw = {"version": 1, "runners": []}
    _apply_builtin_defaults(raw)
    assert raw["runners"] == []


@pytest.mark.asyncio
async def test_load_config_normal_runner_still_requires_command(tmp_path):
    """Non-builtin parsers without a command should fail validation."""
    sensors_dir = tmp_path / ".sensors"
    sensors_dir.mkdir()
    (sensors_dir / "test.sensors.yaml").write_text(
        "version: 1\n"
        "runners:\n"
        "  - name: ruff\n"
        "    parser: ruff\n"
        "    enabled: true\n"
        "    mode: interval\n"
        "    interval: 10000\n"
    )

    with pytest.raises(ConfigLoadError):
        await load_config(str(tmp_path))
