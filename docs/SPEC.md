# Specification

This document is the contract the implementation is built against. Anything not
pinned here is an implementation detail; anything pinned here is a promise that the
tests enforce.

## 1. Claim event contract

The unit of work is a **claim event**: a single line of synthetic claims data
published to the ingestion topic. Fields:

| field            | type    | notes                                                        |
|------------------|---------|--------------------------------------------------------------|
| `claim_id`       | string  | **Idempotency key.** Globally unique. `clm_` prefix.         |
| `provider_id`    | string  | `P-###`. The entity being scored and ranked.                 |
| `specialty`      | string  | Small enum (e.g. `cardiology`, `orthopedics`, `primary`).    |
| `procedure_code` | string  | Synthetic code, `Q###`. **Not** a real CPT/HCPCS code.       |
| `billed_amount`  | number  | > 0. What the provider billed.                               |
| `allowed_amount` | number  | > 0. Benchmark-allowed amount for the procedure.             |
| `outcome`        | string  | Enum: `clean` \| `complication` \| `readmission`.            |
| `patient_ref`    | string  | PHI-shaped opaque reference. Handled per ADR-0005.           |
| `service_date`   | string  | ISO date.                                                    |
| `schema_version` | string  | `"1.0"`. Consumers reject unknown major versions.            |

Synthetic codes use a `Q` prefix specifically so no one can (or needs to) evaluate
whether a real procedure code was used correctly. The code is an opaque grouping key.

### Validation rules

A claim is **valid** iff:

1. `claim_id`, `provider_id`, `procedure_code`, `outcome` are present and non-empty.
2. `billed_amount > 0` and `allowed_amount > 0`.
3. `allowed_amount <= billed_amount`. (An allowed amount above billed is a data
   error, not a legal claim.)
4. `outcome` is in the enum.
5. `schema_version` major version is supported.

An invalid claim is routed to the dead-letter queue with a structured reason. It is
never dropped silently and never scored.

## 2. Pipeline stages

1. **Ingest.** The generator publishes claim events to SNS topic `claims.raw`.
2. **Fan-out.** `claims.raw` fans out to SQS queue `validation.q`. (The topic, not a
   direct queue, is the seam that lets future consumers subscribe without touching
   the producer — see ADR-0002.)
3. **Validation worker.** Consumes `validation.q`. Valid claims are forwarded to SQS
   `scoring.q`. Invalid claims go to `validation.dlq` with a reason. Consumer is
   idempotent on `claim_id` (ADR-0007).
4. **Scoring worker.** Consumes `scoring.q`. Computes per-claim signals, upserts the
   claim into `claim_scores`, and recomputes the provider's aggregate in
   `provider_scores`. Idempotent on `claim_id`: reprocessing the same claim must not
   change the aggregate.
5. **Ranking API.** Serves the deterministic ranking and, on demand, a confined
   natural-language explanation for a single provider's rank.

## 3. Scoring contract

The scoring function is deterministic and hand-verifiable. This is a hard
requirement: it is the test oracle.

### Per-claim signals

For a valid claim `c`:

```
cost_efficiency(c) = allowed_amount / billed_amount          # in (0, 1]
quality(c)         = { clean: 1.0, complication: 0.5, readmission: 0.0 }[outcome]
```

### Per-provider aggregate

For provider `p` with valid claims `C_p` (|C_p| = n > 0):

```
cost_efficiency(p) = mean( cost_efficiency(c) for c in C_p )
quality(p)         = mean( quality(c)         for c in C_p )
provider_score(p)  = round( 0.5 * cost_efficiency(p) + 0.5 * quality(p), 4 )
```

A provider with zero valid claims has no score and does not appear in the ranking.

### Ranking

Providers are ranked by `provider_score` descending. Ties break by `provider_id`
ascending. The ordering is therefore total and deterministic — the same input set
always yields the same ranking.

### Worked example (also in `benchmark/golden.seed.jsonl`)

Provider `P-001`, two claims:

- `clm_0001`: billed 1000, allowed 800, `clean` → cost_efficiency 0.80, quality 1.0
- `clm_0002`: billed 2000, allowed 1000, `complication` → cost_efficiency 0.50, quality 0.5

```
cost_efficiency(P-001) = (0.80 + 0.50) / 2 = 0.65
quality(P-001)         = (1.0  + 0.5 ) / 2 = 0.75
provider_score(P-001)  = 0.5*0.65 + 0.5*0.75 = 0.70
```

