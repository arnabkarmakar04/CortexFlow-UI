"""FastAPI entry point connecting the browser UI to the CortexFlow graph.

Run from the repository root: uvicorn src.api:app --reload
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langchain_mcp_adapters.client import MultiServerMCPClient

from src.api_schemas import ChatRequest, ChatResponse
from src.core.config import client_config
from src.core.errors import get_error_message
from src.graph.request_state import create_request_state
from src.graph.workflow import build_graph

logger = logging.getLogger(__name__)
UI_DIR = Path(__file__).resolve().parent / "ui"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create the MCP client and compiled graph once for this API process."""
    try:
        app.state.mcp_client = MultiServerMCPClient(client_config, tool_name_prefix=True)
        tools = await app.state.mcp_client.get_tools()
        app.state.agent_graph = build_graph(tools)
        logger.info("CortexFlow ready | MCP tools=%d", len(tools))
    except Exception:
        logger.exception("CortexFlow initialization failed")
        raise
    yield


app = FastAPI(title="CortexFlow", version="1.0.0", lifespan=lifespan)
app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")


async def process_message(message: str, conversation_id: str) -> str:
    """Process one message through LangGraph and return its final response."""
    graph = app.state.agent_graph
    config = {"configurable": {"thread_id": conversation_id}}
    input_state = create_request_state(message)

    logger.info("Chat turn started | conversation_id=%s", conversation_id)
    async for event in graph.astream(
        input_state,
        config=config,
        stream_mode="updates",
        subgraphs=True,
        version="v2",
    ):
        if event.get("type") != "updates":
            continue
        updates = event.get("data", {})
        if isinstance(updates, dict) and updates:
            # Log node names only; avoid dumping prompts, user data, or tool outputs.
            logger.info(
                "Graph progress | conversation_id=%s | nodes=%s",
                conversation_id,
                ", ".join(str(node) for node in updates.keys()),
            )

    snapshot = await graph.aget_state(config)
    values = snapshot.values or {}
    response = values.get("final_response")
    if response is None:
        logger.warning("Graph finished without final_response | conversation_id=%s", conversation_id)
        return "I'm sorry, I couldn't generate a response."

    logger.info("Chat turn completed | conversation_id=%s", conversation_id)
    return str(response)


@app.get("/")
async def index():
    return FileResponse(UI_DIR / "index.html")


@app.get("/api/health")
async def health():
    ready = getattr(app.state, "agent_graph", None) is not None
    return {"status": "ok" if ready else "initializing", "service": "cortexflow"}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    if getattr(app.state, "agent_graph", None) is None:
        raise HTTPException(status_code=503, detail="The CortexFlow graph has not initialized.")

    try:
        response = await process_message(payload.message, payload.conversation_id)
        return ChatResponse(response=response, conversation_id=payload.conversation_id)
    except Exception as exc:
        logger.exception("Chat turn failed | conversation_id=%s", payload.conversation_id)
        raise HTTPException(status_code=500, detail=get_error_message(exc)) from exc
