import asyncio
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.types import Command

from src.core.config import client_config
from src.core.errors import get_error_message
from src.graph.workflow import build_graph

import logging
logger = logging.getLogger(__name__)

def create_request_state(user_input: str, timestamp: str) -> dict:
    return {
        "history": [HumanMessage(content=user_input)],
        "query": user_input,
        "timestamp": timestamp,
        "final_response": None,
        "task_plan": [],
        "execution_guidance": "",
        "need_clarification": False,
        "clarification_reason": None,
        "user_clarification": None,
        "user_clarification_intent_type": None,
        "current_clarification_iteration": 0,
        "max_clarification_iterations": 3,
        "route": None,
        "mcp_results": [],
        "rag_context": None,
    }

async def run_agent():
    mcp_client = MultiServerMCPClient(client_config, tool_name_prefix=True)

    tools = await mcp_client.get_tools()
    agent_graph = build_graph(tools)

    thread_config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    print("\nStarting system and initializing MCP Servers. Please wait...")
    print("System ready. Type 'exit' or 'quit' to stop.")
    print("Write the query precisely and clearly with its approximate required args.")
    while True:
        try:
            current_state = await agent_graph.aget_state(thread_config)

            if current_state.tasks and current_state.tasks[0].interrupts:
                interrupt_payload = current_state.tasks[0].interrupts[0].value
                print(f"\nAgent: {interrupt_payload}")

                user_input = await asyncio.to_thread(input, "\nYou: ")
                user_input = user_input.strip()
                print()
                if user_input.lower() in ["exit", "quit"]:
                    break
                if not user_input:
                    continue

                async for event in agent_graph.astream(Command(resume=user_input), config=thread_config, stream_mode="updates", subgraphs=True, version="v2"):
                    if event["type"] == "updates":
                        print(event["data"])
                        print()
            else:
                user_input = await asyncio.to_thread(input, "\nYou: ")
                user_input = user_input.strip()
                print()
                if user_input.lower() in ["exit", "quit"]:
                    break
                if not user_input:
                    continue

                timestamp = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S")
                initial_state = create_request_state(user_input, timestamp)
                async for event in agent_graph.astream(initial_state, config=thread_config, stream_mode="updates", subgraphs=True, version="v2"):
                    if event["type"] == "updates":
                        print(event["data"])
                        print()

            post_state = await agent_graph.aget_state(thread_config)
            if post_state.tasks and post_state.tasks[0].interrupts:
                continue

            final_response = post_state.values.get("final_response")
            if final_response is not None:
                print(f"Agent: {final_response}")
            else:
                print("Agent: I'm sorry, I couldn't generate a response.\n")

        except Exception as exc:
            logger.error("Unhandled agent error | %s: %s", type(exc).__name__, str(exc).strip() or repr(exc))
            error_response = get_error_message(exc)
            try:
                await agent_graph.aupdate_state(
                    thread_config,
                    {
                        "final_response": error_response,
                        "history": [AIMessage(content=error_response)]
                    }
                )
            except Exception as state_exc:
                logger.error("Failed to update graph state after agent error | %s: %s", type(state_exc).__name__, str(state_exc).strip() or repr(state_exc))
            print(f"\nAgent: {error_response}")
            continue

if __name__ == "__main__":
    asyncio.run(run_agent())
