from django.core.management.base import BaseCommand, CommandError

from care.users.draft_recovery.crypto import RecoveryUnavailableError
from care.users.draft_recovery.models import DraftRecoveryKey
from care.users.draft_recovery.service import rewrap_key


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
