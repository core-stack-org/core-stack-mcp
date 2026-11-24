import json
from pathlib import Path
import sys

import httpx
import pytest

from app.catalog import (
    _cli,
    extract_openapi_parameters,
    public_api,
    public_apis,
    refresh_catalog_from_openapi,
    reset_catalog,
    sync_catalog_from_openapi,
)
from app.corestack import _clean_params, call_api, set_http_client
from app.generated_docs import TEHSIL_DATA_ALIASES, TEHSIL_DATA_TYPE_DOCS
from app.identity import CallOutcome, RequestIdentity, identity_var, outcome_var
from app.mcp_app import describe_public_api, list_public_apis
from app.present import present_response


@pytest.fixture(autouse=True)
def _restore_catalog():
    yield
    reset_catalog()


def _set_test_identity(api_key: str = "test-key"):
    id_tok = identity_var.set(
        RequestIdentity(
            request_id="req-catalog",
            client_name="pytest",
            api_key=api_key,
            api_key_fingerprint="sha256:test",
            api_key_hint="test",
            source_ip="127.0.0.1",
            forwarded_for=None,
            user_agent="pytest",
            api_key_from_header=True,
        )
    )
    out_tok = outcome_var.set(CallOutcome())
    return id_tok, out_tok


def test_geometry_routes_allow_mws_id_and_village_id():
    mws = public_api("get_mws_geometries")
    assert "mws_id" in mws.parameters
    assert "mws_id" in mws.description

    village = public_api("get_village_geometries")
    assert "village_id" in village.parameters
    assert "village_id" in village.description

    assert _clean_params(
        mws,
        {"state": "TN", "district": "Tiruvannamalai", "tehsil": "Polur", "mws_id": "12_1001"},
    ) == {"state": "TN", "district": "Tiruvannamalai", "tehsil": "Polur", "mws_id": "12_1001"}

    assert _clean_params(
        village,
        {"state": "TN", "district": "Tiruvannamalai", "tehsil": "Polur", "village_id": "624443"},
    ) == {"state": "TN", "district": "Tiruvannamalai", "tehsil": "Polur", "village_id": "624443"}

    with pytest.raises(ValueError, match="Unexpected parameters"):
        _clean_params(mws, {"state": "TN", "village_id": "624443"})

    with pytest.raises(ValueError, match="Unexpected parameters"):
        _clean_params(village, {"state": "TN", "mws_id": "12_1001"})


@pytest.mark.asyncio
async def test_describe_public_api_has_rich_schema_for_every_route():
    listed = {item["id"]: item for item in await list_public_apis()}
    for api in public_apis():
        described = await describe_public_api(api.id)
        # Strictly richer than list_public_apis.
        assert described != listed[api.id]
        assert set(described.keys()) >= {"id", "path", "description", "parameters", "example_call", "response"}

        # Every parameter has name, type, required, description, matching api.parameters.
        param_names = [p["name"] for p in described["parameters"]]
        assert param_names == list(api.parameters)
        for p in described["parameters"]:
            assert isinstance(p["type"], str) and p["type"]
            assert isinstance(p["required"], bool)
            assert isinstance(p["description"], str) and p["description"]

        # Example call is valid against _clean_params.
        example = described["example_call"]
        assert example["api_id"] == api.id
        _clean_params(api, example.get("params"))

        # Response summary and fields with units/semantics are present.
        assert described["response"]["summary"]
        assert described["response"]["fields"]


@pytest.mark.asyncio
async def test_describe_tehsil_data_and_kyl_include_allowed_values_units_and_legends():
    tehsil = await describe_public_api("get_tehsil_data")
    params_by_name = {p["name"]: p for p in tehsil["parameters"]}
    assert params_by_name["state"]["required"] is True
    assert params_by_name["data"]["required"] is False
    allowed = params_by_name["data"]["allowed_values"]
    assert len(allowed) == len(TEHSIL_DATA_TYPE_DOCS)
    assert "croppingintensity_annual" in allowed
    assert "change_detection_tree_cover_decrease" in allowed
    assert params_by_name["data"]["aliases"] == TEHSIL_DATA_ALIASES
    assert tehsil["response"]["fields"]["delta_g"] == "mm"
    assert "cropping_intensity" in tehsil["response"]["code_legends"]

    kyl = await describe_public_api("get_mws_kyl_indicators")
    assert kyl["response"]["fields"]["avg_precipitation"] == "mm"
    assert kyl["response"]["fields"]["avg_single_cropped"] == "ha"
    assert kyl["response"]["fields"]["trend_g"] == "code"
    assert kyl["response"]["code_legends"]

    latlon = await describe_public_api("get_admin_details_by_latlon")
    latlon_params = {p["name"]: p for p in latlon["parameters"]}
    assert latlon_params["latitude"]["type"] == "number"
    assert latlon_params["latitude"]["required"] is True
    assert latlon_params["longitude"]["type"] == "number"


