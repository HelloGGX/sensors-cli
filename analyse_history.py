#!/usr/bin/env python3
"""Analyse a history.jsonl file produced by the sensors 'check' command.

Usage:
    python analyse_history.py <path-to-history.jsonl> [output.html]

Produces a self-contained HTML file: check status (green/red) first, then per-runner
tables (one row per check) with score-state bars. Open the output file in any browser.

If no output path is given, writes to history_report.html next to the input.
"""

from __future__ import annotations

import html
import json
import sys
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

@dataclass
class CheckRecord:
    timestamp: datetime
    runner_filter: str | None
    runners: dict[str, dict]
    snapshot_id: str | None = None


def load_history(path: Path) -> list[CheckRecord]:
    records: list[CheckRecord] = []
    with path.open() as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"Warning: skipping line {lineno} - {exc}", file=sys.stderr)
                continue
            from sensors.time_util import parse_timestamp

            ts = parse_timestamp(raw["timestamp"])
            records.append(CheckRecord(
                timestamp=ts,
                runner_filter=raw.get("runner_filter"),
                runners=raw.get("runners", {}),
                snapshot_id=raw.get("snapshot_id"),
            ))
    return records


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

@dataclass
class RunnerTimeline:
    name: str
    # (timestamp, status, score_value_or_None)
    checks: list[tuple[datetime, str, int | None]] = field(default_factory=list)
    _direction: str | None = field(default=None, repr=False)

    @property
    def score_series(self) -> list[int]:
        return [s for _, _, s in self.checks if s is not None]

    @property
    def failure_count(self) -> int:
        return sum(1 for _, st, _ in self.checks if st == "failure")

    @property
    def recovery_count(self) -> int:
        """Status transitions from failure to success."""
        count = 0
        prev = None
        for _, st, _ in self.checks:
            if prev == "failure" and st == "success":
                count += 1
            prev = st
        return count

    def score_improved(self, a: int, b: int) -> bool:
        """True if score moved in the improving direction from a to b."""
        if self._direction == "less":
            return b < a
        if self._direction == "more":
            return b > a
        return False


def build_timelines(records: list[CheckRecord]) -> dict[str, RunnerTimeline]:
    timelines: dict[str, RunnerTimeline] = {}
    for rec in records:
        for name, info in rec.runners.items():
            if name not in timelines:
                timelines[name] = RunnerTimeline(name=name)
            score_val: int | None = None
            direction: str | None = None
            if info.get("score"):
                score_val = info["score"].get("value")
                direction = info["score"].get("direction")
            timelines[name].checks.append((rec.timestamp, info["status"], score_val))
            if direction and timelines[name]._direction is None:
                timelines[name]._direction = direction
    return timelines


@dataclass
class ScoreEvent:
    """A notable score change between two consecutive checks."""
    runner: str
    check_index: int          # index within this runner's checks (0-based)
    timestamp: datetime
    prev_timestamp: datetime  # timestamp of the check that showed the previous state
    prev_score: int
    new_score: int
    prev_status: str
    new_status: str
    kind: str                 # "recovery", "improvement", "regression", "worsening"

    @property
    def delta(self) -> int:
        return self.new_score - self.prev_score

    @property
    def delta_str(self) -> str:
        return f"{'+' if self.delta > 0 else ''}{self.delta}"

    @property
    def elapsed(self) -> str:
        """Time between the previous check and this one."""
        secs = int((self.timestamp - self.prev_timestamp).total_seconds())
        if secs < 0:
            return "-"
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}h {m}m {s}s"
        if m:
            return f"{m}m {s}s"
        return f"{s}s"


