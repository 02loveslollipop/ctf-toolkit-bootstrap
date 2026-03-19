#!/usr/bin/env python3
"""OpenCROW installer CLI powered by Typer."""

from __future__ import annotations

import json
import math
import os
import pwd
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import tool_catalog


ROOT_DIR = Path(__file__).resolve().parent.parent
ASCII_TEXT_PATH = ROOT_DIR / "ascii_text.md"
ICON_ASPECT_RATIO = 1.9
AUTOPSY_ZIP_URL = "https://github.com/sleuthkit/autopsy/releases/download/autopsy-4.22.1/autopsy-4.22.1_v2.zip"
AUTOPSY_INSTALL_SCRIPT_URL = (
    "https://raw.githubusercontent.com/sleuthkit/autopsy/develop/linux_macos_install_scripts/install_application.sh"
)
SLEUTHKIT_JAVA_DEB_URL = (
    "https://github.com/sleuthkit/sleuthkit/releases/download/sleuthkit-4.14.0/sleuthkit-java_4.14.0-1_amd64.deb"
)
OPENSTEGO_ZIP_URL = "https://github.com/syvaidya/openstego/releases/download/openstego-0.8.6/openstego-0.8.6.zip"
OWASP_ZAP_TAR_URL = "https://github.com/zaproxy/zaproxy/releases/download/v2.17.0/ZAP_2.17.0_Linux.tar.gz"
STEGSOLVE_JAR_URL = (
    "https://raw.githubusercontent.com/eugenekolo/sec-tools/master/stego/stegsolve/stegsolve/stegsolve.jar"
)
THEHARVESTER_GIT_URL = "git+https://github.com/laramies/theHarvester.git"

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

            # Carve the feather separators on the right side of the body.
            if x > center_x + width * 0.04 and y > center_y - height * 0.10:
                for slope, offset in feather_lines:
                    target_y = slope * (x - center_x) + center_y + offset
                    if abs(y - target_y) <= feather_thickness:
                        filled = False
                        break

            # Open the inner spiral toward the beak.
            arc_gap = abs(math.hypot((x - center_x) * 0.55, y - center_y) - height * 0.33) <= max(0.9, height * 0.025)
            if arc_gap and x < center_x and y < center_y:
                filled = False

            if (x, y) == eye_cell:
                row.append("o")
            else:
                row.append("@" if filled else " ")
        lines.append("".join(row))
    return "\n".join(lines)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    pretty_exceptions_enable=False,
)
console = Console()


@dataclass
class InstallerContext:
    root_dir: Path
    env_name: str
    dry_run: bool
    target_user: str
    target_home: Path
    conda_bin: Path

    @property
    def target_path(self) -> str:
        return f"{self.target_home / '.local/bin'}:{os.environ.get('PATH', '')}"

    @property
    def is_target_user(self) -> bool:
        return pwd.getpwuid(os.geteuid()).pw_name == self.target_user

    def target_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = str(self.target_home)
        env["PATH"] = self.target_path
        env["OPENCROW_HOME"] = str(self.target_home)
        return env


@dataclass
class InteractiveState:
    mode: str = "fast"
    toolbox_ids: list[str] = field(default_factory=list)
    profile: str = "headless"
    tool_ids: list[str] = field(default_factory=list)


@dataclass
class TuiOption:
    value: str
    label: str
    description: str = ""
    checked: bool = False


ASCII_ICON_16 = generate_temp_crow_icon(16)
ASCII_ICON_32 = generate_temp_crow_icon(32)

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


def padded_plain_text(text: str, width: int, color: str) -> Text:
    line = Text()
    padding = max(width - len(text), 0)
    line.append(" " * padding)
    line.append(text, style=f"bold {color}")
    return line


def empty_panel_line(width: int) -> Text:
    return Text(" " * width)


def load_ascii_wordmarks() -> dict[str, str]:
    global ASCII_WORDMARKS
    if ASCII_WORDMARKS is not None:
        return ASCII_WORDMARKS

    raw = ASCII_TEXT_PATH.read_text(encoding="utf-8")
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


def interactive_summary_lines(catalog: tool_catalog.Catalog, selection: dict[str, object]) -> list[str]:
    lines: list[str] = []
    grouped: dict[str, list[dict[str, object]]] = {}
    for tool_id in selection["tool_ids"]:  # type: ignore[index]
        tool = catalog.tools[tool_id]
        grouped.setdefault(tool["toolbox"], []).append(tool)

    for toolbox_id in selection["toolboxes"]:  # type: ignore[index]
        toolbox = catalog.toolboxes[toolbox_id]
        lines.append(f"{toolbox['display_name']}")
        for tool in sorted(grouped[toolbox_id], key=lambda item: str(item["display_name"]).lower()):
            lines.append(f"  - {tool['display_name']} [{tool['install']['kind']}]")
            lines.append(f"    homepage: {tool['homepage_url']}")
            lines.append(f"    license:  {tool['license_url']}")
        lines.append("")
    return lines


def text_from_lines(lines: list[str], color: str = SUBTITLE_COLOR) -> Text:
    text = Text()
    for index, line in enumerate(lines):
        text.append(line, style=color)
        if index != len(lines) - 1:
            text.append("\n")
    return text


def render_options_panel(
    title: str,
    options: list[TuiOption],
    cursor: int,
    *,
    multi_select: bool,
    hint: str,
) -> Panel:
    lines: list[Text] = [Text(title, style=f"bold {SUBTITLE_COLOR}"), Text("")]
    for index, option in enumerate(options):
        is_selected = index == cursor
        prefix = "▶ " if is_selected else "  "
        marker = "[x] " if option.checked else "[ ] " if multi_select else "(*) " if option.checked else "( ) "
        color = BOOT_COLOR if is_selected else SUBTITLE_COLOR
        line = Text()
        line.append(prefix, style=f"bold {color}")
        line.append(marker, style=f"bold {color}")
        line.append(option.label, style=f"bold {color}")
        if option.description:
            line.append(f" - {option.description}", style=SUBTITLE_COLOR)
        lines.append(line)
    lines.extend([Text(""), Text(hint, style=f"bold {BOOT_COLOR}")])
    return Panel(
        Group(*lines),
        box=box.SQUARE,
        border_style=PANEL_BORDER,
        padding=(1, 2),
    )


