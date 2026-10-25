"""Safe error mapping and LangGraph node-failure handling."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.errors import NodeError
from langgraph.graph import END
from langgraph.types import Command
from pydantic import ValidationError

logger = logging.getLogger(__name__)


def _exception_chain(exc: BaseException):
    """Yield an exception and its causes/contexts without looping."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _status_code(exc: BaseException) -> int | None:
    """Read a status code from common SDK/HTTP exception shapes."""
    for item in _exception_chain(exc):
        direct = getattr(item, "status_code", None)
        if isinstance(direct, int):
            return direct
        response = getattr(item, "response", None)
        response_status = getattr(response, "status_code", None)
        if isinstance(response_status, int):
            return response_status
    return None



def exception_summary(exc: BaseException, max_chars: int = 500) -> str:
    """Return a compact single-line exception message for logs, without a traceback."""
    message = " ".join(str(exc).split())
    if not message:
        message = "No exception message provided"
    if len(message) > max_chars:
        message = message[: max_chars - 1] + "…"
    return message

def get_error_message(exc: BaseException) -> str:
    """Map common provider/tool failures to concise user-safe messages."""
    chain = list(_exception_chain(exc))
    names = " ".join(type(item).__name__.lower() for item in chain)
    messages = " ".join(str(item).lower() for item in chain)
    status_code = _status_code(exc)

    if "toolinvocationerror" in names:
        return "The tool call arguments were invalid. Correct the arguments before continuing."

    if any(isinstance(item, ValidationError) for item in chain):
        return "A model response or structured output did not match the expected schema. Please try again."

    # Distinguish exhausted quota/billing from a temporary 429 rate limit.
    quota_markers = (
        "insufficient_quota",
        "quota exceeded",
        "exceeded your current quota",
        "billing limit",
        "billing hard limit",
        "usage limit reached",
        "account quota exhausted",
        "credits exhausted",
    )
    if any(marker in names or marker in messages for marker in quota_markers):
        return "The model or service quota/billing limit appears to be exhausted. Check the provider's usage and billing limits."

    if status_code == 429 or any(
        marker in names or marker in messages
        for marker in ("ratelimit", "rate limit", "rate_limit", "rate_limit_exceeded", "too many requests", "provider limit")
    ):
        return "The required model or service is rate-limited. Please try again later or check the provider's rate limits."

    if status_code in (401, 403) or any(
        marker in names or marker in messages
        for marker in ("authenticationerror", "permissiondenied", "unauthorized", "invalid api key", "invalid_api_key", "incorrect api key")
    ):
        return "The required model or service rejected authentication or permissions. Check the server credentials and access configuration."

    model_missing_markers = (
        "model not found", "model_not_found", "model unavailable", "modelunavailable",
        "model does not exist", "unknown model", "no such model", "deployment not found",
    )
    if any(marker in names or marker in messages for marker in model_missing_markers):
        return "The configured model or deployment was not found or is unavailable. Check the provider/model configuration."
    if status_code == 404:
        return "A required service or requested resource was not found. Check the relevant service and resource configuration."

    context_markers = (
        "context length", "context window", "maximum context", "token limit",
        "too many tokens", "prompt is too long", "request too large",
    )
    if any(marker in messages for marker in context_markers) or any(
        marker in names for marker in ("contextoverflow", "contextlength", "tokenlimit")
    ):
        return "This request exceeds the model's context limit. Try a shorter request or conversation."

    if any(
        marker in names or marker in messages
        for marker in ("jsondecodeerror", "json decoding", "deserialization", "deserialize", "decode error", "unable to decode", "failed to parse", "malformed json", "invalid json", "outputparserexception")
    ):
        return "A model or service response could not be decoded or parsed into the expected format. Check the response format or structured-output schema."

    if status_code in (408, 504) or any(
        marker in names or marker in messages
        for marker in ("timeout", "timed out", "deadline exceeded")
    ):
        return "The request timed out. Check the service availability and try again later."

    if status_code in (500, 502, 503, 504) or any(
        marker in names or marker in messages
        for marker in (
            "connection", "connecterror", "service unavailable", "temporarily unavailable",
            "internalservererror", "server disconnected", "connection reset",
        )
    ):
        return "The required model or service encountered a server or connection problem. Check provider/service availability."

    if isinstance(exc, ValueError):
        message = str(exc).strip().lower()
        if "is required. ask the user for this information" in message:
            return "A required tool input is missing. Ask the user to provide the missing information."
        if "must be later than" in message:
            return "The end time must be later than the start time. Ask the user to provide a valid time range."
        if "invalid timezone" in message:
            return "The supplied timezone is invalid. Ask the user for a valid IANA timezone."
        if "unsupported datetime format" in message or "unsupported calendar date format" in message:
            return "A date or time value has an unsupported format. Ask the user to provide a valid date/time."

    # Keep arbitrary implementation details and provider payloads out of user-facing responses.
    return "I couldn't complete this request because an internal error occurred. Please try again."

def graph_error_handler(state: Any, error: NodeError) -> Command:
    """Log an uncaught node failure concisely and end the current graph with a safe response."""
    exc = error.error
    logger.error(
        "LangGraph node failed | node=%s | exception_type=%s | error=%s",
        error.node,
        type(exc).__name__,
        exception_summary(exc),
    )
    error_response = get_error_message(exc)

    return Command(
        update={
            "final_response": error_response,
            "history": [AIMessage(content=error_response)],
            "run_status": "failed",
        },
        goto=END,
    )
