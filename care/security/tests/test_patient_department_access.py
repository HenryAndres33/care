from django.test import override_settings

from care.emr.resources.encounter.constants import StatusChoices
from care.security.authorization import AuthorizationController
from care.security.permissions.patient import PatientPermissions
from care.utils.tests.base import CareAPITestBase


class PatientDepartmentAccessTest(CareAPITestBase):
    """A department keeps reading a patient it has treated (completed consult)."""

    def setUp(self):
        self.user = self.create_user()
        # Grant actor permissions explicitly; facility creation grants admin.
        self.facility = self.create_facility(user=self.create_user())
        self.department = self.create_facility_organization(facility=self.facility)
        self.other_department = self.create_facility_organization(
            facility=self.facility
        )
        role = self.create_role_with_permissions(
            permissions=[
                PatientPermissions.can_list_patients.name,
                PatientPermissions.can_view_clinical_data.name,
            ]
        )
        self.attach_role_facility_organization_user(self.department, self.user, role)
        self.patient = self.create_patient()

    def can_view(self):
        return AuthorizationController.call(
            "can_view_patient_obj", self.user, self.patient
        )

    def test_no_encounter_gives_no_access(self):
        self.assertFalse(self.can_view())

    def test_completed_encounter_keeps_department_access(self):
        self.create_encounter(
            self.patient,
            self.facility,
            self.department,
            status=StatusChoices.completed.value,
        )
        self.assertTrue(self.can_view())
        self.assertTrue(
            AuthorizationController.call(
                "can_view_clinical_data", self.user, self.patient
            )
        )

    def test_completed_encounter_of_other_department_gives_no_access(self):
        self.create_encounter(
            self.patient,
            self.facility,
            self.other_department,
            status=StatusChoices.completed.value,
        )
        self.assertFalse(self.can_view())

    def test_cancelled_or_erroneous_encounter_gives_no_access(self):
        for status in (
            StatusChoices.cancelled.value,
            StatusChoices.entered_in_error.value,
            StatusChoices.discontinued.value,
        ):
            self.create_encounter(
                self.patient, self.facility, self.department, status=status
            )
        self.assertFalse(self.can_view())

    @override_settings(PATIENT_DEPARTMENT_LONGITUDINAL_ACCESS_ENABLED=False)
    def test_disabled_setting_restores_upstream_behaviour(self):
        self.create_encounter(
            self.patient,
            self.facility,
            self.department,
            status=StatusChoices.completed.value,
        )
        self.assertFalse(self.can_view())
