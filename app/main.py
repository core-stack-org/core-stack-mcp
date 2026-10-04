"""FastAPI application: MCP endpoint, health check, and access-log reader."""

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import desc, select

from app.config import get_settings
from app.corestack import set_http_client
from app.db import create_tables, dispose_engine, init_engine, session_factory
from app.mcp_app import mcp
from app.middleware import AccessLogMiddleware, McpSlashMiddleware
from app.models import AccessLog, Client

logger = logging.getLogger("corestack")
settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def _transport_security() -> TransportSecuritySettings | None:
    hosts = settings.allowed_hosts
    if not hosts:
        return None
    if hosts == ["*"]:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    allowed = []
    for host in hosts:
        allowed.append(host)
        if ":" not in host and not host.endswith(":*"):
            allowed.append(f"{host}:*")
    return TransportSecuritySettings(
        allowed_hosts=allowed,
        allowed_origins=[f"http://{host}" for host in hosts if host != "*"]
        + [f"https://{host}" for host in hosts if host != "*"],
    )


mcp_asgi = mcp.streamable_http_app(
    streamable_http_path="/",
    stateless_http=True,
    transport_security=_transport_security(),
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_engine(settings.database_url)
    await create_tables()
    client = httpx.AsyncClient(timeout=settings.upstream_timeout_seconds)
    set_http_client(client)
    logger.info("CoRE Stack MCP listening, upstream %s", settings.core_stack_base_url)
    async with mcp.session_manager.run():
        yield
    set_http_client(None)
    await client.aclose()
    await dispose_engine()


app = FastAPI(
    title="CoRE Stack MCP",
    version="0.1.0",
    description="MCP server for the CoRE Stack public API. Access is logged in Postgres.",
    lifespan=lifespan,
)
app.add_middleware(AccessLogMiddleware)
app.add_middleware(McpSlashMiddleware)


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    expected = settings.admin_token
    if not expected:
        raise HTTPException(status_code=404, detail="Access log is disabled")
    if x_admin_token != expected:
        raise HTTPException(status_code=401, detail="Invalid admin token")


@app.get("/health")
async def health():
    try:
        factory = session_factory()
        async with factory() as session:
            await session.execute(select(1))
    except Exception as exc:
        logger.warning("health check failed: %s", exc)
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok"}


@app.get("/access-logs", dependencies=[Depends(require_admin)])
async def access_logs(limit: int = Query(default=50, ge=1, le=500)):
    factory = session_factory()
    async with factory() as session:
        rows = (
            await session.scalars(select(AccessLog).order_by(desc(AccessLog.created_at)).limit(limit))
        ).all()
    return {"access_logs": [_log_dict(row) for row in rows]}


@app.get("/clients", dependencies=[Depends(require_admin)])
async def clients(limit: int = Query(default=50, ge=1, le=500)):
    factory = session_factory()
    async with factory() as session:
        rows = (
            await session.scalars(select(Client).order_by(desc(Client.last_seen_at)).limit(limit))
        ).all()
    return {
        "clients": [
            {
                "api_key_fingerprint": row.api_key_fingerprint,
                "api_key_hint": row.api_key_hint,
                "client_name": row.client_name,
                "first_seen_at": row.first_seen_at.isoformat(),
                "last_seen_at": row.last_seen_at.isoformat(),
                "request_count": row.request_count,
                "last_source_ip": row.last_source_ip,
                "last_user_agent": row.last_user_agent,
                "last_tool_name": row.last_tool_name,
                "last_path": row.last_path,
            }
            for row in rows
        ]
    }


def _log_dict(row: AccessLog) -> dict:
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat(),
        "request_id": row.request_id,
        "client_name": row.client_name,
        "api_key_fingerprint": row.api_key_fingerprint,
        "api_key_hint": row.api_key_hint,
        "source_ip": row.source_ip,
        "forwarded_for": row.forwarded_for,
        "user_agent": row.user_agent,
        "http_method": row.http_method,
        "path": row.path,
        "mcp_method": row.mcp_method,
        "mcp_request_id": row.mcp_request_id,
        "tool_name": row.tool_name,
        "tool_arguments": row.tool_arguments,
        "status_code": row.status_code,
        "outcome": row.outcome,
        "error_message": row.error_message,
        "duration_ms": row.duration_ms,
        "upstream_status": row.upstream_status,
        "response_bytes": row.response_bytes,
    }


app.mount("/mcp", mcp_asgi)
