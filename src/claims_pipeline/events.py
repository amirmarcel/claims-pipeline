"""The claim event contract (SPEC.md §1).

Dataclasses, not pydantic: the contract is small, fields are already typed and
checked under mypy strict, and pulling in a validation library would add a
dependency for work a five-branch pure function does just as clearly.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

OUTCOMES = frozenset({"clean", "complication", "readmission"})
SUPPORTED_SCHEMA_MAJOR_VERSIONS = frozenset({"1"})


@dataclass(frozen=True, slots=True)
class ClaimEvent:
    claim_id: str
    provider_id: str
    specialty: str
    procedure_code: str
    billed_amount: float
    allowed_amount: float
    outcome: str
    patient_ref: str
    service_date: str
    schema_version: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, object]) -> ClaimEvent:
        return ClaimEvent(
            claim_id=str(data["claim_id"]),
            provider_id=str(data["provider_id"]),
            specialty=str(data["specialty"]),
            procedure_code=str(data["procedure_code"]),
            billed_amount=float(data["billed_amount"]),  # type: ignore[arg-type]
            allowed_amount=float(data["allowed_amount"]),  # type: ignore[arg-type]
            outcome=str(data["outcome"]),
            patient_ref=str(data["patient_ref"]),
            service_date=str(data["service_date"]),
            schema_version=str(data["schema_version"]),
        )


def validate(claim: ClaimEvent) -> tuple[bool, str | None]:
    """Apply the five validation rules from SPEC.md §1, in order.

    Pure: no I/O, no clock, no randomness (AGENTS.md non-negotiable #1).
    Returns (True, None) if valid, else (False, <structured reason>) for the
    dead-letter record.
    """
    for field_name in ("claim_id", "provider_id", "procedure_code", "outcome"):
        if not getattr(claim, field_name):
            return False, f"missing required field: {field_name}"

    if claim.billed_amount <= 0:
        return False, "billed_amount must be > 0"
    if claim.allowed_amount <= 0:
        return False, "allowed_amount must be > 0"

    if claim.allowed_amount > claim.billed_amount:
        return False, "allowed_amount exceeds billed_amount"

    if claim.outcome not in OUTCOMES:
        return False, f"invalid outcome: {claim.outcome}"

    major_version = claim.schema_version.split(".", 1)[0]
    if major_version not in SUPPORTED_SCHEMA_MAJOR_VERSIONS:
        return False, f"unsupported schema_version: {claim.schema_version}"

    return True, None
