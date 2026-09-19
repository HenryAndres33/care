import uuid

from django.urls import reverse

from care.emr.models import Condition
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

    def test_invalid_domain_and_patient_binding_leave_no_command(self):
        invalid = self.client.post(
            self.url, self.payload(clinical_domain="other"), format="json"
        )
        self.assertEqual(invalid.status_code, 400)
        other_patient = self.create_patient()
        other_url = reverse(
            "diagnosis-idempotent-create",
            kwargs={"patient_external_id": other_patient.external_id},
        )
        mismatch = self.client.post(other_url, self.payload(), format="json")
        self.assertEqual(mismatch.status_code, 400)
        self.assertEqual(Condition.objects.filter(patient=self.patient).count(), 0)

    def test_replay_actor_and_deleted_record_conflicts(self):
        payload = self.payload()
        first = self.client.post(self.url, payload, format="json")
        self.assertEqual(first.status_code, 201)
        self.client.force_authenticate(user=self.create_user())
        other_actor = self.client.post(self.url, payload, format="json")
        self.assertEqual(other_actor.status_code, 409)
        self.assertEqual(
            other_actor.json()["errors"][0]["type"], "idempotency_conflict"
        )
        self.client.force_authenticate(user=self.user)
        Condition.objects.filter(client_request_id=payload["client_request_id"]).update(
            deleted=True
        )
        deleted = self.client.post(self.url, payload, format="json")
        self.assertEqual(deleted.status_code, 409)

    def test_closed_encounter_rejects_new_command_but_preserves_exact_replay(self):
        payload = self.payload()
        first = self.client.post(self.url, payload, format="json")
        self.assertEqual(first.status_code, 201)
        self.encounter.status = "completed"
        self.encounter.save(update_fields=["status"])
        replay = self.client.post(self.url, payload, format="json")
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()["replayed"])
        denied = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(denied.status_code, 403)

    def test_permission_denial_rolls_back_and_unauthenticated_request_is_rejected(self):
        from unittest.mock import patch

        from care.security.authorization import AuthorizationController

        with patch.object(AuthorizationController, "call", return_value=False):
            denied = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(Condition.objects.filter(patient=self.patient).count(), 0)
        self.client.force_authenticate(user=None)
        response = self.client.post(self.url, self.payload(), format="json")
        self.assertIn(response.status_code, [401, 403])

    def test_failure_after_native_save_rolls_back_condition_and_audit_response(self):
        from unittest.mock import patch

        from care.emr.api.viewsets.condition import DiagnosisViewSet
        from care.emr.models.questionnaire import QuestionnaireResponse

        original = DiagnosisViewSet.perform_create

        def fail_after_save(view, instance):
            original(view, instance)
            raise RuntimeError("Synthetic command rollback")

        count = QuestionnaireResponse.objects.filter(patient=self.patient).count()
        with (
            patch.object(DiagnosisViewSet, "perform_create", fail_after_save),
            self.assertRaisesMessage(RuntimeError, "Synthetic command rollback"),
        ):
            self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(Condition.objects.filter(patient=self.patient).count(), 0)
        self.assertEqual(
            QuestionnaireResponse.objects.filter(patient=self.patient).count(), count
        )
