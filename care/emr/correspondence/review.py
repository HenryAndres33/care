from care.emr.correspondence.author import (
    InvalidVerifiedAuthorError,
    verified_author_snapshot,
)
from care.emr.correspondence.recipient import (
    recipient_snapshot,
    validate_verified_recipient,
)
from care.emr.correspondence.source import (
    compilation_frozen_integrity_valid,
    compilation_sources_available,
)
from care.emr.models.organization import FacilityOrganizationUser
from care.emr.resources.correspondence import canonical_sha256


def correspondence_review_hash(review) -> str:
    return canonical_sha256(
        {
            "author": review.author_snapshot,
            "compilation": str(review.compilation.external_id),
            "compilation_hash": review.compilation_hash,
            "contract": "correspondence-review-binding-v1",
            "recipient": review.recipient_snapshot,
            "review": str(review.external_id),
            "reviewed_at": review.reviewed_at,
        }
    )


def review_binding_integrity_valid(review) -> bool:
    """Validate the immutable review evidence without consulting live directory state."""
    author = review.author_snapshot
    recipient = review.recipient_snapshot
    compilation = review.compilation
    if (
        review.deleted
        or review.status != "reviewed"
        or review.compilation_hash != compilation.compiled_hash
        or review.patient_id != compilation.patient_id
        or review.encounter_id != compilation.encounter_id
        or review.facility_id != compilation.facility_id
        or review.department_id != compilation.department_id
        or review.author_id != compilation.author_id
        or review.reviewer_id != review.author_id
        or not _author_snapshot_valid(review, author)
        or not _recipient_snapshot_valid(review, recipient)
    ):
        return False
    return correspondence_review_hash(review) == review.review_hash


def reviewed_binding_frozen_integrity_valid(review) -> bool:
    try:
        return all(
            [
                compilation_frozen_integrity_valid(review.compilation),
                review_binding_integrity_valid(review),
            ]
        )
    except (AttributeError, TypeError, ValueError):
        return False


def reviewed_binding_available(review, *, lock_verifier=False) -> bool:
    """Validate current actionability for a new letter or delivery action."""
    try:
        validate_verified_recipient(
            review.recipient,
            lock_verifier=lock_verifier,
        )
        current_recipient_matches = all(
            [
                review.recipient_version == review.recipient.resource_version,
                review.recipient_hash == review.recipient.content_hash,
                review.recipient_snapshot == recipient_snapshot(review.recipient),
            ]
        )
        current_author_matches = _current_author_matches_snapshot(
            review,
            lock=lock_verifier,
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return all(
        [
            reviewed_binding_frozen_integrity_valid(review),
            compilation_sources_available(review.compilation),
            current_recipient_matches,
            current_author_matches,
        ]
    )


def _author_snapshot_valid(review, snapshot):
    if not isinstance(snapshot, dict):
        return False
    facility = snapshot.get("facility")
    department = snapshot.get("department")
    return all(
        [
            snapshot.get("id") == str(review.author.external_id),
            snapshot.get("verified") is True,
            isinstance(snapshot.get("display"), str),
            bool(snapshot.get("display", "").strip()),
            isinstance(snapshot.get("professional_role"), str),
            bool(snapshot.get("professional_role", "").strip()),
            isinstance(snapshot.get("membership"), str),
            bool(snapshot.get("membership", "").strip()),
            isinstance(facility, dict),
            facility.get("id") == str(review.facility.external_id),
            isinstance(department, dict),
            department.get("id") == str(review.department.external_id),
            snapshot.get("qualification") is None
            or isinstance(snapshot.get("qualification"), str),
            snapshot.get("registration") is None
            or isinstance(snapshot.get("registration"), str),
        ]
    )


def _recipient_snapshot_valid(review, snapshot):
    return bool(
        isinstance(snapshot, dict)
        and snapshot.get("id") == str(review.recipient.external_id)
        and snapshot.get("patient") == str(review.patient.external_id)
        and snapshot.get("facility") == str(review.facility.external_id)
        and snapshot.get("version") == review.recipient_version
        and snapshot.get("hash") == review.recipient_hash
    )


def _current_author_matches_snapshot(review, *, lock):
    membership_query = (
        FacilityOrganizationUser._base_manager.select_related(  # noqa: SLF001
            "role"
        ).filter(
            external_id=review.author_snapshot.get("membership"),
            user_id=review.author_id,
            organization_id=review.department_id,
        )
    )
    if lock:
        membership_query = membership_query.select_for_update(of=("self",))
    membership = membership_query.first()
    if not membership:
        return False
    try:
        current = verified_author_snapshot(
            user=review.author,
            membership=membership,
            role=membership.role,
            facility=review.facility,
            department=review.department,
        )
    except InvalidVerifiedAuthorError:
        return False
    return current == review.author_snapshot
