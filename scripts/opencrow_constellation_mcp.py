#!/usr/bin/env python3
"""OpenCROW Constellation stdio MCP server."""

from __future__ import annotations

from collections import deque
import hashlib
import importlib.util
import json
import os
import queue
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SEARCH_PATHS = [SCRIPT_DIR, SCRIPT_DIR.parent]
for candidate in SEARCH_PATHS:
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import websocket

from constellation.client import ConstellationAPIClient, ConstellationAPIError
from constellation.config import ClientSettings, load_client_settings
from constellation.workspace import (
    discover_markdown_files,
    ensure_topic_resume_credentials,
    filter_markdown_paths,
    relative_workspace_path,
    topic_state,
    update_topic_state,
)

from opencrow_mcp_core import (
    MCPResourceTemplate,
    MCPTool,
    StdioMCPServer,
    command_exists,
    error_envelope,
    json_resource_contents,
    make_toolbox_capabilities_handler,
    make_toolbox_info_handler,
    make_toolbox_self_test_handler,
    success_envelope,
)


SERVER_NAME = "opencrow-constellation-mcp"
SERVER_VERSION = "0.1.0"
TOOLBOX_ID = "opencrow-constellation"
DISPLAY_NAME = "OpenCROW - Constellation"
OPERATIONS = [
    {"name": "constellation_topic_join", "description": "Join a topic and subscribe for live events."},
    {"name": "constellation_topic_leave", "description": "Leave a topic and close the local live subscription."},
    {"name": "constellation_topic_status", "description": "Return local subscription state and remote topic metadata."},
    {"name": "constellation_topic_history", "description": "Return recent persisted message history for a topic."},
    {"name": "constellation_message_send", "description": "Send a normal chat message to a topic."},
    {"name": "constellation_task_send", "description": "Send a hard-priority task directive to a topic."},
    {"name": "constellation_broadcast_send", "description": "Send a topic-wide broadcast event."},
    {"name": "constellation_master_claim", "description": "Claim master capability using a UI-issued single-use password."},
    {"name": "constellation_master_release", "description": "Release master capability for the current topic member."},
    {"name": "constellation_artifact_sync", "description": "Push markdown document snapshots into the shared topic corpus."},
    {"name": "constellation_final_artifact_upload", "description": "Upload immutable final artifacts after flag recovery."},
    {"name": "constellation_runtime_list", "description": "List dashboard runtimes and their health."},
    {"name": "constellation_runtime_commands", "description": "List queued or historical commands for a runtime."},
    {"name": "constellation_challenge_list", "description": "List dashboard-managed challenges."},
    {"name": "constellation_challenge_create", "description": "Create a dashboard-managed challenge."},
    {"name": "constellation_challenge_status", "description": "Inspect a dashboard-managed challenge."},
    {"name": "constellation_challenge_convert", "description": "Convert a single-agent challenge to Constellation mode."},
    {"name": "constellation_agent_list", "description": "List agents for a dashboard-managed challenge."},
    {"name": "constellation_agent_spawn_request", "description": "Request or directly spawn an agent for a challenge."},
    {"name": "constellation_agent_approve", "description": "Approve a pending agent spawn request."},
    {"name": "constellation_agent_reject", "description": "Reject a pending agent spawn request."},
    {"name": "constellation_agent_prompt", "description": "Queue a follow-up prompt for an agent."},
    {"name": "constellation_agent_interrupt", "description": "Interrupt a running agent."},
    {"name": "constellation_agent_events", "description": "Read event history for an agent or challenge."},
]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


RECONNECT_INITIAL_DELAY_SEC = 1.0
RECONNECT_MAX_DELAY_SEC = 30.0
BROKER_EVENT_CATCHUP_LIMIT = 500
RECENT_EVENT_WINDOW = 512


class PushStdioMCPServer(StdioMCPServer):
    def __init__(self, *, server_name: str, server_version: str, instructions: str | None = None) -> None:
        super().__init__(server_name=server_name, server_version=server_version, instructions=instructions)
        self._notification_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._write_lock = threading.Lock()
        self._stdout = None
        self._stop_event = threading.Event()

    def send_notification(self, method: str, params: dict[str, Any]) -> None:
        self._notification_queue.put({"jsonrpc": "2.0", "method": method, "params": params})

    def _notification_writer(self) -> None:
        while not self._stop_event.is_set():
            try:
                payload = self._notification_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if self._stdout is None:
                continue
            with self._write_lock:
                self._write_message(self._stdout, payload)

    def serve(self) -> int:
        stdin = sys.stdin.buffer
        self._stdout = sys.stdout.buffer
        writer = threading.Thread(target=self._notification_writer, daemon=True)
        writer.start()
        try:
            while True:
                request = self._read_message(stdin)
                if request is None:
                    return 0
                response = self._handle_message(request)
                if response is None:
                    continue
                with self._write_lock:
                    self._write_message(self._stdout, response)
        finally:
            self._stop_event.set()
            writer.join(timeout=1)


