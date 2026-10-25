"""FastAPI entry point connecting the browser UI to the CortexFlow graph.
Run from the repository root: uvicorn src.api:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from langchain_mcp_adapters.client import MultiServerMCPClient

from src.api_schemas import ChatRequest, ChatResponse
from src.core.config import client_config
from src.core.errors import exception_summary, get_error_message
from src.graph.request_state import create_request_state
from src.graph.workflow import build_graph

logger = logging.getLogger(__name__)
UI_DIR = Path(__file__).resolve().parent / "ui"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create the MCP client and graph; keep the API alive but unavailable on init failure."""

    app.state.mcp_client = None
    app.state.agent_graph = None
    app.state.initialization_failed = False
    try:
        app.state.mcp_client = MultiServerMCPClient(client_config, tool_name_prefix=True)
        tools = await app.state.mcp_client.get_tools()
        app.state.agent_graph = build_graph(tools)
        logger.info("CortexFlow ready | MCP tools=%d", len(tools))
    except Exception as exc:
        # Keep the process alive without a traceback; health reports the service as unavailable.
        logger.error(
            "CortexFlow initialization failed | exception_type=%s | error=%s",
            type(exc).__name__,
            exception_summary(exc),
        )
        app.state.initialization_failed = True
    yield


app = FastAPI(title="CortexFlow", version="1.0.0", lifespan=lifespan)
app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")

@app.exception_handler(Exception)
async def unhandled_api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log unexpected request-handler failures without returning exception details."""

    logger.error(
        "Unhandled API exception | method=%s | path=%s | exception_type=%s | error=%s",
        request.method,
        request.url.path,
        type(exc).__name__,
        exception_summary(exc),
    )

    return JSONResponse(
        status_code=500, content={"detail": get_error_message(exc)},
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return safe 422 details and log field locations without request values."""

    fields = sorted({
        ".".join(str(part) for part in error.get("loc", ()) if part not in ("body", "query", "path")) for error in exc.errors()
    })
    logger.warning(
        "API request validation failed | path=%s | fields=%s", request.url.path, ",".join(fields) or "unknown",
    )
    return JSONResponse(
        status_code=422, content={"detail": "Invalid request payload. Check required fields and field formats."},
    )


async def process_message(message: str, conversation_id: str) -> tuple[str, str]:
    """Process one message and return (safe response, execution status)."""

    graph = app.state.agent_graph
    config = {"configurable": {"thread_id": conversation_id}}
    input_state = create_request_state(message)

    logger.info("Chat turn started | conversation_id=%s", conversation_id)
    async for event in graph.astream(input_state, config=config, stream_mode="updates", version="v2"):
        if event.get("type") != "updates":
            continue
        updates = event.get("data", {})
        if isinstance(updates, dict) and updates:
            # Log node names only; avoid dumping prompts, user data, or tool outputs.
            logger.info(
                "Graph progress | conversation_id=%s | nodes=%s", conversation_id, ", ".join(str(node) for node in updates.keys()),
            )

    snapshot = await graph.aget_state(config)
    values = snapshot.values or {}
    response = values.get("final_response")
    run_status = values.get("run_status", "failed")
    tool_error_status = bool(values.get("tool_error_status", False))

    if response is None:
        logger.error(
            "Graph finished without final_response | conversation_id=%s | run_status=%s", conversation_id, run_status,
        )
        return "I'm sorry, I couldn't generate a response.", "failed"

    if run_status == "failed":
        logger.error(
            "Chat turn ended after a handled graph failure | conversation_id=%s",conversation_id,
        )
    elif run_status == "completed" and tool_error_status:
        logger.warning(
            "Chat turn completed with MCP tool errors | conversation_id=%s | tool_error_status=true",conversation_id,
        )

        return str(response), "completed_with_tool_errors"
    
    elif run_status == "completed":
        logger.info("Chat turn completed | conversation_id=%s", conversation_id)
    else:
        logger.error(
            "Graph ended with unexpected run status | conversation_id=%s | run_status=%s", conversation_id, run_status,
        )

        return str(response), "failed"

    return str(response), run_status


@app.get("/")
async def index():
    return FileResponse(UI_DIR / "index.html")


@app.get("/api/health")
async def health():
    ready = getattr(app.state, "agent_graph", None) is not None
    if ready:
        status = "ok"
    elif getattr(app.state, "initialization_failed", False):
        status = "unavailable"
    else:
        status = "initializing"
    return {"status": status, "service": "cortexflow"}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    if getattr(app.state, "agent_graph", None) is None:
        raise HTTPException(status_code=503, detail="The CortexFlow graph has not initialized.")

    try:
        response, status = await process_message(payload.message, payload.conversation_id)
        return ChatResponse(
            response=response,
            conversation_id=payload.conversation_id,
            status=status,
        )
    except Exception as exc:
        logger.error(
            "Chat turn failed | conversation_id=%s | exception_type=%s | error=%s",
            payload.conversation_id,
            type(exc).__name__,
            exception_summary(exc),
        )
        raise HTTPException(status_code=500, detail=get_error_message(exc)) from exc
