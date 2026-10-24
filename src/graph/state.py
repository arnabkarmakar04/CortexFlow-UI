from typing import Annotated, Literal, TypedDict
from langchain_core.messages import AnyMessage, ToolMessage
from langgraph.graph.message import add_messages

from src.planner.schemas import TaskSpec

Route = Literal["MCP", "RAG", "Conversation", "MCP_RAG"]


class BaseSharedState(TypedDict):
    """State shared by the parent graph and compatible subgraphs."""

    query: str
    history: Annotated[list[AnyMessage], add_messages]
    timestamp: str
    final_response: str | None
    task_plan: list[TaskSpec]
    execution_guidance: str
    mcp_results: list[ToolMessage]
    rag_context: str | None

class MasterState(BaseSharedState):
    """Top-level routing and clarification state."""

    need_clarification: bool
    clarification_reason: str | None
    user_clarification: str | None
    user_clarification_intent_type: Literal["merge", "context_switch", "cancel"] | None
    current_clarification_iteration: int
    max_clarification_iterations: int
    route: Route | None

class MCPState(BaseSharedState):
    """Per-invocation MCP execution state."""
    
    working_memory: Annotated[list[AnyMessage], add_messages]
    evaluation_status: Literal["complete", "continue"] | None
    evaluator_reasoning: str | None
