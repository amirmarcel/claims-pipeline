"""Load generator configuration (SPEC.md §6).

Session 1 implements only: rate, duration/count, provider_distribution
(uniform), outcome_mix, seed. Two knobs from the full §6 contract are
deliberately not wired up yet:

- `burst` — a step change in rate at a configured offset. Session 3
  (autoscaling benchmark). Plugs in at the publish-pacing loop in
  `generator/cli.py`, which currently paces at a single constant `rate`.
- `failure_injection` — invalid/malformed/duplicate events. Session 7
  (DLQ + reliability tests). Plugs in at `generate_claims` below, which
  currently only ever emits claims that pass `events.validate`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_OUTCOME_MIX: dict[str, float] = {
    "clean": 0.7,
    "complication": 0.2,
    "readmission": 0.1,
}


@dataclass(frozen=True, slots=True)
class GeneratorConfig:
    rate: float
    seed: int
    count: int | None = None
    duration: float | None = None
    provider_distribution: str = "uniform"
    provider_pool_size: int = 20
    outcome_mix: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_OUTCOME_MIX))

    def __post_init__(self) -> None:
        if self.rate <= 0:
            raise ValueError("rate must be > 0")
        if (self.count is None) == (self.duration is None):
            raise ValueError("exactly one of count or duration must be set")
        if self.count is not None and self.count <= 0:
            raise ValueError("count must be > 0")
        if self.duration is not None and self.duration <= 0:
            raise ValueError("duration must be > 0")
        if self.provider_distribution != "uniform":
            raise ValueError(
                f"unsupported provider_distribution: {self.provider_distribution!r} "
                "(only 'uniform' is implemented in v1)"
            )
        if self.provider_pool_size <= 0:
            raise ValueError("provider_pool_size must be > 0")

    @property
    def event_count(self) -> int:
        if self.count is not None:
            return self.count
        assert self.duration is not None
        return max(1, round(self.rate * self.duration))