Provider `P-002`, one claim:

- `clm_0003`: billed 500, allowed 500, `clean` → cost_efficiency 1.0, quality 1.0
- `provider_score(P-002) = 1.0`

Ranking: `1. P-002 (1.0)`, `2. P-001 (0.70)`.

## 4. API contract

All responses are JSON. The ranking endpoints are pure reads over `provider_scores`
and never invoke the language model.

### `GET /providers/ranking?limit=<n>`

```json
[
  { "rank": 1, "provider_id": "P-002", "provider_score": 1.0,
    "cost_efficiency": 1.0, "quality": 1.0, "claim_count": 1 },
  { "rank": 2, "provider_id": "P-001", "provider_score": 0.70,
    "cost_efficiency": 0.65, "quality": 0.75, "claim_count": 2 }
]
```

### `GET /providers/{provider_id}`

The single-provider detail: score, sub-signals, claim count, rank.

### `GET /providers/{provider_id}/explanation`

The only endpoint that calls the language model. The model receives a fixed set of
**grounded facts** (the provider's score, sub-signals, claim count, rank, and the
same figures for the neighbors it is being compared against) and must explain the
rank using only those facts.

```json
{
  "provider_id": "P-001",
  "rank": 2,
  "grounded_facts": {
    "provider_score": 0.70, "cost_efficiency": 0.65, "quality": 0.75,
    "claim_count": 2, "rank": 2, "neighbor_above": { "provider_id": "P-002", "provider_score": 1.0 }
  },
  "explanation": "P-001 ranks 2nd with a score of 0.70. Its quality signal (0.75) ..."
}
```

The explanation is generated, so it is not asserted for exact text. It is measured
for **faithfulness**: every quantitative claim in the explanation must match a value
in `grounded_facts`, and it must not introduce facts that are not present. See
`docs/EVAL_PLAN.md`.

## 5. Failure and delivery semantics

- **At-least-once delivery.** Both queues may redeliver. Consumers are therefore
  idempotent on `claim_id` (ADR-0007).
- **Poison messages.** A message that fails processing repeatedly (validation
  worker: unparseable; scoring worker: unexpected error) is redriven to the relevant
  dead-letter queue after a bounded number of receives, with structured context.
- **Replay.** Dead-lettered messages can be inspected and re-driven back onto the
  source queue once the cause is addressed. Replay must be safe precisely because
  consumers are idempotent.

These semantics are enforced by tests, not left to runtime hope. See
`docs/EVAL_PLAN.md` §Reliability tests.

## 6. Load generator

Synthetic claims are produced by a first-class, configurable tool — not an ad-hoc
script — because the same tool drives three different jobs: it seeds normal
processing, it injects the poison messages the dead-letter tests assert on, and it
produces the bursts the autoscaling and benchmark runs measure. Building it once, with
knobs, is what makes those runs reproducible rather than one-off.

### Configuration

| knob | meaning |
|------|---------|
| `rate` | events per second to publish. |
| `duration` | how long to publish for (or a fixed `count`). |
| `burst` | optional step change in rate at a given offset, to trigger a scale-up event. |
| `provider_distribution` | how claims spread across providers (e.g. uniform, or skewed so a few providers dominate) — shapes what the ranking looks like. |
| `outcome_mix` | probability weights over `clean` / `complication` / `readmission`. |
| `failure_injection` | fraction of emitted events that are deliberately invalid or malformed, and of what kind (see below). |
| `seed` | RNG seed, so a run is reproducible byte-for-byte. |

### Failure injection

The generator can emit, at a configured rate, events that exercise the failure paths
rather than the happy path:

- **Invalid-but-parseable** — violates a validation rule from §1 (e.g.
  `allowed_amount > billed_amount`). Must land in `validation.dlq` with the right
  reason, and must not be scored.
- **Malformed / unparseable** — not a decodable claim event at all. Must be
  dead-lettered as a poison message after the bounded receive count, never crash the
  consumer.
- **Duplicate** — re-emits an already-sent `claim_id`. Must not double-count
  (idempotency, ADR-0007).

This makes the reliability tests (`EVAL_PLAN.md` Tier 1) driven by the same tool as
the load benchmarks: a DLQ test is just a generator run with `failure_injection` set,
and a benchmark is a generator run with `rate`/`burst` set. Every reported number and
every failure test traces back to a reproducible generator configuration and seed.
