"""TypeScript compiler (tsc) output parser."""

import re

from sensors.config import Finding, Metric, ParsedOutput, ScoreInfo

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

    def parse(self, output: str) -> ParsedOutput:
        try:
            errors = self._parse_errors(output)
            error_count, file_count = self._parse_summary(output, errors)
            findings = [
                Finding(
                    file=e["file"],
                    line=e["line"],
                    column=e["column"],
                    rule=e["code"],
                    message=e["message"],
                    severity="error",
                )
                for e in errors
            ]

            return ParsedOutput(
                success=error_count == 0,
                summary=self._summary_text(error_count, file_count),
                score=ScoreInfo(
                    value=error_count,
                    direction="less",
                    description="Number of type errors",
                ),
                findings=findings,
                metrics=[
                    Metric("errorCount", "Errors", error_count),
                    Metric("fileCount", "Files", file_count),
                ],
            )
        except Exception as e:
            return ParsedOutput(
                success=False,
                summary=f"Parse error: {e}",
                score=ScoreInfo(value=0, direction="less", description="Number of type errors"),
                extra={"parseError": str(e), "raw": output[:500]},
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

    def _summary_text(self, ec: int, fc: int) -> str:
        if ec == 0:
            return "No errors"
        if fc <= 1:
            return f"{ec} error{'s' if ec != 1 else ''}"
        return f"{ec} error{'s' if ec != 1 else ''} in {fc} file{'s' if fc != 1 else ''}"