@dataclass
class TopicSubscription:
    topic: str
    display_name: str
    client: ConstellationAPIClient
    member: dict[str, Any]
    notify: Any
    client_kind: str = "agent"
    workspace_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    chat_identity_id: str | None = None
    resume_secret: str | None = None
    ws_app: websocket.WebSocketApp | None = None
    thread: threading.Thread | None = None
    connected: bool = False
    last_event: dict[str, Any] | None = None
    last_event_id: str | None = None
    reconnect_count: int = 0
    reconnect_attempts: int = 0
    last_disconnect: dict[str, Any] | None = None
    terminal: bool = False
    member_invalid: bool = False
    closed_intentionally: bool = False
    _pending_catchup_after_id: str | None = None
    _has_connected_once: bool = False
    _recent_event_ids: deque[str] = field(default_factory=deque)
    _recent_event_id_set: set[str] = field(default_factory=set)
    _state_lock: threading.Lock = field(default_factory=threading.Lock)
    _stop_event: threading.Event = field(default_factory=threading.Event)

    def _ws_url(self) -> str:
        return self.client.build_ws_url(
            topic=self.topic,
            member_id=self.member["id"],
            client_kind=self.client_kind,
            display_name=self.display_name,
            session_epoch=int(self.member.get("session_epoch", 0)),
        )

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.closed_intentionally = True
        self._stop_event.set()
        if self.ws_app is not None:
            self.ws_app.close()
        with self._state_lock:
            self.connected = False
        if self.thread is not None and self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=2)

    def should_replace(self) -> bool:
        if self.closed_intentionally:
            return True
        if self.thread is None:
            return False
        return not self.thread.is_alive()

    def _run_forever(self) -> None:
        delay_sec = RECONNECT_INITIAL_DELAY_SEC
        while not self._stop_event.is_set() and not self.terminal:
            if self.member_invalid:
                if not self._rejoin_member():
                    self._notify_reconnecting(delay_sec)
                    if self._stop_event.wait(delay_sec):
                        break
                    delay_sec = min(delay_sec * 2, RECONNECT_MAX_DELAY_SEC)
                    continue
                delay_sec = RECONNECT_INITIAL_DELAY_SEC

            self._pending_catchup_after_id = self.last_event_id
            self.ws_app = websocket.WebSocketApp(
                self._ws_url(),
                header=self.client.build_ws_headers(),
                subprotocols=self.client.build_ws_subprotocols(),
                on_open=self._on_open,
                on_message=self._on_message,
                on_close=self._on_close,
                on_error=self._on_error,
            )
            self.ws_app.run_forever(ping_interval=30)
            self.ws_app = None
            if self._stop_event.is_set() or self.closed_intentionally or self.terminal:
                break
            self.reconnect_attempts += 1
            self._notify_reconnecting(delay_sec)
            if self._stop_event.wait(delay_sec):
                break
            delay_sec = min(delay_sec * 2, RECONNECT_MAX_DELAY_SEC)

    def _notify_reconnecting(self, delay_sec: float) -> None:
        self.notify(
            {
                "topic": self.topic,
                "event_type": "subscription_reconnecting",
                "payload": {
                    "member_id": self.member["id"],
                    "display_name": self.display_name,
                    "attempt": self.reconnect_attempts + 1,
                    "delay_sec": delay_sec,
                    "member_invalid": self.member_invalid,
                },
            }
        )

    def _rejoin_member(self) -> bool:
        previous_member_id = self.member["id"]
        try:
            if self.chat_identity_id and self.resume_secret:
                joined = self.client.resume_topic(
                    self.topic,
                    display_name=self.display_name,
                    chat_identity_id=self.chat_identity_id,
                    resume_secret=self.resume_secret,
                    client_kind=self.client_kind,
                    workspace_path=self.workspace_path,
                    metadata=self.metadata,
                    allow_create=True,
                )
            else:
                joined = self.client.join_topic(
                    self.topic,
                    display_name=self.display_name,
                    client_kind=self.client_kind,
                    workspace_path=self.workspace_path,
                    metadata=self.metadata,
                )
        except ConstellationAPIError as exc:
            error_text = str(exc)
            if error_text.startswith("404"):
                self.terminal = True
            if error_text.startswith("403"):
                self.terminal = True
            self.notify(
                {
                    "topic": self.topic,
                    "event_type": "error",
                    "payload": {"error": f"Failed to rejoin topic member: {error_text}"},
                }
            )
            return False
        self.member = joined.member
        self.member_invalid = False
        if self.workspace_path:
            workspace_dir = Path(self.workspace_path).expanduser().resolve()
            update_topic_state(
                workspace_dir,
                self.client.settings,
                self.topic,
                {
                    "member_id": joined.member["id"],
                    "display_name": self.display_name,
                    "chat_identity_id": joined.member.get("chat_identity_id"),
                    "session_epoch": joined.member.get("session_epoch"),
                },
            )
        self.notify(
            {
                "topic": self.topic,
                "event_type": "subscription_member_rejoined",
                "payload": {
                    "previous_member_id": previous_member_id,
                    "member_id": joined.member["id"],
                    "display_name": self.display_name,
                    "master_capability": joined.member.get("master_capability", False),
                },
            }
        )
        return True

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        catchup_after_id = self._pending_catchup_after_id
        self._pending_catchup_after_id = None
        with self._state_lock:
            was_reconnect = self._has_connected_once
            self.connected = True
            self.member_invalid = False
            self.last_disconnect = None
            if was_reconnect:
                self.reconnect_count += 1
            self._has_connected_once = True
        self.notify(
            {
                "topic": self.topic,
                "event_type": "subscription_reconnected" if was_reconnect else "subscription_ready",
                "payload": {
                    "member_id": self.member["id"],
                    "display_name": self.display_name,
                    "reconnect_count": self.reconnect_count,
                },
            }
        )
        if was_reconnect and catchup_after_id:
            self._replay_catchup(catchup_after_id)

    def _on_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            payload = {"event_type": "error", "payload": {"raw": message}}
        if isinstance(payload, dict):
            payload.setdefault("topic", self.topic)
            if not self._remember_event(payload):
                return
            if payload.get("event_type") == "topic_deleted":
                self.terminal = True
            self.notify(payload)

    def _on_close(self, ws: websocket.WebSocketApp, status_code: int, message: str) -> None:
        with self._state_lock:
            self.connected = False
            self.last_disconnect = {"status_code": status_code, "message": message}
        if status_code == 4001:
            self.terminal = True
        elif status_code == 4002:
            self.terminal = True
        elif status_code == 4004:
            self.member_invalid = True
        elif status_code == 4005:
            self.terminal = True
        elif status_code == 4006:
            self.terminal = True
            self.notify(
                {
                    "topic": self.topic,
                    "event_type": "subscription_superseded",
                    "payload": {"member_id": self.member["id"], "display_name": self.display_name},
                }
            )
        self.notify(
            {
                "topic": self.topic,
                "event_type": "subscription_closed",
                "payload": {"status_code": status_code, "message": message},
            }
        )

    def _on_error(self, ws: websocket.WebSocketApp, error: Any) -> None:
        self.notify({"topic": self.topic, "event_type": "error", "payload": {"error": str(error)}})

    def _remember_event(self, payload: dict[str, Any]) -> bool:
        event_id = str(payload.get("id", "")).strip() or None
        with self._state_lock:
            if event_id:
                if event_id in self._recent_event_id_set:
                    return False
                while len(self._recent_event_ids) >= RECENT_EVENT_WINDOW:
                    oldest = self._recent_event_ids.popleft()
                    self._recent_event_id_set.discard(oldest)
                self._recent_event_ids.append(event_id)
                self._recent_event_id_set.add(event_id)
                self.last_event_id = event_id
            self.last_event = dict(payload)
        return True

    def _replay_catchup(self, after_id: str) -> None:
        replayed = 0
        cursor = after_id
        while True:
            try:
                result = self.client.events(self.topic, after_id=cursor, limit=BROKER_EVENT_CATCHUP_LIMIT)
            except ConstellationAPIError as exc:
                self.notify(
                    {
                        "topic": self.topic,
                        "event_type": "error",
                        "payload": {"error": f"Failed to replay broker events after reconnect: {exc}"},
                    }
                )
                return
            events = result.get("events", [])
            if not isinstance(events, list) or not events:
                break
            for event in events:
                if not isinstance(event, dict):
                    continue
                event.setdefault("topic", self.topic)
                event_id = str(event.get("id", "")).strip() or None
                if event_id:
                    cursor = event_id
                if not self._remember_event(event):
                    continue
                replayed += 1
                replay_event = dict(event)
                replay_event["replayed"] = True
                if replay_event.get("event_type") == "topic_deleted":
                    self.terminal = True
                self.notify(replay_event)
            if len(events) < BROKER_EVENT_CATCHUP_LIMIT:
                break
        self.notify(
            {
                "topic": self.topic,
                "event_type": "subscription_catchup",
                "payload": {
                    "after_event_id": after_id,
                    "replayed_count": replayed,
                },
            }
        )
        if self.terminal and self.ws_app is not None:
            self.ws_app.close()


