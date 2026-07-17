"""Load generator configuration (SPEC.md §6).

Session 1 implemented rate, duration/count, provider_distribution (uniform),
outcome_mix, seed. Session 3 added `failure_injection`. Session 7 adds
`burst` -- a step change in rate at a configured offset, the last knob from
the full §6 contract. It plugs into the publish-pacing loop in
`generator/publisher.py`, which paces at a single constant `rate` before this.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_OUTCOME_MIX: dict[str, float] = {
    "clean": 0.7,
    "complication": 0.2,
    "readmission": 0.1,
}

# The three failure_injection modes from SPEC.md §6.
FAILURE_INJECTION_MODES: tuple[str, ...] = ("invalid-but-parseable", "malformed", "duplicate")


@dataclass(frozen=True, slots=True)
class BurstConfig:
    """A step change in publish rate at `offset` seconds into the run.

    Before `offset`, events publish at the base `GeneratorConfig.rate`; from
    `offset` onward, at `rate`. Only meaningful with `duration` (there's no
    time axis to place a step change on in fixed-`count` mode).
    """

    offset: float
    rate: float

    def __post_init__(self) -> None:
        if self.offset < 0:
            raise ValueError("burst offset must be >= 0")
        if self.rate <= 0:
            raise ValueError("burst rate must be > 0")


@dataclass(frozen=True, slots=True)
class GeneratorConfig:
    rate: float
    seed: int
    count: int | None = None
    duration: float | None = None
    provider_distribution: str = "uniform"
    provider_pool_size: int = 20
    outcome_mix: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_OUTCOME_MIX))
    failure_injection: dict[str, float] | None = None
    burst: BurstConfig | None = None

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
        if self.failure_injection is not None:
            unknown = set(self.failure_injection) - set(FAILURE_INJECTION_MODES)
            if unknown:
                raise ValueError(f"unknown failure_injection mode(s): {sorted(unknown)}")
            for mode, fraction in self.failure_injection.items():
                if not 0.0 <= fraction <= 1.0:
                    raise ValueError(f"failure_injection[{mode!r}] must be in [0, 1]")
            total = sum(self.failure_injection.values())
            if total > 1.0:
                raise ValueError(f"failure_injection fractions must sum to <= 1.0, got {total}")
        if self.burst is not None:
            if self.duration is None:
                raise ValueError("burst requires duration to be set, not count")
            if self.burst.offset >= self.duration:
                raise ValueError("burst offset must be < duration")

    @property
    def event_count(self) -> int:
        if self.count is not None:
            return self.count
        assert self.duration is not None
        if self.burst is None:
            return max(1, round(self.rate * self.duration))
        sustained_seconds = self.burst.offset
        burst_seconds = self.duration - self.burst.offset
        total = self.rate * sustained_seconds + self.burst.rate * burst_seconds
        return max(1, round(total))
