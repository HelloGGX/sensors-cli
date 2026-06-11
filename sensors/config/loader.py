"""Configuration loader for loading and validating YAML configuration files."""

import logging
import logging.handlers
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from .schema import SensorsConfig

SENSORS_DIR_NAME = ".sensors"
GITIGNORE_CONTENT = """\
# Sensors runtime state

events*.jsonl
sessions*.jsonl
*history.jsonl

*.log

*.state.json
*.control.json
*.sock
"""

LOG_FILE_NAME = "sensors.log"
# Max 1 MB per file, keep 3 backups
_LOG_MAX_BYTES = 1 * 1024 * 1024
_LOG_BACKUP_COUNT = 3

# Default configuration for built-in parsers.
# Keyed by parser name.  When a runner specifies a parser listed here, any
# fields the user omitted are filled in before Pydantic validation.  Every
# default can be overridden by specifying the field in the YAML.
_BUILTIN_RUNNER_DEFAULTS: dict[str, dict] = {
}


class ConfigLoadError(Exception):
    """Raised when configuration loading fails."""

    pass


def ensure_sensors_dir(working_dir: Path) -> Path:
    """Ensure the .sensors directory exists inside working_dir.

    Creates the directory and a .gitignore if they don't exist.
    Returns the .sensors directory path.
    """
    sensors_dir = working_dir / SENSORS_DIR_NAME
    if not sensors_dir.exists():
        sensors_dir.mkdir(parents=True, exist_ok=True)
        print(
            f"Warning: Created {sensors_dir} — no config file found yet.",
            file=sys.stderr,
        )
    gitignore = sensors_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE_CONTENT)
    return sensors_dir


def find_config_files(sensors_dir: Path) -> list[Path]:
    """Find all *.sensors.yaml config files in the .sensors directory."""
    return sorted(sensors_dir.glob("*.sensors.yaml"))


def resolve_config_path(working_dir: str, config_name: str | None = None) -> Path:
    """Resolve the config file path inside {working_dir}/.sensors/.

    Args:
        working_dir: The project working directory.
        config_name: Optional config file name (e.g. "myproject.sensors.yaml").
            If None, uses the single config file found, or raises an error.

    Returns:
        Resolved Path to the config file.

    Raises:
        ConfigLoadError: If the config file cannot be resolved.
    """
    wd = Path(working_dir).resolve()
    sensors_dir = ensure_sensors_dir(wd)

    if config_name:
        return sensors_dir / config_name

    configs = find_config_files(sensors_dir)
    if len(configs) == 1:
        return configs[0]
    if len(configs) == 0:
        raise ConfigLoadError(
            f"No *.sensors.yaml config files found in {sensors_dir}"
        )
    names = ", ".join(c.name for c in configs)
    raise ConfigLoadError(
        f"Multiple config files found in {sensors_dir}: {names}. "
        "Specify which one with --config."
    )


def _config_stem(config_path: Path) -> str:
    """Base name for sidecar files (e.g. foo from foo.sensors.yaml)."""
    stem = config_path.stem
    if stem.endswith(".sensors"):
        stem = stem[: -len(".sensors")]
    return stem


def state_path_for_config(config_path: Path) -> Path:
    """Derive state JSON path from a config path.

    The state file lives in the same .sensors directory as the config.
    foo.sensors.yaml -> foo.state.json
    """
    return config_path.parent / f"{_config_stem(config_path)}.state.json"


def control_path_for_config(config_path: Path) -> Path:
    """Path to control metadata JSON (socket path, pid). foo.sensors.yaml -> foo.control.json."""
    return config_path.parent / f"{_config_stem(config_path)}.control.json"


def history_path_for_config(config_path: Path) -> Path:
    """Path to check history JSONL. Always history.jsonl in the .sensors directory."""
    return config_path.parent / "history.jsonl"


def socket_path_for_config(config_path: Path) -> Path:
    """Unix domain socket path. foo.sensors.yaml -> foo.sock."""
    return config_path.parent / f"{_config_stem(config_path)}.sock"


def log_path_for_config(config_path: Path) -> Path:
    """Path to the error log file. Always sensors.log in the .sensors directory."""
    return config_path.parent / LOG_FILE_NAME


