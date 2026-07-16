# 0001 — Event-driven ingestion over synchronous REST

**Status:** Accepted

## Context

Claims arrive as a high-volume stream. Processing per claim (validation, scoring,
aggregation) is independent across claims and does not need to complete within the
time of any single request. The system's stated design pressure is that volume grows
over time and processing must absorb bursts without dropping work.

A synchronous `POST /claims` that validates and scores inline couples ingestion rate
to processing rate: a burst either blocks producers or is shed. It also makes each
processing stage a hard dependency in the request path, so one slow stage stalls
intake.

## Decision

Ingestion is asynchronous. Producers publish claim events to a topic and return
immediately. Processing happens in workers that consume from queues, decoupled from
the rate of arrival. Buffering lives in the queues; work is durable until a consumer
acknowledges it.

## Consequences

Producers and consumers scale independently, and a burst becomes queue depth rather
than dropped work or backpressure on the producer. In exchange we take on the
genuine costs of asynchronous systems: at-least-once delivery (hence idempotency,
ADR-0007), eventual consistency between ingestion and the ranking, and the need for
explicit dead-letter handling. The rest of the architecture is shaped by paying these
costs deliberately rather than avoiding them.