class ConstellationSessionManager:
    def __init__(self, settings: ClientSettings, notifier: Any) -> None:
        self.settings = settings
        self.client = ConstellationAPIClient(settings)
        self.notifier = notifier
        self.subscriptions: dict[str, TopicSubscription] = {}
        self.lock = threading.Lock()

    def join(
        self,
        *,
        topic: str,
        display_name: str,
        workspace_path: str | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            existing = self.subscriptions.get(topic)
            if existing is not None:
                if existing.should_replace():
                    self.subscriptions.pop(topic, None)
                    existing.close()
                else:
                    return {"topic": self.client.get_topic(topic)["topic"], "member": existing.member, "subscription": self._public_subscription(existing)}
            resume_identity: dict[str, str] | None = None
            workspace_dir: Path | None = None
            if workspace_path:
                workspace_dir = Path(workspace_path).expanduser().resolve()
                resume_identity = ensure_topic_resume_credentials(workspace_dir, self.settings, topic)
                joined = self.client.resume_topic(
                    topic,
                    display_name=display_name,
                    chat_identity_id=resume_identity["chat_identity_id"],
                    resume_secret=resume_identity["resume_secret"],
                    client_kind="agent",
                    workspace_path=str(workspace_dir),
                    metadata={"via": "mcp"},
                    allow_create=True,
                )
            else:
                joined = self.client.join_topic(
                    topic,
                    display_name=display_name,
                    client_kind="agent",
                    workspace_path=workspace_path,
                    metadata={"via": "mcp"},
                )
            subscription = TopicSubscription(
                topic=topic,
                display_name=display_name,
                client=self.client,
                member=joined.member,
                client_kind="agent",
                workspace_path=workspace_path,
                metadata={"via": "mcp"},
                chat_identity_id=resume_identity["chat_identity_id"] if resume_identity else joined.member.get("chat_identity_id"),
                resume_secret=resume_identity["resume_secret"] if resume_identity else None,
                notify=self.notifier,
            )
            subscription.start()
            self.subscriptions[topic] = subscription
            if workspace_dir is not None:
                update_topic_state(
                    workspace_dir,
                    self.settings,
                    topic,
                    {
                        "member_id": joined.member["id"],
                        "display_name": display_name,
                        "chat_identity_id": joined.member.get("chat_identity_id"),
                        "session_epoch": joined.member.get("session_epoch"),
                    },
                )
            return {"topic": joined.topic, "member": joined.member, "subscription": self._public_subscription(subscription)}

    def leave(self, topic: str) -> dict[str, Any]:
        with self.lock:
            subscription = self.subscriptions.pop(topic, None)
        if subscription is None:
            raise KeyError(topic)
        subscription.close()
        self.client.leave_topic(topic, member_id=subscription.member["id"])
        if subscription.workspace_path:
            workspace_dir = Path(subscription.workspace_path).expanduser().resolve()
            update_topic_state(
                workspace_dir,
                self.settings,
                topic,
                {
                    "member_id": None,
                    "session_epoch": None,
                },
            )
        return {"topic": topic, "member_id": subscription.member["id"], "left": True}

    def status(self, topic: str | None = None) -> dict[str, Any]:
        if topic:
            subscription = self.subscriptions.get(topic)
            return {
                "topic": self.client.get_topic(topic)["topic"],
                "subscription": self._public_subscription(subscription) if subscription else None,
            }
        return {
            "subscriptions": [
                self._public_subscription(subscription)
                for subscription in self.subscriptions.values()
            ]
        }

    def history(self, topic: str, limit: int) -> dict[str, Any]:
        return self.client.history(topic, limit=limit)

    def send(self, *, topic: str, message_type: str, body: str) -> dict[str, Any]:
        subscription = self._require_subscription(topic)
        return self.client.send_message(
            topic,
            member_id=subscription.member["id"],
            message_type=message_type,
            body=body,
        )

    def claim_master(self, *, topic: str, single_use_password: str) -> dict[str, Any]:
        subscription = self._require_subscription(topic)
        result = self.client.claim_master(topic, member_id=subscription.member["id"], single_use_password=single_use_password)
        subscription.member = result["member"]
        return result

    def release_master(self, *, topic: str) -> dict[str, Any]:
        subscription = self._require_subscription(topic)
        result = self.client.release_master(topic, member_id=subscription.member["id"])
        subscription.member = result["member"]
        return result

    def sync_documents(self, *, topic: str, workspace: str, paths: list[str] | None = None) -> dict[str, Any]:
        subscription = self._require_subscription(topic)
        workspace_dir = Path(workspace).expanduser().resolve()
        if paths:
            selected_paths = filter_markdown_paths(workspace_dir, self.settings, paths)
        else:
            selected_paths = discover_markdown_files(workspace_dir, self.settings)
        documents: list[dict[str, Any]] = []
        for path in selected_paths:
            content = path.read_text(encoding="utf-8", errors="replace")
            documents.append(
                {
                    "relative_path": relative_workspace_path(workspace_dir, path),
                    "content": content,
                    "sha256": _sha256_text(content),
                }
            )
        return self.client.sync_documents(topic, member_id=subscription.member["id"], documents=documents)

    def upload_final_artifacts(
        self,
        *,
        topic: str,
        writeup_path: str,
        flag: str,
        solver_paths: list[str],
        handoff_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        subscription = self._require_subscription(topic)
        writeup = Path(writeup_path).expanduser().resolve()
        solvers = [Path(value).expanduser().resolve() for value in solver_paths]
        handoffs = [Path(value).expanduser().resolve() for value in handoff_paths or []]
        return self.client.upload_final_artifacts(
            topic,
            member_id=subscription.member["id"],
            flag=flag,
            writeup_path=writeup,
            solver_paths=solvers,
            handoff_paths=handoffs,
        )

    def list_runtimes(self) -> dict[str, Any]:
        return self.client.list_runtimes()

    def runtime_commands(self, runtime_id: str, *, status: str | None, limit: int) -> dict[str, Any]:
        return self.client.runtime_commands(runtime_id, status=status, limit=limit)

    def list_challenges(self) -> dict[str, Any]:
        return self.client.list_challenges()

    def create_challenge(
        self,
        *,
        title: str,
        description: str,
        category: str,
        challenge_type: str,
        runtime_id: str | None,
        handoff_urls: list[str],
        model: str | None,
        start_agent: bool,
    ) -> dict[str, Any]:
        settings = {"model": model} if model else None
        return self.client.create_challenge(
            title=title,
            description=description,
            category=category,
            challenge_type=challenge_type,
            runtime_id=runtime_id,
            handoff_urls=handoff_urls,
            settings=settings,
            start_agent=start_agent,
        )

    def challenge_status(self, challenge_id: str) -> dict[str, Any]:
        payload = self.client.get_challenge(challenge_id)
        challenge = payload["challenge"]
        return {
            **payload,
            "files": self.client.list_challenge_files(challenge["id"]).get("files", []),
            "agents": self.client.list_agents(challenge["id"]).get("agents", []),
            "events": self.client.challenge_events(challenge["id"], limit=100).get("events", []),
        }

    def convert_challenge(self, challenge_id: str) -> dict[str, Any]:
        return self.client.convert_challenge_to_constellation(challenge_id)

    def list_agents(self, challenge_id: str) -> dict[str, Any]:
        return self.client.list_agents(challenge_id)

    def spawn_agent(
        self,
        *,
        challenge_id: str,
        role: str,
        display_name: str,
        prompt: str | None,
        runtime_id: str | None,
        model: str | None,
        require_approval: bool,
    ) -> dict[str, Any]:
        return self.client.create_agent(
            challenge_id,
            role=role,
            display_name=display_name,
            prompt=prompt,
            runtime_id=runtime_id,
            model=model,
            require_approval=require_approval,
        )

    def approve_agent(self, agent_id: str) -> dict[str, Any]:
        return self.client.approve_agent(agent_id)

    def reject_agent(self, agent_id: str, *, reason: str | None) -> dict[str, Any]:
        return self.client.reject_agent(agent_id, reason=reason)

    def prompt_agent(self, agent_id: str, *, body: str) -> dict[str, Any]:
        return self.client.prompt_agent(agent_id, body=body)

    def interrupt_agent(self, agent_id: str) -> dict[str, Any]:
        return self.client.interrupt_agent(agent_id)

    def agent_events(self, *, agent_id: str | None, challenge_id: str | None, limit: int) -> dict[str, Any]:
        if agent_id:
            return self.client.agent_events(agent_id, limit=limit)
        if challenge_id:
            return self.client.challenge_events(challenge_id, limit=limit)
        raise ValueError("agent_id or challenge_id is required.")

    def _require_subscription(self, topic: str) -> TopicSubscription:
        subscription = self.subscriptions.get(topic)
        if subscription is None:
            raise KeyError(f"Topic is not joined locally: {topic}")
        return subscription

    @staticmethod
    def _public_subscription(subscription: TopicSubscription | None) -> dict[str, Any] | None:
        if subscription is None:
            return None
        return {
            "topic": subscription.topic,
            "display_name": subscription.display_name,
            "member_id": subscription.member["id"],
            "chat_identity_id": subscription.member.get("chat_identity_id"),
            "session_epoch": subscription.member.get("session_epoch"),
            "connected": subscription.connected,
            "thread_alive": bool(subscription.thread and subscription.thread.is_alive()),
            "reconnect_count": subscription.reconnect_count,
            "reconnect_attempts": subscription.reconnect_attempts,
            "terminal": subscription.terminal,
            "member_invalid": subscription.member_invalid,
            "last_event_id": subscription.last_event_id,
            "last_disconnect": subscription.last_disconnect,
            "last_event": subscription.last_event,
        }


SERVER: PushStdioMCPServer | None = None
SESSION_MANAGER: ConstellationSessionManager | None = None


def _manager() -> ConstellationSessionManager:
    if SESSION_MANAGER is None:
        raise RuntimeError("Constellation session manager not initialized.")
    return SESSION_MANAGER


def _notify_client(payload: dict[str, Any]) -> None:
    if SERVER is None:
        return
    SERVER.send_notification("notifications/opencrow/constellation_event", payload)


def toolbox_verify(arguments: dict[str, object]) -> dict[str, object]:
    observations = [
        {"dependency": "python3", "available": command_exists("python3")},
        {"dependency": "python_requests", "available": importlib.util.find_spec("requests") is not None},
        {"dependency": "python_websocket_client", "available": importlib.util.find_spec("websocket") is not None},
        {"dependency": "configured_api_base", "value": load_client_settings().api_base_url},
        {"dependency": "configured_ws_base", "value": load_client_settings().ws_base_url},
        {"dependency": "token_present", "available": bool(load_client_settings().token)},
    ]
    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation="toolbox_verify",
        summary="Constellation MCP client dependency status returned.",
        inputs=arguments,
        observations=observations,
        next_steps=["Use `constellation_topic_join` to subscribe to a Constellation topic."],
    )


def constellation_topic_join(arguments: dict[str, object]) -> dict[str, object]:
    topic = str(arguments.get("topic", "")).strip()
    display_name = str(arguments.get("display_name", "")).strip() or os.uname().nodename
    workspace = arguments.get("workspace")
    if not topic:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="constellation_topic_join",
            summary="Topic is required.",
            inputs=arguments,
            stderr="Pass `topic`.",
            exit_code=2,
        )
    try:
        result = _manager().join(topic=topic, display_name=display_name, workspace_path=str(workspace) if workspace else None)
    except ConstellationAPIError as exc:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="constellation_topic_join",
            summary=f"Failed to join topic `{topic}`.",
            inputs=arguments,
            stderr=str(exc),
        )
    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation="constellation_topic_join",
        summary=f"Joined topic `{topic}`.",
        inputs=arguments,
        observations=[result],
        next_steps=[
            "Use `constellation_message_send` or `constellation_task_send` to speak into the topic.",
            "Watch for `notifications/opencrow/constellation_event` notifications from the live subscription.",
        ],
    )


