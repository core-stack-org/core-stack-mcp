"""CoRE Stack public v2 routes this MCP server is allowed to call."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from pathlib import Path
import sys
from typing import Any

import httpx

from app.generated_docs import (
    ACTIVE_LOCATION_FIELD_HINTS,
    ADMIN_DETAIL_FIELD_HINTS,
    ANNUAL_METRIC_UNITS,
    GENERATED_LAYER_FIELD_UNITS,
    KYL_INDICATOR_UNITS,
    METADATA_FIELD_UNITS,
    MWS_BY_LATLON_FIELD_HINTS,
    MWS_REPORT_FIELD_HINTS,
    SWAGGER_QUERY_PARAMS,
    TEHSIL_DATA_ALIASES,
    TEHSIL_DATA_TYPE_DOCS,
)


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    type: str = "string"
    required: bool = False
    description: str = ""
    example: Any = None
    allowed_values: dict[str, str] | None = None
    aliases: dict[str, str] | None = None
    lock_description: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "description": self.description,
        }
        if self.example is not None:
            out["example"] = self.example
        if self.allowed_values:
            out["allowed_values"] = dict(self.allowed_values)
        if self.aliases:
            out["aliases"] = dict(self.aliases)
        return out


@dataclass(frozen=True)
class PublicAPI:
    id: str
    path: str
    description: str
    parameters: tuple[str, ...]
    parameter_specs: tuple[ParameterSpec, ...] = ()
    example_call: dict[str, Any] = field(default_factory=dict)
    response_summary: str = ""
    response_fields: dict[str, str] = field(default_factory=dict)
    code_legends: dict[str, dict[str, str]] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def describe_dict(self) -> dict[str, Any]:
        specs = self.parameter_specs or tuple(ParameterSpec(name=p) for p in self.parameters)
        out: dict[str, Any] = {
            "id": self.id,
            "path": self.path,
            "description": self.description,
            "parameters": [spec.to_dict() for spec in specs],
            "example_call": self.example_call,
            "response": {
                "summary": self.response_summary,
                "fields": dict(self.response_fields),
            },
        }
        if self.code_legends:
            out["response"]["code_legends"] = {
                k: dict(v) for k, v in self.code_legends.items()
            }
        if self.notes:
            out["notes"] = list(self.notes)
        return out


def _swagger_param(
    key: str,
    *,
    example: Any = None,
    allowed_values: dict[str, str] | None = None,
    aliases: dict[str, str] | None = None,
    description_override: str | None = None,
    lock_description: bool = False,
) -> ParameterSpec:
    raw = SWAGGER_QUERY_PARAMS[key]
    return ParameterSpec(
        name=raw["name"],
        type=raw["type"],
        required=raw["required"],
        description=description_override if description_override is not None else raw["description"],
        example=example,
        allowed_values=allowed_values,
        aliases=aliases,
        lock_description=lock_description,
    )


_STATE_REQ = _swagger_param("state_param", example="Uttar Pradesh")
_DISTRICT_REQ = _swagger_param("district_param", example="Jaunpur")
_TEHSIL_REQ = _swagger_param("tehsil_param", example="Badlapur")
_LAT_REQ = _swagger_param("latitude_param", example=25.9)
_LON_REQ = _swagger_param("longitude_param", example=82.45)
_MWS_ID_REQ = _swagger_param("mws_id_param", example="12_234647")

_TEHSIL_DATA_ALLOWED_VALUES: dict[str, str] = {
    **TEHSIL_DATA_TYPE_DOCS,
    "all": (
        "Every dataset in the tehsil file — AVOID for agent calls: far exceeds "
        "180,000 characters and will be truncated. Pass one or two specific dataset names instead."
    ),
}

_WATERBODY_FIELDS: dict[str, str] = {
    "UID": "id (unique waterbody identifier; pass as uid to get_waterbody_data)",
    "MWS_UID": "id (micro-watershed identifier)",
    "waterbody_name": "name",
    "water": "flag (detected surface water)",
    "sum": METADATA_FIELD_UNITS.get("sum", "ha") + " (waterbody surface area)",
    "zoi": METADATA_FIELD_UNITS.get("zoi", "count") + " (zone of influence feature count)",
    "zoi_area": METADATA_FIELD_UNITS.get("zoi_area", "ha") + " (zone of influence area)",
    "cropping_intensity": (
        ANNUAL_METRIC_UNITS.get("cropping_intensity", "ratio")
        + " (inside zoi_properties; 1.0 = single-cropped, 2.0 = double-cropped, 3.0 = triple-cropped)"
    ),
    "total_cropable_area_ever_hydroyear": (
        ANNUAL_METRIC_UNITS.get("total_cropable_area_ever_hydroyear", "ha")
        + " (inside zoi_properties)"
    ),
}

_MWS_DATA_FIELD_VALUES: dict[str, str] = {
    "et": "Evapotranspiration (mm per ~15-day fortnight)",
    "runoff": "Surface runoff (mm per ~15-day fortnight)",
    "precipitation": "Precipitation / rainfall (mm per ~15-day fortnight)",
}

_TREND_AND_FLAG_LEGENDS: dict[str, dict[str, str]] = {
    "trend_codes (cropping_intensity_trend, trend_swb, trend_g)": {
        "-1": "decreasing trend",
        "0": "no significant trend / stable",
        "1": "increasing trend",
    },
    "binary_flags (lcw_conflict, mining, green_credit, factory_csr)": {
        "0 / false": "not present in MWS",
        "1 / true": "present / intersects MWS",
    },
}

DEFAULT_PUBLIC_APIS: tuple[PublicAPI, ...] = (
    PublicAPI(
        "get_active_locations",
        "/api/v2/get_active_locations/",
        "States, districts, and tehsils for which CoRE Stack has generated datasets. "
        "Optional filters: state, district, tehsil (alias: block).",
        ("state", "district", "tehsil", "block"),
        parameter_specs=(
            _swagger_param("active_locations_state_filter_param", example="Rajasthan"),
            _swagger_param("active_locations_district_filter_param", example="Bhilwara"),
            _swagger_param("active_locations_tehsil_filter_param", example="Mandalgarh"),
            _swagger_param("active_locations_block_filter_param", example="Mandalgarh"),
        ),
        example_call={
            "api_id": "get_active_locations",
            "params": {"state": "Rajasthan"},
        },
        response_summary=(
            "v2 envelope {status, error_message, data} with data.locations "
            "(state -> district -> blocks tree) and data.location_field_hints."
        ),
        response_fields=dict(ACTIVE_LOCATION_FIELD_HINTS),
        notes=(
            "Use the exact State, District, and Tehsil/block labels returned here on all other dataset routes.",
        ),
    ),
    PublicAPI(
        "get_admin_details_by_latlon",
        "/api/v2/get_admin_details_by_latlon/",
        "Resolve latitude and longitude to the State, District, and Tehsil names "
        "CoRE Stack uses on the other dataset APIs.",
        ("latitude", "longitude"),
        parameter_specs=(_LAT_REQ, _LON_REQ),
        example_call={
            "api_id": "get_admin_details_by_latlon",
            "params": {"latitude": 25.9, "longitude": 82.45},
        },
        response_summary=(
            "v2 envelope {status, error_message, data} with data.admin_details "
            "(State, District, Tehsil) and data.admin_field_hints."
        ),
        response_fields=dict(ADMIN_DETAIL_FIELD_HINTS),
        notes=("Confirm the returned tehsil exists in get_active_locations before querying datasets.",),
    ),
    PublicAPI(
        "get_mwsid_by_latlon",
        "/api/v2/get_mwsid_by_latlon/",
        "Resolve latitude and longitude to the micro-watershed uid, plus State, "
        "District, and Tehsil. Pass uid as mws_id to the time series, KYL, report, and geometry APIs.",
        ("latitude", "longitude"),
        parameter_specs=(_LAT_REQ, _LON_REQ),
        example_call={
            "api_id": "get_mwsid_by_latlon",
            "params": {"latitude": 25.9, "longitude": 82.45},
        },
        response_summary=(
            "v2 envelope {status, error_message, data} with data.mws_details "
            "(uid, State, District, Tehsil) and data.mws_field_hints."
        ),
        response_fields=dict(MWS_BY_LATLON_FIELD_HINTS),
    ),
    PublicAPI(
        "get_tehsil_data",
        "/api/v2/get_tehsil_data/",
        "Datasets generated by CoRE Stack for one tehsil, one row per micro-watershed. "
        "Always pass data= with one or two dataset names. data=all is too large for an agent. "
        "Example: data=croppingintensity_annual. "
        "Call describe_public_api('get_tehsil_data') for all valid dataset names.",
        ("state", "district", "tehsil", "data"),
        parameter_specs=(
            _STATE_REQ,
            _DISTRICT_REQ,
            _TEHSIL_REQ,
            _swagger_param(
                "tehsil_data_filter_param",
                example="croppingintensity_annual",
                allowed_values=_TEHSIL_DATA_ALLOWED_VALUES,
                aliases=TEHSIL_DATA_ALIASES,
                description_override=(
                    "Comma-separated dataset name(s) to return. Always pass one or two dataset "
                    "names from allowed_values (e.g. croppingintensity_annual or "
                    "croppingintensity_annual,stream_order). data=all (or omitting data) is too "
                    "large for an agent and will be truncated."
                ),
                lock_description=True,
            ),
        ),
        example_call={
            "api_id": "get_tehsil_data",
            "params": {
                "state": "Uttar Pradesh",
                "district": "Jaunpur",
                "tehsil": "Badlapur",
                "data": "croppingintensity_annual",
            },
        },
        response_summary=(
            "v2 envelope {status, error_message, data} with data.tehsil_data "
            "(keyed by dataset sheet name, one row per MWS) and data.tehsil_units "
            "(parallel unit map per sheet)."
        ),
        response_fields={**METADATA_FIELD_UNITS, **ANNUAL_METRIC_UNITS},
        code_legends={
            "cropping_intensity": {
                "1.0": "single-cropped",
                "2.0": "double-cropped",
                "3.0": "triple-cropped",
            },
            "delta_g (mm)": {
                "> 0": "net groundwater storage gain",
                "< 0": "net groundwater storage depletion",
            },
        },
        notes=(
            "Always pass data= with one or two dataset names (e.g. data=croppingintensity_annual). "
            "data=all (or omitting data) is too large for an agent.",
            "Always read data.tehsil_units in the response for exact per-column units.",
        ),
    ),
    PublicAPI(
        "get_mws_data",
        "/api/v2/get_mws_data/",
        "Fortnight evapotranspiration, runoff, and precipitation for one micro-watershed. "
        "Optional fields=et,runoff,precipitation.",
        ("state", "district", "tehsil", "mws_id", "fields"),
        parameter_specs=(
            _STATE_REQ,
            _DISTRICT_REQ,
            _TEHSIL_REQ,
            _MWS_ID_REQ,
            ParameterSpec(
                name="fields",
                type="string",
                required=False,
                description="Optional comma-separated time-series fields to keep.",
                example="et,runoff,precipitation",
                allowed_values=_MWS_DATA_FIELD_VALUES,
            ),
        ),
        example_call={
            "api_id": "get_mws_data",
            "params": {
                "state": "Uttar Pradesh",
                "district": "Jaunpur",
                "tehsil": "Badlapur",
                "mws_id": "12_234647",
                "fields": "et,runoff,precipitation",
            },
        },
        response_summary=(
            "v2 envelope {status, error_message, data} with data.metadata, "
            "data.fortnight (aligned ~15-day arrays), and data.fortnight_units."
        ),
        response_fields={
            "time": "iso8601 (15_days time step)",
            "et": "mm (evapotranspiration)",
            "runoff": "mm (surface runoff)",
            "precipitation": "mm (rainfall)",
        },
    ),
    PublicAPI(
        "get_mws_kyl_indicators",
        "/api/v2/get_mws_kyl_indicators/",
        "Know Your Landscape (KYL) single-row indicator snapshot for one micro-watershed. "
        "Optional fields= filters returned indicator columns.",
        ("state", "district", "tehsil", "mws_id", "fields"),
        parameter_specs=(
            _STATE_REQ,
            _DISTRICT_REQ,
            _TEHSIL_REQ,
            _MWS_ID_REQ,
            ParameterSpec(
                name="fields",
                type="string",
                required=False,
                description="Optional comma-separated KYL indicator names to keep (see response.fields).",
                example="avg_precipitation,avg_runoff,drought_category",
            ),
        ),
        example_call={
            "api_id": "get_mws_kyl_indicators",
            "params": {
                "state": "Uttar Pradesh",
                "district": "Jaunpur",
                "tehsil": "Badlapur",
                "mws_id": "12_234647",
            },
        },
        response_summary=(
            "v2 envelope {status, error_message, data} with data.indicators "
            "(flat single-row snapshot for mws_id) and data.indicator_units."
        ),
        response_fields=dict(KYL_INDICATOR_UNITS),
        code_legends=_TREND_AND_FLAG_LEGENDS,
        notes=("This is a static snapshot per MWS, not a time series.",),
    ),
    PublicAPI(
        "get_generated_layer_urls",
        "/api/v2/get_generated_layer_urls/",
        "GeoServer layer URLs (WFS/WCS) for one tehsil. Open layer_url in QGIS.",
        ("state", "district", "tehsil"),
        parameter_specs=(_STATE_REQ, _DISTRICT_REQ, _TEHSIL_REQ),
        example_call={
            "api_id": "get_generated_layer_urls",
            "params": {
                "state": "Uttar Pradesh",
                "district": "Jaunpur",
                "tehsil": "Badlapur",
            },
        },
        response_summary=(
            "v2 envelope {status, error_message, data} with data.layers "
            "(list of GeoServer layer records) and data.layer_field_units."
        ),
        response_fields=dict(GENERATED_LAYER_FIELD_UNITS),
    ),
    PublicAPI(
        "get_mws_report",
        "/api/v2/get_mws_report/",
        "Watershed PDF/HTML report URL for one micro-watershed.",
        ("state", "district", "tehsil", "mws_id"),
        parameter_specs=(_STATE_REQ, _DISTRICT_REQ, _TEHSIL_REQ, _MWS_ID_REQ),
        example_call={
            "api_id": "get_mws_report",
            "params": {
                "state": "Uttar Pradesh",
                "district": "Jaunpur",
                "tehsil": "Badlapur",
                "mws_id": "12_234647",
            },
        },
        response_summary=(
            "v2 envelope {status, error_message, data} with data.report.Mws_report_url "
            "and data.report_field_hints."
        ),
        response_fields=dict(MWS_REPORT_FIELD_HINTS),
    ),
    PublicAPI(
        "get_mws_geometries",
        "/api/v2/get_mws_geometries/",
        "Micro-watershed polygons for one tehsil, as a GeoJSON FeatureCollection. "
        "Pass optional mws_id to fetch a single micro-watershed polygon instead of "
        "the whole tehsil. Returns properties/IDs and bounding boxes by default; "
        "pass include_geometry=true on call_public_api to request full shapes.",
        ("state", "district", "tehsil", "mws_id"),
        parameter_specs=(
            _STATE_REQ,
            _DISTRICT_REQ,
            _TEHSIL_REQ,
            _swagger_param("mws_id_optional_param", example="12_234647"),
        ),
        example_call={
            "api_id": "get_mws_geometries",
            "params": {
                "state": "Uttar Pradesh",
                "district": "Jaunpur",
                "tehsil": "Badlapur",
                "mws_id": "12_234647",
            },
            "include_geometry": True,
        },
        response_summary=(
            "v2 envelope with GeoJSON FeatureCollection (or single mws_geometry when mws_id is set). "
            "By default the MCP server omits polygon coordinates and returns properties/IDs + bbox."
        ),
        response_fields={
            "uid": "id (micro-watershed identifier in feature.properties)",
            "bbox": "[min_lon, min_lat, max_lon, max_lat] in WGS84 degrees (returned when include_geometry=false)",
            "geometry_type": "GeoJSON geometry type (Polygon or MultiPolygon)",
            "geometry": "Full GeoJSON geometry (only returned when include_geometry=true)",
        },
        notes=(
            "Leave include_geometry=false to list all MWS IDs and bounding boxes in a tehsil without truncation.",
            "Pass mws_id with include_geometry=true to fetch one full polygon cleanly.",
        ),
    ),
    PublicAPI(
        "get_village_geometries",
        "/api/v2/get_village_geometries/",
        "Village polygons for one tehsil, as a GeoJSON FeatureCollection. "
        "Pass optional village_id to fetch a single village polygon instead of "
        "the whole tehsil. Returns properties/IDs and bounding boxes by default; "
        "pass include_geometry=true on call_public_api to request full shapes.",
        ("state", "district", "tehsil", "village_id"),
        parameter_specs=(
            _STATE_REQ,
            _DISTRICT_REQ,
            _TEHSIL_REQ,
            _swagger_param("village_id_param", example="258411"),
        ),
        example_call={
            "api_id": "get_village_geometries",
            "params": {
                "state": "Uttar Pradesh",
                "district": "Jaunpur",
                "tehsil": "Badlapur",
                "village_id": "258411",
            },
            "include_geometry": True,
        },
        response_summary=(
            "v2 envelope with GeoJSON FeatureCollection of village/panchayat boundaries. "
            "By default the MCP server omits polygon coordinates and returns properties/IDs + bbox."
        ),
        response_fields={
            "vill_ID": "id (village identifier in feature.properties; pass as village_id)",
            "vill_name": "name (village name in feature.properties)",
            "bbox": "[min_lon, min_lat, max_lon, max_lat] in WGS84 degrees (returned when include_geometry=false)",
            "geometry_type": "GeoJSON geometry type (Polygon or MultiPolygon)",
            "geometry": "Full GeoJSON geometry (only returned when include_geometry=true)",
        },
        notes=(
            "Leave include_geometry=false to list all village IDs, names, and bounding boxes in a tehsil.",
            "Pass village_id with include_geometry=true to fetch one full village polygon cleanly.",
        ),
    ),
    PublicAPI(
        "get_waterbodies_data_by_admin",
        "/api/v2/get_waterbodies_data_by_admin/",
        "Waterbodies in one tehsil, detected by the CoRE Stack remote sensing method, "
        "with seasonal water availability, zone of influence, and cropping intensity.",
        ("state", "district", "tehsil"),
        parameter_specs=(_STATE_REQ, _DISTRICT_REQ, _TEHSIL_REQ),
        example_call={
            "api_id": "get_waterbodies_data_by_admin",
            "params": {
                "state": "Uttar Pradesh",
                "district": "Jaunpur",
                "tehsil": "Badlapur",
            },
        },
        response_summary=(
            "Remotely sensed surface waterbody inventory for a tehsil with seasonal water "
            "availability, zone of influence (ZOI), and cropping intensity."
        ),
        response_fields=dict(_WATERBODY_FIELDS),
        notes=("Use UID from this response with get_waterbody_data to inspect one waterbody in full.",),
    ),
    PublicAPI(
        "get_waterbody_data",
        "/api/v2/get_waterbody_data/",
        "One waterbody for a tehsil and uid. Same fields as the waterbodies-by-admin API.",
        ("state", "district", "tehsil", "uid"),
        parameter_specs=(
            _STATE_REQ,
            _DISTRICT_REQ,
            _TEHSIL_REQ,
            ParameterSpec(
                name="uid",
                type="string",
                required=True,
                description="Unique waterbody identifier (UID from get_waterbodies_data_by_admin).",
                example="12_100174_104",
            ),
        ),
        example_call={
            "api_id": "get_waterbody_data",
            "params": {
                "state": "Uttar Pradesh",
                "district": "Jaunpur",
                "tehsil": "Badlapur",
                "uid": "12_100174_104",
            },
        },
        response_summary=(
            "Single remotely sensed waterbody record matching uid, with the same fields "
            "as get_waterbodies_data_by_admin."
        ),
        response_fields=dict(_WATERBODY_FIELDS),
    ),
)

PUBLIC_APIS: tuple[PublicAPI, ...] = DEFAULT_PUBLIC_APIS
API_BY_ID: dict[str, PublicAPI] = {item.id: item for item in PUBLIC_APIS}


def public_apis() -> tuple[PublicAPI, ...]:
    """Return the active whitelisted routes (including any OpenAPI-synced parameters)."""
    return PUBLIC_APIS


def public_api(api_id: str) -> PublicAPI:
    try:
        return API_BY_ID[api_id]
    except KeyError as exc:
        known = ", ".join(API_BY_ID)
        raise KeyError(f"Unknown api_id {api_id!r}. Known ids: {known}") from exc


def reset_catalog() -> None:
    """Restore the active catalog to the static fallback definitions."""
    global PUBLIC_APIS, API_BY_ID
    PUBLIC_APIS = DEFAULT_PUBLIC_APIS
    API_BY_ID = {item.id: item for item in PUBLIC_APIS}


def _normalize_path(path: str) -> str:
    stripped = path.strip()
    if not stripped.startswith("/"):
        stripped = "/" + stripped
    return stripped.rstrip("/") or "/"


def _resolve_ref(spec: dict[str, Any], item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    ref = item.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return item
    target: Any = spec
    for part in ref[2:].split("/"):
        if not isinstance(target, dict) or part not in target:
            return None
        target = target[part]
    return target if isinstance(target, dict) else None


def _query_param_specs_for_path_item(
    spec: dict[str, Any], path_item: Any
) -> tuple[ParameterSpec, ...]:
    if not isinstance(path_item, dict):
        return ()
    raw_params: list[Any] = []
    if isinstance(path_item.get("parameters"), list):
        raw_params.extend(path_item["parameters"])
    get_op = path_item.get("get")
    if isinstance(get_op, dict) and isinstance(get_op.get("parameters"), list):
        raw_params.extend(get_op["parameters"])

    seen: set[str] = set()
    specs: list[ParameterSpec] = []
    for entry in raw_params:
        resolved = _resolve_ref(spec, entry)
        if not resolved or resolved.get("in") != "query":
            continue
        name = resolved.get("name")
        if not isinstance(name, str) or not name or name in seen:
            continue
        seen.add(name)
        schema = resolved.get("schema") if isinstance(resolved.get("schema"), dict) else {}
        param_type = resolved.get("type") or schema.get("type") or "string"
        enum_vals = resolved.get("enum") or schema.get("enum")
        allowed = (
            {str(v): "" for v in enum_vals}
            if isinstance(enum_vals, list) and enum_vals
            else None
        )
        specs.append(
            ParameterSpec(
                name=name,
                type=str(param_type),
                required=bool(resolved.get("required", False)),
                description=str(resolved.get("description") or "").strip(),
                example=resolved.get("example") if "example" in resolved else schema.get("example"),
                allowed_values=allowed,
            )
        )
    return tuple(specs)


def _match_openapi_paths(spec: dict[str, Any]) -> dict[str, Any]:
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return {}
    base_path = spec.get("basePath")
    prefix = ""
    if isinstance(base_path, str) and base_path.strip() and base_path.strip() != "/":
        prefix = _normalize_path(base_path)

    by_norm_path: dict[str, Any] = {}
    for raw_path, path_item in paths.items():
        if not isinstance(raw_path, str):
            continue
        norm = _normalize_path(raw_path)
        by_norm_path[norm] = path_item
        if prefix and not norm.startswith(prefix + "/") and norm != prefix:
            by_norm_path[_normalize_path(prefix + norm)] = path_item
    return by_norm_path


def extract_openapi_parameter_specs(spec: dict[str, Any]) -> dict[str, tuple[ParameterSpec, ...]]:
    """Extract query ParameterSpec tuples per whitelisted api_id from an OpenAPI/Swagger spec."""
    by_norm_path = _match_openapi_paths(spec)
    extracted: dict[str, tuple[ParameterSpec, ...]] = {}
    for api in DEFAULT_PUBLIC_APIS:
        path_item = by_norm_path.get(_normalize_path(api.path))
        if path_item is None:
            continue
        specs = _query_param_specs_for_path_item(spec, path_item)
        if specs:
            extracted[api.id] = specs
    return extracted


def extract_openapi_parameters(spec: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    """Extract query parameter names per whitelisted api_id from an OpenAPI/Swagger spec."""
    return {
        api_id: tuple(p.name for p in specs)
        for api_id, specs in extract_openapi_parameter_specs(spec).items()
    }


def _merge_param_specs(
    existing: tuple[ParameterSpec, ...], incoming: tuple[ParameterSpec, ...]
) -> tuple[ParameterSpec, ...]:
    by_name = {p.name: p for p in existing}
    merged: list[ParameterSpec] = []
    for item in incoming:
        cur = by_name.get(item.name)
        if cur is None:
            merged.append(item)
        else:
            description = (
                cur.description if cur.lock_description else (item.description or cur.description)
            )
            example = (
                cur.example
                if cur.lock_description and cur.example is not None
                else (item.example if item.example is not None else cur.example)
            )
            merged.append(
                ParameterSpec(
                    name=item.name,
                    type=item.type or cur.type,
                    required=item.required,
                    description=description,
                    example=example,
                    allowed_values=cur.allowed_values or item.allowed_values,
                    aliases=cur.aliases or item.aliases,
                    lock_description=cur.lock_description,
                )
            )
    return tuple(merged)


def sync_catalog_from_openapi(spec: dict[str, Any]) -> list[str]:
    """Update whitelisted route parameter lists and metadata from an OpenAPI/Swagger document.

    Routes not in DEFAULT_PUBLIC_APIS are never added; routes absent from the spec keep
    their static fallback parameters. Returns the api_ids whose parameters changed.
    """
    global PUBLIC_APIS, API_BY_ID
    extracted = extract_openapi_parameter_specs(spec)
    updated: list[PublicAPI] = []
    changed: list[str] = []
    for api in DEFAULT_PUBLIC_APIS:
        spec_params = extracted.get(api.id)
        if spec_params:
            names = tuple(p.name for p in spec_params)
            merged_specs = _merge_param_specs(api.parameter_specs, spec_params)
            if names != api.parameters or merged_specs != api.parameter_specs:
                updated.append(replace(api, parameters=names, parameter_specs=merged_specs))
                changed.append(api.id)
            else:
                updated.append(api)
        else:
            updated.append(api)
    PUBLIC_APIS = tuple(updated)
    API_BY_ID = {item.id: item for item in PUBLIC_APIS}
    return changed


async def refresh_catalog_from_openapi(client: httpx.AsyncClient, url: str) -> list[str]:
    """Fetch an OpenAPI/Swagger JSON document and sync whitelisted parameter lists."""
    response = await client.get(url, headers={"Accept": "application/json"})
    response.raise_for_status()
    spec = response.json()
    if not isinstance(spec, dict):
        raise ValueError("OpenAPI document must be a JSON object")
    return sync_catalog_from_openapi(spec)


def _cli(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python -m app.catalog <openapi-url-or-file>", file=sys.stderr)
        return 2
    source = argv[1]
    if source.startswith(("http://", "https://")):
        response = httpx.get(source, timeout=30, headers={"Accept": "application/json"})
        response.raise_for_status()
        spec = response.json()
    else:
        spec = json.loads(Path(source).read_text(encoding="utf-8"))

    extracted = extract_openapi_parameters(spec)
    drift = 0
    for api in DEFAULT_PUBLIC_APIS:
        spec_params = extracted.get(api.id)
        if spec_params is None:
            print(f"MISSING  {api.id} ({api.path})")
        elif spec_params != api.parameters:
            drift += 1
            print(f"DRIFT    {api.id}: static={list(api.parameters)} openapi={list(spec_params)}")
        else:
            print(f"OK       {api.id}: {list(api.parameters)}")
    return 1 if drift else 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
