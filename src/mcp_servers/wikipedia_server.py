import os
import warnings
import logging
import requests
from urllib.parse import quote
from dotenv import load_dotenv
from fastmcp import FastMCP
load_dotenv()

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("Wikipedia Server")

WIKIPEDIA_SEARCH_URL = os.getenv("WIKIPEDIA_SEARCH_URL")
WIKIPEDIA_SUMMARY_URL = os.getenv("WIKIPEDIA_SUMMARY_URL")

def enforce_presence(**kwargs):
    """Validates that all required tool arguments are present and non-empty."""
    for key, value in kwargs.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"'{key}' is required. Ask the user for this information.")

def format_article(article: dict):
    """Converts a Wikipedia page summary into a compact normalized dictionary."""
    content_urls = article.get("content_urls", {})
    desktop = content_urls.get("desktop", {})

    return {
        "title": article.get("title"),
        "content": article.get("extract"),
        "link": desktop.get("page")
    }

@mcp.tool(name="Wikipedia-Search")
def wikipedia_search(query: str):
    """
    Searches Wikipedia and retrieves a summary of the best matching article.
    Use this tool for encyclopedic information, historical facts, biographies, established concepts, places, and other topics covered by Wikipedia.

    Args:
        query: The topic, concept, person, place, or subject to search for.

    Returns:
        A dictionary containing the retrieved Wikipedia article.
        If no matching article is found, the returned article list is empty.
    """

    enforce_presence(query=query)
    user_agent = os.getenv("WIKIPEDIA_USER_AGENT")

    if not user_agent or not user_agent.strip():
        raise RuntimeError("WIKIPEDIA_USER_AGENT is not configured. Set a descriptive user-agent with contact information.")

    if not WIKIPEDIA_SEARCH_URL:
        raise RuntimeError("WIKIPEDIA_SEARCH_URL is not configured.")

    if not WIKIPEDIA_SUMMARY_URL:
        raise RuntimeError("WIKIPEDIA_SUMMARY_URL is not configured.")

    normalized_query = query.strip()
    headers = {"User-Agent": user_agent.strip(),}

    try:
        search_response = requests.get(
            WIKIPEDIA_SEARCH_URL,
            headers=headers,
            params={"q": normalized_query},
            timeout=10,
        )
        search_response.raise_for_status()
        search_data = search_response.json()

    except requests.exceptions.Timeout as exc:
        logger.exception("Wikipedia search request timed out.")
        raise RuntimeError("Wikipedia API request timed out.") from exc

    except requests.exceptions.RequestException as exc:
        logger.exception("Wikipedia search request failed.")
        raise RuntimeError(f"Wikipedia API request failed: {exc}") from exc

    if not isinstance(search_data, dict):
        raise ValueError("Wikipedia search returned an invalid response.")

    pages = search_data.get("pages", [])

    if not isinstance(pages, list):
        raise ValueError("Wikipedia search returned an invalid pages collection.")

    if not pages:
        logger.info("No Wikipedia articles found for '%s'.", normalized_query)

        return {
            "article": [],
        }

    best_match = pages[0]

    if not isinstance(best_match, dict):
        raise ValueError("Wikipedia search returned an invalid page result.")

    page_title = best_match.get("title")

    if not page_title:
        raise ValueError("Wikipedia search returned a page without a title.")

    summary_url = (f"{WIKIPEDIA_SUMMARY_URL.rstrip('/')}/{quote(page_title, safe='')}")

    try:
        summary_response = requests.get(
            summary_url,
            headers=headers,
            timeout=10,
        )
        summary_response.raise_for_status()
        article = summary_response.json()

    except requests.exceptions.Timeout as exc:
        logger.exception("Wikipedia summary request timed out.")
        raise RuntimeError("Wikipedia API request timed out.") from exc

    except requests.exceptions.RequestException as exc:
        logger.exception("Wikipedia summary request failed.")
        raise RuntimeError(f"Wikipedia API request failed: {exc}") from exc

    if not isinstance(article, dict):
        raise ValueError("Wikipedia summary returned an invalid response.")

    if not article.get("title") or not article.get("extract"):
        raise ValueError(f"Wikipedia summary could not be retrieved for '{page_title}'.")

    formatted_article = format_article(article)

    logger.info("Wikipedia article retrieved successfully: %s", page_title)

    return {
        "article": [formatted_article],
    }

if __name__ == "__main__":
    mcp.run(transport="stdio")