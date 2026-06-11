"""Domain result types for runner output, shared across the runner and parser layers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.dataclasses import dataclass as pydantic_dataclass


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
    threshold: float | None = Field(
        default=None,
        description="Optional target threshold. Below this value is considered 'below threshold' for direction='more'."
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


class Formatted(BaseModel):
    """Pre-computed formatted strings for each client type.

    Populated by the output parser at write time so consumers
    never need to load parsers.
    """

    summary_terminal: str = Field(
        default="",
        description="Short label with Rich markup for terminal display"
    )
    summary_html: str = Field(
        default="",
        description="Short label as HTML for web dashboards"
    )
    summary_llm: str = Field(
        default="",
        description="Short label as plain text for LLM/agent consumption"
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
# Structured parser output types
# ---------------------------------------------------------------------------


@pydantic_dataclass
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


@pydantic_dataclass
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


@pydantic_dataclass
class GuidanceBlock:
    """A block of guidance text associated with a triggered rule."""

    rule: str
    body: str                    # multi-line explanation


class SensorReading(BaseModel):
    """Structured result returned by a parser."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    success: bool
    summary: str        # plain-text one-liner, e.g. "2 errors, 1 warning" or "72% coverage"
    score: ScoreInfo  # basis of trend comparison and sometimes success/failure determination

    findings: list[Finding] = Field(default_factory=list)
    # TODO: It seems like we are not reading the metrics anywhere at the moment?!
    metrics: list[Metric] = Field(default_factory=list)
    guidance: list[GuidanceBlock] = Field(default_factory=list)

    # Escape hatch for parser-specific data that does not fit the model above.
    extra: dict[str, Any] = Field(default_factory=dict)

    formatted: Formatted = Field(default_factory=Formatted)

    @model_validator(mode="after")
    def _populate_formatted(self) -> SensorReading:
        if not self.formatted.summary_llm:
            self.formatted = _build_formatted(self)
        return self

    @classmethod
    def from_error(cls, message: str) -> SensorReading:
        """Produce a minimal failed reading for infrastructure errors (timeout, etc.)."""
        return cls(
            success=False,
            summary=message,
            score=ScoreInfo(value=0, direction="less", description="Infrastructure error"),
        )


# ---------------------------------------------------------------------------
# Formatting logic (moved from sensors/runners/formatter.py)
# ---------------------------------------------------------------------------


def _build_formatted(reading: SensorReading) -> Formatted:
    return Formatted(
        summary_terminal=_summary(reading, "terminal"),
        summary_html=_summary(reading, "html"),
        summary_llm=_summary(reading, "llm"),
        failures_terminal=_failures(reading, "terminal"),
        failures_html=_failures(reading, "html"),
        failures_llm=_failures(reading, "llm"),
    )


def _apply_color(text: str, color: str, style: str) -> str:
    if style == "terminal":
        return f"[{color}]{text}[/{color}]"
    if style == "html":
        css = {
            "green": "sensors-success",
            "yellow": "sensors-warn",
            "red": "sensors-error",
            "dim": "sensors-dim",
        }.get(color, "sensors-info")
        return f'<span class="{css}">{text}</span>'
    return text


def _summary(reading: SensorReading, style: str) -> str:
    color = "green" if reading.success else "red"
    return _apply_color(reading.summary, color, style)


def _failures(reading: SensorReading, style: str) -> str:
    if reading.success:
        return ""
    parts = [_render_finding(f, style) for f in reading.findings]
    parts += [_render_guidance(g, style) for g in reading.guidance]
    if not parts:
        return _apply_color(reading.summary, "red", style)
    return "\n".join(p for p in parts if p)


def _build_loc(f: Finding) -> str | None:
    loc_parts: list[str] = []
    if f.file:
        loc_parts.append(f.file)
        if f.line is not None:
            loc_parts.append(str(f.line))
            if f.column is not None:
                loc_parts.append(str(f.column))
    return ":".join(loc_parts) if loc_parts else None


def _render_finding(f: Finding, style: str) -> str:
    loc = _build_loc(f)
    if style == "html":
        return _render_finding_html(f, loc)
    return _render_finding_text(f, loc, style)


def _render_guidance(g: GuidanceBlock, style: str) -> str:
    header = g.rule
    if style == "terminal":
        lines = [f"  [yellow]{header}[/yellow]"]
        lines += [f"  [dim]{line}[/dim]" for line in g.body.splitlines()]
        return "\n".join(lines)
    if style == "html":
        return (
            f'<div class="sensors-guidance">'
            f'<span class="sensors-rule">{header}</span>'
            f'<div class="sensors-message">{g.body}</div>'
            f'</div>'
        )
    return f"  {header}\n" + "\n".join(f"  {line}" for line in g.body.splitlines())


def _render_finding_html(f: Finding, loc: str | None) -> str:
    parts: list[str] = []
    if loc:
        parts.append(f'<span class="sensors-file">{loc}</span>')
    if f.rule:
        parts.append(f'<span class="sensors-rule">{f.rule}</span>')
    parts.append(f'<span class="sensors-message">{f.message}</span>')
    css = f"sensors-violation sensors-{f.severity}"
    inner = " ".join(parts)
    if f.context:
        inner += f'<div class="sensors-context">{f.context}</div>'
    return f'<div class="{css}">{inner}</div>'


def _render_finding_text(f: Finding, loc: str | None, style: str) -> str:
    color = {"error": "red", "warning": "yellow", "info": "dim"}.get(f.severity, "red")
    text_parts: list[str] = []
    if loc:
        text_parts.append(_apply_color(loc, color, style))
    if f.rule:
        text_parts.append(f"[dim]{f.rule}[/dim]" if style == "terminal" else f.rule)
    text_parts.append(f.message)
    line = "  " + " ".join(text_parts)
    if f.context:
        line += f"\n    {f.context}"
    return line
