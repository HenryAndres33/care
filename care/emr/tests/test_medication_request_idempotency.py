import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from unittest.mock import patch
from uuid import uuid1, uuid4

from django.core.cache import cache
from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from django.urls import reverse
from model_bakery import baker
from rest_framework import status
from rest_framework.test import APIClient

from care.emr.api.viewsets import medication_request as medication_request_viewset
from care.emr.models.medication_request import (
    MedicationRequest,
    MedicationRequestPrescription,
)
from care.emr.models.questionnaire import FormSubmission, QuestionnaireResponse
from care.emr.resources.form_submission.spec import FormSubmissionStatusChoices
from care.emr.signals.patient.facility_name_identifier import (
    FacilityPatientNameIdentifierConfig,
)
from care.emr.signals.patient.name_identifier import NameIdentifierConfig
from care.emr.signals.patient.phone_number_identifier import (
    PhoneNumberIdentifierConfig,
)
from care.security.permissions.encounter import EncounterPermissions
from care.security.permissions.patient import PatientPermissions
from care.utils.tests.base import CareAPITestBase
from care_suriname.api.viewsets import medication_commands


class TestMedicationRequestIdempotencyApi(CareAPITestBase):
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
        )
        self.permissions = [
            PatientPermissions.can_view_clinical_data.name,
            EncounterPermissions.can_read_encounter_clinical_data.name,
            EncounterPermissions.can_write_encounter_clinical_data.name,
        ]
        self.role = self.create_role_with_permissions(self.permissions)
        self.user_role = self.attach_role_facility_organization_user(
            self.organization, self.user, self.role
        )
        self.client.force_authenticate(user=self.user)
        self.url = self._url(self.patient)
        self.reconcile_url = self._reconcile_url(self.patient)
        self.valid_code = {
            "display": "Synthetic medication",
            "system": "http://test-system.invalid/medication",
            "code": "synthetic-code",
        }

    def _url(self, patient):
        return reverse(
            "medication-request-idempotent-create",
            kwargs={"patient_external_id": patient.external_id},
        )

    def _reconcile_url(self, patient):
        return reverse(
            "medication-request-idempotent-reconcile",
            kwargs={"patient_external_id": patient.external_id},
        )

    def _payload(self, **overrides):
        data = {
            "client_request_id": str(uuid4()),
            "status": "active",
            "intent": "order",
            "category": "outpatient",
            "priority": "routine",
            "do_not_perform": False,
            "medication": self.valid_code,
            "dosage_instruction": [],
            "authored_on": "2026-07-20T10:00:00Z",
            "encounter": str(self.encounter.external_id),
            "requester": str(self.user.external_id),
        }
        data.update(overrides)
        return data

    def test_first_create_and_identical_replay(self):
        payload = self._payload()

        created = self.client.post(self.url, payload, format="json")
        replayed = self.client.post(self.url, payload, format="json")

        self.assertEqual(created.status_code, 201)
        self.assertFalse(created.json()["replayed"])
        self.assertEqual(replayed.status_code, 200)
        self.assertTrue(replayed.json()["replayed"])
        self.assertEqual(
            created.json()["client_request_id"], payload["client_request_id"]
        )
        self.assertEqual(
            created.json()["medication_request"]["id"],
            replayed.json()["medication_request"]["id"],
        )
        self.assertEqual(MedicationRequest.objects.count(), 1)
        self.assertEqual(QuestionnaireResponse.objects.count(), 1)

    def test_canonical_hash_uses_validated_payload(self):
        payload = self._payload(requester=None)
        created = self.client.post(self.url, payload, format="json")
        reordered = copy.deepcopy(payload)
        reordered["medication"] = {
            "code": self.valid_code["code"],
            "display": self.valid_code["display"],
            "system": self.valid_code["system"],
        }
        reordered["authored_on"] = "2026-07-20T12:00:00+02:00"

        replayed = self.client.post(self.url, reordered, format="json")

        self.assertEqual(created.status_code, 201)
        self.assertEqual(replayed.status_code, 200)
        self.assertTrue(replayed.json()["replayed"])
        medication_request = MedicationRequest.objects.get()
        self.assertEqual(len(medication_request.client_request_payload_hash), 64)

    def test_same_key_with_different_payload_returns_non_leaking_conflict(self):
        payload = self._payload()
        created = self.client.post(self.url, payload, format="json")
        changed = {**payload, "note": "Different synthetic instruction"}

        conflict = self.client.post(self.url, changed, format="json")

        self.assertEqual(created.status_code, 201)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(
            conflict.json(),
            {
                "errors": [
                    {
                        "type": "idempotency_conflict",
                        "msg": (
                            "client_request_id was already used with different "
                            "request data or context"
                        ),
                    }
                ]
            },
        )
        self.assertNotIn(
            created.json()["medication_request"]["id"], conflict.content.decode()
        )
        self.assertNotIn(self.valid_code["display"], conflict.content.decode())
        self.assertEqual(MedicationRequest.objects.count(), 1)

    def test_same_key_from_another_authorized_actor_is_a_context_conflict(self):
        payload = self._payload(requester=None)
        self.assertEqual(
            self.client.post(self.url, payload, format="json").status_code, 201
        )
        other_user = self.create_user()
        self.attach_role_facility_organization_user(
            self.organization, other_user, self.role
        )
        self.client.force_authenticate(user=other_user)

        conflict = self.client.post(self.url, payload, format="json")

        self.assertEqual(conflict.status_code, 409)
        self.assertNotIn("medication_request", conflict.json())

    def test_replay_requires_current_patient_and_encounter_authorization(self):
        payload = self._payload()
        self.assertEqual(
            self.client.post(self.url, payload, format="json").status_code, 201
        )
        self.user_role.delete()

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("medication_request", response.json())

    def test_exact_replay_after_encounter_closure_needs_read_not_write_access(self):
        payload = self._payload()
        created = self.client.post(self.url, payload, format="json")
        self.encounter.status = "completed"
        self.encounter.save(update_fields=["status"])

        replayed = self.client.post(self.url, payload, format="json")
        new_create = self.client.post(self.url, self._payload(), format="json")

        self.assertEqual(created.status_code, 201)
        self.assertEqual(replayed.status_code, 200)
        self.assertTrue(replayed.json()["replayed"])
        self.assertEqual(new_create.status_code, 403)
        self.assertEqual(MedicationRequest.objects.count(), 1)
        self.assertEqual(QuestionnaireResponse.objects.count(), 1)

    def test_same_key_in_another_patient_context_is_a_non_leaking_conflict(self):
        payload = self._payload(requester=None)
        created = self.client.post(self.url, payload, format="json")
        other_patient = self.create_patient()
        other_encounter = self.create_encounter(
            patient=other_patient,
            facility=self.facility,
            organization=self.organization,
        )
        changed_context = {
            **payload,
            "encounter": str(other_encounter.external_id),
        }

        conflict = self.client.post(
            self._url(other_patient), changed_context, format="json"
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(conflict.status_code, 409)
        self.assertNotIn(
            created.json()["medication_request"]["id"], conflict.content.decode()
        )

    def test_route_patient_must_match_encounter_patient(self):
        other_patient = self.create_patient()
        other_encounter = self.create_encounter(
            patient=other_patient,
            facility=self.facility,
            organization=self.organization,
        )
        payload = self._payload(encounter=str(other_encounter.external_id))

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(MedicationRequest.objects.count(), 0)

    def test_actor_requester_and_session_are_authorized(self):
        payload = self._payload()
        unauthorized_user = self.create_user()
        self.client.force_authenticate(user=unauthorized_user)
        self.assertEqual(
            self.client.post(self.url, payload, format="json").status_code, 403
        )

        self.client.force_authenticate(user=self.user)
        unauthorized_requester = self.create_user()
        requester_payload = self._payload(
            requester=str(unauthorized_requester.external_id)
        )
        self.assertEqual(
            self.client.post(self.url, requester_payload, format="json").status_code,
            403,
        )

        self.client.force_authenticate(user=None)
        self.assertEqual(
            self.client.post(self.url, self._payload(), format="json").status_code,
            403,
        )
        self.assertEqual(MedicationRequest.objects.count(), 0)

    def test_client_request_id_is_required_uuid_v4(self):
        missing = self._payload()
        del missing["client_request_id"]
        wrong_version = self._payload(client_request_id=str(uuid1()))

        self.assertEqual(
            self.client.post(self.url, missing, format="json").status_code, 400
        )
        self.assertEqual(
            self.client.post(self.url, wrong_version, format="json").status_code, 400
        )
        self.assertEqual(MedicationRequest.objects.count(), 0)

    def test_dedicated_commands_reject_unknown_fields_without_side_effects(self):
        payload = self._payload(medciation="misspelled-field")

        create_response = self.client.post(self.url, payload, format="json")
        reconcile_response = self.client.post(
            self.reconcile_url, payload, format="json"
        )

        self.assertEqual(create_response.status_code, 400)
        self.assertEqual(reconcile_response.status_code, 400)
        self.assertEqual(MedicationRequest._base_manager.count(), 0)  # noqa: SLF001
        self.assertEqual(MedicationRequestPrescription.objects.count(), 0)
        self.assertEqual(QuestionnaireResponse.objects.count(), 0)

    def test_soft_deleted_target_fails_closed_and_keeps_key_reserved(self):
        payload = self._payload()
        created = self.client.post(self.url, payload, format="json")
        medication_request = MedicationRequest.objects.get()
        medication_request.delete()

        identical = self.client.post(self.url, payload, format="json")
        changed = self.client.post(
            self.url,
            {**payload, "priority": "urgent"},
            format="json",
        )

        self.assertEqual(identical.status_code, 409)
        self.assertEqual(changed.status_code, 409)
        self.assertNotIn("medication_request", identical.json())
        self.assertNotIn("medication_request", changed.json())
        self.assertNotIn(
            created.json()["medication_request"]["id"], identical.content.decode()
        )
        self.assertNotIn(
            created.json()["medication_request"]["id"], changed.content.decode()
        )
        self.assertEqual(MedicationRequest.objects.count(), 0)
        self.assertEqual(MedicationRequest._base_manager.count(), 1)  # noqa: SLF001

    def test_medication_prescription_and_questionnaire_side_effects_are_atomic(self):
        payload = self._payload(
            create_prescription={
                "alternate_identifier": "synthetic-prescription",
                "name": "Synthetic prescription",
            }
        )

        with (
            self.assertLogs("django.request", level="ERROR") as request_logs,
            patch(
                "care.emr.api.viewsets.base.QuestionnaireResponse.objects.create",
                side_effect=RuntimeError("synthetic questionnaire failure"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.client.post(self.url, payload, format="json")

        self.assertEqual(MedicationRequest._base_manager.count(), 0)  # noqa: SLF001
        self.assertEqual(MedicationRequestPrescription.objects.count(), 0)
        self.assertEqual(QuestionnaireResponse.objects.count(), 0)
        log_output = "\n".join(request_logs.output)
        self.assertNotIn(self.valid_code["display"], log_output)
        self.assertNotIn(self.valid_code["code"], log_output)

        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(MedicationRequest.objects.count(), 1)
        self.assertEqual(MedicationRequestPrescription.objects.count(), 1)
        self.assertEqual(QuestionnaireResponse.objects.count(), 1)

    def test_form_submission_is_validated_linked_and_hashed(self):
        questionnaire = baker.make("emr.Questionnaire")
        form_submission = baker.make(
            FormSubmission,
            questionnaire=questionnaire,
            patient=self.patient,
            encounter=self.encounter,
            status=FormSubmissionStatusChoices.draft.value,
        )
        other_form_submission = baker.make(
            FormSubmission,
            questionnaire=questionnaire,
            patient=self.patient,
            encounter=self.encounter,
            status=FormSubmissionStatusChoices.draft.value,
        )
        payload = self._payload(form_submission=str(form_submission.external_id))

        created = self.client.post(self.url, payload, format="json")
        conflict = self.client.post(
            self.url,
            {
                **payload,
                "form_submission": str(other_form_submission.external_id),
            },
            format="json",
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(conflict.status_code, 409)
        questionnaire_response = QuestionnaireResponse.objects.get()
        self.assertEqual(questionnaire_response.form_submission, form_submission)

    def test_reconcile_returns_exact_existing_resource_without_creating(self):
        payload = self._payload()
        created = self.client.post(self.url, payload, format="json")

        reconciled = self.client.post(self.reconcile_url, payload, format="json")

        self.assertEqual(created.status_code, 201)
        self.assertEqual(reconciled.status_code, 200)
        self.assertEqual(
            reconciled.json(),
            {
                "client_request_id": payload["client_request_id"],
                "medication_request": created.json()["medication_request"],
                "replayed": True,
            },
        )
        self.assertEqual(MedicationRequest.objects.count(), 1)
        self.assertEqual(QuestionnaireResponse.objects.count(), 1)

    def test_reconcile_missing_key_returns_404_without_side_effects(self):
        response = self.client.post(self.reconcile_url, self._payload(), format="json")

        self.assertEqual(response.status_code, 404)
        self.assertNotIn("medication_request", response.json())
        self.assertNotIn(self.valid_code["display"], response.content.decode())
        self.assertEqual(MedicationRequest._base_manager.count(), 0)  # noqa: SLF001
        self.assertEqual(MedicationRequestPrescription.objects.count(), 0)
        self.assertEqual(QuestionnaireResponse.objects.count(), 0)

    def test_reconcile_conflict_does_not_leak_existing_resource(self):
        payload = self._payload()
        created = self.client.post(self.url, payload, format="json")

        conflict = self.client.post(
            self.reconcile_url,
            {**payload, "priority": "urgent"},
            format="json",
        )

        self.assertEqual(conflict.status_code, 409)
        self.assertNotIn("medication_request", conflict.json())
        self.assertNotIn(
            created.json()["medication_request"]["id"], conflict.content.decode()
        )
        self.assertEqual(MedicationRequest.objects.count(), 1)
        self.assertEqual(QuestionnaireResponse.objects.count(), 1)

    def test_reconcile_rejects_wrong_route_patient_without_resource_leakage(self):
        payload = self._payload()
        created = self.client.post(self.url, payload, format="json")
        other_patient = self.create_patient()

        response = self.client.post(
            self._reconcile_url(other_patient), payload, format="json"
        )

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("medication_request", response.json())
        self.assertNotIn(
            created.json()["medication_request"]["id"], response.content.decode()
        )

    def test_reconcile_deleted_target_fails_closed_and_keeps_key_reserved(self):
        payload = self._payload()
        created = self.client.post(self.url, payload, format="json")
        MedicationRequest.objects.get().delete()

        response = self.client.post(self.reconcile_url, payload, format="json")

        self.assertEqual(response.status_code, 409)
        self.assertNotIn("medication_request", response.json())
        self.assertNotIn(
            created.json()["medication_request"]["id"], response.content.decode()
        )
        self.assertEqual(MedicationRequest.objects.count(), 0)
        self.assertEqual(MedicationRequest._base_manager.count(), 1)  # noqa: SLF001

    def test_legacy_create_remains_compatible(self):
        legacy_url = reverse(
            "medication-request-list",
            kwargs={"patient_external_id": self.patient.external_id},
        )
        payload = self._payload()
        payload.pop("client_request_id")

        response = self.client.post(legacy_url, payload, format="json")

        self.assertEqual(response.status_code, 200)
        medication_request = MedicationRequest.objects.get()
        self.assertIsNone(medication_request.client_request_id)
        self.assertIsNone(medication_request.client_request_payload_hash)


class TestMedicationRequestIdempotencyConcurrency(TransactionTestCase):
    fake = CareAPITestBase.fake
    reset_sequences = True

    def setUp(self):
        cache.clear()
        FacilityPatientNameIdentifierConfig.CACHED_CONFIG.clear()
        NameIdentifierConfig.CACHED_CONFIG.clear()
        PhoneNumberIdentifierConfig.CACHED_CONFIG.clear()
        self.user = CareAPITestBase.create_user(self)
        self.facility = CareAPITestBase.create_facility(self, user=self.user)
        self.organization = CareAPITestBase.create_facility_organization(
            self, facility=self.facility
        )
        self.patient = CareAPITestBase.create_patient(self)
        self.encounter = CareAPITestBase.create_encounter(
            self,
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
        )
        self.other_encounter = CareAPITestBase.create_encounter(
            self,
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
        )
        role = CareAPITestBase.create_role_with_permissions(
            self,
            [
                PatientPermissions.can_view_clinical_data.name,
                EncounterPermissions.can_read_encounter_clinical_data.name,
                EncounterPermissions.can_write_encounter_clinical_data.name,
            ],
        )
        CareAPITestBase.attach_role_facility_organization_user(
            self, self.organization, self.user, role
        )
        self.url = reverse(
            "medication-request-idempotent-create",
            kwargs={"patient_external_id": self.patient.external_id},
        )

    def _payload(self, request_id):
        return {
            "client_request_id": str(request_id),
            "status": "active",
            "intent": "order",
            "category": "outpatient",
            "priority": "routine",
            "do_not_perform": False,
            "medication": {
                "display": "Synthetic medication",
                "system": "http://test-system.invalid/medication",
                "code": "synthetic-code",
            },
            "dosage_instruction": [],
            "authored_on": datetime(2026, 7, 20, 10, tzinfo=UTC).isoformat(),
            "encounter": str(self.encounter.external_id),
            "requester": str(self.user.external_id),
            "create_prescription": {
                "alternate_identifier": "concurrent-synthetic-prescription",
            },
        }

    def _post_concurrently(self, payloads):
        barrier = Barrier(2)

        def post_request(payload):
            close_old_connections()
            client = APIClient()
            client.force_authenticate(user=self.user)
            barrier.wait()
            response = client.post(self.url, copy.deepcopy(payload), format="json")
            close_old_connections()
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(post_request, payloads))

    def test_concurrent_identical_requests_create_exactly_one_resource(self):
        payload = self._payload(uuid4())

        responses = self._post_concurrently([payload, payload])

        self.assertEqual(sorted(code for code, _ in responses), [200, 201])
        self.assertEqual(
            sorted(body["replayed"] for _, body in responses), [False, True]
        )
        self.assertEqual(
            len({body["medication_request"]["id"] for _, body in responses}),
            1,
        )
        self.assertEqual(MedicationRequest.objects.count(), 1)
        self.assertEqual(MedicationRequestPrescription.objects.count(), 1)
        self.assertEqual(QuestionnaireResponse.objects.count(), 1)
        constraint_names = {
            constraint.name
            for constraint in MedicationRequest._meta.constraints  # noqa: SLF001
        }
        self.assertIn(MedicationRequest.IDEMPOTENCY_CONSTRAINT_NAME, constraint_names)

    def test_concurrent_different_payloads_return_one_non_leaking_conflict(self):
        payload = self._payload(uuid4())
        changed = {
            **payload,
            "encounter": str(self.other_encounter.external_id),
        }
        preflight_barrier = Barrier(2)
        original_response = (
            medication_request_viewset.MedicationRequestViewSet._idempotency_response  # noqa: SLF001
        )

        def synchronize_preflight(viewset, *args):
            response = original_response(viewset, *args)
            if response is None and not connection.in_atomic_block:
                preflight_barrier.wait(timeout=5)
            return response

        with (
            patch.object(
                medication_request_viewset.MedicationRequestViewSet,
                "_idempotency_response",
                synchronize_preflight,
            ),
            patch.object(
                medication_commands,
                "_is_idempotency_constraint_violation",
                wraps=medication_commands._is_idempotency_constraint_violation,  # noqa: SLF001
            ) as constraint_check,
        ):
            responses = self._post_concurrently([payload, changed])

        self.assertEqual(sorted(code for code, _ in responses), [201, 409])
        constraint_check.assert_called_once()
        conflict = next(
            body for code, body in responses if code == status.HTTP_409_CONFLICT
        )
        created = next(
            body for code, body in responses if code == status.HTTP_201_CREATED
        )
        self.assertNotIn("medication_request", conflict)
        self.assertNotIn(
            created["medication_request"]["id"],
            str(conflict),
        )
        self.assertEqual(MedicationRequest.objects.count(), 1)
        self.assertEqual(MedicationRequestPrescription.objects.count(), 1)
        self.assertEqual(QuestionnaireResponse.objects.count(), 1)
