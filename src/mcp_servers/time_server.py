import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("Time Server")

@mcp.tool(name="Get-Current-Time-Info")
def get_current_time_info(timezone: str = "Asia/Kolkata") -> dict:
    """
    Retrieves the current local time, date, and day of the week for a specified timezone.
    Use this tool to get the current day and time for a timezone.

    Args:
        timezone: Standard IANA timezone identifier (e.g., 'Asia/Kolkata', 'UTC', 'America/New_York').
        (Default: "Asia/Kolkata")
        
    Returns:
        A dictionary containing the formatted time, date, day, timezone, and UTC offset.
    """

    try:
        tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        logger.warning("Invalid timezone: %s", timezone)
        raise ValueError(f"Unknown timezone: {timezone}") from exc

    now = datetime.now(tz)

    logger.info("Current time retrieved successfully for timezone: %s", timezone)

    return {
        "time": now.strftime("%H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "day": now.strftime("%A"),
        "timezone": timezone,
        "utc_offset": now.strftime("%z"),
    }

if __name__ == "__main__":
    mcp.run(transport="stdio")