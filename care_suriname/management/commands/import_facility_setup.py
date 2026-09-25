"""Create a new facility from a setup file written by export_facility_setup.

    python manage.py import_facility_setup --input azp-setup.json --user admin

All or nothing: any conflict rolls the whole import back. Never changes or
deletes existing rows. See care_suriname/resources/facility_setup/README.md.
"""

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from care_suriname.resources.facility_setup.format import FacilitySetupError
from care_suriname.resources.facility_setup.importer import import_facility_setup


class Command(BaseCommand):
    help = "Create a new facility and its setup from an exported JSON file."

    def add_arguments(self, parser):
        parser.add_argument("--input", required=True)
        parser.add_argument(
            "--user",
            required=True,
            help="Existing superuser; becomes the facility's first admin.",
        )
        parser.add_argument(
            "--facility-name",
            help="Name for the new facility. Default: the exported name.",
        )

    def handle(self, *args, **options):
        admin = (
            get_user_model()
            .objects.filter(username=options["user"], is_superuser=True)
            .first()
        )
        if admin is None:
            msg = f"No superuser named {options['user']!r}."
            raise CommandError(msg)
        data = json.loads(Path(options["input"]).read_text(encoding="utf-8"))
        try:
            result = import_facility_setup(data, admin, options["facility_name"])
        except FacilitySetupError as error:
            msg = f"{error} Nothing was imported."
            raise CommandError(msg) from error
        facility = result["facility"]
        self.stdout.write(f"Created facility {facility.name!r}")
        self.stdout.write(f"  facility id: {facility.external_id}")
        for name, count in result["counts"].items():
            self.stdout.write(f"  {name}: {count}")
        self.stdout.write(
            "Next: set CLINICAL_WORKFLOW_MUTATIONS_ENABLED_FACILITIES to the "
            "facility id above, then create the colleagues' accounts."
        )
