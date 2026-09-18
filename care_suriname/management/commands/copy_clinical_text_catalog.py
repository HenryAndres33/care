"""Copy a facility's Smart Text catalog to another facility.

Clinicians author Smart Text through the UI, and that content is facility-scoped:
a template written at one hospital does not exist at another. Moving from a
practice facility to the real one, or standing up a second site, otherwise means
retyping every template and list by hand.

Templates reference their lists by key, not by database id, so copying the keys
into the destination facility is enough for the references to resolve there.

Additive only. Resources present at the destination but absent from the source
are left alone, and nothing is ever deleted.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from care.emr.models.clinical_text import ClinicalTextResource
from care.facility.models import Facility

COPYABLE_KINDS = ("template", "list", "preset", "dictionary")
DEFAULT_KINDS = ("template", "list", "preset")
COPIED_FIELDS = ("label", "description", "status", "payload")


class Command(BaseCommand):
    help = (
        "Copy Smart Text templates, lists and presets from one facility to "
        "another. Additive: never deletes at the destination."
    )

    def add_arguments(self, parser):
        parser.add_argument("--from-facility-name", required=True)
        parser.add_argument("--to-facility-name", required=True)
        parser.add_argument(
            "--user",
            required=True,
            help="CARE superuser recorded as the actor for audit attribution.",
        )
        parser.add_argument(
            "--kinds",
            default=",".join(DEFAULT_KINDS),
            help=(
                "Comma-separated resource kinds to copy. "
                f"Available: {', '.join(COPYABLE_KINDS)}. "
                f"Default: {', '.join(DEFAULT_KINDS)}."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        source = self._load_facility(options["from_facility_name"])
        destination = self._load_facility(options["to_facility_name"])
        if source.pk == destination.pk:
            raise CommandError("Source and destination facilities are the same")

        actor = self._load_actor(options["user"])
        kinds = self._parse_kinds(options["kinds"])
        dry_run = options["dry_run"]

        resources = ClinicalTextResource.objects.filter(
            facility=source, deleted=False, kind__in=kinds
        ).order_by("kind", "key")

        counts = {"created": 0, "updated": 0, "unchanged": 0}
        for resource in resources:
            action = self._copy_resource(resource, destination, actor, dry_run)
            counts[action] += 1
            if action != "unchanged":
                self.stdout.write(f"  {action:9} {resource.kind:11} {resource.key}")

        summary = (
            f"{source.name} -> {destination.name}: "
            f"{counts['created']} created, {counts['updated']} updated, "
            f"{counts['unchanged']} unchanged."
        )
        if dry_run:
            # Roll back so a dry run cannot leave anything behind, even if a
            # later change accidentally writes.
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING(f"DRY RUN — {summary}"))
            self.stdout.write("Nothing was written. Re-run without --dry-run to apply.")
        else:
            self.stdout.write(self.style.SUCCESS(summary))

    @staticmethod
    def _parse_kinds(raw):
        kinds = [kind.strip() for kind in raw.split(",") if kind.strip()]
        unknown = [kind for kind in kinds if kind not in COPYABLE_KINDS]
        if unknown:
            message = (
                f"Unknown kind(s): {', '.join(unknown)}. "
                f"Available: {', '.join(COPYABLE_KINDS)}"
            )
            raise CommandError(message)
        if not kinds:
            raise CommandError("At least one kind must be given")
        return kinds

    @staticmethod
    def _load_facility(name):
        name = (name or "").strip()
        if not name:
            raise CommandError("Facility name cannot be empty")
        matches = list(Facility.objects.filter(name=name))
        if len(matches) != 1:
            message = (
                f"Facility name must match exactly one facility: {name} "
                f"({len(matches)} found)"
            )
            raise CommandError(message)
        return matches[0]

    @staticmethod
    def _load_actor(username):
        actor = get_user_model().objects.filter(username=username).first()
        if actor is None:
            message = f"User not found: {username}"
            raise CommandError(message)
        if not actor.is_superuser:
            raise CommandError("User must be a CARE superuser")
        return actor

    @staticmethod
    def _copy_resource(source_resource, destination, actor, dry_run):
        desired = {field: getattr(source_resource, field) for field in COPIED_FIELDS}
        existing = (
            ClinicalTextResource.objects.select_for_update()
            .filter(
                facility=destination,
                kind=source_resource.kind,
                key=source_resource.key,
                deleted=False,
            )
            .first()
        )

        if existing is None:
            if not dry_run:
                ClinicalTextResource.objects.create(
                    facility=destination,
                    kind=source_resource.kind,
                    key=source_resource.key,
                    version=1,
                    created_by=actor,
                    updated_by=actor,
                    **desired,
                )
            return "created"

        if all(getattr(existing, field) == value for field, value in desired.items()):
            return "unchanged"

        if not dry_run:
            for field, value in desired.items():
                setattr(existing, field, value)
            # Version increments at the destination rather than copying the
            # source version, so the destination's own history stays monotonic.
            existing.version += 1
            existing.updated_by = actor
            existing.save(
                update_fields=[*desired, "version", "updated_by", "modified_date"]
            )
        return "updated"
