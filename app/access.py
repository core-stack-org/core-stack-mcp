"""Write one access row and update the client rollup. Failures stay in the app log."""

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccessLog, Client
from app.redact import anonymous_fingerprint, clip_text

logger = logging.getLogger("corestack.access")


async def record_access(session: AsyncSession, row: dict) -> None:
    now = datetime.now(timezone.utc)
    fingerprint = row.get("api_key_fingerprint")
    if not fingerprint:
        fingerprint = anonymous_fingerprint(
            row.get("source_ip"), row.get("user_agent"), row.get("client_name")
        )
        row = {**row, "api_key_fingerprint": fingerprint, "api_key_hint": None}

    arguments = row.get("tool_arguments")
    if isinstance(arguments, dict):
        encoded = json.dumps(arguments)
        if len(encoded) > 16_000:
            arguments = {"_truncated": encoded[:16_000]}

    log = AccessLog(
        request_id=row["request_id"],
        client_name=row.get("client_name"),
        api_key_fingerprint=fingerprint,
        api_key_hint=row.get("api_key_hint"),
        source_ip=row.get("source_ip"),
        forwarded_for=row.get("forwarded_for"),
        user_agent=clip_text(row.get("user_agent"), 500),
        http_method=row["http_method"],
        path=row["path"][:300],
        mcp_method=row.get("mcp_method"),
        mcp_request_id=row.get("mcp_request_id"),
        tool_name=row.get("tool_name"),
        tool_arguments=arguments if isinstance(arguments, dict) else row.get("tool_arguments"),
        status_code=row.get("status_code"),
        outcome=row.get("outcome") or "success",
        error_message=clip_text(row.get("error_message"), 2000),
        duration_ms=int(row.get("duration_ms") or 0),
        upstream_status=row.get("upstream_status"),
        response_bytes=row.get("response_bytes"),
    )
    session.add(log)

    client_name = row.get("client_name")
    existing = await session.scalar(
        select(Client).where(Client.api_key_fingerprint == fingerprint)
    )
    if existing is None:
        session.add(
            Client(
                api_key_fingerprint=fingerprint,
                api_key_hint=row.get("api_key_hint"),
                client_name=client_name,
                first_seen_at=now,
                last_seen_at=now,
                request_count=1,
                last_source_ip=row.get("source_ip"),
                last_user_agent=clip_text(row.get("user_agent"), 500),
                last_tool_name=row.get("tool_name"),
                last_path=row["path"][:300],
            )
        )
    else:
        existing.last_seen_at = now
        existing.request_count = (existing.request_count or 0) + 1
        if row.get("api_key_hint"):
            existing.api_key_hint = row["api_key_hint"]
        if client_name:
            existing.client_name = client_name
        existing.last_source_ip = row.get("source_ip")
        existing.last_user_agent = clip_text(row.get("user_agent"), 500)
        if row.get("tool_name"):
            existing.last_tool_name = row["tool_name"]
        existing.last_path = row["path"][:300]
    await session.commit()


async def safe_record_access(row: dict) -> None:
    from app.db import session_factory

    try:
        factory = session_factory()
    except RuntimeError:
        logger.warning("skipped access log because the database is not ready")
        return
    try:
        async with factory() as session:
            await record_access(session, row)
    except Exception:
        logger.exception("failed to write access log request_id=%s", row.get("request_id"))
