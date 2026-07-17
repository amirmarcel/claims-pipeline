# 0010 — SQS-native redrive policy; visibility timeout as the only backoff

**Status:** Accepted

## Context

SPEC.md §5 requires that a message failing processing repeatedly is redriven to a
dead-letter queue after a bounded number of receives, with structured context, and
that this never means a message is silently dropped (AGENTS.md non-negotiable #4).
Two questions were open:

1. What bounds "repeatedly" — the `maxReceiveCount` on the redrive policy?
2. When a downstream dependency is transiently unavailable (e.g. Postgres briefly
   down for the scoring worker), does the worker retry in-process with its own
   backoff, or rely purely on SQS's redelivery cycle?

## Decision

**`maxReceiveCount = 3`** on both `validation-q` and `scoring-q`. Three attempts is
enough to absorb a genuinely transient blip (a dropped connection, a momentary
Postgres restart) without holding a poison message in normal circulation for long;
it also keeps the reliability tests fast, since redriving in a test only requires
failing a message three times rather than five.

**Backoff is the SQS visibility timeout, full stop — no application-level retry
loop.** A worker that fails to process a message does not retry it in-process; it
simply does not delete/ack it. The message becomes visible again after the queue's
visibility timeout elapses, `ApproximateReceiveCount` increments, and SQS itself
redrives the message to the dead-letter queue once `maxReceiveCount` is exceeded.
This is true for both the poison-payload case and the transient-downstream-failure
case — they are handled by the same mechanism, because from the worker's point of
view both are just "processing this message failed." Reliability lives in the queue
configuration and in correct ack discipline, not in hand-rolled retry code (this
mirrors the existing stance, ADR-0007, that correctness lives in the persistence
layer's upsert semantics rather than in defensive worker logic).

**Two DLQ purposes stay on two queues.** `validation-dlq` (Session 2) already
carries a specific meaning: business-invalid claims, explicitly routed there by the
validation worker with a structured `reason` field it authored itself. Poison
messages on `validation-q` — bodies that don't even decode — are redriven to that
same `validation-dlq` by SQS's redrive policy, not by worker code, so they arrive
without a `reason` field; the replay utility's dry-run mode distinguishes the two by
shape (a decodable `{"claim": ..., "reason": ...}` record is business-invalid;
anything else is poison, and the utility derives a reason by attempting to
decode/validate it). `scoring-q` gets a new, dedicated `scoring-dlq`: the scoring
worker does no business validation of its own (that already happened upstream), so
everything that lands there is by definition a processing failure — a distinct
purpose from `validation-dlq`, kept on a distinct queue rather than conflated.

## Consequences

Worker code stays a thin I/O shell: on success, delete the message; on any failure,
log structured context (message id, receive count, reason) and return without
deleting. No retry-count bookkeeping, no sleep/backoff timers, no second "attempts"
concept living in application code alongside SQS's own. The cost is coarser control
over backoff timing than an application-level exponential backoff would give —
if Postgres has an extended outage, three receives (bounded by the visibility
timeout) may not be enough headroom, and those messages will be redriven to
`scoring-dlq` and need a manual replay once the outage is resolved. That is judged
an acceptable trade for this system's scale: replay is a first-class, safe operation
(ADR-0007), so a batch of transiently-failed messages ending up in the DLQ during an
outage is recoverable, not lost work. If extended-outage tolerance becomes a real
requirement, application-level backoff is a future, explicit decision to make — not
a default to reach for now.
