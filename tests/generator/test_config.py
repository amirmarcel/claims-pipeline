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