@pytest.mark.asyncio
async def test_call_api_forwards_mws_id_and_village_id_to_backend():
    seen_queries: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_queries.append(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"uid": "12_1001"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[80.0, 12.0], [80.1, 12.0], [80.1, 12.1], [80.0, 12.0]]],
                        },
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        set_http_client(mock_client)
        id_tok, out_tok = _set_test_identity()
        try:
            res_mws = await call_api(
                "get_mws_geometries",
                {"state": "TN", "district": "D", "tehsil": "T", "mws_id": "12_1001"},
                include_geometry=True,
            )
            res_village = await call_api(
                "get_village_geometries",
                {"state": "TN", "district": "D", "tehsil": "T", "village_id": "624443"},
                include_geometry=True,
            )
            assert res_mws["status_code"] == 200
            assert res_village["status_code"] == 200
            assert seen_queries[0]["mws_id"] == "12_1001"
            assert seen_queries[1]["village_id"] == "624443"
        finally:
            identity_var.reset(id_tok)
            outcome_var.reset(out_tok)
            set_http_client(None)


def test_geometry_truncation_hints_suggest_mws_id_and_village_id():
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"uid": f"id_{i}"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[80.0, 12.0 + j * 0.001] for j in range(30)]],
                },
            }
            for i in range(10)
        ],
    }

    mws_res = present_response(
        fc, 200, max_response_chars=1_200, api_id="get_mws_geometries", include_geometry=True
    )
    assert "mws_id=" in mws_res["hint"]

    village_res = present_response(
        fc, 200, max_response_chars=1_200, api_id="get_village_geometries", include_geometry=True
    )
    assert "village_id=" in village_res["hint"]


def test_extract_and_sync_from_openapi3_spec():
    spec = {
        "openapi": "3.0.3",
        "components": {
            "parameters": {
                "StateParam": {"name": "state", "in": "query", "required": True},
            }
        },
        "paths": {
            "/api/v2/get_village_geometries": {  # no trailing slash
                "parameters": [
                    {"$ref": "#/components/parameters/StateParam"},
                    {"name": "X-API-Key", "in": "header"},
                ],
                "get": {
                    "parameters": [
                        {"name": "district", "in": "query", "required": True},
                        {"name": "tehsil", "in": "query", "required": True},
                        {"name": "village_id", "in": "query", "required": False},
                        {
                            "name": "simplify",
                            "in": "query",
                            "required": False,
                            "description": "Simplify tolerance",
                            "schema": {"type": "number"},
                        },
                    ]
                },
            },
            "/api/v2/not_whitelisted/": {
                "get": {"parameters": [{"name": "foo", "in": "query"}]}
            },
        },
    }

    extracted = extract_openapi_parameters(spec)
    assert extracted == {
        "get_village_geometries": ("state", "district", "tehsil", "village_id", "simplify")
    }

    changed = sync_catalog_from_openapi(spec)
    assert changed == ["get_village_geometries"]
    assert public_api("get_village_geometries").parameters == (
        "state",
        "district",
        "tehsil",
        "village_id",
        "simplify",
    )
    specs = {p.name: p for p in public_api("get_village_geometries").parameter_specs}
    assert specs["simplify"].type == "number"
    assert specs["simplify"].description == "Simplify tolerance"
    # Non-whitelisted paths are never added; untouched routes keep static fallback params.
    with pytest.raises(KeyError):
        public_api("not_whitelisted")
    assert public_api("get_mws_geometries").parameters == (
        "state",
        "district",
        "tehsil",
        "mws_id",
    )


def test_extract_from_swagger2_with_base_path():
    spec = {
        "swagger": "2.0",
        "basePath": "/api/v2",
        "parameters": {
            "TehsilParam": {"name": "tehsil", "in": "query"},
        },
        "paths": {
            "/get_mws_geometries/": {
                "get": {
                    "parameters": [
                        {"name": "state", "in": "query"},
                        {"name": "district", "in": "query"},
                        {"$ref": "#/parameters/TehsilParam"},
                        {"name": "mws_id", "in": "query"},
                        {"name": "format", "in": "query"},
                    ]
                }
            }
        },
    }

    extracted = extract_openapi_parameters(spec)
    assert extracted == {
        "get_mws_geometries": ("state", "district", "tehsil", "mws_id", "format")
    }


