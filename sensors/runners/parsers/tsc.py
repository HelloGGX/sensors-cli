"""TypeScript compiler (tsc) output parser."""

import re
from datetime import datetime

from sensors.config import RunnerResult, ScoreInfo

from .base import OutputParser

# filepath:line:col - error TSxxxx: message
_ERROR_RE = re.compile(
    r"^(.+?)\((\d+),(\d+)\):\s+error\s+(TS\d+):\s+(.+)$"
    r"|"
    r"^(.+?):(\d+):(\d+)\s+-\s+error\s+(TS\d+):\s+(.+)$",
    re.MULTILINE,
)

# "Found N error(s) in N file(s)." or "Found N error."
_SUMMARY_RE = re.compile(
    r"Found\s+(\d+)\s+errors?\s+in\s+(\d+)\s+files?\.",
    re.MULTILINE,
)

_SUMMARY_SINGLE_FILE_RE = re.compile(
    r"Found\s+(\d+)\s+errors?\.",
    re.MULTILINE,
)

# tsc --watch completion marker
_WATCH_COMPLETE_RE = re.compile(
    r"Found\s+\d+\s+errors?\.?\s+Watching for file changes"
)


class TscParser(OutputParser):
    """Parser for TypeScript compiler (tsc) text output."""

    async def parse_output(self, output: str) -> RunnerResult:
        try:
            errors = self._parse_errors(output)
            error_count, file_count = self._parse_summary(output, errors)

            return RunnerResult(
                timestamp=datetime.now(),
                success=error_count == 0,
                output={
                    "errorCount": error_count,
                    "fileCount": file_count,
                    "errors": errors,
                },
            )
        except Exception as e:
            return RunnerResult(
                timestamp=datetime.now(),
                success=False,
                output={"parseError": str(e), "raw": output[:500]},
            )

    def _parse_errors(self, output: str) -> list[dict]:
        errors: list[dict] = []
        for m in _ERROR_RE.finditer(output):
            if m.group(1) is not None:
                file, line, col, code, message = m.group(1, 2, 3, 4, 5)
            else:
                file, line, col, code, message = m.group(6, 7, 8, 9, 10)
            errors.append({
                "file": file.strip(),
                "line": int(line),
                "column": int(col),
                "code": code,
                "message": message.strip(),
            })
        return errors

    def _parse_summary(self, output: str, errors: list[dict]) -> tuple[int, int]:
        m = _SUMMARY_RE.search(output)
        if m:
            return int(m.group(1)), int(m.group(2))
        m = _SUMMARY_SINGLE_FILE_RE.search(output)
        if m:
            return int(m.group(1)), 1
        error_count = len(errors)
        file_count = len({e["file"] for e in errors})
        return error_count, file_count

    def is_watch_run_complete(self, line: str) -> bool:
        return bool(_WATCH_COMPLETE_RE.search(line))

    def calculate_score(self, result: RunnerResult) -> ScoreInfo:
        return ScoreInfo(
            value=result.output.get("errorCount", 0),
            direction="less",
            description="Number of type errors",
        )

    # -- Details --

    def format_details_terminal(self, result: RunnerResult) -> str:
        text = self._summary_text(result)
        if result.success:
            return f"[green]{text}[/green]"
        return f"[red]{text}[/red]"

    def format_details_html(self, result: RunnerResult) -> str:
        text = self._summary_text(result)
        css = "sensors-success" if result.success else "sensors-error"
        return f'<span class="{css}">{text}</span>'

    def format_details_llm(self, result: RunnerResult) -> str:
        return self._summary_text(result)

    # -- Failures --

    def format_failures_terminal(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        errors = result.output.get("errors", [])
        if not errors:
            ec = result.output.get("errorCount", 0)
            return f"[red]{ec} errors (no details available)[/red]"
        lines = []
        for e in errors:
            lines.append(
                f"  [red]{e['file']}:{e['line']}:{e['column']}[/red] "
                f"[dim]{e['code']}[/dim] {e['message']}"
            )
        return "\n".join(lines)

    def format_failures_html(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        errors = result.output.get("errors", [])
        if not errors:
            ec = result.output.get("errorCount", 0)
            return f'<span class="sensors-error">{ec} errors (no details available)</span>'
        parts = []
        for e in errors:
            parts.append(
                f'<div class="sensors-violation">'
                f'<span class="sensors-file">{e["file"]}:{e["line"]}:{e["column"]}</span> '
                f'<span class="sensors-error">{e["code"]}</span> '
                f'<span class="sensors-message">{e["message"]}</span>'
                f'</div>'
            )
        return "\n".join(parts)

    def format_failures_llm(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        errors = result.output.get("errors", [])
        if not errors:
            ec = result.output.get("errorCount", 0)
            return f"{ec} errors (no details available)"
        lines = []
        for e in errors:
            lines.append(f"  {e['file']}:{e['line']}:{e['column']} {e['code']} {e['message']}")
        return "\n".join(lines)

    # -- Helpers --

    def _summary_text(self, result: RunnerResult) -> str:
        ec = result.output.get("errorCount", 0)
        fc = result.output.get("fileCount", 0)
        if ec == 0:
            return "No errors"
        if fc <= 1:
            return f"{ec} error{'s' if ec != 1 else ''}"
        return f"{ec} error{'s' if ec != 1 else ''} in {fc} file{'s' if fc != 1 else ''}"
