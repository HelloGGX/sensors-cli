#!/usr/bin/env python3
"""Run maintainability checks for test-to-implementation alignment.

This script is designed as an entry point for multiple custom checks.
It performs shared discovery and analysis once, then executes selected checks.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModuleInfo:
    path: Path
    is_package: bool


@dataclass(frozen=True)
class TestFileAnalysis:
    path: Path
    expected_name: str
    imported_modules: set[str]


@dataclass(frozen=True)
class Violation:
    check_id: str
    path: Path
    message: str


@dataclass(frozen=True)
class CheckDefinition:
    check_id: str
    guidance: str
    run: CheckFunction
    ignored_file_patterns: list[str]


@dataclass(frozen=True)
class CheckContext:
    repo_root: Path
    tests_dir: Path
    package_root: Path
    package_name: str
    ignored_test_stems: set[str]
    modules: dict[str, ModuleInfo]
    reexport_cache: dict[str, dict[str, str]]


CheckFunction = Callable[[TestFileAnalysis, CheckContext], list[Violation]]

CHECK_TEST_MODULE_NAMING = "test-module-naming"
PRECHECK = "preflight"


def _resolve_relative_module(
    *, current_module: str, is_package: bool, level: int, module: str | None
) -> str:
    """Resolve a possibly-relative import to an absolute module path."""
    if level == 0:
        return module or ""

    parts = current_module.split(".")
    if not is_package:
        parts = parts[:-1]

    up = max(level - 1, 0)
    if up:
        parts = parts[:-up]

    base = ".".join(parts)
    if module:
        return f"{base}.{module}" if base else module
    return base


def _validate_inputs(repo_root: Path, tests_dir: Path, package_root: Path) -> int:
    if not tests_dir.is_dir():
        print(f"Tests directory not found: {tests_dir}", file=sys.stderr)
        return 2
    if not package_root.is_dir():
        print(f"Package directory not found: {package_root}", file=sys.stderr)
        return 2
    if not repo_root.is_dir():
        print(f"Repository root not found: {repo_root}", file=sys.stderr)
        return 2
    return 0


def _build_module_index(package_root: Path, package_name: str) -> dict[str, ModuleInfo]:
    modules: dict[str, ModuleInfo] = {}
    for py_file in package_root.rglob("*.py"):
        rel = py_file.relative_to(package_root)
        if py_file.name == "__init__.py":
            if rel.parent == Path("."):
                module_name = package_name
            else:
                module_name = f"{package_name}.{".".join(rel.parent.parts)}"
            is_package = True
        else:
            stem = py_file.with_suffix("").relative_to(package_root)
            module_name = f"{package_name}.{".".join(stem.parts)}"
            is_package = False

        modules[module_name] = ModuleInfo(path=py_file, is_package=is_package)

    return modules


def _parse_reexports(module_name: str, info: ModuleInfo) -> dict[str, str]:
    """Parse simple ``from ... import ...`` re-exports from a package __init__.py."""
    if not info.is_package:
        return {}

    try:
        tree = ast.parse(info.path.read_text(encoding="utf-8"), filename=str(info.path))
    except (OSError, SyntaxError):
        return {}

    reexports: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue

        from_module = _resolve_relative_module(
            current_module=module_name,
            is_package=True,
            level=node.level,
            module=node.module,
        )

        if not from_module.startswith("sensors"):
            continue

        for alias in node.names:
            if alias.name == "*":
                continue

            exported_name = alias.asname or alias.name
            if node.module is None:
                reexports[exported_name] = f"{from_module}.{alias.name}"
            else:
                reexports[exported_name] = from_module

    return reexports


def _is_package_or_submodule(module_name: str, package_name: str) -> bool:
    return module_name == package_name or module_name.startswith(f"{package_name}.")


def _resolve_import_from_alias(
    *,
    module_name: str,
    alias: ast.alias,
    module_info: ModuleInfo | None,
    modules: dict[str, ModuleInfo],
    reexport_cache: dict[str, dict[str, str]],
) -> str:
    if alias.name == "*":
        return module_name

    if not module_info or not module_info.is_package:
        return module_name

    package_reexports = reexport_cache.setdefault(
        module_name,
        _parse_reexports(module_name, module_info),
    )
    if alias.name in package_reexports:
        return package_reexports[alias.name]

    submodule_candidate = f"{module_name}.{alias.name}"
    if submodule_candidate in modules:
        return submodule_candidate

    return module_name


def _imports_from_node(
    *,
    node: ast.AST,
    package_name: str,
    modules: dict[str, ModuleInfo],
    reexport_cache: dict[str, dict[str, str]],
) -> set[str]:
    imported: set[str] = set()

    if isinstance(node, ast.Import):
        for alias in node.names:
            if _is_package_or_submodule(alias.name, package_name):
                imported.add(alias.name)
        return imported

    if not isinstance(node, ast.ImportFrom):
        return imported
    if node.module is None:
        return imported
    if not _is_package_or_submodule(node.module, package_name):
        return imported

    module_name = node.module
    module_info = modules.get(module_name)
    for alias in node.names:
        imported.add(
            _resolve_import_from_alias(
                module_name=module_name,
                alias=alias,
                module_info=module_info,
                modules=modules,
                reexport_cache=reexport_cache,
            )
        )

    return imported


def _resolve_imported_modules(
    tree: ast.AST,
    *,
    package_name: str,
    modules: dict[str, ModuleInfo],
    reexport_cache: dict[str, dict[str, str]],
) -> set[str]:
    imported: set[str] = set()

    for node in ast.walk(tree):
        imported.update(
            _imports_from_node(
                node=node,
                package_name=package_name,
                modules=modules,
                reexport_cache=reexport_cache,
            )
        )

    return imported


def _basename(module_name: str) -> str:
    return module_name.split(".")[-1]


def _subject_prefixes(expected_name: str) -> set[str]:
    """Return progressive subject prefixes from the test file stem.

    Example: "cli_status" -> {"cli", "cli_status"}
    """
    parts = [part for part in expected_name.split("_") if part]
    prefixes: set[str] = set()
    for idx in range(1, len(parts) + 1):
        prefixes.add("_".join(parts[:idx]))
    return prefixes


def _analyze_test_file(
    test_file: Path,
    *,
    package_name: str,
    modules: dict[str, ModuleInfo],
    reexport_cache: dict[str, dict[str, str]],
) -> tuple[TestFileAnalysis | None, list[Violation]]:
    violations: list[Violation] = []
    expected = test_file.stem.removeprefix("test_")
    if not expected:
        return None, violations

    try:
        source = test_file.read_text(encoding="utf-8")
    except OSError as exc:
        violations.append(
            Violation(
                check_id=PRECHECK,
                path=test_file,
                message=f"failed to read file ({exc})",
            )
        )
        return None, violations

    try:
        tree = ast.parse(source, filename=str(test_file))
    except SyntaxError as exc:
        violations.append(
            Violation(
                check_id=PRECHECK,
                path=test_file,
                message=f"syntax error while parsing test file ({exc})",
            )
        )
        return None, violations

    imported_modules = _resolve_imported_modules(
        tree,
        package_name=package_name,
        modules=modules,
        reexport_cache=reexport_cache,
    )

    return (
        TestFileAnalysis(
            path=test_file,
            expected_name=expected,
            imported_modules=imported_modules,
        ),
        violations,
    )


def _collect_test_analyses(context: CheckContext) -> tuple[list[TestFileAnalysis], list[Violation]]:
    analyses: list[TestFileAnalysis] = []
    violations: list[Violation] = []

    for test_file in sorted(context.tests_dir.glob("test_*.py")):
        if test_file.stem in context.ignored_test_stems:
            continue
        analysis, preflight_violations = _analyze_test_file(
            test_file,
            package_name=context.package_name,
            modules=context.modules,
            reexport_cache=context.reexport_cache,
        )
        violations.extend(preflight_violations)
        if analysis is not None:
            analyses.append(analysis)

    return analyses, violations


def _check_test_module_naming(
    analysis: TestFileAnalysis,
    context: CheckContext,
) -> list[Violation]:
    imported_basenames = {_basename(name) for name in analysis.imported_modules}
    expected_candidates = _subject_prefixes(analysis.expected_name)

    if expected_candidates & imported_basenames:
        return []

    if not analysis.imported_modules:
        return []

    candidates = ", ".join(sorted(imported_basenames))
    expected = ", ".join(sorted(expected_candidates))
    return [
        Violation(
            check_id=CHECK_TEST_MODULE_NAMING,
            path=analysis.path,
            message=(
                f"expected one of [{expected}] in imports [{candidates}]."
            ),
        )
    ]


def _check_registry() -> dict[str, CheckDefinition]:
    return {
        CHECK_TEST_MODULE_NAMING: CheckDefinition(
            check_id=CHECK_TEST_MODULE_NAMING,
            guidance=(
                "Naming convention: A test should be named after its main "
                "subject test_<subject>.py. Suffixes are allowed in exceptional cases, such as "
                "test_<subject>_<test-area>.py. But if a test suite has to be broken up into many pieces, "
                "it might be a smell that the subject has too many responsibilities, consider that as well."
            ),
            run=_check_test_module_naming,
            ignored_file_patterns=[r".*e2e.*"],
        ),
    }


def _is_ignored_for_check(path: Path, repo_root: Path, ignore_patterns: list[str]) -> bool:
    if not ignore_patterns:
        return False

    try:
        rel_path = path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        rel_path = path.as_posix()

    return any(re.search(pattern, rel_path) for pattern in ignore_patterns)


def _run_checks(
    *,
    analyses: list[TestFileAnalysis],
    context: CheckContext,
    selected_checks: list[str],
) -> list[Violation]:
    registry = _check_registry()
    violations: list[Violation] = []

    for check_id in selected_checks:
        check_def = registry[check_id]
        check_fn = check_def.run
        for analysis in analyses:
            if _is_ignored_for_check(
                analysis.path,
                context.repo_root,
                check_def.ignored_file_patterns,
            ):
                continue
            violations.extend(check_fn(analysis, context))

    return violations


def _print_report(
    violations: list[Violation],
    selected_checks: list[str],
    registry: dict[str, CheckDefinition],
    repo_root: Path,
) -> int:
    if violations:
        print("Maintainability check failures:")
        printed_guidance_for: set[str] = set()

        for check_id in selected_checks:
            has_violation = any(v.check_id == check_id for v in violations)
            if not has_violation:
                continue

            check_def = registry.get(check_id)
            if check_def is None or check_id in printed_guidance_for:
                continue

            print(f"  Guidance [{check_id}]: {check_def.guidance}")
            printed_guidance_for.add(check_id)

        for violation in violations:
            try:
                display_path = violation.path.resolve().relative_to(repo_root)
            except ValueError:
                display_path = violation.path
            print(f"  - [{violation.check_id}] {display_path}: {violation.message}")
        return 1

    checks_display = ", ".join(selected_checks)
    print(f"All selected checks passed: {checks_display}")
    return 0


def _guidance_blocks(
    violations: list[Violation],
    selected_checks: list[str],
    registry: dict[str, CheckDefinition],
) -> list[dict[str, str]]:
    guidance: list[dict[str, str]] = []
    for check_id in selected_checks:
        has_violation = any(v.check_id == check_id for v in violations)
        if not has_violation:
            continue
        check_def = registry.get(check_id)
        if check_def is None:
            continue
        guidance.append({"rule": check_id, "body": check_def.guidance})
    return guidance


def _json_finding(violation: Violation, repo_root: Path) -> dict[str, str]:
    try:
        rel_path = violation.path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        rel_path = violation.path.as_posix()

    return {
        "message": violation.message,
        "severity": "error",
        "file": rel_path,
        "rule": violation.check_id,
    }


def _print_json_report(
    violations: list[Violation],
    selected_checks: list[str],
    registry: dict[str, CheckDefinition],
    repo_root: Path,
) -> int:
    findings = [_json_finding(v, repo_root) for v in violations]
    guidance = _guidance_blocks(violations, selected_checks, registry)
    success = len(violations) == 0

    payload = {
        "findings": findings,
        "guidance": guidance,
        "metrics": [
            {
                "key": "violationCount",
                "label": "Violations",
                "value": len(violations),
                "direction": "less",
            }
        ],
        "score": {
            "value": len(violations),
            "direction": "less",
            "description": "Maintainability check violations",
        },
        "success": success,
        "summary": (
            "No issues" if success else f"{len(violations)} issue{'s' if len(violations) != 1 else ''}"
        ),
        "extra": {
            "selectedChecks": selected_checks,
        },
    }
    print(json.dumps(payload))
    return 0 if success else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--tests-dir", default="tests")
    parser.add_argument("--package-dir", default="sensors")
    parser.add_argument("--package-name", default="sensors")
    parser.add_argument(
        "--output-format",
        choices=["text", "json"],
        default="text",
        help="Output format. Use json for sensors default parser compatibility.",
    )
    parser.add_argument(
        "--check",
        dest="checks",
        action="append",
        default=[],
        help="Check ID to run. Repeat to run multiple checks. Default: run all checks.",
    )
    parser.add_argument(
        "--list-checks",
        action="store_true",
        help="List available check IDs and exit.",
    )
    parser.add_argument(
        "--ignore",
        nargs="*",
        default=[],
        help="Test file stems to ignore, e.g. test_e2e_cli test_integration",
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    tests_dir = repo_root / args.tests_dir
    package_root = repo_root / args.package_dir

    registry = _check_registry()
    if args.list_checks:
        print("Available checks:")
        for check_id in sorted(registry):
            print(f"  - {check_id}: {registry[check_id].guidance}")
        return 0

    selected_checks = args.checks or sorted(registry)
    unknown_checks = [check_id for check_id in selected_checks if check_id not in registry]
    if unknown_checks:
        print(f"Unknown check IDs: {', '.join(unknown_checks)}", file=sys.stderr)
        print("Use --list-checks to see valid values.", file=sys.stderr)
        return 2

    input_error = _validate_inputs(repo_root, tests_dir, package_root)
    if input_error != 0:
        return input_error

    ignored = set(args.ignore)
    modules = _build_module_index(package_root, args.package_name)
    reexport_cache: dict[str, dict[str, str]] = {}

    context = CheckContext(
        repo_root=repo_root,
        tests_dir=tests_dir,
        package_root=package_root,
        package_name=args.package_name,
        ignored_test_stems=ignored,
        modules=modules,
        reexport_cache=reexport_cache,
    )

    analyses, preflight_violations = _collect_test_analyses(context)
    check_violations = _run_checks(
        analyses=analyses,
        context=context,
        selected_checks=selected_checks,
    )

    all_violations = preflight_violations + check_violations
    if args.output_format == "json":
        return _print_json_report(
            all_violations,
            selected_checks,
            registry,
            repo_root,
        )

    return _print_report(all_violations, selected_checks, registry, repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
