#!/usr/bin/env python3
"""Manage asynchronous SSH sessions for agent workflows.

Commands:
  start   - create and start a background SSH session daemon
  send    - send data to a running session
  read    - read captured output from a session
  status  - show running state and metadata
  stop    - terminate a session daemon
"""

from __future__ import annotations

import argparse
import json
import os
import pty
import selectors
import shutil
import signal
import subprocess
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BASE_DIR = Path("/tmp/codex-ssh-async")
ENCODING = "utf-8"
DAEMON_CHILD: Optional[subprocess.Popen[bytes]] = None


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
    path = paths_for(name)["meta"]
    if not path.exists():
        raise SessionError(f"Session '{name}' has no metadata. Did you start it?")
    return json.loads(path.read_text(encoding=ENCODING))


def read_pid(name: str) -> Optional[int]:
    path = paths_for(name)["pid"]
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding=ENCODING).strip())
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


def build_ssh_command(meta: dict) -> list[str]:
    dest = f"{meta['user']}@{meta['host']}" if meta.get("user") else meta["host"]
    cmd = [
        meta.get("ssh_bin", "ssh"),
        "-tt",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
    ]
    if meta.get("port"):
        cmd.extend(["-p", str(meta["port"])])
    if meta.get("identity"):
        cmd.extend(["-i", meta["identity"]])
    for option in meta.get("options", []):
        cmd.extend(["-o", option])
    cmd.append(dest)
    if meta.get("remote_command"):
        cmd.append(meta["remote_command"])
    return cmd


def terminate_child(proc: Optional[subprocess.Popen[bytes]], timeout: float = 5.0) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    proc.kill()
    proc.wait(timeout=5)


