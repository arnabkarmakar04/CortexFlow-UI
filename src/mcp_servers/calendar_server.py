import logging
import os
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastmcp import FastMCP
from google.auth.transport.requests import Request
from googleapiclient.errors import HttpError
from langchain_google_community.calendar.utils import get_google_credentials, build_calendar_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUTH_DIR = os.path.join(BASE_DIR, "auth")
TOKEN_PATH = os.path.join(AUTH_DIR, "token.json")
CREDS_PATH = os.path.join(AUTH_DIR, "credentials.json")
SCOPES = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_ID = "primary"
DEFAULT_TIMEZONE = "Asia/Kolkata"

mcp = FastMCP("Google Calendar Server")

def get_calendar_service():
    """Build and return an authenticated Google Calendar service client."""
    credentials = get_google_credentials(token_file=TOKEN_PATH, client_secrets_file=CREDS_PATH, scopes=SCOPES)
    if getattr(credentials, "expired", False) and getattr(credentials, "refresh_token", None):
        credentials.refresh(Request())
    return build_calendar_service(credentials)

def enforce_presence(**kwargs):
    """Validate that required arguments are present and non-empty."""
    for key, value in kwargs.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"'{key}' is required. Ask the user for this information.")

def validate_timezone(timezone: str) -> ZoneInfo:
    """Validate an IANA timezone name and return its ZoneInfo object."""
    enforce_presence(timezone=timezone)
    try:
        return ZoneInfo(timezone)
    except ZoneInfoNotFoundError as e:
        raise ValueError(f"Invalid timezone '{timezone}'. Use a valid IANA timezone.") from e

def to_google_datetime(dt_string: str, timezone: str = DEFAULT_TIMEZONE) -> str:
    """Convert a supported datetime string into a timezone-aware ISO 8601 datetime."""
    tz = validate_timezone(timezone)
    try:
        dt = datetime.fromisoformat(dt_string)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return dt.isoformat()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(dt_string, fmt)
            dt = dt.replace(tzinfo=tz)
            return dt.isoformat()
        except ValueError:
            continue
    raise ValueError(f"Unsupported datetime format: {dt_string}")

def to_calendar_date(date_string: str) -> date:
    """Convert a supported date or datetime string into a calendar date."""
    try:
        return datetime.fromisoformat(date_string).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(date_string, fmt ).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported calendar date format: {date_string}")

def validate_datetime_range(start_datetime: str, end_datetime: str):
    """Validate that the start datetime occurs strictly before the end datetime."""
    start = datetime.fromisoformat(start_datetime)
    end = datetime.fromisoformat(end_datetime)

    if start >= end:
        raise ValueError("'end_datetime' must be later than 'start_datetime'. Ask the user for a valid time range.")

def format_event(event: dict):
    """Convert a Google Calendar event into the compact MCP event representation."""
    start = event.get("start", {})
    end = event.get("end", {})
    original_start = event.get("originalStartTime", {})

    return {
        "summary": event.get("summary"),
        "description": event.get("description", ""),
        "event_id": event.get("id"),
        "start": start.get("dateTime", start.get("date")),
        "end": end.get("dateTime", end.get("date")),
        "location": event.get("location", ""),
        "recurring_event_id": event.get("recurringEventId"),
        "original_start_time": original_start.get("dateTime", original_start.get("date")),
    }

def find_event_candidates(service, event_name: str) -> list[dict]:
    """Search the primary calendar for exact event-title candidates starting from the beginning of the current year."""
    timezone = ZoneInfo(DEFAULT_TIMEZONE)
    current_year = datetime.now(timezone).year
    current_year_start = datetime(current_year, 1, 1, tzinfo=timezone).isoformat()

    response = service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=current_year_start,
        q=event_name.strip(),
        maxResults=15,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = response.get("items", [])
    normalized_name = event_name.strip().casefold()
    exact_matches= [event for event in events if event.get("summary", "").strip().casefold() == normalized_name]
    return exact_matches[:3]

@mcp.tool(name="Get-Calendar-Events")
def get_events( query: str = "", start_datetime: str | None = None, max_datetime: str | None = None ) -> dict:
    """
    Retrieve events from the user's primary Google Calendar.
    Use this tool to list calendar events within a specified date or datetime range, optionally filtered by a free-text search query.

    Args:
        query: Optional free-text search query for calendar events. (Default: "")
        start_datetime: Optional lower datetime bound in ISO 8601 format. (Default: None)
        max_datetime: Optional upper datetime bound in ISO 8601 format. (Default: None)

    Returns:
        A dictionary containing the matching calendar events.
    """

    MAX_RESULTS = 15

    if query.strip() and start_datetime is None and max_datetime is None:
        raise ValueError("A query-only calendar search must use Find-Calendar-Event.")

    try:
        if start_datetime is not None:
            start_datetime = to_google_datetime(start_datetime, DEFAULT_TIMEZONE)

        if max_datetime is not None:
            max_datetime = to_google_datetime(max_datetime, DEFAULT_TIMEZONE)

        if start_datetime is None and max_datetime is None:
            start_datetime = datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).isoformat()

            max_datetime = (datetime.fromisoformat(start_datetime) + timedelta(days=30)).isoformat()

        elif start_datetime is not None and max_datetime is None:
            start_dt = datetime.fromisoformat(start_datetime)
            max_datetime = (start_dt + timedelta(days=30)).isoformat()

        elif start_datetime is None and max_datetime is not None:
            timezone = ZoneInfo(DEFAULT_TIMEZONE)
            current_year = datetime.now(timezone).year

            start_datetime = datetime(current_year, 1, 1, tzinfo=timezone).isoformat()

        validate_datetime_range(start_datetime, max_datetime)

    except ValueError as e:
        logger.warning("Calendar event validation failed: %s", e)
        raise

    try:
        service = get_calendar_service()

        response = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=start_datetime,
            timeMax=max_datetime,
            q=query.strip() or None,
            maxResults=MAX_RESULTS,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = response.get("items", [])

        logger.info("Calendar events retrieved successfully. Returned %s event(s).", len(events))

        return {
            "events": [format_event(event) for event in events]
        }

    except HttpError as e:
        logger.exception("Google Calendar API request failed.")
        raise RuntimeError(f"Google Calendar API Error: {str(e)}") from e

    except Exception as e:
        logger.exception("Unexpected calendar error.")
        raise RuntimeError(f"Calendar event listing failed: {str(e)}") from e

