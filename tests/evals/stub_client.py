"""Stub Anthropic clients for Tier 3 harness tests (docs/EVAL_PLAN.md).

`tests/explanation/stub_client.py`'s `StubAnthropicClient` already covers the
single-fixed-response case and is reused directly where that's enough (e.g.
judge unit tests). `SequencedStubAnthropicClient` below additionally lets a
test hand the runner a different canned response on each successive call --
needed to exercise `run_eval_set`'s aggregation/clustering logic against a
mix of faithful and unfaithful verdicts without a live model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tests.explanation.stub_client import _StubResponse, _StubTextBlock


class _SequencedMessages:
    def __init__(self, responses: list[tuple[str, str | None]]) -> None:
        self._responses = responses
        self._calls = 0

    def create(self, **kwargs: Any) -> _StubResponse:
        text, stop_reason = self._responses[self._calls % len(self._responses)]
        self._calls += 1
        return _StubResponse(content=[_StubTextBlock(text=text)], stop_reason=stop_reason)


@dataclass
class SequencedStubAnthropicClient:
    """Replays `responses` (text, stop_reason) pairs in order, cycling if
    exhausted -- one call per `.messages.create(...)` invocation.
    """

    responses: list[tuple[str, str | None]]
    messages: _SequencedMessages = field(init=False)

    def __post_init__(self) -> None:
        self.messages = _SequencedMessages(self.responses)
