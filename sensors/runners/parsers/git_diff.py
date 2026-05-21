"""Git diff stats parser — reports change size data for agent review.

This runner does not make pass/fail judgements.  It always reports success
and provides the agent with structured data about what has changed since the
last commit (files, paths, line counts, source vs test split) so the agent
can apply its own judgement about whether the change has grown too large or
too spread out.

Guidance for the agent is supplied via the runner's ``prompt`` field in the
sensors config (see ``_BUILTIN_RUNNER_DEFAULTS`` in loader.py).
"""

import re
from datetime import datetime
from pathlib import Path

from sensors.persistence.models import RunnerResult, ScoreInfo

from .base import OutputParser

# Directories named test / tests / spec / specs (case-insensitive)
_TEST_DIR_RE = re.compile(r'^tests?$|^specs?$', re.IGNORECASE)
# Filenames starting with test_ or containing .test. / .spec. / _test. / _spec.
_TEST_FILE_RE = re.compile(r'^test_|[._](test|spec)\.', re.IGNORECASE)


class GitDiffParser(OutputParser):
    """Parser for git diff --numstat output.

    Designed for use with this command (or a variant of it):

        git diff --numstat HEAD;
        echo "---UNTRACKED---";
        git ls-files --others --exclude-standard | while IFS= read -r f; do
          [ -f "$f" ] && c=$(wc -l < "$f" 2>/dev/null | tr -d ' ');
          printf "%s\\t0\\t%s\\n" "${c:-0}" "$f";
        done

    The ``---UNTRACKED---`` section is optional; if absent, only tracked
    file diffs are counted.  Untracked files are treated as pure additions.

    Test files (any file whose path contains a test/spec directory, or whose
    filename matches common test-file patterns) are reported separately and
    do not appear in the source file breakdown.

    This runner always reports success — it is purely informational.  The
    agent is expected to read the data and apply its own judgement.
    """

    # -- Classification --

    @staticmethod
    def _is_test_file(filepath: str) -> bool:
        """Return True if the path looks like a test/spec file."""
        p = Path(filepath)
        if any(_TEST_DIR_RE.match(part) for part in p.parts[:-1]):
            return True
        return bool(_TEST_FILE_RE.search(p.name))

    # -- Parsing --

    async def parse_output(self, output: str) -> RunnerResult:
        try:
            source_files: list[dict] = []
            test_files: list[dict] = []

            if "---UNTRACKED---" in output:
                diff_section, untracked_section = output.split("---UNTRACKED---", 1)
            else:
                diff_section = output
                untracked_section = ""

            for line in diff_section.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split("\t", 2)
                if len(parts) != 3:
                    continue
                added_str, removed_str, filename = parts
                if added_str == "-" or removed_str == "-":
                    continue  # binary file
                try:
                    added, removed = int(added_str), int(removed_str)
                except ValueError:
                    continue
                entry = {"file": filename, "added": added, "removed": removed}
                (test_files if self._is_test_file(filename) else source_files).append(entry)

            for line in untracked_section.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split("\t", 2)
                if len(parts) != 3:
                    continue
                added_str, _, filename = parts
                if added_str == "-":
                    continue
                try:
                    added = int(added_str)
                except ValueError:
                    continue
                entry = {"file": filename, "added": added, "removed": 0}
                (test_files if self._is_test_file(filename) else source_files).append(entry)

            # Sort source files largest first so the agent sees the biggest changes up top
            source_files.sort(key=lambda f: f["added"] + f["removed"], reverse=True)

            src_added = sum(f["added"] for f in source_files)
            src_removed = sum(f["removed"] for f in source_files)
            src_unique_dirs = len({str(Path(f["file"]).parent) for f in source_files})
            test_added = sum(f["added"] for f in test_files)
            test_removed = sum(f["removed"] for f in test_files)

            return RunnerResult(
                timestamp=datetime.now(),
                success=True,  # always — agent decides whether to reflect
                output={
                    "linesAdded": src_added,
                    "linesRemoved": src_removed,
                    "totalChanged": src_added + src_removed,
                    "filesChanged": len(source_files),
                    "uniqueDirs": src_unique_dirs,
                    "files": source_files,
                    "testLinesAdded": test_added,
                    "testLinesRemoved": test_removed,
                    "testFilesChanged": len(test_files),
                    "testFiles": test_files,
                },
            )
        except Exception as e:
            return RunnerResult(
                timestamp=datetime.now(),
                success=True,
                output={"parseError": str(e), "raw": output[:500]},
            )

    def calculate_score(self, result: RunnerResult) -> ScoreInfo:
        """Track total source lines changed as a trend metric."""
        return ScoreInfo(
            value=result.output.get("totalChanged", 0),
            direction="less",
            description="Total source lines changed since last commit",
        )

    # -- Internal helpers --

    def _headline(self, result: RunnerResult) -> str:
        src_added = result.output.get("linesAdded", 0)
        src_removed = result.output.get("linesRemoved", 0)
        src_files = result.output.get("filesChanged", 0)
        src_dirs = result.output.get("uniqueDirs", 0)
        test_added = result.output.get("testLinesAdded", 0)
        test_removed = result.output.get("testLinesRemoved", 0)
        test_files = result.output.get("testFilesChanged", 0)

        if src_added + src_removed + test_added + test_removed == 0:
            return "No changes"

        src_part = f"src +{src_added}/-{src_removed} ({src_files}f, {src_dirs}d)"
        test_part = f"tests +{test_added}/-{test_removed} ({test_files}f)"
        return f"{src_part}  |  {test_part}"

    # -- Details --

    def format_details_terminal(self, result: RunnerResult) -> str:
        return f"[green]{self._headline(result)}[/green]"

    def format_details_html(self, result: RunnerResult) -> str:
        return f'<span class="sensors-success">{self._headline(result)}</span>'

    def format_details_llm(self, result: RunnerResult) -> str:
        src_added = result.output.get("linesAdded", 0)
        src_removed = result.output.get("linesRemoved", 0)
        src_files = result.output.get("filesChanged", 0)
        src_dirs = result.output.get("uniqueDirs", 0)
        test_added = result.output.get("testLinesAdded", 0)
        test_removed = result.output.get("testLinesRemoved", 0)
        test_files_count = result.output.get("testFilesChanged", 0)

        if src_added + src_removed + test_added + test_removed == 0:
            return "No changes since last commit."

        lines = [
            f"Source changes: +{src_added}/-{src_removed} across {src_files} files in {src_dirs} dirs",
        ]
        for f in result.output.get("files", []):
            lines.append(f"  +{f['added']}/-{f['removed']}  {f['file']}")

        lines.append(
            f"Test changes: +{test_added}/-{test_removed} across {test_files_count} files"
        )
        for f in result.output.get("testFiles", []):
            lines.append(f"  +{f['added']}/-{f['removed']}  {f['file']}")

        return "\n".join(lines)

    # -- Failures (never triggered — runner always succeeds) --

    def format_failures_terminal(self, result: RunnerResult) -> str:
        return ""

    def format_failures_html(self, result: RunnerResult) -> str:
        return ""

    def format_failures_llm(self, result: RunnerResult) -> str:
        return ""
