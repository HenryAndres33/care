"""Export the clinical catalogue: lab, letter templates, forms, Smart Text."""

from care.emr.models.activity_definition import ActivityDefinition
from care.emr.models.observation_definition import ObservationDefinition
from care.emr.models.questionnaire import Questionnaire, QuestionnaireOrganization
from care.emr.models.report.template import Template
from care.emr.models.resource_category import ResourceCategory
from care_suriname.models.clinical_term_translation import ClinicalTermTranslation
from care_suriname.models.clinical_text import ClinicalTextResource
from care_suriname.resources.facility_setup.format import (
    FacilitySetupError,
    plug_meta,
    slug_value,
)


def export_resource_categories(facility):
    rows = []
    for category in ResourceCategory.objects.filter(facility=facility).select_related(
        "parent"
    ):
        rows.append(
            {
                "slug_value": slug_value(category),
                "resource_type": category.resource_type,
                "resource_sub_type": category.resource_sub_type,
                "title": category.title,
                "description": category.description,
                "configured_monetary_components": (
                    category.configured_monetary_components
                ),
                "parent": slug_value(category.parent) if category.parent else None,
            }
        )
    return sorted(rows, key=lambda row: row["parent"] is not None)


OBSERVATION_FIELDS = (
    "version",
    "title",
    "status",
    "description",
    "derived_from_uri",
    "category",
    "code",
    "permitted_data_type",
    "body_site",
    "method",
    "permitted_unit",
    "component",
    "qualified_ranges",
)


def export_observation_definitions(facility):
    return [
        {
            "slug_value": slug_value(definition),
            **{field: getattr(definition, field) for field in OBSERVATION_FIELDS},
            "meta": plug_meta(definition.meta),
        }
        for definition in ObservationDefinition.objects.filter(facility=facility)
    ]


ACTIVITY_FIELDS = (
    "version",
    "title",
    "classification",
    "derived_from_uri",
    "status",
    "description",
    "usage",
    "kind",
    "code",
    "body_site",
    "diagnostic_report_codes",
)
UNSUPPORTED_ACTIVITY_LINKS = (
    "specimen_requirements",
    "charge_item_definitions",
    "locations",
    "tags",
)


def export_activity_definitions(facility):
    observation_slugs = {
        definition.id: slug_value(definition)
        for definition in ObservationDefinition.objects.filter(facility=facility)
    }
    rows = []
    for activity in ActivityDefinition.objects.filter(
        facility=facility, latest=True
    ).select_related("category", "healthcare_service"):
        linked = [f for f in UNSUPPORTED_ACTIVITY_LINKS if getattr(activity, f)]
        if linked:
            msg = f"Panel {activity.title!r} uses {', '.join(linked)}; not supported."
            raise FacilitySetupError(msg)
        missing = [
            i
            for i in activity.observation_result_requirements
            if i not in observation_slugs
        ]
        if missing:
            msg = f"Panel {activity.title!r} points at tests outside this facility."
            raise FacilitySetupError(msg)
        rows.append(
            {
                "slug_value": slug_value(activity),
                **{field: getattr(activity, field) for field in ACTIVITY_FIELDS},
                "observations": [
                    observation_slugs[i]
                    for i in activity.observation_result_requirements
                ],
                "category": slug_value(activity.category)
                if activity.category
                else None,
                "healthcare_service": activity.healthcare_service.name
                if activity.healthcare_service
                else None,
            }
        )
    return rows


TEMPLATE_FIELDS = (
    "name",
    "status",
    "template_data",
    "template_type",
    "default_format",
    "context",
    "description",
    "options",
)


def export_templates(facility):
    return [
        {
            "slug_value": slug_value(template),
            **{field: getattr(template, field) for field in TEMPLATE_FIELDS},
        }
        for template in Template.objects.filter(facility=facility)
    ]


QUESTIONNAIRE_FIELDS = (
    "version",
    "title",
    "description",
    "subject_type",
    "status",
    "styling_metadata",
    "questions",
)


def export_questionnaires(slugs):
    rows = []
    for slug in slugs:
        questionnaire = Questionnaire.objects.filter(slug=slug).first()
        if questionnaire is None:
            msg = f"Form {slug!r} does not exist."
            raise FacilitySetupError(msg)
        if questionnaire.tags:
            msg = f"Form {slug!r} carries tags; not supported."
            raise FacilitySetupError(msg)
        organizations = QuestionnaireOrganization.objects.filter(
            questionnaire=questionnaire
        ).select_related("organization")
        rows.append(
            {
                "slug": slug,
                **{f: getattr(questionnaire, f) for f in QUESTIONNAIRE_FIELDS},
                "organizations": sorted(
                    [link.organization.org_type, link.organization.name]
                    for link in organizations
                ),
            }
        )
    return rows


def export_clinical_text(facility, excluded_keys):
    """Active Smart Text only; archived items and excluded keys stay behind."""
    excluded = {key.strip().casefold() for key in excluded_keys}
    return [
        {
            "kind": resource.kind,
            "key": resource.key,
            "label": resource.label,
            "description": resource.description,
            "status": resource.status,
            "version": resource.version,
            "payload": resource.payload,
        }
        for resource in ClinicalTextResource.objects.filter(
            facility=facility, status="active"
        ).order_by("kind", "key")
        if resource.key not in excluded
    ]


TRANSLATION_FIELDS = (
    "system",
    "code",
    "language",
    "concept_kind",
    "source_display",
    "preferred_display",
    "synonyms",
    "status",
    "source_name",
    "source_version",
    "is_active",
)


def export_term_translations():
    """Review provenance travels as text; user accounts do not travel."""
    return [
        {
            **{field: getattr(term, field) for field in TRANSLATION_FIELDS},
            "reviewed_at": term.reviewed_at.isoformat() if term.reviewed_at else None,
            "reviewed_by_username": term.reviewed_by.username
            if term.reviewed_by
            else None,
        }
        for term in ClinicalTermTranslation.objects.select_related(
            "reviewed_by"
        ).order_by("system", "code", "language")
    ]
