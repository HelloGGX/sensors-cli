"""Tests for built-in runner default injection in the config loader."""

import pytest

from sensors.config.loader import (
    _BUILTIN_RUNNER_DEFAULTS,
    ConfigLoadError,
    _apply_builtin_defaults,
    load_config,
)
from sensors.config.schema import RunnerMode

# ---------------------------------------------------------------------------
# _apply_builtin_defaults unit tests
# ---------------------------------------------------------------------------

def _git_diff_runner(**extra):
    return {"name": "my-diff", "parser": "git_diff", "enabled": True, **extra}


def test_git_diff_gets_all_defaults():
    raw = {"version": 1, "runners": [_git_diff_runner()]}
    _apply_builtin_defaults(raw)
    runner = raw["runners"][0]

    for key, value in _BUILTIN_RUNNER_DEFAULTS["git_diff"].items():
        assert runner[key] == value, f"Expected default for '{key}'"


def test_user_command_overrides_default():
    raw = {"version": 1, "runners": [_git_diff_runner(command="echo custom")]}
    _apply_builtin_defaults(raw)
    assert raw["runners"][0]["command"] == "echo custom"


def test_user_interval_overrides_default():
    raw = {"version": 1, "runners": [_git_diff_runner(interval=60000)]}
    _apply_builtin_defaults(raw)
    assert raw["runners"][0]["interval"] == 60000


def test_user_prompt_overrides_default():
    raw = {"version": 1, "runners": [_git_diff_runner(prompt="my prompt")]}
    _apply_builtin_defaults(raw)
    assert raw["runners"][0]["prompt"] == "my prompt"


def test_runner_name_is_irrelevant():
    """Any runner name works — it's the parser that identifies the built-in."""
    raw = {"version": 1, "runners": [
        {"name": "diff-watcher", "parser": "git_diff", "enabled": True},
    ]}
    _apply_builtin_defaults(raw)
    runner = raw["runners"][0]
    assert runner["mode"] == "interval"
    assert "git diff" in runner["command"]


def test_non_builtin_parser_untouched():
    raw = {
        "version": 1,
        "runners": [{"name": "ruff", "parser": "ruff", "enabled": True, "command": "ruff ."}],
    }
    before = dict(raw["runners"][0])
    _apply_builtin_defaults(raw)
    assert raw["runners"][0] == before


def test_runner_without_parser_field_untouched():
    """A runner with no parser field at all should not be modified."""
    raw = {"version": 1, "runners": [{"name": "mystery", "enabled": True}]}
    before = dict(raw["runners"][0])
    _apply_builtin_defaults(raw)
    assert raw["runners"][0] == before


def test_empty_runners_list():
    raw = {"version": 1, "runners": []}
    _apply_builtin_defaults(raw)
    assert raw["runners"] == []


def test_multiple_runners_only_builtin_gets_defaults():
    raw = {
        "version": 1,
        "runners": [
            {"name": "ruff", "parser": "ruff", "enabled": True, "command": "ruff ."},
            {"name": "git-diff", "parser": "git_diff", "enabled": True},
        ],
    }
    _apply_builtin_defaults(raw)
    ruff = raw["runners"][0]
    git_diff = raw["runners"][1]

    # ruff has only what user specified (no mode/interval injected)
    assert "mode" not in ruff
    # git-diff got defaults
    assert git_diff["mode"] == "interval"
    assert git_diff["interval"] == 30000


# ---------------------------------------------------------------------------
# Integration: load_config with minimal git_diff entry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_config_git_diff_minimal(tmp_path):
    """name + parser + enabled is enough for a valid git_diff runner."""
    sensors_dir = tmp_path / ".sensors"
    sensors_dir.mkdir()
    (sensors_dir / "test.sensors.yaml").write_text(
        "version: 1\n"
        "runners:\n"
        "  - name: git-diff\n"
        "    parser: git_diff\n"
        "    enabled: true\n"
    )

    config = await load_config(str(tmp_path))
    assert len(config.runners) == 1
    runner = config.runners[0]
    assert runner.parser == "git_diff"
    assert runner.mode == RunnerMode.INTERVAL
    assert runner.interval == 30000
    assert "git diff" in runner.command
    assert runner.prompt is not None


@pytest.mark.asyncio
async def test_load_config_git_diff_any_name(tmp_path):
    """The runner name doesn't matter — any name with parser: git_diff works."""
    sensors_dir = tmp_path / ".sensors"
    sensors_dir.mkdir()
    (sensors_dir / "test.sensors.yaml").write_text(
        "version: 1\n"
        "runners:\n"
        "  - name: diff-checker\n"
        "    parser: git_diff\n"
        "    enabled: true\n"
    )

    config = await load_config(str(tmp_path))
    runner = config.runners[0]
    assert runner.name == "diff-checker"
    assert runner.mode == RunnerMode.INTERVAL


@pytest.mark.asyncio
async def test_load_config_git_diff_custom_interval(tmp_path):
    sensors_dir = tmp_path / ".sensors"
    sensors_dir.mkdir()
    (sensors_dir / "test.sensors.yaml").write_text(
        "version: 1\n"
        "runners:\n"
        "  - name: git-diff\n"
        "    parser: git_diff\n"
        "    enabled: true\n"
        "    interval: 60000\n"
    )

    config = await load_config(str(tmp_path))
    assert config.runners[0].interval == 60000


@pytest.mark.asyncio
async def test_load_config_git_diff_disabled_still_loads(tmp_path):
    sensors_dir = tmp_path / ".sensors"
    sensors_dir.mkdir()
    (sensors_dir / "test.sensors.yaml").write_text(
        "version: 1\n"
        "runners:\n"
        "  - name: git-diff\n"
        "    parser: git_diff\n"
        "    enabled: false\n"
    )

    config = await load_config(str(tmp_path))
    runner = config.runners[0]
    assert runner.enabled is False
    assert runner.mode == RunnerMode.INTERVAL  # defaults still applied


@pytest.mark.asyncio
async def test_load_config_normal_runner_still_requires_command(tmp_path):
    """Non-builtin parsers without a command should still fail validation."""
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
        # no command — should fail
    )

    with pytest.raises(ConfigLoadError):
        await load_config(str(tmp_path))
