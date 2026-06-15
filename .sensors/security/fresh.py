#!/usr/bin/env python3
"""Dependency freshness checker for Python projects."""

import json
import sys
import tomllib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RED = "\x1b[31m"
RESET = "\x1b[0m"
FAIL = f"{RED}x{RESET}"

# ----------------------- config -------------------
STALE_MONTHS = 6
PROMPT = """For outdated dependencies, we should double check
- Are we really using the package?
- If no, then let's remove it.
- If yes, how big is the functionality? Is it easy to replace with a small implementation of our own?
- If yes, and it's big and specific enough that it's worth outsourcing it to a dependency, let's do some more checks:
  - Is there deprecation information?
  - Is there a better alternative?
  - How many dependencies does it have, and by how many people is it still used? -> Should we want to keep it, how big is the risk of that?
Do web research if necessary.
"""
# --------------------------------------------------

STALE_SECONDS = STALE_MONTHS * 30 * 24 * 60 * 60

IgnoreMode = str  # "ignore" | "warning"


def load_ignore_map(root: Path) -> dict[str, IgnoreMode]:
    ignore_path = root / ".sensors" / "security" / ".fresh-ignore"
    result: dict[str, IgnoreMode] = {}
    if not ignore_path.exists():
        return result
    for raw in ignore_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        pkg = parts[0]
        mode: IgnoreMode = parts[1] if len(parts) > 1 else "ignore"
        result[pkg] = mode
    return result


def load_deps(root: Path) -> tuple[list[str], list[str]]:
    pyproject_path = root / "pyproject.toml"
    if not pyproject_path.exists():
        print("pyproject.toml not found", file=sys.stderr)
        sys.exit(1)

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    project = data.get("project", {})

    # Runtime deps: strip version specifiers, extras, markers
    runtime_raw = project.get("dependencies", [])
    runtime = [_parse_dep_name(d) for d in runtime_raw]

    # Dev deps: uv uses [dependency-groups], older tools use [project.optional-dependencies]
    dev: list[str] = []
    dep_groups = data.get("dependency-groups", {})
    for group_deps in dep_groups.values():
        for d in group_deps:
            if isinstance(d, str):
                dev.append(_parse_dep_name(d))

    optional_deps = project.get("optional-dependencies", {})
    for group_deps in optional_deps.values():
        for d in group_deps:
            dev.append(_parse_dep_name(d))

    return runtime, dev


def _parse_dep_name(dep: str) -> str:
    """Strip version specifiers, extras, and markers from a dependency string."""
    import re
    # Remove environment markers (everything after ';')
    dep = dep.split(";")[0].strip()
    # Remove extras like package[extra]
    dep = re.sub(r"\[.*?\]", "", dep)
    # Remove version specifiers
    dep = re.split(r"[><=!~\s]", dep)[0].strip()
    return dep.lower()


def fetch_pypi_info(pkg: str) -> dict:
    url = f"https://pypi.org/pypi/{urllib.parse.quote(pkg)}/json"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}") from e


def fetch_pypi_downloads(pkg: str) -> int:
    """Fetch weekly downloads from pypistats.org."""
    url = f"https://pypistats.org/api/packages/{urllib.parse.quote(pkg.lower())}/recent?period=week"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("data", {}).get("last_week", 0)
    except Exception:
        return 0


import urllib.parse


def get_pkg_stats(pkg: str) -> dict:
    info = fetch_pypi_info(pkg)
    meta = info.get("info", {})

    # Find last published date across all releases
    releases = info.get("releases", {})
    latest_ts: datetime | None = None
    for files in releases.values():
        for f in files:
            upload_time = f.get("upload_time_iso_8601") or f.get("upload_time")
            if upload_time:
                try:
                    dt = datetime.fromisoformat(upload_time.replace("Z", "+00:00"))
                    if latest_ts is None or dt > latest_ts:
                        latest_ts = dt
                except ValueError:
                    pass

    if latest_ts is None:
        latest_ts = datetime.now(timezone.utc)

    classifiers = meta.get("classifiers", [])
    is_deprecated = any("Development Status :: 7 - Inactive" in c for c in classifiers)
    deprecated_msg = meta.get("description", "")[:120] if is_deprecated else None

    version = meta.get("version", "?")
    weekly_downloads = fetch_pypi_downloads(pkg)

    return {
        "last_published": latest_ts,
        "version": version,
        "deprecated": deprecated_msg if is_deprecated else None,
        "weekly_downloads": weekly_downloads,
    }


