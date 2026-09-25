"""Build the setup file for one facility. Reads only; never writes."""

from django.db import connection, transaction
from django.utils import timezone

from care_suriname.resources.facility_setup import export_catalogue as catalogue
from care_suriname.resources.facility_setup import export_structure as structure
from care_suriname.resources.facility_setup.format import (
    FORMAT_NAME,
    FORMAT_VERSION,
)


def build_facility_setup(facility, questionnaire_slugs, excluded_text_keys):
    """Everything the facility needs to work, and no patient data.

    Runs in a read-only PostgreSQL transaction, so a mistake here cannot
    change the source database.
    """
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        sections = {
            "facility": structure.export_facility(facility),
            "facility_organizations": structure.export_facility_organizations(facility),
            "roles": structure.export_custom_roles(),
            "identifier_configs": structure.export_identifier_configs(facility),
            "tag_configs": structure.export_tag_configs(facility),
            "locations": structure.export_locations(facility),
            "healthcare_services": structure.export_healthcare_services(facility),
            "resource_categories": catalogue.export_resource_categories(facility),
            "observation_definitions": catalogue.export_observation_definitions(
                facility
            ),
            "activity_definitions": catalogue.export_activity_definitions(facility),
            "templates": catalogue.export_templates(facility),
            "questionnaires": catalogue.export_questionnaires(questionnaire_slugs),
            "clinical_text": catalogue.export_clinical_text(
                facility, excluded_text_keys
            ),
            "term_translations": catalogue.export_term_translations(),
        }
    counts = {
        name: len(value) for name, value in sections.items() if name != "facility"
    }
    return {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "exported_at": timezone.now().isoformat(),
        "source_facility": facility.name,
        "counts": counts,
        **sections,
    }
