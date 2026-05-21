"""Vitest coverage output parser — reads coverage/coverage-final.json (istanbul/v8 JSON format).

Configure the runner with `result: "coverage/coverage-final.json"` so the runner passes
the JSON file content to parse_output instead of stdout. is_watch_run_complete still
uses stdout line detection for watch mode.
"""

import json
from datetime import datetime
from typing import Any

from sensors.persistence.models import RunnerResult, ScoreInfo

from .base import OutputParser


class VitestCovParser(OutputParser):
    """Parser for Vitest coverage-final.json (istanbul JSON format).

    Reads the JSON written by the `json` coverage reporter and computes
    per-file and aggregate statement / branch / function / line percentages.

    Configure the runner with:
        result: "coverage/coverage-final.json"
    """

    def is_watch_run_complete(self, line: str) -> bool:
        """Detect vitest watch-mode run completion via stdout."""
        import re
        return bool(re.search(r'Tests\s+\d+\s+(failed|passed)', line))

    async def parse_output(self, output: str) -> RunnerResult:
        try:
            data: dict[str, Any] = json.loads(output)
        except (json.JSONDecodeError, ValueError):
            return RunnerResult(
                timestamp=datetime.now(),
                success=False,
                output={
                    "parseError": (
                        'Expected JSON from coverage/coverage-final.json — '
                        'add `result: "coverage/coverage-final.json"` to your runner config '
                        'and `reporter: ["json"]` to your vitest coverage config.'
                    ),
                    "raw": output[:200],
                },
            )

        try:
            files: list[dict[str, Any]] = []
            totals = {"s": [0, 0], "f": [0, 0], "b": [0, 0]}

            for file_data in data.values():
                file_result = self._parse_file(file_data)
                files.append(file_result)
                for key in ("s", "f", "b"):
                    totals[key][0] += file_result[f"_{key}_hit"]
                    totals[key][1] += file_result[f"_{key}_total"]

            total_stmts = _pct(totals["s"][0], totals["s"][1])
            total_funcs = _pct(totals["f"][0], totals["f"][1])
            total_branch = _pct(totals["b"][0], totals["b"][1])

            # Strip internal accounting keys before storing
            clean_files = [{k: v for k, v in f.items() if not k.startswith("_")} for f in files]

            return RunnerResult(
                timestamp=datetime.now(),
                success=True,
                output={
                    "totalStmts": total_stmts,
                    "totalBranch": total_branch,
                    "totalFuncs": total_funcs,
                    "totalLines": total_stmts,  # istanbul JSON has no separate line count
                    "files": clean_files,
                },
            )
        except Exception as e:
            return RunnerResult(
                timestamp=datetime.now(),
                success=False,
                output={"parseError": str(e), "raw": output[:500]},
            )

    # -- Private helpers --

    def _parse_file(self, file_data: dict[str, Any]) -> dict[str, Any]:
        """Compute per-file coverage percentages from istanbul JSON entry."""
        path = file_data.get("path", "")
        name = path.split("/")[-1] if path else ""

        s: dict[str, int] = file_data.get("s", {})
        f: dict[str, int] = file_data.get("f", {})
        b: dict[str, list[int]] = file_data.get("b", {})
        stmt_map: dict[str, Any] = file_data.get("statementMap", {})

        s_hit = sum(1 for v in s.values() if v > 0)
        s_total = len(s)
        f_hit = sum(1 for v in f.values() if v > 0)
        f_total = len(f)
        b_hit = sum(1 for counts in b.values() for v in counts if v > 0)
        b_total = sum(len(counts) for counts in b.values())

        uncovered_lines = sorted({
            stmt_map[sid]["start"]["line"]
            for sid, count in s.items()
            if count == 0 and sid in stmt_map
        })
        uncovered_str = _format_line_ranges(uncovered_lines)

        return {
            "name": name,
            "path": path,
            "stmts": _pct(s_hit, s_total),
            "branch": _pct(b_hit, b_total),
            "funcs": _pct(f_hit, f_total),
            "lines": _pct(s_hit, s_total),
            "uncovered": uncovered_str,
            # Internal accounting keys (stripped before storage)
            "_s_hit": s_hit, "_s_total": s_total,
            "_f_hit": f_hit, "_f_total": f_total,
            "_b_hit": b_hit, "_b_total": b_total,
        }

    def calculate_score(self, result: RunnerResult) -> ScoreInfo:
        return ScoreInfo(
            value=int(result.output.get("totalBranch", 0)),
            direction="more",
            description="Branch coverage percentage",
        )

    # -- Helpers --

    def _detail_text(self, result: RunnerResult) -> str:
        if "parseError" in result.output:
            return result.output["parseError"]
        branch = result.output.get("totalBranch", 0)
        return f"{branch}% branch"

    def _cov_color(self, result: RunnerResult) -> str:
        if not result.success:
            return "red"
        cov = result.output.get("totalBranch", 0)
        if cov >= 80:
            return "green"
        if cov >= 50:
            return "yellow"
        return "red"

    # -- Details --

    def format_details_terminal(self, result: RunnerResult) -> str:
        text = self._detail_text(result)
        color = self._cov_color(result)
        return f"[{color}]{text}[/{color}]"

    def format_details_html(self, result: RunnerResult) -> str:
        text = self._detail_text(result)
        css = {
            "green": "sensors-success",
            "yellow": "sensors-warn",
            "red": "sensors-error",
        }[self._cov_color(result)]
        return f'<span class="{css}">{text}</span>'

    def format_details_llm(self, result: RunnerResult) -> str:
        return self._detail_text(result)

    # -- Failures --

    def format_failures_terminal(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        return self._format_failure_items(result, "terminal")

    def format_failures_html(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        return self._format_failure_items(result, "html")

    def format_failures_llm(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        return self._format_failure_items(result, "llm")

    def _format_failure_items(self, result: RunnerResult, style: str) -> str:
        lines: list[str] = []

        parse_error = result.output.get("parseError")
        if parse_error:
            msg = f"Parse error: {parse_error}"
            if style == "terminal":
                lines.append(f"  [red]{msg}[/red]")
            elif style == "html":
                lines.append(f'<span class="sensors-error">{msg}</span>')
            else:
                lines.append(f"  {msg}")
            return "\n".join(lines)

        uncovered_files = [f for f in result.output.get("files", []) if f.get("uncovered")]
        if uncovered_files:
            if style == "terminal":
                lines.append("  [yellow]Uncovered lines:[/yellow]")
                for f in uncovered_files:
                    lines.append(f"    [dim]{f['name']}[/dim] lines {f['uncovered']}")
            elif style == "html":
                for f in uncovered_files:
                    lines.append(
                        f'<div class="sensors-violation">'
                        f'<span class="sensors-file">{f["name"]}</span> '
                        f'<span class="sensors-warn">lines {f["uncovered"]}</span>'
                        f'</div>'
                    )
            else:
                lines.append("  Uncovered lines:")
                for f in uncovered_files:
                    lines.append(f"    {f['name']} lines {f['uncovered']}")

        return "\n".join(lines)


# -- Module-level helpers --

def _pct(hit: int, total: int) -> float:
    if total == 0:
        return 100.0
    return round(hit / total * 100, 2)


def _format_line_ranges(lines: list[int]) -> str:
    """Collapse a sorted list of line numbers into a compact range string, e.g. '10-12,15,20-21'."""
    if not lines:
        return ""
    ranges: list[str] = []
    start = end = lines[0]
    for n in lines[1:]:
        if n == end + 1:
            end = n
        else:
            ranges.append(str(start) if start == end else f"{start}-{end}")
            start = end = n
    ranges.append(str(start) if start == end else f"{start}-{end}")
    return ",".join(ranges)
