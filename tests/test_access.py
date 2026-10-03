import json

import pytest
from fastapi.testclient import TestClient

from app.catalog import public_api
from app.corestack import call_api
from app.identity import CallOutcome, RequestIdentity, identity_var, outcome_var
from app.main import app
from app.redact import fingerprint_api_key, parse_mcp_message, redact


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
    result = await call_api("get_active_locations", {})
    assert result["status_code"] == 401
    identity_var.set(None)
    outcome_var.set(None)


def test_health_and_access_log():
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        denied = client.get("/clients", headers={"X-Client-Name": "pytest-user"})
        assert denied.status_code == 401

        listed = client.get(
            "/access-logs",
            headers={"X-Admin-Token": "test-admin", "X-Client-Name": "admin"},
        )
        assert listed.status_code == 200
        rows = listed.json()["access_logs"]
        assert any(row["path"] == "/clients" and row["client_name"] == "pytest-user" for row in rows)
        assert all(row["path"] != "/health" for row in rows)
        assert all("api_key" not in json.dumps(row) or row["api_key_fingerprint"] is None or row["api_key_fingerprint"].startswith(("sha256:", "anon:")) for row in rows)

        people = client.get("/clients", headers={"X-Admin-Token": "test-admin"})
        assert people.status_code == 200
        assert any(item["client_name"] == "pytest-user" for item in people.json()["clients"])
