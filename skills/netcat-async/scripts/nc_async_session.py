#!/usr/bin/env python3
"""Manage asynchronous netcat-like TCP sessions for agent workflows.

Commands:
  start   - create and start a background session daemon
  send    - send data to a running session
  read    - read captured output from a session
  status  - show running state and metadata
  stop    - terminate a session daemon
"""

from __future__ import annotations

import argparse
import json
import os
import selectors
import signal
import socket
import subprocess
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BASE_DIR = Path("/tmp/codex-nc-async")
ENCODING = "utf-8"


class SessionError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_session_name(name: str) -> str:
    normalized = str(name).strip()
    if not normalized:
        raise SessionError("Session name is required.")
    if normalized in {".", ".."} or "/" in normalized or "\\" in normalized:
        raise SessionError(
            "Session name must be a single non-empty path segment without '/' or '\\' and cannot be '.' or '..'."
        )
    return normalized


def session_dir(name: str) -> Path:
    return BASE_DIR / validate_session_name(name)


def paths_for(name: str) -> dict[str, Path]:
    root = session_dir(name)
    return {
        "root": root,
        "pid": root / "pid",
        "meta": root / "meta.json",
        "fifo": root / "tx.fifo",
        "io_log": root / "io.log",
        "rx_raw": root / "rx.raw",
        "daemon_log": root / "daemon.log",
    }


def load_meta(name: str) -> dict:
    p = paths_for(name)["meta"]
    if not p.exists():
        raise SessionError(f"Session '{name}' has no metadata. Did you start it?")
    return json.loads(p.read_text(encoding=ENCODING))


def read_pid(name: str) -> Optional[int]:
    p = paths_for(name)["pid"]
    if not p.exists():
        return None
    try:
        return int(p.read_text(encoding=ENCODING).strip())
    except ValueError:
        return None


def is_alive(pid: Optional[int]) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def ensure_stopped(name: str) -> None:
    pid = read_pid(name)
    if is_alive(pid):
        raise SessionError(f"Session '{name}' is already running with PID {pid}.")


def write_meta(name: str, data: dict) -> None:
    paths_for(name)["meta"].write_text(
        json.dumps(data, indent=2, sort_keys=True), encoding=ENCODING
    )


def write_io_line(path: Path, direction: str, payload: bytes) -> None:
    ts = now_iso()
    text = payload.decode(ENCODING, errors="replace")
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    with path.open("a", encoding=ENCODING) as fh:
        fh.write(f"[{ts}] {direction} {text}\n")


def open_fifo_reader(path: Path) -> int:
    return os.open(path, os.O_RDONLY | os.O_NONBLOCK)


def daemon_loop(name: str) -> int:
    p = paths_for(name)
    meta = load_meta(name)
    host = meta["host"]
    port = int(meta["port"])
    timeout = float(meta.get("connect_timeout", 10))

    sock = socket.create_connection((host, port), timeout=timeout)
    sock.setblocking(False)

    fifo_fd = open_fifo_reader(p["fifo"])
    sel = selectors.DefaultSelector()
    sel.register(sock, selectors.EVENT_READ, "sock")
    sel.register(fifo_fd, selectors.EVENT_READ, "fifo")

    with p["rx_raw"].open("ab") as raw_fh:
        write_io_line(p["io_log"], "[STATE]", b"connected")
        while True:
            for key, _ in sel.select(timeout=0.5):
                if key.data == "sock":
                    try:
                        data = sock.recv(65536)
                    except BlockingIOError:
                        continue

                    if not data:
                        write_io_line(p["io_log"], "[STATE]", b"remote closed")
                        return 0

                    raw_fh.write(data)
                    raw_fh.flush()
                    write_io_line(p["io_log"], "[RX]", data)
                else:
                    try:
                        tx = os.read(fifo_fd, 65536)
                    except BlockingIOError:
                        continue

                    if not tx:
                        sel.unregister(fifo_fd)
                        os.close(fifo_fd)
                        fifo_fd = open_fifo_reader(p["fifo"])
                        sel.register(fifo_fd, selectors.EVENT_READ, "fifo")
                        continue

                    sock.sendall(tx)
                    write_io_line(p["io_log"], "[TX]", tx)


def handle_signal(signum, _frame):
    raise KeyboardInterrupt(f"received signal {signum}")


def cmd_start(args: argparse.Namespace) -> int:
    name = args.name
    p = paths_for(name)
    ensure_stopped(name)

    p["root"].mkdir(parents=True, exist_ok=True)
    # Reset logs/state so restarts with the same name do not mix old/new runs.
    p["io_log"].write_text("", encoding=ENCODING)
    p["daemon_log"].write_text("", encoding=ENCODING)
    p["rx_raw"].write_bytes(b"")
    p["pid"].unlink(missing_ok=True)
    if p["fifo"].exists():
        p["fifo"].unlink()
    os.mkfifo(p["fifo"], mode=0o600)

    meta = {
        "name": name,
        "host": args.host,
        "port": args.port,
        "connect_timeout": args.connect_timeout,
        "started_at": now_iso(),
    }
    write_meta(name, meta)

    with p["daemon_log"].open("a", encoding=ENCODING) as daemon_log:
        proc = subprocess.Popen(
            [sys.executable, __file__, "_daemon", "--name", name],
            stdin=subprocess.DEVNULL,
            stdout=daemon_log,
            stderr=daemon_log,
            start_new_session=True,
        )

    p["pid"].write_text(f"{proc.pid}\n", encoding=ENCODING)

    for _ in range(50):
        if proc.poll() is not None:
            raise SessionError("daemon exited during startup; check daemon.log")
        if is_alive(proc.pid):
            print(f"started session '{name}' pid={proc.pid} {args.host}:{args.port}")
            return 0
        time.sleep(0.1)

    raise SessionError("daemon failed to start; check daemon.log")


