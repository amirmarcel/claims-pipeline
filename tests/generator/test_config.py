import pytest

from claims_pipeline.generator.config import GeneratorConfig


def test_requires_exactly_one_of_count_or_duration() -> None:
    with pytest.raises(ValueError, match="exactly one of count or duration"):
        GeneratorConfig(rate=10.0, seed=1)
    with pytest.raises(ValueError, match="exactly one of count or duration"):
        GeneratorConfig(rate=10.0, seed=1, count=5, duration=1.0)


def test_rate_must_be_positive() -> None:
    with pytest.raises(ValueError, match="rate must be > 0"):
        GeneratorConfig(rate=0.0, seed=1, count=5)


def test_only_uniform_provider_distribution_supported() -> None:
    with pytest.raises(ValueError, match="unsupported provider_distribution"):
        GeneratorConfig(rate=10.0, seed=1, count=5, provider_distribution="skewed")


def test_failure_injection_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="unknown failure_injection mode"):
        GeneratorConfig(rate=10.0, seed=1, count=5, failure_injection={"bogus": 0.1})


def test_failure_injection_rejects_out_of_range_fraction() -> None:
    with pytest.raises(ValueError, match=r"must be in \[0, 1\]"):
        GeneratorConfig(rate=10.0, seed=1, count=5, failure_injection={"malformed": 1.5})


def test_failure_injection_rejects_fractions_summing_over_one() -> None:
    with pytest.raises(ValueError, match="must sum to <= 1.0"):
        GeneratorConfig(
            rate=10.0,
            seed=1,
            count=5,
            failure_injection={"malformed": 0.6, "duplicate": 0.6},
        )


def test_failure_injection_accepts_a_valid_configuration() -> None:
    config = GeneratorConfig(
        rate=10.0,
        seed=1,
        count=5,
        failure_injection={"invalid-but-parseable": 0.1, "malformed": 0.1, "duplicate": 0.1},
    )
    assert config.failure_injection == {
        "invalid-but-parseable": 0.1,
        "malformed": 0.1,
        "duplicate": 0.1,
    }