def run_interactive_installer(
    catalog: tool_catalog.Catalog,
    state: InteractiveState,
    *,
    has_existing_install: bool,
) -> tuple[dict[str, object], InteractiveState, str]:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Vertical, VerticalScroll
    from textual.widget import Widget
    from textual.widgets import Static

    class PassiveVerticalScroll(VerticalScroll):
        can_focus = False

    class InstallerApp(App[object]):
        CSS = """
        #root {
            width: 1fr;
            height: 1fr;
            layout: vertical;
        }

        #banner {
            height: auto;
        }

        #body-header {
            height: auto;
        }

        #body-scroll {
            width: 1fr;
            height: 1fr;
            scrollbar-size-vertical: 0;
            scrollbar-size-horizontal: 0;
        }

        #body {
            height: auto;
        }

        #body-footer {
            height: auto;
        }
        """

        BINDINGS = [
            Binding("up", "move_up", "Up", priority=True),
            Binding("down", "move_down", "Down", priority=True),
            Binding("k", "move_up", "Up", priority=True),
            Binding("j", "move_down", "Down", priority=True),
            Binding("space", "toggle", "Toggle", priority=True),
            Binding("enter", "confirm", "Confirm", priority=True),
            Binding("b", "back", "Back", priority=True),
            Binding("pagedown", "scroll_body_down", "Page Down", priority=True),
            Binding("pageup", "scroll_body_up", "Page Up", priority=True),
            Binding("escape", "quit", "Quit", priority=True),
            Binding("q", "quit", "Quit", priority=True),
        ]

        def __init__(self, default_state: InteractiveState, existing_install: bool) -> None:
            super().__init__()
            initial_mode = default_state.mode
            if existing_install and initial_mode not in {"update", "modify"}:
                initial_mode = "update"
            self.state = InteractiveState(
                mode=initial_mode,
                toolbox_ids=list(default_state.toolbox_ids),
                profile=default_state.profile,
                tool_ids=list(default_state.tool_ids),
            )
            self.has_existing_install = existing_install
            self.saved_state = InteractiveState(
                mode="modify",
                toolbox_ids=list(default_state.toolbox_ids),
                profile=default_state.profile,
                tool_ids=list(default_state.tool_ids),
            )
            self.step = "mode"
            self.cursor = 0
            self.terms_index = 0
            if self.has_existing_install:
                self.mode_options = [
                    TuiOption("update", "Update", "Add new toolboxes to the current managed install."),
                    TuiOption("modify", "Modify", "Edit and replace the current managed install selection."),
                ]
            else:
                self.mode_options = [
                    TuiOption("fast", "Fast install", "Choose toolboxes, then one profile for all selected tools."),
                    TuiOption("personalized", "Personalized", "Choose toolboxes and then select individual tools."),
                ]
            self.toolbox_options = [
                TuiOption(
                    entry["id"],
                    entry["display_name"],
                    entry["summary"],
                    checked=entry["id"] in self.saved_state.toolbox_ids,
                )
                for entry in catalog.raw["toolboxes"]
            ]
            self.profile_options = [
                TuiOption("headless", "Headless", "CLI and automation-friendly tooling only.", checked=self.saved_state.profile == "headless"),
                TuiOption("full", "Full", "Include GUI and manual-acquisition tools when selected.", checked=self.state.profile == "full"),
            ]
            self.tool_options = self.build_tool_options()
            self.terms_options = [
                TuiOption("accept", "Accept", checked=True),
                TuiOption("deny", "Deny", checked=False),
            ]
            self.apply_mode_defaults()

        def compose(self) -> ComposeResult:
            with Vertical(id="root"):
                yield Static(id="banner")
                yield Static(id="body-header")
                with PassiveVerticalScroll(id="body-scroll"):
                    yield Static(id="body")
                yield Static(id="body-footer")

        def on_mount(self) -> None:
            body_scroll = self.query_one("#body-scroll", PassiveVerticalScroll)
            body_scroll.show_vertical_scrollbar = False
            body_scroll.show_horizontal_scrollbar = False
            body_scroll.styles.scrollbar_size_vertical = 0
            body_scroll.styles.scrollbar_size_horizontal = 0
            self.refresh_view()

        def on_resize(self, _event: object) -> None:
            self.refresh_view()

        def build_tool_options(self) -> list[TuiOption]:
            selected_toolboxes = {option.value for option in self.toolbox_options if option.checked}
            options = []
            for tool in sorted(catalog.tools.values(), key=lambda item: str(item["display_name"]).lower()):
                if tool["toolbox"] not in selected_toolboxes:
                    continue
                options.append(
                    TuiOption(
                        tool["id"],
                        tool["display_name"],
                        f"[{'/'.join(tool['profiles'])}] {tool['toolbox']}",
                        checked=tool["id"] in self.state.tool_ids,
                    )
                )
            return options

        def apply_mode_defaults(self) -> None:
            if self.state.mode == "update":
                self.state.toolbox_ids = []
                self.state.tool_ids = []
                for option in self.toolbox_options:
                    option.checked = False
                self.select_single(self.profile_options, self.saved_state.profile)
                self.tool_options = self.build_tool_options()
                return

            if self.state.mode == "modify":
                self.state.toolbox_ids = list(self.saved_state.toolbox_ids)
                self.state.tool_ids = list(self.saved_state.tool_ids)
                self.state.profile = self.saved_state.profile
                for option in self.toolbox_options:
                    option.checked = option.value in self.saved_state.toolbox_ids
                self.select_single(self.profile_options, self.saved_state.profile)
                self.tool_options = self.build_tool_options()
                for option in self.tool_options:
                    option.checked = option.value in self.saved_state.tool_ids
                return

            for option in self.toolbox_options:
                option.checked = option.value in self.state.toolbox_ids
            self.select_single(self.profile_options, self.state.profile)
            self.tool_options = self.build_tool_options()
            for option in self.tool_options:
                option.checked = option.value in self.state.tool_ids

        def proprietary_queue(self) -> list[dict[str, object]]:
            if self.state.mode in {"fast", "update"}:
                selection = tool_catalog.resolve_selection(
                    catalog,
                    profile=self.state.profile,
                    toolbox_ids=[option.value for option in self.toolbox_options if option.checked],
                    tool_ids=None,
                    mode="fast",
                )
            else:
                selection = tool_catalog.resolve_selection(
                    catalog,
                    profile=None,
                    toolbox_ids=None,
                    tool_ids=[option.value for option in self.tool_options if option.checked],
                    mode="personalized",
                )
            return proprietary_tools(catalog, selection)

        def current_options(self) -> list[TuiOption]:
            if self.step == "mode":
                return self.mode_options
            if self.step == "toolboxes":
                return self.toolbox_options
            if self.step == "profile":
                return self.profile_options
            if self.step == "tools":
                return self.tool_options
            if self.step == "terms":
                return self.terms_options
            return []

        def select_single(self, options: list[TuiOption], value: str) -> None:
            for option in options:
                option.checked = option.value == value

        def selected_toolboxes(self) -> list[str]:
            return [option.value for option in self.toolbox_options if option.checked]

        def selected_tools(self) -> list[str]:
            return [option.value for option in self.tool_options if option.checked]

        def build_selection(self) -> dict[str, object]:
            if self.state.mode in {"fast", "update"}:
                return tool_catalog.resolve_selection(
                    catalog,
                    profile=self.state.profile,
                    toolbox_ids=self.selected_toolboxes(),
                    tool_ids=None,
                    mode="fast",
                )
            return tool_catalog.resolve_selection(
                catalog,
                profile=None,
                toolbox_ids=None,
                tool_ids=self.selected_tools(),
                mode="personalized",
            )

        def validate_current_step(self) -> str | None:
            if self.step == "toolboxes" and not self.selected_toolboxes():
                return "Select at least one toolbox."
            if self.step == "tools" and not self.selected_tools():
                return "Select at least one tool."
            return None

        def selected_terms_value(self) -> str:
            for option in self.terms_options:
                if option.checked:
                    return option.value
            return "accept"

        def advance(self) -> None:
            if self.step == "mode":
                self.state.mode = next(option.value for option in self.mode_options if option.checked)
                self.apply_mode_defaults()
                self.step = "toolboxes"
                self.cursor = 0
                return
            if self.step == "toolboxes":
                if self.state.mode in {"fast", "update"}:
                    self.step = "profile"
                    self.cursor = 0
                else:
                    self.tool_options = self.build_tool_options()
                    self.step = "tools"
                    self.cursor = 0
                return
            if self.step == "profile":
                self.state.profile = next(option.value for option in self.profile_options if option.checked)
                queue = self.proprietary_queue()
                if queue:
                    self.step = "terms"
                    self.terms_index = 0
                    self.select_single(self.terms_options, "accept")
                    self.cursor = 0
                else:
                    self.step = "summary"
                return
            if self.step == "tools":
                self.state.tool_ids = self.selected_tools()
                queue = self.proprietary_queue()
                if queue:
                    self.step = "terms"
                    self.terms_index = 0
                    self.select_single(self.terms_options, "accept")
                    self.cursor = 0
                else:
                    self.step = "summary"
                return
            if self.step == "terms":
                if self.selected_terms_value() == "deny":
                    self.step = "toolboxes"
                    self.cursor = 0
                    return
                self.terms_index += 1
                if self.terms_index >= len(self.proprietary_queue()):
                    self.step = "summary"
                else:
                    self.select_single(self.terms_options, "accept")
                    self.cursor = 0
                return
            if self.step == "summary":
                selection = self.build_selection()
                final_state = InteractiveState(
                    mode=self.state.mode,
                    toolbox_ids=self.selected_toolboxes(),
                    profile=self.state.profile,
                    tool_ids=self.selected_tools(),
                )
                self.exit({"selection": selection, "state": final_state})

        def back(self) -> None:
            if self.step == "toolboxes":
                self.step = "mode"
                self.cursor = 0
            elif self.step == "profile":
                self.step = "toolboxes"
                self.cursor = 0
            elif self.step == "tools":
                self.step = "toolboxes"
                self.cursor = 0
            elif self.step == "terms":
                self.step = "profile" if self.state.mode in {"fast", "update"} else "tools"
                self.cursor = 0
            elif self.step == "summary":
                self.step = "profile" if self.state.mode in {"fast", "update"} else "tools"
                self.cursor = 0

        def screen_layout(self) -> tuple[Widget | object, Widget | object, Widget | object]:
            if self.step == "mode":
                title = "Choose install mode"
                hint = "Use ↑/↓ or j/k, Enter to continue, q to quit."
                if self.has_existing_install:
                    title = "Managed install found"
                    hint = "Update adds new tools. Modify replaces the saved selection."
                return (
                    Text(""),
                    render_options_panel(
                        title,
                        self.mode_options,
                        self.cursor,
                        multi_select=False,
                        hint=hint,
                    ),
                    Text(""),
                )
            if self.step == "toolboxes":
                return (
                    Text(""),
                    render_options_panel(
                        "Select toolboxes",
                        self.toolbox_options,
                        self.cursor,
                        multi_select=True,
                        hint="Use ↑/↓ to move, Space to toggle, Enter to continue, b to go back.",
                    ),
                    Text(""),
                )
            if self.step == "profile":
                return (
                    Text(""),
                    render_options_panel(
                        "Choose profile",
                        self.profile_options,
                        self.cursor,
                        multi_select=False,
                        hint="Use ↑/↓ to move, Enter to continue, b to go back.",
                    ),
                    Text(""),
                )
            if self.step == "tools":
                return (
                    Text(""),
                    render_options_panel(
                        "Select tools",
                        self.tool_options,
                        self.cursor,
                        multi_select=True,
                        hint="Use ↑/↓ to move, Space to toggle, Enter to continue, b to go back.",
                    ),
                    Text(""),
                )
            if self.step == "terms":
                tool = self.proprietary_queue()[self.terms_index]
                lines = [
                    f"This script will install the following proprietary package: {tool['display_name']}",
                    "",
                    f"License: {tool['license_url']}",
                    "",
                    "Do you accept terms and conditions?",
                    "",
                ]
                base = text_from_lines(lines)
                options_panel = render_options_panel(
                    f"Proprietary package {self.terms_index + 1}/{len(self.proprietary_queue())}",
                    self.terms_options,
                    self.cursor,
                    multi_select=False,
                    hint="Enter to continue. If you deny, the installer returns to toolbox selection.",
                )
                return (
                    Text(""),
                    Panel(
                        Group(base, Text(""), options_panel),
                        box=box.SQUARE,
                        border_style=PANEL_BORDER,
                        padding=(1, 2),
                    ),
                    Text(""),
                )
            selection = self.build_selection()
            lines = interactive_summary_lines(catalog, selection)
            return (
                Panel(
                    Text("Selected OpenCROW tools", style=f"bold {SUBTITLE_COLOR}"),
                    box=box.SQUARE,
                    border_style=PANEL_BORDER,
                    padding=(1, 2),
                ),
                Panel(
                    text_from_lines(lines),
                    box=box.SQUARE,
                    border_style=PANEL_BORDER,
                    padding=(1, 2),
                ),
                Panel(
                    Text("Enter to confirm install. Press b to go back.", style=f"bold {BOOT_COLOR}"),
                    box=box.SQUARE,
                    border_style=PANEL_BORDER,
                    padding=(1, 2),
                ),
            )

        def current_scroll_line(self) -> int | None:
            if self.step in {"mode", "toolboxes", "profile", "tools"}:
                return 3 + self.cursor
            if self.step == "terms":
                return 0
            if self.step == "summary":
                return 0
            return None

        def ensure_cursor_visible(self) -> None:
            target_line = self.current_scroll_line()
            if target_line is None:
                return
            if self.step == "terms":
                self.query_one("#body-scroll", PassiveVerticalScroll).scroll_to(
                    y=0,
                    animate=False,
                    force=True,
                    immediate=True,
                )
                return
            self.query_one("#body-scroll", PassiveVerticalScroll).scroll_to(
                y=max(target_line - 2, 0),
                animate=False,
                force=True,
                immediate=True,
            )

        def refresh_view(self) -> None:
            width = self.size.width or 80
            height = self.size.height or 24
            banner_height = max(int(height * 0.4), 10)
            self.query_one("#banner", Static).update(build_banner_renderable(width, banner_height))
            header, body, footer = self.screen_layout()
            self.query_one("#body-header", Static).update(header)
            self.query_one("#body", Static).update(body)
            self.query_one("#body-footer", Static).update(footer)
            self.call_after_refresh(self.ensure_cursor_visible)

        def action_move_up(self) -> None:
            options = self.current_options()
            if options:
                self.cursor = (self.cursor - 1) % len(options)
                self.refresh_view()

        def action_move_down(self) -> None:
            options = self.current_options()
            if options:
                self.cursor = (self.cursor + 1) % len(options)
                self.refresh_view()

        def action_toggle(self) -> None:
            options = self.current_options()
            if not options:
                return
            selected = options[self.cursor]
            if self.step in {"toolboxes", "tools"}:
                selected.checked = not selected.checked
            else:
                self.select_single(options, selected.value)
            self.refresh_view()

        def action_confirm(self) -> None:
            if self.step not in {"toolboxes", "tools"}:
                options = self.current_options()
                if options:
                    self.select_single(options, options[self.cursor].value)
            error = self.validate_current_step()
            if error:
                self.notify(error, severity="warning")
                return
            self.advance()
            self.refresh_view()

        def action_back(self) -> None:
            self.back()
            self.refresh_view()

        def action_scroll_body_down(self) -> None:
            self.query_one("#body-scroll", PassiveVerticalScroll).scroll_page_down(animate=False)

        def action_scroll_body_up(self) -> None:
            self.query_one("#body-scroll", PassiveVerticalScroll).scroll_page_up(animate=False)

        def action_quit(self) -> None:
            self.exit("__quit__")

    result = InstallerApp(state, has_existing_install).run()
    console.clear()
    if result == "__quit__":
        raise typer.Exit(code=0)
    if not isinstance(result, dict):
        raise typer.Exit(code=1)
    final_state = result["state"]
    strategy = "update" if final_state.mode == "update" else "replace"
    return result["selection"], final_state, strategy


