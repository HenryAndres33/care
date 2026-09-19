from django.urls import reverse
from model_bakery import baker

from care.emr.models import Condition
from care.emr.resources.condition.spec import CategoryChoices
from care.security.permissions.patient import PatientPermissions
from care.utils.tests.base import CareAPITestBase


class TestDiagnosisUpdateAuthorizationRegression(CareAPITestBase):
    def test_read_only_user_cannot_update_chronic_condition(self):
        user = self.create_user()
        # The actor must not inherit the facility creator's admin role.
        facility = self.create_facility(user=self.create_user())
        organization = self.create_facility_organization(facility=facility)
        patient = self.create_patient()
        encounter = self.create_encounter(
            patient=patient,
            facility=facility,
            organization=organization,
            status=None,
        )
        role = self.create_role_with_permissions(
            [PatientPermissions.can_view_clinical_data.name]
        )
        self.attach_role_facility_organization_user(organization, user, role)
        diagnosis = baker.make(
            Condition,
            patient=patient,
            encounter=encounter,
            category=CategoryChoices.chronic_condition.value,
            clinical_status="active",
            verification_status="confirmed",
            code={
                "system": "http://test_system.care/test",
                "code": "HTN-1",
                "display": "Hypertension",
            },
        )
        self.client.force_authenticate(user=user)
        url = reverse(
            "diagnosis-detail",
            kwargs={
                "patient_external_id": patient.external_id,
                "external_id": diagnosis.external_id,
            },
        )

        response = self.client.put(
            url,
            {
                "clinical_status": "resolved",
                "verification_status": "confirmed",
                "severity": None,
                "code": diagnosis.code,
                "onset": {},
                "abatement": {},
                "note": "",
                "clinical_domain": "general",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)