def cmd_send(args: argparse.Namespace) -> int:
    name = args.name
    p = paths_for(name)
    pid = read_pid(name)
    if not is_alive(pid):
        raise SessionError(f"Session '{name}' is not running.")

    payload = args.data.encode(ENCODING)
    if args.newline:
        payload += b"\n"

    deadline = time.time() + args.timeout
    while True:
        try:
            fd = os.open(p["fifo"], os.O_WRONLY | os.O_NONBLOCK)
            try:
                os.write(fd, payload)
            finally:
                os.close(fd)
            break
        except OSError:
            if time.time() >= deadline:
                raise SessionError(
                    f"Timeout writing to '{name}' FIFO. Session may be unhealthy."
                )
            time.sleep(0.05)

    print(f"sent {len(payload)} bytes to '{name}'")
    return 0


def tail_file(path: Path, lines: int) -> str:
    dq: deque[str] = deque(maxlen=lines)
    with path.open("r", encoding=ENCODING, errors="replace") as fh:
        for line in fh:
            dq.append(line)
    return "".join(dq)


def cmd_read(args: argparse.Namespace) -> int:
    p = paths_for(args.name)
    if not p["io_log"].exists():
        raise SessionError(f"Session '{args.name}' has no logs yet.")

    if args.tail:
        sys.stdout.write(tail_file(p["io_log"], args.tail))
    else:
        with p["io_log"].open("r", encoding=ENCODING, errors="replace") as fh:
            sys.stdout.write(fh.read())

    if args.follow:
        with p["io_log"].open("r", encoding=ENCODING, errors="replace") as fh:
            fh.seek(0, os.SEEK_END)
            while True:
                line = fh.readline()
                if line:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    continue
                pid = read_pid(args.name)
                if not is_alive(pid):
                    return 0
                time.sleep(0.2)

    return 0


def cmd_status(args: argparse.Namespace) -> int:
    name = args.name
    p = paths_for(name)
    pid = read_pid(name)
    running = is_alive(pid)

    if not p["meta"].exists():
        raise SessionError(f"Session '{name}' does not exist.")

    meta = load_meta(name)
    result = {
        "name": name,
        "running": running,
        "pid": pid,
        "host": meta.get("host"),
        "port": meta.get("port"),
        "started_at": meta.get("started_at"),
        "paths": {k: str(v) for k, v in p.items()},
    }
    print(json.dumps(result, indent=2))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    name = args.name
    p = paths_for(name)
    pid = read_pid(name)
    if not is_alive(pid):
        print(f"session '{name}' already stopped")
        return 0

    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        if not is_alive(pid):
            print(f"stopped session '{name}'")
            return 0
        time.sleep(0.1)

    os.kill(pid, signal.SIGKILL)
    print(f"force-stopped session '{name}'")
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    try:
        return daemon_loop(args.name)
    except KeyboardInterrupt as exc:
        p = paths_for(args.name)
        write_io_line(p["io_log"], "[STATE]", str(exc).encode(ENCODING))
        return 0
    except Exception as exc:  # noqa: BLE001
        p = paths_for(args.name)
        write_io_line(p["io_log"], "[ERROR]", str(exc).encode(ENCODING))
        raise
    finally:
        p = paths_for(args.name)
        try:
            p["pid"].unlink(missing_ok=True)
        except OSError:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Async netcat session manager")
    sub = parser.add_subparsers(dest="cmd", required=True)

    start = sub.add_parser("start", help="Start a background session")
    start.add_argument("--name", required=True)
    start.add_argument("--host", required=True)
    start.add_argument("--port", type=int, required=True)
    start.add_argument("--connect-timeout", type=float, default=10.0)
    start.set_defaults(func=cmd_start)

    send = sub.add_parser("send", help="Send text to a session")
    send.add_argument("--name", required=True)
    send.add_argument("--data", required=True)
    send.add_argument("--newline", action="store_true")
    send.add_argument("--timeout", type=float, default=2.0)
    send.set_defaults(func=cmd_send)

    read = sub.add_parser("read", help="Read session logs")
    read.add_argument("--name", required=True)
    read.add_argument("--tail", type=int)
    read.add_argument("--follow", action="store_true")
    read.set_defaults(func=cmd_read)

    status = sub.add_parser("status", help="Show session status")
    status.add_argument("--name", required=True)
    status.set_defaults(func=cmd_status)

    stop = sub.add_parser("stop", help="Stop a session")
    stop.add_argument("--name", required=True)
    stop.add_argument("--timeout", type=float, default=3.0)
    stop.set_defaults(func=cmd_stop)

    daemon = sub.add_parser("_daemon", help=argparse.SUPPRESS)
    daemon.add_argument("--name", required=True)
    daemon.set_defaults(func=cmd_daemon)

    return parser


def main() -> int:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except SessionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
