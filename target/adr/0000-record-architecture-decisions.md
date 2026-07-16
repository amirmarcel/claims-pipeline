# 0000 — Record architecture decisions

**Status:** Accepted

## Context

The architecturally significant choices in this system (event-driven ingestion, the
deterministic/model boundary, autoscaling strategy, data-handling posture) are worth
capturing at the point of decision, with their tradeoffs, so a reader can reconstruct
*why* rather than only *what*.

## Decision

We record architecturally significant decisions as numbered ADRs in `docs/adr/`. Each
ADR states its status, the context that forced a choice, the decision, and the
consequences we accept. An ADR is immutable once accepted; a later decision that
reverses it is a new ADR that supersedes it.

## Consequences

The reasoning behind the system is legible and reviewable. The cost is the discipline
of writing an ADR when a decision is genuinely architectural — not for every routine
implementation choice.
