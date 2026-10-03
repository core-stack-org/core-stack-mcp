"""Log every HTTP call except the health check."""

import logging
import time
import uuid
from typing import Any

from app.access import safe_record_access
from app.config import get_settings
from app.identity import CallOutcome, RequestIdentity, identity_var, outcome_var
from app.redact import fingerprint_api_key, parse_mcp_message

logger = logging.getLogger("corestack.access")
_SKIP_PATHS = {"/health"}


def _header(scope: dict, name: str) -> str | None:
    target = name.lower().encode("latin-1")
    for key, value in scope.get("headers") or []:
        if key.lower() == target:
            text = value.decode("latin-1", errors="replace").strip()
            return text or None
    return None


def _client_ip(scope: dict) -> tuple[str | None, str | None]:
    forwarded = _header(scope, "x-forwarded-for")
    client = scope.get("client")
    direct = client[0] if client else None
    if get_settings().trust_proxy and forwarded:
        return forwarded.split(",")[0].strip(), forwarded
    return direct, forwarded


class AccessLogMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or ""
        if path in _SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        body = await _read_body(receive)
        message = parse_mcp_message(body)
        source_ip, forwarded = _client_ip(scope)
        header_key = _header(scope, "x-api-key")
        api_key = header_key or get_settings().core_stack_api_key.strip() or None
        fingerprint, hint = fingerprint_api_key(api_key)
        identity = RequestIdentity(
            request_id=_header(scope, "x-request-id") or str(uuid.uuid4()),
            client_name=_header(scope, "x-client-name"),
            api_key=api_key,
            api_key_fingerprint=fingerprint,
            api_key_hint=hint,
            source_ip=source_ip,
            forwarded_for=forwarded,
            user_agent=_header(scope, "user-agent"),
        )
        outcome = CallOutcome()
        identity_token = identity_var.set(identity)
        outcome_token = outcome_var.set(outcome)
        status_code: int | None = None
        error_message = None

        async def send_wrapper(event: dict):
            nonlocal status_code
            if event["type"] == "http.response.start":
                status_code = event["status"]
            await send(event)

        replayed = False

        async def replay_receive():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        try:
            await self.app(scope, replay_receive, send_wrapper)
        except Exception as exc:
            error_message = str(exc)
            outcome.outcome = "error"
            outcome.error_message = error_message
            raise
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            recorded_outcome = outcome.outcome or _outcome_from_status(status_code)
            row = {
                "request_id": identity.request_id,
                "client_name": identity.client_name,
                "api_key_fingerprint": identity.api_key_fingerprint,
                "api_key_hint": identity.api_key_hint,
                "source_ip": identity.source_ip,
                "forwarded_for": identity.forwarded_for,
                "user_agent": identity.user_agent,
                "http_method": scope.get("method") or "",
                "path": path,
                "mcp_method": message.get("mcp_method"),
                "mcp_request_id": message.get("mcp_request_id"),
                "tool_name": message.get("tool_name"),
                "tool_arguments": message.get("tool_arguments"),
                "status_code": status_code,
                "outcome": recorded_outcome,
                "error_message": outcome.error_message or error_message,
                "duration_ms": duration_ms,
                "upstream_status": outcome.upstream_status,
                "response_bytes": outcome.response_bytes,
            }
            logger.info(
                "request_id=%s client=%s key=%s ip=%s method=%s path=%s mcp=%s tool=%s "
                "status=%s outcome=%s upstream=%s duration_ms=%s",
                identity.request_id,
                identity.client_name or "-",
                identity.api_key_hint or "-",
                identity.source_ip or "-",
                row["http_method"],
                path,
                row["mcp_method"] or "-",
                row["tool_name"] or "-",
                status_code,
                recorded_outcome,
                outcome.upstream_status,
                duration_ms,
            )
            await safe_record_access(row)
            outcome_var.reset(outcome_token)
            identity_var.reset(identity_token)


def _outcome_from_status(status_code: int | None) -> str:
    if status_code is None:
        return "error"
    if status_code >= 500:
        return "error"
    if status_code >= 400:
        return "client_error"
    return "success"


async def _read_body(receive) -> bytes:
    chunks: list[bytes] = []
    while True:
        message: dict[str, Any] = await receive()
        if message["type"] != "http.request":
            return b"".join(chunks)
        chunks.append(message.get("body") or b"")
        if not message.get("more_body", False):
            return b"".join(chunks)
