"""Regenerate the README workflow SVGs from docs/diagrams/workflow-graph.html.

The HTML page is the source of truth: its SVG uses CSS variables and web
fonts, which GitHub does not apply to an image in a README. This writes two
self-contained copies, workflow-graph-light.svg and workflow-graph-dark.svg,
with the colours filled in, system fonts, a background and the legend drawn
into the image.

    python scripts/diagram_svgs.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DIAGRAMS = Path(__file__).resolve().parents[1] / "docs" / "diagrams"
SANS = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, 'SF Mono', Menlo, Consolas, monospace"
LEGEND_H = 48

# (fill var, stroke var, stroke width, dashed, label, x step); steps are set so
# labels do not overlap at 11.5 px.
LEGEND = [
    ("panel", "ink", 1.5, False, "saved on your computer", 190),
    ("panel", "line", 1.0, False, "software step", 134),
    (None, "muted", 1.5, True, "temporary, nothing kept", 196),
    ("rose-soft", "rose", 1.5, False, "identifiable", 128),
    ("amber-soft", "amber", 1.5, False, "you decide", 116),
    ("teal-soft", "teal", 1.5, False, "what the study tests", 0),
]


def palettes(html: str) -> dict[str, dict[str, str]]:
    def block(pattern: str) -> dict[str, str]:
        match = re.search(pattern + r"\s*\{(.*?)\}", html, re.DOTALL)
        if not match:
            raise SystemExit(f"palette block not found: {pattern}")
        return dict(re.findall(r"--([\w-]+):\s*([^;]+);", match.group(1)))

    return {"light": block(r":root"), "dark": block(r':root\[data-theme="dark"\]')}


def style(c: dict[str, str]) -> str:
    return f"""  <style>
    .bandtitle {{ font: 600 15px {SANS}; fill: {c['muted']}; letter-spacing: 0.06em; }}
    .node {{ font: 600 13.5px {SANS}; fill: {c['ink']}; }}
    .sub {{ font: 400 11px {SANS}; fill: {c['muted']}; }}
    .subm {{ font: 400 10.5px {MONO}; fill: {c['muted']}; }}
    .edge {{ font: 400 10.5px {MONO}; fill: {c['muted']}; }}
    .leg {{ font: 400 11.5px {SANS}; fill: {c['muted']}; }}
    .tealtxt {{ fill: {c['teal']}; }}
    .ambertxt {{ fill: {c['amber']}; }}
    .rosetxt {{ fill: {c['rose']}; }}
  </style>"""


def legend(c: dict[str, str]) -> str:
    parts, x = ["  <g>"], 30
    for fill, stroke, width, dashed, label, step in LEGEND:
        dash = ' stroke-dasharray="4 3"' if dashed else ""
        parts.append(
            f'    <rect x="{x}" y="-36" width="22" height="14" rx="3" '
            f'fill="{c[fill] if fill else "none"}" stroke="{c[stroke]}" '
            f'stroke-width="{width}"{dash}/>')
        parts.append(f'    <text class="leg" x="{x + 30}" y="-25">{label}</text>')
        x += step
    parts.append("  </g>")
    return "\n".join(parts)


def render(html: str, c: dict[str, str]) -> str:
    match = re.search(r"<svg viewBox=\"0 0 (\d+) (\d+)\"(.*?)>(.*)</svg>", html, re.DOTALL)
    if not match:
        raise SystemExit("no <svg viewBox=...> in the HTML")
    width, height, attrs, body = int(match[1]), int(match[2]), match[3], match[4]
    body = re.sub(r"var\(--([\w-]+)\)", lambda m: c[m.group(1)], body)
    top, total = -LEGEND_H, height + LEGEND_H
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 {top} {width} {total}"{attrs}>\n'
        f"{style(c)}\n"
        f'  <rect x="0" y="{top}" width="{width}" height="{total}" fill="{c["paper"]}"/>\n'
        f"{legend(c)}\n{body}</svg>\n"
    )


def main() -> int:
    html = (DIAGRAMS / "workflow-graph.html").read_text()
    for name, colours in palettes(html).items():
        out = DIAGRAMS / f"workflow-graph-{name}.svg"
        out.write_text(render(html, colours))
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