def configure_file_logging(log_path: Path) -> None:
    """Configure the root sensors logger to write WARNING+ messages to a rotating log file.

    Safe to call multiple times -- subsequent calls are no-ops if the handler
    is already attached.  The log file is created inside the .sensors directory
    so it is co-located with other runtime artefacts.

    Format: ISO-8601 timestamp | level | logger name | message (+ exception
    traceback on the following lines when one is present).
    """
    sensors_logger = logging.getLogger("sensors")

    # Avoid adding duplicate handlers on config reload
    for handler in sensors_logger.handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler) and getattr(
            handler, "baseFilename", None
        ) == str(log_path):
            return

    sensors_logger.setLevel(logging.DEBUG)

    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.WARNING)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    sensors_logger.addHandler(file_handler)


async def load_config(working_dir: str, config_name: str | None = None) -> SensorsConfig:
    """Load and validate YAML configuration file.

    Args:
        working_dir: The project working directory containing .sensors/.
        config_name: Optional config file name within .sensors/.

    Returns:
        Validated SensorsConfig object

    Raises:
        ConfigLoadError: If configuration loading or validation fails
    """
    resolved = resolve_config_path(working_dir, config_name)
    config = _load_and_validate(resolved)
    _resolve_working_dirs(config, working_dir)
    _resolve_result_paths(config, working_dir)
    return config


def load_config_sync(working_dir: str, config_name: str | None = None) -> SensorsConfig:
    """Synchronous version of load_config for non-async contexts."""
    resolved = resolve_config_path(working_dir, config_name)
    config = _load_and_validate(resolved)
    _resolve_working_dirs(config, working_dir)
    _resolve_result_paths(config, working_dir)
    return config


def _resolve_result_paths(config: SensorsConfig, working_dir: str) -> None:
    """Resolve each runner's ``result`` path to an absolute path under the project root."""
    base = Path(working_dir).resolve()
    for runner in config.runners:
        if not runner.result:
            continue
        raw = Path(runner.result)
        resolved = raw.resolve() if raw.is_absolute() else (base / raw).resolve()
        try:
            resolved.relative_to(base)
        except ValueError as e:
            raise ConfigLoadError(
                f"Runner '{runner.name}': result must stay within the project directory "
                f"({base}), got {resolved}"
            ) from e
        runner.result = str(resolved)


def _resolve_working_dirs(config: SensorsConfig, working_dir: str) -> None:
    """Resolve each runner's workingDir to an absolute path.

    If workingDir is None, uses the project working_dir.
    If workingDir is a relative path, resolves it relative to working_dir.
    """
    base = Path(working_dir).resolve()
    for runner in config.runners:
        resolved = (base / runner.workingDir).resolve() if runner.workingDir else base
        if not resolved.exists():
            raise ConfigLoadError(
                f"Working directory for runner '{runner.name}' does not exist: {resolved}"
            )
        if not resolved.is_dir():
            raise ConfigLoadError(
                f"Working directory for runner '{runner.name}' is not a directory: {resolved}"
            )
        runner.workingDir = str(resolved)


def _apply_builtin_defaults(raw_config: dict) -> None:
    """Fill in built-in defaults for known parsers.

    For each runner whose ``parser`` field matches a key in
    ``_BUILTIN_RUNNER_DEFAULTS``, any field the user did not specify is
    populated with the built-in default value.  Fields the user did specify
    are left untouched, so every default can be overridden from the YAML.
    """
    for runner in raw_config.get("runners", []):
        parser_name = runner.get("parser")
        if parser_name in _BUILTIN_RUNNER_DEFAULTS:
            for key, value in _BUILTIN_RUNNER_DEFAULTS[parser_name].items():
                if key not in runner:
                    runner[key] = value


def _load_and_validate(config_path: Path) -> SensorsConfig:
    """Load YAML config file and validate against schema."""
    if not config_path.exists():
        raise ConfigLoadError(f"Configuration file not found: {config_path}")

    try:
        with open(config_path, encoding="utf-8") as f:
            raw_config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigLoadError(f"Failed to parse YAML configuration: {e}") from e
    except OSError as e:
        raise ConfigLoadError(f"Failed to read configuration file: {e}") from e

    _apply_builtin_defaults(raw_config)

    try:
        config = SensorsConfig(**raw_config)
    except ValidationError as e:
        raise ConfigLoadError(f"Configuration validation failed:\n{e}") from e

    return config
