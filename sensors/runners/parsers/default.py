"""Generic JSON output parser.

Expects the tool to emit a JSON object (see README for the schema).
Tolerates arbitrary text before and after the JSON object.
"""

import json
from datetime import datetime
from html import escape
from typing import Any

from sensors.config import RunnerResult, ScoreInfo

from .base import OutputParser


def _find_json_end(output: str, start: int) -> int:
    """Return the index of the closing brace that matches output[start]."""
    depth = 0
    in_string = False
    escape_next = False
    for i, ch in enumerate(output[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("Unterminated JSON object in output")


def _extract_json(output: str) -> dict[str, Any]:
    """Find and parse the first complete JSON object in output."""
    start = output.find("{")
    if start == -1:
        raise ValueError("No JSON object found in output")
    end = _find_json_end(output, start)
    obj = json.loads(output[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError("Expected a JSON object, got a different type")
    return obj


def _derive_summary(violations: list[dict[str, Any]]) -> str:
    if not violations:
        return "No issues"
    n = len(violations)
    return f"{n} issue{'s' if n != 1 else ''}"


def _severity_class(severity: str) -> str:
    if severity == "warning":
        return "sensors-warn"
    if severity == "info":
        return "sensors-success"
    return "sensors-error"


def _severity_rich(severity: str) -> str:
    if severity == "warning":
        return "yellow"
    if severity == "info":
        return "dim"
    return "red"


def _format_violation_terminal(v: dict[str, Any]) -> str:
    color = _severity_rich(v.get("severity", "error"))
    parts: list[str] = []
    if v.get("file"):
        loc = v["file"]
        if v.get("line"):
            loc += f":{v['line']}"
        parts.append(f"[{color}]{loc}[/{color}]")
    if v.get("rule"):
        parts.append(f"[dim]{v['rule']}[/dim]")
    parts.append(v.get("message", ""))
    return "  " + " ".join(parts)


def _format_violation_html(v: dict[str, Any]) -> str:
    sev = v.get("severity", "error")
    cls = _severity_class(sev)
    inner = ""
    if v.get("file"):
        loc = escape(v["file"])
        if v.get("line"):
            loc += f":{v['line']}"
        inner += f'<span class="sensors-file">{loc}</span> '
    if v.get("rule"):
        inner += f'<span class="sensors-rule">{escape(str(v["rule"]))}</span> '
    inner += f'<span class="sensors-message">{escape(v.get("message", ""))}</span>'
    return f'<div class="sensors-violation {cls}">{inner}</div>'


def _format_violation_llm(v: dict[str, Any]) -> str:
    parts: list[str] = []
    if v.get("file"):
        loc = v["file"]
        if v.get("line"):
            loc += f":{v['line']}"
        parts.append(loc)
    if v.get("rule"):
        parts.append(str(v["rule"]))
    parts.append(v.get("message", ""))
    sev = v.get("severity", "error")
    if sev != "error":
        parts.insert(0, f"[{sev.upper()}]")
    return "  " + " ".join(parts)


class DefaultParser(OutputParser):
    """Parser for tools that emit a structured JSON object.

    See the README section 'Generic JSON output format' for the full schema.
    The parser is tolerant of text before and after the JSON object.
    """

    async def parse_output(self, output: str) -> RunnerResult:
        try:
            data = _extract_json(output)
        except Exception as e:
            return RunnerResult(
                timestamp=datetime.now(),
                success=False,
                output={"parseError": str(e), "raw": output[:500]},
            )

        violations: list[dict[str, Any]] = data.get("violations") or []

        success: bool = data["success"] if "success" in data else len(violations) == 0

        summary: str = (
            data["summary"]
            if data.get("summary")
            else _derive_summary(violations)
        )

        score_raw = data.get("score") or {}
        score_value: int = (
            int(score_raw["value"])
            if "value" in score_raw
            else len(violations)
        )
        score_direction: str = score_raw.get("direction", "less")

        return RunnerResult(
            timestamp=datetime.now(),
            success=success,
            output={
                "success": success,
                "summary": summary,
                "scoreValue": score_value,
                "scoreDirection": score_direction,
                "violations": violations,
            },
        )

    def calculate_score(self, result: RunnerResult) -> ScoreInfo:
        return ScoreInfo(
            value=result.output.get("scoreValue", 0),
            direction=result.output.get("scoreDirection", "less"),
            description="Issues reported by tool",
        )

    # -- Helpers --

    def _summary(self, result: RunnerResult) -> str:
        return result.output.get("summary", "No issues")

    def _violations(self, result: RunnerResult) -> list[dict[str, Any]]:
        return result.output.get("violations", [])

    # -- Details (short one-liner) --

    def format_details_terminal(self, result: RunnerResult) -> str:
        text = self._summary(result)
        color = "green" if result.success else "red"
        return f"[{color}]{text}[/{color}]"

    def format_details_html(self, result: RunnerResult) -> str:
        text = escape(self._summary(result))
        cls = "sensors-success" if result.success else "sensors-error"
        return f'<span class="{cls}">{text}</span>'

    def format_details_llm(self, result: RunnerResult) -> str:
        return self._summary(result)

    # -- Failures (multi-line) --

    def format_failures_terminal(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        violations = self._violations(result)
        if not violations:
            return f"  [red]{self._summary(result)}[/red]"
        return "\n".join(_format_violation_terminal(v) for v in violations)

    def format_failures_html(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        violations = self._violations(result)
        if not violations:
            return f'<span class="sensors-error">{escape(self._summary(result))}</span>'
        return "\n".join(_format_violation_html(v) for v in violations)

    def format_failures_llm(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        violations = self._violations(result)
        if not violations:
            return self._summary(result)
        return "\n".join(_format_violation_llm(v) for v in violations)
