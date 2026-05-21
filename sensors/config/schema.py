"""Configuration schema definitions using Pydantic for validation."""

from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


class RunnerMode(str, Enum):
    """Execution mode for a runner."""

    WATCH = "watch"
    INTERVAL = "interval"
    TRIGGERED = "triggered"
    ON_CHECK = "on_check"


class RunnerConfig(BaseModel):
    """Configuration for a single runner.

    Attributes:
        name: Unique identifier for the runner (e.g., "eslint", "tests")
        parser: Output parser to use (e.g., "eslint", "vitest")
        enabled: Whether this runner is active
        mode: Execution mode — watch, interval (periodic), or triggered (keyboard shortcut only)
        command: Shell command to execute the tool
        watchCommand: Optional command specifically for watch mode (overrides 'command' in watch mode)
        workingDir: Optional relative subdirectory for command execution (resolved against project root)
        interval: Interval in milliseconds between runs (interval mode only; omit for triggered)
        commandTimeout: Max seconds to wait for the command in interval/triggered mode (0 = no limit).
            When omitted, a long default is used so slow jobs (e.g. mutation tests) are not killed early.
        result: Optional path to a result file relative to the project root; when set, the runner
            runs the command first, then reads this file and passes its contents to the parser
            (stdout is not parsed). When omitted, the parser receives the command stdout.
        capture: List of fields to capture from the tool's output (deprecated, parsers handle this)
    """

    name: str = Field(..., description="Unique identifier for the runner")
    parser: str | None = Field(
        None,
        description=(
            "Output parser to use (e.g., 'eslint', 'vitest'). "
            "Required except for mode 'on_check' which passes stdout through as-is."
        ),
    )
    enabled: bool = Field(True, description="Whether this runner is active")
    mode: RunnerMode = Field(..., description="Execution mode: watch, interval, or triggered")
    command: str = Field(..., description="Shell command to execute")
    watchCommand: str | None = Field(
        None, description="Optional command for watch mode (overrides 'command')"
    )
    workingDir: str | None = Field(
        None, description="Optional relative subdirectory for command execution"
    )
    result: str | None = Field(
        None,
        description=(
            "Optional project-relative path to a result file; after the command finishes, "
            "read this file and parse it instead of stdout"
        ),
    )
    interval: int | None = Field(
        None, description="Interval in milliseconds between runs (interval mode only)", gt=0
    )
    commandTimeout: int | None = Field(
        None,
        ge=0,
        description=(
            "Interval/triggered modes: max seconds before the command is killed (0 = wait indefinitely). "
            "Omit to use the sensors default (long enough for typical mutation-test runs)."
        ),
    )
    capture: list[str] = Field(
        default_factory=list, description="List of fields to capture from output (deprecated)"
    )
    threshold: float | None = Field(
        None,
        description=(
            "Optional minimum acceptable score. When the runner succeeds but its score does not "
            "meet this target, the status becomes 'below_threshold'. "
            "For 'more is better' scores (e.g. coverage %), the score must be >= threshold. "
            "For 'less is better' scores (e.g. violation counts), the score must be <= threshold."
        ),
    )
    prompt: str | None = Field(
        None, description="Optional prompt text shown when status is queried, providing guidance for coding agents"
    )

    @field_validator("interval")
    @classmethod
    def validate_interval_for_mode(cls, v: int | None, info) -> int | None:
        """Ensure interval matches mode (required for interval, forbidden for triggered/on_check)."""
        # Note: info.data contains already-validated fields
        mode = info.data.get("mode")
        if mode == RunnerMode.INTERVAL and v is None:
            raise ValueError("interval is required when mode is 'interval'")
        if mode in (RunnerMode.TRIGGERED, RunnerMode.ON_CHECK) and v is not None:
            mode_label = mode.value if isinstance(mode, RunnerMode) else str(mode)
            raise ValueError(f"interval must be omitted when mode is {mode_label!r}")
        return v

    @model_validator(mode="after")
    def validate_parser_required(self) -> "RunnerConfig":
        """Parser is required unless the runner is in on_check mode (raw passthrough)."""
        if self.mode != RunnerMode.ON_CHECK and not self.parser:
            raise ValueError(
                f"parser is required for mode {self.mode!r} (only 'on_check' may omit it)"
            )
        return self

    class Config:
        """Pydantic model configuration."""

        use_enum_values = True


class SensorsConfig(BaseModel):
    """Root configuration for the sensors system.

    Attributes:
        version: Configuration schema version
        runners: List of runner configurations
    """

    version: int = Field(..., description="Configuration schema version")
    prompt: str | None = Field(
        None, description="Optional prompt text shown at the top of status queries, providing guidance for coding agents"
    )
    runners: list[RunnerConfig] = Field(
        default_factory=list, description="List of runner configurations"
    )

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: int) -> int:
        """Ensure version is supported."""
        if v != 1:
            raise ValueError(f"Unsupported configuration version: {v}. Expected version 1.")
        return v

    @field_validator("runners")
    @classmethod
    def validate_unique_names(cls, v: list[RunnerConfig]) -> list[RunnerConfig]:
        """Ensure runner names are unique."""
        names = [runner.name for runner in v]
        if len(names) != len(set(names)):
            raise ValueError("Runner names must be unique")
        return v
