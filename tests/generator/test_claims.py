from claims_pipeline.events import validate
from claims_pipeline.generator.claims import generate_claims
from claims_pipeline.generator.config import GeneratorConfig


def test_same_seed_produces_identical_claims() -> None:
    config = GeneratorConfig(rate=10.0, seed=42, count=50)
    first = generate_claims(config)
    second = generate_claims(config)
    assert first == second


def test_different_seed_produces_different_claims() -> None:
    a = generate_claims(GeneratorConfig(rate=10.0, seed=1, count=20))
    b = generate_claims(GeneratorConfig(rate=10.0, seed=2, count=20))
    assert a != b


def test_generated_claims_are_all_valid() -> None:
    config = GeneratorConfig(rate=10.0, seed=42, count=200)
    for claim in generate_claims(config):
        ok, reason = validate(claim)
        assert ok is True, reason


def test_generated_claim_ids_are_unique() -> None:
    config = GeneratorConfig(rate=10.0, seed=7, count=200)
    claim_ids = [c.claim_id for c in generate_claims(config)]
    assert len(claim_ids) == len(set(claim_ids))


def test_count_from_duration() -> None:
    config = GeneratorConfig(rate=10.0, seed=1, duration=5.0)
    assert config.event_count == 50
    assert len(generate_claims(config)) == 50
