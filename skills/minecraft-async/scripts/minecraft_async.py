#!/usr/bin/env python3
"""Manage a local Minecraft Java client for async agent workflows."""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import os
import platform
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid
import zipfile
from collections import deque
from pathlib import Path
from typing import Any

try:
    from Xlib import X, XK, display, protocol
    from Xlib.ext import xtest
except ImportError:  # pragma: no cover - handled at runtime
    X = XK = display = protocol = xtest = None

BASE_DIR = Path("/tmp/codex-minecraft-async")
DEFAULT_GAME_DIR = Path.home() / ".minecraft"
ENCODING = "utf-8"


class McError(RuntimeError):
    pass


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def validate_session_name(name: str) -> str:
    normalized = str(name).strip()
    if not normalized:
        raise McError("Session name is required.")
    if normalized in {".", ".."} or "/" in normalized or "\\" in normalized:
        raise McError(
            "Session name must be a single non-empty path segment without '/' or '\\' and cannot be '.' or '..'."
        )
    return normalized


def session_paths(name: str) -> dict[str, Path]:
    root = BASE_DIR / validate_session_name(name)
    return {
        "root": root,
        "pid": root / "pid",
        "meta": root / "meta.json",
        "launcher_log": root / "launcher.log",
        "natives": root / "natives",
        "quick_play": root / "quick_play.json",
    }


def read_pid(name: str) -> int | None:
    path = session_paths(name)["pid"]
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding=ENCODING).strip())
    except ValueError:
        return None


def is_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def load_meta(name: str) -> dict[str, Any]:
    path = session_paths(name)["meta"]
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding=ENCODING))


def save_meta(name: str, meta: dict[str, Any]) -> None:
    path = session_paths(name)["meta"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding=ENCODING)


def detect_game_dir(raw: str | None) -> Path:
    if raw:
        return Path(raw).expanduser().resolve()
    return DEFAULT_GAME_DIR


def detect_java(game_dir: Path, version_meta: dict[str, Any], raw_java: str | None) -> str:
    if raw_java:
        return str(Path(raw_java).expanduser())

    component = (
        version_meta.get("javaVersion", {}).get("component")
        or version_meta.get("javaVersion", {}).get("componentName")
    )
    candidates: list[Path] = []
    if component:
        candidates.extend(
            [
                game_dir / "runtime" / component / "linux" / component / "bin" / "java",
                game_dir / "runtime" / component / "linux-x64" / component / "bin" / "java",
            ]
        )

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    java_from_path = shutil_which("java")
    if java_from_path:
        return java_from_path

    raise McError("No Java runtime found. Set --java explicitly.")


def shutil_which(binary: str) -> str | None:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        candidate = Path(directory) / binary
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def offline_uuid(username: str) -> str:
    raw = bytearray(hashlib.md5(f"OfflinePlayer:{username}".encode(ENCODING)).digest())
    raw[6] = (raw[6] & 0x0F) | 0x30
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


def os_name() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "osx"
    if os.name == "nt":
        return "windows"
    return platform.system().lower()


def os_arch() -> str:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "x64"
    if machine in {"x86", "i386", "i686"}:
        return "x86"
    return machine


def rule_matches(rule: dict[str, Any], features: dict[str, bool]) -> bool:
    rule_os = rule.get("os")
    if rule_os:
        name = rule_os.get("name")
        if name and name != os_name():
            return False
        arch = rule_os.get("arch")
        if arch and arch != os_arch():
            return False
        version_pattern = rule_os.get("version")
        if version_pattern and not re.search(version_pattern, platform.release()):
            return False

    feature_rules = rule.get("features", {})
    for key, expected in feature_rules.items():
        if bool(features.get(key, False)) != bool(expected):
            return False

    return True


def rules_allow(rules: list[dict[str, Any]] | None, features: dict[str, bool]) -> bool:
    if not rules:
        return True

    allowed = False
    for rule in rules:
        if rule_matches(rule, features):
            allowed = rule.get("action", "allow") == "allow"
    return allowed


