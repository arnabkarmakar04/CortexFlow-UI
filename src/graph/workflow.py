from functools import partial

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.core.errors import graph_error_handler
from src.graph.nodes import ambiguity_clarifier, cancel_node, clarification_limit_node, mcp_result_collector, conversation_node, create_mcp_tool_node, mcp_node, successful_objective_evaluator, orchestrator, rag_node, final_synthesizer
from src.graph.router import route_after_clarifier, route_after_evaluator, route_after_mcp, route_after_orchestrator, route_after_tool_executor
from src.graph.state import MCPState, MasterState


def build_graph(tools: list):

    memory = InMemorySaver()
    mcp_tool_node = create_mcp_tool_node(tools)

    mcp_subgraph = StateGraph(MCPState)
    mcp_subgraph.add_node("MCP_Node", partial(mcp_node, tools=tools))
    mcp_subgraph.add_node("ToolExecutor", mcp_tool_node)
    mcp_subgraph.add_node("Evaluator", successful_objective_evaluator)
    mcp_subgraph.add_node("MCPCollector", mcp_result_collector)

    mcp_subgraph.add_edge(START, "MCP_Node")
    mcp_subgraph.add_conditional_edges(
        "MCP_Node",
        route_after_mcp,
        {
            "ToolExecutor": "ToolExecutor",
            "MCPCollector": "MCPCollector"
        }
    )
    mcp_subgraph.add_conditional_edges(
        "ToolExecutor",
        route_after_tool_executor,
        {
            "Evaluator": "Evaluator",
            "MCPCollector": "MCPCollector"
        }
    )
    mcp_subgraph.add_conditional_edges(
        "Evaluator",
        route_after_evaluator,
        {
            "MCP_Node": "MCP_Node",
            "MCPCollector": "MCPCollector"
        }
    )
    mcp_subgraph.add_edge("MCPCollector", END)
    mcp_app = mcp_subgraph.compile()

    workflow = StateGraph(MasterState)
    workflow.set_node_defaults(error_handler=graph_error_handler)

    workflow.add_node("Orchestrator", orchestrator)
    workflow.add_node("Clarifier", ambiguity_clarifier)
    workflow.add_node("CancelNode", cancel_node)
    workflow.add_node("ClarificationLimit", clarification_limit_node)
    workflow.add_node("MCP_Subgraph", mcp_app)
    workflow.add_node("RAG", rag_node)
    workflow.add_node("Conversation", conversation_node)
    workflow.add_node("final_Synthesizer", final_synthesizer, defer=True)

    workflow.add_edge(START, "Orchestrator")
    workflow.add_conditional_edges("Orchestrator", route_after_orchestrator)
    workflow.add_conditional_edges(
        "Clarifier",
        route_after_clarifier,
        {
            "Orchestrator": "Orchestrator",
            "CancelNode": "CancelNode"
        }
    )
    workflow.add_edge("MCP_Subgraph", "final_Synthesizer")
    workflow.add_edge("RAG", "final_Synthesizer")
    workflow.add_edge("final_Synthesizer", END)
    workflow.add_edge("CancelNode", END)
    workflow.add_edge("ClarificationLimit", END)
    workflow.add_edge("Conversation", END)

    return workflow.compile(checkpointer=memory)