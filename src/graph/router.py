from typing import Literal
from langchain_core.messages import AIMessage, ToolMessage

from src.graph.state import MCPState, MasterState

import logging
logger = logging.getLogger(__name__)

def route_after_orchestrator(state: MasterState) -> list[str]:
    """Route execution to the appropriate downstream node or subgraph based on the orchestrator's decision, while enforcing the clarification iteration limit."""
    
    if state.get("need_clarification"):
        if state.get("current_clarification_iteration", 0) >= state.get("max_clarification_iterations", 3):
            return ["ClarificationLimit"]
        return ["Clarification_Node"]

    route = state.get("route")

    if route == "MCP":
        return ["MCP_Subgraph"]
    if route == "RAG":
        return ["RAG"]
    if route == "MCP_RAG":
        return ["MCP_Subgraph", "RAG"]

    return ["Conversation"]


def route_after_mcp(state: MCPState) -> Literal["ToolExecutor", "MCPCollector"]:
    """Route MCP execution to the tool executor when the latest planner output contains tool calls; otherwise route to the result collector."""

    working_memory = state.get("working_memory", [])

    if not working_memory:
        return "MCPCollector"

    last_message = working_memory[-1]

    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "ToolExecutor"

    return "MCPCollector"


def route_after_tool_executor(state: MCPState) -> Literal["Evaluator", "MCPCollector"]:
    """Route completed MCP tool execution to the evaluator when dependency evaluation is required; otherwise route to the result collector."""

    working_memory = state.get("working_memory", [])
    task_plan = state.get("task_plan", [])
    current_tool_messages = []

    for message in reversed(working_memory):
        if isinstance(message, AIMessage):
            break

        if isinstance(message, ToolMessage):
            current_tool_messages.append(message)

    if not current_tool_messages:
        logger.warning("No MCP tool results found after ToolNode execution.")
        return "MCPCollector"

    for message in current_tool_messages:
        # The custom ToolNode error handler represents handled tool failures as
        # ToolMessage(status="error"); this is an intentional workflow branch.
        if message.status == "error":
            error_detail = " ".join(str(message.content).split())
            if not error_detail:
                error_detail = "No error details provided"
            elif len(error_detail) > 400:
                error_detail = error_detail[:399] + "…"

            logger.error(
                "MCP tool returned an error ToolMessage | tool=%s | tool_call_id=%s | error=%s",
                message.name or "unknown",
                message.tool_call_id or "unknown",
                error_detail,
            )
            return "MCPCollector"

    # Check if any MCP task has dependencies that need to be evaluated
    has_dependencies = any(task.execution_mode == "MCP" and task.dependencies for task in task_plan)

    if has_dependencies:
        return "Evaluator"

    return "MCPCollector"


def route_after_evaluator(state: MCPState) -> Literal["MCP_Node", "MCPCollector"]:
    """Route MCP execution back to the planner when further execution is required; otherwise route to the result collector."""

    status = state.get("evaluation_status")

    if status == "continue":
        return "MCP_Node"

    if status == "complete":
        return "MCPCollector"

    logger.error("Unexpected MCP evaluation status | status=%s", status)
    return "MCPCollector"