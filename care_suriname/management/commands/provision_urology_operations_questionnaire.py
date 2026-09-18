import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from care.emr.models.organization import Organization
from care.emr.models.questionnaire import Questionnaire, QuestionnaireOrganization

QUESTIONNAIRE_SLUG = "urology-operaties"
NARRATIVE_LINK_ID = "urology-narrative"
NARRATIVE_QUESTION_ID = uuid.UUID("47d4caf1-97eb-4e88-83e3-0e16769c2b58")


def _questions():
    return [
        {
            "id": str(NARRATIVE_QUESTION_ID),
            "link_id": NARRATIVE_LINK_ID,
            "text": "Operatieverslag",
            "description": "Leesbaar, definitief operatieverslag.",
            "type": "text",
            "required": True,
            "repeats": False,
            "read_only": False,
            "questions": [],
            "styling_metadata": {},
            "templates": [],
            "is_component": False,
        }
    ]


def _is_compatible(questionnaire):
    return (
        questionnaire.status == "active"
        and questionnaire.subject_type == "encounter"
        and any(
            question.get("link_id") == NARRATIVE_LINK_ID
            and question.get("type") in {"string", "text"}
            for question in questionnaire.questions
            if isinstance(question, dict)
        )
    )


class Command(BaseCommand):
    help = (
        "Provision the active encounter-owned urology operations questionnaire "
        "for one or more CARE organizations."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--organization",
            action="append",
            dest="organizations",
            required=True,
            help="External organization UUID. Repeat for multiple organizations.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        organization_ids = []
        for raw_id in options["organizations"]:
            try:
                organization_ids.append(uuid.UUID(raw_id))
            except (TypeError, ValueError) as error:
                message = f"Invalid organization UUID: {raw_id}"
                raise CommandError(message) from error

        organizations = list(
            Organization.objects.filter(external_id__in=organization_ids)
        )
        found_ids = {organization.external_id for organization in organizations}
        missing = [
            str(organization_id)
            for organization_id in organization_ids
            if organization_id not in found_ids
        ]
        if missing:
            message = f"Organization(s) not found: {', '.join(missing)}"
            raise CommandError(message)

        questionnaire = Questionnaire.objects.filter(slug=QUESTIONNAIRE_SLUG).first()
        created = questionnaire is None
        if questionnaire is None:
            questionnaire = Questionnaire.objects.create(
                version="1.0",
                slug=QUESTIONNAIRE_SLUG,
                title="Urologie operatieverslag",
                description=(
                    "Encounter-owned digitaal operatieverslag voor de urologieworkflow."
                ),
                subject_type="encounter",
                status="active",
                styling_metadata={},
                questions=_questions(),
            )
        elif not _is_compatible(questionnaire):
            raise CommandError(
                "Existing urology-operaties questionnaire is incompatible; "
                "no data was changed."
            )

        linked = 0
        for organization in organizations:
            _, association_created = QuestionnaireOrganization.objects.get_or_create(
                questionnaire=questionnaire,
                organization=organization,
            )
            linked += int(association_created)

        action = "created" if created else "verified"
        self.stdout.write(
            self.style.SUCCESS(
                f"{QUESTIONNAIRE_SLUG} {action}; "
                f"{linked} organization association(s) added."
            )
        )
