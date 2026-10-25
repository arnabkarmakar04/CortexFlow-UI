import logging
from pathlib import Path

from langchain_groq import ChatGroq
from langchain_nvidia_ai_endpoints import ChatNVIDIA

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MCP_DIR = PROJECT_ROOT / "src" / "mcp_servers"

client_config = {
    "DateTime": {"transport": "stdio", "command": "python", "args": [str(MCP_DIR / "time_server.py")]},
    "WebSearch": {"transport": "stdio", "command": "python", "args": [str(MCP_DIR / "web_search_serper.py")]},
    "Location": {"transport": "stdio", "command": "python", "args": [str(MCP_DIR / "location_server.py")]},
    "Weather": {"transport": "stdio", "command": "python", "args": [str(MCP_DIR / "weather_server.py")]},
    "WikiPedia": {"transport": "stdio", "command": "python", "args": [str(MCP_DIR / "wikipedia_server.py")]},
    "StockPrice": {"transport": "stdio", "command": "python", "args": [str(MCP_DIR / "stock_price_server.py")]},
    "Calendar": {"transport": "stdio", "command": "python", "args": [str(MCP_DIR / "calendar_server.py")]},
    "ExpenseTracker": {"transport": "stdio", "command": "python", "args": [str(MCP_DIR / "expense_tracker.py")]},
    "ResearchOrientesWebSearch": {"transport": "stdio", "command": "python", "args": [str(MCP_DIR / "research_tavily.py")]},
}

orchestrator_llm = ChatGroq(model="openai/gpt-oss-20b", temperature=0.0)
clarification_llm = ChatGroq(model="openai/gpt-oss-20b", temperature=0.3)
execution_llm = ChatNVIDIA(model="nvidia/nemotron-3.5-super-vl-120b-a12b", temperature=0.0)
evaluator_llm = ChatNVIDIA(model="nvidia/nemotron-3.5-super-vl-120b-a12b", temperature=0.0)
chat_llm = ChatGroq(model="openai/gpt-oss-20b", temperature=0.2)
synth_llm = ChatGroq(model="openai/gpt-oss-20b", temperature=0.2)