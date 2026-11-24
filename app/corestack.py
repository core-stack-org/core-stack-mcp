"""HTTP client for the CoRE Stack public API. Only whitelisted paths are called."""

import json
import logging
from typing import Any
from urllib.parse import urlparse

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from app.catalog import PublicAPI, public_api
from app.config import get_settings
from app.identity import current_identity, current_outcome
from app.present import present_response

logger = logging.getLogger("corestack.upstream")

_client: httpx.AsyncClient | None = None

_ERROR_MESSAGE_KEYS = ("message", "error", "detail", "msg", "reason")
_ERROR_META_KEYS = frozenset({"status", "status_code", "code"})
_MAX_ERROR_TEXT_CHARS = 2_000


class CoreStackAPIError(ToolError):
    """Raised when a CoRE Stack API call fails or returns an error payload."""

    def __init__(self, status_code: int, message: str, *, body: Any = None) -> None:
        self.status_code = status_code
        self.detail = message
        self.body = body
        text = f"HTTP {status_code}: {message}" if message else f"HTTP {status_code}"
        super().__init__(text)


def set_http_client(client: httpx.AsyncClient | None) -> None:
    global _client
    _client = client


def normalize_base_url(value: str) -> str:
    """Accept an http(s) CoRE Stack origin and drop a trailing slash."""
    text = value.strip().rstrip("/")
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("CoRE Stack base URL must be an http or https URL with a host")
    if parsed.username or parsed.password:
        raise ValueError("CoRE Stack base URL must not include a username or password")
    return text


def _api_key() -> str | None:
    identity = current_identity()
    if identity and identity.api_key:
        return identity.api_key
    fallback = get_settings().core_stack_api_key.strip()
    return fallback or None


def _clean_params(api: PublicAPI, params: dict[str, Any] | None) -> dict[str, str]:
    if not params:
        return {}
    unknown = sorted(set(params) - set(api.parameters))
    if unknown:
        allowed = ", ".join(api.parameters) or "(none)"
        raise ValueError(f"Unexpected parameters {unknown}. {api.id} accepts: {allowed}")
    cleaned: dict[str, str] = {}
    for key, value in params.items():
        if value is None or value == "":
            continue
        if isinstance(value, (dict, list)):
            raise ValueError(f"Parameter {key} must be a string or number, not a list or object")
        cleaned[key] = str(value)
    return cleaned


def _clip_error_text(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) <= _MAX_ERROR_TEXT_CHARS:
        return cleaned
    return cleaned[:_MAX_ERROR_TEXT_CHARS] + "...[truncated]"


def _extract_error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        primary: str | None = None
        used_key: str | None = None
        for key in _ERROR_MESSAGE_KEYS:
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                primary = val.strip()
                used_key = key
                break
        if primary is not None:
            extras = {
                k: v
                for k, v in payload.items()
                if k != used_key and k not in _ERROR_META_KEYS and v not in (None, "")
            }
            if extras:
                return _clip_error_text(f"{primary} ({json.dumps(extras)})")
            return _clip_error_text(primary)
        return _clip_error_text(json.dumps(payload))
    if isinstance(payload, str):
        return _clip_error_text(payload)
    if payload is None:
        return ""
    return _clip_error_text(json.dumps(payload))


def _is_error_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    status = payload.get("status")
    return isinstance(status, str) and status.strip().lower() == "error"


def _present(
    payload: Any,
    status_code: int,
    *,
    api_id: str | None = None,
    include_geometry: bool = False,
) -> dict[str, Any]:
    settings = get_settings()
    return present_response(
        payload,
        status_code,
        max_response_chars=settings.max_response_chars,
        api_id=api_id,
        include_geometry=include_geometry,
    )


async def call_api(
    api_id: str,
    params: dict[str, Any] | None = None,
    *,
    include_geometry: bool = False,
) -> dict[str, Any]:
    api = public_api(api_id)
    query = _clean_params(api, params)
    api_key = _api_key()
    outcome = current_outcome()
    if not api_key:
        message = "Send the caller's CoRE Stack API key in the X-API-Key header."
        outcome.outcome = "missing_api_key"
        outcome.error_message = "No X-API-Key header and no CORE_STACK_API_KEY"
        raise CoreStackAPIError(401, message)

    settings = get_settings()
    identity = current_identity()
    if identity and identity.core_stack_base_url_error:
        outcome.outcome = "client_error"
        outcome.error_message = identity.core_stack_base_url_error
        raise CoreStackAPIError(400, identity.core_stack_base_url_error)
    if identity and identity.core_stack_base_url:
        if not identity.api_key_from_header:
            message = (
                "Send X-API-Key together with X-Core-Stack-Base-Url. "
                "The server fallback key is only sent to CORE_STACK_BASE_URL."
            )
            outcome.outcome = "client_error"
            outcome.error_message = message
            raise CoreStackAPIError(400, message)
        base_url = identity.core_stack_base_url
    else:
        base_url = normalize_base_url(settings.core_stack_base_url)
    url = base_url + api.path
    client = _client or httpx.AsyncClient(timeout=settings.upstream_timeout_seconds)
    close_client = _client is None
    try:
        response = await client.get(
            url,
            params=query,
            headers={"X-API-Key": api_key, "Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        outcome.outcome = "upstream_error"
        outcome.error_message = str(exc)
        logger.warning("upstream request failed api=%s error=%s", api_id, exc)
        raise CoreStackAPIError(502, f"CoRE Stack request failed: {exc}") from exc
    finally:
        if close_client:
            await client.aclose()

    outcome.upstream_status = response.status_code
    outcome.response_bytes = len(response.content)

    content_type = response.headers.get("content-type", "").lower()
    if "json" in content_type:
        try:
            payload: Any = response.json()
        except json.JSONDecodeError:
            payload = response.text
    else:
        text = response.text
        if text.lstrip().startswith(("{", "[")):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = text
        else:
            payload = text

    if response.status_code >= 400 or _is_error_payload(payload):
        detail = _extract_error_message(payload)
        outcome.outcome = "upstream_error"
        outcome.error_message = (detail or response.text)[:500]
        raise CoreStackAPIError(response.status_code, detail, body=payload)

    outcome.outcome = "success"
    return _present(
        payload,
        response.status_code,
        api_id=api.id,
        include_geometry=include_geometry,
    )
