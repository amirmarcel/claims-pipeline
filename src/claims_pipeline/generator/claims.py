"""Deterministic synthetic claim generation.

Pure given a `GeneratorConfig`: all randomness flows from `random.Random(seed)`,
and the only "clock" is a fixed anchor date, not wall-clock time, so the same
seed always produces the same claims in the same order (SPEC.md §6).

`failure_injection` (SPEC.md §6) makes a configured fraction of emitted events
exercise the failure paths instead of the happy path:

- **invalid-but-parseable** — a well-formed `ClaimEvent` that violates a
  validation rule (here, `allowed_amount > billed_amount`). Exercises the
  validation worker's business-invalid -> validation-dlq path (Session 2).
- **malformed** — not a decodable claim event at all. Represented as a
  `MalformedEvent`, a raw body that fails to even parse as JSON. Exercises
  the poison -> redrive -> DLQ path this session builds (ADR-0010).
- **duplicate** — reuses a `claim_id` already emitted earlier in the same
  run. Exercises consumer idempotency (ADR-0007).

Selection is a single deterministic `rng.random()` roll per event, in the
fixed mode order `FAILURE_INJECTION_MODES`, so the same seed always assigns
the same event index to the same mode regardless of dict insertion order. If
`failure_injection` is unset, no extra randomness is drawn at all, so
generator output is byte-for-byte identical to the pre-Session-3 behavior.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import date, timedelta

from claims_pipeline.events import ClaimEvent

from .config import FAILURE_INJECTION_MODES, GeneratorConfig

SPECIALTIES = ("cardiology", "orthopedics", "primary")
SCHEMA_VERSION = "1.0"

# Fixed reference point for synthetic service dates. Not wall-clock time --
# using date.today() here would make generator output depend on when it is
# run, breaking determinism for a fixed seed.
ANCHOR_DATE = date(2026, 1, 1)
SERVICE_DATE_WINDOW_DAYS = 365


@dataclass(frozen=True, slots=True)
class MalformedEvent:
    """A deliberately undecodable payload (SPEC.md §6 failure_injection:
    malformed). Not a `ClaimEvent` -- `raw_body` is published to SNS verbatim
    and is not valid JSON, so it fails to decode at every consumer."""

    raw_body: str


GeneratedEvent = ClaimEvent | MalformedEvent


def _malformed_body(claim: ClaimEvent) -> str:
    """Corrupt a well-formed claim's JSON so it fails `json.loads` -- a
    deterministic, content-derived way to produce an unparseable body."""
    body = json.dumps(claim.to_dict())
    return body[: len(body) // 2]


def generate_claims(config: GeneratorConfig) -> list[GeneratedEvent]:
    rng = random.Random(config.seed)
    providers = [f"P-{i:03d}" for i in range(1, config.provider_pool_size + 1)]
    outcomes = list(config.outcome_mix.keys())
    weights = list(config.outcome_mix.values())

    injection = config.failure_injection or {}
    thresholds: list[tuple[str, float]] = []
    cumulative = 0.0
    for mode in FAILURE_INJECTION_MODES:
        cumulative += injection.get(mode, 0.0)
        thresholds.append((mode, cumulative))

    events: list[GeneratedEvent] = []
    emitted_claim_ids: list[str] = []
    for i in range(1, config.event_count + 1):
        kind = "normal"
        if injection:
            roll = rng.random()
            for mode, threshold in thresholds:
                if roll < threshold:
                    kind = mode
                    break
        if kind == "duplicate" and not emitted_claim_ids:
            kind = "normal"

        billed_amount = round(rng.uniform(100.0, 5000.0), 2)
        allowed_amount = round(billed_amount * rng.uniform(0.5, 1.0), 2)
        offset_days = rng.randint(0, SERVICE_DATE_WINDOW_DAYS - 1)

        if kind == "invalid-but-parseable":
            # Violate SPEC.md §1 rule 3: allowed_amount must not exceed billed_amount.
            allowed_amount = billed_amount + round(rng.uniform(1.0, 100.0), 2)

        claim_id = (
            rng.choice(emitted_claim_ids)
            if kind == "duplicate"
            else f"clm_{config.seed:04d}_{i:08d}"
        )

        claim = ClaimEvent(
            claim_id=claim_id,
            provider_id=rng.choice(providers),
            specialty=rng.choice(SPECIALTIES),
            procedure_code=f"Q{rng.randint(100, 999)}",
            billed_amount=billed_amount,
            allowed_amount=allowed_amount,
            outcome=rng.choices(outcomes, weights=weights)[0],
            patient_ref=f"ref_{rng.randrange(10**12):012d}",
            service_date=(ANCHOR_DATE - timedelta(days=offset_days)).isoformat(),
            schema_version=SCHEMA_VERSION,
        )

        if kind == "malformed":
            events.append(MalformedEvent(raw_body=_malformed_body(claim)))
        else:
            events.append(claim)
            if kind == "normal":
                emitted_claim_ids.append(claim.claim_id)
    return events
