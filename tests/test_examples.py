"""Tests that every documented example across catalog, tool docstrings, and README works."""

import json
import os
from pathlib import Path
import re

import httpx
import pytest

from app.catalog import DEFAULT_PUBLIC_APIS, reset_catalog, sync_catalog_from_openapi
from app.config import get_settings
from app.corestack import set_http_client
from app.identity import CallOutcome, RequestIdentity, identity_var, outcome_var
from app.mcp_app import call_public_api, describe_public_api, list_public_apis, mcp
from app.present import _route_hint

REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"


@pytest.fixture(autouse=True)
def _restore_catalog():
    yield
    reset_catalog()


def _set_test_identity(api_key: str = "test-key", base_url: str = "https://example.test"):
    id_tok = identity_var.set(
        RequestIdentity(
            request_id="req-examples",
            client_name="pytest",
            api_key=api_key,
            api_key_fingerprint="sha256:test",
            api_key_hint="test",
            source_ip="127.0.0.1",
            forwarded_for=None,
            user_agent="pytest",
            api_key_from_header=True,
            core_stack_base_url=base_url,
        )
    )
    out_tok = outcome_var.set(CallOutcome())
    return id_tok, out_tok


def _mock_payload_for(api_id: str, params: dict[str, str]) -> dict:
    if api_id == "get_tehsil_data":
        sheet = params.get("data", "croppingintensity_annual").split(",")[0]
        return {
            "status": "success",
            "error_message": None,
            "data": {
                "tehsil_data": {sheet: [{"uid": "12_234647", "cropping_intensity": 1.8}]},
                "tehsil_units": {sheet: {"cropping_intensity": "ratio"}},
            },
        }
    if api_id in {"get_mws_geometries", "get_village_geometries"}:
        return {
            "status": "success",
            "error_message": None,
            "data": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "uid": params.get("mws_id") or params.get("village_id") or "1"
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [[82.4, 25.8], [82.5, 25.8], [82.5, 25.9], [82.4, 25.8]]
                            ],
                        },
                    }
                ],
            },
        }
    return {"status": "success", "error_message": None, "data": {"ok": True}}


@pytest.mark.asyncio
async def test_get_tehsil_data_docs_warn_against_all_and_never_recommend_drought():
    listed = {item["id"]: item for item in await list_public_apis()}
    desc = listed["get_tehsil_data"]["description"]
    assert "data=croppingintensity_annual" in desc
    assert "data=all is too large" in desc
    assert "describe_public_api" in desc
    assert "Omit data, or pass data=all" not in desc
    assert "drought" not in desc.lower()

    described = await describe_public_api("get_tehsil_data")
    data_spec = {p["name"]: p for p in described["parameters"]}["data"]
    assert data_spec["example"] == "croppingintensity_annual"
    assert "croppingintensity_annual" in data_spec["description"]
    assert "drought" not in data_spec["description"].lower()
    assert "too large" in data_spec["description"].lower()
    assert "AVOID" in data_spec["allowed_values"]["all"]
    assert described["example_call"]["params"]["data"] == "croppingintensity_annual"

    assert "data=croppingintensity_annual" in (mcp.instructions or "")
    assert "data=all is too large" in (mcp.instructions or "")
    assert "drought" not in (call_public_api.__doc__ or "").lower()
    assert "croppingintensity_annual" in (call_public_api.__doc__ or "")

    hint = _route_hint("get_tehsil_data", include_geometry=False)
    assert "croppingintensity_annual" in hint
    assert "drought" not in hint.lower()

    readme = README_PATH.read_text(encoding="utf-8")
    assert "Omit `data`, or pass `data=all`" not in readme
    assert '"data": "drought"' not in readme
    assert '"data": "croppingintensity_annual"' in readme