def format_age(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    days = (now - dt).days
    if days < 30:
        return f"{days}d ago"
    months = days // 30
    if months < 12:
        return f"{months}mo ago"
    return f"{months / 12:.1f}y ago"


def format_downloads(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M/wk"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k/wk"
    return f"{n}/wk"


def is_stale(dt: datetime) -> bool:
    now = datetime.now(timezone.utc)
    return (now - dt).total_seconds() > STALE_SECONDS


def is_pre_stable(version: str) -> bool:
    return version.startswith("0.")


def check_group(
    deps: list[str], label: str, ignore_map: dict[str, IgnoreMode]
) -> tuple[list[dict], list[dict]]:
    filtered = [p for p in deps if ignore_map.get(p) != "ignore"]
    print(f"{label} ({len(filtered)})\n")

    errors: list[dict] = []
    warnings: list[dict] = []

    for pkg in filtered:
        is_warn_only = ignore_map.get(pkg) == "warning"
        try:
            stats = get_pkg_stats(pkg)
            age_str = format_age(stats["last_published"])
            dl_str = format_downloads(stats["weekly_downloads"])
            version = stats["version"]
            stale = is_stale(stats["last_published"])
            pre_stable = is_pre_stable(version)

            issues: list[str] = []
            if stats["deprecated"]:
                issues.append(f'deprecated')
            if stale:
                issues.append(f"last published {age_str}")
            if pre_stable:
                issues.append(f"pre-stable version ({version})")

            meta = "  *  ".join(filter(None, [
                f"v{version}",
                age_str,
                dl_str,
                "DEPRECATED" if stats["deprecated"] else None,
            ]))

            if issues:
                marker = "!" if is_warn_only else FAIL
                print(f"  {marker} {pkg}  --  {meta}")
                entry = {"pkg": pkg, "reasons": issues, "meta": meta}
                (warnings if is_warn_only else errors).append(entry)
            else:
                print(f"  v {pkg}  --  {meta}")
        except Exception as e:
            msg = str(e).splitlines()[0]
            print(f"  ? {pkg}  --  could not fetch info ({msg})")

    print()
    return errors, warnings


def main() -> None:
    root = Path.cwd()
    ignore_map = load_ignore_map(root)
    runtime_deps, dev_deps = load_deps(root)

    pkg_filter = sys.argv[1] if len(sys.argv) > 1 else None
    all_deps = runtime_deps + dev_deps

    if pkg_filter:
        pkg_filter = _parse_dep_name(pkg_filter)
        if pkg_filter not in all_deps:
            print(f'Package "{pkg_filter}" not found in dependencies.', file=sys.stderr)
            sys.exit(1)
        runtime_deps = [pkg_filter] if pkg_filter in runtime_deps else []
        dev_deps = [pkg_filter] if pkg_filter in dev_deps else []

    print()
    runtime_errors, runtime_warnings = check_group(runtime_deps, "Runtime dependencies", ignore_map)
    dev_errors, dev_warnings = check_group(dev_deps, "Dev dependencies", ignore_map)

    all_warnings = runtime_warnings + dev_warnings
    all_errors = runtime_errors + dev_errors

    if all_warnings:
        print(f"{len(all_warnings)} package(s) with warnings (ignored for exit code):\n")
        for entry in all_warnings:
            print(f"  ! {entry['pkg']}  --  {entry['meta']}")
            print(f"      issues: {', '.join(entry['reasons'])}")
        print()

    if not all_errors:
        print("All green.\n")
    else:
        print(f"{len(all_errors)} package(s) failed checks:\n")
        for entry in all_errors:
            print(f"  {FAIL} {entry['pkg']}  --  {entry['meta']}", file=sys.stderr)
            print(f"      issues: {', '.join(entry['reasons'])}", file=sys.stderr)
        print(f"\n{PROMPT}")
        print()
        sys.exit(1)


if __name__ == "__main__":
    main()
