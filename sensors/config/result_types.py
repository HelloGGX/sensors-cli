"""Domain result types for runner output, shared across the runner and parser layers."""

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
