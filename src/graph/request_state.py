"""Translate one validated user message into a LangGraph input update."""
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage


def create_request_state(user_input: str) -> dict:
    """Build per-turn input while leaving checkpoint-managed fields untouched."""
    user_input = user_input.strip()
    if not user_input:
        raise ValueError("User input cannot be empty.")

    timestamp = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "history": [HumanMessage(content=user_input)],
        "query": user_input,
        "timestamp": timestamp,
        "final_response": None,
        "task_plan": [],
        "execution_guidance": "",
        "need_clarification": False,
        "clarification_reason": "",
        # Intentionally omit current_clarification_iteration so its checkpointed
        # value survives between messages in the same conversation.
        "max_clarification_iterations": 3,
        "route": None,
        "mcp_results": [],
        "rag_context": None,
    }
