"""Domain result types for runner output, shared across the runner and parser layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ScoreInfo(BaseModel):
    """Numerical score for trend comparison between states."""

    value: int = Field(
        description="Numerical score (e.g. number of failed tests, linting issues)"
    )
    direction: Literal["more", "less"] = Field(
        description="Which direction is better: 'less' means lower is better, 'more' means higher is better"
    )
    description: str = Field(
        default="",
        description="Human-readable description of what the score measures (e.g. 'Number of failing tests')"
    )


class RunnerResult(BaseModel):
    """Result from a single runner execution."""

    timestamp: datetime = Field(
        description="When this result was generated"
    )
    success: bool = Field(
        description="Whether the runner executed successfully"
    )
    output: dict[str, Any] = Field(
        default_factory=dict,
        description="Runner-specific output data (violations, test results, etc.)"
    )

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class FormattedOutput(BaseModel):
    """Pre-computed formatted strings for each client type.

    Populated by the output parser at write time so consumers
    never need to load parsers.
    """

    details_terminal: str = Field(
        default="",
        description="Short summary with Rich markup for terminal display"
    )
    details_html: str = Field(
        default="",
        description="Short summary as HTML for web dashboards"
    )
    details_llm: str = Field(
        default="",
        description="Short summary as plain text for LLM/agent consumption"
    )
    failures_terminal: str = Field(
        default="",
        description="Multi-line failure details with Rich markup"
    )
    failures_html: str = Field(
        default="",
        description="Multi-line failure details as HTML"
    )
    failures_llm: str = Field(
        default="",
        description="Multi-line failure details as plain text for LLM/agent"
    )


# ---------------------------------------------------------------------------
# Structured parser output types (new interface — Step 1 of refactor)
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    """A single violation, error, test failure, or contract breach."""

    message: str
    severity: Literal["error", "warning", "info"] = "error"

    # Source location. For graph-edge violations (depcruise, import_linter) the parser
    # pre-renders "source -> target" into this field; the formatter treats it uniformly.
    file: str | None = None
    line: int | None = None
    column: int | None = None

    # Rule / code identity
    rule: str | None = None  # ruleId, TSxxxx, contract name, etc.

    # Extra detail — description text, full stack trace excerpt, etc.
    context: str | None = None


@dataclass
class Metric:
    """A single named numeric value (counts, percentages, line totals)."""

    key: str
    label: str
    value: float | int
    unit: str | None = None                    # "%", "lines", "mutants", etc.
    direction: Literal["more", "less"] = "less"

    # Optional threshold for display colouring (relevant for direction="more" metrics).
    # At or above this value -> green; below -> red. None falls back to success/failure.
    threshold: float | None = None


@dataclass
class GuidanceBlock:
    """A block of guidance text associated with a triggered rule."""

    rule: str
    body: str                    # multi-line explanation
    summary: str | None = None   # short one-liner; ruff only, None for eslint


@dataclass
class ParsedOutput:
    """Structured result returned by a parser. Replaces dict[str, Any] in RunnerResult."""

    success: bool
    summary: str        # plain-text one-liner, e.g. "2 errors, 1 warning" or "72% coverage"
    score: ScoreInfo    # previously returned by calculate_score()

    findings: list[Finding] = field(default_factory=list)
    metrics: list[Metric] = field(default_factory=list)
    guidance: list[GuidanceBlock] = field(default_factory=list)

    # Escape hatch for parser-specific data that does not fit the model above.
    # GenericFormatter ignores this field.
    extra: dict[str, Any] = field(default_factory=dict)
