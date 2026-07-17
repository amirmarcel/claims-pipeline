import json

import pytest

from claims_pipeline.events import ClaimEvent, validate
from claims_pipeline.generator.claims import MalformedEvent, generate_claims
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
    # No failure_injection configured: every generated event is a well-formed,
    # business-valid ClaimEvent (never a MalformedEvent).
    config = GeneratorConfig(rate=10.0, seed=42, count=200)
    for claim in generate_claims(config):
        assert isinstance(claim, ClaimEvent)
        ok, reason = validate(claim)
        assert ok is True, reason


def test_generated_claim_ids_are_unique() -> None:
    config = GeneratorConfig(rate=10.0, seed=7, count=200)
    claims = generate_claims(config)
    claim_ids = []
    for c in claims:
        assert isinstance(c, ClaimEvent)
        claim_ids.append(c.claim_id)
    assert len(claim_ids) == len(set(claim_ids))


def test_count_from_duration() -> None:
    config = GeneratorConfig(rate=10.0, seed=1, duration=5.0)
    assert config.event_count == 50
    assert len(generate_claims(config)) == 50


def test_failure_injection_none_is_identical_to_pre_session_3_output() -> None:
    # failure_injection unset draws no extra randomness, so output is
    # byte-for-byte identical to a config with the field left at its default.
    with_default = generate_claims(GeneratorConfig(rate=10.0, seed=3, count=30))
    with_explicit_none = generate_claims(
        GeneratorConfig(rate=10.0, seed=3, count=30, failure_injection=None)
    )
    assert with_default == with_explicit_none


def test_malformed_fraction_produces_undecodable_bodies() -> None:
    config = GeneratorConfig(rate=10.0, seed=42, count=50, failure_injection={"malformed": 1.0})
    events = generate_claims(config)
    assert len(events) == 50
    for event in events:
        assert isinstance(event, MalformedEvent)
        with pytest.raises(json.JSONDecodeError):
            json.loads(event.raw_body)


def test_invalid_but_parseable_fraction_violates_a_validation_rule() -> None:
    config = GeneratorConfig(
        rate=10.0, seed=42, count=50, failure_injection={"invalid-but-parseable": 1.0}
    )
    events = generate_claims(config)
    assert len(events) == 50
    for event in events:
        assert isinstance(event, ClaimEvent)
        ok, reason = validate(event)
        assert ok is False
        assert reason == "allowed_amount exceeds billed_amount"


def test_duplicate_fraction_reuses_earlier_claim_ids() -> None:
    config = GeneratorConfig(rate=10.0, seed=42, count=50, failure_injection={"duplicate": 1.0})
    events = generate_claims(config)
    claim_ids = []
    for event in events:
        assert isinstance(event, ClaimEvent)
        claim_ids.append(event.claim_id)
    # Every event after the first requests a duplicate; the first can't
    # duplicate anything yet, so it's the one distinct claim_id in the run.
    assert len(set(claim_ids)) == 1


def test_failure_injection_is_deterministic_given_the_same_seed() -> None:
    injection = {"invalid-but-parseable": 0.2, "malformed": 0.1, "duplicate": 0.1}
    first = generate_claims(
        GeneratorConfig(rate=10.0, seed=99, count=100, failure_injection=injection)
    )
    second = generate_claims(
        GeneratorConfig(rate=10.0, seed=99, count=100, failure_injection=injection)
    )
    assert first == second


def test_failure_injection_mixed_modes_produce_all_three_kinds() -> None:
    config = GeneratorConfig(
        rate=10.0,
        seed=99,
        count=200,
        failure_injection={"invalid-but-parseable": 0.3, "malformed": 0.3, "duplicate": 0.3},
    )
    events = generate_claims(config)

    malformed = [e for e in events if isinstance(e, MalformedEvent)]
    claims = [e for e in events if isinstance(e, ClaimEvent)]
    invalid_parseable = [e for e in claims if not validate(e)[0]]

    assert malformed
    assert invalid_parseable
    claim_ids = [e.claim_id for e in claims]
    assert len(claim_ids) != len(set(claim_ids))  # duplicates present
