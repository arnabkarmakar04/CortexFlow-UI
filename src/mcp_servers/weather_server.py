import os
import warnings
import logging
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
from fastmcp import FastMCP
load_dotenv()

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("Weather Server")

def get_ip_location() -> str:
    """Fetch the current city via IP geolocation fallback."""
    try:
        response = requests.get(
            "http://ip-api.com/json/",
            params={"fields": "status,message,city"},
            timeout=2.0
        )
        response.raise_for_status()

        data = response.json()

        if data.get("status") == "success" and data.get("city"):
            logger.info("Fallback triggered. Detected location: %s", data["city"])
            return data["city"]

        logger.warning("IP geolocation failed: %s", data.get("message", "Unknown error"))

    except requests.RequestException as e:
        logger.warning("IP geolocation fallback failed: %s", e)

    logger.info("Using default fallback location: Kolkata, India.")
    return "Kolkata"

def format_weather(weather: dict) -> dict:
    """Converts the OpenWeatherMap API response into a compact normalized dictionary."""
    try:
        offset = weather["timezone"]
        utc = timezone.utc

        weather_data = weather["weather"][0]
        main = weather["main"]
        wind = weather.get("wind", {})
        clouds = weather.get("clouds", {})
        system = weather["sys"]
        sunrise = datetime.fromtimestamp(system["sunrise"] + offset, utc).strftime("%H:%M:%S")
        sunset = datetime.fromtimestamp(system["sunset"] + offset, utc).strftime("%H:%M:%S")

        required_fields = {
            "name": weather.get("name"),
            "country": system.get("country"),
            "weather": weather_data,
            "main": main,
            "visibility": weather.get("visibility"),
            "wind": wind,
            "clouds": clouds,
            "timezone": weather.get("timezone"),
            "sunrise": system.get("sunrise"),
            "sunset": system.get("sunset"),
        }

        missing_fields = [field for field, value in required_fields.items() if value is None or value == {}]

        if missing_fields:
            raise ValueError("Weather response is missing required data: "+ ", ".join(missing_fields))

        return {
            "location": weather["name"],
            "country": system["country"],
            "main": weather_data["main"],
            "description": weather_data["description"],
            "temperature": main["temp"],
            "feels_like": main["feels_like"],
            "humidity": main["humidity"],
            "pressure": main["pressure"],
            "visibility": weather["visibility"],
            "wind": wind,
            "clouds": clouds,
            "sunrise": sunrise,
            "sunset": sunset,
        }

    except KeyError as e:
        raise ValueError(f"Weather response is missing expected field: {str(e)}") from e

@mcp.tool(name="Weather-Report")
def weather_report(location: str | None = None) -> dict:
    """
    Retrieves current weather conditions for a specified location.
    Use this tool to check current temperature, weather conditions, humidity, wind, visibility, pressure, or sunrise/sunset times.

    Args:
        location: Optional city, town, region, or location name such as 'Kolkata', 'London, UK', or 'New York'. (Default: None)

    Returns:
        A dictionary containing current weather information.
    """

    if not location or not location.strip():
        location = get_ip_location()

    api_key = os.getenv("WEATHER_API_KEY")

    if not api_key or not api_key.strip():
        logger.error("WEATHER_API_KEY is not configured.")
        raise RuntimeError("Weather data provider is not configured.")

    normalized_location = location.strip()
    api_key = api_key.strip()

    try:
        geo_response = requests.get(
            "https://api.openweathermap.org/geo/1.0/direct",
            params={
                "q": normalized_location,
                "limit": 1,
                "appid": api_key,
            },
            timeout=10,
        )

        geo_response.raise_for_status()
        geo = geo_response.json()

    except requests.exceptions.Timeout as exc:
        logger.exception("Weather geocoding request timed out.")
        raise RuntimeError("Weather data provider request timed out.") from exc

    except requests.exceptions.RequestException as exc:
        logger.exception("Weather geocoding request failed.")
        raise RuntimeError(f"Weather data provider request failed: {str(exc)}") from exc

    if not isinstance(geo, list) or not geo:
        logger.warning("No location found for: %s", normalized_location)
        raise ValueError(f"No location found for '{normalized_location}'.")

    latitude = geo[0].get("lat")
    longitude = geo[0].get("lon")

    if latitude is None or longitude is None:
        logger.warning("Geocoding response did not contain coordinates for: %s", normalized_location)
        raise ValueError(f"Could not resolve coordinates for '{normalized_location}'.")

    try:
        weather_response = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={
                "lat": latitude,
                "lon": longitude,
                "appid": api_key,
                "units": "metric",
            },
            timeout=10,
        )

        weather_response.raise_for_status()
        weather = weather_response.json()

    except requests.exceptions.Timeout as exc:
        logger.exception("Weather request timed out.")
        raise RuntimeError("Weather data provider request timed out.") from exc

    except requests.exceptions.RequestException as exc:
        logger.exception("Weather request failed.")
        raise RuntimeError(f"Weather data provider request failed: {str(exc)}") from exc

    if not isinstance(weather, dict):
        raise ValueError("Weather data provider returned an invalid response.")

    response_code = weather.get("cod")

    if response_code not in (None, 200, "200"):
        logger.warning("OpenWeather returned an application error for: %s", normalized_location)
        raise ValueError(weather.get("message", f"Weather data could not be retrieved for '{normalized_location}'."))

    formatted_weather = format_weather(weather)

    logger.info("Weather report retrieved successfully for %s.", normalized_location)

    return {
        "weather": formatted_weather,
    }

if __name__ == "__main__":
    mcp.run(transport="stdio")