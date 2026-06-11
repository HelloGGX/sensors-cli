"""Run Ruff and attach custom guidance for selected rule codes (agent-oriented output)."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
GUIDANCE_PATH = _SCRIPT_DIR / "ruff_guidance_overrides.py"
GUIDANCE_EXPORT_NAMES = ("RULE_GUIDANCE", "MESSAGE_OVERRIDES")
GUIDANCE_JSON_KEY = "sensors_rule_guidance"
_RUFF_NOT_FOUND = (
    "Could not find ruff. Install it (e.g. `uv add --dev ruff`), ensure it is on PATH, "
    "set RUFF=/path/to/ruff, or run from a project whose .venv contains ruff."
)


@dataclass(frozen=True)
class RuleGuidance:
    short: str | None = None
    guidance: str | None = None


def _coerce_guidance_entry(code: str, entry: Any) -> RuleGuidance:
    if isinstance(entry, RuleGuidance):
        return entry
    if isinstance(entry, Mapping):
        return RuleGuidance(
            short=entry.get("short"),  # type: ignore[arg-type]
            guidance=entry.get("guidance"),  # type: ignore[arg-type]
        )
    raise ValueError(
        f"Rule {code!r} in guidance config: expected dict or RuleGuidance, "
        f"got {type(entry).__name__}"
    )


def load_guidance(path: Path) -> dict[str, RuleGuidance]:
    """Load RULE_GUIDANCE from a Python file (stdlib importlib only)."""
    if not path.is_file():
        return {}
    spec = importlib.util.spec_from_file_location("ruff_rule_guidance_config", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load guidance module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raw: Any = None
    for name in GUIDANCE_EXPORT_NAMES:
        if hasattr(module, name):
            raw = getattr(module, name)
            break
    if raw is None:
        raise ValueError(
            f"{path} must define one of: {', '.join(GUIDANCE_EXPORT_NAMES)}"
        )
    if not isinstance(raw, dict):
        raise ValueError(f"Expected dict in {path}, got {type(raw).__name__}")
    return {str(code): _coerce_guidance_entry(str(code), entry) for code, entry in raw.items()}


def _project_search_roots() -> list[Path]:
    seen: set[Path] = set()
    roots: list[Path] = []
    for start in (Path.cwd(), _SCRIPT_DIR):
        path = start.resolve()
        for _ in range(12):
            if path not in seen:
                seen.add(path)
                roots.append(path)
            if (path / "pyproject.toml").is_file() or (path / ".git").is_dir():
                break
            parent = path.parent
            if parent == path:
                break
            path = parent
    return roots


def resolve_ruff_command() -> list[str]:
    """Find ruff without requiring an activated venv (PATH, .venv, uv run)."""
    if env_ruff := os.environ.get("RUFF"):
        return [env_ruff]
    if found := shutil.which("ruff"):
        return [found]
    for root in _project_search_roots():
        for rel in (".venv/bin/ruff", "venv/bin/ruff"):
            candidate = root / rel
            if candidate.is_file():
                return [str(candidate)]
    if shutil.which("uv"):
        return ["uv", "run", "ruff"]
    raise FileNotFoundError(_RUFF_NOT_FOUND)


def build_ruff_command(
    ruff_args: Sequence[str],
    ruff_cmd: Sequence[str] | None = None,
) -> list[str]:
    args = list(ruff_args)
    if not args or args[0] != "check":
        args = ["check", *args]
    filtered: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in ("--output-format", "-f"):
            skip_next = True
            continue
        if arg.startswith("--output-format="):
            continue
        filtered.append(arg)
    prefix = list(ruff_cmd) if ruff_cmd is not None else resolve_ruff_command()
    return [*prefix, *filtered, "--output-format", "json"]


def run_ruff(
    ruff_args: Sequence[str],
    ruff_cmd: Sequence[str] | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    cmd = build_ruff_command(ruff_args, ruff_cmd=ruff_cmd)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    stdout = proc.stdout.strip()
    diagnostics: list[dict[str, Any]] = []
    if stdout:
        parsed = json.loads(stdout)
        if not isinstance(parsed, list):
            raise ValueError("Expected JSON array from ruff")
        diagnostics = parsed
    return proc.returncode, diagnostics


def is_guidance_entry(entry: Any) -> bool:
    return isinstance(entry, dict) and GUIDANCE_JSON_KEY in entry


def split_ruff_output(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if items and is_guidance_entry(items[-1]):
        block = items[-1][GUIDANCE_JSON_KEY]
        return items[:-1], block if isinstance(block, dict) else None
    return items, None


def _build_findings_from_diagnostics(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert ruff diagnostics to finding dicts (shared logic for all output formats)."""
    cwd = Path.cwd()
    findings = []
    for d in diagnostics:
        if not isinstance(d, dict) or "code" not in d:
            continue
        loc = d.get("location") or {}
        filename = d.get("filename", "")
        try:
            file_str = str(Path(filename).relative_to(cwd))
        except ValueError:
            file_str = filename
        findings.append({
            "message": str(d.get("message", "")),
            "severity": "error",
            "file": file_str,
            "line": int(loc.get("row", 0)),
            "rule": str(d.get("code", "")),
        })
    return findings


