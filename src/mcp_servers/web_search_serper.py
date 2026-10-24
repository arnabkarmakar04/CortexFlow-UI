import os
import warnings
import logging
from dotenv import load_dotenv
from fastmcp import FastMCP
from langchain_community.utilities import GoogleSerperAPIWrapper
load_dotenv()

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("Web Search Server")

def enforce_presence(**kwargs):
    """Validates that all required tool arguments are present and non-empty."""
    for key, value in kwargs.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"'{key}' is required. Ask the user for this information.")

def format_result(result: dict):
    """Converts a raw Google Serper search result into a compact normalized dictionary."""
    return {
        "title": result.get("title"),
        "snippet": result.get("snippet"),
        "link": result.get("link")
    }

@mcp.tool(name="Web-Search")
async def web_search(query: str):
    """
    Searches the public web using the Google Serper API.
    Use this tool to find current events, recent developments, factual information, or public web content.

    Args:
        query: The search query to execute.

    Returns:
        A dictionary containing the search results from the web.
    """

    enforce_presence(query=query)

    api_key = os.getenv("SERPER_API_KEY")

    if not api_key or not api_key.strip():
        logger.error("SERPER_API_KEY is not configured.")
        raise RuntimeError("Web search provider is not configured.")

    normalized_query = query.strip()

    try:
        search = GoogleSerperAPIWrapper(serper_api_key=api_key.strip(), k=3)
        response = await search.aresults(query=normalized_query)

    except Exception as exc:
        logger.exception("Web search request failed.")
        raise RuntimeError(f"Web search provider request failed: {exc}") from exc

    if not isinstance(response, dict):
        raise ValueError("Web search provider returned an invalid response format.")

    if response.get("error"):
        logger.warning("Serper returned an API error: %s", response["error"])
        raise RuntimeError(f"Web search provider error: {response['error']}")

    organic_results = response.get("organic", [])

    if not isinstance(organic_results, list):
        raise ValueError("Web search provider returned an invalid organic results collection.")

    results = [format_result(result) for result in organic_results if isinstance(result, dict)]

    logger.info("Web search completed successfully for query: %s", normalized_query)

    return {
        "results": results,
    }

if __name__ == "__main__":
    mcp.run(transport="stdio")