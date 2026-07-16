# Event-Driven Claims Processing Platform

*(repo: `claims-pipeline`)*

An event-driven platform that ingests a high-volume stream of synthetic **claim
events**, validates and scores them through a fan-out of asynchronous workers, and
serves a deterministic **provider ranking** over the results. Workers autoscale on
queue depth, unprocessable messages are dead-lettered and replayable, and every
consumer is idempotent so redelivery and replay are safe.

This repository demonstrates scalable event-driven architecture, not healthcare
expertise. Synthetic claims are used solely to exercise distributed-systems concerns:
asynchronous processing, fan-out, queue-depth autoscaling, idempotency, retries and
dead-letter recovery, observability, and deterministic business logic. The scoring
rule is deliberately simple so it can be verified by hand and used as a test oracle.
A language model appears only as a carefully constrained component at one endpoint —
it explains a ranking, it never computes one (ADR-0003).

## Architecture

```
        synthetic claim events
                 │
                 ▼
          SNS topic  (claims-raw)
                 │  fan-out
                 ▼                                    ┌─────────────────┐
          SQS (validation-q) ───► Validation worker   │  KEDA           │
                 │                       │ valid       │    ▲            │
   invalid ──────┘                       ▼             │    │ scales on  │
   ▼                              SQS (scoring-q) ─────►│  queue depth   │
 SQS (validation-dlq)                    │             └─────────────────┘
                                         ▼
                                   Scoring worker ──► PostgreSQL
                                                       (claim_scores,
                                                        provider_scores)
                                                          │
                                                          ▼
                                                    Ranking API
                                                     ├─ GET /providers/ranking
                                                     └─ GET /providers/{id}/explanation
                                                            (confined LLM)
```

Runs locally end-to-end on LocalStack + Postgres via docker-compose; deploys to EKS
with KEDA scaling workers on SQS queue depth.

## Failure scenario

The system is designed around what happens when processing goes wrong, not only when
it goes right. The end-to-end recovery path (specified in `docs/SPEC.md` §5 and
ADR-0007):

1. A worker fails on a message (crash, or an unprocessable/poison payload).
2. The message's visibility timeout expires and SQS redelivers it.
3. Redelivery repeats up to a bounded receive count.
4. On exceeding that count, the message is redriven to the dead-letter queue with
   structured context (the reason, the source queue, the receive count).
5. An operator inspects the dead-letter queue and, once the cause is addressed,
   replays the message back onto the source queue with the replay utility.
6. Because every consumer is **idempotent on `claim_id`**, replay cannot double-count
   a claim into a provider's aggregate — reprocessing converges to the same state.

This is why idempotency is a hard invariant rather than a nice-to-have: it is what
makes dead-letter replay safe.

## Design stance

- **Deterministic spine, confined model.** The ranking is a pure function of claim
  data. The language model explains the ranking against a fixed set of grounded
  facts and is never in the path that decides order. See ADR-0003.
- **Correctness lives in tests; the model boundary lives in evals.** Scoring,
  ranking, idempotency, and dead-letter behavior are asserted as tests. Explanation
  faithfulness is measured as an eval. See `docs/EVAL_PLAN.md`.
- **Synthetic data only.** No real protected health information ever enters the
  system. Claim events are generated. De-identification is treated as a design
  concern, not a machine-learning problem to be graded. See ADR-0005.

## Engineering decisions

The architecturally significant choices are recorded as ADRs in `docs/adr/`:

- [ADR-0001](docs/adr/0001-event-driven-ingestion.md) — Event-driven ingestion over synchronous REST
- [ADR-0002](docs/adr/0002-sns-fanout-to-sqs.md) — SNS topic fan-out to SQS (and why not Kafka)
- [ADR-0003](docs/adr/0003-deterministic-scoring-confined-llm.md) — Deterministic scoring; language model confined to explanation
- [ADR-0004](docs/adr/0004-keda-queue-depth-autoscaling.md) — KEDA queue-depth autoscaling over CPU-based HPA
- [ADR-0005](docs/adr/0005-synthetic-data-and-de-identification.md) — Synthetic data only; de-identification as a design concern
- [ADR-0006](docs/adr/0006-python-for-workers-and-api.md) — Python for workers and the API
- [ADR-0007](docs/adr/0007-idempotent-consumers.md) — Idempotent consumers via `claim_id`
- [ADR-0008](docs/adr/0008-local-first-then-eks.md) — Local-first on LocalStack, then EKS

## Scope

Two processing stages (validate, score), not four. The system does not attempt
clinical accuracy, real procedure-code semantics, or reimbursement logic — those
would trade distributed-systems signal for domain surface area (ADR-0005).

Deliberately out of scope for this build, and why each is a *later* decision rather
than a missing feature:

- **Additional processing lanes** (enrichment, audit) — the SNS topic is the seam
  that makes them additive; not building them keeps the pipeline legible (ADR-0002).
- **A replayable ordered log (Kafka/Kinesis)** — appropriate at a throughput and
  ordering profile this system doesn't have; adopting it would be ADR-0002 reversed,
  a new decision (ADR-0002).
- **Production PHI de-identification** — a dedicated component with its own accuracy
  evals; deliberately not staked on here (ADR-0005).
- **Orchestration/transformation tooling and streaming analytics** — downstream of a
  correct pipeline, not part of proving one.

## Repository layout

```
docs/
  SPEC.md            # event contract, pipeline stages, scoring contract, API contract, load generator
  EVAL_PLAN.md       # tests-vs-evals split, the improvement loop, CI gates
  adr/               # architecture decision records
AGENTS.md            # build conventions and the human/agent division of labor
benchmark/
  golden.seed.jsonl  # hand-verified claim → score cases (the oracle seed)
```

## Status

Specification phase. Contracts and decision records are in place; runtime code is
built against them in subsequent milestones.
