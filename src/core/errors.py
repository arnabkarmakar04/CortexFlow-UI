import logging

from langchain_core.messages import AIMessage
from langgraph.errors import NodeError
from langgraph.graph import END
from langgraph.types import Command
from pydantic import ValidationError

logger = logging.getLogger(__name__)


def get_error_message(exc: BaseException) -> str:
    """Convert an exception into a safe, user-facing error message based on its type and details."""

    error_type = type(exc).__name__
    error_message = str(exc).strip().lower()

    if isinstance(exc, ValidationError):
        return "I couldn't process the request because the model response failed validation. Please try again."

    if ("ratelimit" in error_type.lower() or "rate limit" in error_message or "too many requests" in error_message or getattr(exc, "status_code", None) == 429):
        return "The language model is temporarily rate-limited. Please try again shortly."

    if ("context" in error_message and ("length" in error_message or "window" in error_message or "token" in error_message or "too large" in error_message)) or any(marker in error_type.lower() for marker in ("contextoverflow", "contextlength", "tokenlimit") ):
        return "I couldn't process this request because the conversation or request is too large for the model's context window."

    if "timeout" in error_type.lower() or "timeout" in error_message:
        return "The request timed out. Please try again shortly."

    if ("connection" in error_type.lower() or "connection" in error_message or "service unavailable" in error_message ):
        return "The required service could not be reached. Please try again shortly."

    if isinstance(exc, ValueError):
        return str(exc).strip() or "I couldn't process the request because the provided information was invalid."

    return "I couldn't complete this request because an internal error occurred. Please try again."

def graph_error_handler(state, error: NodeError) -> Command:
    """Handle a node failure by logging the internal error, updating the state with a user-safe error message and recording it in history, then terminating the graph."""

    logger.error("Node '%s' failed | %s: %s", error.node, type(error.error).__name__, str(error.error).strip() or repr(error.error))
    error_response = get_error_message(error.error)

    return Command(update={"final_response": error_response, "history": [AIMessage(content=error_response)]}, goto=END)