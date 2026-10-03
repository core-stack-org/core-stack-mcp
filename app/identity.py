"""Who is calling this request. Tools and the access log read the same context."""

from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class RequestIdentity:
    request_id: str
    client_name: str | None
    api_key: str | None
    api_key_fingerprint: str | None
    api_key_hint: str | None
    source_ip: str | None
    forwarded_for: str | None
    user_agent: str | None


@dataclass
class CallOutcome:
    """Mutable bag shared with the tool task. Child tasks see the same object."""

    upstream_status: int | None = None
    response_bytes: int | None = None
    error_message: str | None = None
    outcome: str | None = None
    extra: dict = field(default_factory=dict)


identity_var: ContextVar[RequestIdentity | None] = ContextVar(
    "corestack_identity", default=None
)
outcome_var: ContextVar[CallOutcome | None] = ContextVar(
    "corestack_outcome", default=None
)


def current_identity() -> RequestIdentity | None:
    return identity_var.get()


def current_outcome() -> CallOutcome:
    outcome = outcome_var.get()
    if outcome is None:
        outcome = CallOutcome()
        outcome_var.set(outcome)
    return outcome
