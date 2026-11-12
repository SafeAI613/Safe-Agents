"""Fake chat model for tests (FOO_AGENT_FAKE_LLM=1). Not needed in a real agent."""

from __future__ import annotations

from collections.abc import Iterator
from itertools import cycle
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult

FAKE_ANSWER = "Lorem ipsum dolor sit amet."
# "repeat: <text>" makes the fake model answer <text> (platform tests of the content guard).
REPEAT_PREFIX = "repeat:"
FAKE_USAGE = {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150}


class FakeChatModelWithUsage(GenericFakeChatModel):
    """Reports token usage like a real provider (on the last chunk when streaming)."""

    model_name: str = "foo/bar-model"

    def _add_usage(self, message: BaseMessage) -> None:
        message.usage_metadata = dict(FAKE_USAGE)  # type: ignore[attr-defined]
        message.response_metadata = {"model_name": self.model_name}

    def _answer_for(self, messages: list[BaseMessage]) -> None:
        last = messages[-1].content if messages else ""
        text = last if isinstance(last, str) else ""
        answer = text.removeprefix(REPEAT_PREFIX).strip() if text.startswith(REPEAT_PREFIX) else None
        self.messages = iter([AIMessage(content=answer or FAKE_ANSWER)])

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> ChatResult:
        self._answer_for(messages)
        result = super()._generate(messages, *args, **kwargs)
        self._add_usage(result.generations[0].message)
        return result

    def _stream(
        self, messages: list[BaseMessage], *args: Any, **kwargs: Any
    ) -> Iterator[ChatGenerationChunk]:
        self._answer_for(messages)
        chunks = list(super()._stream(messages, *args, **kwargs))
        for index, chunk in enumerate(chunks):
            if index == len(chunks) - 1:
                self._add_usage(chunk.message)
            yield chunk


def fake_llm(model_name: str) -> FakeChatModelWithUsage:
    return FakeChatModelWithUsage(
        messages=cycle([AIMessage(content=FAKE_ANSWER)]), model_name=model_name
    )
