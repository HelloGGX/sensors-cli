#!/usr/bin/env python3
"""Query a Stryker mutation-testing JSON report from the command line.

Usage:
    python query_stryker.py <report.json> <command> [options]

Commands:
    summary    Overall status totals, mutation scores, thresholds.
    files      Per-file breakdown, default sorted by mutation score asc.
    hotspots   Lines with the most survivors / no-coverage mutants.
    tests      Test effectiveness: weak, unused, or top-killer tests.

Examples

# 1. Overall health — mutation score, status breakdown, threshold pass/fail
python .sensors/maintainability/query_stryker.py reports/mutation/mutation.json summary

# 2. Worst files first, with an action hint (strengthen assertions vs add tests)
python .sensors/maintainability/query_stryker.py reports/mutation/mutation.json files --top 10 -v

# 3. Same, but only for files you've changed in git (auto-detects the repo)
python .sensors/maintainability/query_stryker.py reports/mutation/mutation.json files --changed -v

# 4. Zoom into one file: every (line, actionable counts, sample mutators)
python .sensors/maintainability/query_stryker.py reports/mutation/mutation.json hotspots --file server/services/ai-summaries.ts --top 30

"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable


# --- Data model --------------------------------------------------------------

# Stryker mutant statuses we care about for planning fixes.
ACTIONABLE_SURVIVED = "Survived"
ACTIONABLE_NOCOV = "NoCoverage"
DETECTED = {"Killed", "Timeout"}
VALID = DETECTED | {ACTIONABLE_SURVIVED, ACTIONABLE_NOCOV}


@dataclass
class Mutant:
    file: str
    line: int
    mutator: str
    status: str
    covered_by: tuple[str, ...]
    killed_by: tuple[str, ...]


@dataclass
class Report:
    mutants: list[Mutant]
    test_names: dict[str, str]  # test id -> human name
    thresholds: dict[str, Any] = field(default_factory=dict)
    project_root: str = ""


# --- Loading -----------------------------------------------------------------


def load_report(path: str) -> Report:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        die(f"report not found: {path}")
    except json.JSONDecodeError as e:
        die(f"invalid JSON in {path}: {e}")

    if not isinstance(data, dict):
        die("expected top-level JSON object")

    files = data.get("files")
    if not isinstance(files, dict):
        die("report is missing a 'files' object")

    mutants: list[Mutant] = []
    for file_path, file_result in files.items():
        if not isinstance(file_result, dict):
            continue
        raw_mutants = file_result.get("mutants") or []
        if not isinstance(raw_mutants, list):
            continue
        for m in raw_mutants:
            if not isinstance(m, dict):
                continue
            loc = m.get("location") or {}
            start = loc.get("start") or {}
            line = start.get("line") if isinstance(start.get("line"), int) else 0
            covered = m.get("coveredBy") or []
            killed = m.get("killedBy") or []
            mutants.append(
                Mutant(
                    file=str(file_path),
                    line=int(line or 0),
                    mutator=str(m.get("mutatorName", "")),
                    status=str(m.get("status", "")),
                    covered_by=tuple(str(x) for x in covered if isinstance(x, (str, int))),
                    killed_by=tuple(str(x) for x in killed if isinstance(x, (str, int))),
                )
            )

    test_names: dict[str, str] = {}
    test_files = data.get("testFiles")
    if isinstance(test_files, dict):
        for tf_path, tf in test_files.items():
            if not isinstance(tf, dict):
                continue
            for t in tf.get("tests") or []:
                if not isinstance(t, dict):
                    continue
                tid = t.get("id")
                if tid is None:
                    continue
                name = str(t.get("name") or f"test#{tid}")
                test_names[str(tid)] = f"{name}  ({tf_path})"

    thresholds = data.get("thresholds") if isinstance(data.get("thresholds"), dict) else {}
    project_root = data.get("projectRoot") if isinstance(data.get("projectRoot"), str) else ""

    return Report(
        mutants=mutants,
        test_names=test_names,
        thresholds=thresholds,
        project_root=project_root,
    )


def die(msg: str, code: int = 2) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


# --- Shared helpers ----------------------------------------------------------


def pct(num: int, denom: int) -> float:
    if denom <= 0:
        return 0.0
    return round(100.0 * num / denom, 2)


def status_counts(mutants: Iterable[Mutant]) -> dict[str, int]:
    c: dict[str, int] = defaultdict(int)
    for m in mutants:
        c[m.status] += 1
    return dict(c)


def resolve_git_root(report: Report, override: str | None) -> str:
    """Pick a directory to run `git` in. Explicit override wins, else projectRoot
    if it exists on disk, else cwd."""
    if override:
        return override
    if report.project_root and os.path.isdir(report.project_root):
        return report.project_root
    return os.getcwd()


def git_changed_files(git_root: str) -> set[str]:
    """Return the set of paths changed according to `git status --porcelain`,
    relative to `git_root`. Includes modified, added, renamed (new name),
    copied, and untracked. Paths are normalised with forward slashes."""
    try:
        proc = subprocess.run(
            ["git", "-C", git_root, "status", "--porcelain=v1"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        die("git is not installed or not on PATH")
    if proc.returncode != 0:
        die(f"git status failed in {git_root}: {proc.stderr.strip() or proc.stdout.strip()}")

    changed: set[str] = set()
    for raw in proc.stdout.splitlines():
        if len(raw) < 4:
            continue
        # Porcelain v1 line: "XY path" (2 status chars, 1 space, path).
        # For renames/copies the path field is "old -> new" — keep the new path.
        payload = raw[3:]
        if " -> " in payload:
            payload = payload.split(" -> ", 1)[1]
        # Strip enclosing quotes that git adds for paths with unusual chars.
        if payload.startswith('"') and payload.endswith('"'):
            payload = payload[1:-1]
        changed.add(payload.replace(os.sep, "/"))
    return changed


def print_title(title: str) -> None:
    print(f"=== {title} ===")
    print()


def render_table(headers: list[str], rows: list[list[str]], aligns: list[str] | None = None) -> str:
    cols = len(headers)
    aligns = aligns or ["l"] * cols
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(cells: list[str]) -> str:
        parts = []
        for i, cell in enumerate(cells):
            if aligns[i] == "r":
                parts.append(cell.rjust(widths[i]))
            else:
                parts.append(cell.ljust(widths[i]))
        return "  ".join(parts).rstrip()

    out = [fmt_row(headers), fmt_row(["-" * w for w in widths])]
    if not rows:
        out.append("(no rows)")
    for row in rows:
        out.append(fmt_row(row))
    return "\n".join(out)


# --- summary -----------------------------------------------------------------


def cmd_summary(report: Report, _args: argparse.Namespace) -> None:
    print_title("Mutation testing summary")
    counts = status_counts(report.mutants)
    killed = counts.get("Killed", 0)
    timeout = counts.get("Timeout", 0)
    survived = counts.get("Survived", 0)
    noc = counts.get("NoCoverage", 0)
    detected = killed + timeout
    valid = detected + survived + noc
    covered = detected + survived

    score_total = pct(detected, valid)
    score_covered = pct(detected, covered)

    print(f"Mutants total:        {len(report.mutants)}")
    print()
    print("Status breakdown:")
    # Print in a stable, meaningful order, then any extras.
    order = ["Killed", "Timeout", "Survived", "NoCoverage", "CompileError", "RuntimeError", "Ignored", "Pending"]
    seen = set()
    for s in order:
        if s in counts:
            print(f"  {s:<14} {counts[s]}")
            seen.add(s)
    for s, n in counts.items():
        if s not in seen:
            print(f"  {s:<14} {n}")
    print()
    print(f"Mutation score (of total):   {score_total:.2f}%   ({detected}/{valid})")
    print(f"Mutation score (of covered): {score_covered:.2f}%   ({detected}/{covered})")

    if report.thresholds:
        high = report.thresholds.get("high")
        low = report.thresholds.get("low")
        tag = "pass"
        try:
            if isinstance(high, (int, float)) and score_covered >= float(high):
                tag = "pass"
            elif isinstance(low, (int, float)) and score_covered >= float(low):
                tag = "warn"
            else:
                tag = "fail"
        except (TypeError, ValueError):
            pass
        print(f"Thresholds:  high={high}  low={low}  -> {tag}")

    if survived or noc:
        print()
        print("Hint: `survived` mutants need stronger assertions;")
        print("      `noCoverage` mutants need new tests that execute that code.")


# --- files -------------------------------------------------------------------


@dataclass
class FileAgg:
    path: str
    killed: int = 0
    timeout: int = 0
    survived: int = 0
    nocoverage: int = 0
    other: int = 0
    total: int = 0

    @property
    def detected(self) -> int:
        return self.killed + self.timeout

    @property
    def valid(self) -> int:
        return self.detected + self.survived + self.nocoverage

    @property
    def score(self) -> float:
        return pct(self.detected, self.valid)


def aggregate_files(mutants: Iterable[Mutant]) -> dict[str, FileAgg]:
    out: dict[str, FileAgg] = {}
    for m in mutants:
        agg = out.setdefault(m.file, FileAgg(path=m.file))
        agg.total += 1
        if m.status == "Killed":
            agg.killed += 1
        elif m.status == "Timeout":
            agg.timeout += 1
        elif m.status == "Survived":
            agg.survived += 1
        elif m.status == "NoCoverage":
            agg.nocoverage += 1
        else:
            agg.other += 1
    return out


def cmd_files(report: Report, args: argparse.Namespace) -> None:
    sort_label = {
        "score": "lowest mutation score",
        "survived": "most survived mutants",
        "nocoverage": "most uncovered mutants",
        "total": "most mutants",
    }.get(args.sort, args.sort)
    if args.changed:
        title = f"Mutation scores for currently changed files (sorted by {sort_label})"
    else:
        title = f"Mutation scores: top {args.top} files by {sort_label}"
    print_title(title)

    aggs = list(aggregate_files(report.mutants).values())

    changed: set[str] | None = None
    if args.changed:
        git_root = resolve_git_root(report, args.git_root)
        changed = git_changed_files(git_root)
        print(f"# git root: {git_root}")
        print(f"# changed files in git: {len(changed)}")
        if not changed:
            print("# (nothing changed — nothing to show)")
            return
        report_paths = {a.path for a in aggs}
        # Match by exact path or by trailing-path suffix (handles reports whose paths
        # are rooted at a subdir relative to the git root, or vice versa).
        def matches(agg_path: str) -> bool:
            if agg_path in changed:
                return True
            for c in changed:
                if agg_path.endswith("/" + c) or c.endswith("/" + agg_path):
                    return True
            return False

        aggs = [a for a in aggs if matches(a.path)]

        if not aggs:
            print("# no changed files appear in the mutation report")
            return

    if args.min_mutants:
        aggs = [a for a in aggs if a.total >= args.min_mutants]

    sort_key = args.sort
    if sort_key == "score":
        # Lowest score first; tiebreak on larger file (more mutants) so big problems surface.
        aggs.sort(key=lambda a: (a.score, -a.total, a.path))
    elif sort_key == "survived":
        aggs.sort(key=lambda a: (-a.survived, a.path))
    elif sort_key == "nocoverage":
        aggs.sort(key=lambda a: (-a.nocoverage, a.path))
    elif sort_key == "total":
        aggs.sort(key=lambda a: (-a.total, a.path))

    aggs = aggs[: args.top]

    rows: list[list[str]] = []
    for a in aggs:
        hint = ""
        if args.verbose:
            if a.survived > a.nocoverage:
                hint = "strengthen assertions"
            elif a.nocoverage > 0:
                hint = "add tests for uncovered code"
            else:
                hint = "ok"
        row = [
            f"{a.score:.1f}%",
            str(a.killed),
            str(a.survived),
            str(a.nocoverage),
            str(a.timeout),
            str(a.total),
            a.path,
        ]
        if args.verbose:
            row.append(hint)
        rows.append(row)

    headers = ["score", "killed", "survived", "noCov", "timeout", "total", "file"]
    aligns = ["r", "r", "r", "r", "r", "r", "l"]
    if args.verbose:
        headers.append("hint")
        aligns.append("l")

    print(render_table(headers, rows, aligns))

    if aggs:
        sample = aggs[0].path
        print()
        print(f"Tip: drill into a file's hotspot lines with, e.g.:")
        print(f"  python .sensors/maintainability/query_stryker.py {args.report} hotspots --file {sample}")


# --- hotspots ----------------------------------------------------------------


def cmd_hotspots(report: Report, args: argparse.Namespace) -> None:
    scope = f" in files matching '{args.file}'" if args.file else ""
    print_title(f"Top {args.top} hotspot lines by actionable mutants{scope}")
    # (file, line) -> dict[status -> count], plus sample mutator names for actionable statuses.
    buckets: dict[tuple[str, int], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    samples: dict[tuple[str, int], list[str]] = defaultdict(list)

    for m in report.mutants:
        if args.file and args.file not in m.file:
            continue
        key = (m.file, m.line)
        buckets[key][m.status] += 1
        if m.status in (ACTIONABLE_SURVIVED, ACTIONABLE_NOCOV):
            if m.mutator and m.mutator not in samples[key]:
                if len(samples[key]) < 3:
                    samples[key].append(m.mutator)

    entries: list[tuple[tuple[str, int], dict[str, int]]] = list(buckets.items())
    # Rank by (survived + noCoverage) desc, then survived desc, then file:line.
    entries.sort(
        key=lambda kv: (
            -(kv[1].get(ACTIONABLE_SURVIVED, 0) + kv[1].get(ACTIONABLE_NOCOV, 0)),
            -kv[1].get(ACTIONABLE_SURVIVED, 0),
            kv[0][0],
            kv[0][1],
        )
    )

    # Drop entries with no actionable mutants unless a --file filter is in effect
    # (in which case the user wants to see everything in that file).
    if not args.file:
        entries = [
            e for e in entries if (e[1].get(ACTIONABLE_SURVIVED, 0) + e[1].get(ACTIONABLE_NOCOV, 0)) > 0
        ]

    entries = entries[: args.top]

    rows: list[list[str]] = []
    for (file_, line), c in entries:
        rows.append(
            [
                str(c.get(ACTIONABLE_SURVIVED, 0)),
                str(c.get(ACTIONABLE_NOCOV, 0)),
                str(c.get("Killed", 0) + c.get("Timeout", 0)),
                f"{file_}:{line}",
                ", ".join(samples[(file_, line)]),
            ]
        )

    headers = ["survived", "noCov", "killed", "location", "sample mutators"]
    aligns = ["r", "r", "r", "l", "l"]
    print(render_table(headers, rows, aligns))

    if entries:
        print()
        print("Tip: use an LSP server (if available) on a hotspot line")
        print("     to see what calls into it — callers often reveal which tests should cover")
        print("     or assert on the mutated behaviour.")


# --- tests -------------------------------------------------------------------


@dataclass
class TestStats:
    tid: str
    name: str
    covered: int = 0
    killed: int = 0

    @property
    def kill_ratio(self) -> float:
        if self.covered <= 0:
            return 0.0
        return self.killed / self.covered


def aggregate_tests(report: Report) -> dict[str, TestStats]:
    stats: dict[str, TestStats] = {}

    def ensure(tid: str) -> TestStats:
        if tid not in stats:
            stats[tid] = TestStats(tid=tid, name=report.test_names.get(tid, f"test#{tid}"))
        return stats[tid]

    # Seed from testFiles so tests that never appear in any mutant's lists still show up.
    for tid in report.test_names:
        ensure(tid)

    for m in report.mutants:
        for tid in m.covered_by:
            ensure(tid).covered += 1
        for tid in m.killed_by:
            ensure(tid).killed += 1

    return stats


def cmd_tests(report: Report, args: argparse.Namespace) -> None:
    stats = list(aggregate_tests(report).values())

    if args.unused:
        filtered = [s for s in stats if s.killed == 0]
        filtered.sort(key=lambda s: (s.covered, s.name))
        view = "unused"
        title = f"Top {args.top} unused tests (never kill any mutant)"
    elif args.top_killers:
        filtered = [s for s in stats if s.killed > 0]
        filtered.sort(key=lambda s: (-s.killed, -s.kill_ratio, s.name))
        view = "top-killers"
        title = f"Top {args.top} killer tests (most mutants killed)"
    else:
        filtered = [s for s in stats if s.covered > 0]
        filtered.sort(key=lambda s: (s.kill_ratio, -s.covered, s.name))
        view = "weak"
        title = f"Top {args.top} weak tests (cover mutants but kill few)"

    print_title(title)
    filtered = filtered[: args.top]

    rows: list[list[str]] = []
    for s in filtered:
        rows.append(
            [
                f"{s.kill_ratio * 100:.0f}%" if s.covered else "-",
                str(s.killed),
                str(s.covered),
                s.tid,
                s.name,
            ]
        )

    headers = ["killRatio", "killed", "covered", "id", "test"]
    aligns = ["r", "r", "r", "r", "l"]
    print(render_table(headers, rows, aligns))

    if view == "unused":
        dead = sum(1 for s in stats if s.killed == 0 and s.covered == 0)
        lazy = sum(1 for s in stats if s.killed == 0 and s.covered > 0)
        print()
        print(f"{dead} test(s) cover 0 mutants (dead weight).")
        print(f"{lazy} test(s) cover mutants but kill none (missing assertions).")


# --- CLI ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Query a Stryker mutation-testing JSON report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("report", help="path to the Stryker JSON report")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("summary", help="overall status totals and mutation scores")
    sp.set_defaults(func=cmd_summary)

    fp = sub.add_parser("files", help="per-file breakdown (default: sorted by score asc)")
    fp.add_argument("--top", type=int, default=20, help="max rows to show (default 20)")
    fp.add_argument(
        "--sort",
        choices=["score", "survived", "nocoverage", "total"],
        default="score",
        help="sort key (default: score ascending = worst first)",
    )
    fp.add_argument(
        "--min-mutants",
        type=int,
        default=0,
        help="ignore files with fewer than N mutants (stabilises % on tiny files)",
    )
    fp.add_argument("-v", "--verbose", action="store_true", help="add an actionable hint column")
    fp.add_argument(
        "--changed",
        action="store_true",
        help="only show files currently modified/untracked according to `git status`",
    )
    fp.add_argument(
        "--git-root",
        default=None,
        help="directory to run `git status` in (default: report's projectRoot if it exists, else cwd)",
    )
    fp.set_defaults(func=cmd_files)

    hp = sub.add_parser("hotspots", help="lines with the most actionable mutants")
    hp.add_argument("--file", default=None, help="only consider files whose path contains this substring")
    hp.add_argument("--top", type=int, default=20, help="max rows to show (default 20)")
    hp.set_defaults(func=cmd_hotspots)

    tp = sub.add_parser("tests", help="test effectiveness views")
    group = tp.add_mutually_exclusive_group()
    group.add_argument("--weak", action="store_true", help="tests that cover mutants but kill few (default)")
    group.add_argument("--unused", action="store_true", help="tests that never kill any mutant")
    group.add_argument("--top-killers", action="store_true", help="tests that kill the most mutants")
    tp.add_argument("--top", type=int, default=20, help="max rows to show (default 20)")
    tp.set_defaults(func=cmd_tests)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = load_report(args.report)
    args.func(report, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