def build_json_output(
    diagnostics: list[dict[str, Any]],
    guidance_by_code: dict[str, RuleGuidance],
) -> list[dict[str, Any]]:
    """Ruff diagnostics unchanged; append guidance only for violations we document."""
    findings = _build_findings_from_diagnostics(diagnostics)
    if not findings:
        return []

    triggered_codes = sorted(
        {f["rule"] for f in findings if f["rule"] in guidance_by_code}
    )
    if not triggered_codes:
        return findings

    rules = {
        code: {
            "short": guidance_by_code[code].short,
            "guidance": guidance_by_code[code].guidance or "",
        }
        for code in triggered_codes
    }
    return [
        *findings,
        {
            GUIDANCE_JSON_KEY: {
                "triggered": triggered_codes,
                "rules": rules,
            },
        },
    ]


def build_default_output(
    diagnostics: list[dict[str, Any]],
    guidance_by_code: dict[str, RuleGuidance] | None = None,
) -> dict[str, Any]:
    """Convert ruff diagnostics to the sensors default parser JSON format"""
    guidance_by_code = guidance_by_code or {}
    findings_list = _build_findings_from_diagnostics(diagnostics)

    result: dict[str, Any] = {"findings": findings_list}

    # Extract and include guidance for triggered rules
    triggered_codes = sorted(
        {f["rule"] for f in findings_list if f["rule"] in guidance_by_code}
    )
    if triggered_codes:
        guidance_list = []
        for code in triggered_codes:
            entry = guidance_by_code[code]
            guidance_list.append({
                "rule": code,
                "body": entry.short + " " + (entry.guidance or ""),
            })
        result["guidance"] = guidance_list

    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run ruff check with JSON output enriched by custom rule guidance.",
    )
    parser.add_argument(
        "--ruff",
        metavar="CMD",
        help="Ruff executable or 'uv' style prefix (default: auto-detect PATH, .venv, uv run)",
    )
    parser.add_argument(
        "--sensors-format",
        choices=["ruff", "default"],
        default="default",
        dest="sensors_format",
        help="Output format: 'default' (sensors default parser format, default) or 'ruff' (ruff native JSON array)",
    )
    parser.add_argument(
        "ruff_args",
        nargs=argparse.REMAINDER,
        help="Arguments passed to ruff (default: check .). Prefix with -- to pass flags.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    ruff_args = args.ruff_args
    if ruff_args and ruff_args[0] == "--":
        ruff_args = ruff_args[1:]
    if not ruff_args:
        ruff_args = ["check", "."]

    guidance_by_code = load_guidance(GUIDANCE_PATH)
    ruff_cmd = shlex.split(args.ruff) if args.ruff else None
    try:
        exit_code, diagnostics = run_ruff(ruff_args, ruff_cmd=ruff_cmd)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.sensors_format == "default":
        print(json.dumps(build_default_output(diagnostics, guidance_by_code=guidance_by_code)))
    else:
        output = build_json_output(diagnostics, guidance_by_code)
        print(json.dumps(output, indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
