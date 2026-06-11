"""Generic JSON output parser.

Expects tools to emit a SensorReading-shaped JSON object (see README).
Tolerates arbitrary text before and after the JSON object.
"""

import json
from typing import Any

from sensors.config import Finding, GuidanceBlock, Metric, ScoreInfo, SensorReading

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


def _derive_summary(findings: list[Finding]) -> str:
    if not findings:
        return "No issues"
    n = len(findings)
    return f"{n} issue{'s' if n != 1 else ''}"


def _to_severity(value: Any) -> str:
    severity = str(value or "error").lower()
    if severity in {"error", "warning", "info"}:
        return severity
    return "error"


def _to_findings(raw_findings: Any) -> list[Finding]:
    findings = []
    for raw in raw_findings or []:
        if not isinstance(raw, dict):
            continue
        findings.append(Finding(
            message=str(raw.get("message", "")),
            severity=_to_severity(raw.get("severity")),
            file=raw.get("file"),
            line=raw.get("line"),
            column=raw.get("column"),
            rule=raw.get("rule"),
            context=raw.get("context"),
        ))
    return findings


def _to_metrics(raw_metrics: Any) -> list[Metric]:
    metrics = []
    for raw in raw_metrics or []:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key", "")).strip()
        label = str(raw.get("label", "")).strip()
        if not key or not label:
            continue
        direction = str(raw.get("direction", "less")).lower()
        if direction not in {"less", "more"}:
            direction = "less"
        metrics.append(Metric(
            key=key,
            label=label,
            value=raw.get("value", 0),
            unit=raw.get("unit"),
            direction=direction,
            threshold=raw.get("threshold"),
        ))
    return metrics


def _to_guidance(raw_guidance: Any) -> list[GuidanceBlock]:
    guidance = []
    for raw in raw_guidance or []:
        if not isinstance(raw, dict):
            continue
        rule = str(raw.get("rule", "")).strip()
        body = str(raw.get("body", ""))
        if not rule or not body:
            continue
        guidance.append(GuidanceBlock(rule=rule, body=body))
    return guidance


def _score_info(raw_score: Any, findings: list[Finding]) -> ScoreInfo:
    if not isinstance(raw_score, dict):
        raw_score = {}
    direction = str(raw_score.get("direction", "less")).lower()
    if direction not in {"less", "more"}:
        direction = "less"
    return ScoreInfo(
        value=raw_score.get("value", len(findings)),
        direction=direction,
        description=str(raw_score.get("description", "Issues reported by tool")),
    )


class DefaultParser(OutputParser):
    """Parser for tools that emit SensorReading-like JSON.

    See the README section 'Default parser: Expected output format' for the schema.
    The parser is tolerant of text before and after the JSON object.
    """

    def parse(self, output: str) -> SensorReading:
        try:
            data = _extract_json(output)
        except Exception as e:
            return SensorReading(
                success=False,
                summary=f"Parse error: {e}",
                score=ScoreInfo(value=0, direction="less", description="Issues reported by tool"),
                extra={"parseError": str(e), "raw": output[:500]},
            )

        findings = _to_findings(data.get("findings"))
        metrics = _to_metrics(data.get("metrics"))
        guidance = _to_guidance(data.get("guidance"))
        score = _score_info(data.get("score"), findings)

        success: bool = data["success"] if "success" in data else len(findings) == 0
        summary: str = data["summary"] if data.get("summary") else _derive_summary(findings)
        extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}

        return SensorReading(
            success=success,
            summary=summary,
            score=score,
            findings=findings,
            metrics=metrics,
            guidance=guidance,
            extra=extra,
        )