def constellation_topic_leave(arguments: dict[str, object]) -> dict[str, object]:
    topic = str(arguments.get("topic", "")).strip()
    if not topic:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="constellation_topic_leave",
            summary="Topic is required.",
            inputs=arguments,
            stderr="Pass `topic`.",
            exit_code=2,
        )
    try:
        result = _manager().leave(topic)
    except (ConstellationAPIError, KeyError) as exc:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="constellation_topic_leave",
            summary=f"Failed to leave topic `{topic}`.",
            inputs=arguments,
            stderr=str(exc),
        )
    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation="constellation_topic_leave",
        summary=f"Left topic `{topic}`.",
        inputs=arguments,
        observations=[result],
    )


def constellation_topic_status(arguments: dict[str, object]) -> dict[str, object]:
    topic = str(arguments.get("topic", "")).strip() or None
    try:
        result = _manager().status(topic)
    except ConstellationAPIError as exc:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="constellation_topic_status",
            summary="Failed to load topic status.",
            inputs=arguments,
            stderr=str(exc),
        )
    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation="constellation_topic_status",
        summary="Constellation topic status returned.",
        inputs=arguments,
        observations=[result],
    )


def constellation_topic_history(arguments: dict[str, object]) -> dict[str, object]:
    topic = str(arguments.get("topic", "")).strip()
    limit = int(arguments.get("limit", 100))
    if not topic:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="constellation_topic_history",
            summary="Topic is required.",
            inputs=arguments,
            stderr="Pass `topic`.",
            exit_code=2,
        )
    try:
        result = _manager().history(topic, limit=limit)
    except ConstellationAPIError as exc:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="constellation_topic_history",
            summary=f"Failed to load history for `{topic}`.",
            inputs=arguments,
            stderr=str(exc),
        )
    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation="constellation_topic_history",
        summary=f"History returned for topic `{topic}`.",
        inputs=arguments,
        observations=[result],
    )