def resolve_target_identity() -> tuple[str, Path]:
    target_user = os.environ.get("SUDO_USER") or pwd.getpwuid(os.geteuid()).pw_name
    if target_user == "root" and "SUDO_USER" not in os.environ:
        target_user = pwd.getpwuid(os.geteuid()).pw_name
    target_home = Path(pwd.getpwnam(target_user).pw_dir)
    return target_user, target_home


def find_conda(target_user: str, target_home: Path) -> Path | None:
    if pwd.getpwuid(os.geteuid()).pw_name == target_user:
        resolved = shutil.which("conda")
        if resolved:
            return Path(resolved)

    for candidate in (
        target_home / "miniconda3/bin/conda",
        target_home / "anaconda3/bin/conda",
        Path("/opt/miniconda3/bin/conda"),
        Path("/opt/anaconda3/bin/conda"),
    ):
        if candidate.exists() and os.access(candidate, os.X_OK):
            return candidate
    return None


def print_conda_install_help() -> None:
    console.print(
        "Anaconda or Miniconda is required, but no conda installation was found.\n\n"
        "Download links:\n"
        "  Miniconda: https://docs.conda.io/en/latest/miniconda.html\n"
        "  Anaconda:  https://www.anaconda.com/download\n\n"
        "After installation, reopen your shell or add conda to PATH and rerun this script.",
        style="bold red",
    )


