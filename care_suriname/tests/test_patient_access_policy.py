"""Completed-department scope preserves native membership and permission checks."""

from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from care.emr.models.organization import FacilityOrganizationUser
from care.emr.models.patient import Patient, PatientUser
from care.emr.resources.encounter.constants import StatusChoices
from care.emr.resources.permissions import PatientPermissionsMixin
from care.security.authorization.patient import PatientAccess
from care.security.permissions.patient import PatientPermissions
from care.security.tests.test_patient_department_access import (
    PatientDepartmentAccessTest,
)
from care_suriname.contributions import patient_organizations
from care_suriname.policies.patient_access import completed_department_ids
from plugs.contributions import single


class PatientAccessRegistrationTests(SimpleTestCase):
    def test_provider_registered_at_startup_without_url_import(self):
        self.assertIs(single("patient_organization_ids", None), patient_organizations)


class PatientAccessPolicyTests(PatientDepartmentAccessTest):
    def setUp(self):
        super().setUp()
        # Facility creation grants its creator a root role. Remove only that
        # fixture membership so these tests exercise a department-only clinician.
        FacilityOrganizationUser.objects.filter(
            user=self.user,
            organization_id=self.facility.default_internal_organization_id,
        ).delete()

    def completed(self, department=None, facility=None):
        return self.create_encounter(
            self.patient,
            facility or self.facility,
            department or self.department,
            status=StatusChoices.completed.value,
        )

    def test_same_department_role_reaches_direct_permission_serialization(self):
        self.completed()
        mapping = {}
        PatientPermissionsMixin.add_permissions(mapping, self.user, self.patient)
        self.assertIn(
            PatientPermissions.can_view_clinical_data.name, mapping["permissions"]
        )
        self.assertIn(PatientPermissions.can_list_patients.name, mapping["permissions"])
        self.assertFalse(PatientAccess().can_write_patient_obj(self.user, self.patient))

    def test_unrelated_user_and_facility_do_not_gain_access(self):
        self.completed()
        other = self.create_user()
        self.assertFalse(PatientAccess().can_view_patient_obj(other, self.patient))
        other_patient = self.create_patient()
        other_facility = self.create_facility(user=other)
        other_department = self.create_facility_organization(facility=other_facility)
        self.create_encounter(
            other_patient, other_facility, other_department, status="completed"
        )
        self.assertFalse(PatientAccess().can_view_patient_obj(self.user, other_patient))

    def test_scope_without_membership_or_permission_never_grants_access(self):
        self.completed(self.other_department)
        self.assertFalse(self.can_view())
        role = self.create_role_with_permissions(permissions=[])
        self.attach_role_facility_organization_user(
            self.other_department, self.user, role
        )
        self.assertFalse(self.can_view())

    def test_absence_keeps_native_active_and_direct_access(self):
        self.completed()
        with patch("plugs.contributions.manager.get_apps", return_value=[]):
            self.assertFalse(self.can_view())
            encounter = self.create_encounter(
                self.patient, self.facility, self.department, status="in_progress"
            )
            self.assertTrue(self.can_view())
            encounter.deleted = True
            encounter.save(update_fields=["deleted"])
            self.assertFalse(self.can_view())
            role = self.create_role_with_permissions(
                permissions=[PatientPermissions.can_list_patients.name]
            )
            PatientUser.objects.create(patient=self.patient, user=self.user, role=role)
            self.assertTrue(self.can_view())

    def test_native_write_permission_is_neither_added_nor_removed(self):
        self.completed()
        self.assertFalse(PatientAccess().can_write_patient_obj(self.user, self.patient))
        role = self.create_role_with_permissions(
            permissions=[PatientPermissions.can_write_patient.name]
        )
        self.attach_role_facility_organization_user(self.department, self.user, role)
        self.assertTrue(PatientAccess().can_write_patient_obj(self.user, self.patient))

    def test_native_list_scope_is_not_widened(self):
        self.completed()
        access = PatientAccess()
        before = list(
            access.get_filtered_patients(Patient.objects.all(), self.user).values_list(
                "id", flat=True
            )
        )
        with patch("plugs.contributions.manager.get_apps", return_value=[]):
            after = list(
                access.get_filtered_patients(
                    Patient.objects.all(), self.user
                ).values_list("id", flat=True)
            )
        self.assertEqual(before, after)
        self.assertNotIn(self.patient.id, before)

    def test_patient_retrieve_allows_same_department_and_denies_unrelated(self):
        self.completed()
        self.client.force_authenticate(self.user)
        url = reverse(
            "patient-detail", kwargs={"external_id": self.patient.external_id}
        )
        self.assertEqual(self.client.get(url).status_code, 200)
        unrelated = self.create_user()
        self.client.force_authenticate(unrelated)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_only_completed_adds_scope_and_uses_one_query(self):
        expected = set()
        for status in StatusChoices:
            department = self.create_facility_organization(facility=self.facility)
            encounter = self.create_encounter(
                self.patient, self.facility, department, status=status.value
            )
            if status == StatusChoices.completed:
                expected = set(encounter.facility_organization_cache)
        with self.assertNumQueries(1):
            self.assertEqual(
                completed_department_ids(self.user, self.patient), expected
            )

    @override_settings(PATIENT_DEPARTMENT_LONGITUDINAL_ACCESS_ENABLED=False)
    def test_disabled_policy_performs_no_query(self):
        with self.assertNumQueries(0):
            self.assertEqual(completed_department_ids(self.user, self.patient), set())
