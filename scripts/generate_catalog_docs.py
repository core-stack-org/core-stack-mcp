"""Generate app/generated_docs.py from CoRE Stack backend schema files.

Reads `public_api/dataset_filters.py`, `public_api/swagger_schemas.py`, and
`utilities/openmeteo_format.py` using Python's AST (no Django/drf_yasg needed).

Usage:
    python scripts/generate_catalog_docs.py --backend-dir /path/to/core-stack-backend
    python scripts/generate_catalog_docs.py --github-ref main
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
from pprint import pformat
import sys
from typing import Any

import httpx

_GITHUB_RAW_BASE = "https://raw.githubusercontent.com/core-stack-org/core-stack-backend"

_OPENAPI_TYPE_MAP = {
    "TYPE_STRING": "string",
    "TYPE_NUMBER": "number",
    "TYPE_INTEGER": "integer",
    "TYPE_BOOLEAN": "boolean",
    "TYPE_ARRAY": "array",
    "TYPE_OBJECT": "object",
}

_OPENAPI_IN_MAP = {
    "IN_QUERY": "query",
    "IN_HEADER": "header",
    "IN_PATH": "path",
    "IN_BODY": "body",
}


def _read_sources(backend_dir: Path | None, github_ref: str | None) -> dict[str, str]:
    rel_paths = (
        "public_api/dataset_filters.py",
        "public_api/swagger_schemas.py",
        "utilities/openmeteo_format.py",
    )
    sources: dict[str, str] = {}
    if backend_dir is not None:
        for rel in rel_paths:
            sources[rel] = (backend_dir / rel).read_text(encoding="utf-8")
        return sources

    assert github_ref is not None
    with httpx.Client(timeout=30) as client:
        for rel in rel_paths:
            url = f"{_GITHUB_RAW_BASE}/{github_ref}/{rel}"
            resp = client.get(url)
            resp.raise_for_status()
            sources[rel] = resp.text
    return sources


def _extract_assignments(source: str, names: set[str]) -> dict[str, Any]:
    tree = ast.parse(source)
    values: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in names:
                values[target.id] = ast.literal_eval(node.value)
    return values


def _const_or_attr(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ast.literal_eval(node)


def _extract_swagger_params(source: str) -> dict[str, dict[str, Any]]:
    tree = ast.parse(source)
    params: dict[str, dict[str, Any]] = {}
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        call = node.value
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "Parameter"
        ):
            continue
        if len(call.args) < 2:
            continue
        param_name = _const_or_attr(call.args[0])
        param_in_raw = _const_or_attr(call.args[1])
        param_in = _OPENAPI_IN_MAP.get(str(param_in_raw), str(param_in_raw))
        if param_in != "query":
            continue
        info: dict[str, Any] = {
            "name": param_name,
            "type": "string",
            "required": False,
            "description": "",
        }
        for kw in call.keywords:
            if kw.arg == "description":
                info["description"] = str(_const_or_attr(kw.value)).replace("``", "`")
            elif kw.arg == "type":
                raw_type = str(_const_or_attr(kw.value))
                info["type"] = _OPENAPI_TYPE_MAP.get(raw_type, raw_type)
            elif kw.arg == "required":
                info["required"] = bool(_const_or_attr(kw.value))
        params[target.id] = info
    return params


def generate_module(sources: dict[str, str], source_label: str) -> str:
    df_vals = _extract_assignments(
        sources["public_api/dataset_filters.py"],
        {"TEHSIL_DATA_TYPE_DOCS", "TEHSIL_DATA_ALIASES"},
    )
    om_vals = _extract_assignments(
        sources["utilities/openmeteo_format.py"],
        {
            "KYL_INDICATOR_UNIT_OVERRIDES",
            "GENERATED_LAYER_FIELD_UNITS",
            "ACTIVE_LOCATION_FIELD_HINTS",
            "ADMIN_DETAIL_FIELD_HINTS",
            "MWS_BY_LATLON_FIELD_HINTS",
            "MWS_REPORT_FIELD_HINTS",
            "METADATA_FIELD_UNITS",
            "ANNUAL_METRIC_UNIT_OVERRIDES",
        },
    )
    swagger_params = _extract_swagger_params(sources["public_api/swagger_schemas.py"])

    tehsil_docs = {
        key: desc.replace("``", "`") for key, desc in df_vals["TEHSIL_DATA_TYPE_DOCS"]
    }

    lines = [
        '"""Auto-generated CoRE Stack API metadata extracted from core-stack-backend.',
        "",
        f"Source: {source_label}",
        "Regenerate with: python scripts/generate_catalog_docs.py --backend-dir <path>",
        "             or: python scripts/generate_catalog_docs.py --github-ref main",
        '"""',
        "",
        "from typing import Any",
        "",
        f"TEHSIL_DATA_TYPE_DOCS: dict[str, str] = {pformat(tehsil_docs, sort_dicts=False, width=100)}",
        "",
        f"TEHSIL_DATA_ALIASES: dict[str, str] = {pformat(df_vals['TEHSIL_DATA_ALIASES'], sort_dicts=True, width=100)}",
        "",
        f"SWAGGER_QUERY_PARAMS: dict[str, dict[str, Any]] = {pformat(swagger_params, sort_dicts=False, width=100)}",
        "",
        f"KYL_INDICATOR_UNITS: dict[str, str] = {pformat(om_vals['KYL_INDICATOR_UNIT_OVERRIDES'], sort_dicts=False, width=100)}",
        "",
        f"GENERATED_LAYER_FIELD_UNITS: dict[str, str] = {pformat(om_vals['GENERATED_LAYER_FIELD_UNITS'], sort_dicts=False, width=100)}",
        "",
        f"ACTIVE_LOCATION_FIELD_HINTS: dict[str, str] = {pformat(om_vals['ACTIVE_LOCATION_FIELD_HINTS'], sort_dicts=False, width=100)}",
        "",
        f"ADMIN_DETAIL_FIELD_HINTS: dict[str, str] = {pformat(om_vals['ADMIN_DETAIL_FIELD_HINTS'], sort_dicts=False, width=100)}",
        "",
        f"MWS_BY_LATLON_FIELD_HINTS: dict[str, str] = {pformat(om_vals['MWS_BY_LATLON_FIELD_HINTS'], sort_dicts=False, width=100)}",
        "",
        f"MWS_REPORT_FIELD_HINTS: dict[str, str] = {pformat(om_vals['MWS_REPORT_FIELD_HINTS'], sort_dicts=False, width=100)}",
        "",
        f"METADATA_FIELD_UNITS: dict[str, str] = {pformat(om_vals['METADATA_FIELD_UNITS'], sort_dicts=False, width=100)}",
        "",
        f"ANNUAL_METRIC_UNITS: dict[str, str] = {pformat(om_vals['ANNUAL_METRIC_UNIT_OVERRIDES'], sort_dicts=False, width=100)}",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--backend-dir", type=Path, help="Path to local core-stack-backend checkout")
    group.add_argument("--github-ref", help="Git ref on core-stack-org/core-stack-backend (e.g. main)")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "app" / "generated_docs.py",
        help="Destination module path (default: app/generated_docs.py)",
    )
    args = parser.parse_args(argv)

    sources = _read_sources(args.backend_dir, args.github_ref)
    label = str(args.backend_dir) if args.backend_dir else f"core-stack-org/core-stack-backend@{args.github_ref}"
    content = generate_module(sources, label)
    args.output.write_text(content, encoding="utf-8")
    print(f"Wrote {args.output} ({len(content)} bytes) from {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
