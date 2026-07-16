"""Deterministic synthetic claim generation.

Pure given a `GeneratorConfig`: all randomness flows from `random.Random(seed)`,
and the only "clock" is a fixed anchor date, not wall-clock time, so the same
seed always produces the same claims in the same order (SPEC.md §6).
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from claims_pipeline.events import ClaimEvent

from .config import GeneratorConfig

SPECIALTIES = ("cardiology", "orthopedics", "primary")
SCHEMA_VERSION = "1.0"

# Fixed reference point for synthetic service dates. Not wall-clock time —
# using date.today() here would make generator output depend on when it is
# run, breaking determinism for a fixed seed.
ANCHOR_DATE = date(2026, 1, 1)
SERVICE_DATE_WINDOW_DAYS = 365


def generate_claims(config: GeneratorConfig) -> list[ClaimEvent]:
    rng = random.Random(config.seed)
    providers = [f"P-{i:03d}" for i in range(1, config.provider_pool_size + 1)]
    outcomes = list(config.outcome_mix.keys())
    weights = list(config.outcome_mix.values())

    claims: list[ClaimEvent] = []
    for i in range(1, config.event_count + 1):
        billed_amount = round(rng.uniform(100.0, 5000.0), 2)
        allowed_amount = round(billed_amount * rng.uniform(0.5, 1.0), 2)
        offset_days = rng.randint(0, SERVICE_DATE_WINDOW_DAYS - 1)
        claims.append(
            ClaimEvent(
                claim_id=f"clm_{config.seed:04d}_{i:08d}",
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
        )
    return claims
