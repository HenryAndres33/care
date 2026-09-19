import copy
import json
import math

from care.emr.models.organization import FacilityOrganizationUser
from care.security.models import RoleModel
from care.users.models import User
from care_suriname.resources.correspondence import canonical_sha256

SUPPORTED_RECIPIENT_KINDS = {"healthcare_professional"}
MAX_VERIFIED_RECIPIENT_RESULTS = 50
SUPPORTED_RECIPIENT_CHANNELS = {
    "postal",
    "secure_email",
    "secure_endpoint",
}
MAX_DIRECTORY_JSON_DEPTH = 5
MAX_DIRECTORY_JSON_ITEMS = 50
MAX_DIRECTORY_JSON_NODES = 200
MAX_DIRECTORY_JSON_KEY_LENGTH = 128
MAX_DIRECTORY_JSON_STRING_LENGTH = 2048
MAX_POSTAL_ADDRESS_BYTES = 8192
MAX_SOURCE_PROVENANCE_BYTES = 16384


def recipient_content_hash(recipient) -> str:
    validate_recipient_directory_payloads(recipient)
    return canonical_sha256(
        {
            "channel_identifier": recipient.channel_identifier,
            "channel_type": recipient.channel_type,
            "contract": "verified-correspondence-recipient-v1",
            "display_name": recipient.display_name,
            "facility": _external_id(recipient.facility),
            "healthcare_service": _external_id(recipient.healthcare_service),
            "organization": _external_id(recipient.organization),
            "organization_name": recipient.organization_name,
            "patient": _external_id(recipient.patient),
            "postal_address": copy.deepcopy(recipient.postal_address),
            "professional_role": recipient.professional_role,
            "qualification": recipient.qualification,
            "recipient_kind": recipient.recipient_kind,
            "registration": recipient.registration,
            "source_provenance": copy.deepcopy(recipient.source_provenance),
            "source_reference": recipient.source_reference,
            "source_type": recipient.source_type,
            "verified_at": recipient.verified_at,
            "verified_by": _external_id(recipient.verified_by),
        }
    )


def recipient_snapshot(recipient) -> dict:
    return {
        "channel": {
            "identifier": recipient.channel_identifier,
            "type": recipient.channel_type,
        },
        "display_name": recipient.display_name,
        "facility": str(recipient.facility.external_id),
        "healthcare_service": _external_id(recipient.healthcare_service),
        "id": str(recipient.external_id),
        "organization": _external_id(recipient.organization),
        "organization_name": recipient.organization_name,
        "patient": str(recipient.patient.external_id),
        "postal_address": copy.deepcopy(recipient.postal_address),
        "professional_role": recipient.professional_role,
        "qualification": recipient.qualification or None,
        "recipient_kind": recipient.recipient_kind,
        "registration": recipient.registration or None,
        "source": {
            "provenance": copy.deepcopy(recipient.source_provenance),
            "reference": recipient.source_reference,
            "type": recipient.source_type,
        },
        "verified_at": recipient.verified_at.isoformat(),
        "verified_by": str(recipient.verified_by.external_id),
        "version": recipient.resource_version,
        "hash": recipient.content_hash,
    }


def validate_recipient_directory_payloads(recipient) -> None:
    if recipient.recipient_kind not in SUPPORTED_RECIPIENT_KINDS:
        raise InvalidRecipientDirectoryPayloadError(
            "Recipient kind is not supported for clinical correspondence"
        )
    _validate_bounded_json(
        recipient.postal_address,
        label="postal address",
        max_bytes=MAX_POSTAL_ADDRESS_BYTES,
    )
    _validate_bounded_json(
        recipient.source_provenance,
        label="source provenance",
        max_bytes=MAX_SOURCE_PROVENANCE_BYTES,
    )


