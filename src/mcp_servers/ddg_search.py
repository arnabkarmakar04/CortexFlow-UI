import os
import warnings
import logging
from dotenv import load_dotenv
from ddgs import DDGS
from mcp.server.fastmcp import FastMCP
load_dotenv()

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("DuckDuckGo Server")

def enforce_presence(**kwargs):
    """Validates that all required tool arguments are present and non-empty."""
    for key, value in kwargs.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"'{key}' is required. Ask the user for this information.")

def format_result(result: dict):
    """Converts a raw DuckDuckGo search result into a standardized flat structure."""
    return {
        "title": result.get("title"),
        "snippet": result.get("body"),
        "link": result.get("href")
    }

@mcp.tool(name="DuckDuckGo-Search")
def duckduckgo_search(query: str):
    """
    Searches the public web using DuckDuckGo to retrieve relevant, up-to-date information.
    Use this tool to find current events, factual information, recent developments, or perform general web research.

    Args:
        query: The explicit search query to execute.

    Returns:
        A list of relevant web search results containing page titles, URLs, and summary snippets.
    """

    enforce_presence(query=query)

    try:
        with DDGS() as ddgs:
            response = list(ddgs.text(query.strip(), max_results=3))

        results = [format_result(result) for result in response if isinstance(result, dict)]

        logger.info(f"DuckDuckGo search completed successfully. Returned {len(results)} result(s).")

        return {
            "results": results
        }

    except Exception as e:
        logger.exception("Unexpected DuckDuckGo search error.")
        raise RuntimeError(f"DuckDuckGo search failed: {str(e)}") from e

if __name__ == "__main__":
    mcp.run(transport="stdio")