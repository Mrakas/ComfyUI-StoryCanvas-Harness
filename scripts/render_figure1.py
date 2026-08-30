"""Render the repository's deterministic Figure 1 JSON spec to editable SVG."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = ROOT / "figures" / "specs" / "storycanvas-figure1.json"
DEFAULT_OUTPUT = ROOT / "figures" / "storycanvas-figure1.svg"
CANONICAL_NODE_COUNT = 13

STYLES = {
    "module": ("#FFF7ED", "#F97316", "#9A3412"),
    "skill": ("#ECFDF5", "#10B981", "#065F46"),
    "plugin": ("#EEF2FF", "#6366F1", "#3730A3"),
    "pack": ("#F5F3FF", "#8B5CF6", "#5B21B6"),
    "profile": ("#EFF6FF", "#3B82F6", "#1E40AF"),
    "kernel": ("#F8FAFC", "#64748B", "#0F172A"),
    "media": ("#ECFEFF", "#0891B2", "#155E75"),
    "output": ("#F0FDF4", "#22C55E", "#166534"),
    "future": ("#FAF5FF", "#A855F7", "#6B21A8"),
    "loop": ("#FFFFFF", "#94A3B8", "#334155"),
}


def _anchor(node: dict[str, Any], side: str) -> tuple[float, float]:
    x = float(node["x"])
    y = float(node["y"])
    width = float(node["width"])
    height = float(node["height"])
    anchors = {
        "left": (x, y + height / 2),
        "right": (x + width, y + height / 2),
        "top": (x + width / 2, y),
        "bottom": (x + width / 2, y + height),
    }
    if side not in anchors:
        raise ValueError(f"Unsupported anchor: {side!r}")
    return anchors[side]


def _text_block(
    label: str,
    *,
    x: float,
    y: float,
    font_size: int,
    color: str,
    weight: int = 600,
    line_height: float = 1.18,
    anchor: str = "middle",
) -> str:
    lines = label.split("\n")
    start_y = y - ((len(lines) - 1) * font_size * line_height) / 2
    normalized_weight = max(100, min(900, round(weight / 100) * 100))
    return "\n".join(
        f'<text x="{x:g}" y="{start_y + index * font_size * line_height:g}" '
        f'text-anchor="{anchor}" font-family="Inter, Arial, Helvetica, sans-serif" '
        f'font-size="{font_size}" font-weight="{normalized_weight}" fill="{color}">'
        f"{html.escape(line)}</text>"
        for index, line in enumerate(lines)
    )


def validate_spec(spec: dict[str, Any]) -> None:
    canvas = spec.get("canvas") or {}
    width = int(canvas.get("width", 0))
    height = int(canvas.get("height", 0))
    if width <= 0 or height <= 0:
        raise ValueError("canvas.width and canvas.height must be positive")

    nodes = spec.get("nodes") or []
    node_ids = [str(node.get("id", "")) for node in nodes]
    if not node_ids or any(not node_id for node_id in node_ids):
        raise ValueError("Every node requires a non-empty id")
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("Node ids must be unique")
    for node in nodes:
        if node.get("style") not in STYLES:
            raise ValueError(f"Unknown node style: {node.get('style')!r}")
        x, y = float(node["x"]), float(node["y"])
        node_width, node_height = float(node["width"]), float(node["height"])
        if min(x, y, node_width, node_height) < 0:
            raise ValueError(f"Node {node['id']!r} has a negative geometry value")
        if x + node_width > width or y + node_height > height:
            raise ValueError(f"Node {node['id']!r} falls outside the canvas")
    known = set(node_ids)
    for edge in spec.get("edges") or []:
        if edge.get("from") not in known or edge.get("to") not in known:
            raise ValueError(f"Edge references unknown node: {edge}")


def render_svg(spec: dict[str, Any]) -> str:
    validate_spec(spec)
    canvas = spec["canvas"]
    width, height = int(canvas["width"]), int(canvas["height"])
    background = str(canvas.get("background", "#FFFFFF"))
    nodes = {str(node["id"]): node for node in spec["nodes"]}
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" '
        f'aria-labelledby="figure-title figure-description">',
        f'<title id="figure-title">{html.escape(str(spec["title"]))}</title>',
        f'<desc id="figure-description">{html.escape(str(spec["description"]))}</desc>',
        "<defs>",
        '<filter id="shadow" x="-20%" y="-20%" width="140%" height="160%">'
        '<feDropShadow dx="0" dy="5" stdDeviation="6" flood-color="#0F172A" '
        'flood-opacity="0.10"/></filter>',
        '<marker id="arrow" markerWidth="9" markerHeight="9" refX="7" refY="4.5" '
        'orient="auto" markerUnits="strokeWidth">'
        '<path d="M 0 0 L 9 4.5 L 0 9 z" fill="#64748B"/></marker>',
        "</defs>",
        f'<rect width="{width}" height="{height}" fill="{background}"/>',
    ]

    parts.append(
        _text_block(
            str(spec["title"]),
            x=60,
            y=55,
            font_size=34,
            color="#0F172A",
            weight=760,
            anchor="start",
        )
    )
    parts.append(
        _text_block(
            str(spec["subtitle"]),
            x=60,
            y=96,
            font_size=17,
            color="#475569",
            weight=450,
            anchor="start",
        )
    )

    for section in spec.get("sections") or []:
        parts.append(
            f'<rect x="{section["x"]}" y="{section["y"]}" width="{section["width"]}" '
            f'height="{section["height"]}" rx="22" fill="{section["fill"]}" '
            f'stroke="{section["stroke"]}" stroke-width="1.5"/>'
        )
        parts.append(
            _text_block(
                str(section["title"]),
                x=float(section["x"]) + 24,
                y=float(section["y"]) + 34,
                font_size=17,
                color=str(section.get("text_color", "#334155")),
                weight=720,
                anchor="start",
            )
        )

    for edge in spec.get("edges") or []:
        start = _anchor(nodes[str(edge["from"])], str(edge.get("from_anchor", "right")))
        end = _anchor(nodes[str(edge["to"])], str(edge.get("to_anchor", "left")))
        points = [start, *[tuple(point) for point in edge.get("via", [])], end]
        rendered_points = " ".join(f"{x:g},{y:g}" for x, y in points)
        dash = ' stroke-dasharray="8 7"' if edge.get("dashed") else ""
        parts.append(
            f'<polyline points="{rendered_points}" fill="none" stroke="#64748B" '
            f'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"{dash} '
            'marker-end="url(#arrow)"/>'
        )
        if edge.get("label"):
            label_x, label_y = edge.get("label_at", points[len(points) // 2])
            parts.append(
                f'<rect x="{float(label_x) - 52:g}" y="{float(label_y) - 14:g}" '
                'width="104" height="24" rx="12" fill="#F8FAFC" opacity="0.96"/>'
            )
            parts.append(
                _text_block(
                    str(edge["label"]),
                    x=float(label_x),
                    y=float(label_y) + 2,
                    font_size=11,
                    color="#475569",
                    weight=650,
                )
            )

    for node in spec["nodes"]:
        fill, stroke, text_color = STYLES[str(node["style"])]
        dashed = ' stroke-dasharray="8 6"' if node.get("dashed") else ""
        parts.append(
            f'<g id="node-{html.escape(str(node["id"]))}" filter="url(#shadow)">'
            f'<rect x="{node["x"]}" y="{node["y"]}" width="{node["width"]}" '
            f'height="{node["height"]}" rx="16" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="2"{dashed}/></g>'
        )
        label_y = float(node["y"]) + float(node["height"]) / 2
        if node.get("detail"):
            label_y -= 10
        parts.append(
            _text_block(
                str(node["label"]),
                x=float(node["x"]) + float(node["width"]) / 2,
                y=label_y,
                font_size=int(node.get("font_size", 15)),
                color=text_color,
                weight=720,
            )
        )
        if node.get("detail"):
            parts.append(
                _text_block(
                    str(node["detail"]),
                    x=float(node["x"]) + float(node["width"]) / 2,
                    y=float(node["y"]) + float(node["height"]) - 18,
                    font_size=int(node.get("detail_font_size", 11)),
                    color="#64748B",
                    weight=500,
                )
            )

    legend = str(spec.get("legend", ""))
    if legend:
        parts.append(
            _text_block(
                legend,
                x=width - 60,
                y=96,
                font_size=12,
                color="#64748B",
                weight=520,
                anchor="end",
            )
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    spec_path = args.spec.resolve()
    output_path = args.output.resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if spec_path == DEFAULT_SPEC.resolve() and len(spec.get("nodes") or []) != CANONICAL_NODE_COUNT:
        raise SystemExit(f"Canonical Figure 1 must contain exactly {CANONICAL_NODE_COUNT} nodes")
    rendered = render_svg(spec)
    try:
        display_path: Path | str = output_path.relative_to(ROOT)
    except ValueError:
        display_path = output_path
    if args.check:
        if not output_path.is_file() or output_path.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"Figure is stale; run: python {Path(__file__).name}")
        print(f"validated: {display_path}")
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    print(f"rendered: {display_path}")


if __name__ == "__main__":
    main()
