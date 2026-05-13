"""Tornado backend for OpenCROW Constellation."""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import threading
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

import tornado.ioloop
import tornado.httputil
import tornado.web
import tornado.websocket

from .config import BackendSettings, load_backend_settings
from .storage import ConstellationStorage


@dataclass
class AppState:
    settings: BackendSettings
    storage: ConstellationStorage
    runtime_sockets: dict[str, Any] = field(default_factory=dict)

    def attach_runtime(self, runtime_id: str, socket: Any) -> None:
        self.runtime_sockets[runtime_id] = socket

    def detach_runtime(self, runtime_id: str, socket: Any) -> None:
        if self.runtime_sockets.get(runtime_id) is socket:
            self.runtime_sockets.pop(runtime_id, None)

    def deliver_runtime_command(self, command: dict[str, Any]) -> bool:
        runtime_id = str(command.get("runtime_id") or "")
        socket = self.runtime_sockets.get(runtime_id)
        if socket is None or getattr(socket, "ws_connection", None) is None:
            return False
        socket.write_message(json.dumps({"event_type": "command", "command": command}))
        return True


def _normalize_handoff_urls(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    return []


class BaseHandler(tornado.web.RequestHandler):
    app_state: AppState

    def initialize(self, app_state: AppState) -> None:
        self.app_state = app_state

    def set_default_headers(self) -> None:
        self.set_header("Content-Type", "application/json")

    def _token(self) -> str:
        header = self.request.headers.get("Authorization", "").strip()
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        query_token = self.get_query_argument("token", default="").strip()
        return query_token

    def _ui_request_allowed(self) -> bool:
        expected = self.app_state.settings.ui_shared_secret
        if not expected:
            return False
        supplied = self.request.headers.get("X-Constellation-UI-Auth", "").strip()
        return bool(supplied) and secrets.compare_digest(supplied, expected)

    def _normalized_client_kind(self, raw_kind: object) -> str:
        requested = str(raw_kind or "agent").strip().lower() or "agent"
        if requested == "ui" and self._ui_request_allowed():
            return "ui"
        return "agent"

    def prepare(self) -> None:
        if self.request.path == "/api/v1/health":
            return
        if not self.app_state.storage.validate_system_token(self._token()):
            self.set_status(401)
            self.finish({"ok": False, "error": "Unauthorized"})
            raise tornado.web.Finish()

    def write_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        self.set_status(status)
        self.finish(json.dumps(payload, indent=2, sort_keys=True))

    def read_json_body(self) -> dict[str, Any]:
        if not self.request.body:
            return {}
        try:
            payload = json.loads(self.request.body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise tornado.web.HTTPError(400, reason=f"Invalid JSON body: {exc}") from exc
        if not isinstance(payload, dict):
            raise tornado.web.HTTPError(400, reason="JSON body must be an object.")
        return payload

    def write_error(self, status_code: int, **kwargs: Any) -> None:
        reason = self._reason
        self.finish(json.dumps({"ok": False, "error": reason, "status_code": status_code}, indent=2, sort_keys=True))


class HealthHandler(BaseHandler):
    def get(self) -> None:
        self.write_json({"ok": True, "status": "ready"})


class AuthValidateHandler(BaseHandler):
    def get(self) -> None:
        token = self._token()
        preview = f"{token[:4]}..." if len(token) >= 4 else token
        self.write_json(
            {
                "ok": True,
                "auth_mode": "system_token",
                "token_preview": preview,
            }
        )


class TopicCollectionHandler(BaseHandler):
    def get(self) -> None:
        self.write_json({"ok": True, "topics": self.app_state.storage.list_topics()})

    def post(self) -> None:
        payload = self.read_json_body()
        title = str(payload.get("title", "")).strip()
        if not title:
            raise tornado.web.HTTPError(400, reason="`title` is required.")
        description = str(payload.get("description", "")).strip()
        category = str(payload.get("category", "misc")).strip() or "misc"
        handoff_urls = _normalize_handoff_urls(payload.get("handoff_urls"))
        slug = payload.get("slug")
        try:
            topic, admin_secret = self.app_state.storage.create_topic(
                title=title,
                description=description,
                category=category,
                handoff_urls=handoff_urls,
                slug=str(slug).strip() if slug else None,
                created_by="api",
            )
        except ValueError as exc:
            raise tornado.web.HTTPError(409, reason=str(exc)) from exc
        self.write_json({"ok": True, "topic": topic, "single_use_password": admin_secret}, status=201)


class RuntimeCollectionHandler(BaseHandler):
    def get(self) -> None:
        self.write_json({"ok": True, "runtimes": self.app_state.storage.list_runtimes()})


class ChallengeCollectionHandler(BaseHandler):
    def get(self) -> None:
        self.write_json({"ok": True, "challenges": self.app_state.storage.list_challenges()})

    def post(self) -> None:
        payload = self.read_json_body()
        title = str(payload.get("title", "")).strip()
        if not title:
            raise tornado.web.HTTPError(400, reason="`title` is required.")
        handoff_urls = _normalize_handoff_urls(payload.get("handoff_urls"))
        try:
            challenge, agent, command = self.app_state.storage.create_challenge(
                title=title,
                description=str(payload.get("description", "")).strip(),
                category=str(payload.get("category", "misc")).strip() or "misc",
                challenge_type=str(payload.get("challenge_type", "single_agent")).strip() or "single_agent",
                runtime_id=str(payload.get("runtime_id", "")).strip() or None,
                handoff_urls=handoff_urls,
                settings=payload.get("settings") if isinstance(payload.get("settings"), dict) else None,
                slug=str(payload.get("slug", "")).strip() or None,
                metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
                start_agent=bool(payload.get("start_agent", True)),
            )
        except RuntimeError as exc:
            raise tornado.web.HTTPError(409, reason=str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise tornado.web.HTTPError(400, reason=str(exc)) from exc
        delivered = self.app_state.deliver_runtime_command(command) if command else False
        self.write_json(
            {
                "ok": True,
                "challenge": challenge,
                "agent": agent,
                "command": command,
                "delivered": delivered,
            },
            status=201,
        )


class ChallengeItemHandler(BaseHandler):
    def get(self, challenge_id: str) -> None:
        challenge = self.app_state.storage.get_challenge(challenge_id)
        if challenge is None:
            raise tornado.web.HTTPError(404, reason=f"Unknown challenge: {challenge_id}")
        self.write_json({"ok": True, "challenge": challenge})


class ChallengeConvertHandler(BaseHandler):
    def post(self, challenge_id: str) -> None:
        challenge = self.app_state.storage.get_challenge(challenge_id)
        if challenge is None:
            raise tornado.web.HTTPError(404, reason=f"Unknown challenge: {challenge_id}")
        updated, master = self.app_state.storage.convert_challenge_to_constellation(challenge["id"])
        self.write_json({"ok": True, "challenge": updated, "master_agent": master})


class ChallengeFilesHandler(BaseHandler):
    def get(self, challenge_id: str) -> None:
        challenge = self.app_state.storage.get_challenge(challenge_id)
        if challenge is None:
            raise tornado.web.HTTPError(404, reason=f"Unknown challenge: {challenge_id}")
        self.write_json({"ok": True, "files": self.app_state.storage.list_challenge_files(challenge["id"])})

    def post(self, challenge_id: str) -> None:
        challenge = self.app_state.storage.get_challenge(challenge_id)
        if challenge is None:
            raise tornado.web.HTTPError(404, reason=f"Unknown challenge: {challenge_id}")
        uploaded: list[dict[str, Any]] = []
        for parts in self.request.files.values():
            for part in parts:
                uploaded.append(
                    self.app_state.storage.add_challenge_file(
                        challenge["id"],
                        filename=part.get("filename", "upload.bin"),
                        data=part.get("body", b""),
                        content_type=part.get("content_type"),
                    )
                )
        if not uploaded:
            raise tornado.web.HTTPError(400, reason="At least one uploaded file is required.")
        self.write_json({"ok": True, "files": uploaded}, status=201)


class ChallengeAgentsHandler(BaseHandler):
    def get(self, challenge_id: str) -> None:
        challenge = self.app_state.storage.get_challenge(challenge_id)
        if challenge is None:
            raise tornado.web.HTTPError(404, reason=f"Unknown challenge: {challenge_id}")
        self.write_json({"ok": True, "agents": self.app_state.storage.list_agents(challenge["id"])})

    def post(self, challenge_id: str) -> None:
        payload = self.read_json_body()
        challenge = self.app_state.storage.get_challenge(challenge_id)
        if challenge is None:
            raise tornado.web.HTTPError(404, reason=f"Unknown challenge: {challenge_id}")
        role = str(payload.get("role", "slave")).strip() or "slave"
        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            prompt = self.app_state.storage.default_agent_prompt(challenge, role=role)
        require_approval = bool(payload.get("require_approval", False))
        agent = self.app_state.storage.create_agent(
            challenge["id"],
            role=role,
            display_name=str(payload.get("display_name", "")).strip() or f"{challenge['title']} {role}",
            prompt=prompt,
            runtime_id=str(payload.get("runtime_id", "")).strip() or challenge.get("runtime_id"),
            model=str(payload.get("model", "")).strip() or None,
            metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
            require_approval=require_approval,
        )
        command = None
        delivered = False
        if not require_approval:
            command = self.app_state.storage.queue_runtime_command(
                agent["runtime_id"],
                command_type="spawn_agent",
                challenge_id=challenge["id"],
                agent_id=agent["id"],
                payload={"challenge": challenge, "agent": agent, "files": self.app_state.storage.list_challenge_files(challenge["id"])},
            )
            delivered = self.app_state.deliver_runtime_command(command)
        self.write_json({"ok": True, "agent": agent, "command": command, "delivered": delivered}, status=201)


class AgentItemHandler(BaseHandler):
    def get(self, agent_id: str) -> None:
        agent = self.app_state.storage.get_agent(agent_id)
        if agent is None:
            raise tornado.web.HTTPError(404, reason=f"Unknown agent: {agent_id}")
        self.write_json({"ok": True, "agent": agent})


class AgentEventsHandler(BaseHandler):
    def get(self, agent_id: str) -> None:
        limit = int(self.get_query_argument("limit", "200"))
        agent = self.app_state.storage.get_agent(agent_id)
        if agent is None:
            raise tornado.web.HTTPError(404, reason=f"Unknown agent: {agent_id}")
        self.write_json({"ok": True, "events": self.app_state.storage.list_agent_events(agent_id=agent["id"], limit=limit)})


class AgentPromptHandler(BaseHandler):
    def post(self, agent_id: str) -> None:
        payload = self.read_json_body()
        body = str(payload.get("body", "")).strip()
        if not body:
            raise tornado.web.HTTPError(400, reason="`body` is required.")
        agent = self.app_state.storage.get_agent(agent_id)
        if agent is None:
            raise tornado.web.HTTPError(404, reason=f"Unknown agent: {agent_id}")
        command = self.app_state.storage.queue_runtime_command(
            agent["runtime_id"],
            command_type="prompt_agent",
            challenge_id=agent["challenge_id"],
            agent_id=agent["id"],
            payload={"body": body},
        )
        delivered = self.app_state.deliver_runtime_command(command)
        self.write_json({"ok": True, "command": command, "delivered": delivered}, status=201)


class AgentInterruptHandler(BaseHandler):
    def post(self, agent_id: str) -> None:
        agent = self.app_state.storage.get_agent(agent_id)
        if agent is None:
            raise tornado.web.HTTPError(404, reason=f"Unknown agent: {agent_id}")
        command = self.app_state.storage.queue_runtime_command(
            agent["runtime_id"],
            command_type="interrupt_agent",
            challenge_id=agent["challenge_id"],
            agent_id=agent["id"],
            payload={},
        )
        delivered = self.app_state.deliver_runtime_command(command)
        self.write_json({"ok": True, "command": command, "delivered": delivered}, status=201)


class ChallengeEventsHandler(BaseHandler):
    def get(self, challenge_id: str) -> None:
        limit = int(self.get_query_argument("limit", "200"))
        challenge = self.app_state.storage.get_challenge(challenge_id)
        if challenge is None:
            raise tornado.web.HTTPError(404, reason=f"Unknown challenge: {challenge_id}")
        self.write_json({"ok": True, "events": self.app_state.storage.list_agent_events(challenge_id=challenge["id"], limit=limit)})


class TopicItemHandler(BaseHandler):
    def get(self, topic: str) -> None:
        payload = self.app_state.storage.get_topic(topic)
        if payload is None:
            raise tornado.web.HTTPError(404, reason=f"Unknown topic: {topic}")
        self.write_json({"ok": True, "topic": payload})

    def patch(self, topic: str) -> None:
        payload = self.read_json_body()
        try:
            updated = self.app_state.storage.update_topic(
                topic,
                title=payload.get("title"),
                description=payload.get("description"),
                category=payload.get("category"),
                handoff_urls=_normalize_handoff_urls(payload.get("handoff_urls")) if "handoff_urls" in payload else None,
            )
        except KeyError as exc:
            raise tornado.web.HTTPError(404, reason=f"Unknown topic: {topic}") from exc
        self.write_json({"ok": True, "topic": updated})

    def delete(self, topic: str) -> None:
        try:
            result = self.app_state.storage.delete_topic(topic, deleted_by="api")
        except KeyError as exc:
            raise tornado.web.HTTPError(404, reason=f"Unknown topic: {topic}") from exc
        self.write_json({"ok": True, **result})


class TopicHistoryHandler(BaseHandler):
    def get(self, topic: str) -> None:
        limit = int(self.get_query_argument("limit", "100"))
        self.write_json({"ok": True, "history": self.app_state.storage.history(topic, limit=limit)})


class TopicEventsHandler(BaseHandler):
    def get(self, topic: str) -> None:
        limit = int(self.get_query_argument("limit", "200"))
        after_id = self.get_query_argument("after_id", "").strip() or None
        try:
            events = self.app_state.storage.list_broker_events(topic, after_id=after_id, limit=limit)
        except ValueError as exc:
            raise tornado.web.HTTPError(400, reason=str(exc)) from exc
        self.write_json({"ok": True, "events": events})


class TopicMembersHandler(BaseHandler):
    def get(self, topic: str) -> None:
        self.write_json({"ok": True, "members": self.app_state.storage.list_members(topic)})


class TopicJoinHandler(BaseHandler):
    def post(self, topic: str) -> None:
        payload = self.read_json_body()
        display_name = str(payload.get("display_name", "")).strip()
        if not display_name:
            raise tornado.web.HTTPError(400, reason="`display_name` is required.")
        client_kind = self._normalized_client_kind(payload.get("client_kind", "agent"))
        workspace_path = payload.get("workspace_path")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        try:
            member = self.app_state.storage.create_member(
                topic=topic,
                display_name=display_name,
                client_kind=client_kind,
                workspace_path=str(workspace_path) if workspace_path else None,
                metadata=metadata,
                master_capability=(client_kind == "ui"),
            )
        except KeyError as exc:
            raise tornado.web.HTTPError(404, reason=f"Unknown topic: {topic}") from exc
        topic_payload = self.app_state.storage.get_topic(topic)
        assert topic_payload is not None
        self.write_json({"ok": True, "topic": topic_payload, "member": member}, status=201)


class TopicResumeHandler(BaseHandler):
    def post(self, topic: str) -> None:
        payload = self.read_json_body()
        display_name = str(payload.get("display_name", "")).strip()
        chat_identity_id = str(payload.get("chat_identity_id", "")).strip()
        resume_secret = str(payload.get("resume_secret", "")).strip()
        if not display_name or not chat_identity_id or not resume_secret:
            raise tornado.web.HTTPError(400, reason="`display_name`, `chat_identity_id`, and `resume_secret` are required.")
        client_kind = self._normalized_client_kind(payload.get("client_kind", "agent"))
        workspace_path = payload.get("workspace_path")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        allow_create = bool(payload.get("allow_create", False))
        try:
            member = self.app_state.storage.resume_member(
                topic,
                chat_identity_id=chat_identity_id,
                resume_secret=resume_secret,
                display_name=display_name,
                client_kind=client_kind,
                workspace_path=str(workspace_path) if workspace_path else None,
                metadata=metadata,
                allow_create=allow_create,
            )
        except KeyError as exc:
            raise tornado.web.HTTPError(404, reason=f"Unknown topic or identity: {topic}/{chat_identity_id}") from exc
        except PermissionError as exc:
            raise tornado.web.HTTPError(403, reason=str(exc)) from exc
        except ValueError as exc:
            raise tornado.web.HTTPError(400, reason=str(exc)) from exc
        topic_payload = self.app_state.storage.get_topic(topic)
        assert topic_payload is not None
        self.write_json({"ok": True, "topic": topic_payload, "member": member})


class TopicLeaveHandler(BaseHandler):
    def post(self, topic: str) -> None:
        payload = self.read_json_body()
        member_id = str(payload.get("member_id", "")).strip()
        if not member_id:
            raise tornado.web.HTTPError(400, reason="`member_id` is required.")
        try:
            result = self.app_state.storage.remove_member(topic, member_id)
        except KeyError as exc:
            raise tornado.web.HTTPError(404, reason=f"Unknown member or topic: {member_id}") from exc
        self.write_json({"ok": True, **result})


class TopicHeartbeatHandler(BaseHandler):
    def post(self, topic: str) -> None:
        payload = self.read_json_body()
        member_id = str(payload.get("member_id", "")).strip()
        if not member_id:
            raise tornado.web.HTTPError(400, reason="`member_id` is required.")
        try:
            member = self.app_state.storage.touch_member(member_id)
        except KeyError as exc:
            raise tornado.web.HTTPError(404, reason=f"Unknown member: {member_id}") from exc
        if member["topic"] != topic:
            raise tornado.web.HTTPError(404, reason=f"Member {member_id} is not part of topic {topic}")
        self.write_json({"ok": True, "member": member})


class TopicAdminExchangeHandler(BaseHandler):
    def post(self, topic: str) -> None:
        payload = self.read_json_body()
        member_id = str(payload.get("member_id", "")).strip()
        single_use_password = str(payload.get("single_use_password", "")).strip()
        if not member_id or not single_use_password:
            raise tornado.web.HTTPError(400, reason="`member_id` and `single_use_password` are required.")
        try:
            member = self.app_state.storage.exchange_admin_token(topic, member_id, single_use_password)
        except KeyError as exc:
            raise tornado.web.HTTPError(404, reason=f"Unknown member or topic: {member_id}") from exc
        except PermissionError as exc:
            raise tornado.web.HTTPError(403, reason=str(exc)) from exc
        self.write_json({"ok": True, "member": member})


class TopicAdminRegenerateHandler(BaseHandler):
    def post(self, topic: str) -> None:
        try:
            admin_secret = self.app_state.storage.regenerate_admin_secret(topic)
        except KeyError as exc:
            raise tornado.web.HTTPError(404, reason=f"Unknown topic: {topic}") from exc
        self.write_json({"ok": True, "topic": topic, "single_use_password": admin_secret})


class TopicMasterReleaseHandler(BaseHandler):
    def post(self, topic: str) -> None:
        payload = self.read_json_body()
        member_id = str(payload.get("member_id", "")).strip()
        if not member_id:
            raise tornado.web.HTTPError(400, reason="`member_id` is required.")
        try:
            member = self.app_state.storage.release_master(topic, member_id)
        except KeyError as exc:
            raise tornado.web.HTTPError(404, reason=f"Unknown member or topic: {member_id}") from exc
        self.write_json({"ok": True, "member": member})


class TopicMessagesHandler(BaseHandler):
    def post(self, topic: str) -> None:
        payload = self.read_json_body()
        member_id = str(payload.get("member_id", "")).strip()
        message_type = str(payload.get("type", "")).strip()
        body = str(payload.get("body", "")).strip()
        if not member_id or not message_type or not body:
            raise tornado.web.HTTPError(400, reason="`member_id`, `type`, and `body` are required.")
        audience = payload.get("audience") if isinstance(payload.get("audience"), dict) else None
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None
        try:
            message = self.app_state.storage.send_message(
                topic,
                member_id=member_id,
                message_type=message_type,
                body=body,
                audience=audience,
                metadata=metadata,
            )
        except KeyError as exc:
            raise tornado.web.HTTPError(404, reason=f"Unknown member or topic: {member_id}") from exc
        except PermissionError as exc:
            raise tornado.web.HTTPError(403, reason=str(exc)) from exc
        except ValueError as exc:
            raise tornado.web.HTTPError(400, reason=str(exc)) from exc
        self.write_json({"ok": True, "message": message}, status=201)


class TopicDocsHandler(BaseHandler):
    def get(self, topic: str) -> None:
        include_content = self.get_query_argument("include_content", "0").strip().lower() in {"1", "true", "yes"}
        self.write_json({"ok": True, "documents": self.app_state.storage.list_documents(topic, include_content=include_content)})

    def post(self, topic: str) -> None:
        payload = self.read_json_body()
        member_id = str(payload.get("member_id", "")).strip()
        if not member_id:
            raise tornado.web.HTTPError(400, reason="`member_id` is required.")
        documents = payload.get("documents")
        if not isinstance(documents, list):
            single_path = payload.get("relative_path")
            single_content = payload.get("content")
            single_sha = payload.get("sha256")
            if single_path is None or single_content is None or single_sha is None:
                raise tornado.web.HTTPError(400, reason="`documents` or single document fields are required.")
            documents = [{"relative_path": single_path, "content": single_content, "sha256": single_sha}]
        try:
            synced = self.app_state.storage.sync_documents(topic, member_id=member_id, documents=documents)
        except KeyError as exc:
            raise tornado.web.HTTPError(404, reason=f"Unknown member or topic: {member_id}") from exc
        self.write_json({"ok": True, "documents": synced}, status=201)


class TopicFinalArtifactsHandler(BaseHandler):
    def get(self, topic: str) -> None:
        self.write_json({"ok": True, "artifacts": self.app_state.storage.list_final_artifacts(topic)})

    def post(self, topic: str) -> None:
        member_id = self.get_body_argument("member_id", "").strip()
        flag = self.get_body_argument("flag", "").strip()
        if not member_id or not flag:
            raise tornado.web.HTTPError(400, reason="`member_id` and `flag` are required.")
        writeup_parts = self.request.files.get("writeup", [])
        solver_parts = self.request.files.get("solver", [])
        handoff_parts = self.request.files.get("handoff", [])
        if not writeup_parts:
            raise tornado.web.HTTPError(400, reason="A `writeup` upload is required.")
        if not solver_parts:
            raise tornado.web.HTTPError(400, reason="At least one `solver` upload is required.")
        writeup_part = writeup_parts[0]
        solver_files = [(item["filename"], item["body"]) for item in solver_parts]
        handoff_files = [(item["filename"], item["body"]) for item in handoff_parts]
        try:
            artifact = self.app_state.storage.upload_final_artifacts(
                topic,
                member_id=member_id,
                flag=flag,
                writeup_name=writeup_part["filename"],
                writeup_bytes=writeup_part["body"],
                solver_files=solver_files,
                handoff_files=handoff_files,
            )
        except KeyError as exc:
            raise tornado.web.HTTPError(404, reason=f"Unknown member or topic: {member_id}") from exc
        self.write_json({"ok": True, "artifact": artifact}, status=201)


class FileDownloadHandler(BaseHandler):
    def get(self, file_id: str) -> None:
        try:
            data, metadata = self.app_state.storage.download_file(file_id)
        except Exception as exc:
            raise tornado.web.HTTPError(404, reason=f"Unknown file: {file_id}") from exc
        content_type = str(metadata.get("content_type", "application/octet-stream"))
        filename = _safe_download_filename(str(metadata.get("filename", file_id)))
        self.set_header("Content-Type", content_type)
        self.set_header(
            "Content-Disposition",
            f"attachment; filename={json.dumps(filename)}; filename*=UTF-8''{quote(filename)}",
        )
        self.finish(data)


class ChallengeFileDownloadHandler(BaseHandler):
    def get(self, file_id: str) -> None:
        try:
            data, metadata = self.app_state.storage.download_challenge_file(file_id)
        except Exception as exc:
            raise tornado.web.HTTPError(404, reason=f"Unknown challenge file: {file_id}") from exc
        content_type = str(metadata.get("content_type", "application/octet-stream"))
        filename = _safe_download_filename(str(metadata.get("filename", file_id)))
        self.set_header("Content-Type", content_type)
        self.set_header(
            "Content-Disposition",
            f"attachment; filename={json.dumps(filename)}; filename*=UTF-8''{quote(filename)}",
        )
        self.finish(data)


class RuntimeControlWebSocket(tornado.websocket.WebSocketHandler):
    def initialize(self, app_state: AppState) -> None:
        self.app_state = app_state
        self.runtime_id = ""

    def check_origin(self, origin: str) -> bool:
        return True

    def select_subprotocol(self, subprotocols: list[str]) -> str | None:
        if "opencrow.runtime.v1" in subprotocols:
            return "opencrow.runtime.v1"
        return None

    def open(self) -> None:
        token = _extract_websocket_token(self.request)
        if not self.app_state.storage.validate_system_token(token):
            self.close(code=4001, reason="Unauthorized")
            return
        self.write_message(json.dumps({"event_type": "hello", "protocol": "opencrow.runtime.v1"}))

    def on_message(self, message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            self.write_message(json.dumps({"event_type": "error", "error": "Invalid JSON payload"}))
            return
        action = str(payload.get("action", "")).strip()
        try:
            if action == "register":
                runtime = self.app_state.storage.register_runtime(
                    runtime_id=str(payload.get("runtime_id", "")).strip(),
                    display_name=str(payload.get("display_name", "")).strip(),
                    capabilities=payload.get("capabilities") if isinstance(payload.get("capabilities"), dict) else None,
                    workspace_root=str(payload.get("workspace_root", "")).strip() or None,
                    metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
                )
                self.runtime_id = runtime["runtime_id"]
                self.app_state.attach_runtime(self.runtime_id, self)
                self.write_message(json.dumps({"event_type": "registered", "runtime": runtime}))
                for command in self.app_state.storage.list_runtime_commands(self.runtime_id, status="queued"):
                    self.write_message(json.dumps({"event_type": "command", "command": command}))
                return
            if not self.runtime_id:
                self.write_message(json.dumps({"event_type": "error", "error": "Runtime must register first."}))
                return
            if action == "heartbeat":
                runtime = self.app_state.storage.touch_runtime(self.runtime_id)
                self.write_message(json.dumps({"event_type": "heartbeat", "runtime": runtime}))
                return
            if action == "command_status":
                command = self.app_state.storage.update_runtime_command(
                    str(payload.get("command_id", "")),
                    status=str(payload.get("status", "")).strip() or "running",
                    error=str(payload.get("error", "")).strip() or None,
                )
                self.write_message(json.dumps({"event_type": "command_status", "command": command}))
                return
            if action == "agent_state":
                agent = self.app_state.storage.update_agent_state(
                    str(payload.get("agent_id", "")),
                    status=str(payload.get("status", "")).strip() or None,
                    codex_thread_id=str(payload.get("codex_thread_id", "")).strip() or None,
                    workspace_path=str(payload.get("workspace_path", "")).strip() or None,
                    last_response=str(payload.get("last_response", "")).strip() or None,
                    metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
                )
                self.write_message(json.dumps({"event_type": "agent_state", "agent": agent}))
                return
            if action == "agent_event":
                event = self.app_state.storage.record_agent_event(
                    str(payload.get("challenge_id", "")).strip(),
                    agent_id=str(payload.get("agent_id", "")).strip() or None,
                    runtime_id=self.runtime_id,
                    event_type=str(payload.get("event_type", "runtime_event")).strip() or "runtime_event",
                    payload=payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
                )
                self.write_message(json.dumps({"event_type": "agent_event", "event": event}))
                return
        except Exception as exc:
            self.write_message(json.dumps({"event_type": "error", "error": str(exc)}))
            return
        self.write_message(json.dumps({"event_type": "error", "error": f"Unsupported action: {action}"}))

    def on_close(self) -> None:
        if self.runtime_id:
            self.app_state.detach_runtime(self.runtime_id, self)
            self.app_state.storage.mark_runtime_offline(self.runtime_id)


class ConstellationWebSocket(tornado.websocket.WebSocketHandler):
    def initialize(self, app_state: AppState) -> None:
        self.app_state = app_state
        self.stop_event = threading.Event()
        self.watch_thread: threading.Thread | None = None
        self.topic = ""
        self.member_id = ""
        self.session_epoch: int | None = None
        self.io_loop: tornado.ioloop.IOLoop | None = None

    def check_origin(self, origin: str) -> bool:
        header = self.request.headers.get("Authorization", "").strip()
        if header.lower().startswith("bearer "):
            return True
        if not origin:
            return True
        allowed = self.app_state.settings.allowed_ws_origins
        if not allowed:
            return False
        return _normalize_origin(origin) in allowed

    def select_subprotocol(self, subprotocols: list[str]) -> str | None:
        if "opencrow.constellation.v1" in subprotocols:
            return "opencrow.constellation.v1"
        return None

    def open(self) -> None:
        token = _extract_websocket_token(self.request)
        if not self.app_state.storage.validate_system_token(token):
            self.close(code=4001, reason="Unauthorized")
            return
        self.topic = self.get_argument("topic", default="").strip()
        self.member_id = self.get_argument("member_id", default="").strip()
        session_epoch_raw = self.get_argument("session_epoch", default="").strip()
        if not self.topic or not self.member_id:
            self.close(code=4002, reason="`topic` and `member_id` are required.")
            return
        if session_epoch_raw:
            try:
                self.session_epoch = int(session_epoch_raw)
            except ValueError:
                self.close(code=4002, reason="`session_epoch` must be an integer.")
                return
        try:
            member = self.app_state.storage.get_member(self.member_id)
        except Exception:
            member = None
        if member is None or member["topic"] != self.topic:
            self.close(code=4004, reason="Unknown topic member.")
            return
        if self.session_epoch is not None and int(member.get("session_epoch", 0)) != self.session_epoch:
            self.close(code=4006, reason="Session superseded")
            return
        self.io_loop = tornado.ioloop.IOLoop.current()
        self.app_state.storage.touch_member(self.member_id)
        self.watch_thread = threading.Thread(target=self._watch_events, daemon=True)
        self.watch_thread.start()
        self.write_message(json.dumps({"event_type": "connected", "topic": self.topic, "member_id": self.member_id}))

    def on_message(self, message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            self.write_message(json.dumps({"event_type": "error", "error": "Invalid JSON payload"}))
            return
        action = str(payload.get("action", "ping")).strip()
        current_member = self.app_state.storage.get_member(self.member_id)
        if current_member is None:
            self.close(code=4004, reason="Unknown topic member.")
            return
        if self.session_epoch is not None and int(current_member.get("session_epoch", 0)) != self.session_epoch:
            self.close(code=4006, reason="Session superseded")
            return
        if action == "ping":
            self.write_message(json.dumps({"event_type": "pong"}))
            return
        if action == "heartbeat":
            try:
                member = self.app_state.storage.touch_member(self.member_id)
            except KeyError:
                self.write_message(json.dumps({"event_type": "error", "error": "Unknown member"}))
                return
            self.write_message(json.dumps({"event_type": "heartbeat", "member": member}))
            return
        if action == "send":
            try:
                message_payload = self.app_state.storage.send_message(
                    self.topic,
                    member_id=self.member_id,
                    message_type=str(payload.get("type", "chat_message")),
                    body=str(payload.get("body", "")),
                    audience=payload.get("audience") if isinstance(payload.get("audience"), dict) else None,
                    metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
                )
            except (KeyError, PermissionError, ValueError) as exc:
                self.write_message(json.dumps({"event_type": "error", "error": str(exc)}))
                return
            self.write_message(json.dumps({"event_type": "ack", "payload": message_payload}))
            return
        self.write_message(json.dumps({"event_type": "error", "error": f"Unsupported action: {action}"}))

    def on_close(self) -> None:
        self.stop_event.set()

    def _watch_events(self) -> None:
        try:
            for event in self.app_state.storage.watch_events(self.topic, stop_event=self.stop_event):
                if self.stop_event.is_set():
                    break
                if self.io_loop is not None:
                    self.io_loop.add_callback(self._emit_event, event)
                if event["event_type"] == "topic_deleted":
                    break
        except Exception as exc:  # pragma: no cover - defensive websocket path
            if self.io_loop is not None:
                self.io_loop.add_callback(self._emit_event, {"event_type": "error", "payload": {"error": str(exc)}})

    def _emit_event(self, event: dict[str, Any]) -> None:
        if self.ws_connection is None:
            return
        current_member = self.app_state.storage.get_member(self.member_id)
        if current_member is None:
            self.close(code=4004, reason="Unknown topic member.")
            return
        if self.session_epoch is not None and int(current_member.get("session_epoch", 0)) != self.session_epoch:
            self.close(code=4006, reason="Session superseded")
            return
        self.write_message(json.dumps(event))
        if event.get("event_type") == "topic_deleted":
            self.close(code=4005, reason="Topic deleted")


def _normalize_origin(origin: str) -> str:
    parts = urlsplit(origin.strip())
    if not parts.scheme or not parts.netloc:
        return origin.strip().rstrip("/")
    return f"{parts.scheme}://{parts.netloc}".rstrip("/")


def _decode_ws_token(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii")).decode("utf-8")


def _extract_websocket_token(request: tornado.httputil.HTTPServerRequest) -> str:
    header = request.headers.get("Authorization", "").strip()
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    raw_protocols = request.headers.get("Sec-WebSocket-Protocol", "")
    for protocol in raw_protocols.split(","):
        candidate = protocol.strip()
        if candidate.startswith("auth."):
            encoded = candidate[5:]
            if not encoded:
                continue
            try:
                return _decode_ws_token(encoded)
            except Exception:
                continue
    return request.query_arguments.get("token", [b""])[0].decode("utf-8", errors="ignore").strip()


def _safe_download_filename(raw_value: str) -> str:
    cleaned = "".join(ch for ch in raw_value if 32 <= ord(ch) <= 126 and ch not in {'"', "\\", "/", ";"})
    cleaned = cleaned.strip() or "download.bin"
    return cleaned


def build_app(app_state: AppState) -> tornado.web.Application:
    return tornado.web.Application(
        [
            (r"/api/v1/health", HealthHandler, {"app_state": app_state}),
            (r"/api/v1/auth/validate", AuthValidateHandler, {"app_state": app_state}),
            (r"/api/v1/runtimes", RuntimeCollectionHandler, {"app_state": app_state}),
            (r"/api/v1/challenges", ChallengeCollectionHandler, {"app_state": app_state}),
            (r"/api/v1/challenges/([^/]+)", ChallengeItemHandler, {"app_state": app_state}),
            (r"/api/v1/challenges/([^/]+)/convert-to-constellation", ChallengeConvertHandler, {"app_state": app_state}),
            (r"/api/v1/challenges/([^/]+)/files", ChallengeFilesHandler, {"app_state": app_state}),
            (r"/api/v1/challenges/([^/]+)/agents", ChallengeAgentsHandler, {"app_state": app_state}),
            (r"/api/v1/challenges/([^/]+)/events", ChallengeEventsHandler, {"app_state": app_state}),
            (r"/api/v1/agents/([^/]+)", AgentItemHandler, {"app_state": app_state}),
            (r"/api/v1/agents/([^/]+)/events", AgentEventsHandler, {"app_state": app_state}),
            (r"/api/v1/agents/([^/]+)/prompt", AgentPromptHandler, {"app_state": app_state}),
            (r"/api/v1/agents/([^/]+)/interrupt", AgentInterruptHandler, {"app_state": app_state}),
            (r"/api/v1/topics", TopicCollectionHandler, {"app_state": app_state}),
            (r"/api/v1/topics/([^/]+)", TopicItemHandler, {"app_state": app_state}),
            (r"/api/v1/topics/([^/]+)/history", TopicHistoryHandler, {"app_state": app_state}),
            (r"/api/v1/topics/([^/]+)/events", TopicEventsHandler, {"app_state": app_state}),
            (r"/api/v1/topics/([^/]+)/members", TopicMembersHandler, {"app_state": app_state}),
            (r"/api/v1/topics/([^/]+)/join", TopicJoinHandler, {"app_state": app_state}),
            (r"/api/v1/topics/([^/]+)/resume", TopicResumeHandler, {"app_state": app_state}),
            (r"/api/v1/topics/([^/]+)/leave", TopicLeaveHandler, {"app_state": app_state}),
            (r"/api/v1/topics/([^/]+)/heartbeat", TopicHeartbeatHandler, {"app_state": app_state}),
            (r"/api/v1/topics/([^/]+)/admin/exchange", TopicAdminExchangeHandler, {"app_state": app_state}),
            (r"/api/v1/topics/([^/]+)/admin/regenerate", TopicAdminRegenerateHandler, {"app_state": app_state}),
            (r"/api/v1/topics/([^/]+)/master/release", TopicMasterReleaseHandler, {"app_state": app_state}),
            (r"/api/v1/topics/([^/]+)/messages", TopicMessagesHandler, {"app_state": app_state}),
            (r"/api/v1/topics/([^/]+)/docs", TopicDocsHandler, {"app_state": app_state}),
            (r"/api/v1/topics/([^/]+)/final-artifacts", TopicFinalArtifactsHandler, {"app_state": app_state}),
            (r"/api/v1/files/([^/]+)", FileDownloadHandler, {"app_state": app_state}),
            (r"/api/v1/challenge-files/([^/]+)", ChallengeFileDownloadHandler, {"app_state": app_state}),
            (r"/runtime/ws", RuntimeControlWebSocket, {"app_state": app_state}),
            (r"/ws", ConstellationWebSocket, {"app_state": app_state}),
        ],
        debug=False,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", help="Bind host override.")
    parser.add_argument("--port", type=int, help="Bind port override.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_backend_settings()
    if args.host:
        settings = BackendSettings(
            mongo_uri=settings.mongo_uri,
            mongo_db_name=settings.mongo_db_name,
            listen_host=args.host,
            listen_port=args.port or settings.listen_port,
            system_tokens=settings.system_tokens,
            broker_event_ttl_hours=settings.broker_event_ttl_hours,
            allowed_ws_origins=settings.allowed_ws_origins,
            ui_shared_secret=settings.ui_shared_secret,
        )
    elif args.port:
        settings = BackendSettings(
            mongo_uri=settings.mongo_uri,
            mongo_db_name=settings.mongo_db_name,
            listen_host=settings.listen_host,
            listen_port=args.port,
            system_tokens=settings.system_tokens,
            broker_event_ttl_hours=settings.broker_event_ttl_hours,
            allowed_ws_origins=settings.allowed_ws_origins,
            ui_shared_secret=settings.ui_shared_secret,
        )
    storage = ConstellationStorage(settings)
    storage.ensure_indexes()
    app_state = AppState(settings=settings, storage=storage)
    app = build_app(app_state)
    app.listen(settings.listen_port, address=settings.listen_host)
    tornado.ioloop.IOLoop.current().start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
