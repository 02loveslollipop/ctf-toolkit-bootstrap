"""Configuration helpers for OpenCROW Constellation."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


CONFIG_PATH = Path.home() / ".config" / "opencrow" / "constellation" / "config.json"
DEFAULT_STATE_DIR_NAME = ".opencrow-constellation"
DEFAULT_DEVELOPMENT_TOKEN = "development-token-change-me"


def _load_config_file(path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _env_or_config(
    env_name: str,
    config: dict[str, Any],
    config_key: str,
    default: Any = None,
) -> Any:
    value = os.environ.get(env_name)
    if value is not None:
        return value
    if config_key in config:
        return config[config_key]
    return default


def _normalize_http_base(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    if not cleaned:
        return "http://127.0.0.1:8787"
    if "://" not in cleaned:
        cleaned = f"http://{cleaned}"
    return cleaned


def _normalize_ws_base(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    if not cleaned:
        return "ws://127.0.0.1:8787"
    if "://" not in cleaned:
        cleaned = f"ws://{cleaned}"
    return cleaned


def default_ws_base_from_api(api_base_url: str) -> str:
    parts = urlsplit(_normalize_http_base(api_base_url))
    scheme = "wss" if parts.scheme == "https" else "ws"
    return urlunsplit((scheme, parts.netloc, "", "", ""))


def parse_token_list(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple, set)):
        return tuple(str(token).strip() for token in raw if str(token).strip())
    if isinstance(raw, str):
        return tuple(token.strip() for token in raw.split(",") if token.strip())
    text = str(raw).strip()
    return (text,) if text else ()


@dataclass(frozen=True)
class ClientSettings:
    api_base_url: str
    ws_base_url: str
    token: str
    private_prompt: str | None
    private_prompt_file: str | None
    state_dir_name: str
    request_timeout_sec: int
    prompt_output_name: str


@dataclass(frozen=True)
class BackendSettings:
    mongo_uri: str
    mongo_db_name: str
    listen_host: str
    listen_port: int
    system_tokens: tuple[str, ...]
    broker_event_ttl_hours: int
    allowed_ws_origins: tuple[str, ...]
    ui_shared_secret: str | None


@dataclass(frozen=True)
class UISettings:
    backend_api_base_url: str
    backend_ws_base_url: str
    listen_host: str
    listen_port: int
    secret_key: str
    default_display_name: str
    shared_secret: str | None


@dataclass(frozen=True)
class RuntimeSettings:
    control_api_base_url: str
    control_ws_base_url: str
    token: str
    runtime_id: str
    display_name: str
    workspace_root: str
    codex_model: str | None
    codex_bin: str | None
    reconnect_delay_sec: int


def load_client_settings(*, overrides: dict[str, Any] | None = None) -> ClientSettings:
    config = _load_config_file()
    merged = dict(config)
    if overrides:
        merged.update({key: value for key, value in overrides.items() if value is not None})

    api_base_url = _normalize_http_base(
        str(
            _env_or_config(
                "OPENCROW_CONSTELLATION_API_BASE_URL",
                merged,
                "api_base_url",
                "http://127.0.0.1:8787",
            )
        )
    )
    ws_base_url = _env_or_config(
        "OPENCROW_CONSTELLATION_WS_BASE_URL",
        merged,
        "ws_base_url",
        None,
    )
    token = str(
        _env_or_config(
            "OPENCROW_CONSTELLATION_TOKEN",
            merged,
            "token",
            DEFAULT_DEVELOPMENT_TOKEN,
        )
    )
    private_prompt = _env_or_config(
        "OPENCROW_CONSTELLATION_PRIVATE_PROMPT",
        merged,
        "private_prompt",
        None,
    )
    private_prompt_file = _env_or_config(
        "OPENCROW_CONSTELLATION_PRIVATE_PROMPT_FILE",
        merged,
        "private_prompt_file",
        None,
    )
    state_dir_name = str(
        _env_or_config(
            "OPENCROW_CONSTELLATION_STATE_DIR",
            merged,
            "state_dir_name",
            DEFAULT_STATE_DIR_NAME,
        )
    )
    request_timeout_sec = int(
        _env_or_config(
            "OPENCROW_CONSTELLATION_REQUEST_TIMEOUT_SEC",
            merged,
            "request_timeout_sec",
            20,
        )
    )
    prompt_output_name = str(
        _env_or_config(
            "OPENCROW_CONSTELLATION_PROMPT_OUTPUT_NAME",
            merged,
            "prompt_output_name",
            "generated-prompt.md",
        )
    )
    resolved_ws = _normalize_ws_base(str(ws_base_url)) if ws_base_url else default_ws_base_from_api(api_base_url)
    return ClientSettings(
        api_base_url=api_base_url,
        ws_base_url=resolved_ws,
        token=token,
        private_prompt=str(private_prompt) if private_prompt is not None else None,
        private_prompt_file=str(private_prompt_file) if private_prompt_file is not None else None,
        state_dir_name=state_dir_name,
        request_timeout_sec=request_timeout_sec,
        prompt_output_name=prompt_output_name,
    )


def load_backend_settings() -> BackendSettings:
    config = _load_config_file()
    raw_tokens = os.environ.get("OPENCROW_CONSTELLATION_SYSTEM_TOKENS")
    if raw_tokens is None:
        raw_tokens = os.environ.get("OPENCROW_CONSTELLATION_SYSTEM_TOKEN")
    if raw_tokens is None:
        raw_tokens = config.get("system_tokens", DEFAULT_DEVELOPMENT_TOKEN)

    tokens = parse_token_list(raw_tokens)
    if not tokens:
        tokens = (DEFAULT_DEVELOPMENT_TOKEN,)

    return BackendSettings(
        mongo_uri=str(
            _env_or_config(
                "OPENCROW_CONSTELLATION_MONGO_URI",
                config,
                "mongo_uri",
                "mongodb://127.0.0.1:27017",
            )
        ),
        mongo_db_name=str(
            _env_or_config(
                "OPENCROW_CONSTELLATION_MONGO_DB",
                config,
                "mongo_db_name",
                "opencrow_constellation",
            )
        ),
        listen_host=str(
            _env_or_config(
                "OPENCROW_CONSTELLATION_BACKEND_HOST",
                config,
                "backend_host",
                "0.0.0.0",
            )
        ),
        listen_port=int(
            _env_or_config(
                "OPENCROW_CONSTELLATION_BACKEND_PORT",
                config,
                "backend_port",
                8787,
            )
        ),
        system_tokens=tokens,
        broker_event_ttl_hours=int(
            _env_or_config(
                "OPENCROW_CONSTELLATION_BROKER_EVENT_TTL_HOURS",
                config,
                "broker_event_ttl_hours",
                24,
            )
        ),
        allowed_ws_origins=parse_token_list(
            _env_or_config(
                "OPENCROW_CONSTELLATION_ALLOWED_WS_ORIGINS",
                config,
                "allowed_ws_origins",
                "http://127.0.0.1:8788,http://localhost:8788",
            )
        ),
        ui_shared_secret=str(secret)
        if (secret := _env_or_config("OPENCROW_CONSTELLATION_UI_SHARED_SECRET", config, "ui_shared_secret", ""))
        else None,
    )


def load_ui_settings() -> UISettings:
    config = _load_config_file()
    api_base = _normalize_http_base(
        str(
            _env_or_config(
                "OPENCROW_CONSTELLATION_UI_BACKEND_API_BASE_URL",
                config,
                "ui_backend_api_base_url",
                "http://127.0.0.1:8787",
            )
        )
    )
    ws_base = _env_or_config(
        "OPENCROW_CONSTELLATION_UI_BACKEND_WS_BASE_URL",
        config,
        "ui_backend_ws_base_url",
        None,
    )
    return UISettings(
        backend_api_base_url=api_base,
        backend_ws_base_url=_normalize_ws_base(str(ws_base)) if ws_base else default_ws_base_from_api(api_base),
        listen_host=str(
            _env_or_config(
                "OPENCROW_CONSTELLATION_UI_HOST",
                config,
                "ui_host",
                "0.0.0.0",
            )
        ),
        listen_port=int(
            _env_or_config(
                "OPENCROW_CONSTELLATION_UI_PORT",
                config,
                "ui_port",
                8788,
            )
        ),
        secret_key=str(
            _env_or_config(
                "OPENCROW_CONSTELLATION_UI_SECRET_KEY",
                config,
                "ui_secret_key",
                "opencrow-constellation-ui-dev-secret",
            )
        ),
        default_display_name=str(
            _env_or_config(
                "OPENCROW_CONSTELLATION_UI_DISPLAY_NAME",
                config,
                "ui_display_name",
                "OpenCROW UI",
            )
        ),
        shared_secret=str(secret)
        if (secret := _env_or_config(
            "OPENCROW_CONSTELLATION_UI_SHARED_SECRET",
            config,
            "ui_shared_secret",
            "opencrow-constellation-ui-dev-secret",
        ))
        else None,
    )


def load_runtime_settings() -> RuntimeSettings:
    config = _load_config_file()
    api_base = _normalize_http_base(
        str(
            _env_or_config(
                "OPENCROW_RUNTIME_CONTROL_API_BASE_URL",
                config,
                "runtime_control_api_base_url",
                "http://127.0.0.1:8787",
            )
        )
    )
    ws_base = _env_or_config(
        "OPENCROW_RUNTIME_CONTROL_WS_BASE_URL",
        config,
        "runtime_control_ws_base_url",
        None,
    )
    return RuntimeSettings(
        control_api_base_url=api_base,
        control_ws_base_url=_normalize_ws_base(str(ws_base)) if ws_base else default_ws_base_from_api(api_base),
        token=str(
            _env_or_config(
                "OPENCROW_RUNTIME_TOKEN",
                config,
                "runtime_token",
                DEFAULT_DEVELOPMENT_TOKEN,
            )
        ),
        runtime_id=str(
            _env_or_config(
                "OPENCROW_RUNTIME_ID",
                config,
                "runtime_id",
                "",
            )
        ),
        display_name=str(
            _env_or_config(
                "OPENCROW_RUNTIME_DISPLAY_NAME",
                config,
                "runtime_display_name",
                "",
            )
        ),
        workspace_root=str(
            _env_or_config(
                "OPENCROW_RUNTIME_WORKSPACE_ROOT",
                config,
                "runtime_workspace_root",
                "~/.local/share/opencrow/runtime-workspaces",
            )
        ),
        codex_model=str(model)
        if (model := _env_or_config("OPENCROW_RUNTIME_CODEX_MODEL", config, "runtime_codex_model", ""))
        else None,
        codex_bin=str(codex_bin)
        if (codex_bin := _env_or_config("OPENCROW_RUNTIME_CODEX_BIN", config, "runtime_codex_bin", ""))
        else None,
        reconnect_delay_sec=int(
            _env_or_config(
                "OPENCROW_RUNTIME_RECONNECT_DELAY_SEC",
                config,
                "runtime_reconnect_delay_sec",
                5,
            )
        ),
    )
