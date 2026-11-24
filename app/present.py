"""Prepare upstream CoRE Stack responses for MCP clients without breaking JSON."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Any

_GEOJSON_GEOMETRY_TYPES = frozenset(
    {
        "Point",
        "MultiPoint",
        "LineString",
        "MultiLineString",
        "Polygon",
        "MultiPolygon",
        "GeometryCollection",
    }
)

_WKT_PREFIX_RE = re.compile(
    r"^\s*(?:SRID=\d+\s*;\s*)?"
    r"(POINT|MULTIPOINT|LINESTRING|MULTILINESTRING|POLYGON|MULTIPOLYGON|GEOMETRYCOLLECTION)\b",
    re.IGNORECASE,
)
_WKT_PAIR_RE = re.compile(
    r"(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s+(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
)

_GEOMETRY_FIELD_NAMES = frozenset({"geometry", "geom"})
_MAX_OMITTED_KEYS_LISTED = 10


@dataclass
class _TruncationInfo:
    truncated: bool = False
    returned_count: int | None = None
    total_count: int | None = None
    truncated_field: str | None = None
    omitted_keys: list[str] | None = None
    omitted_key_count: int | None = None
    first_item_chars: int | None = None


def _json_len(value: Any) -> int:
    return len(json.dumps(value))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_geojson_geometry(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    geom_type = value.get("type")
    if geom_type not in _GEOJSON_GEOMETRY_TYPES:
        return False
    if geom_type == "GeometryCollection":
        return isinstance(value.get("geometries"), list)
    return "coordinates" in value


def _geometry_bbox(geom: dict[str, Any]) -> list[float] | None:
    """Compute [min_lon, min_lat, max_lon, max_lat] for a GeoJSON geometry."""
    existing = _valid_bbox(geom.get("bbox"))
    if existing is not None:
        return existing

    geom_stack: list[dict[str, Any]] = [geom]
    coord_stack: list[Any] = []
    found = False
    min_x = min_y = max_x = max_y = 0.0

    while geom_stack or coord_stack:
        if coord_stack:
            item = coord_stack.pop()
            if isinstance(item, (list, tuple)) and item:
                first = item[0]
                if _is_number(first):
                    if len(item) >= 2 and _is_number(item[1]):
                        x = float(first) if isinstance(first, float) else first
                        y = float(item[1]) if isinstance(item[1], float) else item[1]
                        if not found:
                            min_x = max_x = x
                            min_y = max_y = y
                            found = True
                        else:
                            if x < min_x:
                                min_x = x
                            elif x > max_x:
                                max_x = x
                            if y < min_y:
                                min_y = y
                            elif y > max_y:
                                max_y = y
                else:
                    coord_stack.extend(item)
            continue

        current = geom_stack.pop()
        if not isinstance(current, dict):
            continue
        if current.get("type") == "GeometryCollection":
            geometries = current.get("geometries")
            if isinstance(geometries, list):
                geom_stack.extend(g for g in geometries if isinstance(g, dict))
        else:
            coords = current.get("coordinates")
            if coords is not None:
                coord_stack.append(coords)

    if not found:
        return None
    return [min_x, min_y, max_x, max_y]


def _wkt_info(value: str) -> tuple[str, list[float] | None] | None:
    match = _WKT_PREFIX_RE.match(value)
    if not match:
        return None
    geom_type = match.group(1).upper()
    found = False
    min_x = min_y = max_x = max_y = 0.0
    for x_text, y_text in _WKT_PAIR_RE.findall(value[match.end() :]):
        x = float(x_text)
        y = float(y_text)
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        if not found:
            min_x = max_x = x
            min_y = max_y = y
            found = True
        else:
            if x < min_x:
                min_x = x
            elif x > max_x:
                max_x = x
            if y < min_y:
                min_y = y
            elif y > max_y:
                max_y = y
    return geom_type, ([min_x, min_y, max_x, max_y] if found else None)


def _valid_bbox(value: Any) -> list[float] | None:
    if isinstance(value, list) and len(value) in (4, 6) and all(_is_number(v) for v in value):
        if len(value) == 6:
            return [value[0], value[1], value[3], value[4]]
        return list(value)
    return None


def compact_geometries(payload: Any) -> tuple[Any, bool]:
    """Replace GeoJSON/WKT shapes with bounding boxes while preserving IDs and properties."""
    omitted = False

    def _walk(node: Any) -> Any:
        nonlocal omitted
        if isinstance(node, list):
            return [_walk(item) for item in node]
        if not isinstance(node, dict):
            return node

        # GeoJSON Feature: remove geometry coordinates, keep id/properties and add bbox.
        if node.get("type") == "Feature" and "geometry" in node:
            geom = node.get("geometry")
            if _is_geojson_geometry(geom):
                omitted = True
                bbox = _valid_bbox(node.get("bbox")) or _geometry_bbox(geom)
                compacted = {
                    k: _walk(v)
                    for k, v in node.items()
                    if k not in {"geometry", "bbox"}
                }
                compacted["geometry_type"] = geom["type"]
                if bbox is not None:
                    compacted["bbox"] = bbox
                return compacted
            if isinstance(geom, str):
                parsed = _wkt_info(geom)
                if parsed is not None:
                    omitted = True
                    geom_type, bbox = parsed
                    compacted = {
                        k: _walk(v)
                        for k, v in node.items()
                        if k not in {"geometry", "bbox"}
                    }
                    compacted["geometry_type"] = geom_type
                    if bbox is not None:
                        compacted["bbox"] = bbox
                    return compacted

        # Standalone GeoJSON geometry dict.
        if _is_geojson_geometry(node):
            omitted = True
            bbox = _geometry_bbox(node)
            result: dict[str, Any] = {"type": node["type"]}
            if bbox is not None:
                result["bbox"] = bbox
            return result

        # Generic dict (including FeatureCollection and tabular rows).
        new_dict: dict[str, Any] = {}
        for key, value in node.items():
            if key in _GEOMETRY_FIELD_NAMES and isinstance(value, str):
                parsed = _wkt_info(value)
                if parsed is not None:
                    omitted = True
                    geom_type, bbox = parsed
                    summary: dict[str, Any] = {"type": geom_type}
                    if bbox is not None:
                        summary["bbox"] = bbox
                    new_dict[key] = summary
                    continue
            new_dict[key] = _walk(value)
        return new_dict

    return _walk(payload), omitted


def _truncate_list(items: list[Any], budget: int) -> tuple[list[Any], _TruncationInfo]:
    kept: list[Any] = []
    used = 2  # "[]"
    first_item_chars: int | None = None
    for index, item in enumerate(items):
        item_len = _json_len(item)
        sep = 2 if kept else 0  # ", "
        if used + sep + item_len <= budget:
            kept.append(item)
            used += sep + item_len
        else:
            if index == 0:
                first_item_chars = item_len
            break
    return kept, _TruncationInfo(
        truncated=True,
        returned_count=len(kept),
        total_count=len(items),
        first_item_chars=first_item_chars,
    )


def _truncate_json_string(value: str, budget: int) -> str:
    """Shorten a string so json.dumps(result) fits in budget."""
    if budget <= 2:
        return ""
    if _json_len(value) <= budget:
        return value
    low, high = 0, len(value)
    while low < high:
        mid = (low + high + 1) // 2
        if _json_len(value[:mid]) <= budget:
            low = mid
        else:
            high = mid - 1
    return value[:low]


def _prefix_field(prefix: str, child: str | None) -> str:
    return f"{prefix}.{child}" if child else prefix


def _find_dominant_container_key(mapping: dict[str, Any], budget: int) -> str | None:
    """Identify a wrapper dict's main container key (e.g. 'features', 'data', 'results')."""
    if mapping.get("type") == "FeatureCollection" and isinstance(mapping.get("features"), list):
        return "features"

    if len(mapping) > 30:
        return None

    container_sizes: list[tuple[int, str]] = []
    for key, value in mapping.items():
        if isinstance(value, (list, dict)):
            container_sizes.append((_json_len(value), key))

    if not container_sizes:
        return None

    container_sizes.sort(reverse=True)
    largest_size, largest_key = container_sizes[0]
    second_size = container_sizes[1][0] if len(container_sizes) > 1 else 0

    # Treat as a wrapper dict only if one container clearly dominates and everything else
    # is small metadata leaving at least half the budget for the main container.
    if largest_size > budget // 2 and second_size <= budget // 4:
        return largest_key
    return None


