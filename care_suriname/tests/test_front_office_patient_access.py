"""Contract for front office under PATIENT_GLOBAL_EDIT_ACCESS_ENABLED.

The Suriname deployment enables this native CARE setting (docker/.local.env,
deploy/.env.example) so the Secretary role can open and edit the administrative
patient record. These tests pin what that does and does not allow.
"""

from django.test import override_settings
from django.urls import reverse
from rest_framework import status

from care.security.permissions.patient import PatientPermissions
from care.utils.tests.base import CareAPITestBase

SECRETARY_PERMISSIONS = [
    PatientPermissions.can_create_patient.name,
    PatientPermissions.can_list_patients.name,
    PatientPermissions.can_write_patient.name,
]


class FrontOfficePatientAccessTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.geo = self.create_organization(org_type="govt")
        self.secretary = self.create_user()
        facility = self.create_facility(user=self.create_user())
        administration = self.create_facility_organization(facility=facility)
        self.attach_role_facility_organization_user(
            administration,
            self.secretary,
            self.create_role_with_permissions(SECRETARY_PERMISSIONS),
        )
        self.patient = self.create_patient(
            name="DEMO-SIM Front Office", geo_organization=self.geo
        )
        self.detail = reverse(
            "patient-detail", kwargs={"external_id": self.patient.external_id}
        )
        self.client.force_authenticate(user=self.secretary)

    def _update(self):
        return self.client.put(
            self.detail,
            {
                "name": "DEMO-SIM Front Office Bijgewerkt",
                "gender": "male",
                "phone_number": "+5978000000",
                "address": "Testweg 1",
                "geo_organization": str(self.geo.external_id),
                "date_of_birth": "1962-06-19",
            },
            format="json",
        )

    @override_settings(PATIENT_GLOBAL_EDIT_ACCESS_ENABLED=False)
    def test_without_the_setting_front_office_cannot_open_the_record(self):
        self.assertIn(
            self.client.get(self.detail).status_code,
            (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
        )

    @override_settings(PATIENT_GLOBAL_EDIT_ACCESS_ENABLED=True)
    def test_front_office_can_open_and_edit_the_administrative_record(self):
        response = self.client.get(self.detail)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn(
            PatientPermissions.can_view_clinical_data.name,
            response.data.get("permissions", []),
        )
        updated = self._update()
        self.assertEqual(updated.status_code, status.HTTP_200_OK, updated.data)
        self.assertEqual(updated.data["name"], "DEMO-SIM Front Office Bijgewerkt")

    @override_settings(PATIENT_GLOBAL_EDIT_ACCESS_ENABLED=True)
    def test_front_office_still_cannot_read_clinical_data(self):
        allergies = reverse(
            "allergy-intolerance-list",
            kwargs={"patient_external_id": self.patient.external_id},
        )
        self.assertEqual(
            self.client.get(allergies).status_code, status.HTTP_403_FORBIDDEN
        )
