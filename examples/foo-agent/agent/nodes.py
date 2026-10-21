"""Nodes of the reference agent."""

from __future__ import annotations

import os
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import MessagesState
from langgraph.types import interrupt

DEFAULT_MODEL = "meta-llama/llama-3.1-70b-instruct"
SYSTEM_PROMPT = "You are Foo Agent. Answer briefly. Lorem ipsum dolor sit amet."


def get_llm(config: RunnableConfig) -> BaseChatModel:
    """Create the LLM inside the node (never at import time), as the contract requires."""
    if os.environ.get("FOO_AGENT_FAKE_LLM") == "1":
        from langchain_core.language_models import FakeListChatModel

        return FakeListChatModel(responses=["Lorem ipsum dolor sit amet."])

    from langchain_openai import ChatOpenAI

    model = config.get("configurable", {}).get("model", DEFAULT_MODEL)
    return ChatOpenAI(
        model=model,
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
    )


def respond(state: MessagesState, config: RunnableConfig) -> dict[str, Any]:
    llm = get_llm(config)
    reply = llm.invoke([("system", SYSTEM_PROMPT), *state["messages"]])
    return {"messages": [reply]}


def approve(state: MessagesState) -> dict[str, Any]:
    decision = interrupt(
        {
            "type": "approval",
            "action": "publish_answer",
            "details": {"answer": state["messages"][-1].content},
        }
    )
    text = "Approved." if decision.get("approved") else "Rejected by the user."
    return {"messages": [AIMessage(content=text)]}
