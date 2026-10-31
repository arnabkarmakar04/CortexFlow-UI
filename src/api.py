"""FastAPI entry point for CortexFlow and its PostgreSQL-backed conversation API.

Run from the repository root with: uvicorn src.api:app --reload
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from src.api_schemas import (
    ChatRequest,
    ChatResponse,
    ConversationCreateResponse,
    ConversationMessageRequest,
    ConversationRenameRequest,
    ConversationMessagesResponse,
    ConversationSummary,
)
from src.core.config import client_config
from src.core.database import (
    create_database_if_not_exists,
    create_conversation,
    database_conninfo,
    delete_conversation,
    get_conversation_messages,
    initialize_application_schema,
    list_conversations,
    rename_conversation,
)
from src.core.errors import exception_summary, get_error_message
from src.graph.workflow import build_graph
from src.services.chat_service import send_conversation_message

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = PROJECT_ROOT / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize PostgreSQL, LangGraph's checkpointer, MCP tools, and the graph."""
    app.state.db_pool = None
    app.state.checkpointer = None
    app.state.agent_graph = None
    app.state.mcp_client = None
    app.state.initialization_failed = False

    async with AsyncExitStack() as stack:
        try:
            # Step 1: Ensure the database exists and is reachable.
            await create_database_if_not_exists()

            # Step 2: Build the connection string.
            conninfo = database_conninfo()

            pool = AsyncConnectionPool(
                conninfo=conninfo,
                min_size=1,
                max_size=10,
                open=False,
            )
            stack.push_async_callback(pool.close)
            await pool.open(wait=True)

            # Create application-owned tables.
            await initialize_application_schema(pool)
            app.state.db_pool = pool

            # The context-managed saver must remain open for the app's full lifetime.
            checkpointer = await stack.enter_async_context(
                AsyncPostgresSaver.from_conn_string(conninfo)
            )
            await checkpointer.setup()
            app.state.checkpointer = checkpointer

            # Initialize MCP & build the graph.
            app.state.mcp_client = MultiServerMCPClient(
                client_config,
                tool_name_prefix=True,
            )
            tools = await app.state.mcp_client.get_tools()
            app.state.agent_graph = build_graph(tools, checkpointer=checkpointer)
            logger.info(
                "CortexFlow ready | MCP tools=%d | PostgreSQL persistence=enabled",
                len(tools),
            )
        except Exception as exc:
            logger.error(
                "CortexFlow initialization failed | exception_type=%s | error=%s",
                type(exc).__name__,
                exception_summary(exc),
            )
            app.state.initialization_failed = True

        yield


app = FastAPI(title="CortexFlow", version="2.0.0", lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")


@app.exception_handler(Exception)
async def unhandled_api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log unexpected failures without returning internal exception details."""
    logger.error(
        "Unhandled API exception | method=%s | path=%s | exception_type=%s | error=%s",
        request.method,
        request.url.path,
        type(exc).__name__,
        exception_summary(exc),
    )
    return JSONResponse(status_code=500, content={"detail": get_error_message(exc)})


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return safe validation errors and log field names only."""
    fields = sorted(
        {
            ".".join(
                str(part)
                for part in error.get("loc", ())
                if part not in ("body", "query", "path")
            )
            for error in exc.errors()
        }
    )
    logger.warning(
        "API request validation failed | path=%s | fields=%s",
        request.url.path,
        ",".join(fields) or "unknown",
    )
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Invalid request payload. Check required fields and field formats."
        },
    )


def require_database_pool() -> AsyncConnectionPool:
    pool = getattr(app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail="PostgreSQL is unavailable. Check the database configuration and server logs.",
        )
    return pool


def require_agent_graph():
    graph = getattr(app.state, "agent_graph", None)
    if graph is None:
        raise HTTPException(
            status_code=503,
            detail="The CortexFlow graph has not initialized. Check server logs and API keys.",
        )
    return graph


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/health")
async def health():
    pool = getattr(app.state, "db_pool", None)
    database_ready = False
    if pool is not None:
        try:
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
                    database_ready = (await cur.fetchone()) == (1,)
        except Exception as exc:
            logger.warning(
                "PostgreSQL health check failed | exception_type=%s | error=%s",
                type(exc).__name__,
                exception_summary(exc),
            )
    graph_ready = getattr(app.state, "agent_graph", None) is not None
    if database_ready and graph_ready:
        status = "ok"
    elif getattr(app.state, "initialization_failed", False) or not database_ready:
        status = "unavailable"
    else:
        status = "initializing"
    return {
        "status": status,
        "service": "cortexflow",
        "database": "ok" if database_ready else "unavailable",
        "graph": "ok" if graph_ready else "unavailable",
    }


