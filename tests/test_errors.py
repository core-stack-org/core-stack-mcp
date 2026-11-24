import json

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from app import corestack
from app.corestack import CoreStackAPIError, call_api, set_http_client
from app.identity import CallOutcome, RequestIdentity, identity_var, outcome_var
from app.mcp_app import call_public_api, describe_public_api


def _set_test_identity(api_key: str | None = "test-key", **kwargs):
    id_tok = identity_var.set(
        RequestIdentity(
            request_id="req-err",
            client_name="pytest",
            api_key=api_key,
            api_key_fingerprint="sha256:test" if api_key else None,
            api_key_hint="test" if api_key else None,
            source_ip="127.0.0.1",
            forwarded_for=None,
            user_agent="pytest",
            api_key_from_header=bool(api_key),
            **kwargs,
        )
    )
    outcome = CallOutcome()
    out_tok = outcome_var.set(outcome)
    return id_tok, out_tok, outcome


def _parse_mcp_rpc_response(response) -> dict:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                payload = line[len("data:") :].strip()
                if payload:
                    return json.loads(payload)
        raise AssertionError(f"No JSON-RPC data event found in SSE response: {response.text!r}")
    return response.json()


def _mcp_call_tool(client, name: str, arguments: dict, headers: dict | None = None) -> dict:
    req_headers = {
        "Accept": "application/json, text/event-stream",
        "X-Client-Name": "pytest-err",
    }
    if headers:
        req_headers.update(headers)
    resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 42,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=req_headers,
    )
    assert resp.status_code == 200
    return _parse_mcp_rpc_response(resp)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "body", "expected_snippet"),
    [
        (400, {"error": "Invalid tehsil name"}, "Invalid tehsil name"),
        (404, {"detail": "Tehsil file not found"}, "Tehsil file not found"),
        (500, "Internal Server Error", "Internal Server Error"),
        (502, {"message": "Bad Gateway"}, "Bad Gateway"),
    ],
)
async def test_call_api_raises_on_http_4xx_and_5xx(status_code, body, expected_snippet):
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(body, dict):
            return httpx.Response(status_code, json=body)
        return httpx.Response(status_code, text=body)

    prev_client = corestack._client
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock_client:
        set_http_client(mock_client)
        id_tok, out_tok, outcome = _set_test_identity()
        try:
            with pytest.raises(CoreStackAPIError) as exc_info:
                await call_api("get_tehsil_data", {"state": "UP", "district": "D", "tehsil": "T"})
            assert exc_info.value.status_code == status_code
            assert str(status_code) in str(exc_info.value)
            assert expected_snippet in str(exc_info.value)
            assert outcome.outcome == "upstream_error"
            assert outcome.upstream_status == status_code
        finally:
            identity_var.reset(id_tok)
            outcome_var.reset(out_tok)
            set_http_client(prev_client)


@pytest.mark.asyncio
async def test_call_api_raises_on_http_200_with_status_error_body():
    body = {"status": "error", "message": "No micro-watershed at this coordinate"}

    prev_client = corestack._client
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body))
    ) as mock_client:
        set_http_client(mock_client)
        id_tok, out_tok, outcome = _set_test_identity()
        try:
            with pytest.raises(CoreStackAPIError) as exc_info:
                await call_api("get_mwsid_by_latlon", {"latitude": 0, "longitude": 0})
            assert exc_info.value.status_code == 200
            assert "200" in str(exc_info.value)
            assert "No micro-watershed at this coordinate" in str(exc_info.value)
            assert outcome.outcome == "upstream_error"
            assert outcome.upstream_status == 200
        finally:
            identity_var.reset(id_tok)
            outcome_var.reset(out_tok)
            set_http_client(prev_client)


@pytest.mark.asyncio
async def test_call_api_raises_502_on_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    prev_client = corestack._client
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock_client:
        set_http_client(mock_client)
        id_tok, out_tok, outcome = _set_test_identity()
        try:
            with pytest.raises(CoreStackAPIError) as exc_info:
                await call_api("get_active_locations", {})
            assert exc_info.value.status_code == 502
            assert "502" in str(exc_info.value)
            assert "connection refused" in str(exc_info.value)
            assert outcome.outcome == "upstream_error"
        finally:
            identity_var.reset(id_tok)
            outcome_var.reset(out_tok)
            set_http_client(prev_client)


@pytest.mark.asyncio
async def test_describe_and_call_tools_raise_tool_error_on_bad_inputs():
    id_tok, out_tok, outcome = _set_test_identity()
    try:
        with pytest.raises(ToolError, match="Unknown api_id"):
            await describe_public_api("no_such_route")
        assert outcome.outcome == "client_error"

        with pytest.raises(ToolError, match="Unknown api_id"):
            await call_public_api("no_such_route", {})

        with pytest.raises(ToolError, match="Unexpected parameters"):
            await call_public_api("get_active_locations", {"bogus": "1"})
    finally:
        identity_var.reset(id_tok)
        outcome_var.reset(out_tok)


def test_mcp_http_sets_is_error_true_with_status_and_message(http_client):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/get_tehsil_data/"):
            return httpx.Response(404, json={"detail": "Tehsil Tulsipur not found"})
        if request.url.path.endswith("/get_mwsid_by_latlon/"):
            return httpx.Response(200, json={"status": "error", "message": "Point outside India"})
        return httpx.Response(200, json={"status": "ok", "items": [1, 2]})

    prev_client = corestack._client
    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    set_http_client(mock_http)
    try:
        # 1. Upstream HTTP 404 -> isError: true with status code & API message.
        rpc_404 = _mcp_call_tool(
            http_client,
            "call_public_api",
            {
                "api_id": "get_tehsil_data",
                "params": {"state": "UP", "district": "Balrampur", "tehsil": "Tulsipur"},
            },
            headers={"X-API-Key": "test-key"},
        )
        result_404 = rpc_404["result"]
        assert result_404["isError"] is True
        text_404 = result_404["content"][0]["text"]
        assert "404" in text_404
        assert "Tehsil Tulsipur not found" in text_404

        # 2. Upstream HTTP 200 with status: "error" -> isError: true.
        rpc_body_err = _mcp_call_tool(
            http_client,
            "call_public_api",
            {
                "api_id": "get_mwsid_by_latlon",
                "params": {"latitude": 0, "longitude": 0},
            },
            headers={"X-API-Key": "test-key"},
        )
        result_body_err = rpc_body_err["result"]
        assert result_body_err["isError"] is True
        text_body_err = result_body_err["content"][0]["text"]
        assert "200" in text_body_err
        assert "Point outside India" in text_body_err

        # 3. Missing API key (401) -> isError: true.
        rpc_401 = _mcp_call_tool(
            http_client,
            "call_public_api",
            {"api_id": "get_active_locations"},
        )
        assert rpc_401["result"]["isError"] is True
        assert "401" in rpc_401["result"]["content"][0]["text"]

        # 4. describe_public_api unknown route -> isError: true.
        rpc_desc = _mcp_call_tool(
            http_client,
            "describe_public_api",
            {"api_id": "unknown_route"},
        )
        assert rpc_desc["result"]["isError"] is True
        assert "unknown_route" in rpc_desc["result"]["content"][0]["text"]

        # 5. Healthy upstream 200 -> isError is False / not True.
        rpc_ok = _mcp_call_tool(
            http_client,
            "call_public_api",
            {"api_id": "get_active_locations"},
            headers={"X-API-Key": "test-key"},
        )
        assert rpc_ok["result"].get("isError") is not True
    finally:
        set_http_client(prev_client)
