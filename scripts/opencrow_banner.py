#!/usr/bin/env python3
"""Shared OpenCROW banner helpers for lightweight CLIs."""

from __future__ import annotations

import math
import re
import textwrap
from pathlib import Path
from typing import Any

try:
    from rich import box
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.text import Text
except ModuleNotFoundError:  # pragma: no cover - exercised on hosts without rich installed
    box = None
    Console = Any  # type: ignore[assignment]
    Group = Any  # type: ignore[assignment]
    Panel = Any  # type: ignore[assignment]
    Text = Any  # type: ignore[assignment]
    RICH_AVAILABLE = False
else:
    RICH_AVAILABLE = True


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
ICON_ASPECT_RATIO = 1.9
PANEL_BORDER = "#A567D3"
ICON_COLOR = "#F16C48"
ICON_EYE_COLOR = "#F8F0E8"
SUBTITLE_COLOR = "#F0F2F8"
BOOT_COLOR = "#42E481"
WORDMARK_COLORS = [
    "#E2B400",
    "#EE9F00",
    "#F98C00",
    "#FF7530",
    "#FF5D51",
    "#FF456F",
    "#FF328D",
    "#F225AC",
]

ASCII_WORDMARKS: dict[str, str] | None = None


# TODO: replace this generated temporary crow icon with the final approved 16x16/32x32 assets.
def inside_ellipse(x: float, y: float, cx: float, cy: float, rx: float, ry: float) -> bool:
    if rx <= 0 or ry <= 0:
        return False
    return ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1.0


def triangle_area_sign(
    px: float,
    py: float,
    ax: float,
    ay: float,
    bx: float,
    by: float,
    cx: float,
    cy: float,
) -> float:
    return (px - cx) * (ay - cy) - (ax - cx) * (py - cy)


def inside_triangle(
    x: float,
    y: float,
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> bool:
    d1 = triangle_area_sign(x, y, a[0], a[1], b[0], b[1], c[0], c[1])
    d2 = triangle_area_sign(x, y, b[0], b[1], c[0], c[1], a[0], a[1])
    d3 = triangle_area_sign(x, y, c[0], c[1], a[0], a[1], b[0], b[1])
    has_neg = d1 < 0 or d2 < 0 or d3 < 0
    has_pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_neg and has_pos)


def generate_temp_crow_icon(size: int) -> str:
    height = size
    width = max(size, int(round(size * ICON_ASPECT_RATIO)))
    center_x = (width - 1) / 2
    center_y = (height - 1) / 2
    outer_rx = width * 0.24
    outer_ry = height * 0.43
    inner_rx = width * 0.125
    inner_ry = height * 0.27
    head_cx = center_x + width * 0.03
    head_cy = center_y - height * 0.29
    head_rx = width * 0.15
    head_ry = height * 0.15
    beak = (
        (center_x - width * 0.19, center_y - height * 0.17),
        (center_x - width * 0.01, center_y - height * 0.18),
        (center_x - width * 0.07, center_y - height * 0.03),
    )
    mouth_gap = (
        (center_x - width * 0.15, center_y - height * 0.14),
        (center_x - width * 0.02, center_y - height * 0.16),
        (center_x - width * 0.07, center_y - height * 0.08),
    )
    eye_cx = center_x + width * 0.015
    eye_cy = center_y - height * 0.33
    eye_cell = (round(eye_cx), round(eye_cy))
    feather_lines = [
        (-0.42, height * 0.10),
        (-0.36, height * 0.17),
        (-0.31, height * 0.23),
        (-0.26, height * 0.29),
    ]
    feather_thickness = max(0.85, height * 0.028)
    lines: list[str] = []

    for y in range(height):
        row: list[str] = []
        for x in range(width):
            filled = inside_ellipse(x, y, center_x, center_y, outer_rx, outer_ry)
            if inside_ellipse(x, y, center_x - width * 0.02, center_y + height * 0.01, inner_rx, inner_ry):
                filled = False
            if inside_ellipse(x, y, head_cx, head_cy, head_rx, head_ry):
                filled = True
            if inside_triangle(x, y, *beak):
                filled = True
            if inside_triangle(x, y, *mouth_gap):
                filled = False

            if x > center_x + width * 0.04 and y > center_y - height * 0.10:
                for slope, offset in feather_lines:
                    target_y = slope * (x - center_x) + center_y + offset
                    if abs(y - target_y) <= feather_thickness:
                        filled = False
                        break

            arc_gap = abs(math.hypot((x - center_x) * 0.55, y - center_y) - height * 0.33) <= max(0.9, height * 0.025)
            if arc_gap and x < center_x and y < center_y:
                filled = False

            if (x, y) == eye_cell:
                row.append("o")
            else:
                row.append("@" if filled else " ")
        lines.append("".join(row))
    return "\n".join(lines)


ASCII_ICON_16 = generate_temp_crow_icon(16)
ASCII_ICON_32 = generate_temp_crow_icon(32)


def append_ascii_segment(target: Text, segment: str, fg_color: str | None = None) -> None:
    for char in segment:
        if char == " ":
            target.append(" ")
        else:
            style = f"bold {fg_color}" if fg_color else ""
            if char == "o":
                style = f"bold {ICON_EYE_COLOR}"
            target.append(char, style=style)