def format_command(cmd: list[str]) -> str:
    return shlex.join(cmd)


def dry_run_echo(cmd: list[str]) -> None:
    console.print(f"[dry-run] {format_command(cmd)}", markup=False)


def wrap_target_command(ctx: InstallerContext, cmd: list[str]) -> tuple[list[str], dict[str, str] | None]:
    env = ctx.target_env()
    if ctx.is_target_user:
        return cmd, env
    wrapped = [
        "sudo",
        "-u",
        ctx.target_user,
        "env",
        f"HOME={ctx.target_home}",
        f"PATH={ctx.target_path}",
        f"OPENCROW_HOME={ctx.target_home}",
        *cmd,
    ]
    return wrapped, None


def wrap_root_command(cmd: list[str]) -> tuple[list[str], dict[str, str] | None]:
    if os.geteuid() == 0:
        return cmd, None
    return ["sudo", *cmd], None


def run_command(cmd: list[str], *, env: dict[str, str] | None = None, dry_run: bool = False) -> None:
    if dry_run:
        dry_run_echo(cmd)
        return
    subprocess.run(cmd, env=env, check=True)


def run_shell(script: str, *, env: dict[str, str] | None = None, dry_run: bool = False) -> None:
    if dry_run:
        console.print(f"[dry-run] {script}", markup=False)
        return
    subprocess.run(["bash", "-lc", script], env=env, check=True)


def run_as_target(ctx: InstallerContext, cmd: list[str]) -> None:
    wrapped, env = wrap_target_command(ctx, cmd)
    run_command(wrapped, env=env, dry_run=ctx.dry_run)


def capture_as_target(ctx: InstallerContext, cmd: list[str]) -> subprocess.CompletedProcess[str]:
    wrapped, env = wrap_target_command(ctx, cmd)
    return subprocess.run(wrapped, env=env, check=True, capture_output=True, text=True)


def run_shell_as_target(ctx: InstallerContext, script: str) -> None:
    if ctx.is_target_user:
        run_shell(script, env=ctx.target_env(), dry_run=ctx.dry_run)
        return
    wrapped = [
        "sudo",
        "-u",
        ctx.target_user,
        "env",
        f"HOME={ctx.target_home}",
        f"PATH={ctx.target_path}",
        f"OPENCROW_HOME={ctx.target_home}",
        "bash",
        "-lc",
        script,
    ]
    run_command(wrapped, dry_run=ctx.dry_run)


def run_as_root(ctx: InstallerContext, cmd: list[str]) -> None:
    wrapped, env = wrap_root_command(cmd)
    run_command(wrapped, env=env, dry_run=ctx.dry_run)


def run_root_shell(ctx: InstallerContext, script: str) -> None:
    if ctx.dry_run:
        console.print(f"[dry-run] {script}")
        return
    if os.geteuid() == 0:
        subprocess.run(["bash", "-lc", script], check=True)
    else:
        subprocess.run(["sudo", "bash", "-lc", script], check=True)


def ensure_profile(profile: str | None) -> str | None:
    if profile is None:
        return None
    if profile not in {"headless", "full"}:
        raise typer.BadParameter("Profile must be 'headless' or 'full'.")
    return profile


def proprietary_tools(catalog: tool_catalog.Catalog, selection: dict[str, object]) -> list[dict[str, object]]:
    return [
        catalog.tools[tool_id]
        for tool_id in selection["tool_ids"]  # type: ignore[index]
        if catalog.tools[tool_id].get("requires_terms_acceptance")
    ]


def print_summary(catalog: tool_catalog.Catalog, selection: dict[str, object]) -> None:
    table = Table(title="Selected OpenCROW tools", show_lines=True)
    table.add_column("Toolbox", style="bold cyan")
    table.add_column("Tool")
    table.add_column("Kind", style="magenta")
    table.add_column("Homepage", overflow="fold")
    table.add_column("License", overflow="fold")

    grouped: dict[str, list[dict[str, object]]] = {}
    for tool_id in selection["tool_ids"]:  # type: ignore[index]
        tool = catalog.tools[tool_id]
        grouped.setdefault(tool["toolbox"], []).append(tool)

    for toolbox_id in selection["toolboxes"]:  # type: ignore[index]
        toolbox = catalog.toolboxes[toolbox_id]
        for tool in sorted(grouped[toolbox_id], key=lambda item: str(item["display_name"]).lower()):
            table.add_row(
                str(toolbox["display_name"]),
                str(tool["display_name"]),
                str(tool["install"]["kind"]),
                str(tool["homepage_url"]),
                str(tool["license_url"]),
            )
    console.print(table)


def resolve_interactive_selection(
    catalog: tool_catalog.Catalog,
    toolbox_ids: list[str],
    tool_ids: list[str],
    profile: str | None,
    initial_state: InteractiveState | None = None,
    *,
    has_existing_install: bool,
) -> tuple[dict[str, object], str]:
    state = initial_state or InteractiveState()
    if toolbox_ids:
        state.toolbox_ids = toolbox_ids
    if tool_ids:
        state.mode = "modify" if has_existing_install else "personalized"
        state.tool_ids = tool_ids
    if profile:
        state.profile = profile

    selection, _state, strategy = run_interactive_installer(
        catalog,
        state,
        has_existing_install=has_existing_install,
    )
    return selection, strategy


