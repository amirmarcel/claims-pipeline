"""A stub Anthropic client for Tier 2 guardrail tests (docs/EVAL_PLAN.md).

Wraps a recorded response so the real prompt-construction and
response-parsing code in `claims_pipeline.explanation.client` runs
unchanged, without ever calling the network. This is what lets the
deterministic guardrail assertions run keyless in CI and block merge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class _StubTextBlock:
    text: str
    type: str = "text"


@dataclass
class _StubResponse:
    content: list[_StubTextBlock]
    stop_reason: str | None = "end_turn"


class _StubMessages:
    def __init__(self, response_text: str, stop_reason: str | None) -> None:
        self._response_text = response_text
        self._stop_reason = stop_reason
        self.last_request: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> _StubResponse:
        self.last_request = kwargs
        return _StubResponse(
            content=[_StubTextBlock(text=self._response_text)], stop_reason=self._stop_reason
        )


@dataclass
class StubAnthropicClient:
    """Satisfies `claims_pipeline.explanation.client.AnthropicClientLike`
    by replaying a fixed, recorded response text instead of calling the API.
    """

    response_text: str
    stop_reason: str | None = "end_turn"
    messages: _StubMessages = field(init=False)

    def __post_init__(self) -> None:
        self.messages = _StubMessages(self.response_text, self.stop_reason)
