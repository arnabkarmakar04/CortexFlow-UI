import logging
import requests
from fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("Location Server")

@mcp.tool(name="Get-Current-Location")
def get_current_location() -> dict:
    """
    Retrieves the user's approximate current location using the public IP address.
    Use this tool when the user's current city, region, postal code, coordinates is needed.

    Returns:
        A dictionary containing the detected location information.
    """

    try:
        response = requests.get(
            "http://ip-api.com/json/",
            params={
                "fields": "status,message,city,regionName,zip,lat,lon,timezone"
            },
            timeout=2.0
        )

        response.raise_for_status()
        data = response.json()

    except requests.exceptions.Timeout as exc:
        logger.exception("IP geolocation request timed out.")
        raise RuntimeError("Location provider request timed out.") from exc

    except requests.exceptions.RequestException as exc:
        logger.exception("IP geolocation request failed.")
        raise RuntimeError(f"Location provider request failed: {str(exc)}") from exc

    if not isinstance(data, dict):
        raise ValueError("Location provider returned an invalid response.")

    if data.get("status") != "success":
        raise ValueError(data.get("message", "Current location could not be determined."))

    logger.info("Current location retrieved successfully for %s, %s.", data.get("city"), data.get("regionName"))

    return {
        "location": {
            "city": data.get("city"),
            "latitude": data.get("lat"),
            "longitude": data.get("lon"),
            "region": data.get("regionName"),
            "zip": data.get("zip"),
            "timezone": data.get("timezone")
        }
    }

if __name__ == "__main__":
    mcp.run(transport="stdio")