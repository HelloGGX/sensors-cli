"""Generic formatter that renders ParsedOutput into FormattedOutput for all client types.

A single GenericFormatter instance replaces the 6 format_* methods that each parser
previously had to implement. Parsers return structured ParsedOutput; this class handles
all rendering.
"""

from sensors.config.result_types import (
    Finding,
    FormattedOutput,
    GuidanceBlock,
    ParsedOutput,
)


def _apply_color(text: str, color: str, style: str) -> str:
    """Apply a color token to text for the target style."""
    if style == "terminal":
        return f"[{color}]{text}[/{color}]"
    if style == "html":
        css = {
            "green": "sensors-success",
            "yellow": "sensors-warn",
            "red": "sensors-error",
            "dim": "sensors-dim",
        }.get(color, "sensors-info")
        return f'<span class="{css}">{text}</span>'
    # llm / plain text
    return text


class GenericFormatter:
    """Renders a ParsedOutput into FormattedOutput for terminal, HTML, and LLM targets."""

    def format(self, parsed: ParsedOutput) -> FormattedOutput:
        return FormattedOutput(
            details_terminal=self._details(parsed, "terminal"),
            details_html=self._details(parsed, "html"),
            details_llm=self._details(parsed, "llm"),
            failures_terminal=self._failures(parsed, "terminal"),
            failures_html=self._failures(parsed, "html"),
            failures_llm=self._failures(parsed, "llm"),
        )

    def _details(self, parsed: ParsedOutput, style: str) -> str:
        color = "green" if parsed.success else "red"
        return _apply_color(parsed.summary, color, style)

    def _failures(self, parsed: ParsedOutput, style: str) -> str:
        if parsed.success:
            return ""
        parts = [self._render_finding(f, style) for f in parsed.findings]
        parts += [self._render_guidance(g, style) for g in parsed.guidance]
        if not parts:
            return _apply_color(parsed.summary, "red", style)
        return "\n".join(p for p in parts if p)

    def _render_finding(self, f: Finding, style: str) -> str:
        loc = _build_loc(f)
        if style == "html":
            return _render_finding_html(f, loc)
        return _render_finding_text(f, loc, style)

    def _render_guidance(self, g: GuidanceBlock, style: str) -> str:
        header = g.rule + (f" — {g.summary}" if g.summary else "")
        if style == "terminal":
            lines = [f"  [yellow]{header}[/yellow]"]
            lines += [f"  [dim]{line}[/dim]" for line in g.body.splitlines()]
            return "\n".join(lines)
        if style == "html":
            return (
                f'<div class="sensors-guidance">'
                f'<span class="sensors-rule">{header}</span>'
                f'<div class="sensors-message">{g.body}</div>'
                f'</div>'
            )
        return f"  {header}\n" + "\n".join(f"  {line}" for line in g.body.splitlines())


def _build_loc(f: Finding) -> str | None:
    loc_parts: list[str] = []
    if f.file:
        loc_parts.append(f.file)
        if f.line is not None:
            loc_parts.append(str(f.line))
            if f.column is not None:
                loc_parts.append(str(f.column))
    return ":".join(loc_parts) if loc_parts else None


def _render_finding_html(f: Finding, loc: str | None) -> str:
    parts: list[str] = []
    if loc:
        parts.append(f'<span class="sensors-file">{loc}</span>')
    if f.rule:
        parts.append(f'<span class="sensors-rule">{f.rule}</span>')
    parts.append(f'<span class="sensors-message">{f.message}</span>')
    css = f"sensors-violation sensors-{f.severity}"
    inner = " ".join(parts)
    if f.context:
        inner += f'<div class="sensors-context">{f.context}</div>'
    return f'<div class="{css}">{inner}</div>'


def _render_finding_text(f: Finding, loc: str | None, style: str) -> str:
    color = {"error": "red", "warning": "yellow", "info": "dim"}.get(f.severity, "red")
    text_parts: list[str] = []
    if loc:
        text_parts.append(_apply_color(loc, color, style))
    if f.rule:
        text_parts.append(f"[dim]{f.rule}[/dim]" if style == "terminal" else f.rule)
    text_parts.append(f.message)
    line = "  " + " ".join(text_parts)
    if f.context:
        line += f"\n    {f.context}"
    return line
