"""Contract: front office reads the whole dossier but writes no clinical data.

Owner decision 26 September 2026: the Secretary role sees the same dossier as
clinicians, read-only, and edits only patient details and appointments. The
role is given the read permissions below and membership of the department the
patient's encounters belong to (CARE grants patient access through them).
"""

from django.urls import reverse
from rest_framework import status

from care.security.authorization.base import AuthorizationController
from care.security.permissions.encounter import EncounterPermissions
from care.security.permissions.patient import PatientPermissions
from care.security.permissions.questionnaire import QuestionnairePermissions
from care.security.permissions.tag_config import TagConfigPermissions
from care.utils.tests.base import CareAPITestBase

# Secretary today (administrative) plus the read-only clinical set.
FRONT_OFFICE_PERMISSIONS = [
    PatientPermissions.can_create_patient.name,
    PatientPermissions.can_list_patients.name,
    PatientPermissions.can_write_patient.name,
    TagConfigPermissions.can_read_tag_config.name,
    PatientPermissions.can_view_clinical_data.name,
    PatientPermissions.can_view_questionnaire_responses.name,
    EncounterPermissions.can_list_encounter.name,
    EncounterPermissions.can_read_encounter.name,
    QuestionnairePermissions.can_read_questionnaire.name,
]


class FrontOfficeClinicalReadTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.secretary = self.create_user()
        self.facility = self.create_facility(user=self.create_user())
        self.department = self.create_facility_organization(facility=self.facility)
        self.attach_role_facility_organization_user(
            self.department,
            self.secretary,
            self.create_role_with_permissions(FRONT_OFFICE_PERMISSIONS),
        )
        self.patient = self.create_patient(name="DEMO-SIM Front Office Read")
        self.encounter = self.create_encounter(
            patient=self.patient,
            facility=self.facility,
            organization=self.department,
        )
        self.client.force_authenticate(user=self.secretary)
        self.base = f"/api/v1/patient/{self.patient.external_id}"

    def test_front_office_reads_every_dashboard_section(self):
        reads = {
            "patient": (
                reverse(
                    "patient-detail",
                    kwargs={"external_id": self.patient.external_id},
                ),
                {"facility": str(self.facility.external_id)},
            ),
            "encounters": (
                "/api/v1/encounter/",
                {
                    "patient": str(self.patient.external_id),
                    "facility": str(self.facility.external_id),
                },
            ),
            "allergies": (f"{self.base}/allergy_intolerance/", {}),
            "diagnoses": (f"{self.base}/diagnosis/", {}),
            "medication requests": (f"{self.base}/medication/request/", {}),
            "medication statements": (f"{self.base}/medication/statement/", {}),
            "lab reports": (f"{self.base}/diagnostic_report/", {}),
        }
        for name, (url, params) in reads.items():
            with self.subTest(name):
                response = self.client.get(url, params)
                self.assertEqual(response.status_code, status.HTTP_200_OK, name)

    def test_front_office_may_not_write_clinical_data(self):
        # Every clinical write endpoint (diagnosis, medication, notes, lab,
        # letters, discharge) relies on one of these decisions.
        for check in (
            "can_update_encounter_clinical_data",
            "can_update_encounter_obj",
        ):
            with self.subTest(check):
                self.assertFalse(
                    AuthorizationController.call(check, self.secretary, self.encounter)
                )