def score_events(tl: RunnerTimeline) -> list[ScoreEvent]:
    """All checks where either status or score changed from the previous check."""
    events: list[ScoreEvent] = []
    checks = tl.checks

    for i in range(1, len(checks)):
        prev_ts, prev_st, prev_sv = checks[i - 1]
        cur_ts, cur_st, cur_sv = checks[i]

        status_changed = cur_st != prev_st
        score_changed = (prev_sv is not None and cur_sv is not None and cur_sv != prev_sv)

        if not status_changed and not score_changed:
            continue

        if prev_st == "failure" and cur_st == "success":
            kind = "recovery"
        elif prev_st == "success" and cur_st == "failure":
            kind = "regression"
        elif score_changed and prev_sv is not None and cur_sv is not None:
            kind = "improvement" if tl.score_improved(prev_sv, cur_sv) else "worsening"
        else:
            continue

        events.append(ScoreEvent(
            runner=tl.name,
            check_index=i,
            timestamp=cur_ts,
            prev_timestamp=prev_ts,
            prev_score=prev_sv if prev_sv is not None else -1,
            new_score=cur_sv if cur_sv is not None else -1,
            prev_status=prev_st,
            new_status=cur_st,
            kind=kind,
        ))

    return events


def transition_kind(tl: RunnerTimeline, i: int) -> str:
    """Classify row i: first check, steady, or the same kinds as score_events (plus update)."""
    if i == 0:
        return "initial"
    if i < 0 or i >= len(tl.checks):
        return "steady"
    prev_ts, prev_st, prev_sv = tl.checks[i - 1]
    cur_ts, cur_st, cur_sv = tl.checks[i]
    status_changed = cur_st != prev_st
    score_changed = prev_sv is not None and cur_sv is not None and cur_sv != prev_sv
    if not status_changed and not score_changed:
        return "steady"
    if prev_st == "failure" and cur_st == "success":
        return "recovery"
    if prev_st == "success" and cur_st == "failure":
        return "regression"
    if score_changed and prev_sv is not None and cur_sv is not None:
        return "improvement" if tl.score_improved(prev_sv, cur_sv) else "worsening"
    return "update"


def _format_elapsed_between(prev_ts: datetime | None, cur_ts: datetime) -> str:
    if prev_ts is None:
        return "—"
    secs = int((cur_ts - prev_ts).total_seconds())
    if secs < 0:
        return "-"
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _format_score_column(i: int, prev_sv: int | None, cur_sv: int | None) -> str:
    if cur_sv is None:
        return "—"
    if i == 0:
        return str(cur_sv)
    if prev_sv is not None and cur_sv != prev_sv:
        d = cur_sv - prev_sv
        ds = f"{'+' if d > 0 else ''}{d}"
        return f"{prev_sv} &rarr; {cur_sv} ({ds})"
    return str(cur_sv)


def _format_status_column(i: int, prev_st: str | None, cur_st: str) -> str:
    if i == 0 or prev_st is None or prev_st == cur_st:
        return html.escape(cur_st)
    return f"{html.escape(prev_st)} &rarr; {html.escape(cur_st)}"


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

_KIND_BADGE = {
    "recovery":    '<span class="badge badge-ok">recovery</span>',
    "improvement": '<span class="badge badge-improve">improvement</span>',
    "regression":  '<span class="badge badge-fail">regression</span>',
    "worsening":   '<span class="badge badge-warn">worsening</span>',
    "initial":     '<span class="badge badge-initial">initial</span>',
    "steady":      '<span class="badge badge-steady">steady</span>',
    "update":      '<span class="badge badge-update">update</span>',
}

# State bar fill is score / max(scale_ceiling, this). Avoids a single "1" dominating the bar.
STATE_BAR_SCALE_MIN = 20


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name)


