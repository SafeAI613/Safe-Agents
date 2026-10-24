"""Graph nodes: guardrail, answer, approval (HITL)."""

from __future__ import annotations

import os
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import MessagesState
from langgraph.types import interrupt

DEFAULT_MODEL = "meta-llama/llama-3.1-70b-instruct"
SYSTEM_PROMPT = "Lorem ipsum dolor sit amet. Answer only questions about lorem ipsum."


def get_llm(config: RunnableConfig) -> BaseChatModel:
    """Created inside the node, never at import time (the platform loads the graph without keys)."""
    from langchain_openai import ChatOpenAI

    model = config.get("configurable", {}).get("model", DEFAULT_MODEL)
    return ChatOpenAI(
        model=model,
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
        stream_usage=True,  # token counts for the platform's cost display
    )


def guardrail(state: MessagesState) -> dict[str, Any]:
    """Lorem ipsum: block requests outside the agent's task before calling the model."""
    return {}


def respond(state: MessagesState, config: RunnableConfig) -> dict[str, Any]:
    reply = get_llm(config).invoke([("system", SYSTEM_PROMPT), *state["messages"]])
    return {"messages": [reply]}


def approve(state: MessagesState) -> dict[str, Any]:
    """Pause for the user's decision before a sensitive action."""
    decision = interrupt(
        {
            "type": "approval",
            "action": "lorem_action",
            "details": {"answer": state["messages"][-1].text},
        }
    )
    text = "Approved." if decision.get("approved") else "Rejected by the user."
    return {"messages": [AIMessage(content=text)]}
