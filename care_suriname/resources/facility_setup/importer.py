"""Create a new facility from a setup file, all or nothing."""

from django.db import transaction

from care_suriname.resources.facility_setup import import_catalogue as catalogue
from care_suriname.resources.facility_setup import import_structure as structure
from care_suriname.resources.facility_setup.format import (
    FACILITY_ROOT,
    FORMAT_NAME,
    FORMAT_VERSION,
    FacilitySetupError,
)


def check_format(data):
    if data.get("format") != FORMAT_NAME or data.get("version") != FORMAT_VERSION:
        msg = "This is not a facility-setup file this version can read."
        raise FacilitySetupError(msg)


@transaction.atomic
def import_facility_setup(data, admin, facility_name=None):
    """Add one facility and its setup. Never changes or deletes existing rows;
    any conflict raises and rolls the whole import back."""
    check_format(data)
    name = facility_name or data["facility"]["name"]
    facility = structure.create_facility(data["facility"], name, admin)
    organizations = structure.create_facility_organizations(
        data["facility_organizations"], facility, admin
    )
    organizations[FACILITY_ROOT] = facility.default_internal_organization
    structure.create_roles(data["roles"])
    structure.create_identifier_configs(data["identifier_configs"], facility, admin)
    structure.create_tag_configs(data["tag_configs"], facility, organizations, admin)
    locations = structure.create_locations(
        data["locations"], facility, organizations, admin
    )
    services = structure.create_healthcare_services(
        data["healthcare_services"], facility, organizations, locations, admin
    )
    context = {
        "categories": catalogue.create_resource_categories(
            data["resource_categories"], facility, admin
        ),
        "observations": catalogue.create_observation_definitions(
            data["observation_definitions"], facility, admin
        ),
        "services": services,
    }
    catalogue.create_activity_definitions(
        data["activity_definitions"], facility, context, admin
    )
    catalogue.create_templates(data["templates"], facility, admin)
    catalogue.create_questionnaires(data["questionnaires"], admin)
    catalogue.create_clinical_text(data["clinical_text"], facility, admin)
    translations = catalogue.create_term_translations(data["term_translations"], admin)
    return {
        "facility": facility,
        "counts": {**data["counts"], "term_translations_created": translations},
    }
