import asyncio
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, BaseMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langgraph.prebuilt import ToolNode

from src.core.config import clarification_llm, chat_llm, evaluator_llm, execution_llm, orchestrator_llm, synth_llm
from src.core.errors import exception_summary, get_error_message
from src.graph.prompts import (
    build_conversation_prompt,
    build_clarification_prompt,
    build_evaluator_prompt,
    build_mcp_prompt,
    build_orchestrator_prompt,
    build_synthesizer_prompt,
)
from src.graph.state import MCPState, MasterState
from src.planner.schemas import ObjectiveCompletionEvaluatorSchema, OrchestratorOutput

logger = logging.getLogger(__name__)
MAX_CONCURRENT_TOOLS = 5
LOCAL_TZ = ZoneInfo("Asia/Kolkata")

def current_timestamp() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


async def orchestrator(state: MasterState) -> MasterState:
    """Interpret the current request and relevant conversation context, identify and classify its objectives, determine the appropriate execution route, detect genuine ambiguity, and produce a dependency-safe execution plan with guidance for downstream MCP execution."""

    history = state.get("history", [])
    context_history = history[:-1] if history and isinstance(history[-1], HumanMessage) else history

    prompt = ChatPromptTemplate.from_messages([
        ("system", build_orchestrator_prompt()),
        MessagesPlaceholder(variable_name="history", optional=True),
        ("human", "User Request: {query}"),
    ])
    chain = prompt | orchestrator_llm.with_structured_output(OrchestratorOutput, method="json_schema")
    response = await chain.ainvoke({"history": context_history, "query": state.get("query", "")})

    modes = {task.execution_mode for task in response.tasks}
    if response.need_clarification:
        route = None
    elif "MCP" in modes and "RAG" in modes:
        route = "MCP_RAG"
    elif "MCP" in modes:
        route = "MCP"
    elif "RAG" in modes:
        route = "RAG"
    else:
        route = "Conversation"

    current_clarification_iteration = state.get("current_clarification_iteration", 0)
    if not response.need_clarification:
        current_clarification_iteration = 0

    return {
        "need_clarification": response.need_clarification,
        "clarification_reason": response.clarification_reason,
        "task_plan": response.tasks,
        "execution_guidance": response.execution_guidance,
        "route": route,
        "final_response": None,
        "mcp_results": [],
        "rag_context": None,
        "current_clarification_iteration": current_clarification_iteration
    }


async def clarification_limit_node(state: MasterState) -> MasterState:
    """Terminate the clarification flow when the maximum number of clarification attempts is reached and reset the active execution and clarification state."""

    clarification_limit_response = (
        "I'm unable to complete this request because the required information wasn't provided after multiple clarification attempts. "
        "Let's start fresh. Please enter your request again with the required details."
    )

    return {
        "final_response": clarification_limit_response,
        "history": [AIMessage(content=clarification_limit_response)],
        "run_status": "completed",
        "need_clarification": False,
        "clarification_reason": None,
        "current_clarification_iteration": 0,
        "route": None,
        "task_plan": [],
        "execution_guidance": "",
        "mcp_results": [],
        "rag_context": None
    }

async def clarification_node(state: MasterState) -> MasterState:
    """ Handle requests that require additional user clarification by generating a clarification prompt based on the orchestrator's feedback and the conversation context, while enforcing the clarification iteration limit."""

    history = state.get("history", [])
    context_history = history[:-1] if history and isinstance(history[-1], HumanMessage) else history

    prompt = ChatPromptTemplate.from_messages([
        ("system", build_clarification_prompt()),
        MessagesPlaceholder(variable_name="history", optional=True),
        ("human", "User's Request: {query}\nClarification Reason: {clarification_reason}")
    ])
    response = await (prompt | clarification_llm).ainvoke({"history": context_history, "query": state.get("query", ""), "clarification_reason": state.get("clarification_reason", "")})

    return {
        "final_response": response.content,
        "history": [AIMessage(content=response.content)],
        "run_status": "completed",
        "current_clarification_iteration": state.get("current_clarification_iteration", 0) + 1,
        "route": None,
        "task_plan": [],
        "execution_guidance": "",
        "mcp_results": [],
        "rag_context": None
    }

