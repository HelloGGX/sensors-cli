"""Tests for the git_diff parser."""

import asyncio
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from sensors.persistence.models import RunnerResult
from sensors.runners.parsers.git_diff import GitDiffParser

# ---------------------------------------------------------------------------
# Sample diff outputs
# ---------------------------------------------------------------------------

DIFF_MIXED_SRC_AND_TEST = """\
5\t2\tsrc/main.py
10\t0\tsrc/utils.py
0\t3\ttests/test_main.py
"""

DIFF_WITH_BINARY = """\
5\t2\tsrc/main.py
-\t-\tassets/logo.png
3\t1\tsrc/utils.py
"""

DIFF_EMPTY = ""

DIFF_ALL_TESTS = """\
100\t50\ttests/test_foo.py
200\t0\ttests/test_bar.py
"""

DIFF_WITH_UNTRACKED_SOURCE = """\
5\t2\tsrc/main.py
---UNTRACKED---
80\t0\tsrc/new_feature.py
"""

DIFF_WITH_UNTRACKED_TEST = """\
5\t2\tsrc/main.py
---UNTRACKED---
200\t0\ttests/test_new_feature.py
"""


# ---------------------------------------------------------------------------
# _is_test_file classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filepath,expected", [
    ("tests/test_main.py", True),
    ("test/unit/foo.py", True),
    ("spec/helpers/bar.js", True),
    ("specs/e2e/flow.ts", True),
    ("test_utils.py", True),
    ("foo.test.ts", True),
    ("foo.spec.js", True),
    ("foo_test.go", True),
    ("src/main.py", False),
    ("src/utils/helpers.py", False),
    ("sensors/config/loader.py", False),
    ("testing_utils.py", False),   # starts with "testing", not "test_"
    ("contest.py", False),          # contains "test" but not as a path segment
])
def test_is_test_file(filepath, expected):
    assert GitDiffParser._is_test_file(filepath) is expected


# ---------------------------------------------------------------------------
# Source / test separation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_source_and_test_files_separated():
    parser = GitDiffParser()
    result = await parser.parse_output(DIFF_MIXED_SRC_AND_TEST)

    assert result.output["filesChanged"] == 2          # src/main.py + src/utils.py
    assert result.output["linesAdded"] == 15           # 5+10
    assert result.output["linesRemoved"] == 2          # 2+0
    assert result.output["testFilesChanged"] == 1      # tests/test_main.py
    assert result.output["testLinesAdded"] == 0
    assert result.output["testLinesRemoved"] == 3

    src_names = [f["file"] for f in result.output["files"]]
    assert all("test" not in p for p in src_names)
    test_names = [f["file"] for f in result.output["testFiles"]]
    assert all("test" in p for p in test_names)


@pytest.mark.asyncio
async def test_all_test_files_gives_zero_source_stats():
    parser = GitDiffParser()
    result = await parser.parse_output(DIFF_ALL_TESTS)

    assert result.output["filesChanged"] == 0
    assert result.output["totalChanged"] == 0
    assert result.output["testLinesAdded"] == 300
    assert result.output["testFilesChanged"] == 2


@pytest.mark.asyncio
async def test_untracked_source_counted_in_source():
    parser = GitDiffParser()
    result = await parser.parse_output(DIFF_WITH_UNTRACKED_SOURCE)

    assert result.output["filesChanged"] == 2       # src/main.py + src/new_feature.py
    assert result.output["linesAdded"] == 85        # 5+80
    assert result.output["testFilesChanged"] == 0


@pytest.mark.asyncio
async def test_untracked_test_file_goes_to_test_bucket():
    parser = GitDiffParser()
    result = await parser.parse_output(DIFF_WITH_UNTRACKED_TEST)

    assert result.output["filesChanged"] == 1       # src/main.py only
    assert result.output["testFilesChanged"] == 1   # tests/test_new_feature.py
    assert result.output["testLinesAdded"] == 200


@pytest.mark.asyncio
async def test_binary_files_skipped():
    parser = GitDiffParser()
    result = await parser.parse_output(DIFF_WITH_BINARY)

    assert result.output["linesAdded"] == 8         # 5+3
    assert result.output["linesRemoved"] == 3       # 2+1
    assert result.output["filesChanged"] == 2       # binary excluded


@pytest.mark.asyncio
async def test_empty_output():
    parser = GitDiffParser()
    result = await parser.parse_output(DIFF_EMPTY)

    assert result.output["totalChanged"] == 0
    assert result.output["filesChanged"] == 0
    assert result.output["testFilesChanged"] == 0


@pytest.mark.asyncio
async def test_malformed_input_does_not_raise():
    parser = GitDiffParser()
    result = await parser.parse_output("not\tvalid\tat\tall\ngarbage")
    assert result.success is True


# ---------------------------------------------------------------------------
# Runner always succeeds (no threshold)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_always_succeeds_regardless_of_size():
    parser = GitDiffParser()
    # 6 dirs × 600 lines — very large change
    huge = "\n".join(f"100\t0\tsrc/dir_{i}/file.py" for i in range(6))
    result = await parser.parse_output(huge)
    assert result.success is True


@pytest.mark.asyncio
async def test_always_succeeds_with_no_changes():
    parser = GitDiffParser()
    result = await parser.parse_output(DIFF_EMPTY)
    assert result.success is True


# ---------------------------------------------------------------------------
# Unique dirs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unique_dirs_counted():
    parser = GitDiffParser()
    output = "10\t0\tsrc/foo.py\n10\t0\tlib/bar.py\n10\t0\tconfig/baz.py\n"
    result = await parser.parse_output(output)
    assert result.output["uniqueDirs"] == 3


