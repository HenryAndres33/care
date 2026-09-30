"""Add the missing districts of Suriname under the country "Suriname".

    python manage.py provision_suriname_districts                   # dry run
    python manage.py provision_suriname_districts --apply
    python manage.py provision_suriname_districts --apply --create-country
    python manage.py provision_suriname_districts --apply --mark-country

The dry run lists the top-level geographic organizations, so an operator can
see the existing hierarchy without a database query. `--create-country` adds
the country only when no top-level organization named "Suriname" exists.
`--mark-country` marks an existing, untyped top-level "Suriname" as a country
and its untyped children named after a district as districts, so CARE labels
the levels "Land" and "District"; no other field or row is changed.
Otherwise existing rows are reported, never changed; nothing is deleted.
See care_suriname/resources/suriname_districts/README.md.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from care.emr.models.organization import Organization
from care_suriname.resources.suriname_districts import COUNTRY_NAME, DISTRICTS

COUNTRY_METADATA = {"govt_org_type": "country", "govt_org_children_type": "district"}


def _top_level():
    return Organization.objects.filter(
        org_type="govt", parent__isnull=True, deleted=False
    ).order_by("name")


class Command(BaseCommand):
    help = "Add the missing districts of Suriname (dry run by default)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--create-country",
            action="store_true",
            help='Create "Suriname" as a country if no top-level one exists.',
        )
        parser.add_argument(
            "--mark-country",
            action="store_true",
            help='Mark an untyped top-level "Suriname" (and its untyped '
            "district children) with the country/district type.",
        )

    def handle(self, *args, **options):
        top = list(_top_level())
        self.stdout.write(f"top-level geographic organizations: {len(top)}")
        for org in top:
            children = Organization.objects.filter(parent=org, deleted=False).count()
            kind = (org.metadata or {}).get("govt_org_type") or "-"
            self.stdout.write(f"  {org.name!r} type={kind} children={children}")

        named = [org for org in top if org.name == COUNTRY_NAME]
        if len(named) > 1:
            msg = f"{len(named)} top-level {COUNTRY_NAME!r}; resolve by hand"
            raise CommandError(msg)
        country = named[0] if named else None
        country_type = (
            (country.metadata or {}).get("govt_org_type") if country else None
        )
        to_mark = []
        if country and country_type != "country":
            if country_type or not options["mark_country"]:
                msg = (
                    f"{COUNTRY_NAME!r} has type {country_type or '-'}, not country; "
                    "not changed (an untyped one can be marked with --mark-country)"
                )
                raise CommandError(msg)
            to_mark = [
                child
                for child in Organization.objects.filter(
                    parent=country, org_type="govt", deleted=False
                )
                if child.name in DISTRICTS
                and not (child.metadata or {}).get("govt_org_type")
            ]
            self.stdout.write(
                f"to mark: {COUNTRY_NAME!r} as country; districts "
                f"{[child.name for child in to_mark] or 'none'}"
            )
        if country is None and not options["create_country"]:
            msg = f"no top-level {COUNTRY_NAME!r}; rerun with --create-country"
            raise CommandError(msg)

        present = (
            set(
                Organization.objects.filter(
                    parent=country, org_type="govt", deleted=False
                ).values_list("name", flat=True)
            )
            if country
            else set()
        )
        missing = [name for name in DISTRICTS if name not in present]
        plan = f"missing districts: {missing or 'none'}"
        if country is None:
            plan = f"country {COUNTRY_NAME!r} to create; {plan}"
        self.stdout.write(plan)
        if not options["apply"]:
            self.stdout.write("dry-run: nothing written")
            return
        if country and not missing and not to_mark and country_type == "country":
            self.stdout.write("nothing to do")
            return
        with transaction.atomic():
            if country is not None and country_type != "country":
                country.metadata = {**(country.metadata or {}), **COUNTRY_METADATA}
                country.save(update_fields=["metadata"])
                for child in to_mark:
                    child.metadata = {
                        **(child.metadata or {}),
                        "govt_org_type": "district",
                    }
                    child.save(update_fields=["metadata"])
                self.stdout.write(f"marked: country + {len(to_mark)} district(s)")
            if country is None:
                country = Organization(
                    org_type="govt",
                    name=COUNTRY_NAME,
                    description=COUNTRY_NAME,
                    metadata=COUNTRY_METADATA,
                )
                country.save()
                self.stdout.write(f"created country {COUNTRY_NAME!r}")
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
