"""Dead-letter inspection and replay (SPEC.md §5).

Replay is safe *because* consumers are idempotent on `claim_id` (ADR-0007):
re-driving a message that was actually processed before, or a duplicate of
one still in flight, cannot double-count a claim into a provider's aggregate.
"""
