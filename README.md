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
it goes right. Two failure classes exist, and they are kept conceptually and
mechanically distinct (`docs/SPEC.md` §5, ADR-0007, ADR-0010):

- **Business-invalid.** A claim decodes fine but fails an `events.validate` rule
  (e.g. `allowed_amount > billed_amount`). The validation worker routes it directly
  to `validation-dlq` with a structured reason it authors itself — this is not a
  redrive, and it's never scored.
- **Poison / processing failure.** A message that fails to even decode, or that
  raises unexpectedly mid-process (a bug, a transient downstream outage). This is
  where SQS's own redrive policy does the work:

  1. A worker fails on a message and does **not** delete it (ack discipline, not a
     hand-rolled retry loop).
  2. The message's visibility timeout expires and SQS redelivers it.
  3. Redelivery repeats up to `maxReceiveCount = 3` (ADR-0010).
  4. On exceeding that count, SQS itself redrives the message to the matching
     dead-letter queue — `validation-q` → `validation-dlq`, `scoring-q` →
     `scoring-dlq` — with no application-level backoff involved.
  5. An operator inspects the dead-letter queue (`python -m claims_pipeline.replay
     --dlq <name> --dry-run`) and, once the cause is addressed, replays the message
     back onto the source queue (`python -m claims_pipeline.replay --dlq <name>
     --source-queue <queue>`).
  6. Because every consumer is **idempotent on `claim_id`**, replay cannot
     double-count a claim into a provider's aggregate — reprocessing converges to
     the same state. (A genuinely undecodable body has no corrected form to
     replay byte-for-byte; the dry-run inspection is what surfaces it for a human
     to address at the source, not a guarantee that replay alone repairs it.)

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
- [ADR-0009](docs/adr/0009-raw-sql-schema-no-migration-framework.md) — Raw SQL schema, no migration framework (yet)
- [ADR-0010](docs/adr/0010-sqs-native-redrive-and-visibility-backoff.md) — SQS-native redrive policy; visibility timeout as the only backoff
- [ADR-0011](docs/adr/0011-fastapi-ranking-api-and-two-layer-guardrail-tests.md) — FastAPI for the ranking API; two-layer Tier 2 guardrail tests

## API surface

Served by FastAPI (`claims_pipeline.api.app:app`, run locally with `uvicorn
claims_pipeline.api.app:app --reload`):

- `GET /providers/ranking?limit=<n>` — the deterministic ranking, a pure read over
  `provider_scores`. Never touches the language model (ADR-0003) — enforced by a
  test that fails if the ranking router ever constructs an Anthropic client.
- `GET /providers/{provider_id}` — single-provider detail: score, sub-signals, rank.
- `GET /providers/{provider_id}/explanation` — the only endpoint that calls the
  model. Assembles a fixed `grounded_facts` envelope (score, sub-signals, rank,
  neighbors) from the same deterministic read, then asks the model to describe it in
  prose. The model never sees anything beyond that envelope, and any claim/provider
  -derived text inside it is treated as untrusted data, not instructions (ADR-0003).

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
infra/local/         # docker-compose rig (LocalStack + Postgres) and provisioning script
src/claims_pipeline/
  events.py          # the claim event contract (SPEC.md §1)
  generator/         # deterministic synthetic claim load generator (SPEC.md §6)
  scoring.py         # the pure scoring/ranking core (SPEC.md §3, ADR-0003)
  db/                # Postgres persistence: schema.sql, repository.py (ADR-0007, ADR-0009)
  workers/           # validation and scoring workers; ack discipline (SPEC.md §2, §5, ADR-0010)
  replay/            # dead-letter inspection and replay CLI (SPEC.md §5, ADR-0007)
  api/               # FastAPI ranking API: routers, dependencies, schemas (SPEC.md §4, ADR-0011)
  explanation/        # the confined explanation layer -- the only model call (SPEC.md §4, ADR-0003)
tests/               # unit tests, golden-seed scoring tests, and skippable
                     # LocalStack/Postgres integration tests
```

## Status

Local rig in place: the claim event contract, the SNS→SQS provisioning, and a v1
load generator (rate/count/provider distribution/outcome mix/seed) run end-to-end
against LocalStack. The deterministic scoring core, Postgres persistence, and the
validation and scoring workers are built and idempotent on `claim_id`. The
reliability layer is in place: SQS-native redrive policies (`maxReceiveCount=3`,
ADR-0010), correct worker ack discipline, the generator's `failure_injection` knob
(invalid-but-parseable / malformed / duplicate), and the `python -m
claims_pipeline.replay` dead-letter inspection/replay CLI, all exercised end-to-end
against real LocalStack + Postgres. The ranking API and confined explanation
endpoint are built and covered by Tier 1 golden-seed ordering tests and Tier 2
guardrail tests (groundedness, injection resistance, non-empty success, ranking
purity — ADR-0011); Tier 3 faithfulness evals, EKS/KEDA autoscaling, and the
generator's burst knob are built in subsequent milestones.
