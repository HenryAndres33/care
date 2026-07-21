from django.conf import settings
from rest_framework.exceptions import APIException


class WorkflowCapabilityDisabled(APIException):
    status_code = 503
    default_code = "workflow_capability_disabled"

    def __init__(self, blocker_code):
        super().__init__(detail=blocker_code, code=blocker_code)


def _facility_enabled(setting_name, facility_external_id):
    configured = getattr(settings, setting_name, [])
    facility_id = str(facility_external_id).lower()
    normalized = {str(value).lower() for value in configured}
    return "*" in normalized or facility_id in normalized


def workflow_mutations_enabled(facility_external_id):
    return _facility_enabled(
        "CLINICAL_WORKFLOW_MUTATIONS_ENABLED_FACILITIES",
        facility_external_id,
    )


def correspondence_delivery_enabled(facility_external_id):
    return _facility_enabled(
        "CORRESPONDENCE_DELIVERY_ENABLED_FACILITIES",
        facility_external_id,
    )


def require_workflow_mutations_enabled(facility_external_id):
    if not workflow_mutations_enabled(facility_external_id):
        raise WorkflowCapabilityDisabled("workflow_mutations_disabled")


def require_correspondence_delivery_enabled(facility_external_id):
    require_workflow_mutations_enabled(facility_external_id)
    if not correspondence_delivery_enabled(facility_external_id):
        raise WorkflowCapabilityDisabled("correspondence_delivery_disabled")