def _truncate_dict(mapping: dict[str, Any], budget: int) -> tuple[dict[str, Any], _TruncationInfo]:
    if budget < 2:
        return {}, _TruncationInfo(truncated=True, returned_count=0, total_count=len(mapping))

    # 1. Wrapper dict with one dominant container key (FeatureCollection, {"data": [...]}, etc.).
    dominant_key = _find_dominant_container_key(mapping, budget)
    if dominant_key is not None:
        empty_placeholder: Any = [] if isinstance(mapping[dominant_key], list) else {}
        skeleton = {
            k: (empty_placeholder if k == dominant_key else v) for k, v in mapping.items()
        }
        skeleton_len = _json_len(skeleton)
        if skeleton_len <= budget and (
            dominant_key == "features" or skeleton_len <= budget // 2
        ):
            remaining = budget - (skeleton_len - 2)
            trimmed_value, sub = _truncate_json(mapping[dominant_key], remaining)
            skeleton[dominant_key] = trimmed_value
            sub.truncated_field = _prefix_field(str(dominant_key), sub.truncated_field)
            return skeleton, sub

    # 2. General mapping dict: pack as many complete key-value pairs as fit.
    items = list(mapping.items())
    kept: dict[str, Any] = {}
    used = 2  # "{}"
    for key, value in items:
        key_len = len(json.dumps(key))
        val_len = _json_len(value)
        entry_len = key_len + 2 + val_len  # '": '
        sep = 2 if kept else 0  # ", "
        if used + sep + entry_len <= budget:
            kept[key] = value
            used += sep + entry_len
        else:
            break

    if kept:
        omitted = [str(k) for k, _ in items[len(kept) :]]
        return kept, _TruncationInfo(
            truncated=True,
            returned_count=len(kept),
            total_count=len(items),
            omitted_keys=omitted[:_MAX_OMITTED_KEYS_LISTED],
            omitted_key_count=len(omitted),
        )

    # 3. Even the very first entry exceeded the entire budget: shrink its value structurally.
    first_key, first_value = items[0]
    key_overhead = 2 + len(json.dumps(first_key)) + 2  # '{' + '"k"' + ': ' + '}'
    remaining = budget - key_overhead
    omitted = [str(k) for k, _ in items[1:]]
    if isinstance(first_value, (list, dict)) and remaining >= 2:
        trimmed_first, sub = _truncate_json(first_value, remaining)
        sub.truncated_field = _prefix_field(str(first_key), sub.truncated_field)
        if omitted:
            sub.omitted_keys = omitted[:_MAX_OMITTED_KEYS_LISTED]
            sub.omitted_key_count = len(omitted)
        return {first_key: trimmed_first}, sub

    if isinstance(first_value, str) and remaining >= 2:
        trimmed_str = _truncate_json_string(first_value, remaining)
        return {first_key: trimmed_str}, _TruncationInfo(
            truncated=True,
            returned_count=1 if len(items) == 1 else 0,
            total_count=len(items),
            truncated_field=str(first_key),
            omitted_keys=omitted[:_MAX_OMITTED_KEYS_LISTED] if omitted else None,
            omitted_key_count=len(omitted) if omitted else None,
        )

    return {}, _TruncationInfo(
        truncated=True,
        returned_count=0,
        total_count=len(items),
        first_item_chars=_json_len(first_value),
        omitted_keys=[str(k) for k, _ in items[:_MAX_OMITTED_KEYS_LISTED]],
        omitted_key_count=len(items),
    )