@pytest.mark.asyncio
async def test_same_dir_counted_once():
    parser = GitDiffParser()
    output = "10\t0\tsrc/a.py\n10\t0\tsrc/b.py\n10\t0\tsrc/c.py\n"
    result = await parser.parse_output(output)
    assert result.output["uniqueDirs"] == 1


# ---------------------------------------------------------------------------
# Source files sorted largest first
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_source_files_sorted_largest_first():
    parser = GitDiffParser()
    output = "5\t0\tsrc/small.py\n200\t0\tsrc/big.py\n50\t0\tsrc/medium.py\n"
    result = await parser.parse_output(output)
    names = [f["file"] for f in result.output["files"]]
    assert names == ["src/big.py", "src/medium.py", "src/small.py"]


# ---------------------------------------------------------------------------
# calculate_score
# ---------------------------------------------------------------------------

def test_calculate_score_uses_total_source_lines():
    parser = GitDiffParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={"totalChanged": 150},
    )
    score = parser.calculate_score(result)
    assert score.value == 150
    assert score.direction == "less"


# ---------------------------------------------------------------------------
# format_details_llm — agent gets the full per-file breakdown
# ---------------------------------------------------------------------------

def test_format_details_llm_lists_all_source_files():
    parser = GitDiffParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(), success=True,
        output={
            "linesAdded": 261, "linesRemoved": 4, "totalChanged": 265,
            "filesChanged": 7, "uniqueDirs": 6,
            "files": [
                {"file": "sensors/sensors/parsers/git_diff.py", "added": 211, "removed": 0},
                {"file": "sensors/sensors/config/loader.py", "added": 45, "removed": 0},
                {"file": "sensors/sensors/parsers/__init__.py", "added": 5, "removed": 3},
                {"file": "sensors/config/default.sensors.yaml", "added": 3, "removed": 0},
                {"file": "sensors/.sensors/sensors.sensors.yaml", "added": 3, "removed": 0},
                {"file": ".claude/settings.local.json", "added": 3, "removed": 1},
                {"file": "README.md", "added": 1, "removed": 0},
            ],
            "testLinesAdded": 587, "testLinesRemoved": 0, "testFilesChanged": 2,
            "testFiles": [
                {"file": "sensors/tests/test_git_diff.py", "added": 417, "removed": 0},
                {"file": "sensors/tests/test_builtin_defaults.py", "added": 170, "removed": 0},
            ],
        },
    )
    text = parser.format_details_llm(result)

    assert "sensors/sensors/parsers/git_diff.py" in text
    assert "sensors/sensors/config/loader.py" in text
    assert "Source changes" in text
    assert "Test changes" in text
    assert "test_git_diff.py" in text
    assert "test_builtin_defaults.py" in text


def test_format_details_llm_no_changes():
    parser = GitDiffParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(), success=True,
        output={
            "linesAdded": 0, "linesRemoved": 0, "totalChanged": 0,
            "filesChanged": 0, "uniqueDirs": 0, "files": [],
            "testLinesAdded": 0, "testLinesRemoved": 0,
            "testFilesChanged": 0, "testFiles": [],
        },
    )
    assert parser.format_details_llm(result) == "No changes since last commit."


def test_format_failures_always_empty():
    parser = GitDiffParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(), success=True,
        output={
            "linesAdded": 100, "linesRemoved": 50, "totalChanged": 150,
            "filesChanged": 20, "uniqueDirs": 20,
            "files": [{"file": f"src/dir_{i}/svc.py", "added": 5, "removed": 2} for i in range(20)],
            "testLinesAdded": 0, "testLinesRemoved": 0, "testFilesChanged": 0, "testFiles": [],
        },
    )
    assert parser.format_failures_terminal(result) == ""
    assert parser.format_failures_html(result) == ""
    assert parser.format_failures_llm(result) == ""


# ---------------------------------------------------------------------------
# Integration: real git repo
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_integration_real_git_diff(tmp_path: Path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=tmp_path, check=True, capture_output=True)

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("\n".join(f"line_{i}" for i in range(20)))
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"],
                   cwd=tmp_path, check=True, capture_output=True)

    (src_dir / "main.py").write_text("\n".join(f"modified_{i}" for i in range(25)))
    (src_dir / "new_feature.py").write_text("\n".join(f"new_{i}" for i in range(10)))
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_new.py").write_text("\n".join(f"t_{i}" for i in range(30)))

    diff_proc = await asyncio.create_subprocess_shell(
        "git diff --numstat HEAD",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        cwd=str(tmp_path),
    )
    diff_out, _ = await diff_proc.communicate()

    untracked_proc = await asyncio.create_subprocess_shell(
        "git ls-files --others --exclude-standard | "
        "while IFS= read -r f; do "
        '[ -f "$f" ] && c=$(wc -l < "$f" 2>/dev/null | tr -d " "); '
        'printf "%s\\t0\\t%s\\n" "${c:-0}" "$f"; '
        "done",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        cwd=str(tmp_path), shell=True,
    )
    untracked_out, _ = await untracked_proc.communicate()

    combined = (
        diff_out.decode("utf-8", errors="ignore")
        + "---UNTRACKED---\n"
        + untracked_out.decode("utf-8", errors="ignore")
    )

    parser = GitDiffParser()
    result = await parser.parse_output(combined)

    assert result.success is True  # always
    assert any("main.py" in f["file"] for f in result.output["files"])
    assert any("new_feature.py" in f["file"] for f in result.output["files"])
    assert all("test_new" not in f["file"] for f in result.output["files"])
    assert any("test_new" in f["file"] for f in result.output["testFiles"])
    assert result.output["linesAdded"] > 0
    assert result.output["testLinesAdded"] > 0
