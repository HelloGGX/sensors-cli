"""Tests for SensorReading.formatted — verifies that readings are formatted correctly
across all three client styles (terminal, html, llm)."""

from sensors.config.result_types import Finding, GuidanceBlock, Metric, ScoreInfo, SensorReading

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCORE = ScoreInfo(value=0, direction="less", description="")


def _reading(*, success=True, summary="OK", findings=None, metrics=None, guidance=None):
    return SensorReading(
        success=success,
        summary=summary,
        score=_SCORE,
        findings=findings or [],
        metrics=metrics or [],
        guidance=guidance or [],
    )


# ---------------------------------------------------------------------------
# Summary rendering
# ---------------------------------------------------------------------------

class TestSummary:
    def test_success_is_green_terminal(self):
        out = _reading(success=True, summary="No issues").formatted
        assert out.summary_terminal == "[green]No issues[/green]"

    def test_failure_is_red_terminal(self):
        out = _reading(success=False, summary="2 errors").formatted
        assert out.summary_terminal == "[red]2 errors[/red]"

    def test_success_is_green_html(self):
        out = _reading(success=True, summary="No issues").formatted
        assert 'class="sensors-success"' in out.summary_html
        assert "No issues" in out.summary_html

    def test_failure_is_red_html(self):
        out = _reading(success=False, summary="2 errors").formatted
        assert 'class="sensors-error"' in out.summary_html

    def test_llm_is_plain_text(self):
        out = _reading(success=True, summary="No issues").formatted
        assert out.summary_llm == "No issues"

    def test_metric_threshold_green_when_above(self):
        """Primary 'more' metric above threshold -> green (success=True)."""
        reading = _reading(
            success=True,
            summary="85% branch",
            metrics=[Metric("branch", "Branch", 85.0, "%", "more", threshold=80.0)],
        )
        assert "[green]" in reading.formatted.summary_terminal

    def test_success_true_always_green_regardless_of_metric_threshold(self):
        """success=True means green even if the metric value is below its threshold.

        Color authority is reading.success, not the metric threshold. Threshold on
        a metric is informational only; the runner's config.threshold drives status.
        """
        reading = _reading(
            success=True,
            summary="60% branch",
            metrics=[Metric("branch", "Branch", 60.0, "%", "more", threshold=80.0)],
        )
        assert "[green]" in reading.formatted.summary_terminal
        assert "[red]" not in reading.formatted.summary_terminal


# ---------------------------------------------------------------------------
# Failures rendering
# ---------------------------------------------------------------------------

class TestFailures:
    def test_success_returns_empty(self):
        out = _reading(success=True).formatted
        assert out.failures_terminal == ""
        assert out.failures_html == ""
        assert out.failures_llm == ""

    def test_no_findings_falls_back_to_summary(self):
        """When there are no findings, the label is shown as the failure."""
        out = _reading(success=False, summary="Parse error: bad JSON").formatted
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
        out = _reading(success=False, findings=[finding]).formatted
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
        out = _reading(success=False, findings=[finding]).formatted
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
        out = _reading(success=False, findings=[finding]).formatted
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
        out = _reading(success=False, findings=[finding]).formatted
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
        out = _reading(success=False, findings=[finding]).formatted
        assert "Circular dependency detected" in out.failures_terminal

    def test_guidance_rendered_in_terminal(self):
        guidance = GuidanceBlock(
            rule="no-explicit-any",
            body="Use a specific type instead of any.",
        )
        out = _reading(success=False, guidance=[guidance]).formatted
        assert "no-explicit-any" in out.failures_terminal
        assert "Use a specific type" in out.failures_terminal

    def test_guidance_body_rendered_in_terminal(self):
        guidance = GuidanceBlock(
            rule="RUF001",
            body="Replace with ASCII equivalent.",
        )
        out = _reading(success=False, guidance=[guidance]).formatted
        assert "RUF001" in out.failures_terminal
        assert "Replace with ASCII equivalent." in out.failures_terminal

    def test_guidance_rendered_in_html(self):
        guidance = GuidanceBlock(rule="no-console", body="Use logger instead.")
        out = _reading(success=False, guidance=[guidance]).formatted
        assert "sensors-guidance" in out.failures_html
        assert "no-console" in out.failures_html

    def test_guidance_rendered_in_llm(self):
        guidance = GuidanceBlock(rule="no-console", body="Use logger instead.")
        out = _reading(success=False, guidance=[guidance]).formatted
        assert "no-console" in out.failures_llm
        assert "Use logger instead." in out.failures_llm
        assert "[" not in out.failures_llm

    def test_multiple_findings_all_rendered(self):
        findings = [
            Finding(file="a.ts", line=1, message="err1", severity="error"),
            Finding(file="b.ts", line=2, message="err2", severity="warning"),
        ]
        out = _reading(success=False, findings=findings).formatted
        assert "a.ts" in out.failures_terminal
        assert "b.ts" in out.failures_terminal
