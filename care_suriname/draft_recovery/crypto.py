"""Deployment wrapping keys come only from a protected, non-symlink file."""

import base64
import json
import os
import stat
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.views.decorators.debug import sensitive_variables

MAX_CONFIG_BYTES = 16384
MAX_KEY_ID_LENGTH = 80
KEY_BYTES = 32


class RecoveryUnavailableError(Exception):
    pass


@dataclass(repr=False)
class WrappingKeys:
    active_id: str
    keys: dict[str, bytes]


@sensitive_variables()
def load_wrapping_keys():
    descriptor = None
    try:
        path = settings.DRAFT_RECOVERY_WRAPPING_KEYS_FILE
        if not path:
            raise RecoveryUnavailableError
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
            or info.st_uid not in (0, os.geteuid())
            or info.st_size > MAX_CONFIG_BYTES
        ):
            raise RecoveryUnavailableError
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            data = json.loads(stream.read(MAX_CONFIG_BYTES + 1))
        active = data["active_key_id"]
        encoded = data["keys"]
        if not isinstance(active, str) or not isinstance(encoded, dict):
            raise RecoveryUnavailableError
        keys = {}
        for key_id, value in encoded.items():
            if not isinstance(key_id, str) or not 1 <= len(key_id) <= MAX_KEY_ID_LENGTH:
                raise RecoveryUnavailableError
            decoded = base64.b64decode(value, validate=True)
            if len(decoded) != KEY_BYTES:
                raise RecoveryUnavailableError
            keys[key_id] = decoded
        if active not in keys:
            raise RecoveryUnavailableError
        return WrappingKeys(active, keys)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise RecoveryUnavailableError from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def associated_data(owner_id, key_id, wrapping_key_id):
    return f"care-draft-key:v1:{owner_id}:{key_id}:{wrapping_key_id}".encode()


@sensitive_variables()
def wrap_key(material, owner_id, key_id, ring):
    nonce = os.urandom(12)
    ciphertext = AESGCM(ring.keys[ring.active_id]).encrypt(
        nonce, material, associated_data(owner_id, key_id, ring.active_id)
    )
    return ring.active_id, nonce, ciphertext


@sensitive_variables()
def unwrap_key(row, owner_id, ring):
    try:
        material = AESGCM(ring.keys[row.wrapping_key_id]).decrypt(
            bytes(row.nonce),
            bytes(row.ciphertext),
            associated_data(owner_id, row.id, row.wrapping_key_id),
        )
        if len(material) != KEY_BYTES:
            raise RecoveryUnavailableError
        return material
    except (InvalidTag, ValueError, KeyError, TypeError):
        raise RecoveryUnavailableError from None
