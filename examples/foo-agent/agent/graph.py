"""LangGraph workflow of the reference agent: respond -> approve (HITL)."""

from langgraph.graph import END, START, MessagesState, StateGraph

from agent.nodes import approve, respond

builder = StateGraph(MessagesState)
builder.add_node("respond", respond)
builder.add_node("approve", approve)
builder.add_edge(START, "respond")
builder.add_edge("respond", "approve")
builder.add_edge("approve", END)

graph = builder.compile()