def merge_version(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(parent)
    for key, value in child.items():
        if key == "libraries":
            merged.setdefault("libraries", [])
            merged["libraries"].extend(copy.deepcopy(value))
        elif key == "arguments":
            merged.setdefault("arguments", {})
            for arg_key, arg_value in value.items():
                merged["arguments"].setdefault(arg_key, [])
                merged["arguments"][arg_key].extend(copy.deepcopy(arg_value))
        elif key in {"downloads", "logging", "javaVersion", "assetIndex"}:
            base = merged.setdefault(key, {})
            base.update(copy.deepcopy(value))
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def load_version_meta(game_dir: Path, version_id: str) -> dict[str, Any]:
    path = game_dir / "versions" / version_id / f"{version_id}.json"
    if not path.exists():
        raise McError(f"Version metadata not found: {path}")
    meta = json.loads(path.read_text(encoding=ENCODING))
    parent_id = meta.get("inheritsFrom")
    if not parent_id:
        return meta
    return merge_version(load_version_meta(game_dir, parent_id), meta)


def library_path(game_dir: Path, library: dict[str, Any]) -> Path | None:
    downloads = library.get("downloads", {})
    artifact = downloads.get("artifact")
    if artifact and artifact.get("path"):
        return game_dir / "libraries" / artifact["path"]
    return None


def iter_allowed_libraries(game_dir: Path, version_meta: dict[str, Any], features: dict[str, bool]) -> list[tuple[dict[str, Any], Path]]:
    result: list[tuple[dict[str, Any], Path]] = []
    arch = platform.machine().lower()
    for library in version_meta.get("libraries", []):
        if not rules_allow(library.get("rules"), features):
            continue
        path = library_path(game_dir, library)
        if path and path.exists():
            lower = path.name.lower()
            if "linux-aarch_64" in lower and arch not in {"aarch64", "arm64"}:
                continue
            if "linux-x86_64" in lower and arch not in {"x86_64", "amd64"}:
                continue
            result.append((library, path))
    return result


def extract_natives(paths: dict[str, Path], libraries: list[tuple[dict[str, Any], Path]]) -> Path:
    natives_dir = paths["natives"]
    natives_dir.mkdir(parents=True, exist_ok=True)
    for existing in natives_dir.iterdir():
        if existing.is_file() or existing.is_symlink():
            existing.unlink(missing_ok=True)
    for _library, path in libraries:
        lower = path.name.lower()
        if "natives" not in lower:
            continue
        with zipfile.ZipFile(path) as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                name = member.filename
                if (
                    name.startswith("META-INF/")
                    or name.endswith(".git")
                    or not name.endswith((".so", ".dll", ".dylib"))
                ):
                    continue
                target = natives_dir / Path(name).name
                target.write_bytes(zf.read(member))
    return natives_dir


def substitute(text: str, variables: dict[str, Any]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        return str(variables.get(key, ""))

    return re.sub(r"\$\{([^}]+)\}", repl, text)


def flatten_argument_values(values: list[Any], variables: dict[str, Any], features: dict[str, bool]) -> list[str]:
    result: list[str] = []
    for entry in values:
        if isinstance(entry, str):
            result.append(substitute(entry, variables))
            continue
        if not isinstance(entry, dict):
            continue
        if not rules_allow(entry.get("rules"), features):
            continue
        raw_value = entry.get("value", [])
        if isinstance(raw_value, str):
            raw_items = [raw_value]
        else:
            raw_items = raw_value
        for item in raw_items:
            result.append(substitute(str(item), variables))
    return prune_empty_option_pairs(result)


def prune_empty_option_pairs(items: list[str]) -> list[str]:
    cleaned: list[str] = []
    i = 0
    while i < len(items):
        item = items[i]
        nxt = items[i + 1] if i + 1 < len(items) else None
        if item.startswith("--") and nxt == "":
            i += 2
            continue
        if item != "":
            cleaned.append(item)
        i += 1
    return cleaned


def build_direct_command(
    game_dir: Path,
    version_id: str,
    username: str,
    session: str,
    width: int | None,
    height: int | None,
    java_override: str | None,
    min_memory: int,
    max_memory: int,
    server: str | None,
    world: str | None,
) -> tuple[list[str], dict[str, Any]]:
    version_meta = load_version_meta(game_dir, version_id)
    paths = session_paths(session)
    features = {
        "has_custom_resolution": bool(width and height),
        "has_quick_plays_support": bool(server or world),
        "is_quick_play_multiplayer": bool(server),
        "is_quick_play_singleplayer": bool(world),
    }
    libraries = iter_allowed_libraries(game_dir, version_meta, features)
    natives_dir = extract_natives(paths, libraries)

    classpath_items = [str(path) for _library, path in libraries]
    client_jar = game_dir / "versions" / version_id / f"{version_id}.jar"
    if not client_jar.exists():
        raise McError(f"Client JAR not found: {client_jar}")
    classpath_items.append(str(client_jar))

    quick_play_path = paths["quick_play"]
    quick_play_path.parent.mkdir(parents=True, exist_ok=True)
    if server or world:
        quick_play_path.write_text("{}", encoding=ENCODING)

    java_bin = detect_java(game_dir, version_meta, java_override)
    vars_map: dict[str, Any] = {
        "auth_player_name": username,
        "version_name": version_id,
        "game_directory": str(game_dir),
        "assets_root": str(game_dir / "assets"),
        "assets_index_name": version_meta.get("assets", ""),
        "auth_uuid": offline_uuid(username),
        "auth_access_token": "0",
        "auth_xuid": "",
        "clientid": str(uuid.uuid4()),
        "user_type": "legacy",
        "version_type": version_meta.get("type", "release"),
        "natives_directory": str(natives_dir),
        "launcher_name": "minecraft-async",
        "launcher_version": "1",
        "classpath": os.pathsep.join(classpath_items),
        "resolution_width": width or "",
        "resolution_height": height or "",
        "quickPlayPath": str(quick_play_path),
        "quickPlayMultiplayer": server or "",
        "quickPlaySingleplayer": world or "",
    }

    jvm_args = flatten_argument_values(version_meta.get("arguments", {}).get("jvm", []), vars_map, features)
    game_args = flatten_argument_values(version_meta.get("arguments", {}).get("game", []), vars_map, features)

    logging_cfg = version_meta.get("logging", {}).get("client", {})
    logging_file = logging_cfg.get("file", {}).get("id")
    if logging_file:
        candidate = game_dir / "assets" / "log_configs" / logging_file
        if candidate.exists():
            jvm_args.append(substitute(logging_cfg.get("argument", ""), {"path": str(candidate)}))

    cmd = [
        java_bin,
        f"-Xms{min_memory}M",
        f"-Xmx{max_memory}M",
        *jvm_args,
        version_meta["mainClass"],
        *game_args,
    ]

    meta = {
        "backend": "direct",
        "game_dir": str(game_dir),
        "java": java_bin,
        "username": username,
        "version": version_id,
        "server": server,
        "world": world,
        "started_at": now_iso(),
        "command": cmd,
    }
    return cmd, meta


def build_cmd_launcher_command(instance: str, username: str | None) -> list[str]:
    binary = shutil_which("cmd-launcher")
    if not binary:
        raise McError("cmd-launcher is not installed or not on PATH.")
    cmd = [binary, "start", instance]
    if username:
        cmd.extend(["--username", username])
    return cmd


def start_session(name: str, cmd: list[str], meta: dict[str, Any], dry_run: bool) -> int:
    paths = session_paths(name)
    pid = read_pid(name)
    if is_alive(pid):
        raise McError(f"Session '{name}' is already running with PID {pid}.")

    paths["root"].mkdir(parents=True, exist_ok=True)
    paths["launcher_log"].write_text("", encoding=ENCODING)
    if dry_run:
        print(" ".join(shlex.quote(arg) for arg in cmd))
        return 0

    with paths["launcher_log"].open("a", encoding=ENCODING) as log_fh:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=log_fh,
            start_new_session=True,
        )

    paths["pid"].write_text(f"{proc.pid}\n", encoding=ENCODING)
    save_meta(name, meta | {"pid": proc.pid, "session": name})
    print(f"started session '{name}' pid={proc.pid}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    game_dir = detect_game_dir(args.game_dir)
    versions_dir = game_dir / "versions"
    saves_dir = game_dir / "saves"
    versions = sorted(
        [path.name for path in versions_dir.iterdir() if path.is_dir()],
        key=str.lower,
    ) if versions_dir.exists() else []
    saves = sorted(
        [path.name for path in saves_dir.iterdir() if path.is_dir()],
        key=str.lower,
    ) if saves_dir.exists() else []
    pid = read_pid(args.session)
    meta = load_meta(args.session)
    window = find_minecraft_window()
    info = {
        "game_dir": str(game_dir),
        "display": os.environ.get("DISPLAY", ""),
        "cmd_launcher": bool(shutil_which("cmd-launcher")),
        "x11": bool(os.environ.get("DISPLAY")) and Xlib_ready(),
        "session": args.session,
        "pid": pid,
        "running": is_alive(pid),
        "window": window,
        "versions": versions[:10],
        "saves": saves[:20],
        "latest_log": str(game_dir / "logs" / "latest.log"),
        "meta": meta,
    }
    if args.json:
        print(json.dumps(info, indent=2, sort_keys=True))
        return 0

    print(f"game_dir: {info['game_dir']}")
    print(f"display: {info['display'] or 'unset'}")
    print(f"x11_ready: {info['x11']}")
    print(f"cmd_launcher: {info['cmd_launcher']}")
    print(f"session: {args.session}")
    print(f"pid: {pid if pid is not None else 'none'}")
    print(f"running: {info['running']}")
    print(f"window: {window['title'] if window else 'not found'}")
    print("versions:")
    for version in info["versions"]:
        print(f"  - {version}")
    print("saves:")
    for save in info["saves"]:
        print(f"  - {save}")
    return 0


def cmd_launch(args: argparse.Namespace) -> int:
    game_dir = detect_game_dir(args.game_dir)
    backend = args.backend
    if backend == "auto":
        backend = "direct"

    if backend == "direct":
        cmd, meta = build_direct_command(
            game_dir=game_dir,
            version_id=args.version,
            username=args.username,
            session=args.session,
            width=args.width,
            height=args.height,
            java_override=args.java,
            min_memory=args.min_memory,
            max_memory=args.max_memory,
            server=args.server,
            world=getattr(args, "world", None),
        )
        return start_session(args.session, cmd, meta, args.dry_run)

    cmd = build_cmd_launcher_command(args.instance, args.username)
    meta = {
        "backend": "cmd-launcher",
        "instance": args.instance,
        "username": args.username,
        "started_at": now_iso(),
        "command": cmd,
    }
    return start_session(args.session, cmd, meta, args.dry_run)


def cmd_stop(args: argparse.Namespace) -> int:
    pid = read_pid(args.session)
    if not is_alive(pid):
        raise McError(f"Session '{args.session}' is not running.")
    os.killpg(pid, signal.SIGTERM)
    print(f"stopped session '{args.session}' pid={pid}")
    return 0


def Xlib_ready() -> bool:
    return bool(display and xtest and os.environ.get("DISPLAY"))


def get_display():
    if not Xlib_ready():
        raise McError("X11 control is unavailable. Ensure DISPLAY is set and python3-xlib is installed.")
    return display.Display()


def prop_text(disp, win, atom_name: str) -> str:
    atom = disp.intern_atom(atom_name)
    utf8 = disp.intern_atom("UTF8_STRING")
    for prop_type in (utf8, X.AnyPropertyType):
        try:
            prop = win.get_full_property(atom, prop_type)
        except Exception:
            prop = None
        if prop and prop.value is not None:
            value = prop.value
            if isinstance(value, bytes):
                return value.decode(ENCODING, errors="replace")
            if isinstance(value, str):
                return value
            if isinstance(value, (list, tuple)):
                return " ".join(str(item) for item in value)
    return ""


def find_minecraft_window() -> dict[str, Any] | None:
    if not Xlib_ready():
        return None
    try:
        disp = get_display()
    except McError:
        return None
    root = disp.screen().root
    client_list = root.get_full_property(disp.intern_atom("_NET_CLIENT_LIST"), X.AnyPropertyType)
    if not client_list:
        return None
    best: dict[str, Any] | None = None
    for wid in client_list.value:
        win = disp.create_resource_object("window", int(wid))
        try:
            geom = win.get_geometry()
        except Exception:
            continue
        title = prop_text(disp, win, "_NET_WM_NAME") or prop_text(disp, win, "WM_NAME")
        klass = prop_text(disp, win, "WM_CLASS")
        haystack = f"{title}\n{klass}".lower()
        if "minecraft" not in haystack and "glfw" not in haystack:
            continue
        if "launcher" in haystack and "glfw" not in haystack:
            continue
        candidate = {
            "id": int(wid),
            "title": title,
            "class": klass.replace("\x00", " ").strip(),
            "width": geom.width,
            "height": geom.height,
        }
        if "minecraft" in haystack:
            return candidate
        best = candidate
    return best


def activate_window(window_id: int) -> None:
    disp = get_display()
    root = disp.screen().root
    window = disp.create_resource_object("window", window_id)
    net_active = disp.intern_atom("_NET_ACTIVE_WINDOW")
    event = protocol.event.ClientMessage(
        window=window,
        client_type=net_active,
        data=(32, [1, X.CurrentTime, 0, 0, 0]),
    )
    root.send_event(event, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
    try:
        window.set_input_focus(X.RevertToPointerRoot, X.CurrentTime)
    except Exception:
        pass
    disp.sync()
    time.sleep(0.15)


SHIFT_CHARS = {
    "!": "1",
    "@": "2",
    "#": "3",
    "$": "4",
    "%": "5",
    "^": "6",
    "&": "7",
    "*": "8",
    "(": "9",
    ")": "0",
    "_": "-",
    "+": "=",
    "{": "[",
    "}": "]",
    "|": "\\",
    ":": ";",
    '"': "'",
    "<": ",",
    ">": ".",
    "?": "/",
    "~": "`",
}


def keysym_for_char(char: str) -> tuple[int, bool]:
    if char == "\n":
        return XK.string_to_keysym("Return"), False
    if char == "\t":
        return XK.string_to_keysym("Tab"), False
    if char == " ":
        return XK.string_to_keysym("space"), False
    if len(char) != 1:
        raise McError(f"Unsupported character: {char!r}")

    shift = False
    base = char
    if char.isupper():
        base = char.lower()
        shift = True
    elif char in SHIFT_CHARS:
        base = SHIFT_CHARS[char]
        shift = True

    keysym = XK.string_to_keysym(base)
    if keysym == 0:
        raise McError(f"Unsupported character for X11 typing: {char!r}")
    return keysym, shift


def press_key(keysym_name: str) -> None:
    disp = get_display()
    keysym = XK.string_to_keysym(keysym_name)
    if keysym == 0:
        raise McError(f"Unknown key: {keysym_name}")
    keycode = disp.keysym_to_keycode(keysym)
    if not keycode:
        raise McError(f"Unmapped key: {keysym_name}")
    xtest.fake_input(disp, X.KeyPress, keycode)
    xtest.fake_input(disp, X.KeyRelease, keycode)
    disp.sync()
    time.sleep(0.03)


def type_text(text: str) -> None:
    disp = get_display()
    shift_code = disp.keysym_to_keycode(XK.string_to_keysym("Shift_L"))
    for char in text:
        keysym, needs_shift = keysym_for_char(char)
        keycode = disp.keysym_to_keycode(keysym)
        if not keycode:
            raise McError(f"Unmapped key for character: {char!r}")
        if needs_shift:
            xtest.fake_input(disp, X.KeyPress, shift_code)
        xtest.fake_input(disp, X.KeyPress, keycode)
        xtest.fake_input(disp, X.KeyRelease, keycode)
        if needs_shift:
            xtest.fake_input(disp, X.KeyRelease, shift_code)
        disp.sync()
        time.sleep(0.01)


def require_window() -> dict[str, Any]:
    window = find_minecraft_window()
    if not window:
        raise McError("No Minecraft window found on the current X11 display.")
    activate_window(window["id"])
    return window


def cmd_focus(args: argparse.Namespace) -> int:
    window = require_window()
    print(f"focused window {window['id']} {window['title']}")
    return 0


def cmd_send_text(args: argparse.Namespace) -> int:
    require_window()
    type_text(args.text + ("\n" if args.newline else ""))
    print(f"typed {len(args.text)} characters")
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    require_window()
    press_key("t")
    time.sleep(0.05)
    type_text(args.text)
    press_key("Return")
    print("chat message sent")
    return 0


def cmd_command(args: argparse.Namespace) -> int:
    require_window()
    press_key("slash")
    time.sleep(0.05)
    type_text(args.text)
    press_key("Return")
    print("command sent")
    return 0


def cmd_screenshot(args: argparse.Namespace) -> int:
    window = require_window()
    import_bin = shutil_which("import")
    if not import_bin:
        raise McError("ImageMagick 'import' is not installed.")
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [import_bin, "-window", str(window["id"]), str(output)],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if not output.exists() or output.stat().st_size == 0:
        raise McError(f"Screenshot capture did not produce a usable file: {output}")
    print(str(output))
    return 0


def cmd_join_server(args: argparse.Namespace) -> int:
    if is_alive(read_pid(args.session)):
        raise McError(
            "A managed Minecraft session is already running. Stop it first to join a different server at launch time."
        )
    launch_args = argparse.Namespace(
        backend="direct",
        dry_run=args.dry_run,
        game_dir=args.game_dir,
        height=args.height,
        instance="",
        java=args.java,
        max_memory=args.max_memory,
        min_memory=args.min_memory,
        server=args.server,
        world=None,
        session=args.session,
        username=args.username,
        version=args.version,
        width=args.width,
    )
    return cmd_launch(launch_args)


def cmd_join_world(args: argparse.Namespace) -> int:
    if is_alive(read_pid(args.session)):
        raise McError(
            "A managed Minecraft session is already running. Stop it first to launch directly into a different world."
        )
    launch_args = argparse.Namespace(
        backend="direct",
        dry_run=args.dry_run,
        game_dir=args.game_dir,
        height=args.height,
        instance="",
        java=args.java,
        max_memory=args.max_memory,
        min_memory=args.min_memory,
        server=None,
        world=args.world,
        session=args.session,
        username=args.username,
        version=args.version,
        width=args.width,
    )
    return cmd_launch(launch_args)


def open_log_file(path: Path):
    if not path.exists():
        raise McError(f"Log file not found: {path}")
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding=ENCODING, errors="replace")
    return path.open("r", encoding=ENCODING, errors="replace")


def tail_text(path: Path, lines: int) -> str:
    with open_log_file(path) as fh:
        dq: deque[str] = deque(maxlen=lines)
        for line in fh:
            dq.append(line)
        return "".join(dq)


def resolve_logs(args: argparse.Namespace) -> list[tuple[str, Path]]:
    game_dir = detect_game_dir(args.game_dir)
    session_log = session_paths(args.session)["launcher_log"]
    latest = game_dir / "logs" / "latest.log"
    if args.which == "latest":
        return [("latest.log", latest)]
    if args.which == "launcher":
        return [("launcher.log", session_log)]
    return [("latest.log", latest), ("launcher.log", session_log)]


def cmd_read_log(args: argparse.Namespace) -> int:
    logs = resolve_logs(args)
    for index, (label, path) in enumerate(logs):
        print(f"== {label} ==")
        sys.stdout.write(tail_text(path, args.tail))
        if index != len(logs) - 1:
            print()
    if not args.follow:
        return 0

    target_label, target_path = logs[0]
    print(f"-- following {target_label} --")
    with open_log_file(target_path) as fh:
        fh.seek(0, os.SEEK_END)
        while True:
            line = fh.readline()
            if line:
                sys.stdout.write(line)
                sys.stdout.flush()
            else:
                time.sleep(0.2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status")
    p.add_argument("--session", default="default")
    p.add_argument("--game-dir")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("launch")
    p.add_argument("--session", default="default")
    p.add_argument("--game-dir")
    p.add_argument("--backend", choices=["auto", "direct", "cmd-launcher"], default="auto")
    p.add_argument("--username", default="Player")
    p.add_argument("--version", default="1.21.8")
    p.add_argument("--server")
    p.add_argument("--world")
    p.add_argument("--java")
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--min-memory", type=int, default=512)
    p.add_argument("--max-memory", type=int, default=4096)
    p.add_argument("--instance", default="default")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_launch)

    p = sub.add_parser("join-server")
    p.add_argument("--session", default="default")
    p.add_argument("--game-dir")
    p.add_argument("--username", default="Player")
    p.add_argument("--version", default="1.21.8")
    p.add_argument("--server", required=True)
    p.add_argument("--java")
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--min-memory", type=int, default=512)
    p.add_argument("--max-memory", type=int, default=4096)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_join_server)

    p = sub.add_parser("join-world")
    p.add_argument("--session", default="default")
    p.add_argument("--game-dir")
    p.add_argument("--username", default="Player")
    p.add_argument("--version", default="1.21.8")
    p.add_argument("--world", required=True)
    p.add_argument("--java")
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--min-memory", type=int, default=512)
    p.add_argument("--max-memory", type=int, default=4096)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_join_world)

    p = sub.add_parser("stop")
    p.add_argument("--session", default="default")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("focus")
    p.set_defaults(func=cmd_focus)

    p = sub.add_parser("send-text")
    p.add_argument("--text", required=True)
    p.add_argument("--newline", action="store_true")
    p.set_defaults(func=cmd_send_text)

    p = sub.add_parser("chat")
    p.add_argument("--text", required=True)
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("command")
    p.add_argument("--text", required=True)
    p.set_defaults(func=cmd_command)

    p = sub.add_parser("screenshot")
    p.add_argument("--output", required=True)
    p.set_defaults(func=cmd_screenshot)

    p = sub.add_parser("read-log")
    p.add_argument("--session", default="default")
    p.add_argument("--game-dir")
    p.add_argument("--which", choices=["latest", "launcher", "both"], default="both")
    p.add_argument("--tail", type=int, default=80)
    p.add_argument("--follow", action="store_true")
    p.set_defaults(func=cmd_read_log)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except McError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