@mcp.tool(name="Find-Calendar-Event")
def find_calendar_event(query: str):
    """
    Searches the user's primary Google Calendar to identify calendar events matching a specific event title.
    Use this tool when the user wants to find or identify an event by its title and retrieve matching event candidates.

    Args:
        query: The exact event title to search for.

    Returns:
        A dictionary containing up to three matching calendar events.
    """

    enforce_presence(query=query)

    try:
        service = get_calendar_service()
        events = find_event_candidates(service, query)

        logger.info("Calendar event search completed successfully. Returned %s candidate event(s).", len(events))

        return {
            "events": [format_event(event) for event in events]
        }

    except HttpError as e:
        logger.exception("Google Calendar API request failed.")
        raise RuntimeError(f"Google Calendar API Error: {str(e)}") from e

    except Exception as e:
        logger.exception("Unexpected calendar search error.")
        raise RuntimeError(f"Calendar event search failed: {str(e)}") from e

@mcp.tool(name="Create-Calendar-Event")
def create_event( summary: str, start_datetime: str | None = None, end_datetime: str | None = None, description: str = "", timezone: str = DEFAULT_TIMEZONE, all_day: bool = False):
    """
    Create one new calendar event in the user's primary Google Calendar.
    Use this tool to create a timed or all-day calendar event with the specified title, schedule, description, and timezone.

    Args:
        summary: Title of the event.
        start_datetime: Optional event start date or datetime in ISO 8601 format. (Default: None)
        end_datetime: Optional event end date or datetime in ISO 8601 format. (Default: None)
        description: Optional description of the event. (Default: "")
        timezone: IANA timezone for timezone-naive timed events. (Default: "Asia/Kolkata")
        all_day: Whether the event is an all-day event. (Default: False)

    Returns:
        A dictionary containing the created calendar event.
    """
    enforce_presence(summary=summary)

    try:
        if all_day:
            if start_datetime is None:
                start_date = datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).date()
            else:
                start_date = to_calendar_date(start_datetime)

            if end_datetime is None:
                end_date = start_date + timedelta(days=1)
            else:
                end_date = to_calendar_date(end_datetime)

            if start_date >= end_date:
                raise ValueError("'end_datetime' must be later than 'start_datetime'. Ask the user for a valid time range.")
        else:
            validate_timezone(timezone)

            if start_datetime is None:
                start_iso = datetime.now(ZoneInfo(timezone)).isoformat()
            else:
                start_iso = to_google_datetime(start_datetime, timezone)

            start_dt = datetime.fromisoformat(start_iso)

            if end_datetime is None:
                end_iso = (start_dt + timedelta(hours=1)).isoformat()
            else:
                end_iso = to_google_datetime(end_datetime, timezone)

            validate_datetime_range(start_iso, end_iso)

    except ValueError as e:
        logger.warning("Calendar event validation failed: %s", e)
        raise

    try:
        service = get_calendar_service()

        if all_day:
            body = {
                "summary": summary.strip(),
                "description": description.strip() if description else "",
                "start": {"date": start_date.isoformat()},
                "end": {"date": end_date.isoformat()}
            }

        else:
            body = {
                "summary": summary.strip(),
                "description": description.strip() if description else "",
                "start": {"dateTime": start_iso, "timeZone": timezone},
                "end": {"dateTime": end_iso, "timeZone": timezone}
            }

        event = service.events().insert(
            calendarId=CALENDAR_ID,
            body=body
        ).execute()

        logger.info(
            "%s calendar event created successfully.",
            "All-day" if all_day else "Timed"
        )

        return {
            "event": format_event(event)
        }

    except HttpError as e:
        logger.exception("Google Calendar create request failed.")
        raise RuntimeError(f"Google Calendar API Error: {str(e)}") from e

    except Exception as e:
        logger.exception("Unexpected calendar create error.")
        raise RuntimeError(f"Calendar event creation failed: {str(e)}") from e
    
if __name__ == "__main__":
    mcp.run(transport="stdio")