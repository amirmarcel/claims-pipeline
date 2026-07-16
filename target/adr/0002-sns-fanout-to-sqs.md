# 0002 — SNS topic fan-out to SQS

**Status:** Accepted

## Context

Producers publish claim events; the validation stage consumes them. The naive option
is to publish directly to a single queue. But the moment a second consumer is needed
(an audit sink, an analytics tap, a future second processing lane), a
producer-to-single-queue design forces the producer to know about every consumer.

The alternatives considered:

| Option | Fan-out to many consumers | Ordering / replayable log | Operational weight | Verdict |
|--------|---------------------------|---------------------------|--------------------|---------|
| Direct-to-SQS | No — producer owns the coupling | No | Lowest | Rejected: adding a consumer becomes a producer change |
| **SNS → SQS** | **Yes, via topic subscription** | **No** | **Low (managed)** | **Chosen** |
| Log-based broker (Kafka / Kinesis) | Yes, via consumer groups | Yes | High (brokers/shards, consumer-group mgmt) | Rejected for now: pays for ordering/replay this system doesn't need |

**Why not Kafka (the question this ADR exists to answer).** A log-based broker is the
right tool when you need a replayable, ordered event log consumed by many independent
consumer groups at high sustained throughput. This system needs fan-out and durable
per-consumer buffering — both of which SNS→SQS provides as fully managed services —
but it does *not* need arbitrary-offset replay or strict cross-partition ordering. At
that requirement profile, Kafka/Kinesis would add broker/shard operations and
consumer-group management whose cost buys capabilities we wouldn't use. Choosing the
lighter managed option here is the appropriate tradeoff, not a limitation; the day a
requirement genuinely needs an ordered replayable log, that is a deliberate reversal
of this ADR, recorded as a new one.

## Decision

Producers publish to an SNS topic (`claims.raw`). The topic fans out to an SQS queue
(`validation.q`) for the processing lane. New consumers subscribe to the topic
without any change to the producer. SQS provides the per-consumer buffer,
visibility-timeout redelivery, and native dead-letter support the design relies on.

## Consequences

The topic is the extension seam: additional consumers attach to it independently.
Each consumer gets its own queue, its own backlog, and its own failure isolation — a
slow or failing consumer does not affect the others. We accept that this topology is
not a replayable ordered log; if a future requirement needs event replay from an
arbitrary offset or strict ordering, that is a new decision (a log-based broker) and
a new ADR, not a stretch of this one.