async def conversation_node(state: MasterState) -> MasterState:
    """Handle requests that can be answered directly using model capabilities, relevant conversation context, and available system information without MCP or RAG execution."""

    history = state.get("history", [])
    context_history = history[:-1] if history and isinstance(history[-1], HumanMessage) else history

    prompt = ChatPromptTemplate.from_messages([
        ("system", build_conversation_prompt()),
        MessagesPlaceholder(variable_name="history", optional=True),
        ("human", "User's Request: {query}")
    ])
    response = await (prompt | chat_llm).ainvoke({"history": context_history, "query": state.get("query", "")})

    return {
        "final_response": response.content,
        "history": [AIMessage(content=response.content)],
        "run_status": "completed",
        "current_clarification_iteration": 0,
        "route": None,
        "need_clarification": False,
        "task_plan": [],
        "execution_guidance": "",
        "mcp_results": [],
        "rag_context": None
    }


async def rag_node(state: MasterState) -> MasterState:
    """Provide temporary RAG branch output for development until the production RAG subgraph is implemented."""

    rag_context = (
        "[RAG_DEMO_ONLY]\n"
        "No actual retrieval was performed. The real RAG subgraph is not implemented yet."
        "This content is not retrieved knowledge and must not be treated as factual evidence."
    )

    return {"rag_context": rag_context}


async def mcp_node(state: MCPState, tools: list) -> MCPState:
    """Determine and dispatch the currently executable MCP objectives by selecting appropriate tools and formulating valid arguments from authoritative request, execution, dependency, and temporal context."""

    execution_history = serialize_working_memory(state.get("working_memory", []))
    task_plan = state.get("task_plan", [])

    prompt = ChatPromptTemplate.from_messages([
        ("system", build_mcp_prompt()),
        ("human", "Current Execution Timestamp:\n{current_timestamp}\n\nOriginal User Query:\n{query}\n\nAuthoritative Task Plan:\n{task_plan}\n\nOrchestrator Execution Guidance:\n{guidance}\n\nExecution History:\n{history}\n\nEvaluator Guidance:\n{feedback}"),
    ])
    response = await (prompt | execution_llm.bind_tools(tools)).ainvoke({"current_timestamp": state.get("timestamp") or current_timestamp(), "query": state.get("query", ""), "task_plan": task_plan, "guidance": state.get("execution_guidance", ""), "history": execution_history, "feedback": state.get("evaluator_reasoning", "")})

    if response.invalid_tool_calls:
        logger.error("MCP Planner produced invalid tool calls | invalid_calls=%s", response.invalid_tool_calls)
        raise RuntimeError("MCP Planner produced invalid tool calls.")

    if not response.tool_calls:
        logger.warning("MCP Planner produced no executable tool call.")

        return {
            "working_memory": [response],
            "evaluation_status": None,
            "evaluator_reasoning": None
        }

    return {
        "working_memory": [response],
        "evaluation_status": None,
        "evaluator_reasoning": None
    }


def mcp_result_collector(state: MCPState) -> MCPState:
    """Collect this MCP invocation's tool results and preserve its error outcome."""
    
    working_memory = state.get("working_memory", [])
    tool_messages = [message for message in working_memory if isinstance(message, ToolMessage)]

    # The subgraph state resets on each invocation; working_memory still accumulates across MCP loop steps within this run.
    tool_error_status = state.get("tool_error_status", False) or any(
        message.status == "error" for message in tool_messages
    )

    return {
        "mcp_results": tool_messages,
        "tool_error_status": tool_error_status,
    }


def tool_error_message(exc: Exception) -> str:
    """Log tool failures internally and return only a safe error summary to the model."""
    logger.error(
        "MCP tool invocation failed inside ToolNode | exception_type=%s | error=%s",
        type(exc).__name__,
        exception_summary(exc),
    )
    return get_error_message(exc)


