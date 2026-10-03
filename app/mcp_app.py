"""MCP tools that call the CoRE Stack public API."""

from typing import Any

from mcp.server.mcpserver import MCPServer

from app.catalog import PUBLIC_APIS, public_api
from app.corestack import call_api

mcp = MCPServer(
    "CoRE Stack",
    instructions=(
        "CoRE Stack public data. Call list_public_apis, then describe_public_api "
        "for the route you need, then call_public_api. Tehsil datasets use the "
        "data parameter. Micro-watershed time series and Know Your Landscape "
        "indicators use fields. Place names must be the State, District, and "
        "Tehsil returned by get_active_locations or get_admin_details_by_latlon. "
        "The caller API key is the X-API-Key header on this MCP connection, not "
        "a tool argument."
    ),
)


@mcp.tool()
async def list_public_apis() -> list[dict[str, Any]]:
    """List CoRE Stack public API routes this server can call.

    Each item has id, path, description, and the query parameter names.
    Use the id with describe_public_api and call_public_api.
    """
    return [
        {
            "id": item.id,
            "path": item.path,
            "description": item.description,
            "parameters": list(item.parameters),
        }
        for item in PUBLIC_APIS
    ]


@mcp.tool()
async def describe_public_api(api_id: str) -> dict[str, Any]:
    """Describe one CoRE Stack route: path, what it returns, and query parameters.

    api_id is a value from list_public_apis, such as get_tehsil_data.
    """
    try:
        item = public_api(api_id)
    except KeyError as exc:
        return {"error": str(exc)}
    return {
        "id": item.id,
        "path": item.path,
        "description": item.description,
        "parameters": list(item.parameters),
    }


@mcp.tool()
async def call_public_api(api_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call one CoRE Stack public API and return its JSON body.

    api_id is a value from list_public_apis. params is a flat object of query
    parameters for that route, for example
    {"state": "Uttar Pradesh", "district": "Balrampur", "tehsil": "Tulsipur", "data": "drought"}.
    Do not put the API key in params. Send it as the X-API-Key header on the MCP connection.
    """
    try:
        return await call_api(api_id, params)
    except KeyError as exc:
        return {"error": str(exc)}
    except ValueError as exc:
        return {"error": str(exc)}
