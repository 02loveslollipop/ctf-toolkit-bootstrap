"""MongoDB storage layer for OpenCROW Constellation."""

from __future__ import annotations

import mimetypes
import hashlib
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from bson import ObjectId
from gridfs import GridFSBucket
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo import ReturnDocument

from .config import BackendSettings


TOPIC_SLUG_RE = re.compile(r"[^a-z0-9]+")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def digest_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def slugify(value: str) -> str:
    normalized = TOPIC_SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return normalized or f"topic-{secrets.token_hex(4)}"


def isoformat(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


def public_object_id(value: ObjectId | str) -> str:
    return str(value)


class ConstellationStorage:
    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings
        self.client = MongoClient(settings.mongo_uri, tz_aware=True)
        self.db = self.client[settings.mongo_db_name]
        self.topics: Collection = self.db["topics"]
        self.members: Collection = self.db["members"]
        self.messages: Collection = self.db["messages"]
        self.doc_snapshots: Collection = self.db["doc_snapshots"]
        self.final_artifacts: Collection = self.db["final_artifacts"]
        self.admin_tokens: Collection = self.db["admin_tokens"]
        self.broker_events: Collection = self.db["broker_events"]
        self.runtimes: Collection = self.db["runtimes"]
        self.challenges: Collection = self.db["challenges"]
        self.challenge_files: Collection = self.db["challenge_files"]
        self.agents: Collection = self.db["agents"]
        self.agent_artifacts: Collection = self.db["agent_artifacts"]
        self.runtime_commands: Collection = self.db["runtime_commands"]
        self.agent_events: Collection = self.db["agent_events"]
        self.bucket = GridFSBucket(self.db, bucket_name="final_artifacts_files")
        self.challenge_bucket = GridFSBucket(self.db, bucket_name="challenge_files")
        self.agent_artifact_bucket = GridFSBucket(self.db, bucket_name="agent_artifacts")

    def ensure_indexes(self) -> None:
        self.topics.create_index([("slug", ASCENDING)], unique=True)
        self.members.create_index([("topic", ASCENDING), ("created_at", DESCENDING)])
        self.members.create_index([("topic", ASCENDING), ("display_name", ASCENDING)])
        member_identity_index = "topic_1_chat_identity_id_1_client_kind_1"
        existing_member_indexes = self.members.index_information()
        existing_member_identity = existing_member_indexes.get(member_identity_index)
        partial_filter = {"chat_identity_id": {"$type": "string"}}
        if existing_member_identity and existing_member_identity.get("partialFilterExpression") != partial_filter:
            self.members.drop_index(member_identity_index)
        self.members.create_index(
            [("topic", ASCENDING), ("chat_identity_id", ASCENDING), ("client_kind", ASCENDING)],
            unique=True,
            partialFilterExpression=partial_filter,
        )
        self.messages.create_index([("topic", ASCENDING), ("created_at", DESCENDING)])
        self.doc_snapshots.create_index([("topic", ASCENDING), ("updated_at", DESCENDING)])
        self.doc_snapshots.create_index(
            [("topic", ASCENDING), ("member_id", ASCENDING), ("relative_path", ASCENDING)],
            unique=True,
        )
        self.final_artifacts.create_index([("topic", ASCENDING), ("created_at", DESCENDING)])
        self.admin_tokens.create_index([("topic", ASCENDING), ("used", ASCENDING)])
        self.broker_events.create_index([("topic", ASCENDING), ("created_at", DESCENDING)])
        self.broker_events.create_index([("expire_at", ASCENDING)], expireAfterSeconds=0)
        self.runtimes.create_index([("runtime_id", ASCENDING)], unique=True)
        self.runtimes.create_index([("last_seen_at", DESCENDING)])
        self.challenges.create_index([("slug", ASCENDING)], unique=True)
        self.challenges.create_index([("created_at", DESCENDING)])
        self.challenges.create_index([("runtime_id", ASCENDING), ("status", ASCENDING)])
        self.challenge_files.create_index([("challenge_id", ASCENDING), ("created_at", DESCENDING)])
        self.agents.create_index([("challenge_id", ASCENDING), ("created_at", ASCENDING)])
        self.agents.create_index([("runtime_id", ASCENDING), ("status", ASCENDING)])
        self.agent_artifacts.create_index([("agent_id", ASCENDING), ("created_at", DESCENDING)])
        self.agent_artifacts.create_index([("challenge_id", ASCENDING), ("created_at", DESCENDING)])
        self.runtime_commands.create_index([("runtime_id", ASCENDING), ("status", ASCENDING), ("created_at", ASCENDING)])
        self.runtime_commands.create_index([("agent_id", ASCENDING), ("created_at", ASCENDING)])
        self.agent_events.create_index([("challenge_id", ASCENDING), ("created_at", DESCENDING)])
        self.agent_events.create_index([("agent_id", ASCENDING), ("created_at", DESCENDING)])
        self.agent_events.create_index([("runtime_id", ASCENDING), ("created_at", DESCENDING)])

    def validate_system_token(self, token: str) -> bool:
        return token in self.settings.system_tokens

    def register_runtime(
        self,
        *,
        runtime_id: str,
        display_name: str,
        capabilities: dict[str, Any] | None = None,
        workspace_root: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        cleaned_id = runtime_id.strip() or f"runtime-{secrets.token_hex(6)}"
        update = {
            "runtime_id": cleaned_id,
            "display_name": display_name.strip() or cleaned_id,
            "status": "online",
            "capabilities": capabilities or {},
            "workspace_root": workspace_root,
            "metadata": metadata or {},
            "last_seen_at": now,
        }
        doc = self.runtimes.find_one_and_update(
            {"runtime_id": cleaned_id},
            {
                "$set": update,
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        assert doc is not None
        return self._public_runtime(doc)

    def touch_runtime(self, runtime_id: str, *, status: str = "online") -> dict[str, Any]:
        doc = self.runtimes.find_one_and_update(
            {"runtime_id": runtime_id},
            {"$set": {"status": status, "last_seen_at": utc_now()}},
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            raise KeyError(runtime_id)
        return self._public_runtime(doc)

    def mark_runtime_offline(self, runtime_id: str) -> dict[str, Any] | None:
        doc = self.runtimes.find_one_and_update(
            {"runtime_id": runtime_id},
            {"$set": {"status": "offline", "last_seen_at": utc_now()}},
            return_document=ReturnDocument.AFTER,
        )
        return self._public_runtime(doc) if doc else None

    def list_runtimes(self) -> list[dict[str, Any]]:
        return [self._public_runtime(doc) for doc in self.runtimes.find().sort("last_seen_at", DESCENDING)]

    def get_runtime(self, runtime_id: str) -> dict[str, Any] | None:
        doc = self.runtimes.find_one({"runtime_id": runtime_id})
        return self._public_runtime(doc) if doc else None

    def _choose_runtime(self, runtime_id: str | None = None) -> str:
        if runtime_id:
            doc = self.runtimes.find_one({"runtime_id": runtime_id})
            if doc is None:
                raise KeyError(runtime_id)
            return str(doc["runtime_id"])
        doc = self.runtimes.find_one({"status": "online"}, sort=[("last_seen_at", DESCENDING)])
        if doc is None:
            raise RuntimeError("No online runtime is available.")
        return str(doc["runtime_id"])

    def create_challenge(
        self,
        *,
        title: str,
        description: str,
        category: str,
        challenge_type: str,
        runtime_id: str | None,
        handoff_urls: list[str],
        settings: dict[str, Any] | None = None,
        slug: str | None = None,
        metadata: dict[str, Any] | None = None,
        start_agent: bool = True,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
        normalized_type = challenge_type if challenge_type in {"single_agent", "constellation"} else "single_agent"
        assigned_runtime = self._choose_runtime(runtime_id)
        now = utc_now()
        challenge_slug = slugify(slug or title)
        doc = {
            "slug": challenge_slug,
            "title": title.strip() or challenge_slug,
            "description": description.strip(),
            "category": category.strip() or "misc",
            "challenge_type": normalized_type,
            "status": "queued" if start_agent else "created",
            "runtime_id": assigned_runtime,
            "handoff_urls": [value.strip() for value in handoff_urls if value.strip()],
            "settings": settings or {},
            "metadata": metadata or {},
            "created_at": now,
            "updated_at": now,
        }
        try:
            result = self.challenges.insert_one(doc)
        except DuplicateKeyError as exc:
            raise ValueError(f"Challenge already exists: {challenge_slug}") from exc
        doc["_id"] = result.inserted_id
        challenge = self._public_challenge(doc)
        if not start_agent:
            return challenge, None, None
        role = "solo" if normalized_type == "single_agent" else "master"
        prompt = self.default_agent_prompt(challenge, role=role)
        agent = self.create_agent(
            challenge["id"],
            role=role,
            display_name=f"{challenge['title']} {role}",
            prompt=prompt,
            runtime_id=assigned_runtime,
            model=(settings or {}).get("model"),
        )
        command = self.queue_runtime_command(
            assigned_runtime,
            command_type="spawn_agent",
            challenge_id=challenge["id"],
            agent_id=agent["id"],
            payload={"challenge": challenge, "agent": agent, "files": self.list_challenge_files(challenge["id"])},
        )
        return self.get_challenge(challenge["id"]) or challenge, agent, command

    def get_challenge(self, challenge_id_or_slug: str) -> dict[str, Any] | None:
        query: dict[str, Any] = {"slug": challenge_id_or_slug}
        try:
            query = {"_id": ObjectId(challenge_id_or_slug)}
        except Exception:
            pass
        doc = self.challenges.find_one(query)
        return self._public_challenge(doc) if doc else None

    def list_challenges(self) -> list[dict[str, Any]]:
        return [self._public_challenge(doc) for doc in self.challenges.find().sort("created_at", DESCENDING)]

    def update_challenge_status(self, challenge_id: str, status: str) -> dict[str, Any]:
        doc = self.challenges.find_one_and_update(
            {"_id": ObjectId(challenge_id)},
            {"$set": {"status": status, "updated_at": utc_now()}},
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            raise KeyError(challenge_id)
        return self._public_challenge(doc)

    def convert_challenge_to_constellation(self, challenge_id: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
        now = utc_now()
        doc = self.challenges.find_one_and_update(
            {"_id": ObjectId(challenge_id)},
            {"$set": {"challenge_type": "constellation", "updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            raise KeyError(challenge_id)
        agent_doc = self.agents.find_one({"challenge_id": challenge_id}, sort=[("created_at", ASCENDING)])
        agent = None
        if agent_doc is not None:
            updated_agent = self.agents.find_one_and_update(
                {"_id": agent_doc["_id"]},
                {"$set": {"role": "master", "updated_at": now}},
                return_document=ReturnDocument.AFTER,
            )
            agent = self._public_agent(updated_agent) if updated_agent else None
        return self._public_challenge(doc), agent

    def default_agent_prompt(self, challenge: dict[str, Any], *, role: str) -> str:
        handoff = "\n".join(f"- {url}" for url in challenge.get("handoff_urls", [])) or "- none"
        if role == "master":
            role_text = "Plan the solve, coordinate any approved slave agents, and execute the highest-value path yourself."
        elif role == "slave":
            role_text = "Work on the assigned subtask, report concrete findings, and avoid duplicating other agents."
        else:
            role_text = "Plan and execute the solve path end to end."
        return (
            f"You are an OpenCROW Codex agent for challenge `{challenge['title']}`.\n\n"
            f"Category: {challenge.get('category', 'misc')}\n"
            f"Role: {role}\n\n"
            f"Description:\n{challenge.get('description', '')}\n\n"
            f"Handoff URLs:\n{handoff}\n\n"
            f"{role_text}\n"
            "Use installed OpenCROW skills and MCP tools first when they fit. "
            "Keep findings and repeatable evidence in workspace files, and produce final artifacts when solved.\n"
        )

    def add_challenge_file(self, challenge_id: str, *, filename: str, data: bytes, content_type: str | None = None) -> dict[str, Any]:
        if self.get_challenge(challenge_id) is None:
            raise KeyError(challenge_id)
        safe_name = filename.strip().replace("\\", "/").split("/")[-1] or "upload.bin"
        guessed = content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        file_id = self.challenge_bucket.upload_from_stream(
            safe_name,
            data,
            metadata={"challenge_id": challenge_id, "content_type": guessed},
        )
        doc = {
            "challenge_id": challenge_id,
            "name": safe_name,
            "file_id": file_id,
            "size": len(data),
            "content_type": guessed,
            "created_at": utc_now(),
        }
        result = self.challenge_files.insert_one(doc)
        doc["_id"] = result.inserted_id
        return self._public_challenge_file(doc)

    def list_challenge_files(self, challenge_id: str) -> list[dict[str, Any]]:
        return [
            self._public_challenge_file(doc)
            for doc in self.challenge_files.find({"challenge_id": challenge_id}).sort("created_at", ASCENDING)
        ]

    def download_challenge_file(self, file_id: str) -> tuple[bytes, dict[str, Any]]:
        grid_out = self.challenge_bucket.open_download_stream(ObjectId(file_id))
        data = grid_out.read()
        metadata = dict(grid_out.metadata or {})
        metadata["filename"] = grid_out.filename
        metadata["length"] = grid_out.length
        return data, metadata

    def create_agent(
        self,
        challenge_id: str,
        *,
        role: str,
        display_name: str,
        prompt: str,
        runtime_id: str | None = None,
        model: str | None = None,
        metadata: dict[str, Any] | None = None,
        require_approval: bool = False,
    ) -> dict[str, Any]:
        challenge = self.get_challenge(challenge_id)
        if challenge is None:
            raise KeyError(challenge_id)
        assigned_runtime = runtime_id or challenge.get("runtime_id")
        if not assigned_runtime:
            assigned_runtime = self._choose_runtime(None)
        now = utc_now()
        doc = {
            "challenge_id": challenge["id"],
            "runtime_id": assigned_runtime,
            "role": role,
            "display_name": display_name.strip() or f"{role} agent",
            "status": "approval_required" if require_approval else "queued",
            "codex_thread_id": None,
            "workspace_path": None,
            "model": model,
            "prompt": prompt,
            "last_response": None,
            "metadata": metadata or {},
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "finished_at": None,
        }
        result = self.agents.insert_one(doc)
        doc["_id"] = result.inserted_id
        agent = self._public_agent(doc)
        self.record_agent_event(
            challenge["id"],
            agent_id=agent["id"],
            runtime_id=assigned_runtime,
            event_type="agent_spawn_requested" if require_approval else "agent_created",
            payload=agent,
        )
        return agent

    def list_agents(self, challenge_id: str) -> list[dict[str, Any]]:
        return [self._public_agent(doc) for doc in self.agents.find({"challenge_id": challenge_id}).sort("created_at", ASCENDING)]

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        try:
            doc = self.agents.find_one({"_id": ObjectId(agent_id)})
        except Exception:
            return None
        return self._public_agent(doc) if doc else None

    def update_agent_state(
        self,
        agent_id: str,
        *,
        status: str | None = None,
        codex_thread_id: str | None = None,
        workspace_path: str | None = None,
        last_response: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        updates: dict[str, Any] = {"updated_at": now}
        if status is not None:
            updates["status"] = status
            if status in {"starting", "running"}:
                updates.setdefault("started_at", now)
            if status in {"completed", "failed", "stopped", "interrupted"}:
                updates["finished_at"] = now
        if codex_thread_id is not None:
            updates["codex_thread_id"] = codex_thread_id
        if workspace_path is not None:
            updates["workspace_path"] = workspace_path
        if last_response is not None:
            updates["last_response"] = last_response
        if metadata is not None:
            updates["metadata"] = metadata
        doc = self.agents.find_one_and_update(
            {"_id": ObjectId(agent_id)},
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            raise KeyError(agent_id)
        agent = self._public_agent(doc)
        self.record_agent_event(
            agent["challenge_id"],
            agent_id=agent["id"],
            runtime_id=agent.get("runtime_id"),
            event_type="agent_state",
            payload=agent,
        )
        return agent

    def approve_agent(self, agent_id: str) -> dict[str, Any]:
        agent = self.get_agent(agent_id)
        if agent is None:
            raise KeyError(agent_id)
        if agent["status"] != "approval_required":
            raise ValueError(f"Agent is not waiting for approval: {agent['status']}")
        return self.update_agent_state(agent_id, status="queued")

    def reject_agent(self, agent_id: str, *, reason: str | None = None) -> dict[str, Any]:
        agent = self.get_agent(agent_id)
        if agent is None:
            raise KeyError(agent_id)
        updated = self.update_agent_state(
            agent_id,
            status="rejected",
            metadata={**agent.get("metadata", {}), "rejection_reason": reason or ""},
        )
        self.record_agent_event(
            updated["challenge_id"],
            agent_id=updated["id"],
            runtime_id=updated.get("runtime_id"),
            event_type="agent_rejected",
            payload=updated,
        )
        return updated

    def queue_runtime_command(
        self,
        runtime_id: str,
        *,
        command_type: str,
        challenge_id: str | None = None,
        agent_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        doc = {
            "runtime_id": runtime_id,
            "command_type": command_type,
            "challenge_id": challenge_id,
            "agent_id": agent_id,
            "payload": payload or {},
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "acknowledged_at": None,
            "completed_at": None,
            "error": None,
        }
        result = self.runtime_commands.insert_one(doc)
        doc["_id"] = result.inserted_id
        return self._public_runtime_command(doc)

    def list_runtime_commands(self, runtime_id: str, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"runtime_id": runtime_id}
        if status:
            query["status"] = status
        return [
            self._public_runtime_command(doc)
            for doc in self.runtime_commands.find(query).sort("created_at", ASCENDING).limit(max(1, min(limit, 500)))
        ]

    def update_runtime_command(
        self,
        command_id: str,
        *,
        status: str,
        error: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        updates: dict[str, Any] = {"status": status, "updated_at": now}
        if status in {"acknowledged", "running"}:
            updates["acknowledged_at"] = now
        if status in {"completed", "failed", "cancelled"}:
            updates["completed_at"] = now
        if error is not None:
            updates["error"] = error
        doc = self.runtime_commands.find_one_and_update(
            {"_id": ObjectId(command_id)},
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            raise KeyError(command_id)
        return self._public_runtime_command(doc)

    def record_agent_event(
        self,
        challenge_id: str,
        *,
        agent_id: str | None,
        runtime_id: str | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        doc = {
            "challenge_id": challenge_id,
            "agent_id": agent_id,
            "runtime_id": runtime_id,
            "event_type": event_type,
            "payload": payload,
            "created_at": utc_now(),
        }
        result = self.agent_events.insert_one(doc)
        doc["_id"] = result.inserted_id
        return self._public_agent_event(doc)

    def list_agent_events(
        self,
        *,
        challenge_id: str | None = None,
        agent_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {}
        if challenge_id:
            query["challenge_id"] = challenge_id
        if agent_id:
            query["agent_id"] = agent_id
        cursor = self.agent_events.find(query).sort("created_at", DESCENDING).limit(max(1, min(limit, 500)))
        return [self._public_agent_event(doc) for doc in reversed(list(cursor))]

    def add_agent_artifact(
        self,
        agent_id: str,
        *,
        filename: str,
        data: bytes,
        content_type: str | None = None,
        artifact_type: str = "artifact",
    ) -> dict[str, Any]:
        agent = self.get_agent(agent_id)
        if agent is None:
            raise KeyError(agent_id)
        safe_name = filename.strip().replace("\\", "/").split("/")[-1] or "artifact.bin"
        guessed = content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        digest = hashlib.sha256(data).hexdigest()
        file_id = self.agent_artifact_bucket.upload_from_stream(
            safe_name,
            data,
            metadata={
                "agent_id": agent["id"],
                "challenge_id": agent["challenge_id"],
                "runtime_id": agent.get("runtime_id"),
                "content_type": guessed,
                "sha256": digest,
                "artifact_type": artifact_type,
            },
        )
        doc = {
            "agent_id": agent["id"],
            "challenge_id": agent["challenge_id"],
            "runtime_id": agent.get("runtime_id"),
            "name": safe_name,
            "file_id": file_id,
            "size": len(data),
            "sha256": digest,
            "artifact_type": artifact_type,
            "content_type": guessed,
            "created_at": utc_now(),
        }
        result = self.agent_artifacts.insert_one(doc)
        doc["_id"] = result.inserted_id
        return self._public_agent_artifact(doc)

    def list_agent_artifacts(self, agent_id: str) -> list[dict[str, Any]]:
        return [
            self._public_agent_artifact(doc)
            for doc in self.agent_artifacts.find({"agent_id": agent_id}).sort("created_at", ASCENDING)
        ]

    def download_agent_artifact(self, file_id: str) -> tuple[bytes, dict[str, Any]]:
        grid_out = self.agent_artifact_bucket.open_download_stream(ObjectId(file_id))
        data = grid_out.read()
        metadata = dict(grid_out.metadata or {})
        metadata["filename"] = grid_out.filename
        metadata["length"] = grid_out.length
        return data, metadata

    def _public_topic(self, doc: dict[str, Any]) -> dict[str, Any]:
        topic_slug = str(doc["slug"])
        return {
            "id": public_object_id(doc["_id"]),
            "slug": topic_slug,
            "title": doc.get("title", topic_slug),
            "description": doc.get("description", ""),
            "category": doc.get("category", "misc"),
            "handoff_urls": list(doc.get("handoff_urls", [])),
            "created_at": isoformat(doc.get("created_at")),
            "updated_at": isoformat(doc.get("updated_at")),
            "member_count": self.members.count_documents({"topic": topic_slug}),
            "final_artifact_count": self.final_artifacts.count_documents({"topic": topic_slug}),
        }

    def _public_member(self, doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": public_object_id(doc["_id"]),
            "topic": doc["topic"],
            "display_name": doc["display_name"],
            "client_kind": doc["client_kind"],
            "chat_identity_id": doc.get("chat_identity_id"),
            "session_epoch": int(doc.get("session_epoch", 0)),
            "workspace_path": doc.get("workspace_path"),
            "master_capability": bool(doc.get("master_capability", False)),
            "created_at": isoformat(doc.get("created_at")),
            "last_seen_at": isoformat(doc.get("last_seen_at")),
            "metadata": doc.get("metadata", {}),
        }

    def _public_message(self, doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": public_object_id(doc["_id"]),
            "topic": doc["topic"],
            "type": doc["type"],
            "body": doc["body"],
            "audience": doc.get("audience", {"mode": "topic"}),
            "sender": doc["sender"],
            "priority": doc.get("priority", 0),
            "created_at": isoformat(doc.get("created_at")),
            "metadata": doc.get("metadata", {}),
        }

    def _public_doc_snapshot(self, doc: dict[str, Any], *, include_content: bool = True) -> dict[str, Any]:
        payload = {
            "id": public_object_id(doc["_id"]),
            "topic": doc["topic"],
            "member_id": doc["member_id"],
            "display_name": doc["display_name"],
            "relative_path": doc["relative_path"],
            "sha256": doc["sha256"],
            "updated_at": isoformat(doc.get("updated_at")),
        }
        if include_content:
            payload["content"] = doc["content"]
        return payload

    def _public_final_artifact(self, doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": public_object_id(doc["_id"]),
            "topic": doc["topic"],
            "member_id": doc["member_id"],
            "display_name": doc["display_name"],
            "flag": doc["flag"],
            "files": doc["files"],
            "created_at": isoformat(doc["created_at"]),
        }

    def _public_broker_event(self, doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": public_object_id(doc["_id"]),
            "topic": doc["topic"],
            "event_type": doc["event_type"],
            "payload": doc["payload"],
            "created_at": isoformat(doc["created_at"]),
        }

    def _public_runtime(self, doc: dict[str, Any]) -> dict[str, Any]:
        runtime_id = str(doc["runtime_id"])
        return {
            "id": runtime_id,
            "runtime_id": runtime_id,
            "display_name": doc.get("display_name", runtime_id),
            "status": doc.get("status", "offline"),
            "capabilities": doc.get("capabilities", {}),
            "workspace_root": doc.get("workspace_root"),
            "active_agent_count": self.agents.count_documents(
                {"runtime_id": runtime_id, "status": {"$in": ["queued", "starting", "running", "interrupted"]}}
            ),
            "created_at": isoformat(doc.get("created_at")),
            "last_seen_at": isoformat(doc.get("last_seen_at")),
            "metadata": doc.get("metadata", {}),
        }

    def _public_challenge(self, doc: dict[str, Any]) -> dict[str, Any]:
        challenge_id = public_object_id(doc["_id"])
        return {
            "id": challenge_id,
            "slug": doc["slug"],
            "title": doc.get("title", doc["slug"]),
            "description": doc.get("description", ""),
            "category": doc.get("category", "misc"),
            "challenge_type": doc.get("challenge_type", "single_agent"),
            "status": doc.get("status", "queued"),
            "runtime_id": doc.get("runtime_id"),
            "handoff_urls": list(doc.get("handoff_urls", [])),
            "settings": doc.get("settings", {}),
            "agent_count": self.agents.count_documents({"challenge_id": challenge_id}),
            "file_count": self.challenge_files.count_documents({"challenge_id": challenge_id}),
            "created_at": isoformat(doc.get("created_at")),
            "updated_at": isoformat(doc.get("updated_at")),
            "metadata": doc.get("metadata", {}),
        }

    def _public_challenge_file(self, doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": public_object_id(doc["_id"]),
            "challenge_id": doc["challenge_id"],
            "name": doc["name"],
            "file_id": public_object_id(doc["file_id"]),
            "size": int(doc.get("size", 0)),
            "content_type": doc.get("content_type", "application/octet-stream"),
            "created_at": isoformat(doc.get("created_at")),
        }

    def _public_agent(self, doc: dict[str, Any]) -> dict[str, Any]:
        agent_id = public_object_id(doc["_id"])
        return {
            "id": agent_id,
            "challenge_id": doc["challenge_id"],
            "runtime_id": doc.get("runtime_id"),
            "role": doc.get("role", "worker"),
            "display_name": doc.get("display_name", "agent"),
            "status": doc.get("status", "queued"),
            "codex_thread_id": doc.get("codex_thread_id"),
            "workspace_path": doc.get("workspace_path"),
            "model": doc.get("model"),
            "prompt": doc.get("prompt", ""),
            "last_response": doc.get("last_response"),
            "created_at": isoformat(doc.get("created_at")),
            "updated_at": isoformat(doc.get("updated_at")),
            "started_at": isoformat(doc.get("started_at")),
            "finished_at": isoformat(doc.get("finished_at")),
            "artifact_count": self.agent_artifacts.count_documents({"agent_id": agent_id}),
            "metadata": doc.get("metadata", {}),
        }

    def _public_agent_artifact(self, doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": public_object_id(doc["_id"]),
            "agent_id": doc["agent_id"],
            "challenge_id": doc["challenge_id"],
            "runtime_id": doc.get("runtime_id"),
            "name": doc["name"],
            "file_id": public_object_id(doc["file_id"]),
            "size": int(doc.get("size", 0)),
            "sha256": doc.get("sha256"),
            "artifact_type": doc.get("artifact_type", "artifact"),
            "content_type": doc.get("content_type", "application/octet-stream"),
            "created_at": isoformat(doc.get("created_at")),
        }

    def _public_runtime_command(self, doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": public_object_id(doc["_id"]),
            "runtime_id": doc["runtime_id"],
            "command_type": doc["command_type"],
            "status": doc.get("status", "queued"),
            "challenge_id": doc.get("challenge_id"),
            "agent_id": doc.get("agent_id"),
            "payload": doc.get("payload", {}),
            "created_at": isoformat(doc.get("created_at")),
            "updated_at": isoformat(doc.get("updated_at")),
            "acknowledged_at": isoformat(doc.get("acknowledged_at")),
            "completed_at": isoformat(doc.get("completed_at")),
            "error": doc.get("error"),
        }

    def _public_agent_event(self, doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": public_object_id(doc["_id"]),
            "challenge_id": doc["challenge_id"],
            "agent_id": doc.get("agent_id"),
            "runtime_id": doc.get("runtime_id"),
            "event_type": doc["event_type"],
            "payload": doc.get("payload", {}),
            "created_at": isoformat(doc.get("created_at")),
        }

    def _emit_broker_event(self, topic: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        doc = {
            "topic": topic,
            "event_type": event_type,
            "payload": payload,
            "created_at": now,
            "expire_at": now + timedelta(hours=self.settings.broker_event_ttl_hours),
        }
        inserted = self.broker_events.insert_one(doc)
        doc["_id"] = inserted.inserted_id
        return self._public_broker_event(doc)

    def list_topics(self) -> list[dict[str, Any]]:
        return [self._public_topic(doc) for doc in self.topics.find().sort("updated_at", DESCENDING)]

    def get_topic(self, topic: str) -> dict[str, Any] | None:
        doc = self.topics.find_one({"slug": topic})
        return self._public_topic(doc) if doc else None

    def create_topic(
        self,
        *,
        title: str,
        description: str,
        category: str,
        handoff_urls: list[str],
        slug: str | None = None,
        created_by: str = "system",
    ) -> tuple[dict[str, Any], str]:
        now = utc_now()
        topic_slug = slugify(slug or title)
        topic_doc = {
            "slug": topic_slug,
            "title": title.strip() or topic_slug,
            "description": description.strip(),
            "category": category.strip() or "misc",
            "handoff_urls": [value.strip() for value in handoff_urls if value.strip()],
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }
        try:
            result = self.topics.insert_one(topic_doc)
        except DuplicateKeyError as exc:
            raise ValueError(f"Topic already exists: {topic_slug}") from exc
        topic_doc["_id"] = result.inserted_id

        admin_secret = secrets.token_urlsafe(18)
        self.admin_tokens.insert_one(
            {
                "topic": topic_slug,
                "digest": digest_secret(admin_secret),
                "used": False,
                "created_at": now,
                "used_by_member_id": None,
            }
        )
        return self._public_topic(topic_doc), admin_secret

    def update_topic(
        self,
        topic: str,
        *,
        title: str | None = None,
        description: str | None = None,
        category: str | None = None,
        handoff_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        current = self.topics.find_one({"slug": topic})
        if current is None:
            raise KeyError(topic)
        updates: dict[str, Any] = {"updated_at": utc_now()}
        if title is not None:
            updates["title"] = title.strip() or current.get("title", topic)
        if description is not None:
            updates["description"] = description.strip()
        if category is not None:
            updates["category"] = category.strip() or current.get("category", "misc")
        if handoff_urls is not None:
            updates["handoff_urls"] = [value.strip() for value in handoff_urls if value.strip()]
        self.topics.update_one({"slug": topic}, {"$set": updates})
        updated = self.topics.find_one({"slug": topic})
        assert updated is not None
        return self._public_topic(updated)

    def regenerate_admin_secret(self, topic: str) -> str:
        if self.topics.find_one({"slug": topic}) is None:
            raise KeyError(topic)
        now = utc_now()
        self.admin_tokens.update_many({"topic": topic, "used": False}, {"$set": {"used": True, "used_at": now}})
        admin_secret = secrets.token_urlsafe(18)
        self.admin_tokens.insert_one(
            {
                "topic": topic,
                "digest": digest_secret(admin_secret),
                "used": False,
                "created_at": now,
                "used_by_member_id": None,
            }
        )
        return admin_secret

    def delete_topic(self, topic: str, *, deleted_by: str) -> dict[str, Any]:
        existing = self.topics.find_one({"slug": topic})
        if existing is None:
            raise KeyError(topic)
        # Final artifacts intentionally survive topic deletion. They are immutable,
        # permanent deliverables and are not part of ephemeral topic state.
        self.members.delete_many({"topic": topic})
        self.messages.delete_many({"topic": topic})
        self.doc_snapshots.delete_many({"topic": topic})
        self.admin_tokens.delete_many({"topic": topic})
        self.broker_events.delete_many({"topic": topic})
        event = self._emit_broker_event(
            topic,
            "topic_deleted",
            {
                "topic": topic,
                "deleted_by": deleted_by,
                "deleted_at": isoformat(utc_now()),
            },
        )
        self.topics.delete_one({"slug": topic})
        return {
            "topic": topic,
            "deleted": True,
            "event": event,
        }

    def create_member(
        self,
        *,
        topic: str,
        display_name: str,
        client_kind: str,
        workspace_path: str | None = None,
        metadata: dict[str, Any] | None = None,
        master_capability: bool = False,
        chat_identity_id: str | None = None,
        resume_secret: str | None = None,
    ) -> dict[str, Any]:
        if self.topics.find_one({"slug": topic}) is None:
            raise KeyError(topic)
        now = utc_now()
        doc = {
            "topic": topic,
            "display_name": display_name.strip() or "anonymous",
            "client_kind": client_kind,
            "chat_identity_id": (chat_identity_id or secrets.token_urlsafe(12)).strip(),
            "resume_secret_digest": digest_secret(resume_secret) if resume_secret else None,
            "session_epoch": 0,
            "workspace_path": workspace_path,
            "metadata": metadata or {},
            "master_capability": master_capability,
            "created_at": now,
            "last_seen_at": now,
        }
        result = self.members.insert_one(doc)
        doc["_id"] = result.inserted_id
        return self._public_member(doc)

    def resume_member(
        self,
        topic: str,
        *,
        chat_identity_id: str,
        resume_secret: str,
        display_name: str,
        client_kind: str,
        workspace_path: str | None = None,
        metadata: dict[str, Any] | None = None,
        allow_create: bool = False,
    ) -> dict[str, Any]:
        if self.topics.find_one({"slug": topic}) is None:
            raise KeyError(topic)
        identity = chat_identity_id.strip()
        if not identity:
            raise ValueError("`chat_identity_id` is required.")
        secret = resume_secret.strip()
        if not secret:
            raise ValueError("`resume_secret` is required.")

        now = utc_now()
        query = {
            "topic": topic,
            "chat_identity_id": identity,
            "client_kind": client_kind,
        }
        current = self.members.find_one(query)
        if current is None:
            if not allow_create:
                raise KeyError(identity)
            return self.create_member(
                topic=topic,
                display_name=display_name,
                client_kind=client_kind,
                workspace_path=workspace_path,
                metadata=metadata,
                master_capability=(client_kind == "ui"),
                chat_identity_id=identity,
                resume_secret=secret,
            )

        expected = current.get("resume_secret_digest")
        if not isinstance(expected, str) or expected != digest_secret(secret):
            raise PermissionError("Invalid resume secret for this topic identity.")

        updated = self.members.find_one_and_update(
            {"_id": current["_id"]},
            {
                "$set": {
                    "display_name": display_name.strip() or current.get("display_name", "anonymous"),
                    "workspace_path": workspace_path,
                    "metadata": metadata or {},
                    "last_seen_at": now,
                    "session_epoch": int(current.get("session_epoch", 0)) + 1,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        assert updated is not None
        return self._public_member(updated)

    def get_member(self, member_id: str) -> dict[str, Any] | None:
        try:
            object_id = ObjectId(member_id)
        except Exception:
            return None
        doc = self.members.find_one({"_id": object_id})
        return self._public_member(doc) if doc else None

    def _member_doc(self, member_id: str) -> dict[str, Any]:
        try:
            object_id = ObjectId(member_id)
        except Exception as exc:
            raise KeyError(member_id) from exc
        doc = self.members.find_one({"_id": object_id})
        if doc is None:
            raise KeyError(member_id)
        return doc

    def list_members(self, topic: str) -> list[dict[str, Any]]:
        return [self._public_member(doc) for doc in self.members.find({"topic": topic}).sort("created_at", ASCENDING)]

    def touch_member(self, member_id: str) -> dict[str, Any]:
        now = utc_now()
        try:
            object_id = ObjectId(member_id)
        except Exception as exc:
            raise KeyError(member_id) from exc
        doc = self.members.find_one_and_update(
            {"_id": object_id},
            {"$set": {"last_seen_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            raise KeyError(member_id)
        return self._public_member(doc)

    def remove_member(self, topic: str, member_id: str) -> dict[str, Any]:
        member = self._member_doc(member_id)
        if member["topic"] != topic:
            raise KeyError(member_id)
        self.members.delete_one({"_id": member["_id"]})
        return {"removed": True, "member_id": member_id, "topic": topic}

    def exchange_admin_token(self, topic: str, member_id: str, single_use_password: str) -> dict[str, Any]:
        member = self._member_doc(member_id)
        if member["topic"] != topic:
            raise KeyError(member_id)
        digest = digest_secret(single_use_password)
        token_doc = self.admin_tokens.find_one({"topic": topic, "digest": digest, "used": False})
        if token_doc is None:
            raise PermissionError("Invalid or already used single-use password.")
        now = utc_now()
        self.admin_tokens.update_one(
            {"_id": token_doc["_id"]},
            {"$set": {"used": True, "used_at": now, "used_by_member_id": member_id}},
        )
        self.members.update_one({"_id": member["_id"]}, {"$set": {"master_capability": True, "last_seen_at": now}})
        updated = self.members.find_one({"_id": member["_id"]})
        assert updated is not None
        return self._public_member(updated)

    def release_master(self, topic: str, member_id: str) -> dict[str, Any]:
        member = self._member_doc(member_id)
        if member["topic"] != topic:
            raise KeyError(member_id)
        self.members.update_one({"_id": member["_id"]}, {"$set": {"master_capability": False, "last_seen_at": utc_now()}})
        updated = self.members.find_one({"_id": member["_id"]})
        assert updated is not None
        return self._public_member(updated)

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
        member = self._member_doc(member_id)
        if member["topic"] != topic:
            raise KeyError(member_id)
        if message_type not in {"chat_message", "task_directive", "broadcast_event"}:
            raise ValueError(f"Unsupported message type: {message_type}")
        is_ui = member["client_kind"] == "ui"
        if message_type == "task_directive" and not (is_ui or bool(member.get("master_capability"))):
            raise PermissionError("This member is not allowed to issue task directives.")
        priority = 50
        if message_type == "broadcast_event":
            priority = 70
        if message_type == "task_directive":
            priority = 100 if is_ui else 90

        now = utc_now()
        doc = {
            "topic": topic,
            "type": message_type,
            "body": body,
            "audience": audience or {"mode": "topic"},
            "sender": {
                "member_id": member_id,
                "chat_identity_id": member.get("chat_identity_id"),
                "display_name": member["display_name"],
                "client_kind": member["client_kind"],
                "session_epoch": int(member.get("session_epoch", 0)),
            },
            "priority": priority,
            "metadata": metadata or {},
            "created_at": now,
        }
        result = self.messages.insert_one(doc)
        doc["_id"] = result.inserted_id
        public = self._public_message(doc)
        self._emit_broker_event(topic, "message", public)
        self.members.update_one({"_id": member["_id"]}, {"$set": {"last_seen_at": now}})
        return public

    def history(self, topic: str, *, limit: int = 100) -> list[dict[str, Any]]:
        cursor = self.messages.find({"topic": topic}).sort("created_at", DESCENDING).limit(max(1, min(limit, 500)))
        return [self._public_message(doc) for doc in reversed(list(cursor))]

    def sync_documents(
        self,
        topic: str,
        *,
        member_id: str,
        documents: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        member = self._member_doc(member_id)
        if member["topic"] != topic:
            raise KeyError(member_id)
        public_docs: list[dict[str, Any]] = []
        now = utc_now()
        for item in documents:
            relative_path = str(item["relative_path"]).strip()
            content = str(item["content"])
            sha256_value = str(item["sha256"])
            update_doc = {
                "topic": topic,
                "member_id": member_id,
                "display_name": member["display_name"],
                "relative_path": relative_path,
                "content": content,
                "sha256": sha256_value,
                "updated_at": now,
            }
            self.doc_snapshots.update_one(
                {"topic": topic, "member_id": member_id, "relative_path": relative_path},
                {"$set": update_doc},
                upsert=True,
            )
            stored = self.doc_snapshots.find_one(
                {"topic": topic, "member_id": member_id, "relative_path": relative_path}
            )
            assert stored is not None
            public_doc = self._public_doc_snapshot(stored)
            public_docs.append(public_doc)
            self._emit_broker_event(topic, "doc_snapshot", public_doc)
        self.members.update_one({"_id": member["_id"]}, {"$set": {"last_seen_at": now}})
        return public_docs

    def list_documents(self, topic: str, *, include_content: bool = False) -> list[dict[str, Any]]:
        return [
            self._public_doc_snapshot(doc, include_content=include_content)
            for doc in self.doc_snapshots.find({"topic": topic}).sort([("display_name", ASCENDING), ("relative_path", ASCENDING)])
        ]

    def upload_final_artifacts(
        self,
        topic: str,
        *,
        member_id: str,
        flag: str,
        writeup_name: str,
        writeup_bytes: bytes,
        solver_files: list[tuple[str, bytes]],
        handoff_files: list[tuple[str, bytes]] | None = None,
    ) -> dict[str, Any]:
        member = self._member_doc(member_id)
        if member["topic"] != topic:
            raise KeyError(member_id)
        now = utc_now()
        files: list[dict[str, Any]] = []

        writeup_file_id = self.bucket.upload_from_stream(
            writeup_name,
            writeup_bytes,
            metadata={"topic": topic, "member_id": member_id, "role": "writeup", "content_type": "text/markdown"},
        )
        files.append(
            {
                "role": "writeup",
                "name": writeup_name,
                "file_id": public_object_id(writeup_file_id),
                "size": len(writeup_bytes),
                "content_type": "text/markdown",
            }
        )

        for solver_name, solver_bytes in solver_files:
            file_id = self.bucket.upload_from_stream(
                solver_name,
                solver_bytes,
                metadata={"topic": topic, "member_id": member_id, "role": "solver", "content_type": "application/octet-stream"},
            )
            files.append(
                {
                    "role": "solver",
                    "name": solver_name,
                    "file_id": public_object_id(file_id),
                    "size": len(solver_bytes),
                    "content_type": "application/octet-stream",
                }
            )

        for handoff_name, handoff_bytes in handoff_files or []:
            file_id = self.bucket.upload_from_stream(
                handoff_name,
                handoff_bytes,
                metadata={"topic": topic, "member_id": member_id, "role": "handoff", "content_type": "application/octet-stream"},
            )
            files.append(
                {
                    "role": "handoff",
                    "name": handoff_name,
                    "file_id": public_object_id(file_id),
                    "size": len(handoff_bytes),
                    "content_type": "application/octet-stream",
                }
            )

        manifest = {
            "topic": topic,
            "member_id": member_id,
            "display_name": member["display_name"],
            "flag": flag,
            "files": files,
            "created_at": now,
        }
        result = self.final_artifacts.insert_one(manifest)
        manifest["_id"] = result.inserted_id
        public = self._public_final_artifact(manifest)
        self._emit_broker_event(topic, "final_artifact", public)
        self.members.update_one({"_id": member["_id"]}, {"$set": {"last_seen_at": now}})
        return public

    def list_final_artifacts(self, topic: str) -> list[dict[str, Any]]:
        return [self._public_final_artifact(doc) for doc in self.final_artifacts.find({"topic": topic}).sort("created_at", DESCENDING)]

    def get_final_artifact(self, artifact_id: str) -> dict[str, Any]:
        doc = self.final_artifacts.find_one({"_id": ObjectId(artifact_id)})
        if doc is None:
            raise KeyError(artifact_id)
        return self._public_final_artifact(doc)

    def download_file(self, file_id: str) -> tuple[bytes, dict[str, Any]]:
        grid_out = self.bucket.open_download_stream(ObjectId(file_id))
        data = grid_out.read()
        metadata = dict(grid_out.metadata or {})
        metadata["filename"] = grid_out.filename
        metadata["length"] = grid_out.length
        return data, metadata

    def list_broker_events(self, topic: str, *, after_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(limit, 500))
        query: dict[str, Any] = {"topic": topic}
        if after_id:
            try:
                query["_id"] = {"$gt": ObjectId(after_id)}
            except Exception as exc:
                raise ValueError(f"Invalid event id: {after_id}") from exc
        cursor = self.broker_events.find(query).sort("_id", ASCENDING).limit(bounded_limit)
        return [self._public_broker_event(doc) for doc in cursor]

    def watch_events(self, topic: str, *, stop_event: Any | None = None) -> Iterable[dict[str, Any]]:
        pipeline = [{"$match": {"fullDocument.topic": topic}}]
        try:
            with self.broker_events.watch(
                pipeline,
                full_document="updateLookup",
                max_await_time_ms=1000,
            ) as stream:
                for change in stream:
                    if stop_event is not None and stop_event.is_set():
                        return
                    full_document = change.get("fullDocument")
                    if not isinstance(full_document, dict):
                        continue
                    yield self._public_broker_event(full_document)
            return
        except PyMongoError:
            pass

        last_seen_id: ObjectId | None = None
        while stop_event is None or not stop_event.is_set():
            query: dict[str, Any] = {"topic": topic}
            if last_seen_id is not None:
                query["_id"] = {"$gt": last_seen_id}
            emitted = False
            for doc in self.broker_events.find(query).sort("_id", ASCENDING):
                if stop_event is not None and stop_event.is_set():
                    return
                emitted = True
                last_seen_id = doc["_id"]
                yield self._public_broker_event(doc)
            if not emitted:
                if stop_event is not None:
                    if stop_event.wait(0.5):
                        return
                else:
                    time.sleep(0.5)