@pytest.mark.asyncio
async def test_refresh_catalog_from_openapi_updates_mcp_tools_and_preserves_allowed_values():
    spec = {
        "openapi": "3.1.0",
        "paths": {
            "/api/v2/get_tehsil_data/": {
                "get": {
                    "parameters": [
                        {"name": "state", "in": "query", "required": True},
                        {"name": "district", "in": "query", "required": True},
                        {"name": "tehsil", "in": "query", "required": True},
                        {"name": "data", "in": "query", "required": False},
                        {"name": "mws_id", "in": "query", "required": False, "description": "Filter by MWS"},
                    ]
                }
            }
        },
    }

    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=spec))
    async with httpx.AsyncClient(transport=transport) as client:
        changed = await refresh_catalog_from_openapi(client, "https://example.test/openapi.json")
        assert changed == ["get_tehsil_data"]

    listed = {item["id"]: item for item in await list_public_apis()}
    assert "mws_id" in listed["get_tehsil_data"]["parameters"]

    described = await describe_public_api("get_tehsil_data")
    params_by_name = {p["name"]: p for p in described["parameters"]}
    assert "mws_id" in params_by_name
    assert params_by_name["mws_id"]["description"] == "Filter by MWS"
    # Curated dataset allowed_values are preserved when OpenAPI does not supply an enum.
    assert "drought" in params_by_name["data"]["allowed_values"]


def test_catalog_cli_reports_drift_and_ok(tmp_path):
    spec_file = tmp_path / "openapi.json"
    spec_file.write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "paths": {
                    "/api/v2/get_mws_geometries/": {
                        "get": {
                            "parameters": [
                                {"name": "state", "in": "query"},
                                {"name": "district", "in": "query"},
                                {"name": "tehsil", "in": "query"},
                                {"name": "mws_id", "in": "query"},
                            ]
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    assert _cli(["catalog", str(spec_file)]) == 0

    spec_file.write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "paths": {
                    "/api/v2/get_mws_geometries/": {
                        "get": {
                            "parameters": [
                                {"name": "state", "in": "query"},
                                {"name": "district", "in": "query"},
                                {"name": "tehsil", "in": "query"},
                                {"name": "mws_id", "in": "query"},
                                {"name": "new_filter", "in": "query"},
                            ]
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    assert _cli(["catalog", str(spec_file)]) == 1


def test_generate_catalog_docs_parses_backend_files(tmp_path):
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        import generate_catalog_docs
    finally:
        sys.path.remove(str(scripts_dir))

    sources = {
        "public_api/dataset_filters.py": (
            'TEHSIL_DATA_TYPE_DOCS = [("all", "Everything"), ("drought", "Drought weeks")]\n'
            'TEHSIL_DATA_ALIASES = {"old_name": "drought"}\n'
        ),
        "public_api/swagger_schemas.py": (
            "from drf_yasg import openapi\n"
            'latitude_param = openapi.Parameter("latitude", openapi.IN_QUERY, '
            'description="Lat", type=openapi.TYPE_NUMBER, required=True)\n'
            'auth_param = openapi.Parameter("X-API-Key", openapi.IN_HEADER, '
            'description="Key", type=openapi.TYPE_STRING, required=True)\n'
        ),
        "utilities/openmeteo_format.py": (
            'KYL_INDICATOR_UNIT_OVERRIDES = {"avg_precipitation": "mm"}\n'
            'GENERATED_LAYER_FIELD_UNITS = {"layer_url": "url"}\n'
            'ACTIVE_LOCATION_FIELD_HINTS = {"label": "display_name"}\n'
            'ADMIN_DETAIL_FIELD_HINTS = {"State": "state_name"}\n'
            'MWS_BY_LATLON_FIELD_HINTS = {"uid": "mws_identifier"}\n'
            'MWS_REPORT_FIELD_HINTS = {"Mws_report_url": "url"}\n'
            'METADATA_FIELD_UNITS = {"area": "ha"}\n'
            'ANNUAL_METRIC_UNIT_OVERRIDES = {"delta_g": "mm"}\n'
        ),
    }

    output = generate_catalog_docs.generate_module(sources, "test-backend")
    ns: dict = {}
    exec(compile(output, "<generated>", "exec"), ns)
    assert ns["TEHSIL_DATA_TYPE_DOCS"] == {"all": "Everything", "drought": "Drought weeks"}
    assert ns["TEHSIL_DATA_ALIASES"] == {"old_name": "drought"}
    assert "latitude_param" in ns["SWAGGER_QUERY_PARAMS"]
    assert ns["SWAGGER_QUERY_PARAMS"]["latitude_param"]["type"] == "number"
    assert "auth_param" not in ns["SWAGGER_QUERY_PARAMS"]
    assert ns["KYL_INDICATOR_UNITS"] == {"avg_precipitation": "mm"}
