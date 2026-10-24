import asyncio
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, BaseMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from src.core.config import clarification_llm, chat_llm, evaluator_llm, execution_llm, orchestrator_llm, synth_llm
from src.graph.prompts import (
    build_conversation_prompt,
    build_clarifier_prompt,
    build_evaluator_prompt,
    build_mcp_prompt,
    build_orchestrator_prompt,
    build_synthesizer_prompt,
)
from src.graph.state import MCPState, MasterState
from src.planner.schemas import ObjectiveCompletionEvaluatorSchema, OrchestratorOutput, RewrittenQueryOutput

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

    return {
        "need_clarification": response.need_clarification,
        "clarification_reason": response.clarification_reason,
        "task_plan": response.tasks,
        "execution_guidance": response.execution_guidance,
        "route": route,
        "final_response": None,
        "user_clarification": None,
        "user_clarification_intent_type": None,
        "mcp_results": [],
        "rag_context": None,
        "current_clarification_iteration": state.get("current_clarification_iteration", 0)
    }


async def ambiguity_clarifier(state: MasterState) -> MasterState:
    """Resolve genuine ambiguity by obtaining clarification from the user, interpreting the response in the context of the original request, and updating the query based on whether the user continues, changes, or cancels the request."""

    reason = state.get("clarification_reason", "")
    human_response = interrupt(f"I need a bit more context: {reason}\nPlease clarify your request.")

    prompt = ChatPromptTemplate.from_messages([
        ("system", build_clarifier_prompt()),
        ("human", "Original Query: {original_query}\nReason: {reason}\nUser Response: {response}"),
    ])
    chain = prompt | clarification_llm.with_structured_output(RewrittenQueryOutput, method="json_schema")
    result = await chain.ainvoke({"original_query": state.get("query", ""), "reason": reason, "response": human_response})

    new_state = {
        "query": result.rewritten_query,
        "history": [
            AIMessage(content=f"I need a bit more context: {reason}."),
            HumanMessage(content=human_response)],
        "need_clarification": False,
        "clarification_reason": None,
        "user_clarification": human_response,
        "user_clarification_intent_type": result.intent_type,
        "final_response": None,
        "task_plan": [],
        "execution_guidance": "",
        "route": None,
        "mcp_results": [],
        "rag_context": None,
        "timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S")
    }

    if result.intent_type == "merge":
        new_state["current_clarification_iteration"] = (state.get("current_clarification_iteration", 0) + 1)
    else:
        new_state["current_clarification_iteration"] = 0

    return new_state


async def cancel_node(state: MasterState) -> MasterState:
    """Finalize a user-cancelled request by recording the cancellation response and resetting the active execution and clarification state."""

    cancel_response = "Task cancelled. Let me know if there's anything else you'd like to do!"

    return {
        "final_response": cancel_response,
        "history": [AIMessage(content=cancel_response)],
        "need_clarification": False,
        "clarification_reason": None,
        "current_clarification_iteration": 0,
        "route": None,
        "task_plan": [],
        "execution_guidance": "",
        "user_clarification": None,
        "user_clarification_intent_type": None,
        "mcp_results": [],
        "rag_context": None
    }


async def clarification_limit_node(state: MasterState) -> MasterState:
    """Terminate the clarification flow when the maximum number of clarification attempts is reached and reset the active execution and clarification state."""

    clarification_limit_response = (
        "I'm unable to complete this request because the required information wasn't provided after multiple clarification attempts."
        "Let's start fresh. Please enter your request again with the required details."
    )

    return {
        "final_response": clarification_limit_response,
        "history": [AIMessage(content=clarification_limit_response)],
        "need_clarification": False,
        "clarification_reason": None,
        "current_clarification_iteration": 0,
        "route": None,
        "task_plan": [],
        "execution_guidance": "",
        "user_clarification": None,
        "user_clarification_intent_type": None,
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
        "current_clarification_iteration": 0,
        "route": None,
        "need_clarification": False,
        "task_plan": [],
        "execution_guidance": "",
        "user_clarification": None,
        "user_clarification_intent_type": None,
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
        logger.error("MCP Planner produced invalid tool calls: %s\n", response.invalid_tool_calls)
        raise RuntimeError("MCP Planner produced invalid tool calls.\n")

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
    """Collects all ToolMessage results from the working memory and stores them in mcp_results."""

    tool_messages = [message for message in state.get("working_memory", []) if isinstance(message, ToolMessage)]

    return {
        "mcp_results": tool_messages,
    }


def create_mcp_tool_node(tools: list) -> ToolNode:
    """Create an MCP tool executor with bounded concurrency to safely execute available tools in parallel."""

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TOOLS)

    async def bounded_tool_call(request, handler):
        async with semaphore:
            return await handler(request)

    return ToolNode(tools, name="MCP_ToolExecutor", messages_key="working_memory", handle_tool_errors=True, awrap_tool_call=bounded_tool_call)


def get_mcp_result(message: ToolMessage) -> dict | None:
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
    """Synthesize the completed execution results, relevant conversation context, and available knowledge into a single accurate, coherent, non-duplicative final user-facing response."""

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
        "task_plan": [],
        "execution_guidance": "",
        "mcp_results": [],
        "rag_context": None,
    }
