import json

import pytest

from app.catalog import public_api
from app.corestack import CoreStackAPIError, call_api, normalize_base_url
from app.identity import CallOutcome, RequestIdentity, identity_var, outcome_var
from app.redact import fingerprint_api_key, parse_mcp_message, redact


def test_base_url_strips_slash_and_rejects_a_bad_host():
    assert normalize_base_url("https://uat.core-stack.org:444/") == "https://uat.core-stack.org:444"
    with pytest.raises(ValueError):
        normalize_base_url("not a url")


def test_api_key_is_fingerprinted_and_not_reversible():
    fingerprint, hint = fingerprint_api_key("secret-key-1234")
    assert fingerprint.startswith("sha256:")
    assert "secret-key-1234" not in fingerprint
    assert hint == "1234"
    assert fingerprint_api_key(None) == (None, None)


def test_redact_strips_secrets_from_tool_arguments():
    cleaned = redact({"state": "Odisha", "api_key": "abcd", "nested": {"token": "xyz"}})
    assert cleaned["state"] == "Odisha"
    assert cleaned["api_key"] == "[redacted]"
    assert cleaned["nested"]["token"] == "[redacted]"


def test_parse_mcp_tool_call():
    raw = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "call_public_api",
                "arguments": {"api_id": "get_active_locations", "api_key": "hidden"},
            },
        }
    ).encode()
    parsed = parse_mcp_message(raw)
    assert parsed["mcp_method"] == "tools/call"
    assert parsed["tool_name"] == "call_public_api"
    assert parsed["tool_arguments"]["api_key"] == "[redacted]"
    assert parsed["mcp_request_id"] == "7"


def test_unknown_api_is_rejected():
    with pytest.raises(KeyError):
        public_api("not_a_route")


@pytest.mark.asyncio
async def test_call_requires_an_api_key():
    identity_var.set(
        RequestIdentity(
            request_id="r1",
            client_name="tester",
            api_key=None,
            api_key_fingerprint=None,
            api_key_hint=None,
            source_ip="127.0.0.1",
            forwarded_for=None,
            user_agent="pytest",
        )
    )
    outcome_var.set(CallOutcome())
    try:
        with pytest.raises(CoreStackAPIError) as exc_info:
            await call_api("get_active_locations", {})
        assert exc_info.value.status_code == 401
        assert "401" in str(exc_info.value)
        assert "X-API-Key" in str(exc_info.value)
    finally:
        identity_var.set(None)
        outcome_var.set(None)


def test_health_and_access_log(http_client):
    mcp_response = http_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        },
        headers={"Accept": "application/json, text/event-stream"},
        follow_redirects=False,
    )
    assert mcp_response.status_code != 307

    health = http_client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    denied = http_client.get("/clients", headers={"X-Client-Name": "pytest-user"})
    assert denied.status_code == 401

    listed = http_client.get(
        "/access-logs",
        headers={"X-Admin-Token": "test-admin", "X-Client-Name": "admin"},
    )
    assert listed.status_code == 200
    rows = listed.json()["access_logs"]
    assert any(row["path"] == "/clients" and row["client_name"] == "pytest-user" for row in rows)
    assert all(row["path"] != "/health" for row in rows)
    assert all("api_key" not in json.dumps(row) or row["api_key_fingerprint"] is None or row["api_key_fingerprint"].startswith(("sha256:", "anon:")) for row in rows)

    people = http_client.get("/clients", headers={"X-Admin-Token": "test-admin"})
    assert people.status_code == 200
    assert any(item["client_name"] == "pytest-user" for item in people.json()["clients"])
