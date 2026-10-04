"""HTTP client for the CoRE Stack public API. Only whitelisted paths are called."""

import json
import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from app.catalog import PublicAPI, public_api
from app.config import get_settings
from app.identity import current_identity, current_outcome

logger = logging.getLogger("corestack.upstream")

_client: httpx.AsyncClient | None = None


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


def _present(payload: Any, status_code: int) -> dict[str, Any]:
    settings = get_settings()
    encoded = payload if isinstance(payload, str) else json.dumps(payload)
    truncated = False
    if len(encoded) > settings.max_response_chars:
        encoded = encoded[: settings.max_response_chars]
        truncated = True
    body: Any
    try:
        body = json.loads(encoded) if not truncated else encoded
    except json.JSONDecodeError:
        body = encoded
    result: dict[str, Any] = {"status_code": status_code, "body": body}
    if truncated:
        result["truncated"] = True
    return result


async def call_api(api_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    api = public_api(api_id)
    query = _clean_params(api, params)
    api_key = _api_key()
    outcome = current_outcome()
    if not api_key:
        outcome.outcome = "missing_api_key"
        outcome.error_message = "No X-API-Key header and no CORE_STACK_API_KEY"
        return {
            "status_code": 401,
            "error": "Send the caller's CoRE Stack API key in the X-API-Key header.",
        }

    settings = get_settings()
    identity = current_identity()
    if identity and identity.core_stack_base_url_error:
        outcome.outcome = "client_error"
        outcome.error_message = identity.core_stack_base_url_error
        return {"status_code": 400, "error": identity.core_stack_base_url_error}
    if identity and identity.core_stack_base_url:
        if not identity.api_key_from_header:
            message = (
                "Send X-API-Key together with X-Core-Stack-Base-Url. "
                "The server fallback key is only sent to CORE_STACK_BASE_URL."
            )
            outcome.outcome = "client_error"
            outcome.error_message = message
            return {"status_code": 400, "error": message}
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
        return {"status_code": 502, "error": f"CoRE Stack request failed: {exc}"}
    finally:
        if close_client:
            await client.aclose()

    outcome.upstream_status = response.status_code
    outcome.response_bytes = len(response.content)
    if response.status_code >= 400:
        outcome.outcome = "upstream_error"
        outcome.error_message = response.text[:500]
    else:
        outcome.outcome = "success"

    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        try:
            payload: Any = response.json()
        except json.JSONDecodeError:
            payload = response.text
    else:
        payload = response.text
    return _present(payload, response.status_code)
