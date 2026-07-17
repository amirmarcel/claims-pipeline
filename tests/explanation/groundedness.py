"""Shared number-extraction helpers for the Tier 2 groundedness guardrail
(docs/EVAL_PLAN.md), used by both the stubbed and live guardrail tests.
"""

from __future__ import annotations

import re

# Provider IDs look like "P-001" (SPEC.md §1) -- strip them before scanning
# for numbers, or the hyphen plus digits reads as a spurious negative number
# (e.g. "P-001" would otherwise parse as -1).
_IDENTIFIER_RE = re.compile(r"\b[A-Za-z]+-\d+\b")
_NUMBER_RE = re.compile(r"-?\d+\.?\d*")


def numbers_in_facts(value: object) -> set[float]:
    """Recursively collect every numeric leaf in a grounded_facts tree."""
    found: set[float] = set()
    if isinstance(value, bool):
        return found
    if isinstance(value, int | float):
        found.add(float(value))
    elif isinstance(value, dict):
        for v in value.values():
            found |= numbers_in_facts(v)
    elif isinstance(value, list):
        for v in value:
            found |= numbers_in_facts(v)
    return found


def strip_identifiers(text: str) -> str:
    """Blank out provider-id-shaped tokens (e.g. "P-002") so a hyphenated ID
    isn't misread as a signed number, or its digits misattributed to a
    nearby word like "rank".
    """
    return _IDENTIFIER_RE.sub(" ", text)


def numbers_in_text(text: str) -> list[float]:
    return [float(m) for m in _NUMBER_RE.findall(strip_identifiers(text))]
