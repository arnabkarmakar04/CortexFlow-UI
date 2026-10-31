"""Shared turn-processing service used by the HTTP API and other entry points."""

from __future__ import annotations

import logging
from uuid import UUID
from typing import Any

from psycopg_pool import AsyncConnectionPool

from src.core.database import append_message, conversation_exists, get_conversation_messages
from src.core.errors import exception_summary, get_error_message
from src.graph.request_state import create_request_state

logger = logging.getLogger(__name__)


async def run_graph_turn(graph: Any, message: str, thread_id: UUID) -> tuple[str, str]:
    """Invoke LangGraph using the conversation's stable thread_id."""
    config = {"configurable": {"thread_id": str(thread_id)}}
    input_state = create_request_state(message)

    logger.info("Chat turn started | thread_id=%s", thread_id)
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
            # Log node names only; do not log prompts, user content, or tool output.
            logger.info(
                "Graph progress | thread_id=%s | nodes=%s",
                thread_id,
                ", ".join(str(node) for node in updates.keys()),
            )

    snapshot = await graph.aget_state(config)
    values = snapshot.values or {}
    response = values.get("final_response")
    run_status = values.get("run_status", "failed")
    tool_error_status = bool(values.get("tool_error_status", False))

    if response is None:
        logger.error(
            "Graph finished without final_response | thread_id=%s | run_status=%s",
            thread_id,
            run_status,
        )
        return "I'm sorry, I couldn't generate a response.", "failed"

    if run_status == "failed":
        logger.error("Chat turn ended after a handled graph failure | thread_id=%s", thread_id)
        return str(response), "failed"
    if run_status == "completed" and tool_error_status:
        logger.warning("Chat turn completed with MCP tool errors | thread_id=%s", thread_id)
        return str(response), "completed_with_tool_errors"
    if run_status == "completed":
        logger.info("Chat turn completed | thread_id=%s", thread_id)
        return str(response), "completed"

    logger.error(
        "Graph ended with unexpected run status | thread_id=%s | run_status=%s",
        thread_id,
        run_status,
    )
    return str(response), "failed"


async def send_conversation_message(
    pool: AsyncConnectionPool,
    graph: Any,
    thread_id: UUID,
    message: str,
    display_content: str | None = None,
) -> dict[str, Any]:
    """Store the visible user turn, run the graph, and store its visible response.

    ``message`` can include attachment context for the model. ``display_content`` is the
    concise transcript version shown in the UI, so file contents are not rendered as a chat
    bubble. Both are deliberately separate from LangGraph's checkpointed message history.
    """
    if not await conversation_exists(pool, thread_id):
        raise LookupError("Conversation not found.")

    transcript_content = (display_content or message).strip()
    user_record = await append_message(pool, thread_id, "user", transcript_content)
    if user_record is None:
        raise LookupError("Conversation not found.")

    try:
        response, status = await run_graph_turn(graph, message, thread_id)
    except Exception as exc:
        logger.error(
            "Chat turn raised an exception | thread_id=%s | exception_type=%s | error=%s",
            thread_id,
            type(exc).__name__,
            exception_summary(exc),
        )
        response, status = get_error_message(exc), "failed"

    if status == "failed":
        visible_response = (
            f"**Request failed.**\n\n{response}\n\n"
            "Check the FastAPI terminal logs for the node or model failure details."
        )
    elif status == "completed_with_tool_errors":
        visible_response = f"**Completed with tool errors.**\n\n{response}"
    else:
        visible_response = response

    assistant_record = await append_message(pool, thread_id, "assistant", visible_response)
    if assistant_record is None:
        raise LookupError("Conversation was deleted while the request was running.")

    persisted_conversation = await get_conversation_messages(pool, thread_id)
    if persisted_conversation is None:
        raise LookupError("Conversation was deleted while the request was running.")

    return {
        "response": visible_response,
        "conversation_id": str(thread_id),
        "title": persisted_conversation["conversation"]["title"],
        "status": status,
    }