def resolve_headless_selection(
    catalog: tool_catalog.Catalog,
    toolbox_ids: list[str],
    tool_ids: list[str],
    profile: str | None,
) -> dict[str, object]:
    return tool_catalog.resolve_selection(
        catalog,
        profile=profile or "headless",
        toolbox_ids=None if tool_ids else toolbox_ids,
        tool_ids=tool_ids or None,
        mode="noninteractive",
    )


def env_exists(ctx: InstallerContext) -> bool:
    result = capture_as_target(ctx, [str(ctx.conda_bin), "env", "list", "--json"])
    payload = json.loads(result.stdout)
    env_names = {Path(path).name for path in payload.get("envs", [])}
    return ctx.env_name in env_names


def load_existing_selection(catalog: tool_catalog.Catalog) -> dict[str, object] | None:
    state_file = tool_catalog.state_path(catalog)
    if not state_file.exists():
        return None
    state = tool_catalog.load_state(catalog, state_file)
    return tool_catalog.verify_selection_from_state(catalog, state, all_tools=False)


def state_to_interactive(selection: dict[str, object]) -> InteractiveState:
    mode = str(selection.get("mode") or "fast")
    profile = selection.get("profile")
    return InteractiveState(
        mode="personalized" if profile is None and selection.get("tool_ids") else mode,
        toolbox_ids=list(selection["toolboxes"]),  # type: ignore[index]
        profile=str(profile or "headless"),
        tool_ids=list(selection["tool_ids"]),  # type: ignore[index]
    )


def merge_selections(
    existing: dict[str, object] | None,
    requested: dict[str, object],
    replace_selection: bool = False,
) -> dict[str, object]:
    if replace_selection or existing is None:
        return requested
    tool_ids = sorted({*existing["tool_ids"], *requested["tool_ids"]})  # type: ignore[index]
    toolboxes = sorted({*existing["toolboxes"], *requested["toolboxes"]})  # type: ignore[index]
    return {
        "mode": "incremental",
        "profile": requested.get("profile") or existing.get("profile"),
        "toolboxes": toolboxes,
        "tool_ids": tool_ids,
    }


def combine_selections(
    existing: dict[str, object] | None,
    requested: dict[str, object],
    *,
    strategy: str,
) -> dict[str, object]:
    if strategy == "update":
        return merge_selections(existing, requested, False)
    if strategy == "replace":
        return requested
    raise typer.BadParameter(f"Unknown selection strategy: {strategy}")


def apt_package_installed(package: str) -> bool:
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${db:Status-Status}", package],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "installed"


