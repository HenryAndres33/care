import uuid

from django.urls import reverse
from model_bakery import baker

from care.emr.models import Condition
from care.emr.resources.condition.spec import CategoryChoices
from care.security.permissions.encounter import EncounterPermissions
from care.security.permissions.patient import PatientPermissions
from care.utils.tests.base import CareAPITestBase


class TestIdempotentDiagnosisCreate(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.facility = self.create_facility(user=self.user)
        self.organization = self.create_facility_organization(facility=self.facility)
        self.patient = self.create_patient()
        self.encounter = self.create_encounter(
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
            status=None,
        )
        permissions = [
            EncounterPermissions.can_write_encounter_clinical_data.name,
            PatientPermissions.can_view_clinical_data.name,
        ]
        role = self.create_role_with_permissions(permissions)
        self.attach_role_facility_organization_user(self.organization, self.user, role)
        self.client.force_authenticate(user=self.user)
        self.url = reverse(
            "diagnosis-idempotent-create",
            kwargs={"patient_external_id": self.patient.external_id},
        )

    def payload(self, request_id=None, **overrides):
        payload = {
            "client_request_id": request_id or uuid.uuid4(),
            "encounter": self.encounter.external_id,
            "category": "chronic_condition",
            "clinical_status": "active",
            "verification_status": "confirmed",
            "severity": None,
            "code": {
                "display": "Benign prostatic hyperplasia",
                "system": "http://test_system.care/test",
                "code": "BPH-1",
            },
            "onset": {},
            "abatement": {},
            "note": "",
            "clinical_domain": "urology",
        }
        payload.update(overrides)
        return payload

    def test_exact_replay_creates_one_diagnosis(self):
        request_id = uuid.uuid4()
        first = self.client.post(self.url, self.payload(request_id), format="json")
        replay = self.client.post(self.url, self.payload(request_id), format="json")

        self.assertEqual(first.status_code, 201, first.json())
        self.assertEqual(replay.status_code, 200)
        self.assertFalse(first.json()["replayed"])
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(
            first.json()["diagnosis"]["id"], replay.json()["diagnosis"]["id"]
        )
        self.assertEqual(
            Condition.objects.filter(client_request_id=request_id).count(), 1
        )
        self.assertEqual(first.json()["diagnosis"]["clinical_domain"], "urology")

    def test_changed_replay_and_active_duplicate_are_rejected(self):
        request_id = uuid.uuid4()
        first = self.client.post(self.url, self.payload(request_id), format="json")
        changed = self.client.post(
            self.url,
            self.payload(request_id, note="different"),
            format="json",
        )
        duplicate = self.client.post(self.url, self.payload(), format="json")

        self.assertEqual(first.status_code, 201, first.json())
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(changed.json()["errors"][0]["type"], "idempotency_conflict")
        self.assertEqual(duplicate.json()["errors"][0]["type"], "duplicate_diagnosis")
        self.assertEqual(
            Condition.objects.filter(
                patient=self.patient,
                code__system="http://test_system.care/test",
                code__code="BPH-1",
            ).count(),
            1,
        )


class TestDiagnosisUpdateAuthorizationRegression(CareAPITestBase):
    def test_read_only_user_cannot_update_chronic_condition(self):
        user = self.create_user()
        facility = self.create_facility(user=user)
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
