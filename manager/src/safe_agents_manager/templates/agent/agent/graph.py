"""LangGraph workflow: guardrail -> respond -> approve."""

from langgraph.graph import END, START, MessagesState, StateGraph

from agent.nodes import approve, guardrail, respond

builder = StateGraph(MessagesState)
builder.add_node("guardrail", guardrail)
builder.add_node("respond", respond)
builder.add_node("approve", approve)
builder.add_edge(START, "guardrail")
builder.add_edge("guardrail", "respond")
builder.add_edge("respond", "approve")
builder.add_edge("approve", END)

# The platform server adds its own checkpointer; the CLI compiles with an in-memory one.
graph = builder.compile()
