from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from care.facility.models import Facility
from care_suriname.declaration_codes.definitions import (
    DeclarationCodeConflictError,
    apply_changes,
    plan_changes,
)


class Command(BaseCommand):
    help = (
        "Load the SZF / Assuria / AZP declaration code lists into one facility "
        "as native charge item definitions without prices. Dry-run is the "
        "default; --apply writes. Idempotent. See DECLARATION_CODES.md."
    )

    def add_arguments(self, parser):
        parser.add_argument("--facility", required=True, help="Facility external id.")
        parser.add_argument(
            "--user",
            required=True,
            help="Existing CARE superuser recorded as configuration actor.",
        )
        parser.add_argument("--apply", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        facility = Facility.objects.filter(external_id=options["facility"]).first()
        if facility is None:
            msg = "Facility not found."
            raise CommandError(msg)
        actor = (
            get_user_model()
            .objects.filter(username=options["user"], is_superuser=True)
            .first()
        )
        if actor is None:
            msg = "--user must be an existing superuser."
            raise CommandError(msg)
        try:
            changes = plan_changes(facility)
        except DeclarationCodeConflictError as exc:
            msg = f"Existing definitions not made by this command use: {exc}"
            raise CommandError(msg) from exc
        mode = "apply" if options["apply"] else "dry-run"
        self.stdout.write(
            f"{mode}: facility {facility.name}: "
            f"create {len(changes['create'])}, update {len(changes['update'])}, "
            f"unchanged {len(changes['keep'])}, retire {len(changes['retire'])}"
        )
        for item in changes["create"]:
            self.stdout.write(f"  + {item.title}")
        for _current, item in changes["update"]:
            self.stdout.write(f"  ~ {item.title}")
        for current in changes["retire"]:
            self.stdout.write(f"  - {current.title}")
        if mode == "dry-run":
            transaction.set_rollback(True)
            return
        apply_changes(facility, actor, changes)
        self.stdout.write(self.style.SUCCESS("Declaration codes applied."))
