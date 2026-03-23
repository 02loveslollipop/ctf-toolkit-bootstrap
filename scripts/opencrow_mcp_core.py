#!/usr/bin/env python3
"""Shared stdio MCP helpers for OpenCROW toolbox servers."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlsplit


JSON = dict[str, Any]
Handler = Callable[[JSON], JSON]
ResourceHandler = Callable[[str], list[JSON]]
TemplateHandler = Callable[[str, dict[str, str]], list[JSON]]
SUPPORTED_PROTOCOL_VERSIONS = (
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
)
DEFAULT_PROTOCOL_VERSION = "2024-11-05"
CONTENT_LENGTH_FRAMING = "content-length"
JSON_LINE_FRAMING = "json-line"


@dataclass(frozen=True)
class MCPTool:
    name: str
    description: str
    input_schema: JSON
    handler: Handler


@dataclass(frozen=True)
class MCPResource:
    uri: str
    name: str
    description: str
    mime_type: str
    handler: ResourceHandler


@dataclass(frozen=True)
class MCPResourceTemplate:
    uri_template: str
    name: str
    description: str
    mime_type: str
    handler: TemplateHandler


def normalize_path(value: str | Path | None) -> str | None:
    if value is None:
        return None
    return str(Path(value).expanduser().resolve())


def summarize_command(command: list[str]) -> str:
    return subprocess.list2cmdline(command)


def decode_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


def run_command(
    command: list[str],
    *,
    cwd: str | Path | None = None,
    timeout_sec: int = 120,
    env: dict[str, str] | None = None,
) -> JSON:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            capture_output=True,
            check=False,
            timeout=timeout_sec,
        )
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "stdout": "",
            "stdout_bytes": b"",
            "stderr": str(exc),
            "stderr_bytes": b"",
            "exit_code": 127,
            "command": summarize_command(command),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "stdout": decode_output(exc.stdout),
            "stdout_bytes": exc.stdout if isinstance(exc.stdout, bytes) else (exc.stdout.encode("utf-8") if exc.stdout else b""),
            "stderr": decode_output(exc.stderr) or f"Timed out after {timeout_sec} seconds.",
            "stderr_bytes": exc.stderr if isinstance(exc.stderr, bytes) else (exc.stderr.encode("utf-8") if exc.stderr else b""),
            "exit_code": 124,
            "command": summarize_command(command),
        }

    return {
        "ok": completed.returncode == 0,
        "stdout": decode_output(completed.stdout),
        "stdout_bytes": completed.stdout,
        "stderr": decode_output(completed.stderr),
        "stderr_bytes": completed.stderr,
        "exit_code": completed.returncode,
        "command": summarize_command(command),
    }


def success_envelope(
    *,
    toolbox: str,
    operation: str,
    summary: str,
    inputs: JSON,
    artifacts: list[str] | None = None,
    observations: list[JSON] | None = None,
    command: str | None = None,
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = None,
    next_steps: list[str] | None = None,
) -> JSON:
    return {
        "ok": True,
        "summary": summary,
        "toolbox": toolbox,
        "operation": operation,
        "inputs": inputs,
        "artifacts": artifacts or [],
        "observations": observations or [],
        "command": command,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "next_steps": next_steps or [],
    }


def error_envelope(
    *,
    toolbox: str,
    operation: str,
    summary: str,
    inputs: JSON,
    artifacts: list[str] | None = None,
    command: str | None = None,
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = None,
    observations: list[JSON] | None = None,
    next_steps: list[str] | None = None,
) -> JSON:
    return {
        "ok": False,
        "summary": summary,
        "toolbox": toolbox,
        "operation": operation,
        "inputs": inputs,
        "artifacts": artifacts or [],
        "observations": observations or [],
        "command": command,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "next_steps": next_steps or [],
    }


def missing_dependency_envelope(toolbox: str, operation: str, dependency: str, inputs: JSON) -> JSON:
    return error_envelope(
        toolbox=toolbox,
        operation=operation,
        summary=f"Required dependency is not available: {dependency}",
        inputs=inputs,
        stderr=f"Dependency not found: {dependency}",
        exit_code=127,
        next_steps=[f"Install or expose `{dependency}` before retrying `{operation}`."],
    )


def serialize_tool_result(envelope: JSON) -> JSON:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(envelope, indent=2, sort_keys=True),
            }
        ],
        "isError": not envelope.get("ok", False),
    }


def text_resource_contents(uri: str, text: str, *, mime_type: str = "text/plain") -> list[JSON]:
    return [
        {
            "uri": uri,
            "mimeType": mime_type,
            "text": text,
        }
    ]


def json_resource_contents(uri: str, payload: Any) -> list[JSON]:
    return text_resource_contents(uri, json.dumps(payload, indent=2, sort_keys=True), mime_type="application/json")


def static_text_resource(
    *,
    uri: str,
    name: str,
    description: str,
    text: str | Callable[[], str],
    mime_type: str = "text/plain",
) -> MCPResource:
    def handler(resource_uri: str) -> list[JSON]:
        value = text() if callable(text) else text
        return text_resource_contents(resource_uri, value, mime_type=mime_type)

    return MCPResource(
        uri=uri,
        name=name,
        description=description,
        mime_type=mime_type,
        handler=handler,
    )


def static_json_resource(
    *,
    uri: str,
    name: str,
    description: str,
    payload: Any | Callable[[], Any],
) -> MCPResource:
    def handler(resource_uri: str) -> list[JSON]:
        value = payload() if callable(payload) else payload
        return json_resource_contents(resource_uri, value)

    return MCPResource(
        uri=uri,
        name=name,
        description=description,
        mime_type="application/json",
        handler=handler,
    )


def match_uri_template(uri_template: str, uri: str) -> dict[str, str] | None:
    template_parts = urlsplit(uri_template)
    uri_parts = urlsplit(uri)
    if (
        template_parts.scheme != uri_parts.scheme
        or template_parts.netloc != uri_parts.netloc
        or template_parts.query != uri_parts.query
        or template_parts.fragment != uri_parts.fragment
    ):
        return None

    template_segments = [segment for segment in template_parts.path.split("/") if segment]
    uri_segments = [segment for segment in uri_parts.path.split("/") if segment]
    if len(template_segments) != len(uri_segments):
        return None

    params: dict[str, str] = {}
    for template_segment, uri_segment in zip(template_segments, uri_segments):
        match = re.fullmatch(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template_segment)
        if match is not None:
            params[match.group(1)] = unquote(uri_segment)
            continue
        if template_segment != uri_segment:
            return None

    return params


def make_toolbox_info_handler(
    *,
    toolbox: str,
    display_name: str,
    server_name: str,
    server_version: str,
    summary: str,
    operations: list[JSON],
) -> Handler:
    def handler(arguments: JSON) -> JSON:
        return success_envelope(
            toolbox=toolbox,
            operation="toolbox_info",
            summary=summary,
            inputs=arguments,
            observations=[
                {
                    "display_name": display_name,
                    "server_name": server_name,
                    "server_version": server_version,
                    "transport": "stdio",
                    "protocol_baseline": DEFAULT_PROTOCOL_VERSION,
                    "supported_protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
                    "message_framings": [CONTENT_LENGTH_FRAMING, JSON_LINE_FRAMING],
                },
                {
                    "operations": operations,
                },
            ],
            next_steps=["Call `toolbox_capabilities` to inspect the structured operations this server exposes."],
        )

    return handler


def make_toolbox_self_test_handler(
    *,
    toolbox: str,
    display_name: str,
    server_name: str,
    server_version: str,
    operations: list[JSON],
) -> Handler:
    def handler(arguments: JSON) -> JSON:
        return success_envelope(
            toolbox=toolbox,
            operation="toolbox_self_test",
            summary=f"{display_name} self-test passed.",
            inputs=arguments,
            observations=[
                {
                    "status": "ready",
                    "display_name": display_name,
                    "server_name": server_name,
                    "server_version": server_version,
                    "transport": "stdio",
                    "protocol_baseline": DEFAULT_PROTOCOL_VERSION,
                    "supported_protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
                    "message_framings": [CONTENT_LENGTH_FRAMING, JSON_LINE_FRAMING],
                    "operation_count": len(operations),
                    "registered_tool_count": len(operations) + 4,
                }
            ],
            next_steps=[
                "Call `toolbox_capabilities` to inspect the structured operations this server exposes.",
                "Call `toolbox_verify` when you need dependency status for the current environment.",
            ],
        )

    return handler


def make_toolbox_capabilities_handler(toolbox: str, operations: list[JSON]) -> Handler:
    def handler(arguments: JSON) -> JSON:
        return success_envelope(
            toolbox=toolbox,
            operation="toolbox_capabilities",
            summary=f"{toolbox} capabilities returned.",
            inputs=arguments,
            observations=operations,
        )

    return handler


class StdioMCPServer:
    def __init__(self, *, server_name: str, server_version: str, instructions: str | None = None) -> None:
        self.server_name = server_name
        self.server_version = server_version
        self.instructions = instructions
        self.tools: dict[str, MCPTool] = {}
        self.resources: dict[str, MCPResource] = {}
        self.resource_templates: list[MCPResourceTemplate] = []
        self._message_framing = CONTENT_LENGTH_FRAMING

    def register_tool(self, tool: MCPTool) -> None:
        self.tools[tool.name] = tool

    def register_tools(self, tools: list[MCPTool]) -> None:
        for tool in tools:
            self.register_tool(tool)

    def register_resource(self, resource: MCPResource) -> None:
        self.resources[resource.uri] = resource

    def register_resources(self, resources: list[MCPResource]) -> None:
        for resource in resources:
            self.register_resource(resource)

    def register_resource_template(self, resource_template: MCPResourceTemplate) -> None:
        self.resource_templates.append(resource_template)

    def register_resource_templates(self, resource_templates: list[MCPResourceTemplate]) -> None:
        for resource_template in resource_templates:
            self.register_resource_template(resource_template)

    def _tool_descriptors(self) -> list[JSON]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
            }
            for tool in self.tools.values()
        ]

    def _base_resource_uri(self) -> str:
        return f"opencrow://{self.server_name}"

    def _builtin_resources(self) -> dict[str, MCPResource]:
        base_uri = self._base_resource_uri()
        return {
            f"{base_uri}/server": static_json_resource(
                uri=f"{base_uri}/server",
                name=f"{self.server_name} server metadata",
                description="Server metadata, instructions, and resource summary for this OpenCROW MCP server.",
                payload=self._server_metadata_payload,
            ),
            f"{base_uri}/capabilities": static_json_resource(
                uri=f"{base_uri}/capabilities",
                name=f"{self.server_name} capabilities",
                description="Structured tool, resource, and template descriptors for this OpenCROW MCP server.",
                payload=self._capabilities_payload,
            ),
            f"{base_uri}/verify-guide": static_text_resource(
                uri=f"{base_uri}/verify-guide",
                name=f"{self.server_name} verification guide",
                description="Quick usage guidance for readiness checks, capability inspection, and typed tool execution.",
                text=self._verify_guide_text,
                mime_type="text/markdown",
            ),
        }

    def _builtin_resource_templates(self) -> list[MCPResourceTemplate]:
        base_uri = self._base_resource_uri()
        return [
            MCPResourceTemplate(
                uri_template=f"{base_uri}/tools/{{name}}",
                name=f"{self.server_name} tool descriptor",
                description="Read the full descriptor, schema, and usage hints for a named MCP tool exposed by this server.",
                mime_type="application/json",
                handler=self._read_builtin_tool_template,
            )
        ]

    def _all_resources(self) -> dict[str, MCPResource]:
        resources = self._builtin_resources()
        resources.update(self.resources)
        return resources

    def _all_resource_templates(self) -> list[MCPResourceTemplate]:
        return [*self._builtin_resource_templates(), *self.resource_templates]

    def _resource_descriptors(self) -> list[JSON]:
        return [
            {
                "uri": resource.uri,
                "name": resource.name,
                "description": resource.description,
                "mimeType": resource.mime_type,
            }
            for resource in self._all_resources().values()
        ]

    def _resource_template_descriptors(self) -> list[JSON]:
        return [
            {
                "uriTemplate": resource_template.uri_template,
                "name": resource_template.name,
                "description": resource_template.description,
                "mimeType": resource_template.mime_type,
            }
            for resource_template in self._all_resource_templates()
        ]

    def _server_metadata_payload(self) -> JSON:
        return {
            "serverInfo": {
                "name": self.server_name,
                "version": self.server_version,
            },
            "instructions": self.instructions or "",
            "protocolVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "messageFramings": [CONTENT_LENGTH_FRAMING, JSON_LINE_FRAMING],
            "counts": {
                "tools": len(self.tools),
                "resources": len(self._all_resources()),
                "resource_templates": len(self._all_resource_templates()),
            },
        }

    def _capabilities_payload(self) -> JSON:
        return {
            "serverInfo": {
                "name": self.server_name,
                "version": self.server_version,
            },
            "initializeCapabilities": {
                "tools": {
                    "listChanged": False,
                },
                "resources": {
                    "subscribe": False,
                    "listChanged": False,
                },
            },
            "protocolVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "messageFramings": [CONTENT_LENGTH_FRAMING, JSON_LINE_FRAMING],
            "tools": self._tool_descriptors(),
            "resources": self._resource_descriptors(),
            "resourceTemplates": self._resource_template_descriptors(),
        }

    def _verify_guide_text(self) -> str:
        lines = [
            f"# {self.server_name}",
            "",
            "- Call `toolbox_self_test` for a lightweight readiness probe.",
            "- Call `toolbox_verify` when you need dependency or environment diagnostics.",
            "- Read the `/capabilities` resource for the structured tool and resource catalog.",
            "- Read `opencrow://{self.server_name}/tools/<tool-name>` for a tool-specific schema snapshot.",
        ]
        if self.instructions:
            lines.extend(["", "## Instructions", "", self.instructions])
        return "\n".join(lines)

    def _read_builtin_tool_template(self, uri: str, params: dict[str, str]) -> list[JSON]:
        tool_name = params.get("name", "").strip()
        tool = self.tools.get(tool_name)
        if tool is None:
            raise KeyError(f"Unknown tool resource: {tool_name}")
        payload = {
            "serverInfo": {
                "name": self.server_name,
                "version": self.server_version,
            },
            "tool": {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
            },
        }
        return json_resource_contents(uri, payload)

    def _read_resource(self, uri: str) -> list[JSON]:
        resource = self._all_resources().get(uri)
        if resource is not None:
            return resource.handler(uri)

        for resource_template in self._all_resource_templates():
            params = match_uri_template(resource_template.uri_template, uri)
            if params is not None:
                return resource_template.handler(uri, params)

        raise KeyError(f"Unknown resource: {uri}")

    def serve(self) -> int:
        stdin = sys.stdin.buffer
        stdout = sys.stdout.buffer

        while True:
            request = self._read_message(stdin)
            if request is None:
                return 0
            response = self._handle_message(request)
            if response is None:
                continue
            self._write_message(stdout, response)

    def _handle_message(self, request: JSON) -> JSON | None:
        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params", {})

        if method == "notifications/initialized":
            return None

        if method == "initialize":
            client_version = str(params.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION)
            protocol_version = (
                client_version
                if client_version in SUPPORTED_PROTOCOL_VERSIONS
                else DEFAULT_PROTOCOL_VERSION
            )
            return self._result(
                request_id,
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {
                        "tools": {
                            "listChanged": False,
                        },
                        "resources": {
                            "subscribe": False,
                            "listChanged": False,
                        },
                    },
                    "serverInfo": {
                        "name": self.server_name,
                        "version": self.server_version,
                    },
                    "instructions": self.instructions or "",
                },
            )

        if method == "ping":
            return self._result(request_id, {})

        if method == "tools/list":
            return self._result(request_id, {"tools": self._tool_descriptors()})

        if method == "tools/call":
            tool_name = params.get("name")
            if tool_name not in self.tools:
                return self._error(request_id, -32602, f"Unknown tool: {tool_name}")
            arguments = params.get("arguments") or {}
            try:
                envelope = self.tools[tool_name].handler(arguments)
            except Exception as exc:  # pragma: no cover - defensive server path
                envelope = error_envelope(
                    toolbox=self.server_name,
                    operation=str(tool_name),
                    summary=f"Unhandled exception while running {tool_name}",
                    inputs=arguments if isinstance(arguments, dict) else {"arguments": arguments},
                    stderr=f"{exc}\n{traceback.format_exc()}",
                )
            return self._result(request_id, serialize_tool_result(envelope))

        if method == "resources/list":
            return self._result(request_id, {"resources": self._resource_descriptors()})

        if method == "resources/templates/list":
            return self._result(request_id, {"resourceTemplates": self._resource_template_descriptors()})

        if method == "resources/read":
            uri = params.get("uri")
            if not isinstance(uri, str) or not uri.strip():
                return self._error(request_id, -32602, "A non-empty `uri` is required.")
            try:
                contents = self._read_resource(uri.strip())
            except KeyError as exc:
                return self._error(request_id, -32602, str(exc))
            except ValueError as exc:
                return self._error(request_id, -32602, str(exc))
            except Exception as exc:  # pragma: no cover - defensive server path
                return self._error(request_id, -32603, f"Unhandled exception while reading resource: {exc}")
            return self._result(request_id, {"contents": contents})

        return self._error(request_id, -32601, f"Method not found: {method}")

    @staticmethod
    def _result(request_id: Any, result: JSON) -> JSON:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> JSON:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": code,
                "message": message,
            },
        }

    def _read_message(self, stream: Any) -> JSON | None:
        while True:
            line = stream.readline()
            if not line:
                return None
            if line in (b"\r\n", b"\n"):
                continue

            stripped = line.rstrip(b"\r\n")
            if not stripped:
                continue

            if stripped.lstrip().startswith((b"{", b"[")):
                self._message_framing = JSON_LINE_FRAMING
                return json.loads(stripped.decode("utf-8"))

            headers: dict[str, str] = {}
            decoded = stripped.decode("utf-8").strip()
            name, value = decoded.split(":", 1)
            headers[name.lower()] = value.strip()

            while True:
                header_line = stream.readline()
                if not header_line:
                    return None
                if header_line in (b"\r\n", b"\n"):
                    break
                header_text = header_line.decode("utf-8").strip()
                if not header_text:
                    break
                header_name, header_value = header_text.split(":", 1)
                headers[header_name.lower()] = header_value.strip()

            self._message_framing = CONTENT_LENGTH_FRAMING
            content_length = int(headers.get("content-length", "0"))
            if content_length <= 0:
                return None

            body = stream.read(content_length)
            if not body:
                return None
            return json.loads(body.decode("utf-8"))

    def _write_message(self, stream: Any, payload: JSON) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        if self._message_framing == JSON_LINE_FRAMING:
            stream.write((body + "\n").encode("utf-8"))
            stream.flush()
            return

        encoded_body = body.encode("utf-8")
        header = f"Content-Length: {len(encoded_body)}\r\nContent-Type: application/json\r\n\r\n".encode("utf-8")
        stream.write(header)
        stream.write(encoded_body)
        stream.flush()


def command_exists(name: str) -> bool:
    from shutil import which

    return which(name) is not None


def conda_module_available(env_name: str, module_name: str) -> bool:
    code = f"import importlib.util; raise SystemExit(0 if importlib.util.find_spec('{module_name}') else 1)"
    try:
        result = subprocess.run(
            ["conda", "run", "-n", env_name, "python", "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def default_execution(arguments: JSON) -> tuple[str | None, int]:
    execution = arguments.get("execution") if isinstance(arguments.get("execution"), dict) else {}
    cwd = normalize_path(execution.get("cwd")) if execution else None
    timeout_sec = int(execution.get("timeout_sec", 120)) if execution else 120
    return cwd, timeout_sec


def merge_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if extra:
        env.update(extra)
    return env


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_execution_path(value: str | Path | None, *, cwd: str | Path | None = None) -> str | None:
    if value is None:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        base = Path(cwd) if cwd is not None else Path.cwd()
        path = base / path
    return str(path.resolve())


def execution_transcript_path(arguments: JSON) -> str | None:
    execution = arguments.get("execution") if isinstance(arguments.get("execution"), dict) else {}
    if not execution:
        return None
    cwd = normalize_path(execution.get("cwd"))
    raw_path = execution.get("transcript_path")
    if raw_path is None or not str(raw_path).strip():
        return None
    return _resolve_execution_path(str(raw_path).strip(), cwd=cwd)


def append_jsonl(path: str | Path, payload: JSON) -> str:
    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    return str(output_path)
