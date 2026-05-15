"""Host runtime service for dashboard-managed Codex agents."""

from __future__ import annotations

import argparse
from enum import Enum
import json
import os
import shutil
import socket
import stat
import tarfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import requests
import websocket

from .client import ConstellationAPIClient
from .config import ClientSettings, RuntimeSettings, load_runtime_settings


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return str(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)


class RuntimeSocket:
    def __init__(self, settings: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime_id = settings.runtime_id or f"{socket.gethostname()}-{os.getpid()}"
        self.display_name = settings.display_name or f"{socket.gethostname()} runtime"
        self.workspace_root = Path(settings.workspace_root).expanduser().resolve()
        self.client_settings = ClientSettings(
            api_base_url=settings.control_api_base_url,
            ws_base_url=settings.control_ws_base_url,
            token=settings.token,
            private_prompt=None,
            private_prompt_file=None,
            state_dir_name=".opencrow-runtime",
            request_timeout_sec=60,
            prompt_output_name="generated-prompt.md",
        )
        self.client = ConstellationAPIClient(self.client_settings)
        self.ws: websocket.WebSocketApp | None = None
        self.ws_lock = threading.Lock()
        self.active_turns: dict[str, Any] = {}
        self.codex_client: Any | None = None

    def run_forever(self) -> int:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        while True:
            self._connect_once()
            time.sleep(max(1, self.settings.reconnect_delay_sec))

    def _connect_once(self) -> None:
        ws_url = self.client.build_runtime_ws_url()
        self.ws = websocket.WebSocketApp(
            ws_url,
            header=self.client.build_ws_headers(),
            subprotocols=["opencrow.runtime.v1", *self.client.build_ws_subprotocols()[1:]],
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self.ws.run_forever(ping_interval=20, ping_timeout=10)

    def _send(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, default=_json_default)
        with self.ws_lock:
            if self.ws is not None:
                self.ws.send(data)

    def _on_open(self, _ws: websocket.WebSocketApp) -> None:
        self._send(
            {
                "action": "register",
                "runtime_id": self.runtime_id,
                "display_name": self.display_name,
                "workspace_root": str(self.workspace_root),
                "capabilities": {
                    "codex_sdk": self._codex_sdk_available(),
                    "interactive_attach": True,
                    "full_host_access": True,
                },
                "metadata": {"pid": os.getpid(), "hostname": socket.gethostname()},
            }
        )
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

    def _on_message(self, _ws: websocket.WebSocketApp, message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return
        if payload.get("event_type") != "command":
            return
        command = payload.get("command")
        if isinstance(command, dict):
            threading.Thread(target=self._handle_command, args=(command,), daemon=True).start()

    def _on_error(self, _ws: websocket.WebSocketApp, error: Any) -> None:
        print(f"[opencrow-runtime] websocket error: {error}", flush=True)

    def _on_close(self, _ws: websocket.WebSocketApp, code: int | None, reason: str | None) -> None:
        print(f"[opencrow-runtime] websocket closed: {code} {reason}", flush=True)

    def _heartbeat_loop(self) -> None:
        while self.ws is not None:
            time.sleep(15)
            try:
                self._send({"action": "heartbeat"})
            except Exception:
                return

    def _handle_command(self, command: dict[str, Any]) -> None:
        command_id = str(command["id"])
        command_type = str(command["command_type"])
        agent_id = str(command.get("agent_id") or "")
        try:
            self._send({"action": "command_status", "command_id": command_id, "status": "running"})
            if command_type == "spawn_agent":
                self._spawn_agent(command)
            elif command_type == "prompt_agent":
                self._prompt_agent(command)
            elif command_type == "interrupt_agent":
                self._interrupt_agent(agent_id)
            else:
                raise RuntimeError(f"Unsupported runtime command: {command_type}")
            self._send({"action": "command_status", "command_id": command_id, "status": "completed"})
        except Exception as exc:
            if agent_id:
                self._send(
                    {
                        "action": "agent_state",
                        "agent_id": agent_id,
                        "status": "failed",
                        "metadata": {"error": str(exc)},
                    }
                )
            self._send({"action": "command_status", "command_id": command_id, "status": "failed", "error": str(exc)})

    def _spawn_agent(self, command: dict[str, Any]) -> None:
        payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}
        challenge = payload.get("challenge") if isinstance(payload.get("challenge"), dict) else {}
        agent = payload.get("agent") if isinstance(payload.get("agent"), dict) else {}
        files = payload.get("files") if isinstance(payload.get("files"), list) else []
        agent_id = str(agent["id"])
        challenge_id = str(challenge["id"])
        workspace = self._workspace_for(challenge, agent)
        workspace.mkdir(parents=True, exist_ok=True)
        self._materialize_files(files, workspace)
        self._send(
            {
                "action": "agent_state",
                "agent_id": agent_id,
                "status": "starting",
                "workspace_path": str(workspace),
            }
        )
        self._run_codex_turn(
            challenge_id=challenge_id,
            agent_id=agent_id,
            prompt=str(agent.get("prompt") or ""),
            workspace=workspace,
            model=agent.get("model") or self.settings.codex_model,
            thread_id=agent.get("codex_thread_id"),
        )

    def _prompt_agent(self, command: dict[str, Any]) -> None:
        payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}
        agent_id = str(command["agent_id"])
        challenge_id = str(command["challenge_id"])
        agent_payload = self.client._json("GET", f"/agents/{agent_id}").get("agent", {})
        workspace_raw = str(agent_payload.get("workspace_path") or "")
        if not workspace_raw:
            raise RuntimeError(f"Agent {agent_id} has no runtime workspace yet.")
        self._run_codex_turn(
            challenge_id=challenge_id,
            agent_id=agent_id,
            prompt=str(payload.get("body") or ""),
            workspace=Path(workspace_raw),
            model=agent_payload.get("model") or self.settings.codex_model,
            thread_id=agent_payload.get("codex_thread_id"),
        )

    def _interrupt_agent(self, agent_id: str) -> None:
        turn = self.active_turns.get(agent_id)
        if turn is None:
            self._send(
                {
                    "action": "agent_event",
                    "agent_id": agent_id,
                    "challenge_id": "",
                    "event_type": "interrupt_noop",
                    "payload": {"message": "No active turn is registered for this agent."},
                }
            )
            return
        turn.interrupt()

    def _run_codex_turn(
        self,
        *,
        challenge_id: str,
        agent_id: str,
        prompt: str,
        workspace: Path,
        model: str | None,
        thread_id: str | None,
    ) -> None:
        self._send({"action": "agent_state", "agent_id": agent_id, "status": "running", "workspace_path": str(workspace)})
        try:
            from openai_codex import TextInput
        except Exception as exc:
            raise RuntimeError("The Python Codex SDK package `openai-codex` is not installed.") from exc

        codex = self._codex()
        try:
            if thread_id:
                try:
                    thread = codex.thread_resume(thread_id, cwd=str(workspace), model=model)
                except Exception:
                    thread = codex.thread_start(cwd=str(workspace), model=model)
                    thread_id = self._thread_id(thread)
                    if thread_id:
                        self._send({"action": "agent_state", "agent_id": agent_id, "codex_thread_id": str(thread_id)})
            else:
                thread = codex.thread_start(cwd=str(workspace), model=model)
                thread_id = self._thread_id(thread)
                if thread_id:
                    self._send({"action": "agent_state", "agent_id": agent_id, "codex_thread_id": str(thread_id)})
            turn = thread.turn(TextInput(text=prompt), cwd=str(workspace), model=model)
            self.active_turns[agent_id] = turn
            final_response = None
            try:
                for notification in turn.stream():
                    event_payload = self._notification_payload(notification)
                    if not isinstance(event_payload, dict):
                        event_payload = {"value": event_payload}
                    self._send(
                        {
                            "action": "agent_event",
                            "challenge_id": challenge_id,
                            "agent_id": agent_id,
                            "event_type": "codex_notification",
                            "payload": event_payload,
                        }
                    )
                    final_response = self._extract_final_response(event_payload) or final_response
            finally:
                self.active_turns.pop(agent_id, None)
            self._send(
                {
                    "action": "agent_state",
                    "agent_id": agent_id,
                    "status": "completed",
                    "last_response": final_response or "",
                    "workspace_path": str(workspace),
                }
            )
            self._upload_writeup_artifacts(agent_id=agent_id, challenge_id=challenge_id, workspace=workspace)
        except Exception:
            raise

    def _codex(self) -> Any:
        if self.codex_client is not None:
            return self.codex_client
        from openai_codex import Codex

        codex_kwargs: dict[str, Any] = {}
        if self.settings.codex_bin:
            try:
                from openai_codex import AppServerConfig

                codex_kwargs["config"] = AppServerConfig(codex_bin=self.settings.codex_bin)
            except Exception:
                pass
        self.codex_client = Codex(**codex_kwargs)
        return self.codex_client

    def _thread_id(self, thread: Any) -> str | None:
        return getattr(thread, "id", None) or getattr(thread, "thread_id", None)

    def _workspace_for(self, challenge: dict[str, Any], agent: dict[str, Any]) -> Path:
        slug = str(challenge.get("slug") or challenge.get("id") or "challenge")
        agent_id = str(agent.get("id") or "agent")
        return self.workspace_root / slug / agent_id

    def _materialize_files(self, files: list[Any], workspace: Path) -> None:
        for item in files:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "upload.bin")
            file_id = str(item.get("file_id") or "")
            if not file_id:
                continue
            response = self.client._request("GET", f"/challenge-files/{file_id}", stream=True)
            target = workspace / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        handle.write(chunk)
            response.close()
            self._extract_archive(target, workspace)

    def _extract_archive(self, path: Path, workspace: Path) -> None:
        try:
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as archive:
                    for member in archive.infolist():
                        destination = self._safe_archive_destination(workspace, member.filename)
                        mode = member.external_attr >> 16
                        if stat.S_ISLNK(mode):
                            raise RuntimeError(f"Refusing to extract symlink from archive entry: {member.filename}")
                        if member.is_dir():
                            destination.mkdir(parents=True, exist_ok=True)
                            continue
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(member) as source, destination.open("wb") as target:
                            shutil.copyfileobj(source, target)
            elif tarfile.is_tarfile(path):
                with tarfile.open(path) as archive:
                    for member in archive.getmembers():
                        destination = self._safe_archive_destination(workspace, member.name)
                        if member.issym() or member.islnk():
                            raise RuntimeError(f"Refusing to extract link from archive entry: {member.name}")
                        if member.isdir():
                            destination.mkdir(parents=True, exist_ok=True)
                            continue
                        if not member.isfile():
                            continue
                        source = archive.extractfile(member)
                        if source is None:
                            continue
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with source, destination.open("wb") as target:
                            shutil.copyfileobj(source, target)
        except Exception as exc:
            print(f"[opencrow-runtime] failed to extract {path}: {exc}", flush=True)

    def _safe_archive_destination(self, workspace: Path, member_name: str) -> Path:
        base = workspace.resolve()
        relative = Path(member_name.replace("\\", "/"))
        if relative.is_absolute():
            raise RuntimeError(f"Refusing to extract absolute archive path: {member_name}")
        destination = (base / relative).resolve()
        try:
            destination.relative_to(base)
        except ValueError as exc:
            raise RuntimeError(f"Refusing to extract path outside workspace: {member_name}") from exc
        return destination

    def _extract_final_response(self, payload: dict[str, Any]) -> str | None:
        if not isinstance(payload, dict):
            return None
        text = payload.get("final_response")
        if isinstance(text, str) and text.strip():
            return text
        inner_payload = payload.get("payload")
        if isinstance(inner_payload, dict):
            item = inner_payload.get("item")
            if isinstance(item, dict):
                root = item.get("root") if isinstance(item.get("root"), dict) else item
                text = root.get("text") if isinstance(root, dict) else None
                if isinstance(text, str) and text.strip():
                    return text
        item = payload.get("item")
        if isinstance(item, dict):
            text = item.get("text") or item.get("message")
            if isinstance(text, str) and text.strip():
                return text
        return None

    def _upload_writeup_artifacts(self, *, agent_id: str, challenge_id: str, workspace: Path) -> None:
        names = {"writeup.md", "WRITEUP.md", "solution.md", "SOLUTION.md"}
        candidates = sorted(
            path for path in workspace.rglob("*") if path.is_file() and path.name in names and path.stat().st_size <= 2_000_000
        )
        if not candidates:
            return
        try:
            payload = self.client.upload_agent_artifacts(agent_id, candidates, artifact_type="writeup")
            self._send(
                {
                    "action": "agent_event",
                    "challenge_id": challenge_id,
                    "agent_id": agent_id,
                    "event_type": "writeup_artifacts_uploaded",
                    "payload": {"artifacts": payload.get("artifacts", [])},
                }
            )
        except Exception as exc:
            self._send(
                {
                    "action": "agent_event",
                    "challenge_id": challenge_id,
                    "agent_id": agent_id,
                    "event_type": "writeup_artifacts_upload_failed",
                    "payload": {"error": str(exc), "candidate_count": len(candidates)},
                }
            )

    def _notification_payload(self, notification: Any) -> dict[str, Any]:
        method = getattr(notification, "method", None)
        payload = getattr(notification, "payload", None)
        if method is not None or payload is not None:
            return {
                "method": str(method) if method is not None else None,
                "payload": _jsonable(payload),
            }
        return _jsonable(notification)

    def _codex_sdk_available(self) -> bool:
        try:
            import openai_codex  # noqa: F401
        except Exception:
            return False
        return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-id", help="Stable runtime id override.")
    parser.add_argument("--display-name", help="Dashboard display name override.")
    parser.add_argument("--workspace-root", help="Host workspace root override.")
    parser.add_argument("--model", help="Default Codex model override.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_runtime_settings()
    if args.runtime_id or args.display_name or args.workspace_root or args.model:
        settings = RuntimeSettings(
            control_api_base_url=settings.control_api_base_url,
            control_ws_base_url=settings.control_ws_base_url,
            token=settings.token,
            runtime_id=args.runtime_id or settings.runtime_id,
            display_name=args.display_name or settings.display_name,
            workspace_root=args.workspace_root or settings.workspace_root,
            codex_model=args.model or settings.codex_model,
            codex_bin=settings.codex_bin,
            reconnect_delay_sec=settings.reconnect_delay_sec,
        )
    return RuntimeSocket(settings).run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
