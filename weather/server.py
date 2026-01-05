from typing import Any
import os
import logging

logger = logging.getLogger("weather.server")

# A small registry to export tool metadata (server-first source of truth)
_TOOL_SPECS: list[dict] = []
# Temporarily store functions (and args/kwargs for mcp.tool) until the MCP server
# is initialized. This avoids importing heavy dependencies at module import time.
_REGISTERED_FUNCS: list[tuple] = []

# The MCP instance is created lazily via `get_mcp()` / `register_tools_with_mcp()`.
mcp = None


def get_mcp():
    """Lazily initialize and return the FastMCP server instance."""
    global mcp
    if mcp is not None:
        return mcp
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("weather")
    return mcp


def register_tools_with_mcp():
    """Register all previously-decorated functions with the MCP instance."""
    m = get_mcp()
    for fn, args, kwargs in _REGISTERED_FUNCS:
        decorated = m.tool(*args, **kwargs)(fn)
        # Preserve attached tool metadata if any
        if hasattr(fn, "__tool_spec__"):
            setattr(decorated, "__tool_spec__", getattr(fn, "__tool_spec__"))
        # Replace the original function in this module with the decorated one
        globals()[fn.__name__] = decorated


def tool(*args, schema: dict | None = None, **kwargs):
    """Lightweight decorator that records tool metadata without initializing MCP.

    Use as `@tool(schema={...})`. The functions will be registered with the MPC
    instance when `register_tools_with_mcp()` is called (e.g., inside `run_server`).
    """
    def decorator(fn):
        spec = {
            "name": fn.__name__,
            "description": (fn.__doc__ or "").strip(),
            "input_schema": schema or {},
        }
        _TOOL_SPECS.append(spec)
        _REGISTERED_FUNCS.append((fn, args, kwargs))
        setattr(fn, "__tool_spec__", spec)
        return fn
    return decorator


def get_tool_specs() -> list[dict]:
    """Return a deep-copy of registered tool specs."""
    return [dict(s) for s in _TOOL_SPECS]


def export_tools_json(path: str = "tools.json") -> None:
    """Write the exported tool metadata to a JSON file."""
    import json
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(get_tool_specs(), fh, indent=2)


# Constants
NWS_API_BASE = "https://api.weather.gov"
USER_AGENT = "weather-app/1.0"

GOOGLE_API_KEY = os.environ.get("GOOGLE_GEOCODING_API_KEY")

async def make_nws_request(url: str) -> dict[str, Any] | None:
    """Make a request to the NWS API with proper error handling."""
    # Import httpx lazily to avoid import-time side effects when importing this module
    import httpx
    headers = {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            response = await client.get(url, headers=headers, timeout=30.0)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.exception(f"[NWS API Error] {e}")
            return None


def format_alert(feature: dict) -> str:
    """Format an alert feature into a readable string."""
    props = feature["properties"]
    return f"""
Event: {props.get("event", "Unknown")}
Area: {props.get("areaDesc", "Unknown")}
Severity: {props.get("severity", "Unknown")}
Description: {props.get("description", "No description available")}
Instructions: {props.get("instruction", "No specific instructions provided")}
"""


@tool(schema={
    "type": "object",
    "properties": {"state": {"type": "string", "description": "Two-letter US state code (e.g., CA, NY, TX)"}},
    "required": ["state"],
    "additionalProperties": False,
})
async def get_alerts(state: str) -> str:
    """Get weather alerts for a US state.

    Args:
        state: Two-letter US state code (e.g. CA, NY)
    """
    url = f"{NWS_API_BASE}/alerts/active/area/{state}"
    data = await make_nws_request(url)

    if not data or "features" not in data:
        return "Unable to fetch alerts or no alerts found."

    if not data["features"]:
        return "No active alerts for this state."

    alerts = [format_alert(feature) for feature in data["features"]]
    return "\n---\n".join(alerts)


@tool(schema={
    "type": "object",
    "properties": {
        "latitude": {"type": "number", "description": "Latitude of the location"},
        "longitude": {"type": "number", "description": "Longitude of the location"},
    },
    "required": ["latitude", "longitude"],
    "additionalProperties": False,
})
async def get_forecast(latitude: float, longitude: float) -> str:
    """Get weather forecast for a location.

    Args:
        latitude: Latitude of the location
        longitude: Longitude of the location
    """
    # First get the forecast grid endpoint
    points_url = f"{NWS_API_BASE}/points/{latitude},{longitude}"
    points_data = await make_nws_request(points_url)

    if not points_data:
        return "Unable to fetch forecast data for this location."

    # Get the forecast URL from the points response
    forecast_url = points_data["properties"]["forecast"]
    forecast_data = await make_nws_request(forecast_url)

    if not forecast_data:
        return "Unable to fetch detailed forecast."

    # Format the periods into a readable forecast
    periods = forecast_data["properties"]["periods"]
    forecasts = []
    for period in periods[:5]:  # Only show next 5 periods
        forecast = f"""
{period["name"]}:
Temperature: {period["temperature"]}°{period["temperatureUnit"]}
Wind: {period["windSpeed"]} {period["windDirection"]}
Forecast: {period["detailedForecast"]}
"""
        forecasts.append(forecast)

    return "\n---\n".join(forecasts)


@tool(schema={
    "type": "object",
    "properties": {"location": {"type": "string", "description": "Name of the city or location"}},
    "required": ["location"],
    "additionalProperties": False,
})
async def geocode(location: str) -> str:
    """Convert a location name to coordinates using Google Geocoding API.
    
    Args:
        location: City name, address, or location description (e.g. "San Francisco, CA" or "Lansing, Michigan")
    """
    if not GOOGLE_API_KEY:
        return "Google Geocoding API key is not set. Please set the GOOGLE_GEOCODING_API_KEY environment variable."

    try:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            "address": location,
            "key": GOOGLE_API_KEY,
            "region": "us"  # Bias towards US results
        }

        # Import httpx lazily to avoid import-time side effects
        import httpx
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(url, params=params, timeout=10.0)
            response.raise_for_status()
            data = response.json()

        status = data.get("status")
        if status == "OK" and data.get("results"):
            result = data["results"][0]
            coords = result["geometry"]["location"]
            lat = coords["lat"]
            lon = coords["lng"]
            address = result.get("formatted_address", location)

            logger.info(f"[Geocoding] Google API found: {address}")

            return f"""
Location: {address}
Latitude: {lat}
Longitude: {lon}
"""
        elif status == "ZERO_RESULTS":
            return f"Could not find coordinates for '{location}'. Please check the spelling or be more specific."
        else:
            return f"Google Geocoding API returned status: {status}. Unable to geocode '{location}'."

    except Exception as e:
        logger.exception(f"[Geocoding] Google API failed: {e}")
        return f"Geocoding error: {str(e)}"


def run_server(transport: str = "stdio") -> None:
    """Run the MCP server (convenience wrapper)."""
    # Ensure MCP instance is initialized and tools are registered prior to run.
    register_tools_with_mcp()
    m = get_mcp()
    m.run(transport=transport)


if __name__ == "__main__":
    # Ensure logs directory exists and configure file logging for the server process
    LOG_DIR = os.environ.get("LOG_DIR", "logs")
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        filename=os.path.join(LOG_DIR, "weather_server.log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    run_server()
