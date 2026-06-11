"""Tests for GenericFormatter — verifies that ParsedOutput is rendered correctly
across all three client styles (terminal, html, llm)."""

import pytest

from sensors.config.result_types import Finding, GuidanceBlock, Metric, ParsedOutput, ScoreInfo
from sensors.runners.formatter import GenericFormatter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCORE = ScoreInfo(value=0, direction="less", description="")


def _parsed(*, success=True, summary="OK", findings=None, metrics=None, guidance=None):
    return ParsedOutput(
        success=success,
        summary=summary,
        score=_SCORE,
        findings=findings or [],
        metrics=metrics or [],
        guidance=guidance or [],
    )


# ---------------------------------------------------------------------------
# Details rendering
# ---------------------------------------------------------------------------

class TestDetails:
    def test_success_is_green_terminal(self):
        fmt = GenericFormatter()
        out = fmt.format(_parsed(success=True, summary="No issues"))
        assert "[green]No issues[/green]" == out.details_terminal

    def test_failure_is_red_terminal(self):
        fmt = GenericFormatter()
        out = fmt.format(_parsed(success=False, summary="2 errors"))
        assert "[red]2 errors[/red]" == out.details_terminal

    def test_success_is_green_html(self):
        fmt = GenericFormatter()
        out = fmt.format(_parsed(success=True, summary="No issues"))
        assert 'class="sensors-success"' in out.details_html
        assert "No issues" in out.details_html

    def test_failure_is_red_html(self):
        fmt = GenericFormatter()
        out = fmt.format(_parsed(success=False, summary="2 errors"))
        assert 'class="sensors-error"' in out.details_html

    def test_llm_is_plain_text(self):
        fmt = GenericFormatter()
        out = fmt.format(_parsed(success=True, summary="No issues"))
        assert out.details_llm == "No issues"

    def test_metric_threshold_green_when_above(self):
        """Primary 'more' metric above threshold -> green (success=True)."""
        parsed = _parsed(
            success=True,
            summary="85% branch",
            metrics=[Metric("branch", "Branch", 85.0, "%", "more", threshold=80.0)],
        )
        out = GenericFormatter().format(parsed)
        assert "[green]" in out.details_terminal

    def test_success_true_always_green_regardless_of_metric_threshold(self):
        """success=True means green even if the metric value is below its threshold.

        Color authority is parsed.success, not the metric threshold. Threshold on
        a metric is informational only; the runner's config.threshold drives status.
        """
        parsed = _parsed(
            success=True,
            summary="60% branch",
            metrics=[Metric("branch", "Branch", 60.0, "%", "more", threshold=80.0)],
        )
        out = GenericFormatter().format(parsed)
        assert "[green]" in out.details_terminal
        assert "[red]" not in out.details_terminal


# ---------------------------------------------------------------------------
# Failures rendering
# ---------------------------------------------------------------------------

class TestFailures:
    def test_success_returns_empty(self):
        fmt = GenericFormatter()
        out = fmt.format(_parsed(success=True))
        assert out.failures_terminal == ""
        assert out.failures_html == ""
        assert out.failures_llm == ""

    def test_no_findings_falls_back_to_summary(self):
        """When there are no findings, the summary is shown as the failure."""
        fmt = GenericFormatter()
        out = fmt.format(_parsed(success=False, summary="Parse error: bad JSON"))
        assert "Parse error: bad JSON" in out.failures_terminal
        assert "Parse error: bad JSON" in out.failures_llm

    def test_finding_rendered_in_terminal(self):
        finding = Finding(
            file="/app/src/foo.ts",
            line=10,
            column=5,
            message="x is defined but never used",
            rule="no-unused-vars",
            severity="error",
        )
        out = GenericFormatter().format(_parsed(success=False, findings=[finding]))
        assert "/app/src/foo.ts" in out.failures_terminal
        assert "10" in out.failures_terminal
        assert "no-unused-vars" in out.failures_terminal
        assert "x is defined but never used" in out.failures_terminal
        assert "[red]" in out.failures_terminal

    def test_warning_finding_uses_yellow(self):
        finding = Finding(
            file="/app/src/foo.ts",
            line=1,
            column=1,
            message="no console",
            rule="no-console",
            severity="warning",
        )
        out = GenericFormatter().format(_parsed(success=False, findings=[finding]))
        assert "[yellow]" in out.failures_terminal

    def test_finding_rendered_in_html(self):
        finding = Finding(
            file="/app/src/foo.ts",
            line=10,
            column=5,
            message="x is not defined",
            rule="no-undef",
            severity="error",
        )
        out = GenericFormatter().format(_parsed(success=False, findings=[finding]))
        assert "sensors-file" in out.failures_html
        assert "sensors-rule" in out.failures_html
        assert "sensors-violation" in out.failures_html
        assert "sensors-error" in out.failures_html
        assert "no-undef" in out.failures_html

    def test_finding_rendered_in_llm(self):
        finding = Finding(
            file="/app/src/foo.ts",
            line=10,
            column=5,
            message="x is not defined",
            rule="no-undef",
            severity="error",
        )
        out = GenericFormatter().format(_parsed(success=False, findings=[finding]))
        assert "/app/src/foo.ts" in out.failures_llm
        assert "no-undef" in out.failures_llm
        # no markup
        assert "[" not in out.failures_llm

    def test_finding_with_context(self):
        finding = Finding(
            file="src -> dst",
            message="import not allowed",
            rule="no-circular",
            severity="error",
            context="Circular dependency detected",
        )
        out = GenericFormatter().format(_parsed(success=False, findings=[finding]))
        assert "Circular dependency detected" in out.failures_terminal

    def test_guidance_rendered_in_terminal(self):
        guidance = GuidanceBlock(
            rule="no-explicit-any",
            body="Use a specific type instead of any.",
        )
        out = GenericFormatter().format(_parsed(success=False, guidance=[guidance]))
        assert "no-explicit-any" in out.failures_terminal
        assert "Use a specific type" in out.failures_terminal

    def test_guidance_with_summary_in_terminal(self):
        guidance = GuidanceBlock(
            rule="RUF001",
            summary="Ambiguous unicode character",
            body="Replace with ASCII equivalent.",
        )
        out = GenericFormatter().format(_parsed(success=False, guidance=[guidance]))
        assert "RUF001" in out.failures_terminal
        assert "Ambiguous unicode character" in out.failures_terminal

    def test_guidance_rendered_in_html(self):
        guidance = GuidanceBlock(rule="no-console", body="Use logger instead.")
        out = GenericFormatter().format(_parsed(success=False, guidance=[guidance]))
        assert "sensors-guidance" in out.failures_html
        assert "no-console" in out.failures_html

    def test_guidance_rendered_in_llm(self):
        guidance = GuidanceBlock(rule="no-console", body="Use logger instead.")
        out = GenericFormatter().format(_parsed(success=False, guidance=[guidance]))
        assert "no-console" in out.failures_llm
        assert "Use logger instead." in out.failures_llm
        assert "[" not in out.failures_llm

    def test_multiple_findings_all_rendered(self):
        findings = [
            Finding(file="a.ts", line=1, message="err1", severity="error"),
            Finding(file="b.ts", line=2, message="err2", severity="warning"),
        ]
        out = GenericFormatter().format(_parsed(success=False, findings=findings))
        assert "a.ts" in out.failures_terminal
        assert "b.ts" in out.failures_terminal
