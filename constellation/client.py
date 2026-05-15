"""HTTP and WebSocket client helpers for Constellation."""

from __future__ import annotations

import base64
import json
import mimetypes
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from .config import ClientSettings


class ConstellationAPIError(RuntimeError):
    """Raised when the backend API returns an error."""


def default_agent_name() -> str:
    return f"{socket.gethostname()}:{Path.cwd().name}"


@dataclass(frozen=True)
class TopicJoinResult:
    topic: dict[str, Any]
    member: dict[str, Any]


class ConstellationAPIClient:
    def __init__(self, settings: ClientSettings, *, extra_headers: dict[str, str] | None = None) -> None:
        self.settings = settings
        self.session = requests.Session()
        self.extra_headers = dict(extra_headers or {})

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.settings.token}",
        }
        headers.update(self.extra_headers)
        return headers

    def _api_url(self, path: str) -> str:
        base = self.settings.api_base_url.rstrip("/")
        return f"{base}/api/v1{path}"

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        headers = dict(self._headers())
        extra_headers = kwargs.pop("headers", None)
        if isinstance(extra_headers, dict):
            headers.update(extra_headers)
        response = self.session.request(
            method=method,
            url=self._api_url(path),
            headers=headers,
            timeout=self.settings.request_timeout_sec,
            **kwargs,
        )
        if response.status_code >= 400:
            message = response.text.strip() or response.reason
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    message = str(payload.get("error") or payload.get("summary") or message)
            except ValueError:
                pass
            raise ConstellationAPIError(f"{response.status_code} {response.reason}: {message}")
        return response

    def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self._request(method, path, **kwargs)
        payload = response.json()
        if not isinstance(payload, dict):
            raise ConstellationAPIError(f"Unexpected JSON payload for {method} {path}: {payload!r}")
        return payload

    def validate_auth(self) -> dict[str, Any]:
        return self._json("GET", "/auth/validate")

    def list_topics(self) -> dict[str, Any]:
        return self._json("GET", "/topics")

    def create_topic(
        self,
        *,
        title: str,
        description: str,
        category: str,
        handoff_urls: list[str],
        slug: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": title,
            "description": description,
            "category": category,
            "handoff_urls": handoff_urls,
        }
        if slug:
            payload["slug"] = slug
        return self._json("POST", "/topics", json=payload)

    def get_topic(self, topic: str) -> dict[str, Any]:
        return self._json("GET", f"/topics/{topic}")

    def update_topic(
        self,
        topic: str,
        *,
        title: str | None = None,
        description: str | None = None,
        category: str | None = None,
        handoff_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if description is not None:
            payload["description"] = description
        if category is not None:
            payload["category"] = category
        if handoff_urls is not None:
            payload["handoff_urls"] = handoff_urls
        return self._json("PATCH", f"/topics/{topic}", json=payload)

    def delete_topic(self, topic: str) -> dict[str, Any]:
        return self._json("DELETE", f"/topics/{topic}")

    def list_runtimes(self) -> dict[str, Any]:
        return self._json("GET", "/runtimes")

    def runtime_commands(self, runtime_id: str, *, status: str | None = None, limit: int = 100) -> dict[str, Any]:
        path = f"/runtimes/{runtime_id}/commands?limit={limit}"
        if status:
            path = f"{path}&status={status}"
        return self._json("GET", path)

    def list_challenges(self) -> dict[str, Any]:
        return self._json("GET", "/challenges")

    def create_challenge(
        self,
        *,
        title: str,
        description: str,
        category: str,
        challenge_type: str,
        runtime_id: str | None = None,
        handoff_urls: list[str] | None = None,
        slug: str | None = None,
        settings: dict[str, Any] | None = None,
        start_agent: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": title,
            "description": description,
            "category": category,
            "challenge_type": challenge_type,
            "handoff_urls": handoff_urls or [],
            "start_agent": start_agent,
        }
        if runtime_id:
            payload["runtime_id"] = runtime_id
        if slug:
            payload["slug"] = slug
        if settings:
            payload["settings"] = settings
        return self._json("POST", "/challenges", json=payload)

    def get_challenge(self, challenge_id: str) -> dict[str, Any]:
        return self._json("GET", f"/challenges/{challenge_id}")

    def convert_challenge_to_constellation(self, challenge_id: str) -> dict[str, Any]:
        return self._json("POST", f"/challenges/{challenge_id}/convert-to-constellation", json={})

    def list_challenge_files(self, challenge_id: str) -> dict[str, Any]:
        return self._json("GET", f"/challenges/{challenge_id}/files")

    def upload_challenge_files(self, challenge_id: str, paths: list[Path]) -> dict[str, Any]:
        files: list[tuple[str, tuple[str, Any, str]]] = []
        opened: list[Any] = []
        try:
            for path in paths:
                handle = path.open("rb")
                opened.append(handle)
                content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                files.append(("file", (path.name, handle, content_type)))
            response = self._request("POST", f"/challenges/{challenge_id}/files", files=files)
            payload = response.json()
            if not isinstance(payload, dict):
                raise ConstellationAPIError(f"Unexpected upload payload: {payload!r}")
            return payload
        finally:
            for handle in opened:
                handle.close()

    def list_agents(self, challenge_id: str) -> dict[str, Any]:
        return self._json("GET", f"/challenges/{challenge_id}/agents")

    def create_agent(
        self,
        challenge_id: str,
        *,
        role: str,
        display_name: str,
        prompt: str | None = None,
        runtime_id: str | None = None,
        model: str | None = None,
        require_approval: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "role": role,
            "display_name": display_name,
            "require_approval": require_approval,
        }
        if prompt:
            payload["prompt"] = prompt
        if runtime_id:
            payload["runtime_id"] = runtime_id
        if model:
            payload["model"] = model
        return self._json("POST", f"/challenges/{challenge_id}/agents", json=payload)

    def agent_events(self, agent_id: str, *, limit: int = 200) -> dict[str, Any]:
        return self._json("GET", f"/agents/{agent_id}/events?limit={limit}")

    def list_agent_artifacts(self, agent_id: str) -> dict[str, Any]:
        return self._json("GET", f"/agents/{agent_id}/artifacts")

    def upload_agent_artifacts(self, agent_id: str, paths: list[Path], *, artifact_type: str = "artifact") -> dict[str, Any]:
        files: list[tuple[str, tuple[str, Any, str]]] = []
        opened: list[Any] = []
        try:
            for path in paths:
                handle = path.open("rb")
                opened.append(handle)
                content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                files.append(("file", (path.name, handle, content_type)))
            response = self._request("POST", f"/agents/{agent_id}/artifacts", data={"artifact_type": artifact_type}, files=files)
            payload = response.json()
            if not isinstance(payload, dict):
                raise ConstellationAPIError(f"Unexpected artifact upload payload: {payload!r}")
            return payload
        finally:
            for handle in opened:
                handle.close()

    def download_agent_artifact(self, file_id: str) -> requests.Response:
        return self._request("GET", f"/agent-artifacts/{file_id}", stream=True)

    def challenge_events(self, challenge_id: str, *, limit: int = 200) -> dict[str, Any]:
        return self._json("GET", f"/challenges/{challenge_id}/events?limit={limit}")

    def prompt_agent(self, agent_id: str, *, body: str) -> dict[str, Any]:
        return self._json("POST", f"/agents/{agent_id}/prompt", json={"body": body})

    def interrupt_agent(self, agent_id: str) -> dict[str, Any]:
        return self._json("POST", f"/agents/{agent_id}/interrupt", json={})

    def approve_agent(self, agent_id: str) -> dict[str, Any]:
        return self._json("POST", f"/agents/{agent_id}/approve", json={})

    def reject_agent(self, agent_id: str, *, reason: str | None = None) -> dict[str, Any]:
        payload = {"reason": reason} if reason else {}
        return self._json("POST", f"/agents/{agent_id}/reject", json=payload)

    def join_topic(
        self,
        topic: str,
        *,
        display_name: str,
        client_kind: str,
        workspace_path: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TopicJoinResult:
        payload: dict[str, Any] = {
            "display_name": display_name,
            "client_kind": client_kind,
        }
        if workspace_path:
            payload["workspace_path"] = workspace_path
        if metadata:
            payload["metadata"] = metadata
        result = self._json("POST", f"/topics/{topic}/join", json=payload)
        return TopicJoinResult(topic=result["topic"], member=result["member"])

    def resume_topic(
        self,
        topic: str,
        *,
        display_name: str,
        chat_identity_id: str,
        resume_secret: str,
        client_kind: str,
        workspace_path: str | None = None,
        metadata: dict[str, Any] | None = None,
        allow_create: bool = False,
    ) -> TopicJoinResult:
        payload: dict[str, Any] = {
            "display_name": display_name,
            "chat_identity_id": chat_identity_id,
            "resume_secret": resume_secret,
            "client_kind": client_kind,
            "allow_create": allow_create,
        }
        if workspace_path:
            payload["workspace_path"] = workspace_path
        if metadata:
            payload["metadata"] = metadata
        result = self._json("POST", f"/topics/{topic}/resume", json=payload)
        return TopicJoinResult(topic=result["topic"], member=result["member"])

    def leave_topic(self, topic: str, *, member_id: str) -> dict[str, Any]:
        return self._json("POST", f"/topics/{topic}/leave", json={"member_id": member_id})

    def list_members(self, topic: str) -> dict[str, Any]:
        return self._json("GET", f"/topics/{topic}/members")

    def touch_member(self, topic: str, *, member_id: str) -> dict[str, Any]:
        return self._json("POST", f"/topics/{topic}/heartbeat", json={"member_id": member_id})

    def history(self, topic: str, *, limit: int = 100) -> dict[str, Any]:
        return self._json("GET", f"/topics/{topic}/history?limit={limit}")

    def events(self, topic: str, *, after_id: str | None = None, limit: int = 200) -> dict[str, Any]:
        path = f"/topics/{topic}/events?limit={limit}"
        if after_id:
            path = f"{path}&after_id={after_id}"
        return self._json("GET", path)

    def send_message(
        self,
        topic: str,
        *,
        member_id: str,
        message_type: str,
        body: str,
        audience: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "member_id": member_id,
            "type": message_type,
            "body": body,
        }
        if audience is not None:
            payload["audience"] = audience
        if metadata is not None:
            payload["metadata"] = metadata
        return self._json("POST", f"/topics/{topic}/messages", json=payload)

    def claim_master(self, topic: str, *, member_id: str, single_use_password: str) -> dict[str, Any]:
        return self._json(
            "POST",
            f"/topics/{topic}/admin/exchange",
            json={"member_id": member_id, "single_use_password": single_use_password},
        )

    def release_master(self, topic: str, *, member_id: str) -> dict[str, Any]:
        return self._json("POST", f"/topics/{topic}/master/release", json={"member_id": member_id})

    def regenerate_admin_secret(self, topic: str) -> dict[str, Any]:
        return self._json("POST", f"/topics/{topic}/admin/regenerate", json={})

    def list_docs(self, topic: str, *, include_content: bool = False) -> dict[str, Any]:
        suffix = "?include_content=1" if include_content else ""
        return self._json("GET", f"/topics/{topic}/docs{suffix}")

    def sync_documents(self, topic: str, *, member_id: str, documents: list[dict[str, Any]]) -> dict[str, Any]:
        return self._json("POST", f"/topics/{topic}/docs", json={"member_id": member_id, "documents": documents})

    def list_final_artifacts(self, topic: str) -> dict[str, Any]:
        return self._json("GET", f"/topics/{topic}/final-artifacts")

    def upload_final_artifacts(
        self,
        topic: str,
        *,
        member_id: str,
        flag: str,
        writeup_path: Path,
        solver_paths: list[Path],
        handoff_paths: list[Path] | None = None,
    ) -> dict[str, Any]:
        files: list[tuple[str, tuple[str, Any, str]]] = []
        opened: list[Any] = []
        try:
            writeup_handle = writeup_path.open("rb")
            opened.append(writeup_handle)
            files.append(("writeup", (writeup_path.name, writeup_handle, "text/markdown")))
            for solver_path in solver_paths:
                solver_handle = solver_path.open("rb")
                opened.append(solver_handle)
                files.append(("solver", (solver_path.name, solver_handle, "application/octet-stream")))
            for handoff_path in handoff_paths or []:
                handoff_handle = handoff_path.open("rb")
                opened.append(handoff_handle)
                files.append(("handoff", (handoff_path.name, handoff_handle, "application/octet-stream")))
            response = self._request(
                "POST",
                f"/topics/{topic}/final-artifacts",
                data={"member_id": member_id, "flag": flag},
                files=files,
            )
            payload = response.json()
            if not isinstance(payload, dict):
                raise ConstellationAPIError(f"Unexpected upload payload: {payload!r}")
            return payload
        finally:
            for handle in opened:
                handle.close()

    def build_ws_url(
        self,
        *,
        topic: str,
        member_id: str,
        client_kind: str,
        display_name: str,
        session_epoch: int | None = None,
    ) -> str:
        base = self.settings.ws_base_url.rstrip("/")
        payload = {
            "topic": topic,
            "member_id": member_id,
            "client_kind": client_kind,
            "display_name": display_name,
        }
        if session_epoch is not None:
            payload["session_epoch"] = session_epoch
        query = urlencode(payload)
        return f"{base}/ws?{query}"

    def build_runtime_ws_url(self) -> str:
        base = self.settings.ws_base_url.rstrip("/")
        return f"{base}/runtime/ws"

    def build_ws_headers(self) -> list[str]:
        if not self.settings.token:
            return []
        return [f"Authorization: Bearer {self.settings.token}"]

    def build_ws_subprotocols(self) -> list[str]:
        protocols = ["opencrow.constellation.v1"]
        if self.settings.token:
            token = base64.urlsafe_b64encode(self.settings.token.encode("utf-8")).decode("ascii").rstrip("=")
            protocols.append(f"auth.{token}")
        return protocols

    @staticmethod
    def format_handoff_urls(raw_value: str) -> list[str]:
        values = [line.strip() for line in raw_value.splitlines()]
        return [value for value in values if value]

    @staticmethod
    def pretty_json(payload: dict[str, Any]) -> str:
        return json.dumps(payload, indent=2, sort_keys=True)
