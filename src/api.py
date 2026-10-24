"""FastAPI web adapter for the existing CortexFlow LangGraph agent.

This module reuses the existing MCP config, graph builder, request-state
factory, and error handler. It does not change prompts, routing, or graph nodes.
Run from the repository root: uvicorn src.api:app --reload
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.types import Command

from src.core.config import client_config
from src.core.errors import get_error_message
from src.graph.workflow import build_graph
from src.run_agent import create_request_state

logger = logging.getLogger(__name__)
UI_DIR = Path(__file__).resolve().parent / "ui"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize MCP tools and graph once per API process."""
    try:
        app.state.mcp_client = MultiServerMCPClient(client_config, tool_name_prefix=True)
        tools = await app.state.mcp_client.get_tools()
        app.state.agent_graph = build_graph(tools)
        logger.info("CortexFlow graph initialized with %d MCP tools", len(tools))
    except Exception:
        logger.exception("Failed to initialize CortexFlow MCP tools / graph")
        raise
    # Each browser conversation gets a unique LangGraph thread_id.
    app.state.active_threads = set()
    yield


app = FastAPI(title="CortexFlow", version="1.0.0", lifespan=lifespan)
app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    conversation_id: str = Field(min_length=1, max_length=128)


class ChatResponse(BaseModel):
    response: str
    conversation_id: str
    needs_input: bool = False


def _interrupt_value(state) -> str | None:
    if state.tasks and state.tasks[0].interrupts:
        value = state.tasks[0].interrupts[0].value
        return value if isinstance(value, str) else str(value)
    return None


async def _run_turn(graph, thread_config: dict, user_text: str) -> tuple[str, bool]:
    """Run one user turn, preserving LangGraph interrupts within this conversation."""
    state = await graph.aget_state(thread_config)
    if state.tasks and state.tasks[0].interrupts:
        # This turn answers a graph interrupt (e.g. clarification / approval).
        async for _event in graph.astream(
            Command(resume=user_text),
            config=thread_config,
            stream_mode="updates",
            subgraphs=True,
            version="v2",
        ):
            pass
    else:
        timestamp = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S")
        initial_state = create_request_state(user_text, timestamp)
        async for _event in graph.astream(
            initial_state,
            config=thread_config,
            stream_mode="updates",
            subgraphs=True,
            version="v2",
        ):
            pass

    post_state = await graph.aget_state(thread_config)
    interrupt = _interrupt_value(post_state)
    if interrupt is not None:
        return interrupt, True

    final_response = post_state.values.get("final_response") if post_state.values else None
    if final_response is None:
        return "I'm sorry, I couldn't generate a response.", False
    return str(final_response), False


@app.get("/")
async def index():
    return FileResponse(UI_DIR / "index.html")


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "cortexflow"}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest):
    graph = getattr(app.state, "agent_graph", None)
    if graph is None:
        raise HTTPException(status_code=503, detail="The CortexFlow graph has not initialized.")
    thread_id = payload.conversation_id
    thread_config = {"configurable": {"thread_id": thread_id}}
    app.state.active_threads.add(thread_id)

    try:
        response, needs_input = await _run_turn(graph, thread_config, payload.message.strip())
        return ChatResponse(
            response=response,
            conversation_id=payload.conversation_id,
            needs_input=needs_input,
        )
    except Exception as exc:
        logger.exception("CortexFlow request failed")
        error_response = get_error_message(exc)
        try:
            await graph.aupdate_state(
                thread_config,
                {
                    "final_response": error_response,
                    "history": [AIMessage(content=error_response)],
                },
            )
        except Exception:
            logger.exception("Failed to update graph state after request error")
        raise HTTPException(status_code=500, detail=error_response) from exc