def _send_message(arguments: dict[str, object], *, message_type: str, operation: str, summary_label: str) -> dict[str, object]:
    topic = str(arguments.get("topic", "")).strip()
    body = str(arguments.get("body", "")).strip()
    if not topic or not body:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation=operation,
            summary="Topic and body are required.",
            inputs=arguments,
            stderr="Pass `topic` and `body`.",
            exit_code=2,
        )
    try:
        result = _manager().send(topic=topic, message_type=message_type, body=body)
    except (ConstellationAPIError, KeyError) as exc:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation=operation,
            summary=f"Failed to send {summary_label} into `{topic}`.",
            inputs=arguments,
            stderr=str(exc),
        )
    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation=operation,
        summary=f"Sent {summary_label} into `{topic}`.",
        inputs=arguments,
        observations=[result],
    )


def constellation_message_send(arguments: dict[str, object]) -> dict[str, object]:
    return _send_message(arguments, message_type="chat_message", operation="constellation_message_send", summary_label="chat message")


def constellation_task_send(arguments: dict[str, object]) -> dict[str, object]:
    return _send_message(arguments, message_type="task_directive", operation="constellation_task_send", summary_label="task directive")


def constellation_broadcast_send(arguments: dict[str, object]) -> dict[str, object]:
    return _send_message(arguments, message_type="broadcast_event", operation="constellation_broadcast_send", summary_label="broadcast event")


def constellation_master_claim(arguments: dict[str, object]) -> dict[str, object]:
    topic = str(arguments.get("topic", "")).strip()
    password = str(arguments.get("single_use_password", "")).strip()
    if not topic or not password:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="constellation_master_claim",
            summary="Topic and single_use_password are required.",
            inputs=arguments,
            stderr="Pass `topic` and `single_use_password`.",
            exit_code=2,
        )
    try:
        result = _manager().claim_master(topic=topic, single_use_password=password)
    except (ConstellationAPIError, KeyError) as exc:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="constellation_master_claim",
            summary=f"Failed to claim master capability for `{topic}`.",
            inputs=arguments,
            stderr=str(exc),
        )
    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation="constellation_master_claim",
        summary=f"Master capability claimed for `{topic}`.",
        inputs=arguments,
        observations=[result],
    )


def constellation_master_release(arguments: dict[str, object]) -> dict[str, object]:
    topic = str(arguments.get("topic", "")).strip()
    if not topic:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="constellation_master_release",
            summary="Topic is required.",
            inputs=arguments,
            stderr="Pass `topic`.",
            exit_code=2,
        )
    try:
        result = _manager().release_master(topic=topic)
    except (ConstellationAPIError, KeyError) as exc:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="constellation_master_release",
            summary=f"Failed to release master capability for `{topic}`.",
            inputs=arguments,
            stderr=str(exc),
        )
    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation="constellation_master_release",
        summary=f"Released master capability for `{topic}`.",
        inputs=arguments,
        observations=[result],
    )


def constellation_artifact_sync(arguments: dict[str, object]) -> dict[str, object]:
    topic = str(arguments.get("topic", "")).strip()
    workspace = str(arguments.get("workspace", "")).strip()
    paths = arguments.get("paths") if isinstance(arguments.get("paths"), list) else None
    if not topic or not workspace:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="constellation_artifact_sync",
            summary="Topic and workspace are required.",
            inputs=arguments,
            stderr="Pass `topic` and `workspace`.",
            exit_code=2,
        )
    try:
        result = _manager().sync_documents(topic=topic, workspace=workspace, paths=[str(item) for item in paths] if paths else None)
    except (ConstellationAPIError, KeyError, OSError) as exc:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="constellation_artifact_sync",
            summary=f"Failed to sync markdown documents for `{topic}`.",
            inputs=arguments,
            stderr=str(exc),
        )
    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation="constellation_artifact_sync",
        summary=f"Synced markdown documents for `{topic}`.",
        inputs=arguments,
        observations=[result],
    )


def constellation_final_artifact_upload(arguments: dict[str, object]) -> dict[str, object]:
    topic = str(arguments.get("topic", "")).strip()
    writeup_path = str(arguments.get("writeup_path", "")).strip()
    flag = str(arguments.get("flag", "")).strip()
    solver_paths = arguments.get("solver_paths") if isinstance(arguments.get("solver_paths"), list) else []
    handoff_paths = arguments.get("handoff_paths") if isinstance(arguments.get("handoff_paths"), list) else []
    if not topic or not writeup_path or not flag or not solver_paths:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="constellation_final_artifact_upload",
            summary="Topic, writeup_path, flag, and solver_paths are required.",
            inputs=arguments,
            stderr="Pass `topic`, `writeup_path`, `flag`, and at least one solver path.",
            exit_code=2,
        )
    try:
        result = _manager().upload_final_artifacts(
            topic=topic,
            writeup_path=writeup_path,
            flag=flag,
            solver_paths=[str(value) for value in solver_paths],
            handoff_paths=[str(value) for value in handoff_paths],
        )
    except (ConstellationAPIError, KeyError, OSError) as exc:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="constellation_final_artifact_upload",
            summary=f"Failed to upload final artifacts for `{topic}`.",
            inputs=arguments,
            stderr=str(exc),
        )
    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation="constellation_final_artifact_upload",
        summary=f"Uploaded final artifacts for `{topic}`.",
        inputs=arguments,
        observations=[result],
    )


