"""Create the instance-wide "Dossiernummer" patient identifier.

    python manage.py provision_dossier_number_identifier            # dry run
    python manage.py provision_dossier_number_identifier --apply --user admin

Idempotent: an existing identifier with the same system is reported, never
changed. See care_suriname/resources/dossier_number/README.md.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from care.emr.models.patient import PatientIdentifierConfig
from care.emr.resources.patient_identifier.spec import PatientIdentifierCreateSpec
from care_suriname.resources.dossier_number import (
    DOSSIER_NUMBER_CONFIG,
    DOSSIER_NUMBER_SYSTEM,
)


class Command(BaseCommand):
    help = (
        "Create the instance-wide Dossiernummer patient identifier (dry run default)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--user", help="Superuser recorded as creator.")

    def handle(self, *args, **options):
        existing = PatientIdentifierConfig.objects.filter(
            config__system=DOSSIER_NUMBER_SYSTEM
        ).first()
        if existing:
            scope = "facility" if existing.facility_id else "instance"
            self.stdout.write(
                f"present: {existing.config.get('display')!r} "
                f"status={existing.status} scope={scope}; nothing changed"
            )
            return
        # CARE's own spec validates the config exactly like its API does.
        spec = PatientIdentifierCreateSpec(
            config=DOSSIER_NUMBER_CONFIG, status="active"
        )
        if not options["apply"]:
            self.stdout.write("dry-run: would create 'Dossiernummer' (instance)")
            return
        admin = (
            get_user_model()
            .objects.filter(username=options["user"] or "", is_superuser=True)
            .first()
        )
        if admin is None:
            msg = "--apply needs --user with an existing superuser."
            raise CommandError(msg)
        config = spec.de_serialize()
        config.created_by = admin
        config.save()
        self.stdout.write(f"created: 'Dossiernummer' id={config.external_id}")
