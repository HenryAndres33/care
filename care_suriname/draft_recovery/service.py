import secrets
import uuid

from django.db import transaction
from django.views.decorators.debug import sensitive_variables

from care.users.models import User
from care_suriname.draft_recovery.crypto import (
    RecoveryUnavailableError,
    load_wrapping_keys,
    unwrap_key,
    wrap_key,
)
from care_suriname.models.draft_recovery import DraftRecoveryKey


def eligible(owner):
    return owner.is_active and not owner.deleted and not owner.is_service_account


@sensitive_variables()
@transaction.atomic
def get_or_create_key(owner):
    ring = load_wrapping_keys()  # No DB mutation if secure configuration is absent.
    locked_owner = User.objects.select_for_update().get(pk=owner.pk)
    if not eligible(locked_owner):
        raise RecoveryUnavailableError
    row = DraftRecoveryKey.objects.filter(owner=locked_owner, active=True).first()
    if row is None:
        key_id = uuid.uuid4()
        material = secrets.token_bytes(32)
        wrapping_id, nonce, ciphertext = wrap_key(
            material, locked_owner.external_id, key_id, ring
        )
        row = DraftRecoveryKey.objects.create(
            id=key_id,
            owner=locked_owner,
            wrapping_key_id=wrapping_id,
            nonce=nonce,
            ciphertext=ciphertext,
        )
    return row, unwrap_key(row, locked_owner.external_id, ring)


@sensitive_variables()
@transaction.atomic
def get_owned_key(owner, key_id):
    locked_owner = User.objects.select_for_update().get(pk=owner.pk)
    if not eligible(locked_owner):
        raise RecoveryUnavailableError
    row = DraftRecoveryKey.objects.get(owner=locked_owner, id=key_id)
    return row, unwrap_key(row, locked_owner.external_id, load_wrapping_keys())


@sensitive_variables()
@transaction.atomic
def rewrap_key(key_id):
    ring = load_wrapping_keys()
    row = (
        DraftRecoveryKey.objects.select_for_update()
        .select_related("owner")
        .get(id=key_id)
    )
    material = unwrap_key(row, row.owner.external_id, ring)
    row.wrapping_key_id, row.nonce, row.ciphertext = wrap_key(
        material, row.owner.external_id, row.id, ring
    )
    row.save(update_fields=["wrapping_key_id", "nonce", "ciphertext"])