def _platform_call(arguments: dict[str, object], *, operation: str, summary: str, call: Any) -> dict[str, object]:
    try:
        result = call()
    except (ConstellationAPIError, KeyError, ValueError, OSError) as exc:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation=operation,
            summary=f"{summary} failed.",
            inputs=arguments,
            stderr=str(exc),
        )
    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation=operation,
        summary=summary,
        inputs=arguments,
        observations=[result],
    )


def constellation_runtime_list(arguments: dict[str, object]) -> dict[str, object]:
    return _platform_call(
        arguments,
        operation="constellation_runtime_list",
        summary="Runtime fleet returned.",
        call=lambda: _manager().list_runtimes(),
    )


def constellation_runtime_commands(arguments: dict[str, object]) -> dict[str, object]:
    runtime_id = str(arguments.get("runtime_id", "")).strip()
    status = str(arguments.get("status", "")).strip() or None
    limit = int(arguments.get("limit", 100))
    if not runtime_id:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="constellation_runtime_commands",
            summary="runtime_id is required.",
            inputs=arguments,
            stderr="Pass `runtime_id`.",
            exit_code=2,
        )
    return _platform_call(
        arguments,
        operation="constellation_runtime_commands",
        summary=f"Runtime commands returned for `{runtime_id}`.",
        call=lambda: _manager().runtime_commands(runtime_id, status=status, limit=limit),
    )


def constellation_challenge_list(arguments: dict[str, object]) -> dict[str, object]:
    return _platform_call(
        arguments,
        operation="constellation_challenge_list",
        summary="Dashboard challenges returned.",
        call=lambda: _manager().list_challenges(),
    )


def constellation_challenge_create(arguments: dict[str, object]) -> dict[str, object]:
    title = str(arguments.get("title", "")).strip()
    if not title:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="constellation_challenge_create",
            summary="title is required.",
            inputs=arguments,
            stderr="Pass `title`.",
            exit_code=2,
        )
    handoff_urls = [str(value).strip() for value in arguments.get("handoff_urls", [])] if isinstance(arguments.get("handoff_urls"), list) else []
    return _platform_call(
        arguments,
        operation="constellation_challenge_create",
        summary=f"Challenge `{title}` created.",
        call=lambda: _manager().create_challenge(
            title=title,
            description=str(arguments.get("description", "")).strip(),
            category=str(arguments.get("category", "misc")).strip() or "misc",
            challenge_type=str(arguments.get("challenge_type", "single_agent")).strip() or "single_agent",
            runtime_id=str(arguments.get("runtime_id", "")).strip() or None,
            handoff_urls=handoff_urls,
            model=str(arguments.get("model", "")).strip() or None,
            start_agent=bool(arguments.get("start_agent", True)),
        ),
    )


def constellation_challenge_status(arguments: dict[str, object]) -> dict[str, object]:
    challenge_id = str(arguments.get("challenge_id", "")).strip()
    if not challenge_id:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="constellation_challenge_status",
            summary="challenge_id is required.",
            inputs=arguments,
            stderr="Pass `challenge_id`.",
            exit_code=2,
        )
    return _platform_call(
        arguments,
        operation="constellation_challenge_status",
        summary=f"Challenge `{challenge_id}` status returned.",
        call=lambda: _manager().challenge_status(challenge_id),
    )


def constellation_challenge_convert(arguments: dict[str, object]) -> dict[str, object]:
    challenge_id = str(arguments.get("challenge_id", "")).strip()
    if not challenge_id:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="constellation_challenge_convert",
            summary="challenge_id is required.",
            inputs=arguments,
            stderr="Pass `challenge_id`.",
            exit_code=2,
        )
    return _platform_call(
        arguments,
        operation="constellation_challenge_convert",
        summary=f"Challenge `{challenge_id}` converted to Constellation mode.",
        call=lambda: _manager().convert_challenge(challenge_id),
    )


def constellation_agent_list(arguments: dict[str, object]) -> dict[str, object]:
    challenge_id = str(arguments.get("challenge_id", "")).strip()
    if not challenge_id:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="constellation_agent_list",
            summary="challenge_id is required.",
            inputs=arguments,
            stderr="Pass `challenge_id`.",
            exit_code=2,
        )
    return _platform_call(
        arguments,
        operation="constellation_agent_list",
        summary=f"Agents returned for challenge `{challenge_id}`.",
        call=lambda: _manager().list_agents(challenge_id),
    )


def constellation_agent_spawn_request(arguments: dict[str, object]) -> dict[str, object]:
    challenge_id = str(arguments.get("challenge_id", "")).strip()
    if not challenge_id:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="constellation_agent_spawn_request",
            summary="challenge_id is required.",
            inputs=arguments,
            stderr="Pass `challenge_id`.",
            exit_code=2,
        )
    role = str(arguments.get("role", "slave")).strip() or "slave"
    display_name = str(arguments.get("display_name", "")).strip() or f"{role} agent"
    require_approval = bool(arguments.get("require_approval", True))
    return _platform_call(
        arguments,
        operation="constellation_agent_spawn_request",
        summary=f"Agent spawn {'requested' if require_approval else 'queued'} for `{challenge_id}`.",
        call=lambda: _manager().spawn_agent(
            challenge_id=challenge_id,
            role=role,
            display_name=display_name,
            prompt=str(arguments.get("prompt", "")).strip() or None,
            runtime_id=str(arguments.get("runtime_id", "")).strip() or None,
            model=str(arguments.get("model", "")).strip() or None,
            require_approval=require_approval,
        ),
    )


def constellation_agent_approve(arguments: dict[str, object]) -> dict[str, object]:
    agent_id = str(arguments.get("agent_id", "")).strip()
    if not agent_id:
        return error_envelope(toolbox=TOOLBOX_ID, operation="constellation_agent_approve", summary="agent_id is required.", inputs=arguments, stderr="Pass `agent_id`.", exit_code=2)
    return _platform_call(arguments, operation="constellation_agent_approve", summary=f"Agent `{agent_id}` approved.", call=lambda: _manager().approve_agent(agent_id))


