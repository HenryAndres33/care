"""Write one facility's setup (no patients) to a JSON file.

    python manage.py export_facility_setup \\
        --facility-name "Academisch Ziekenhuis Paramaribo" \\
        --exclude-clinical-text .politest --output /tmp/azp-setup.json

Read-only on the database. See care_suriname/resources/facility_setup/README.md.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from care.facility.models import Facility
from care_suriname.resources.facility_setup.export import build_facility_setup
from care_suriname.resources.facility_setup.format import (
    DEFAULT_QUESTIONNAIRE_SLUGS,
    FacilitySetupError,
)


class Command(BaseCommand):
    help = "Export one facility's setup, without any patient data, to JSON."

    def add_arguments(self, parser):
        parser.add_argument("--facility-name", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument(
            "--questionnaire",
            action="append",
            dest="questionnaires",
            help="Form slug to include; repeatable. Default: the urology forms.",
        )
        parser.add_argument(
            "--exclude-clinical-text",
            action="append",
            default=[],
            dest="excluded_text_keys",
            help="Smart Text key to leave behind (test material); repeatable.",
        )

    def handle(self, *args, **options):
        facilities = list(Facility.objects.filter(name=options["facility_name"]))
        if len(facilities) != 1:
            msg = f"Expected one facility named {options['facility_name']!r}."
            raise CommandError(msg)
        output = Path(options["output"])
        if output.exists():
            msg = f"{output} exists; choose a new file name."
            raise CommandError(msg)
        try:
            data = build_facility_setup(
                facilities[0],
                options["questionnaires"] or DEFAULT_QUESTIONNAIRE_SLUGS,
                options["excluded_text_keys"],
            )
        except FacilitySetupError as error:
            raise CommandError(str(error)) from error
        output.write_text(
            json.dumps(data, cls=DjangoJSONEncoder, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        self.stdout.write(f"Wrote {output}")
        for name, count in data["counts"].items():
            self.stdout.write(f"  {name}: {count}")