def daemon_loop(name: str) -> int:
    global DAEMON_CHILD

    meta = load_meta(name)
    paths = paths_for(name)
    ssh_cmd = build_ssh_command(meta)
    master_fd, slave_fd = pty.openpty()

    try:
        proc = subprocess.Popen(
            ssh_cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        os.close(slave_fd)

    DAEMON_CHILD = proc
    os.set_blocking(master_fd, False)
    fifo_fd = open_fifo_reader(paths["fifo"])
    sel = selectors.DefaultSelector()
    sel.register(master_fd, selectors.EVENT_READ, "pty")
    sel.register(fifo_fd, selectors.EVENT_READ, "fifo")

    with paths["rx_raw"].open("ab") as raw_fh:
        write_io_line(paths["io_log"], "[STATE]", f"spawned pid={proc.pid}".encode())
        while True:
            if proc.poll() is not None:
                write_io_line(
                    paths["io_log"],
                    "[STATE]",
                    f"ssh exited rc={proc.returncode}".encode(),
                )
                return int(proc.returncode or 0)

            for key, _ in sel.select(timeout=0.5):
                if key.data == "pty":
                    try:
                        data = os.read(master_fd, 65536)
                    except BlockingIOError:
                        continue
                    except OSError:
                        data = b""

                    if not data:
                        rc = proc.wait(timeout=5)
                        write_io_line(paths["io_log"], "[STATE]", f"pty closed rc={rc}".encode())
                        return int(rc or 0)

                    raw_fh.write(data)
                    raw_fh.flush()
                    write_io_line(paths["io_log"], "[RX]", data)
                else:
                    try:
                        tx = os.read(fifo_fd, 65536)
                    except BlockingIOError:
                        continue

                    if not tx:
                        sel.unregister(fifo_fd)
                        os.close(fifo_fd)
                        fifo_fd = open_fifo_reader(paths["fifo"])
                        sel.register(fifo_fd, selectors.EVENT_READ, "fifo")
                        continue

                    os.write(master_fd, tx)
                    write_io_line(paths["io_log"], "[TX]", tx)
    return 0


def handle_signal(signum, _frame):
    raise KeyboardInterrupt(f"received signal {signum}")


def cmd_start(args: argparse.Namespace) -> int:
    if shutil.which(args.ssh_bin) is None:
        raise SessionError(f"SSH client '{args.ssh_bin}' is not available.")

    name = args.name
    paths = paths_for(name)
    ensure_stopped(name)

    paths["root"].mkdir(parents=True, exist_ok=True)
    paths["io_log"].write_text("", encoding=ENCODING)
    paths["daemon_log"].write_text("", encoding=ENCODING)
    paths["rx_raw"].write_bytes(b"")
    paths["pid"].unlink(missing_ok=True)
    if paths["fifo"].exists():
        paths["fifo"].unlink()
    os.mkfifo(paths["fifo"], mode=0o600)

    meta = {
        "name": name,
        "host": args.host,
        "user": args.user,
        "port": args.port,
        "identity": os.path.expanduser(args.identity) if args.identity else None,
        "options": args.option or [],
        "remote_command": args.remote_command,
        "ssh_bin": args.ssh_bin,
        "started_at": now_iso(),
    }
    write_meta(name, meta)

    with paths["daemon_log"].open("a", encoding=ENCODING) as daemon_log:
        proc = subprocess.Popen(
            [sys.executable, __file__, "_daemon", "--name", name],
            stdin=subprocess.DEVNULL,
            stdout=daemon_log,
            stderr=daemon_log,
            start_new_session=True,
        )

    paths["pid"].write_text(f"{proc.pid}\n", encoding=ENCODING)

    for _ in range(50):
        if proc.poll() is not None:
            raise SessionError("daemon exited during startup; check daemon.log")
        if is_alive(proc.pid):
            print(f"started session '{name}' pid={proc.pid} {meta['host']}")
            return 0
        time.sleep(0.1)

    raise SessionError("daemon failed to start; check daemon.log")


def cmd_send(args: argparse.Namespace) -> int:
    name = args.name
    paths = paths_for(name)
    pid = read_pid(name)
    if not is_alive(pid):
        raise SessionError(f"Session '{name}' is not running.")

    payload = args.data.encode(ENCODING)
    if args.newline:
        payload += b"\n"

    deadline = time.time() + args.timeout
    while True:
        try:
            fd = os.open(paths["fifo"], os.O_WRONLY | os.O_NONBLOCK)
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
    paths = paths_for(args.name)
    if not paths["io_log"].exists():
        raise SessionError(f"Session '{args.name}' has no logs yet.")

    if args.tail:
        sys.stdout.write(tail_file(paths["io_log"], args.tail))
    else:
        with paths["io_log"].open("r", encoding=ENCODING, errors="replace") as fh:
            sys.stdout.write(fh.read())

    if args.follow:
        with paths["io_log"].open("r", encoding=ENCODING, errors="replace") as fh:
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
    paths = paths_for(name)
    pid = read_pid(name)
    running = is_alive(pid)

    if not paths["meta"].exists():
        raise SessionError(f"Session '{name}' does not exist.")

    meta = load_meta(name)
    result = {
        "name": name,
        "running": running,
        "pid": pid,
        "host": meta.get("host"),
        "user": meta.get("user"),
        "port": meta.get("port"),
        "identity": meta.get("identity"),
        "remote_command": meta.get("remote_command"),
        "started_at": meta.get("started_at"),
        "paths": {k: str(v) for k, v in paths.items()},
    }
    print(json.dumps(result, indent=2))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    name = args.name
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
        if DAEMON_CHILD is not None:
            terminate_child(DAEMON_CHILD)
        print(f"daemon interrupted: {exc}", file=sys.stderr)
        return 130
    except Exception as exc:  # pragma: no cover - operational logging path
        if DAEMON_CHILD is not None:
            terminate_child(DAEMON_CHILD)
        print(f"daemon error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    start = sub.add_parser("start", help="start an SSH session daemon")
    start.add_argument("--name", required=True)
    start.add_argument("--host", required=True)
    start.add_argument("--user")
    start.add_argument("--port", type=int, default=22)
    start.add_argument("--identity")
    start.add_argument("--option", action="append", help="repeatable ssh -o option")
    start.add_argument("--remote-command", help="optional remote command instead of a login shell")
    start.add_argument("--ssh-bin", default="ssh")
    start.set_defaults(func=cmd_start)

    send = sub.add_parser("send", help="send data to a running session")
    send.add_argument("--name", required=True)
    send.add_argument("--data", required=True)
    send.add_argument("--newline", action="store_true")
    send.add_argument("--timeout", type=float, default=3.0)
    send.set_defaults(func=cmd_send)

    read = sub.add_parser("read", help="read session logs")
    read.add_argument("--name", required=True)
    read.add_argument("--tail", type=int)
    read.add_argument("--follow", action="store_true")
    read.set_defaults(func=cmd_read)

    status = sub.add_parser("status", help="show session status")
    status.add_argument("--name", required=True)
    status.set_defaults(func=cmd_status)

    stop = sub.add_parser("stop", help="stop a running session")
    stop.add_argument("--name", required=True)
    stop.add_argument("--timeout", type=float, default=5.0)
    stop.set_defaults(func=cmd_stop)

    daemon = sub.add_parser("_daemon", help=argparse.SUPPRESS)
    daemon.add_argument("--name", required=True)
    daemon.set_defaults(func=cmd_daemon)

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except SessionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