def constellation_agent_reject(arguments: dict[str, object]) -> dict[str, object]:
    agent_id = str(arguments.get("agent_id", "")).strip()
    if not agent_id:
        return error_envelope(toolbox=TOOLBOX_ID, operation="constellation_agent_reject", summary="agent_id is required.", inputs=arguments, stderr="Pass `agent_id`.", exit_code=2)
    return _platform_call(
        arguments,
        operation="constellation_agent_reject",
        summary=f"Agent `{agent_id}` rejected.",
        call=lambda: _manager().reject_agent(agent_id, reason=str(arguments.get("reason", "")).strip() or None),
    )


def constellation_agent_prompt(arguments: dict[str, object]) -> dict[str, object]:
    agent_id = str(arguments.get("agent_id", "")).strip()
    body = str(arguments.get("body", "")).strip()
    if not agent_id or not body:
        return error_envelope(toolbox=TOOLBOX_ID, operation="constellation_agent_prompt", summary="agent_id and body are required.", inputs=arguments, stderr="Pass `agent_id` and `body`.", exit_code=2)
    return _platform_call(arguments, operation="constellation_agent_prompt", summary=f"Prompt queued for `{agent_id}`.", call=lambda: _manager().prompt_agent(agent_id, body=body))


def constellation_agent_interrupt(arguments: dict[str, object]) -> dict[str, object]:
    agent_id = str(arguments.get("agent_id", "")).strip()
    if not agent_id:
        return error_envelope(toolbox=TOOLBOX_ID, operation="constellation_agent_interrupt", summary="agent_id is required.", inputs=arguments, stderr="Pass `agent_id`.", exit_code=2)
    return _platform_call(arguments, operation="constellation_agent_interrupt", summary=f"Interrupt queued for `{agent_id}`.", call=lambda: _manager().interrupt_agent(agent_id))


def constellation_agent_events(arguments: dict[str, object]) -> dict[str, object]:
    agent_id = str(arguments.get("agent_id", "")).strip() or None
    challenge_id = str(arguments.get("challenge_id", "")).strip() or None
    limit = int(arguments.get("limit", 100))
    if not agent_id and not challenge_id:
        return error_envelope(toolbox=TOOLBOX_ID, operation="constellation_agent_events", summary="agent_id or challenge_id is required.", inputs=arguments, stderr="Pass `agent_id` or `challenge_id`.", exit_code=2)
    return _platform_call(
        arguments,
        operation="constellation_agent_events",
        summary="Agent event history returned.",
        call=lambda: _manager().agent_events(agent_id=agent_id, challenge_id=challenge_id, limit=limit),
    )


def _read_topic_resource(uri: str, params: dict[str, str]) -> list[dict[str, object]]:
    topic = params["topic"]
    payload = _manager().client.get_topic(topic)
    return json_resource_contents(uri, payload)


def _read_history_resource(uri: str, params: dict[str, str]) -> list[dict[str, object]]:
    topic = params["topic"]
    payload = _manager().client.history(topic, limit=100)
    return json_resource_contents(uri, payload)


