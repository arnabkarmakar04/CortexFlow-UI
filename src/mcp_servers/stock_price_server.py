import os
import warnings
import logging
import requests
from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()
warnings.filterwarnings("ignore")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("Stock Price Server")

def enforce_presence(**kwargs):
    """Validates that all required tool arguments are present and non-empty."""
    for key, value in kwargs.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"'{key}' is required. Ask the user for this information.")

def format_stock(data: dict):
    """Convert the Alpha Vantage GLOBAL_QUOTE response into a compact normalized stock representation. """

    quote = data.get("Global Quote", {})

    required_fields = {
        "01. symbol": "symbol",
        "02. open": "open",
        "03. high": "high",
        "04. low": "low",
        "05. price": "price",
        "06. volume": "volume",
        "07. latest trading day": "latest_trading_day",
        "08. previous close": "previous_close",
        "09. change": "change",
        "10. change percent": "change_percent",
    }

    missing_fields = [field for field in required_fields if not quote.get(field)]

    if missing_fields:
        raise ValueError(f"Stock quote response is missing required fields: {', '.join(missing_fields)}.")

    return {output_key: quote[input_key] for input_key, output_key in required_fields.items()}

@mcp.tool(name="Stock-Price")
def stock_price(symbol: str):
    """
    Retrieves the latest market quote for a publicly traded stock.
    Use this tool to check current stock prices, market quotes, trading ranges, or volume.

    Args:
        symbol: The ticker symbol of the publicly traded stock (e.g., 'AAPL', 'MSFT', 'TSLA', 'INFY').

    Returns:
        A dictionary containing the latest trading metrics, including price, open, high, low, volume, and change.
    """

    enforce_presence(symbol=symbol)

    api_key = os.getenv("ALPHA_VANTAGE_API_KEY")

    if not api_key or not api_key.strip():
        raise RuntimeError("Stock data provider is not configured.")

    normalized_symbol = symbol.strip().upper()

    try:
        response = requests.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "GLOBAL_QUOTE",
                "symbol": normalized_symbol,
                "apikey": api_key.strip(),
            },
            timeout=10,
        )

        response.raise_for_status()

        data = response.json()

    except requests.exceptions.Timeout as exc:
        logger.exception("Stock API request timed out.")
        raise RuntimeError("Stock data provider request timed out.") from exc

    except requests.exceptions.RequestException as exc:
        logger.exception("Stock API request failed.")
        raise RuntimeError(f"Stock data provider request failed: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("Stock data provider returned an invalid response.")

    if data.get("Error Message"):
        logger.warning("Alpha Vantage returned an API error for symbol: %s", normalized_symbol)
        raise ValueError(f"Stock data provider error: {data['Error Message']}")

    if data.get("Note"):
        logger.warning("Alpha Vantage returned a usage-limit message.")
        raise RuntimeError(f"Stock data provider limit: {data['Note']}")

    stock = format_stock(data)

    logger.info("Stock information retrieved successfully for %s.", normalized_symbol)

    return {
        "stock": stock,
    }

if __name__ == "__main__":
    mcp.run(transport="stdio")