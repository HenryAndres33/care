"""Add the missing districts of Suriname under the country "Suriname".

    python manage.py provision_suriname_districts           # dry run
    python manage.py provision_suriname_districts --apply

Idempotent: an existing district (same name, same parent, not deleted) is
reported, never changed; nothing is deleted. See
care_suriname/resources/suriname_districts/README.md.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from care.emr.models.organization import Organization
from care_suriname.resources.suriname_districts import COUNTRY_NAME, DISTRICTS


class Command(BaseCommand):
    help = "Add the missing districts of Suriname (dry run by default)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        countries = Organization.objects.filter(
            org_type="govt",
            name=COUNTRY_NAME,
            parent__isnull=True,
            deleted=False,
            metadata__govt_org_type="country",
        )
        if countries.count() != 1:
            msg = f"expected exactly one country {COUNTRY_NAME!r}, found {countries.count()}"
            raise CommandError(msg)
        country = countries.get()
        present = set(
            Organization.objects.filter(
                parent=country, org_type="govt", deleted=False
            ).values_list("name", flat=True)
        )
        missing = [name for name in DISTRICTS if name not in present]
        self.stdout.write(
            f"before: {len(present)} under {COUNTRY_NAME}; missing: {missing or 'none'}"
        )
        if not options["apply"] or not missing:
            self.stdout.write(
                "dry-run: nothing written" if missing else "nothing to do"
            )
            return
        with transaction.atomic():
            for name in missing:
                # Organization.save fills level/parent caches and root, and
                # refuses a duplicate name at the same level.
                Organization(
                    org_type="govt",
                    name=name,
                    parent=country,
                    description=f"District {name}, Suriname",
                    metadata={"govt_org_type": "district"},
                ).save()
        after = Organization.objects.filter(
            parent=country, org_type="govt", deleted=False
        ).count()
        self.stdout.write(
            f"created: {len(missing)}; after: {after} under {COUNTRY_NAME}"
        )
