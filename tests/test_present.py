import json

import httpx
import pytest

from app.config import get_settings
from app.corestack import call_api, set_http_client
from app.identity import CallOutcome, RequestIdentity, identity_var, outcome_var
from app.mcp_app import call_public_api, mcp
from app.present import compact_geometries, present_response


def _polygon_feature(idx: int, vertices: int = 20) -> dict:
    base_x = 80.0 + idx * 0.01
    base_y = 26.0 + idx * 0.01
    ring = [
        [round(base_x + (i % 5) * 0.001, 6), round(base_y + (i // 5) * 0.001, 6)]
        for i in range(vertices)
    ]
    ring.append(ring[0])
    return {
        "type": "Feature",
        "id": f"feat-{idx}",
        "properties": {"uid": f"12_{1000 + idx}", "village_code": str(624400 + idx)},
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


def _set_test_identity(api_key: str = "test-key"):
    id_tok = identity_var.set(
        RequestIdentity(
            request_id="req-present",
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


def test_small_json_response_is_unchanged():
    payload = [{"uid": "12_1", "drought": 0.2}, {"uid": "12_2", "drought": 0.5}]
    result = present_response(payload, 200, max_response_chars=10_000, api_id="get_tehsil_data")
    assert result == {"status_code": 200, "body": payload}


def test_oversized_list_keeps_complete_items_and_valid_json():
    rows = [{"uid": f"12_{i}", "name": "रामपुर", "value": "x" * 60} for i in range(50)]
    limit = 1_000
    result = present_response(rows, 200, max_response_chars=limit, api_id="get_tehsil_data")

    assert result["status_code"] == 200
    assert result["truncated"] is True
    assert isinstance(result["body"], list)
    assert 0 < result["returned_count"] < 50
    assert result["returned_count"] == len(result["body"])
    assert result["total_count"] == 50
    assert result["body"] == rows[: result["returned_count"]]
    assert len(json.dumps(result["body"])) <= limit
    # Next item would have pushed over the limit.
    assert len(json.dumps(rows[: result["returned_count"] + 1])) > limit
    assert "data=" in result["hint"]
    assert "mws_id=" in result["hint"]
    assert f"Showing {result['returned_count']} of 50 items" in result["hint"]


def test_oversized_list_where_first_item_exceeds_limit_returns_empty_list():
    rows = [{"uid": "huge", "blob": "a" * 500}, {"uid": "second", "blob": "b" * 10}]
    result = present_response(rows, 200, max_response_chars=200, api_id="get_tehsil_data")

    assert result["body"] == []
    assert result["truncated"] is True
    assert result["returned_count"] == 0
    assert result["total_count"] == 2
    assert "first item alone" in result["hint"].lower()


def test_oversized_wrapper_dict_truncates_inner_list():
    payload = {
        "status": "ok",
        "tehsil": "Tulsipur",
        "data": [{"uid": f"12_{i}", "score": i * 1.25} for i in range(100)],
    }
    limit = 600
    result = present_response(payload, 200, max_response_chars=limit, api_id="get_tehsil_data")

    assert result["truncated"] is True
    assert result["truncated_field"] == "data"
    assert result["body"]["status"] == "ok"
    assert result["body"]["tehsil"] == "Tulsipur"
    assert isinstance(result["body"]["data"], list)
    assert result["returned_count"] == len(result["body"]["data"])
    assert result["total_count"] == 100
    assert len(json.dumps(result["body"])) <= limit


def test_oversized_mapping_dict_keeps_complete_entries():
    # Mirrors waterbody lists keyed by MWS id.
    payload = {
        f"mws_{i}": [{"wb_uid": f"wb_{i}_{j}", "area": 12.5} for j in range(3)]
        for i in range(114)
    }
    limit = 2_000
    result = present_response(
        payload,
        200,
        max_response_chars=limit,
        api_id="get_waterbodies_data_by_admin",
    )

    assert result["truncated"] is True
    assert isinstance(result["body"], dict)
    assert 0 < result["returned_count"] < 114
    assert result["returned_count"] == len(result["body"])
    assert result["total_count"] == 114
    assert result["omitted_key_count"] == 114 - result["returned_count"]
    assert len(result["omitted_keys"]) == 10
    assert len(json.dumps(result["body"])) <= limit
    for key, val in result["body"].items():
        assert val == payload[key]
    assert "get_waterbody_data" in result["hint"]
    assert "uid=" in result["hint"]


def test_oversized_multi_dataset_dict_first_key_too_large_truncates_inside():
    payload = {
        "drought": [{"uid": f"12_{i}", "val": i} for i in range(80)],
        "lulc": [{"uid": f"12_{i}", "val": i} for i in range(80)],
    }
    limit = 500
    result = present_response(payload, 200, max_response_chars=limit, api_id="get_tehsil_data")

    assert result["truncated"] is True
    assert list(result["body"].keys()) == ["drought"]
    assert result["truncated_field"] == "drought"
    assert result["returned_count"] == len(result["body"]["drought"])
    assert result["total_count"] == 80
    assert result["omitted_keys"] == ["lulc"]
    assert result["omitted_key_count"] == 1
    assert len(json.dumps(result["body"])) <= limit


def test_geojson_compacts_to_ids_and_bboxes_by_default():
    features = [_polygon_feature(i, vertices=120) for i in range(114)]
    fc = {"type": "FeatureCollection", "features": features}
    # Raw GeoJSON is well over 180,000 chars.
    assert len(json.dumps(fc)) > 180_000

    result = present_response(
        fc,
        200,
        max_response_chars=180_000,
        api_id="get_mws_geometries",
        include_geometry=False,
    )

    assert result["status_code"] == 200
    assert result.get("truncated") is not True
    assert result["geometry_omitted"] is True
    assert "include_geometry=true" in result["hint"]

    out_features = result["body"]["features"]
    assert len(out_features) == 114
    first = out_features[0]
    assert first["id"] == "feat-0"
    assert first["properties"]["uid"] == "12_1000"
    assert first["properties"]["village_code"] == "624400"
    assert "geometry" not in first
    assert first["geometry_type"] == "Polygon"
    assert first["bbox"] == [80.0, 26.0, 80.004, 26.023]


def test_geojson_compacted_and_still_oversized_sets_both_flags():
    features = [_polygon_feature(i, vertices=20) for i in range(30)]
    fc = {"type": "FeatureCollection", "features": features}
    limit = 1_000

    result = present_response(
        fc,
        200,
        max_response_chars=limit,
        api_id="get_village_geometries",
        include_geometry=False,
    )

    assert result["geometry_omitted"] is True
    assert result["truncated"] is True
    assert result["truncated_field"] == "features"
    assert 0 < result["returned_count"] < 30
    assert result["total_count"] == 30
    assert len(json.dumps(result["body"])) <= limit
    assert "include_geometry=true" in result["hint"]
    assert f"Showing {result['returned_count']} of 30 items in 'features'" in result["hint"]


def test_geojson_handles_multipolygon_geometrycollection_and_null_geometry():
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"uid": "multi"},
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [
                        [[[10.0, 20.0], [11.0, 20.0], [11.0, 21.0], [10.0, 20.0]]],
                        [[[12.0, 22.0], [13.5, 22.0], [13.5, 24.5], [12.0, 22.0]]],
                    ],
                },
            },
            {
                "type": "Feature",
                "properties": {"uid": "gc"},
                "geometry": {
                    "type": "GeometryCollection",
                    "geometries": [
                        {"type": "Point", "coordinates": [75.0, 15.0]},
                        {"type": "LineString", "coordinates": [[76.0, 16.0], [77.5, 14.5]]},
                    ],
                },
            },
            {
                "type": "Feature",
                "properties": {"uid": "empty"},
                "geometry": None,
            },
        ],
    }

    compacted, omitted = compact_geometries(fc)
    assert omitted is True
    f0, f1, f2 = compacted["features"]
    assert f0["bbox"] == [10.0, 20.0, 13.5, 24.5]
    assert f0["geometry_type"] == "MultiPolygon"
    assert f1["bbox"] == [75.0, 14.5, 77.5, 16.0]
    assert f1["geometry_type"] == "GeometryCollection"
    assert f2["geometry"] is None


def test_geojson_with_include_geometry_true_preserves_shapes_and_truncates_cleanly():
    features = [_polygon_feature(i, vertices=40) for i in range(30)]
    fc = {"type": "FeatureCollection", "features": features}
    limit = 4_000

    result = present_response(
        fc,
        200,
        max_response_chars=limit,
        api_id="get_mws_geometries",
        include_geometry=True,
    )

    assert result["truncated"] is True
    assert result.get("geometry_omitted") is not True
    assert result["truncated_field"] == "features"
    assert 0 < result["returned_count"] < 30
    assert result["total_count"] == 30
    assert len(json.dumps(result["body"])) <= limit
    assert "geometry" in result["body"]["features"][0]
    assert "include_geometry=false" in result["hint"]


def test_wkt_geometry_field_is_compacted():
    rows = [
        {
            "uid": "wb_1",
            "geometry": "POLYGON ((82.1 27.4, 82.3 27.4, 82.3 27.6, 82.1 27.6, 82.1 27.4))",
        }
    ]
    result = present_response(rows, 200, max_response_chars=10_000, include_geometry=False)
    assert result["geometry_omitted"] is True
    assert result["body"][0]["geometry"] == {
        "type": "POLYGON",
        "bbox": [82.1, 27.4, 82.3, 27.6],
    }


def test_plain_text_response_truncates_with_char_counts():
    text = "error detail " * 100
    result = present_response(text, 500, max_response_chars=100, api_id="get_tehsil_data")
    assert result["status_code"] == 500
    assert result["body"] == text[:100]
    assert result["truncated"] is True
    assert result["returned_chars"] == 100
    assert result["total_chars"] == len(text)
    assert "100 of" in result["hint"]


@pytest.mark.asyncio
async def test_call_api_compacts_geometries_and_supports_include_geometry():
    fc = {"type": "FeatureCollection", "features": [_polygon_feature(i, vertices=30) for i in range(10)]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fc)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        set_http_client(mock_client)
        id_tok, out_tok = _set_test_identity()
        try:
            compact_res = await call_public_api(
                "get_mws_geometries",
                {"state": "UP", "district": "Balrampur", "tehsil": "Tulsipur"},
            )
            assert compact_res["status_code"] == 200
            assert compact_res["geometry_omitted"] is True
            assert len(compact_res["body"]["features"]) == 10
            assert "geometry" not in compact_res["body"]["features"][0]
            assert "bbox" in compact_res["body"]["features"][0]

            full_res = await call_public_api(
                "get_mws_geometries",
                {"state": "UP", "district": "Balrampur", "tehsil": "Tulsipur"},
                include_geometry=True,
            )
            assert full_res["status_code"] == 200
            assert full_res.get("geometry_omitted") is not True
            assert "geometry" in full_res["body"]["features"][0]
        finally:
            identity_var.reset(id_tok)
            outcome_var.reset(out_tok)
            set_http_client(None)


@pytest.mark.asyncio
async def test_call_api_parses_json_even_when_content_type_is_text_plain(monkeypatch):
    rows = [{"uid": f"12_{i}", "payload": "z" * 100} for i in range(40)]
    monkeypatch.setattr(get_settings(), "max_response_chars", 1_000)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(rows).encode("utf-8"),
            headers={"content-type": "text/plain"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        set_http_client(mock_client)
        id_tok, out_tok = _set_test_identity()
        try:
            res = await call_api(
                "get_tehsil_data",
                {"state": "UP", "district": "Balrampur", "tehsil": "Tulsipur"},
            )
            assert res["status_code"] == 200
            assert res["truncated"] is True
            assert isinstance(res["body"], list)
            assert res["total_count"] == 40
            assert res["returned_count"] == len(res["body"])
            assert len(json.dumps(res["body"])) <= 1_000
        finally:
            identity_var.reset(id_tok)
            outcome_var.reset(out_tok)
            set_http_client(None)


@pytest.mark.asyncio
async def test_mcp_tool_schema_exposes_include_geometry():
    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}
    schema = by_name["call_public_api"].input_schema
    assert "include_geometry" in schema["properties"]
    assert schema["properties"]["include_geometry"]["default"] is False