def build_server() -> PushStdioMCPServer:
    server = PushStdioMCPServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        instructions="OpenCROW Constellation stdio MCP client. Join a topic to receive live broker notifications.",
    )
    server.register_tools(
        [
            MCPTool(
                name="toolbox_info",
                description="Return metadata about the OpenCROW Constellation MCP client.",
                input_schema={"type": "object", "properties": {}},
                handler=make_toolbox_info_handler(
                    toolbox=TOOLBOX_ID,
                    display_name=DISPLAY_NAME,
                    server_name=SERVER_NAME,
                    server_version=SERVER_VERSION,
                    summary="OpenCROW Constellation MCP information returned.",
                    operations=OPERATIONS,
                ),
            ),
            MCPTool(
                name="toolbox_self_test",
                description="Run a lightweight self-test for this OpenCROW MCP server.",
                input_schema={"type": "object", "properties": {}},
                handler=make_toolbox_self_test_handler(
                    toolbox=TOOLBOX_ID,
                    display_name=DISPLAY_NAME,
                    server_name=SERVER_NAME,
                    server_version=SERVER_VERSION,
                    operations=OPERATIONS,
                ),
            ),
            MCPTool(
                name="toolbox_verify",
                description="Return dependency status for the OpenCROW Constellation MCP client.",
                input_schema={"type": "object", "properties": {}},
                handler=toolbox_verify,
            ),
            MCPTool(
                name="toolbox_capabilities",
                description="Return the structured operations exposed by the OpenCROW Constellation MCP client.",
                input_schema={"type": "object", "properties": {}},
                handler=make_toolbox_capabilities_handler(TOOLBOX_ID, OPERATIONS),
            ),
            MCPTool(
                name="constellation_topic_join",
                description="Join a topic and subscribe for live events.",
                input_schema={
                    "type": "object",
                    "required": ["topic"],
                    "properties": {
                        "topic": {"type": "string"},
                        "display_name": {"type": "string"},
                        "workspace": {"type": "string"},
                    },
                },
                handler=constellation_topic_join,
            ),
            MCPTool(
                name="constellation_topic_leave",
                description="Leave a topic and close the local live subscription.",
                input_schema={"type": "object", "required": ["topic"], "properties": {"topic": {"type": "string"}}},
                handler=constellation_topic_leave,
            ),
            MCPTool(
                name="constellation_topic_status",
                description="Return local subscription state and remote topic metadata.",
                input_schema={"type": "object", "properties": {"topic": {"type": "string"}}},
                handler=constellation_topic_status,
            ),
            MCPTool(
                name="constellation_topic_history",
                description="Return recent persisted message history for a topic.",
                input_schema={
                    "type": "object",
                    "required": ["topic"],
                    "properties": {"topic": {"type": "string"}, "limit": {"type": "integer"}},
                },
                handler=constellation_topic_history,
            ),
            MCPTool(
                name="constellation_message_send",
                description="Send a normal chat message to a topic.",
                input_schema={
                    "type": "object",
                    "required": ["topic", "body"],
                    "properties": {"topic": {"type": "string"}, "body": {"type": "string"}},
                },
                handler=constellation_message_send,
            ),
            MCPTool(
                name="constellation_task_send",
                description="Send a hard-priority task directive to a topic.",
                input_schema={
                    "type": "object",
                    "required": ["topic", "body"],
                    "properties": {"topic": {"type": "string"}, "body": {"type": "string"}},
                },
                handler=constellation_task_send,
            ),
            MCPTool(
                name="constellation_broadcast_send",
                description="Send a topic-wide broadcast event.",
                input_schema={
                    "type": "object",
                    "required": ["topic", "body"],
                    "properties": {"topic": {"type": "string"}, "body": {"type": "string"}},
                },
                handler=constellation_broadcast_send,
            ),
            MCPTool(
                name="constellation_master_claim",
                description="Claim master capability using a UI-issued single-use password.",
                input_schema={
                    "type": "object",
                    "required": ["topic", "single_use_password"],
                    "properties": {"topic": {"type": "string"}, "single_use_password": {"type": "string"}},
                },
                handler=constellation_master_claim,
            ),
            MCPTool(
                name="constellation_master_release",
                description="Release master capability for the current topic member.",
                input_schema={"type": "object", "required": ["topic"], "properties": {"topic": {"type": "string"}}},
                handler=constellation_master_release,
            ),
            MCPTool(
                name="constellation_artifact_sync",
                description="Push markdown document snapshots into the shared topic corpus.",
                input_schema={
                    "type": "object",
                    "required": ["topic", "workspace"],
                    "properties": {
                        "topic": {"type": "string"},
                        "workspace": {"type": "string"},
                        "paths": {"type": "array", "items": {"type": "string"}},
                    },
                },
                handler=constellation_artifact_sync,
            ),
            MCPTool(
                name="constellation_final_artifact_upload",
                description="Upload immutable final artifacts after flag recovery.",
                input_schema={
                    "type": "object",
                    "required": ["topic", "writeup_path", "flag", "solver_paths"],
                    "properties": {
                        "topic": {"type": "string"},
                        "writeup_path": {"type": "string"},
                        "flag": {"type": "string"},
                        "solver_paths": {"type": "array", "items": {"type": "string"}},
                        "handoff_paths": {"type": "array", "items": {"type": "string"}},
                    },
                },
                handler=constellation_final_artifact_upload,
            ),
            MCPTool(
                name="constellation_runtime_list",
                description="List dashboard runtimes and their health.",
                input_schema={"type": "object", "properties": {}},
                handler=constellation_runtime_list,
            ),
            MCPTool(
                name="constellation_runtime_commands",
                description="List queued or historical commands for a runtime.",
                input_schema={
                    "type": "object",
                    "required": ["runtime_id"],
                    "properties": {
                        "runtime_id": {"type": "string"},
                        "status": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
                handler=constellation_runtime_commands,
            ),
            MCPTool(
                name="constellation_challenge_list",
                description="List dashboard-managed challenges.",
                input_schema={"type": "object", "properties": {}},
                handler=constellation_challenge_list,
            ),
            MCPTool(
                name="constellation_challenge_create",
                description="Create a dashboard-managed challenge.",
                input_schema={
                    "type": "object",
                    "required": ["title"],
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "category": {"type": "string"},
                        "challenge_type": {"type": "string"},
                        "runtime_id": {"type": "string"},
                        "handoff_urls": {"type": "array", "items": {"type": "string"}},
                        "model": {"type": "string"},
                        "start_agent": {"type": "boolean"},
                    },
                },
                handler=constellation_challenge_create,
            ),
            MCPTool(
                name="constellation_challenge_status",
                description="Inspect a dashboard-managed challenge.",
                input_schema={"type": "object", "required": ["challenge_id"], "properties": {"challenge_id": {"type": "string"}}},
                handler=constellation_challenge_status,
            ),
            MCPTool(
                name="constellation_challenge_convert",
                description="Convert a single-agent challenge to Constellation mode.",
                input_schema={"type": "object", "required": ["challenge_id"], "properties": {"challenge_id": {"type": "string"}}},
                handler=constellation_challenge_convert,
            ),
            MCPTool(
                name="constellation_agent_list",
                description="List agents for a dashboard-managed challenge.",
                input_schema={"type": "object", "required": ["challenge_id"], "properties": {"challenge_id": {"type": "string"}}},
                handler=constellation_agent_list,
            ),
            MCPTool(
                name="constellation_agent_spawn_request",
                description="Request or directly spawn an agent for a challenge.",
                input_schema={
                    "type": "object",
                    "required": ["challenge_id"],
                    "properties": {
                        "challenge_id": {"type": "string"},
                        "role": {"type": "string"},
                        "display_name": {"type": "string"},
                        "prompt": {"type": "string"},
                        "runtime_id": {"type": "string"},
                        "model": {"type": "string"},
                        "require_approval": {"type": "boolean"},
                    },
                },
                handler=constellation_agent_spawn_request,
            ),
            MCPTool(
                name="constellation_agent_approve",
                description="Approve a pending agent spawn request.",
                input_schema={"type": "object", "required": ["agent_id"], "properties": {"agent_id": {"type": "string"}}},
                handler=constellation_agent_approve,
            ),
            MCPTool(
                name="constellation_agent_reject",
                description="Reject a pending agent spawn request.",
                input_schema={"type": "object", "required": ["agent_id"], "properties": {"agent_id": {"type": "string"}, "reason": {"type": "string"}}},
                handler=constellation_agent_reject,
            ),
            MCPTool(
                name="constellation_agent_prompt",
                description="Queue a follow-up prompt for an agent.",
                input_schema={
                    "type": "object",
                    "required": ["agent_id", "body"],
                    "properties": {"agent_id": {"type": "string"}, "body": {"type": "string"}},
                },
                handler=constellation_agent_prompt,
            ),
            MCPTool(
                name="constellation_agent_interrupt",
                description="Interrupt a running agent.",
                input_schema={"type": "object", "required": ["agent_id"], "properties": {"agent_id": {"type": "string"}}},
                handler=constellation_agent_interrupt,
            ),
            MCPTool(
                name="constellation_agent_events",
                description="Read event history for an agent or challenge.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "string"},
                        "challenge_id": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
                handler=constellation_agent_events,
            ),
        ]
    )
    base_uri = f"opencrow://{SERVER_NAME}"
    server.register_resource_templates(
        [
            MCPResourceTemplate(
                uri_template=f"{base_uri}/topic/{{topic}}",
                name="Constellation topic",
                description="Read topic metadata for a Constellation topic.",
                mime_type="application/json",
                handler=_read_topic_resource,
            ),
            MCPResourceTemplate(
                uri_template=f"{base_uri}/history/{{topic}}",
                name="Constellation topic history",
                description="Read recent history for a Constellation topic.",
                mime_type="application/json",
                handler=_read_history_resource,
            ),
        ]
    )
    return server


def main() -> int:
    global SERVER, SESSION_MANAGER
    settings = load_client_settings()
    SERVER = build_server()
    SESSION_MANAGER = ConstellationSessionManager(settings, _notify_client)
    return SERVER.serve()


if __name__ == "__main__":
    raise SystemExit(main())
