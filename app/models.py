"""Postgres tables for who called the MCP server."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class AccessLog(Base):
    __tablename__ = "access_logs"
    __table_args__ = (
        Index("ix_access_logs_created_at", "created_at"),
        Index("ix_access_logs_fingerprint", "api_key_fingerprint"),
        Index("ix_access_logs_tool_name", "tool_name"),
        Index("ix_access_logs_client_name", "client_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=False)
    request_id: Mapped[str] = mapped_column(String(64))
    client_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    api_key_fingerprint: Mapped[str | None] = mapped_column(String(80), nullable=True)
    api_key_hint: Mapped[str | None] = mapped_column(String(8), nullable=True)
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    forwarded_for: Mapped[str | None] = mapped_column(String(500), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    http_method: Mapped[str] = mapped_column(String(16))
    path: Mapped[str] = mapped_column(String(300))
    mcp_method: Mapped[str | None] = mapped_column(String(80), nullable=True)
    mcp_request_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tool_arguments: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer)
    upstream_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Client(Base):
    """One row per API key, or per anonymous IP when no key was sent."""

    __tablename__ = "clients"

    api_key_fingerprint: Mapped[str] = mapped_column(String(80), primary_key=True)
    api_key_hint: Mapped[str | None] = mapped_column(String(8), nullable=True)
    client_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    last_source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_tool_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