def module_present(ctx: InstallerContext, module_name: str) -> bool:
    if not env_exists(ctx):
        return False
    result = subprocess.run(
        [
            str(ctx.conda_bin),
            "run",
            "-n",
            ctx.env_name,
            "python",
            "-c",
            f"import importlib.util as u, sys; sys.exit(0 if u.find_spec({module_name!r}) else 1)",
        ],
        env=ctx.target_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def conda_command_present(ctx: InstallerContext, command_name: str) -> bool:
    if not env_exists(ctx):
        return False
    result = subprocess.run(
        [
            str(ctx.conda_bin),
            "run",
            "-n",
            ctx.env_name,
            "python",
            "-c",
            f"import shutil, sys; sys.exit(0 if shutil.which({command_name!r}) else 1)",
        ],
        env=ctx.target_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def target_command_present(ctx: InstallerContext, command_name: str) -> bool:
    result = subprocess.run(
        ["bash", "-lc", f"command -v {shlex.quote(command_name)} >/dev/null 2>&1"],
        env=ctx.target_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def tool_is_installed(ctx: InstallerContext, tool: dict[str, object]) -> bool:
    verify = tool["verify"]  # type: ignore[index]
    verify_kind = verify["kind"]
    verify_value = verify["value"]
    install_kind = tool["install"]["kind"]  # type: ignore[index]

    if verify_kind == "module":
        return module_present(ctx, str(verify_value))
    if verify_kind == "command":
        if install_kind == "pip":
            return conda_command_present(ctx, str(verify_value))
        return target_command_present(ctx, str(verify_value))
    return False


def pending_selection(
    ctx: InstallerContext,
    catalog: tool_catalog.Catalog,
    selection: dict[str, object],
) -> tuple[dict[str, object], list[str]]:
    installed_tool_ids: list[str] = []
    pending_tool_ids: list[str] = []
    for tool_id in selection["tool_ids"]:  # type: ignore[index]
        tool = catalog.tools[tool_id]
        if tool_is_installed(ctx, tool):
            installed_tool_ids.append(tool_id)
        else:
            pending_tool_ids.append(tool_id)
    return (
        {
            "mode": "incremental-pending",
            "profile": selection.get("profile"),
            "toolboxes": sorted({catalog.tools[tool_id]["toolbox"] for tool_id in pending_tool_ids}),
            "tool_ids": pending_tool_ids,
        },
        installed_tool_ids,
    )


def ruby_version(ctx: InstallerContext) -> str:
    result = capture_as_target(ctx, ["ruby", "-e", "print RbConfig::CONFIG[%q[ruby_version]]"])
    return result.stdout.strip()


def link_gem_executable(ctx: InstallerContext, executable: str) -> None:
    version = ruby_version(ctx)
    run_as_target(
        ctx,
        [
            "ln",
            "-sfn",
            str(ctx.target_home / f".local/share/gem/ruby/{version}/bin/{executable}"),
            str(ctx.target_home / f".local/bin/{executable}"),
        ],
    )


def install_gem_spec(ctx: InstallerContext, spec: str) -> None:
    name, _, version = spec.partition(":")
    cmd = ["gem", "install", "--user-install", name]
    if version:
        cmd.extend(["-v", version])
    run_as_target(ctx, cmd)
    link_gem_executable(ctx, name)


def write_target_executable(ctx: InstallerContext, path: Path, content: str) -> None:
    quoted = shlex.quote(content)
    run_shell_as_target(
        ctx,
        f"printf '%s\\n' {quoted} > {shlex.quote(str(path))} && chmod +x {shlex.quote(str(path))}",
    )


def install_opencrow_python_command(
    ctx: InstallerContext,
    *,
    install_name: str,
    python_script: str,
    launcher_script: str,
    support_files: list[str] | None = None,
    completion_script: str | None = None,
) -> None:
    source_dir = ROOT_DIR / "scripts"
    install_dir = ctx.target_home / f".local/opt/{install_name}"
    completion_dir = ctx.target_home / ".local/share/bash-completion/completions"
    run_as_target(ctx, ["mkdir", "-p", str(install_dir)])
    run_as_target(ctx, ["mkdir", "-p", str(ctx.target_home / ".local/bin")])

    run_as_target(
        ctx,
        [
            "install",
            "-m",
            "755",
            str(source_dir / python_script),
            str(install_dir / python_script),
        ],
    )
    for support_file in support_files or []:
        source_path = source_dir / support_file
        if not source_path.exists():
            source_path = ROOT_DIR / support_file
        destination_name = source_path.name
        run_as_target(
            ctx,
            [
                "install",
                "-m",
                "644",
                str(source_path),
                str(install_dir / destination_name),
            ],
        )
    run_as_target(
        ctx,
        [
            "install",
            "-m",
            "755",
            str(source_dir / launcher_script),
            str(install_dir / launcher_script),
        ],
    )
    if completion_script is not None:
        run_as_target(ctx, ["mkdir", "-p", str(completion_dir)])
        run_as_target(
            ctx,
            [
                "install",
                "-m",
                "644",
                str(source_dir / completion_script),
                str(completion_dir / install_name),
            ],
        )
    run_as_target(
        ctx,
        [
            "ln",
            "-sfn",
            str(install_dir / launcher_script),
            str(ctx.target_home / f".local/bin/{install_name}"),
        ],
    )


def save_state_as_target(ctx: InstallerContext, catalog: tool_catalog.Catalog, selection: dict[str, object]) -> None:
    payload = {
        "env_name": ctx.env_name,
        "mode": selection["mode"],
        "profile": selection["profile"],
        "toolboxes": selection["toolboxes"],
        "tool_ids": selection["tool_ids"],
    }
    state_file = tool_catalog.state_path(catalog)
    run_as_target(
        ctx,
        [
            "python3",
            "-c",
            (
                "import sys; "
                "from pathlib import Path; "
                "path = Path(sys.argv[1]); "
                "path.parent.mkdir(parents=True, exist_ok=True); "
                "path.write_text(sys.argv[2])"
            ),
            str(state_file),
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        ],
    )


def install_direct_handler(ctx: InstallerContext, handler: str) -> None:
    if handler == "pwninit":
        run_shell_as_target(
            ctx,
            "curl -fsSL "
            "https://github.com/io12/pwninit/releases/download/3.3.1/pwninit "
            f"-o '{ctx.target_home}/.local/bin/pwninit' && chmod +x '{ctx.target_home}/.local/bin/pwninit'",
        )
        return
    if handler == "ghidra":
        zip_name = "ghidra_12.0.4_PUBLIC_20260303.zip"
        url = (
            "https://github.com/NationalSecurityAgency/ghidra/releases/download/"
            "Ghidra_12.0.4_build/ghidra_12.0.4_PUBLIC_20260303.zip"
        )
        run_shell_as_target(
            ctx,
            f"cd '{ctx.target_home}/.local/opt' && "
            f"curl -L -o '{zip_name}' '{url}' && "
            f"unzip -q -o '{zip_name}' && "
            f"ln -sfn '{ctx.target_home}/.local/opt/ghidra_12.0.4_PUBLIC' '{ctx.target_home}/.local/opt/ghidra'",
        )
        run_as_target(
            ctx,
            [
                "ln",
                "-sfn",
                str(ctx.target_home / ".local/opt/ghidra/support/analyzeHeadless"),
                str(ctx.target_home / ".local/bin/ghidra-headless"),
            ],
        )
        run_as_target(
            ctx,
            [
                "ln",
                "-sfn",
                str(ctx.target_home / ".local/opt/ghidra/ghidraRun"),
                str(ctx.target_home / ".local/bin/ghidra"),
            ],
        )
        return
    if handler == "pwndbg":
        run_shell_as_target(
            ctx,
            "curl -qsL https://install.pwndbg.re | "
            "sh -s -- -u -v 2026.02.18 -t pwndbg-gdb",
        )
        return
    if handler == "openstego":
        install_dir = ctx.target_home / ".local/opt/openstego"
        run_shell_as_target(
            ctx,
            f"""
set -euo pipefail
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
rm -rf {shlex.quote(str(install_dir))}
mkdir -p {shlex.quote(str(install_dir.parent))}
curl -fsSL {shlex.quote(OPENSTEGO_ZIP_URL)} -o "$tmp/openstego.zip"
unzip -q -o "$tmp/openstego.zip" -d "$tmp/extract"
root="$(find "$tmp/extract" -mindepth 1 -maxdepth 1 -type d | head -n1)"
mv "$root" {shlex.quote(str(install_dir))}
chmod +x {shlex.quote(str(install_dir / 'openstego.sh'))}
ln -sfn {shlex.quote(str(install_dir / 'openstego.sh'))} {shlex.quote(str(ctx.target_home / '.local/bin/openstego'))}
""".strip(),
        )
        return
    if handler == "owasp-zap":
        install_dir = ctx.target_home / ".local/opt/zap"
        run_shell_as_target(
            ctx,
            f"""
set -euo pipefail
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
rm -rf {shlex.quote(str(install_dir))}
mkdir -p {shlex.quote(str(install_dir.parent))}
curl -fsSL {shlex.quote(OWASP_ZAP_TAR_URL)} -o "$tmp/zap.tar.gz"
mkdir -p "$tmp/extract"
tar -xzf "$tmp/zap.tar.gz" -C "$tmp/extract"
root="$(find "$tmp/extract" -mindepth 1 -maxdepth 1 -type d | head -n1)"
mv "$root" {shlex.quote(str(install_dir))}
launcher="$(find {shlex.quote(str(install_dir))} -maxdepth 2 -name 'zap.sh' | head -n1)"
chmod +x "$launcher"
ln -sfn "$launcher" {shlex.quote(str(ctx.target_home / '.local/bin/zap.sh'))}
ln -sfn "$launcher" {shlex.quote(str(ctx.target_home / '.local/bin/zaproxy'))}
""".strip(),
        )
        return
    if handler == "stegsolve":
        install_dir = ctx.target_home / ".local/opt/stegsolve"
        run_shell_as_target(
            ctx,
            f"""
set -euo pipefail
mkdir -p {shlex.quote(str(install_dir))}
curl -fsSL {shlex.quote(STEGSOLVE_JAR_URL)} -o {shlex.quote(str(install_dir / 'stegsolve.jar'))}
""".strip(),
        )
        write_target_executable(
            ctx,
            ctx.target_home / ".local/bin/stegsolve",
            "\n".join(
                [
                    "#!/bin/sh",
                    f'exec java -jar "{install_dir / "stegsolve.jar"}" "$@"',
                ]
            ),
        )
        return
    if handler == "theharvester":
        install_dir = ctx.target_home / ".local/opt/theHarvester"
        venv_dir = install_dir / "venv"
        run_shell_as_target(
            ctx,
            f"""
set -euo pipefail
mkdir -p {shlex.quote(str(install_dir))}
python3 -m venv {shlex.quote(str(venv_dir))}
{shlex.quote(str(venv_dir / 'bin/pip'))} install --upgrade pip
{shlex.quote(str(venv_dir / 'bin/pip'))} install {shlex.quote(THEHARVESTER_GIT_URL)}
ln -sfn {shlex.quote(str(venv_dir / 'bin/theHarvester'))} {shlex.quote(str(ctx.target_home / '.local/bin/theHarvester'))}
if [ -f {shlex.quote(str(venv_dir / 'bin/restfulHarvest'))} ]; then
  ln -sfn {shlex.quote(str(venv_dir / 'bin/restfulHarvest'))} {shlex.quote(str(ctx.target_home / '.local/bin/restfulHarvest'))}
fi
""".strip(),
        )
        return
    if handler == "autopsy":
        install_dir = ctx.target_home / ".local/opt/autopsy"
        run_root_shell(
            ctx,
            f"""
set -euo pipefail
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
curl -fsSL {shlex.quote(SLEUTHKIT_JAVA_DEB_URL)} -o "$tmp/sleuthkit-java.deb"
apt-get install -y "$tmp/sleuthkit-java.deb"
""".strip(),
        )
        run_shell_as_target(
            ctx,
            f"""
set -euo pipefail
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
rm -rf {shlex.quote(str(install_dir))}
mkdir -p {shlex.quote(str(install_dir.parent))}
curl -fsSL {shlex.quote(AUTOPSY_ZIP_URL)} -o "$tmp/autopsy.zip"
curl -fsSL {shlex.quote(AUTOPSY_INSTALL_SCRIPT_URL)} -o "$tmp/install_application.sh"
chmod +x "$tmp/install_application.sh"
JAVA_HOME="$(dirname "$(dirname "$(readlink -f "$(command -v java)")")")"
bash "$tmp/install_application.sh" -z "$tmp/autopsy.zip" -i {shlex.quote(str(install_dir))} -j "$JAVA_HOME" -n autopsy
launcher="$(find {shlex.quote(str(install_dir))} -path '*/bin/autopsy' | head -n1)"
chmod +x "$launcher"
ln -sfn "$launcher" {shlex.quote(str(ctx.target_home / '.local/bin/autopsy'))}
""".strip(),
        )
        return
    if handler == "opencrow-autosetup":
        install_opencrow_python_command(
            ctx,
            install_name="opencrow-autosetup",
            python_script="opencrow_autosetup.py",
            launcher_script="opencrow-autosetup",
            support_files=["opencrow_banner.py", "ascii_text.md"],
            completion_script="opencrow-autosetup.bash-completion",
        )
        return
    if handler == "opencrow-exploit":
        install_opencrow_python_command(
            ctx,
            install_name="opencrow-exploit",
            python_script="opencrow_exploit.py",
            launcher_script="opencrow-exploit",
            support_files=["opencrow_banner.py", "ascii_text.md"],
            completion_script="opencrow-exploit.bash-completion",
        )
        return
    if handler == "opencrow-stego-mcp":
        install_opencrow_python_command(
            ctx,
            install_name="opencrow-stego-mcp",
            python_script="opencrow_stego_mcp.py",
            launcher_script="opencrow-stego-mcp",
            support_files=["opencrow_mcp_core.py"],
        )
        return
    if handler == "opencrow-forensics-mcp":
        install_opencrow_python_command(
            ctx,
            install_name="opencrow-forensics-mcp",
            python_script="opencrow_forensics_mcp.py",
            launcher_script="opencrow-forensics-mcp",
            support_files=["opencrow_mcp_core.py"],
        )
        return
    if handler == "opencrow-osint-mcp":
        install_opencrow_python_command(
            ctx,
            install_name="opencrow-osint-mcp",
            python_script="opencrow_osint_mcp.py",
            launcher_script="opencrow-osint-mcp",
            support_files=["opencrow_mcp_core.py"],
        )
        return
    if handler == "opencrow-web-mcp":
        install_opencrow_python_command(
            ctx,
            install_name="opencrow-web-mcp",
            python_script="opencrow_web_mcp.py",
            launcher_script="opencrow-web-mcp",
            support_files=["opencrow_mcp_core.py"],
        )
        return
    if handler == "opencrow-netcat-mcp":
        install_opencrow_python_command(
            ctx,
            install_name="opencrow-netcat-mcp",
            python_script="opencrow_netcat_mcp.py",
            launcher_script="opencrow-netcat-mcp",
            support_files=[
                "opencrow_mcp_core.py",
                "opencrow_io_mcp_common.py",
                "skills/netcat-async/scripts/nc_async_session.py",
            ],
        )
        return
    if handler == "opencrow-ssh-mcp":
        install_opencrow_python_command(
            ctx,
            install_name="opencrow-ssh-mcp",
            python_script="opencrow_ssh_mcp.py",
            launcher_script="opencrow-ssh-mcp",
            support_files=[
                "opencrow_mcp_core.py",
                "opencrow_io_mcp_common.py",
                "skills/ssh-async/scripts/ssh_async_session.py",
            ],
        )
        return
    if handler == "opencrow-minecraft-mcp":
        install_opencrow_python_command(
            ctx,
            install_name="opencrow-minecraft-mcp",
            python_script="opencrow_minecraft_mcp.py",
            launcher_script="opencrow-minecraft-mcp",
            support_files=[
                "opencrow_mcp_core.py",
                "opencrow_io_mcp_common.py",
                "skills/minecraft-async/scripts/minecraft_async.py",
            ],
        )
        return
    raise typer.BadParameter(f"Unknown direct install handler: {handler}")


def set_tshark_debconf(ctx: InstallerContext) -> None:
    script = "echo 'wireshark-common wireshark-common/install-setuid boolean false' | debconf-set-selections"
    run_root_shell(ctx, script)


def install_selection(
    ctx: InstallerContext,
    catalog: tool_catalog.Catalog,
    requested_selection: dict[str, object],
    selection_to_save: dict[str, object],
) -> None:
    os.environ["OPENCROW_HOME"] = str(ctx.target_home)
    pending, already_installed = pending_selection(ctx, catalog, requested_selection)
    plan = tool_catalog.build_plan(catalog, pending)

    run_as_target(ctx, ["mkdir", "-p", str(ctx.target_home / ".local/bin"), str(ctx.target_home / ".local/opt")])
    run_as_target(ctx, ["mkdir", "-p", str(ctx.target_home / ".codex/skills")])

    if already_installed:
        names = ", ".join(already_installed)
        console.print(f"Already installed and kept in managed state: {names}")

    if "tshark" in plan["selected_tool_ids"]:
        set_tshark_debconf(ctx)

    apt_packages = [package for package in plan["apt_packages"] if not apt_package_installed(package)]
    if apt_packages:
        run_as_root(ctx, ["apt-get", "update"])
        run_as_root(ctx, ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", *apt_packages])

    if env_exists(ctx):
        console.print(f"Conda environment '{ctx.env_name}' already exists.")
    else:
        run_as_target(ctx, [str(ctx.conda_bin), "create", "-n", ctx.env_name, "python=3.12", "pip", "-y"])

    if plan["pip_packages"]:
        run_as_target(ctx, [str(ctx.conda_bin), "run", "-n", ctx.env_name, "pip", "install", *plan["pip_packages"]])

    for spec in plan["gem_specs"]:
        install_gem_spec(ctx, spec)

    for handler in plan["direct_handlers"]:
        install_direct_handler(ctx, handler)

    run_as_target(
        ctx,
        ["env", f"OPENCROW_HOME={ctx.target_home}", "bash", str(ctx.root_dir / "scripts/sync_skills.sh")],
    )

    if not plan["selected_tool_ids"]:
        console.print("All selected tools were already installed; only skills/state were refreshed.", style="cyan")

    if plan["manual_tool_ids"]:
        console.print()
        console.print("Manual steps are still required for some full-profile tools.", style="yellow")
        console.print("See the summary above for their homepage and license links.", style="yellow")
        console.print(f"Pending manual tools: {' '.join(plan['manual_tool_ids'])}")

    if ctx.dry_run:
        console.print("Dry-run mode: install state was not written.")
    else:
        save_state_as_target(ctx, catalog, selection_to_save)

    console.print()
    console.print("Bootstrap complete.", style="bold green")
    console.print(f"Verify with: bash '{ctx.root_dir / 'scripts/verify.sh'}' --env '{ctx.env_name}'")


def warn_noninteractive_terms(catalog: tool_catalog.Catalog, selection: dict[str, object]) -> None:
    tools = proprietary_tools(catalog, selection)
    if tools:
        names = ", ".join(str(tool["display_name"]) for tool in tools)
        raise typer.BadParameter(
            f"Non-interactive installs cannot accept proprietary terms automatically. "
            f"Rerun with a TTY and accept the prompt for: {names}"
        )


def run_install_flow(
    *,
    mode: str,
    env_name: Annotated[str, typer.Option("--env", help="Conda environment name to create/update.")] = "ctf",
    toolbox: Annotated[list[str], typer.Option("--toolbox", help="Select a toolbox. Repeatable.")] = [],
    tool: Annotated[list[str], typer.Option("--tool", help="Select an individual tool. Repeatable.")] = [],
    profile: Annotated[str | None, typer.Option("--profile", help="Install profile: headless or full.")] = None,
    all_toolboxes: Annotated[bool, typer.Option("--all-toolboxes", help="Select all OpenCROW toolboxes explicitly.")] = False,
    replace_selection: Annotated[
        bool,
        typer.Option("--replace-selection", help="Replace the saved managed selection instead of merging into it."),
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print commands without executing them.")] = False,
) -> None:
    profile = ensure_profile(profile)
    target_user, target_home = resolve_target_identity()
    conda_bin = find_conda(target_user, target_home)
    if conda_bin is None:
        print_conda_install_help()
        raise typer.Exit(code=1)

    ctx = InstallerContext(
        root_dir=ROOT_DIR,
        env_name=env_name,
        dry_run=dry_run,
        target_user=target_user,
        target_home=target_home,
        conda_bin=conda_bin,
    )
    os.environ["OPENCROW_HOME"] = str(target_home)

    console.print(f"Installing for user: {ctx.target_user}")
    console.print(f"Using conda at: {ctx.conda_bin}")

    catalog = tool_catalog.load_catalog()
    existing_selection = load_existing_selection(catalog)
    selected_toolboxes = [] if all_toolboxes else toolbox

    if mode == "headless-update" and not all_toolboxes and not selected_toolboxes and not tool:
        raise typer.BadParameter(
            "headless-update requires an explicit scope. Pass --tool, --toolbox, or --all-toolboxes."
        )

    if mode == "interactive":
        initial_state = state_to_interactive(existing_selection) if existing_selection else None
        requested_selection, strategy = resolve_interactive_selection(
            catalog,
            selected_toolboxes,
            tool,
            profile,
            initial_state,
            has_existing_install=existing_selection is not None,
        )
        selection = combine_selections(existing_selection, requested_selection, strategy=strategy)
        if existing_selection:
            message = (
                "Existing OpenCROW install state detected; updating managed tools."
                if strategy == "update"
                else "Existing OpenCROW install state detected; modifying the managed selection."
            )
            console.print(message, style="cyan")
        install_selection(ctx, catalog, requested_selection, selection)
        return

    if mode == "headless-install":
        requested_selection = resolve_headless_selection(catalog, selected_toolboxes, tool, profile)
        strategy = "replace" if replace_selection or existing_selection else "replace"
        selection = combine_selections(existing_selection, requested_selection, strategy=strategy)
        if existing_selection:
            console.print("Existing OpenCROW install state detected; replacing the managed selection.", style="cyan")
        warn_noninteractive_terms(catalog, requested_selection)
        print_summary(catalog, selection)
        install_selection(ctx, catalog, requested_selection, selection)
        return

    if mode == "headless-update":
        requested_selection = resolve_headless_selection(catalog, selected_toolboxes, tool, profile)
        selection = combine_selections(existing_selection, requested_selection, strategy="update")
        if existing_selection:
            console.print("Existing OpenCROW install state detected; applying an incremental update.", style="cyan")
        else:
            console.print("No existing OpenCROW install state detected; creating managed state from the requested selection.", style="cyan")
        warn_noninteractive_terms(catalog, requested_selection)
        print_summary(catalog, selection)
        install_selection(ctx, catalog, requested_selection, selection)
        return

    raise typer.BadParameter(f"Unknown installer mode: {mode}")


@app.command("interactive")
def interactive_install(
    env_name: Annotated[str, typer.Option("--env", help="Conda environment name to create/update.")] = "ctf",
    toolbox: Annotated[list[str], typer.Option("--toolbox", help="Prefill one or more toolboxes in the TUI. Repeatable.")] = [],
    tool: Annotated[list[str], typer.Option("--tool", help="Prefill one or more tools in the TUI. Repeatable.")] = [],
    profile: Annotated[str | None, typer.Option("--profile", help="Prefill the profile in the TUI: headless or full.")] = None,
    all_toolboxes: Annotated[bool, typer.Option("--all-toolboxes", help="Prefill all OpenCROW toolboxes in the TUI.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print commands without executing them.")] = False,
) -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise typer.BadParameter("The interactive installer requires a TTY.")
    run_install_flow(
        mode="interactive",
        env_name=env_name,
        toolbox=toolbox,
        tool=tool,
        profile=profile,
        all_toolboxes=all_toolboxes,
        dry_run=dry_run,
    )


@app.command("headless-install")
def headless_install(
    env_name: Annotated[str, typer.Option("--env", help="Conda environment name to create/update.")] = "ctf",
    toolbox: Annotated[list[str], typer.Option("--toolbox", help="Select a toolbox. Repeatable.")] = [],
    tool: Annotated[list[str], typer.Option("--tool", help="Select an individual tool. Repeatable.")] = [],
    profile: Annotated[str | None, typer.Option("--profile", help="Install profile: headless or full.")] = None,
    all_toolboxes: Annotated[bool, typer.Option("--all-toolboxes", help="Select all OpenCROW toolboxes explicitly.")] = False,
    replace_selection: Annotated[
        bool,
        typer.Option("--replace-selection", help="Replace the saved managed selection instead of merging into it."),
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print commands without executing them.")] = False,
) -> None:
    run_install_flow(
        mode="headless-install",
        env_name=env_name,
        toolbox=toolbox,
        tool=tool,
        profile=profile,
        all_toolboxes=all_toolboxes,
        replace_selection=replace_selection,
        dry_run=dry_run,
    )


@app.command("headless-update")
def headless_update(
    env_name: Annotated[str, typer.Option("--env", help="Conda environment name to create/update.")] = "ctf",
    toolbox: Annotated[list[str], typer.Option("--toolbox", help="Select a toolbox. Repeatable.")] = [],
    tool: Annotated[list[str], typer.Option("--tool", help="Select an individual tool. Repeatable.")] = [],
    profile: Annotated[str | None, typer.Option("--profile", help="Install profile: headless or full.")] = None,
    all_toolboxes: Annotated[bool, typer.Option("--all-toolboxes", help="Select all OpenCROW toolboxes explicitly.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print commands without executing them.")] = False,
) -> None:
    run_install_flow(
        mode="headless-update",
        env_name=env_name,
        toolbox=toolbox,
        tool=tool,
        profile=profile,
        all_toolboxes=all_toolboxes,
        dry_run=dry_run,
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
