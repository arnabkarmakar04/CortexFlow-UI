import os
import warnings
import logging
from dotenv import load_dotenv
from tavily import TavilyClient
from fastmcp import FastMCP
load_dotenv()

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("Research Oriented Web Search Server")

def enforce_presence(**kwargs):
    """Validates that all required tool arguments are present and non-empty."""
    for key, value in kwargs.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"'{key}' is required. Ask the user for this information.")

def format_result(result: dict):
    """Converts a raw Tavily search result into a standardized flat dictionary."""
    return {
        "title": result.get("title"),
        "snippet": result.get("content"),
        "link": result.get("url")
    }

@mcp.tool(name="Research-Oriented-Web-Search")
def tavily_search(query: str) -> dict:
    """
    Searches the public web to retrieve relevant information with an AI-generated answer.
    Use this tool for research-oriented web search, multi-source investigation, factual research, or questions
    where a concise synthesized answer alongside supporting web results is useful.

    Args:
        query: The research query to execute.

    Returns:
        A dictionary containing the AI-generated answer and supporting web search results.
    """

    enforce_presence(query=query)

    api_key = os.getenv("TAVILY_API_KEY")

    if not api_key or not api_key.strip():
        logger.error("TAVILY_API_KEY is not configured.")
        raise RuntimeError("Tavily search provider is not configured.")

    normalized_query = query.strip()

    try:
        tavily = TavilyClient(api_key=api_key.strip())

        response = tavily.search(
            query=normalized_query,
            topic="general",
            search_depth="basic",
            include_answer=True,
            include_raw_content=False,
            include_images=False,
            max_results=3
        )

    except Exception as exc:
        logger.exception("Tavily search request failed.")
        raise RuntimeError(f"Tavily search provider request failed: {exc}") from exc

    if not isinstance(response, dict):
        raise ValueError("Tavily returned an invalid response format.")

    answer = response.get("answer")
    raw_results = response.get("results", [])

    if not isinstance(raw_results, list):
        raise ValueError("Tavily returned an invalid results collection.")

    results = [format_result(result) for result in raw_results if isinstance(result, dict)]

    if not results and not answer:
        logger.info("Tavily returned no useful results for: %s", normalized_query)

        return {
            "answer": None,
            "results": [],
        }

    logger.info( "Tavily search completed successfully for: %s", normalized_query)

    return {
        "answer": answer,
        "results": results,
    }

if __name__ == "__main__":
    mcp.run(transport="stdio")
