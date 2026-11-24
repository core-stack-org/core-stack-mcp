"""MCP tools that call the CoRE Stack public API."""

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from app.catalog import public_api, public_apis
from app.corestack import call_api
from app.identity import current_outcome

mcp = MCPServer(
    "CoRE Stack",
    instructions=(
        "CoRE Stack public data. Use list_public_apis to discover available routes. "
        "Call describe_public_api(api_id) when you need a route's parameter types, "
        "required flags, allowed values (such as valid data= dataset names on "
        "get_tehsil_data), response field units, code legends, or an example call — "
        "do not guess dataset names, place spellings, or units. Once you know a "
        "route's parameters, call call_public_api directly. Place names must be the "
        "exact State, District, and Tehsil returned by get_active_locations or "
        "get_admin_details_by_latlon. On get_tehsil_data, always pass data= with one "
        "or two dataset names (for example data=croppingintensity_annual); data=all is "
        "too large for an agent. Geometry routes return feature properties/IDs "
        "and bounding boxes by default; pass mws_id on get_mws_geometries or "
        "village_id on get_village_geometries for one feature, and pass "
        "include_geometry=true only when full polygon coordinates are needed. If a "
        "response is marked truncated, inspect returned_count, total_count, and hint "
        "to narrow the query. The caller API key is the X-API-Key header on this MCP "
        "connection, not a tool argument."
    ),
)


@mcp.tool()
async def list_public_apis() -> list[dict[str, Any]]:
    """List CoRE Stack public API routes this server can call.

    Each item has id, path, description, and the query parameter names.
    Use describe_public_api(id) for parameter types, required flags, allowed
    values, examples, response units, and code legends.
    """
    return [
        {
            "id": item.id,
            "path": item.path,
            "description": item.description,
            "parameters": list(item.parameters),
        }
        for item in public_apis()
    ]


@mcp.tool()
async def describe_public_api(api_id: str) -> dict[str, Any]:
    """Describe one CoRE Stack route in detail before calling it.

    Returns parameter specs (name, type, required, description, allowed_values,
    aliases, example), a ready-to-use example_call, and response details
    (summary, fields with units, and code_legends). api_id is a value from
    list_public_apis, such as get_tehsil_data.
    """
    try:
        item = public_api(api_id)
    except KeyError as exc:
        outcome = current_outcome()
        outcome.outcome = "client_error"
        outcome.error_message = str(exc)
        raise ToolError(str(exc)) from exc
    return item.describe_dict()


@mcp.tool()
async def call_public_api(
    api_id: str,
    params: dict[str, Any] | None = None,
    include_geometry: bool = False,
) -> dict[str, Any]:
    """Call one CoRE Stack public API and return its JSON body.

    api_id is a value from list_public_apis. params is a flat object of query
    parameters for that route, for example
    {"state": "Uttar Pradesh", "district": "Jaunpur", "tehsil": "Badlapur", "data": "croppingintensity_annual"}.
    Geometry routes return feature properties/IDs and bounding boxes by default;
    set include_geometry=True to return full GeoJSON shapes. Oversized JSON
    responses are trimmed at complete item boundaries with returned_count,
    total_count, and a hint rather than cut mid-JSON.
    Do not put the API key in params. Send it as the X-API-Key header on the MCP connection.
    """
    try:
        return await call_api(api_id, params, include_geometry=include_geometry)
    except (KeyError, ValueError) as exc:
        outcome = current_outcome()
        outcome.outcome = "client_error"
        outcome.error_message = str(exc)
        raise ToolError(str(exc)) from exc