def _score_state_bar_cell(score: int | None, runner_status: str, scale_max: int) -> str:
    """Bar fill = score / scale_max. Bar color = check status (green/red only). Score 0: colored number, no fill."""
    if score is None:
        return '<td class="score-state-cell"><span class="score-state-empty">—</span></td>'
    ok = runner_status == "success"
    fill_cls = "score-state-fill-good" if ok else "score-state-fill-bad"
    scale = scale_max
    pct = min(100.0, (score / scale) * 100.0)
    title = html.escape(f"score={score} (fill vs scale max {scale}); status={runner_status}")
    score_txt = html.escape(str(score))
    if score == 0:
        num_cls = "score-state-num-ok" if ok else "score-state-num-fail"
        return f'''<td class="score-state-cell" title="{title}">
      <div class="score-state-track score-state-track-zero-only">
        <span class="score-state-text score-state-text-nobar {num_cls}">{score_txt}</span>
      </div>
    </td>'''
    return f'''<td class="score-state-cell" title="{title}">
      <div class="score-state-track">
        <div class="score-state-fill {fill_cls}" style="width: {pct:.1f}%"></div>
        <span class="score-state-text">{score_txt}</span>
      </div>
    </td>'''


def build_html(records: list[CheckRecord], timelines: dict[str, RunnerTimeline], source: str) -> str:
    total_checks = len(records)
    runner_names = sorted(timelines.keys())

    # --- summary stats ---
    span_str = "n/a"
    if records:
        delta = records[-1].timestamp - records[0].timestamp
        h, rem = divmod(int(delta.total_seconds()), 3600)
        m, s = divmod(rem, 60)
        span_str = (f"{h}h {m}m {s}s" if h else f"{m}m {s}s") if delta.total_seconds() > 0 else "< 1s"

    filtered_count = sum(1 for r in records if r.runner_filter is not None)
    total_failures = sum(tl.failure_count for tl in timelines.values())
    total_recoveries = sum(tl.recovery_count for tl in timelines.values())

    # --- status heatmap: one row per runner, one column per check index ---
    # x = global check index (position among ALL checks, not per-runner)
    # This means all runners share the same x axis so you can see when
    # multiple runners failed at the same time.
    from sensors.time_util import format_local_datetime

    all_checks_ts = [format_local_datetime(r.timestamp) for r in records]
    # Map each (runner, timestamp) -> global index
    ts_to_global: dict[str, int] = {format_local_datetime(r.timestamp): idx
                                    for idx, r in enumerate(records)}

    status_datasets = []
    for name in runner_names:
        tl = timelines[name]
        bg_colours = []
        xs = []
        for ts, st, _ in tl.checks:
            key = format_local_datetime(ts)
            gidx = ts_to_global.get(key, -1)
            if gidx < 0:
                continue
            xs.append(gidx)
            bg_colours.append("#59a14f" if st == "success" else "#e15759")
        status_datasets.append({
            "label": name,
            "data": [{"x": x, "y": 1} for x in xs],
            "backgroundColor": bg_colours,
            "borderColor": bg_colours,
            "borderWidth": 0,
            "barThickness": 12,
        })

    # --- one row per check per runner + score-state bars ---
    runner_sections: list[str] = []
    for name in runner_names:
        tl = timelines[name]
        checks = tl.checks
        score_vals = [sv for _, _, sv in checks if sv is not None]
        scale_max = max(max(score_vals) if score_vals else 0, STATE_BAR_SCALE_MIN)

        if not checks:
            rows = "<tr><td colspan='7' class='empty'>No checks for this runner in history.</td></tr>"
        else:
            row_parts = []
            for i, (ts, st, sv) in enumerate(checks):
                kind = transition_kind(tl, i)
                badge = _KIND_BADGE.get(kind, html.escape(kind))
                prev_ts = checks[i - 1][0] if i > 0 else None
                prev_st = checks[i - 1][1] if i > 0 else None
                prev_sv = checks[i - 1][2] if i > 0 else None
                score_str = _format_score_column(i, prev_sv, sv)
                status_str = _format_status_column(i, prev_st, st)
                elapsed = _format_elapsed_between(prev_ts, ts)
                bar_td = _score_state_bar_cell(sv, st, scale_max)
                row_parts.append(f"""
              <tr>
                <td class="col-check">#{i + 1}</td>
                <td>{format_local_datetime(ts)}</td>
                <td>{badge}</td>
                <td>{status_str}</td>
                <td>{score_str}</td>
                {bar_td}
                <td class="col-elapsed">{elapsed}</td>
              </tr>""")
            rows = "\n".join(row_parts)

        runner_sections.append(f"""
  <section class="runner-section" id="runner-{_slug(name)}">
    <h2 class="runner-title">{name}</h2>
    <div class="table-chart-layout">
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th class="col-check" title="Check # (index for this runner)"><span class="col-check-hdr">Check</span><span class="col-check-hdr">#</span></th>
              <th>Timestamp</th>
              <th>Event</th>
              <th>Status</th>
              <th>Score</th>
              <th class="col-score-state" title="Score at this check; bar color = success (green) or failure (red). Fill = score / max(max score in checks, {STATE_BAR_SCALE_MIN})">State</th>
              <th class="col-elapsed">Elapsed</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </div>
  </section>""")

    status_datasets_json = json.dumps(status_datasets, indent=2)
    all_checks_ts_json = json.dumps(all_checks_ts)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Sensors check history</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: system-ui, -apple-system, sans-serif;
      background: #0f1117; color: #e2e8f0; padding: 1.25rem 1.35rem;
    }}
    h1 {{ font-size: 1.45rem; font-weight: 600; color: #94d2bd; margin-bottom: 0.25rem; }}
    .meta {{ font-size: 0.88rem; color: #64748b; margin-bottom: 1.35rem; }}
    h2 {{ font-size: 1.05rem; font-weight: 600; color: #cbd5e1; margin: 1.5rem 0 0.6rem; }}
    .cards {{ display: flex; gap: 0.85rem; flex-wrap: wrap; margin-bottom: 1.35rem; }}
    .card {{
      background: #1e2330; border: 1px solid #2d3748; border-radius: 8px;
      padding: 0.75rem 1.1rem; min-width: 140px;
    }}
    .card-value {{ font-size: 2rem; font-weight: 700; }}
    .card-label {{ font-size: 0.8rem; color: #64748b; margin-top: 0.2rem; }}
    .card.red   .card-value {{ color: #e15759; }}
    .card.green .card-value {{ color: #59a14f; }}
    .card.blue  .card-value {{ color: #4e79a7; }}
    .chart-wrap {{
      background: #1e2330; border: 1px solid #2d3748; border-radius: 8px;
      padding: 1rem 1.15rem; margin-bottom: 1.15rem;
    }}
    canvas {{ max-height: 380px; }}
    table {{
      width: 100%; border-collapse: collapse; font-size: 0.9rem;
      background: #1e2330; border: 1px solid #2d3748; border-radius: 8px;
      overflow: hidden;
    }}
    thead {{ background: #161b27; }}
    th, td {{ padding: 0.45rem 0.7rem; text-align: left; border-bottom: 1px solid #2d3748; }}
    th {{
      font-weight: 600; color: #94a3b8; font-size: 0.82rem;
      text-transform: uppercase; letter-spacing: 0.05em;
    }}
    tr:last-child td {{ border-bottom: none; }}
    .badge {{
      display: inline-block; padding: 0.15rem 0.55rem;
      border-radius: 9999px; font-size: 0.8rem; font-weight: 600;
    }}
    .badge-ok      {{ background: #14532d; color: #86efac; }}
    .badge-improve {{ background: #1e3a5f; color: #93c5fd; }}
    .badge-fail    {{ background: #450a0a; color: #fca5a5; }}
    .badge-warn    {{ background: #422006; color: #fed7aa; }}
    .badge-initial {{ background: #1e293b; color: #94a3b8; }}
    .badge-steady  {{ background: #243044; color: #94a3b8; }}
    .badge-update  {{ background: #312e81; color: #c4b5fd; }}
    .note {{ font-size: 0.82rem; color: #475569; margin-top: 0.45rem; }}
    .section-intro {{ margin-bottom: 1rem; max-width: 52rem; }}
    td.empty {{ color: #475569; font-style: italic; }}
    .runner-section {{
      margin-top: 1.75rem;
      padding-top: 1.1rem;
      border-top: 1px solid #2d3748;
    }}
    .runner-section:first-of-type {{ border-top: none; padding-top: 0; margin-top: 0.85rem; }}
    .runner-title {{
      font-size: 1.08rem; font-weight: 600; color: #94d2bd;
      margin-bottom: 0.55rem;
    }}
    .table-chart-layout {{ width: 100%; }}
    .table-scroll {{ overflow-x: auto; }}
    th.col-check, td.col-check {{
      width: 2.75rem; max-width: 3.25rem; min-width: 2.5rem;
      padding-left: 0.35rem; padding-right: 0.35rem;
      text-align: center;
      vertical-align: middle;
      font-variant-numeric: tabular-nums;
    }}
    td.col-check {{ white-space: nowrap; }}
    th.col-check {{
      text-transform: none;
      letter-spacing: 0;
      line-height: 1.15;
      padding-top: 0.45rem; padding-bottom: 0.45rem;
    }}
    .col-check-hdr {{ display: block; font-size: 0.65rem; font-weight: 600; color: #94a3b8; }}
    th.col-elapsed, td.col-elapsed {{
      white-space: nowrap;
    }}
    th.col-score-state, td.score-state-cell {{
      min-width: 16rem;
      width: 38%;
      vertical-align: middle;
    }}
    .score-state-track {{
      position: relative;
      width: 100%;
      min-width: 14rem;
      min-height: 2.85rem;
      border-radius: 8px;
      overflow: hidden;
      background: #252b3a;
    }}
    .score-state-track-zero-only {{
      display: flex;
      align-items: center;
      justify-content: center;
      background: #252b3a;
    }}
    .score-state-fill {{
      position: absolute;
      left: 0;
      top: 0;
      bottom: 0;
      border-radius: 7px;
      transition: width 0.15s ease;
      z-index: 0;
    }}
    .score-state-fill-good {{ background: linear-gradient(90deg, #14532d, #4ade80); }}
    .score-state-fill-bad  {{ background: linear-gradient(90deg, #450a0a, #f87171); }}
    .score-state-text {{
      position: relative;
      z-index: 1;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 2.85rem;
      padding: 0 0.35rem;
      font-size: 1.05rem;
      font-weight: 650;
      font-variant-numeric: tabular-nums;
      color: #f1f5f9;
      text-shadow:
        0 0 4px #0f1117,
        0 1px 3px #0f1117,
        0 0 14px #0f1117;
      pointer-events: none;
    }}
    .score-state-text-nobar {{
      position: static;
      min-height: 0;
      text-shadow: none;
    }}
    .score-state-num-ok {{ color: #4ade80; }}
    .score-state-num-fail {{ color: #f87171; }}
    .score-state-empty {{ color: #475569; }}
    .status-legend {{
      display: flex; flex-wrap: wrap; gap: 0.5rem 1rem;
      align-items: center; margin-bottom: 0.75rem; font-size: 0.82rem; color: #94a3b8;
    }}
    .status-legend-item {{ display: flex; align-items: center; gap: 0.35rem; }}
    .swatch {{
      display: inline-block; width: 12px; height: 12px;
      border-radius: 2px; flex-shrink: 0;
    }}
    .swatch-ok   {{ background: #59a14f; }}
    .swatch-fail {{ background: #e15759; }}
  </style>
</head>
<body>
  <h1>Sensors check history</h1>
  <div class="meta">
    Source: {source} &nbsp;|&nbsp; Span: {span_str}
    &nbsp;|&nbsp; Runners: {", ".join(runner_names) or "none"}
  </div>

  <div class="cards">
    <div class="card blue">
      <div class="card-value">{total_checks}</div>
      <div class="card-label">total checks</div>
    </div>
    <div class="card {'red' if total_failures else 'green'}">
      <div class="card-value">{total_failures}</div>
      <div class="card-label">failure checks (all runners)</div>
    </div>
    <div class="card {'green' if total_recoveries else 'blue'}">
      <div class="card-value">{total_recoveries}</div>
      <div class="card-label">status recoveries</div>
    </div>
    {f'<div class="card blue"><div class="card-value">{filtered_count}</div><div class="card-label">filtered checks (--runner)</div></div>' if filtered_count else ""}
  </div>

  <h2>Check status per runner</h2>
  <div class="chart-wrap">
    <div class="status-legend">
      <span class="status-legend-item"><span class="swatch swatch-ok"></span> success</span>
      <span class="status-legend-item"><span class="swatch swatch-fail"></span> failure</span>
    </div>
    <canvas id="statusChart"></canvas>
    <p class="note">
      Each bar is one global check call. Colour = success/failure per runner.
      X axis = check number (equal spacing).
    </p>
  </div>

  <h2>Runners</h2>
  <p class="note section-intro">One row per check for each runner. &ldquo;State&rdquo; bar fill is score &divide; max(largest score in that runner&rsquo;s checks, {STATE_BAR_SCALE_MIN}). Bar color is <em>status</em> (green = success, red = failure). At score 0 the number is green or red instead. Event highlights transitions; steady means unchanged vs the previous check.</p>
  {"".join(runner_sections)}

  <script>
    const STATUS_DATASETS = {status_datasets_json};
    const ALL_TS          = {all_checks_ts_json};

    const TOOLTIP_STYLE = {{
      backgroundColor: "#1e2330",
      borderColor: "#2d3748",
      borderWidth: 1,
      titleColor: "#94d2bd",
      bodyColor: "#e2e8f0",
      padding: 10,
    }};

    // --- Status chart ---
    // x axis = global check index so all runners share the same columns.
    new Chart(document.getElementById("statusChart"), {{
      type: "bar",
      data: {{
        labels: ALL_TS.map((_, i) => i),
        datasets: STATUS_DATASETS,
      }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{
            ...TOOLTIP_STYLE,
            callbacks: {{
              title: (items) => `Check #${{items[0].label * 1 + 1}} — ${{ALL_TS[items[0].label]}}`,
              label: (ctx) => {{
                const ds = STATUS_DATASETS[ctx.datasetIndex];
                const col = ds.backgroundColor[ctx.dataIndex];
                const st = col === "#59a14f" ? "success" : col === "#e15759" ? "failure" : "-";
                return ` ${{ds.label}}: ${{st}}`;
              }},
            }},
          }},
        }},
        scales: {{
          x: {{
            stacked: true,
            title: {{ display: true, text: "Check #", color: "#64748b" }},
            ticks: {{
              color: "#64748b",
              callback: (v) => `#${{v + 1}}`,
            }},
            grid: {{ color: "#1e2a3a" }},
          }},
          y: {{ stacked: true, display: false }},
        }},
      }},
    }});
  </script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <history.jsonl> [output.html]", file=sys.stderr)
        sys.exit(1)

    src = Path(sys.argv[1])
    if not src.exists():
        print(f"Error: {src} does not exist", file=sys.stderr)
        sys.exit(1)

    out = Path(sys.argv[2]) if len(sys.argv) > 2 else src.parent / "history_report.html"

    records = load_history(src)
    if not records:
        print("No records found in history file.", file=sys.stderr)
        sys.exit(0)

    timelines = build_timelines(records)
    html = build_html(records, timelines, str(src))
    out.write_text(html, encoding="utf-8")
    print(f"Report written to {out}")
    webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
