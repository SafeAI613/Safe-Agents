# Foo Agent (reference)

Minimal agent that follows [the agent contract](../../docs/AGENT_CONTRACT.md):
`respond` calls the LLM through OpenRouter, `approve` pauses with `interrupt()` for user approval.

Set `FOO_AGENT_FAKE_LLM=1` to replace the LLM with a fixed fake answer (used by integration tests,
no API key needed).
