"""Hash API keys and strip secrets before they are written to Postgres."""

import hashlib
import json
from typing import Any

_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
    "x-api-key",
    "x_api_key",
}


def fingerprint_api_key(api_key: str | None) -> tuple[str | None, str | None]:
    if not api_key:
        return None, None
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    hint = api_key[-4:] if len(api_key) >= 4 else "****"
    return f"sha256:{digest}", hint


def anonymous_fingerprint(source_ip: str | None, user_agent: str | None, client_name: str | None) -> str:
    raw = "|".join([source_ip or "", user_agent or "", client_name or ""])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"anon:{digest}"


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if str(key).lower() in _SECRET_KEYS:
                cleaned[key] = "[redacted]"
            else:
                cleaned[key] = redact(item)
        return cleaned
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def parse_mcp_message(raw: bytes) -> dict[str, Any]:
    """Pull method, tool name, and arguments out of one JSON-RPC body."""
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        return {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    method = payload.get("method")
    tool_name = None
    arguments = None
    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments")
    request_id = payload.get("id")
    return {
        "mcp_method": method if isinstance(method, str) else None,
        "mcp_request_id": None if request_id is None else str(request_id)[:80],
        "tool_name": tool_name if isinstance(tool_name, str) else None,
        "tool_arguments": redact(arguments) if isinstance(arguments, dict) else None,
    }


def clip_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    if len(value) <= limit:
        return value
    return value[:limit] + "...[truncated]"