def append_gradient_segment(target: Text, segment: str, colors: list[str]) -> None:
    visible_columns = [index for index, char in enumerate(segment) if char != " "]
    if not visible_columns:
        target.append(segment)
        return

    min_col = visible_columns[0]
    max_col = visible_columns[-1]
    span = max(max_col - min_col, 1)

    for index, char in enumerate(segment):
        if char == " ":
            target.append(" ")
            continue
        color_index = min(((index - min_col) * len(colors)) // (span + 1), len(colors) - 1)
        target.append(char, style=f"bold {colors[color_index]}")


def empty_panel_line(width: int) -> Text:
    return Text(" " * width)


def load_ascii_wordmarks() -> dict[str, str]:
    global ASCII_WORDMARKS
    if ASCII_WORDMARKS is not None:
        return ASCII_WORDMARKS

    candidates = [
        SCRIPT_DIR / "ascii_text.md",
        ROOT_DIR / "ascii_text.md",
    ]
    ascii_text_path = next((path for path in candidates if path.exists()), None)
    if ascii_text_path is None:
        candidate_list = ", ".join(str(path) for path in candidates)
        raise RuntimeError(f"ascii_text.md was not found in any expected location: {candidate_list}")

    raw = ascii_text_path.read_text(encoding="utf-8")
    matches = re.findall(r"```(\w+)\n(.*?)\n```", raw, flags=re.DOTALL)
    wordmarks = {name.strip().lower(): block.strip("\n") for name, block in matches}
    required = {"xxsmall", "xsmall", "small", "medium", "large"}
    missing = required.difference(wordmarks)
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise RuntimeError(f"ascii_text.md is missing required blocks: {missing_list}")
    ASCII_WORDMARKS = wordmarks
    return wordmarks


def renderable_width(renderable: str) -> int:
    return max(len(line) for line in renderable.splitlines())


def renderable_height(renderable: str) -> int:
    return len(renderable.splitlines())


def selected_wordmark_for_terminal(available_width: int, available_height: int) -> str:
    wordmarks = load_ascii_wordmarks()
    for key in ("large", "medium", "small", "xsmall", "xxsmall"):
        wordmark = wordmarks[key]
        if renderable_width(wordmark) <= available_width and renderable_height(wordmark) <= max(available_height, 1):
            return wordmark
    return wordmarks["xxsmall"]


def build_wordmark_lines(wordmark: str) -> list[Text]:
    lines: list[Text] = []
    for raw_line in wordmark.splitlines():
        line = Text()
        append_gradient_segment(line, raw_line, WORDMARK_COLORS)
        lines.append(line)
    return lines


def selected_icon_for_terminal(width: int, height: int) -> str:
    minimum_wordmark_width = renderable_width(load_ascii_wordmarks()["xsmall"])
    for icon in (ASCII_ICON_32, ASCII_ICON_16):
        if renderable_height(icon) <= max(height - 8, 1) and renderable_width(icon) + minimum_wordmark_width + 10 <= width:
            return icon
    return ASCII_ICON_16


def build_splash_lines(icon: str, width: int, height: int) -> list[Text]:
    icon_lines = icon.splitlines()
    icon_width = max(len(line) for line in icon_lines)
    gap = 4
    available_right_width = max(width - icon_width - gap - 8, 12)
    wordmark = selected_wordmark_for_terminal(available_right_width, height - 8)
    wordmark_lines = build_wordmark_lines(wordmark)
    wordmark_width = max(len(line.plain) for line in wordmark_lines)
    subtitle_lines = textwrap.wrap(
        "Open Codex Runtime for Offensive Workflows",
        width=max(available_right_width, 12),
        break_long_words=False,
        break_on_hyphens=False,
    ) or ["Open Codex Runtime for Offensive Workflows"]
    boot_line = "Boot Script 2.0"
    footer_width = max([len(line) for line in subtitle_lines] + [len(boot_line)])
    right_width = max(wordmark_width, footer_width)
    right_lines: list[Text] = list(wordmark_lines)
    right_lines.append(Text(""))
    for subtitle in subtitle_lines:
        right_lines.append(Text(subtitle, style=f"bold {SUBTITLE_COLOR}"))
    right_lines.append(Text(boot_line, style=f"bold {BOOT_COLOR}"))
    total_height = max(len(icon_lines), len(right_lines))
    right_top = max((total_height - len(right_lines)) // 2, 0)

    lines: list[Text] = []
    for row in range(total_height):
        line = Text()
        icon_segment = icon_lines[row] if row < len(icon_lines) else " " * icon_width
        append_ascii_segment(line, icon_segment.ljust(icon_width), ICON_COLOR)
        append_ascii_segment(line, " " * gap, None)

        if right_top <= row < right_top + len(right_lines):
            segment = right_lines[row - right_top]
            line.append(segment)
            segment_width = len(segment.plain)
            if segment_width < right_width:
                append_ascii_segment(line, " " * (right_width - segment_width), None)
        else:
            line.append(empty_panel_line(right_width))
        lines.append(line)
    return lines


def build_banner_renderable(width: int, height: int) -> Panel | Group:
    if not RICH_AVAILABLE:
        raise RuntimeError("rich is required to render the OpenCROW banner")
    icon = selected_icon_for_terminal(width, height)
    if width < 72 or height < 16:
        subtitle = Text("Open Codex Runtime for Offensive Workflows", style=f"bold {SUBTITLE_COLOR}")
        return Group(
            Text(icon, style=f"bold {ICON_COLOR}"),
            Text(selected_wordmark_for_terminal(width, height), style=f"bold {BOOT_COLOR}"),
            subtitle,
        )
    return Panel(
        Group(*build_splash_lines(icon, width, height)),
        box=box.SQUARE,
        border_style=PANEL_BORDER,
        padding=(1, 2),
    )


def maybe_print_banner(console: Console | None = None) -> None:
    if not RICH_AVAILABLE:
        return
    if console is None:
        console = Console()
    if not console.is_terminal:
        return
    width = console.size.width
    height = max(12, min(console.size.height // 3, 16))
    console.print(build_banner_renderable(width, height))
