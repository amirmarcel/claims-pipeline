from claims_pipeline.events import ClaimEvent, validate

VALID_KWARGS: dict[str, object] = {
    "claim_id": "clm_0001",
    "provider_id": "P-001",
    "specialty": "cardiology",
    "procedure_code": "Q100",
    "billed_amount": 1000.0,
    "allowed_amount": 800.0,
    "outcome": "clean",
    "patient_ref": "ref_000000000001",
    "service_date": "2026-01-01",
    "schema_version": "1.0",
}


def make_claim(**overrides: object) -> ClaimEvent:
    kwargs = dict(VALID_KWARGS)
    kwargs.update(overrides)
    return ClaimEvent(**kwargs)  # type: ignore[arg-type]


def test_valid_claim_passes() -> None:
    ok, reason = validate(make_claim())
    assert ok is True
    assert reason is None


def test_missing_required_field_fails() -> None:
    ok, reason = validate(make_claim(claim_id=""))
    assert ok is False
    assert reason == "missing required field: claim_id"


def test_non_positive_billed_amount_fails() -> None:
    ok, reason = validate(make_claim(billed_amount=0.0))
    assert ok is False
    assert reason == "billed_amount must be > 0"


def test_non_positive_allowed_amount_fails() -> None:
    ok, reason = validate(make_claim(allowed_amount=-5.0))
    assert ok is False
    assert reason == "allowed_amount must be > 0"


def test_allowed_exceeds_billed_fails() -> None:
    ok, reason = validate(make_claim(billed_amount=100.0, allowed_amount=200.0))
    assert ok is False
    assert reason == "allowed_amount exceeds billed_amount"


def test_invalid_outcome_fails() -> None:
    ok, reason = validate(make_claim(outcome="denied"))
    assert ok is False
    assert reason == "invalid outcome: denied"


def test_unsupported_schema_version_fails() -> None:
    ok, reason = validate(make_claim(schema_version="2.0"))
    assert ok is False
    assert reason == "unsupported schema_version: 2.0"


def test_allowed_equals_billed_is_valid() -> None:
    ok, reason = validate(make_claim(billed_amount=500.0, allowed_amount=500.0))
    assert ok is True
    assert reason is None
