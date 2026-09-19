"""Preserve treating-department roles after a completed consultation."""

from django.conf import settings

from care.emr.models import Encounter
from care.emr.resources.encounter.constants import StatusChoices


def completed_department_ids(user, patient):
    organizations = set()
    if settings.PATIENT_DEPARTMENT_LONGITUDINAL_ACCESS_ENABLED:
        completed = Encounter.objects.filter(
            patient=patient, status=StatusChoices.completed.value
        ).values_list("facility_organization_cache", flat=True)
        for department_ids in completed:
            organizations = organizations.union(set(department_ids))
    return organizations
