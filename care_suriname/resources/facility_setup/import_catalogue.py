"""Create the clinical catalogue: lab, letter templates, forms, Smart Text."""

from django.utils.dateparse import parse_datetime

from care.emr.models.activity_definition import ActivityDefinition
from care.emr.models.observation_definition import ObservationDefinition
from care.emr.models.questionnaire import Questionnaire, QuestionnaireOrganization
from care.emr.models.report.template import Template
from care.emr.models.resource_category import ResourceCategory
from care_suriname.models.clinical_term_translation import ClinicalTermTranslation
from care_suriname.models.clinical_text import ClinicalTextResource
from care_suriname.resources.facility_setup.export_catalogue import (
    ACTIVITY_FIELDS,
    OBSERVATION_FIELDS,
    QUESTIONNAIRE_FIELDS,
    TEMPLATE_FIELDS,
    TRANSLATION_FIELDS,
)
from care_suriname.resources.facility_setup.format import (
    PLUG_META_KEY,
    FacilitySetupError,
)
from care_suriname.resources.facility_setup.import_structure import (
    find_or_create_organization,
)


def _slug(model, facility, value):
    """CARE's facility-scoped slug, rebuilt for the new facility id."""
    return model.calculate_slug_from_facility(facility.external_id, value)


def create_resource_categories(rows, facility, admin):
    created = {}
    for row in rows:
        created[row["slug_value"]] = ResourceCategory.objects.create(
            facility=facility,
            slug=_slug(ResourceCategory, facility, row["slug_value"]),
            resource_type=row["resource_type"],
            resource_sub_type=row["resource_sub_type"],
            title=row["title"],
            description=row["description"],
            configured_monetary_components=row["configured_monetary_components"],
            parent=created.get(row["parent"]),
            created_by=admin,
        )
    return created


def create_observation_definitions(rows, facility, admin):
    return {
        row["slug_value"]: ObservationDefinition.objects.create(
            facility=facility,
            slug=_slug(ObservationDefinition, facility, row["slug_value"]),
            meta=row["meta"],
            created_by=admin,
            **{field: row[field] for field in OBSERVATION_FIELDS},
        )
        for row in rows
    }


def create_activity_definitions(rows, facility, context, admin):
    for row in rows:
        ActivityDefinition.objects.create(
            facility=facility,
            slug=_slug(ActivityDefinition, facility, row["slug_value"]),
            observation_result_requirements=[
                context["observations"][slug].id for slug in row["observations"]
            ],
            category=context["categories"].get(row["category"]),
            healthcare_service=context["services"].get(row["healthcare_service"]),
            latest=True,
            created_by=admin,
            **{field: row[field] for field in ACTIVITY_FIELDS},
        )


def create_templates(rows, facility, admin):
    for row in rows:
        Template.objects.create(
            facility=facility,
            slug=_slug(Template, facility, row["slug_value"]),
            created_by=admin,
            **{field: row[field] for field in TEMPLATE_FIELDS},
        )


def create_questionnaires(rows, admin):
    """Forms are instance-wide: an existing slug is a conflict, not a merge."""
    for row in rows:
        if Questionnaire.objects.filter(slug=row["slug"]).exists():
            msg = f"Form {row['slug']!r} already exists on this server."
            raise FacilitySetupError(msg)
        questionnaire = Questionnaire.objects.create(
            slug=row["slug"],
            created_by=admin,
            **{field: row[field] for field in QUESTIONNAIRE_FIELDS},
        )
        for org_type, name in row["organizations"]:
            QuestionnaireOrganization.objects.create(
                questionnaire=questionnaire,
                organization=find_or_create_organization(org_type, name, None, admin),
            )


def create_clinical_text(rows, facility, admin):
    for row in rows:
        ClinicalTextResource.objects.create(
            facility=facility,
            created_by=admin,
            **{
                field: row[field]
                for field in (
                    "kind",
                    "key",
                    "label",
                    "description",
                    "status",
                    "version",
                    "payload",
                )
            },
        )


def create_term_translations(rows, admin):
    """Instance-wide names. The importing admin is recorded as reviewer and
    the original review is kept, in words, in `meta`."""
    created = 0
    for row in rows:
        key = {field: row[field] for field in ("system", "code", "language")}
        existing = ClinicalTermTranslation.objects.filter(**key).first()
        if existing:
            if existing.preferred_display != row["preferred_display"]:
                msg = f"Term {row['code']!r} ({row['language']}) differs here."
                raise FacilitySetupError(msg)
            continue
        reviewed_at = parse_datetime(row["reviewed_at"]) if row["reviewed_at"] else None
        ClinicalTermTranslation.objects.create(
            reviewed_by=admin if reviewed_at else None,
            reviewed_at=reviewed_at,
            created_by=admin,
            meta={
                PLUG_META_KEY: {
                    "imported_review": {
                        "reviewed_by_username": row["reviewed_by_username"],
                        "reviewed_at": row["reviewed_at"],
                    }
                }
            },
            **{field: row[field] for field in TRANSLATION_FIELDS},
        )
        created += 1
    return created