@app.post("/api/conversations", response_model=ConversationCreateResponse)
async def new_conversation():
    """Create a server-side conversation with a new stable UUID thread_id."""
    try:
        return await create_conversation(require_database_pool())
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Conversation creation failed | exception_type=%s | error=%s",
            type(exc).__name__,
            exception_summary(exc),
        )
        raise HTTPException(status_code=500, detail=get_error_message(exc)) from exc


@app.get("/api/conversations", response_model=list[ConversationSummary])
async def conversations(
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=200),
):
    """List recent non-empty conversations; q matches titles only, never message content."""
    try:
        return await list_conversations(require_database_pool(), search_title=q, limit=limit)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Conversation list failed | exception_type=%s | error=%s",
            type(exc).__name__,
            exception_summary(exc),
        )
        raise HTTPException(status_code=500, detail=get_error_message(exc)) from exc


@app.patch("/api/conversations/{thread_id}", response_model=ConversationSummary)
async def update_conversation_title(
    thread_id: UUID, payload: ConversationRenameRequest
):
    """Rename a conversation in PostgreSQL; the database remains the source of truth."""
    try:
        result = await rename_conversation(
            require_database_pool(), thread_id, payload.title
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        # Include message_count because the response uses ConversationSummary.
        transcript = await get_conversation_messages(require_database_pool(), thread_id)
        result["message_count"] = len(transcript["messages"]) if transcript else 0
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Conversation rename failed | thread_id=%s | exception_type=%s | error=%s",
            thread_id,
            type(exc).__name__,
            exception_summary(exc),
        )
        raise HTTPException(status_code=500, detail=get_error_message(exc)) from exc


@app.get(
    "/api/conversations/{thread_id}/messages",
    response_model=ConversationMessagesResponse,
)
async def conversation_messages(thread_id: UUID):
    """Load the stored transcript for a conversation selected in the sidebar."""
    try:
        result = await get_conversation_messages(require_database_pool(), thread_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Conversation transcript read failed | thread_id=%s | exception_type=%s | error=%s",
            thread_id,
            type(exc).__name__,
            exception_summary(exc),
        )
        raise HTTPException(status_code=500, detail=get_error_message(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return result


@app.post(
    "/api/conversations/{thread_id}/messages",
    response_model=ChatResponse,
)
async def send_message(thread_id: UUID, payload: ConversationMessageRequest):
    """Persist a user turn, invoke LangGraph with that thread_id, and persist the reply."""
    pool = require_database_pool()
    graph = require_agent_graph()
    try:
        return await send_conversation_message(
            pool=pool,
            graph=graph,
            thread_id=thread_id,
            message=payload.message,
            display_content=payload.display_content,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Chat request failed | thread_id=%s | exception_type=%s | error=%s",
            thread_id,
            type(exc).__name__,
            exception_summary(exc),
        )
        raise HTTPException(status_code=500, detail=get_error_message(exc)) from exc


@app.delete("/api/conversations/{thread_id}")
async def remove_conversation(thread_id: UUID):
    """Delete a conversation, its transcript, and its LangGraph checkpoint thread."""
    pool = require_database_pool()
    checkpointer = getattr(app.state, "checkpointer", None)
    try:
        exists = await get_conversation_messages(pool, thread_id)
        if exists is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        if checkpointer is not None:
            await checkpointer.adelete_thread(str(thread_id))
        deleted = await delete_conversation(pool, thread_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        return {"deleted": True, "thread_id": str(thread_id)}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Conversation deletion failed | thread_id=%s | exception_type=%s | error=%s",
            thread_id,
            type(exc).__name__,
            exception_summary(exc),
        )
        raise HTTPException(status_code=500, detail=get_error_message(exc)) from exc


@app.post("/api/chat", response_model=ChatResponse, include_in_schema=False)
async def legacy_chat(payload: ChatRequest):
    """Backward-compatible alias for the previous frontend's POST /api/chat contract."""
    try:
        thread_id = UUID(payload.conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="conversation_id must be a UUID.") from exc
    try:
        return await send_conversation_message(
            pool=require_database_pool(),
            graph=require_agent_graph(),
            thread_id=thread_id,
            message=payload.message,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
