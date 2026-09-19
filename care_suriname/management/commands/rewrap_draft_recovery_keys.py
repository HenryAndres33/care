from django.core.management.base import BaseCommand, CommandError

from care_suriname.draft_recovery.crypto import RecoveryUnavailableError
from care_suriname.draft_recovery.service import rewrap_key
from care_suriname.models.draft_recovery import DraftRecoveryKey


class Command(BaseCommand):
    help = "Rewrap retained draft keys under the configured active wrapping key; never prints keys."

    def handle(self, *args, **options):
        count = 0
        try:
            for key_id in DraftRecoveryKey.objects.values_list(
                "id", flat=True
            ).iterator():
                rewrap_key(key_id)
                count += 1
        except RecoveryUnavailableError:
            raise CommandError(
                "Rewrap unavailable; retain every old wrapping key and retry."
            ) from None
        self.stdout.write(f"Rewrapped {count} retained draft keys.")
