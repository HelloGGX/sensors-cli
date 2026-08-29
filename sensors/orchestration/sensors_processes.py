"""Discover running sensors OS processes (``sensors start --worker`` / ``--show``) via /proc or ps."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from sensors.os_compat import NO_WINDOW


@dataclass(frozen=True)
class SensorsProcessInfo:
    """A process that looks like ``sensors … start --worker`` or ``… --show``."""

    pid: int
    mode: str  # "worker" | "show"
    folder: str | None
    """Project root (directory that contains ``.sensors/``): from ``control.json`` + ``socketPath`` when found, else cwd/argv."""


def project_root_from_lsof_sensors_socket(pid: int) -> Path | None:
    """Resolve project root from an open ``…/.sensors/<stem>.sock`` (listener) for this PID.

    Uses the same path the kernel associates with the Unix socket — more reliable than
    scanning ``cwd`` when ``ps`` truncates argv or paths differ by symlink prefix.
    """
    try:
        out = subprocess.check_output(  # noqa: S603 -- pid is always int, no injection risk
            ["lsof", "-a", "-p", str(pid), "-Fn"],  # noqa: S607 -- lsof location varies across systems so we can't be more specific
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        if not line.startswith("n"):
            continue
        path = line[1:]
        if "/.sensors/" not in path or not path.endswith(".sock"):
            continue
        try:
            sock = Path(path).resolve()
        except OSError:
            continue
        return sock.parent.parent
    return None


def _project_root_from_control_data(data: dict) -> Path | None:
    """Derive project root from control JSON: ``workingDir`` or ``socketPath`` parent chain."""
    wd = data.get("workingDir")
    if wd:
        try:
            return Path(wd).resolve()
        except OSError:
            return Path(wd)
    sock_s = data.get("socketPath")
    if not sock_s:
        return None
    sock = Path(sock_s)
    try:
        sock_r = sock.resolve()
    except OSError:
        sock_r = sock
    # .../proj/.sensors/stem.sock
    return sock_r.parent.parent


def project_root_from_control_files(pid: int, search_bases: list[Path]) -> Path | None:
    """Find ``.sensors/*.control.json`` listing this ``pid``; return project root (parent of ``.sensors/``).

    Uses the same metadata as ``sensors stop`` (``socketPath`` -> ``.../stem.sock`` under ``.sensors/``).
    Prefers the explicit ``workingDir`` field when present, falls back to socketPath derivation.
    """
    want = int(pid)
    for base in search_bases:
        try:
            base_r = base.resolve()
        except OSError:
            base_r = base
        sensors = base_r / ".sensors"
        if not sensors.is_dir():
            continue
        for ctl in sorted(sensors.glob("*.control.json")):
            try:
                data = json.loads(ctl.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            cpid = data.get("pid")
            if cpid is None or int(cpid) != want:
                continue
            root = _project_root_from_control_data(data)
            if root is not None:
                return root
    return None


def _search_bases_for_pid(folder: str | None, argv_path: str | None) -> list[Path]:
    """Distinct resolved paths to look for ``.sensors/*.control.json`` (cwd and argv)."""
    seen: set[str] = set()
    out: list[Path] = []
    for s in (folder, argv_path):
        if not s:
            continue
        try:
            p = Path(s).expanduser().resolve()
        except OSError:
            p = Path(s).expanduser()
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def read_process_cwd(pid: int) -> str | None:
    """Return the process working directory, or None if unavailable (permissions, OS)."""
    if sys.platform.startswith("linux"):
        try:
            return os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            return None
    return _read_process_cwd_lsof(pid)


def _read_process_cwd_lsof(pid: int) -> str | None:
    """macOS and other Unix: parse ``lsof -Fn`` cwd entry."""
    try:
        out = subprocess.check_output(  # noqa: S603 -- pid is always int, no injection risk
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],  # noqa: S607 -- lsof location varies across systems so we can't be more specific
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    last_n: str | None = None
    for line in out.splitlines():
        if line.startswith("n"):
            last_n = line[1:]
    return last_n


def _folder_display(pid: int, argv_path: str | None) -> str | None:
    """Prefer OS cwd (matches ``Popen(..., cwd=project)``); fall back to argv path."""
    cwd = read_process_cwd(pid)
    if cwd:
        try:
            return str(Path(cwd).resolve())
        except OSError:
            pass
    if argv_path:
        try:
            return str(Path(argv_path).expanduser().resolve(strict=False))
        except OSError:
            return argv_path
    return None


def format_folder_for_display(absolute_path: str) -> str:
    """Pretty-print a resolved path: shorten ``$HOME`` to ``~/`` for readability."""
    try:
        p = Path(absolute_path).expanduser().resolve()
    except OSError:
        return absolute_path
    home = Path.home().resolve()
    try:
        rel = p.relative_to(home)
    except ValueError:
        return str(p)
    if rel == Path("."):
        return "~"
    return f"~/{rel.as_posix()}"


def normalize_path_for_display(path_str: str) -> str:
    """macOS: show ``/var/...`` instead of ``/private/var/...`` (same path, shorter)."""
    if sys.platform != "darwin":
        return path_str
    if path_str.startswith("/private/var/"):
        return "/var/" + path_str[len("/private/var/") :]
    if path_str.startswith("/private/tmp/"):
        return "/tmp/" + path_str[len("/private/tmp/") :]  # noqa: S108 -- display-only string rewrite, not writing to /tmp
    return path_str


def compact_temp_path(absolute_path: str) -> str | None:
    """For macOS temp dirs, return a short form like ``(temp) tmpXYZ/proj``.

    Returns None if the path is not a temp dir.
    """
    if not looks_like_macos_temp_sensors_dir(absolute_path):
        return None
    # Typical: /private/var/folders/t5/.../T/tmpXYZ/proj  -- show last 2 components
    p = Path(absolute_path)
    parts = p.parts
    # Find the "T" component (macOS temp subdir marker) and show everything after it
    for i, part in enumerate(parts):
        if part == "T" and i + 1 < len(parts):
            tail = "/".join(parts[i + 1 :])
            return f"(temp) {tail}"
    # Fallback: just show last 2 components
    if len(parts) >= 2:
        return f"(temp) {'/'.join(parts[-2:])}"
    return f"(temp) {parts[-1]}" if parts else None


def display_project_path(absolute_path: str) -> str:
    """Format a project path for ``status --all``: ``~/``, then macOS ``/private`` cleanup."""
    return normalize_path_for_display(format_folder_for_display(absolute_path))


def looks_like_macos_temp_sensors_dir(path: str) -> bool:
    """Heuristic: disposable temp dirs (often pytest e2e) under macOS /var/folders."""
    p = path
    return "/var/folders/" in p or p.startswith("/private/var/folders/")


def _find_sensors_start_token_index(tokens: list[str]) -> int:
    """Return index of the ``start`` subcommand, or -1."""
    for i, t in enumerate(tokens):
        if t != "start":
            continue
        if i >= 2 and tokens[i - 2] == "-m" and tokens[i - 1] == "sensors.cli":
            return i
        if i >= 1:
            prev = tokens[i - 1]
            # The PyInstaller build is ``sensors`` on POSIX but ``sensors.exe`` on
            # Windows; split on both separators so matching is host-independent.
            base = prev.replace("\\", "/").rsplit("/", 1)[-1].lower()
            if base in ("sensors", "sensors.exe"):
                return i
    return -1


def _consume_start_args(rest: list[str]) -> tuple[str | None, str | None]:
    """Parse argv after ``start``: return (mode, argv project path)."""
    mode: str | None = None
    for t in rest:
        if t == "--worker":
            mode = "worker"
            break
        if t == "--show":
            mode = "show"
            break
    if mode is None:
        return None, None
    i = 0
    filtered: list[str] = []
    while i < len(rest):
        t = rest[i]
        if t in ("--worker", "--show"):
            i += 1
            continue
        if t in ("--config", "-c") and i + 1 < len(rest):
            i += 2
            continue
        filtered.append(t)
        i += 1
    pos = [x for x in filtered if not x.startswith("-")]
    path = pos[-1] if pos else None
    return mode, path


def try_parse_sensors_start(tokens: list[str]) -> tuple[str, str | None] | None:
    """If ``tokens`` is a sensors ``start`` invocation, return ``(mode, argv path)``."""
    idx = _find_sensors_start_token_index(tokens)
    if idx < 0:
        return None
    rest = tokens[idx + 1 :]
    mode, path = _consume_start_args(rest)
    if mode is None:
        return None
    return mode, path


def _parse_ps_line(line: str) -> tuple[int, str] | None:
    m = re.match(r"^\s*(\d+)\s+(.*)$", line.rstrip())
    if not m:
        return None
    return int(m.group(1)), m.group(2)


def _iter_proc_cmdline_linux() -> list[tuple[int, list[str]]]:
    proc = Path("/proc")
    if not (sys.platform.startswith("linux") and proc.is_dir()):
        return []
    rows: list[tuple[int, list[str]]] = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        cmdline = entry / "cmdline"
        try:
            raw = cmdline.read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        parts = raw.split(b"\x00")
        tokens = [p.decode("utf-8", errors="replace") for p in parts if p]
        if not tokens:
            continue
        rows.append((int(entry.name), tokens))
    return rows


def _iter_ps_fallback() -> list[tuple[int, list[str]]]:
    import shutil

    ps_bin = shutil.which("ps")
    if not ps_bin:
        return []
    for argv in (
        [ps_bin, "axww", "-o", "pid=", "-o", "args="],
        [ps_bin, "-axww", "-o", "pid=", "-o", "args="],
        [ps_bin, "-eo", "pid=", "-o", "args="],
    ):
        try:
            out = subprocess.check_output(argv, text=True, timeout=120, stderr=subprocess.DEVNULL)  # noqa: S603 -- argv built from shutil.which("ps") + literal flags
        except (OSError, subprocess.CalledProcessError):
            continue
        rows: list[tuple[int, list[str]]] = []
        for line in out.splitlines():
            parsed = _parse_ps_line(line)
            if not parsed:
                continue
            pid, cmd = parsed
            try:
                tokens = shlex.split(cmd)
            except ValueError:
                continue
            if not tokens:
                continue
            rows.append((pid, tokens))
        if rows:
            return rows
    return []


def _iter_windows_processes() -> list[tuple[int, int, list[str]]]:
    """Enumerate (pid, ppid, argv tokens) on Windows via PowerShell CIM (no /proc, no ps)."""
    import shutil

    ps_bin = shutil.which("powershell")
    if not ps_bin:
        return []
    # [Console]::OutputEncoding: without a console (CREATE_NO_WINDOW) PowerShell
    # falls back to the legacy ANSI codepage (GBK etc.), which the UTF-8 decode
    # below cannot read when any process has non-ASCII argv.
    command = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId, ParentProcessId, CommandLine | "
        "ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.check_output(  # noqa: S603 -- argv is shutil.which("powershell") + literal flags
            [ps_bin, "-NoProfile", "-NonInteractive", "-Command", command],
            text=True,
            timeout=30,
            stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    return _windows_rows_from_json(out)


def _windows_cmdline_tokens(cmdline: str) -> list[str]:
    """Split a Windows command line into argv tokens.

    POSIX shlex would eat backslashes in paths like ``C:\\Users\\...``, so split
    with quoting disabled and strip the surrounding double quotes it keeps.
    """
    try:
        tokens = shlex.split(cmdline, posix=False)
    except ValueError:
        return []
    return [t[1:-1] if len(t) >= 2 and t.startswith('"') and t.endswith('"') else t for t in tokens]


def _windows_rows_from_json(out: str) -> list[tuple[int, int, list[str]]]:
    """Parse ConvertTo-Json output of ProcessId/ParentProcessId/CommandLine into rows."""
    stripped = out.strip()
    if not stripped:
        return []
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return []
    entries = payload if isinstance(payload, list) else [payload]
    rows: list[tuple[int, int, list[str]]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        pid = entry.get("ProcessId")
        cmdline = entry.get("CommandLine")
        ppid = entry.get("ParentProcessId")
        if not isinstance(pid, int) or not isinstance(cmdline, str) or not cmdline:
            continue
        tokens = _windows_cmdline_tokens(cmdline)
        if tokens:
            rows.append((pid, ppid if isinstance(ppid, int) else -1, tokens))
    return rows


def _windows_sensors_rows() -> list[tuple[int, list[str]]]:
    """Windows: (pid, tokens) of sensors-start processes, uv trampolines collapsed.

    uv-managed venvs on Windows launch python through a trampoline process that
    re-execs the base interpreter with the same argv, so one worker shows up as
    two processes (trampoline + real worker). The real worker is the child; it
    owns the PID recorded in control.json, so the parent row is dropped.
    """
    rows = _iter_windows_processes()
    matched_pids = {pid for pid, _ppid, tokens in rows if try_parse_sensors_start(tokens) is not None}
    parents_of_matched = {ppid for pid, ppid, tokens in rows if pid in matched_pids and ppid in matched_pids}
    return [
        (pid, tokens)
        for pid, ppid, tokens in rows
        if pid in matched_pids and pid not in parents_of_matched
    ]


def list_sensors_start_pids() -> set[int]:
    """Fast: return PIDs of processes whose argv matches ``sensors start`` (no lsof, no disk I/O)."""
    if sys.platform == "win32":
        return {pid for pid, _tokens in _windows_sensors_rows()}
    raw_rows = _iter_proc_cmdline_linux() or _iter_ps_fallback()
    return {pid for pid, tokens in raw_rows if try_parse_sensors_start(tokens) is not None}


def list_sensors_start_processes() -> list[SensorsProcessInfo]:
    """Return all processes that look like an active sensors ``start`` (worker or show)."""
    if sys.platform == "win32":
        raw_rows = _windows_sensors_rows()
    else:
        raw_rows = _iter_proc_cmdline_linux()
        if not raw_rows:
            raw_rows = _iter_ps_fallback()

    result: list[SensorsProcessInfo] = []
    for pid, tokens in raw_rows:
        parsed = try_parse_sensors_start(tokens)
        if parsed is None:
            continue
        mode, argv_path = parsed
        folder = _folder_display(pid, argv_path)
        bases = _search_bases_for_pid(folder, argv_path)
        sock_root = project_root_from_lsof_sensors_socket(pid)
        ctrl_root = project_root_from_control_files(pid, bases)
        root = sock_root or ctrl_root
        resolved = str(root) if root is not None else folder
        result.append(SensorsProcessInfo(pid=pid, mode=mode, folder=resolved))
    result.sort(key=lambda r: (r.folder or "", r.pid))
    return result