def _truncate_json(value: Any, budget: int) -> tuple[Any, _TruncationInfo]:
    if _json_len(value) <= budget:
        return value, _TruncationInfo(truncated=False)
    if isinstance(value, list):
        return _truncate_list(value, budget)
    if isinstance(value, dict):
        return _truncate_dict(value, budget)
    if isinstance(value, str):
        return _truncate_json_string(value, budget), _TruncationInfo(truncated=True)
    return value, _TruncationInfo(truncated=True)


def _route_hint(api_id: str | None, *, include_geometry: bool) -> str:
    if api_id == "get_tehsil_data":
        return (
            "Call again with one or two dataset names in data= "
            "(e.g. data='croppingintensity_annual'), "
            "or query one micro-watershed with get_mws_data or get_mws_kyl_indicators using mws_id=."
        )
    if api_id == "get_active_locations":
        return "Narrow the response by passing state, district, or tehsil in params."
    if api_id == "get_mws_data":
        return "Narrow the response by passing specific fields= (e.g. fields='et,runoff')."
    if api_id == "get_mws_kyl_indicators":
        return "Narrow the response by passing specific fields=."
    if api_id == "get_waterbodies_data_by_admin":
        return "Call get_waterbody_data with a specific uid= to inspect one waterbody in full."
    if api_id == "get_mws_geometries":
        if include_geometry:
            return (
                "Full polygon shapes exceeded the size limit. Call again with mws_id= "
                "for one micro-watershed shape, or with include_geometry=false (default) "
                "to get all IDs and bounding boxes."
            )
        return (
            "Only a subset of micro-watersheds fit in one response; pass mws_id= "
            "for a single micro-watershed or use get_mwsid_by_latlon for a point."
        )
    if api_id == "get_village_geometries":
        if include_geometry:
            return (
                "Full polygon shapes exceeded the size limit. Call again with village_id= "
                "for one village shape, or with include_geometry=false (default) "
                "to get all IDs and bounding boxes."
            )
        return (
            "Only a subset of villages fit in one response; pass village_id= "
            "to fetch a specific village."
        )
    return (
        "Call again with narrower query parameters (such as mws_id=, village_id=, uid=, "
        "one dataset in data=, or specific fields= where supported by the route)."
    )