@pytest.mark.asyncio
async def test_openapi_sync_does_not_reintroduce_drought_or_data_all_guidance():
    spec = {
        "openapi": "3.0.0",
        "paths": {
            "/api/v2/get_tehsil_data/": {
                "get": {
                    "parameters": [
                        {"name": "state", "in": "query", "required": True},
                        {"name": "district", "in": "query", "required": True},
                        {"name": "tehsil", "in": "query", "required": True},
                        {
                            "name": "data",
                            "in": "query",
                            "required": False,
                            "description": "Omit or pass all. Example: data=drought,stream_order",
                            "example": "drought",
                        },
                    ]
                }
            }
        },
    }
    sync_catalog_from_openapi(spec)
    described = await describe_public_api("get_tehsil_data")
    data_spec = {p["name"]: p for p in described["parameters"]}["data"]
    assert data_spec["example"] == "croppingintensity_annual"
    assert "drought" not in data_spec["description"].lower()
    assert "too large" in data_spec["description"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("api", DEFAULT_PUBLIC_APIS, ids=lambda a: a.id)
async def test_every_catalog_example_call_runs_and_matches_schema(api):
    example = api.example_call
    assert example["api_id"] == api.id
    params = example["params"]
    assert isinstance(params, dict)

    specs_by_name = {spec.name: spec for spec in api.parameter_specs}
    # All required parameters must be supplied, and no unexpected parameters.
    for spec in api.parameter_specs:
        if spec.required:
            assert spec.name in params, f"{api.id} example_call missing required param {spec.name!r}"
    for key, value in params.items():
        assert key in specs_by_name, f"{api.id} example_call has unknown param {key!r}"
        spec = specs_by_name[key]
        if spec.type == "number":
            assert isinstance(value, (int, float)) and not isinstance(value, bool)
        elif spec.type == "string":
            assert isinstance(value, str) and value.strip()
        if spec.allowed_values:
            parts = [part.strip() for part in str(value).split(",") if part.strip()]
            assert parts, f"{api.id}.{key} example value is empty"
            for part in parts:
                assert part in spec.allowed_values, f"{api.id}.{key}={part!r} not in allowed_values"

    if api.id == "get_tehsil_data":
        assert "data" in params
        data_parts = [p.strip() for p in str(params["data"]).split(",") if p.strip()]
        assert 1 <= len(data_parts) <= 2
        assert "all" not in data_parts
        assert "drought" not in data_parts

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_mock_payload_for(api.id, dict(request.url.params)))

    id_tok, out_tok = _set_test_identity()
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            set_http_client(client)
            result = await call_public_api(
                api_id=example["api_id"],
                params=params,
                include_geometry=example.get("include_geometry", False),
            )
    finally:
        set_http_client(None)
        identity_var.reset(id_tok)
        outcome_var.reset(out_tok)

    assert len(seen) == 1
    assert seen[0].url.path == api.path
    for key, value in params.items():
        assert seen[0].url.params[key] == str(value)
    assert result["status_code"] == 200
    assert result.get("truncated") is not True
    assert result["body"]["status"] == "success"


@pytest.mark.asyncio
async def test_readme_and_docstring_examples_run():
    readme = README_PATH.read_text(encoding="utf-8")
    json_blocks = re.findall(r"```json\s*(.*?)\s*```", readme, flags=re.DOTALL)
    readme_calls = []
    for block in json_blocks:
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "api_id" in parsed and "params" in parsed:
            readme_calls.append(parsed)

    assert readme_calls, "Expected at least one documented call_public_api JSON example in README.md"

    # Also extract the inline JSON example from call_public_api.__doc__.
    doc_match = re.search(r"(\{[^\n]+\})", call_public_api.__doc__ or "")
    assert doc_match is not None, "Expected inline params example in call_public_api docstring"
    doc_params = json.loads(doc_match.group(1))
    all_examples = [*readme_calls, {"api_id": "get_tehsil_data", "params": doc_params}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_mock_payload_for("get_tehsil_data", dict(request.url.params)),
        )

    id_tok, out_tok = _set_test_identity()
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            set_http_client(client)
            for example in all_examples:
                if example["api_id"] == "get_tehsil_data":
                    data_val = str(example["params"].get("data", ""))
                    assert data_val and data_val not in {"all", "drought"}
                result = await call_public_api(
                    api_id=example["api_id"],
                    params=example["params"],
                    include_geometry=example.get("include_geometry", False),
                )
                assert result["status_code"] == 200
                assert result.get("truncated") is not True
    finally:
        set_http_client(None)
        identity_var.reset(id_tok)
        outcome_var.reset(out_tok)


def _live_tests_enabled() -> bool:
    return os.getenv("CORE_STACK_LIVE_TEST") == "1" and bool(
        os.getenv("CORE_STACK_API_KEY") or get_settings().core_stack_api_key
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _live_tests_enabled(),
    reason=(
        "Set CORE_STACK_LIVE_TEST=1 and CORE_STACK_API_KEY to run documented examples "
        "against live CoRE Stack"
    ),
)
@pytest.mark.parametrize("api", DEFAULT_PUBLIC_APIS, ids=lambda a: a.id)
async def test_live_documented_examples(api):
    settings = get_settings()
    api_key = os.getenv("CORE_STACK_API_KEY") or settings.core_stack_api_key
    base_url = os.getenv("CORE_STACK_BASE_URL") or settings.core_stack_base_url
    id_tok, out_tok = _set_test_identity(api_key=api_key, base_url=base_url)
    try:
        async with httpx.AsyncClient(timeout=settings.upstream_timeout_seconds) as client:
            set_http_client(client)
            result = await call_public_api(
                api_id=api.example_call["api_id"],
                params=api.example_call["params"],
                include_geometry=api.example_call.get("include_geometry", False),
            )
    finally:
        set_http_client(None)
        identity_var.reset(id_tok)
        outcome_var.reset(out_tok)

    assert result["status_code"] == 200
    assert result.get("truncated") is not True
    body = result["body"]
    assert body, f"{api.id} returned empty body"
    if isinstance(body, dict) and "data" in body:
        assert body["data"], f"{api.id} returned empty data payload"