def create_mcp_tool_node(tools: list) -> ToolNode:
    """Create a bounded-concurrency tool executor that preserves ToolMessage error status."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TOOLS)

    async def bounded_tool_call(request, handler):
        async with semaphore:
            return await handler(request)

    return ToolNode(
        tools,
        name="MCP_ToolExecutor",
        messages_key="working_memory",
        handle_tool_errors=tool_error_message,
        awrap_tool_call=bounded_tool_call,
    )


def get_mcp_result(message: ToolMessage) -> dict | None:
    """Extract structured result from a ToolMessage, if available."""
    if isinstance(message.artifact, dict):
        structured_content = message.artifact.get("structured_content")
        if isinstance(structured_content, dict):
            return structured_content

    content = message.content
    if isinstance(content, str):
        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            return None
        return result if isinstance(result, dict) else None

    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = block.get("text")
            if not isinstance(text, str):
                continue
            try:
                result = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(result, dict):
                return result
    return None


def serialize_working_memory(working_memory: list[BaseMessage]) -> str:
    """Serialize MCP tool results from working memory into a structured text representation for downstream execution and evaluation."""

    tool_messages = [message for message in working_memory if isinstance(message, ToolMessage)]

    if not tool_messages:
        return "No tools have been executed."

    serialized = []

    for message in tool_messages:
        result = get_mcp_result(message)
        if result is not None:
            result_text = json.dumps(result, ensure_ascii=False)
        else:
            result_text = str(message.content)

        serialized.append(f"Tool: {message.name}\n"f"Call ID: {message.tool_call_id}\n"f"Status: {message.status}\n"f"Result:\n{result_text}")
        
    return "\n\n".join(serialized)


async def successful_objective_evaluator(state: MCPState) -> MCPState:
    """Evaluate completed MCP execution results against the planned objectives to determine whether further MCP execution is required and, when necessary, provide evidence-based guidance for the next execution step."""

    task_plan = state.get("task_plan", [])
    execution_history = serialize_working_memory(state.get("working_memory", []))

    prompt = ChatPromptTemplate.from_messages([
        ("system", build_evaluator_prompt()),
        ("human", "User Query: {query}\nTask Plan: {task_plan}\nExecution History:\n{history}")
    ])
    result = await (prompt | evaluator_llm.with_structured_output(ObjectiveCompletionEvaluatorSchema)).ainvoke({"query": state.get("query", ""), "task_plan": task_plan, "history": execution_history})

    return {
        "evaluation_status": result.evaluation_status,
        "evaluator_reasoning": result.reasoning,
    }


async def final_synthesizer(state: MasterState) -> MasterState:
    """Synthesize successful/partial results, preserving an earlier handled graph failure."""

    # A failed node in the MCP subgraph ends that subgraph, but the parent graph may still reach this deferred node. Do not overwrite its safe error response.
    if state.get("run_status") == "failed":
        return {
            "task_plan": [],
            "execution_guidance": "",
            "mcp_results": [],
            "rag_context": None,
            "route": None,
        }

    history = state.get("history", [])
    context_history = history[:-1] if history and isinstance(history[-1], HumanMessage) else history
    task_plan = state.get("task_plan", [])
    execution_history = serialize_working_memory(state.get("mcp_results", []))

    prompt = ChatPromptTemplate.from_messages([
        ("system", build_synthesizer_prompt()),
        MessagesPlaceholder(variable_name="history", optional=True),
        ("human", "Original Query:\n{query}\n\nTask Plan:\n{task_plan}\n\nMCP Results:\n{mcp_results}\n\nRAG Context:\n{rag_context}"),
    ])
    response = await (prompt | synth_llm).ainvoke({"history": context_history, "query": state.get("query", ""), "task_plan": task_plan, "mcp_results": execution_history, "rag_context": state.get("rag_context")})

    return {
        "final_response": response.content,
        "history": [AIMessage(content=response.content)],
        "run_status": "completed",
        "task_plan": [],
        "execution_guidance": "",
        "mcp_results": [],
        "rag_context": None,
        "route": None,
    }