def _build_truncation_hint(
    info: _TruncationInfo,
    api_id: str | None,
    *,
    include_geometry: bool,
) -> str:
    parts: list[str] = []
    if info.returned_count is not None and info.total_count is not None:
        target = f" in '{info.truncated_field}'" if info.truncated_field else ""
        parts.append(f"Showing {info.returned_count} of {info.total_count} items{target}.")
        if info.returned_count == 0 and info.first_item_chars is not None:
            parts.append(f"The first item alone is {info.first_item_chars} characters.")
    if info.omitted_key_count:
        sample = ", ".join(info.omitted_keys or [])
        if info.omitted_key_count > len(info.omitted_keys or []):
            parts.append(f"Omitted {info.omitted_key_count} top-level keys (e.g. {sample}).")
        elif sample:
            parts.append(f"Omitted top-level keys: {sample}.")
    parts.append(_route_hint(api_id, include_geometry=include_geometry))
    return " ".join(parts)


_GEOMETRY_OMITTED_HINT = (
    "Geometry coordinates omitted; feature properties/IDs and bbox "
    "[min_lon, min_lat, max_lon, max_lat] returned instead. "
    "Call again with include_geometry=true to request full shapes."
)


def present_response(
    payload: Any,
    status_code: int,
    *,
    max_response_chars: int,
    api_id: str | None = None,
    include_geometry: bool = False,
) -> dict[str, Any]:
    """Format an upstream payload within max_response_chars without ever cutting inside JSON."""
    if isinstance(payload, str):
        total_chars = len(payload)
        if total_chars <= max_response_chars:
            return {"status_code": status_code, "body": payload}
        return {
            "status_code": status_code,
            "body": payload[:max_response_chars],
            "truncated": True,
            "returned_chars": max_response_chars,
            "total_chars": total_chars,
            "hint": (
                f"Plain-text response trimmed to {max_response_chars} of {total_chars} characters. "
                + _route_hint(api_id, include_geometry=include_geometry)
            ),
        }

    geometry_omitted = False
    if not include_geometry:
        payload, geometry_omitted = compact_geometries(payload)

    body, info = _truncate_json(payload, max_response_chars)
    result: dict[str, Any] = {"status_code": status_code, "body": body}

    hints: list[str] = []
    if geometry_omitted:
        result["geometry_omitted"] = True
        hints.append(_GEOMETRY_OMITTED_HINT)

    if info.truncated:
        result["truncated"] = True
        if info.returned_count is not None:
            result["returned_count"] = info.returned_count
        if info.total_count is not None:
            result["total_count"] = info.total_count
        if info.truncated_field is not None:
            result["truncated_field"] = info.truncated_field
        if info.omitted_keys:
            result["omitted_keys"] = info.omitted_keys
            if info.omitted_key_count is not None:
                result["omitted_key_count"] = info.omitted_key_count
        hints.append(
            _build_truncation_hint(info, api_id, include_geometry=include_geometry)
        )

    if hints:
        result["hint"] = " ".join(hints)

    return result
