import uuid

from django.conf import settings
from django.db import models


class DraftRecoveryKey(models.Model):
    """Wrapped encryption keys only; never clinical text. Retain retired keys."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    wrapping_key_id = models.CharField(max_length=80)
    nonce = models.BinaryField()
    ciphertext = models.BinaryField()
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "users"
        constraints = [
            models.UniqueConstraint(
                fields=["owner"],
                condition=models.Q(active=True),
                name="one_active_draft_key_per_owner",
            )
        ]

    def __str__(self):
        return f"Draft recovery key {self.id}"