def validate_verified_recipient(recipient, *, lock_verifier=False) -> None:
    required_strings = [
        recipient.display_name,
        recipient.professional_role,
        recipient.organization_name,
        recipient.channel_identifier,
        recipient.source_type,
        recipient.source_reference,
    ]
    if (
        recipient.deleted
        or not recipient.active
        or not recipient.verified
        or not recipient.verified_by_id
        or not recipient.verified_at
        or any(not value or not value.strip() for value in required_strings)
        or recipient.recipient_kind not in SUPPORTED_RECIPIENT_KINDS
        or recipient.channel_type not in SUPPORTED_RECIPIENT_CHANNELS
        or not _verifier_governance_available(recipient, lock=lock_verifier)
    ):
        raise InvalidVerifiedRecipientError(
            "Recipient identity, channel, or verification provenance is incomplete"
        )
    try:
        valid_hash = recipient_content_hash(recipient) == recipient.content_hash
    except InvalidRecipientDirectoryPayloadError as exc:
        raise InvalidVerifiedRecipientError(str(exc)) from exc
    if not valid_hash:
        raise InvalidVerifiedRecipientError(
            "Recipient identity, channel, or verification provenance is incomplete"
        )


class InvalidVerifiedRecipientError(ValueError):
    pass


class InvalidRecipientDirectoryPayloadError(ValueError):
    pass


def _validate_bounded_json(value, *, label, max_bytes):
    if not isinstance(value, dict) or not value:
        raise _payload_error(label, "must be a non-empty JSON object")
    state = {"nodes": 0}
    _validate_json_value(value, depth=1, state=state, label=label)
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise _payload_error(label, "is not canonical JSON") from exc
    if len(encoded) > max_bytes:
        raise _payload_error(label, "exceeds the verified directory size limit")


def _validate_json_value(value, *, depth, state, label):  # noqa: PLR0912
    state["nodes"] += 1
    if state["nodes"] > MAX_DIRECTORY_JSON_NODES:
        raise _payload_error(label, "has too many JSON values")
    if depth > MAX_DIRECTORY_JSON_DEPTH:
        raise _payload_error(label, "exceeds the JSON depth limit")
    if isinstance(value, dict):
        if len(value) > MAX_DIRECTORY_JSON_ITEMS:
            raise _payload_error(label, "has too many JSON properties")
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or not key.strip()
                or len(key) > MAX_DIRECTORY_JSON_KEY_LENGTH
            ):
                raise _payload_error(label, "contains an invalid JSON property")
            _validate_json_value(
                item,
                depth=depth + 1,
                state=state,
                label=label,
            )
        return
    if isinstance(value, list):
        if len(value) > MAX_DIRECTORY_JSON_ITEMS:
            raise _payload_error(label, "has too many JSON items")
        for item in value:
            _validate_json_value(
                item,
                depth=depth + 1,
                state=state,
                label=label,
            )
        return
    if isinstance(value, str):
        if len(value) > MAX_DIRECTORY_JSON_STRING_LENGTH:
            raise _payload_error(label, "contains an oversized JSON string")
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    raise _payload_error(label, "contains an unsupported JSON value")


def _payload_error(label, detail):
    return InvalidRecipientDirectoryPayloadError(f"Recipient {label} {detail}")


def _verifier_governance_available(recipient, *, lock):
    user_query = User._base_manager.filter(  # noqa: SLF001
        pk=recipient.verified_by_id,
        deleted=False,
        is_active=True,
        verified=True,
        is_service_account=False,
    )
    if lock:
        user_query = user_query.select_for_update(of=("self",))
    verifier = user_query.first()
    if not verifier:
        return False

    memberships = FacilityOrganizationUser._base_manager.filter(  # noqa: SLF001
        deleted=False,
        user=verifier,
        organization__deleted=False,
        organization__active=True,
        organization__facility_id=recipient.facility_id,
        role__deleted=False,
        role__is_archived=False,
    ).order_by("pk")
    if lock:
        memberships = memberships.select_for_update(of=("self",))
    memberships = list(memberships[:MAX_DIRECTORY_JSON_ITEMS])
    if not memberships:
        return False
    if lock:
        role_ids = {membership.role_id for membership in memberships}
        locked_role_ids = set(
            RoleModel._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .filter(
                pk__in=role_ids,
                deleted=False,
                is_archived=False,
            )
            .values_list("pk", flat=True)
        )
        if locked_role_ids != role_ids:
            return False
    return True


def _external_id(instance):
    return str(instance.external_id) if instance else None
