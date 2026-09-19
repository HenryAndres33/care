import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.core.cache import cache
from django.db import close_old_connections
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker
from rest_framework import status
from rest_framework.test import APIClient

from care.emr.models.questionnaire import (
    FormSubmission,
    Questionnaire,
)
from care.emr.resources.encounter.constants import StatusChoices
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
from care.security.permissions.questionnaire import QuestionnairePermissions
from care.utils.tests.base import CareAPITestBase
from care_suriname.models.form_submission_command import (
    FormSubmissionCommand,
)


class TestFormSubmissionViewSet(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.facility = self.create_facility(user=self.user)
        self.organization = self.create_facility_organization(facility=self.facility)
        self.questionnaire_organization = self.create_organization()
        self.patient = self.create_patient()
        self.encounter = self.create_encounter(
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
        )
        self.questionnaire = baker.make(
            Questionnaire,
            organization_cache=[self.questionnaire_organization.id],
            slug="test-questionnaire",
            title="Test Questionnaire",
        )
        self.client.force_authenticate(user=self.user)
        self.base_url = reverse("form_submission-list")

    def _get_detail_url(self, external_id):
        return reverse(
            "form_submission-detail",
            kwargs={"external_id": external_id},
        )

    def _create_form_submission(self, patient=None, encounter=None, **kwargs):
        data = {
            "questionnaire": self.questionnaire,
            "patient": patient or self.patient,
            "status": FormSubmissionStatusChoices.draft.value,
            "response_dump": {"key": "value"},
        }
        if encounter:
            data["encounter"] = encounter
        data.update(kwargs)
        if data["status"] == FormSubmissionStatusChoices.submitted.value:
            data.setdefault("workflow_finalized_at", timezone.now())
            data.setdefault("workflow_finalized_by", self.user)
            data.setdefault("finalized_snapshot_hash", "a" * 64)
        return baker.make(FormSubmission, **data)

    def _generate_create_data(self, encounter=None, patient=None, **kwargs):
        data = {
            "questionnaire": self.questionnaire.slug,
            "patient": str((patient or self.patient).external_id),
            "status": FormSubmissionStatusChoices.draft.value,
            "response_dump": {"answer": 42},
        }
        if encounter:
            data["encounter"] = str(encounter.external_id)
        data.update(kwargs)
        return data

    def _generate_idempotent_create_data(self, **kwargs):
        data = {
            "client_request_id": str(uuid.uuid4()),
            "encounter": str(self.encounter.external_id),
            "form_instance_id": str(uuid.uuid4()),
            "patient": str(self.patient.external_id),
            "questionnaire": self.questionnaire.slug,
            "response_dump": {"answer": 42},
        }
        data.update(kwargs)
        return data

    def _grant_patient_submit_permission(self):
        permissions = [
            PatientPermissions.can_submit_patient_questionnaire.name,
            QuestionnairePermissions.can_submit_questionnaire.name,
        ]
        role = self.create_role_with_permissions(permissions)
        self.attach_role_facility_organization_user(self.organization, self.user, role)
        self.attach_role_organization_user(
            self.questionnaire_organization,
            self.user,
            role,
        )

    def _grant_encounter_submit_permission(self):
        permissions = [
            EncounterPermissions.can_submit_encounter_questionnaire.name,
            QuestionnairePermissions.can_submit_questionnaire.name,
        ]
        role = self.create_role_with_permissions(permissions)
        self.attach_role_facility_organization_user(self.organization, self.user, role)
        self.attach_role_organization_user(
            self.questionnaire_organization,
            self.user,
            role,
        )

    # ── LIST ─────────────────────────────────────────────────────────────

    def test_list_without_patient_or_encounter_filter(self):
        response = self.client.get(self.base_url)
        self.assertEqual(response.status_code, 400)

    def test_list_with_patient_filter_without_permissions(self):
        response = self.client.get(self.base_url, {"patient": self.patient.external_id})
        self.assertEqual(response.status_code, 403)

    def test_list_with_patient_filter_with_permissions(self):
        self._grant_patient_submit_permission()
        self._create_form_submission()
        response = self.client.get(self.base_url, {"patient": self.patient.external_id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["results"]), 1)

    def test_list_with_encounter_filter_without_permissions(self):
        response = self.client.get(
            self.base_url, {"encounter": self.encounter.external_id}
        )
        self.assertEqual(response.status_code, 403)

    def test_list_with_encounter_filter_with_permissions(self):
        self._grant_encounter_submit_permission()
        self._create_form_submission(encounter=self.encounter)
        response = self.client.get(
            self.base_url, {"encounter": self.encounter.external_id}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["results"]), 1)

    def test_list_with_encounter_filter_completed_encounter(self):
        self._grant_encounter_submit_permission()
        completed_encounter = self.create_encounter(
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
            status=StatusChoices.completed.value,
        )
        response = self.client.get(
            self.base_url, {"encounter": completed_encounter.external_id}
        )
        self.assertEqual(response.status_code, 403)

    def test_list_filtered_by_patient_returns_only_matching(self):
        self._grant_patient_submit_permission()
        submission = self._create_form_submission()

        other_patient = self.create_patient()
        other_encounter = self.create_encounter(
            patient=other_patient,
            facility=self.facility,
            organization=self.organization,
        )
        self._create_form_submission(patient=other_patient, encounter=other_encounter)

        response = self.client.get(self.base_url, {"patient": self.patient.external_id})
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], str(submission.external_id))
        self.assertEqual(results[0]["resource_version"], 1)

    def test_list_filtered_by_encounter_returns_only_matching(self):
        self._grant_encounter_submit_permission()
        submission = self._create_form_submission(encounter=self.encounter)

        other_encounter = self.create_encounter(
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
        )
        self._create_form_submission(encounter=other_encounter)

        response = self.client.get(
            self.base_url, {"encounter": self.encounter.external_id}
        )
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], str(submission.external_id))

    def test_list_filter_by_status(self):
        self._grant_patient_submit_permission()
        self._create_form_submission(status=FormSubmissionStatusChoices.draft.value)
        self._create_form_submission(status=FormSubmissionStatusChoices.submitted.value)

        response = self.client.get(
            self.base_url,
            {
                "patient": self.patient.external_id,
                "status": FormSubmissionStatusChoices.draft.value,
            },
        )
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], FormSubmissionStatusChoices.draft.value)

    def test_list_filter_by_questionnaire_slug(self):
        self._grant_patient_submit_permission()
        self._create_form_submission()

        other_questionnaire = baker.make(Questionnaire, slug="other-questionnaire")
        self._create_form_submission(questionnaire=other_questionnaire)

        response = self.client.get(
            self.base_url,
            {
                "patient": self.patient.external_id,
                "questionnaire": self.questionnaire.slug,
            },
        )
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual(len(results), 1)

    def test_list_with_invalid_patient_uuid(self):
        response = self.client.get(self.base_url, {"patient": uuid.uuid4()})
        self.assertEqual(response.status_code, 404)

    def test_list_with_invalid_encounter_uuid(self):
        response = self.client.get(self.base_url, {"encounter": uuid.uuid4()})
        self.assertEqual(response.status_code, 404)

    # ── CREATE ───────────────────────────────────────────────────────────

    def test_create_with_patient_permissions(self):
        self._grant_patient_submit_permission()
        data = self._generate_create_data()
        response = self.client.post(self.base_url, data, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["status"], FormSubmissionStatusChoices.draft.value
        )
        self.assertEqual(response.json()["resource_version"], 1)

    def test_create_with_encounter_permissions(self):
        self._grant_encounter_submit_permission()
        data = self._generate_create_data(encounter=self.encounter)
        response = self.client.post(self.base_url, data, format="json")
        self.assertEqual(response.status_code, 200)

    def test_create_honors_client_owned_form_instance_id(self):
        self._grant_encounter_submit_permission()
        form_instance_id = uuid.uuid4()
        data = self._generate_create_data(
            encounter=self.encounter,
            id=str(form_instance_id),
        )
        response = self.client.post(self.base_url, data, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], str(form_instance_id))
        self.assertTrue(
            FormSubmission.objects.filter(external_id=form_instance_id).exists()
        )

    def test_create_without_permissions(self):
        data = self._generate_create_data()
        response = self.client.post(self.base_url, data, format="json")
        self.assertEqual(response.status_code, 403)

    def test_create_unauthenticated(self):
        self.client.logout()
        data = self._generate_create_data()
        response = self.client.post(self.base_url, data, format="json")
        self.assertEqual(response.status_code, 403)

    def test_create_with_encounter_rejects_mismatched_patient(self):
        self._grant_encounter_submit_permission()
        other_patient = self.create_patient()
        data = self._generate_create_data(
            encounter=self.encounter, patient=other_patient
        )
        response = self.client.post(self.base_url, data, format="json")
        self.assertEqual(response.status_code, 404)

    def test_create_with_completed_encounter(self):
        self._grant_encounter_submit_permission()
        completed_encounter = self.create_encounter(
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
            status=StatusChoices.completed.value,
        )
        data = self._generate_create_data(encounter=completed_encounter)
        response = self.client.post(self.base_url, data, format="json")
        self.assertEqual(response.status_code, 403)

    def test_create_with_invalid_questionnaire_slug(self):
        self._grant_patient_submit_permission()
        data = self._generate_create_data()
        data["questionnaire"] = "nonexistent-slug"
        response = self.client.post(self.base_url, data, format="json")
        self.assertEqual(response.status_code, 404)

    def test_create_with_invalid_patient_uuid(self):
        self._grant_patient_submit_permission()
        data = self._generate_create_data()
        data["patient"] = str(uuid.uuid4())
        response = self.client.post(self.base_url, data, format="json")
        self.assertEqual(response.status_code, 404)

    def test_create_with_invalid_encounter_uuid(self):
        self._grant_encounter_submit_permission()
        data = self._generate_create_data(encounter=self.encounter)
        data["encounter"] = str(uuid.uuid4())
        response = self.client.post(self.base_url, data, format="json")
        self.assertEqual(response.status_code, 404)

    def test_create_stores_response_dump(self):
        self._grant_patient_submit_permission()
        response_dump = {"q1": "yes", "q2": [1, 2, 3]}
        data = self._generate_create_data(response_dump=response_dump)
        response = self.client.post(self.base_url, data, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["response_dump"], response_dump)

    def test_idempotent_create_draft_replays_exact_request(self):
        self._grant_encounter_submit_permission()
        payload = self._generate_idempotent_create_data()
        url = reverse("form_submission-idempotent-create-draft")

        created = self.client.post(url, payload, format="json")
        replayed = self.client.post(url, payload, format="json")

        self.assertEqual(created.status_code, 201)
        self.assertEqual(replayed.status_code, 200)
        self.assertFalse(created.json()["replayed"])
        self.assertTrue(replayed.json()["replayed"])
        self.assertEqual(
            created.json()["form_submission"]["id"],
            payload["form_instance_id"],
        )
        self.assertEqual(
            replayed.json()["form_submission"]["id"],
            payload["form_instance_id"],
        )
        self.assertEqual(FormSubmission.objects.count(), 1)
        self.assertEqual(
            FormSubmissionCommand.objects.filter(command_type="create_draft").count(),
            1,
        )

    def test_idempotent_create_draft_rejects_reused_key_with_new_content(self):
        self._grant_encounter_submit_permission()
        payload = self._generate_idempotent_create_data()
        url = reverse("form_submission-idempotent-create-draft")
        created = self.client.post(url, payload, format="json")

        conflict = self.client.post(
            url,
            {**payload, "response_dump": {"answer": "different"}},
            format="json",
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(
            conflict.json()["errors"][0]["type"],
            "idempotency_conflict",
        )
        self.assertNotIn("form_submission", conflict.json())
        self.assertEqual(FormSubmission.objects.count(), 1)

    def test_idempotent_create_draft_rejects_second_command_for_instance(self):
        self._grant_encounter_submit_permission()
        payload = self._generate_idempotent_create_data()
        url = reverse("form_submission-idempotent-create-draft")
        created = self.client.post(url, payload, format="json")

        conflict = self.client.post(
            url,
            {**payload, "client_request_id": str(uuid.uuid4())},
            format="json",
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(
            conflict.json()["errors"][0]["type"],
            "form_submission_instance_conflict",
        )
        self.assertNotIn("form_submission", conflict.json())
        self.assertEqual(FormSubmission.objects.count(), 1)

    def test_idempotent_create_draft_rejects_soft_deleted_instance(self):
        self._grant_encounter_submit_permission()
        payload = self._generate_idempotent_create_data()
        existing = self._create_form_submission(
            encounter=self.encounter,
            external_id=payload["form_instance_id"],
            deleted=True,
        )

        conflict = self.client.post(
            reverse("form_submission-idempotent-create-draft"),
            payload,
            format="json",
        )

        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(
            conflict.json()["errors"][0]["type"],
            "form_submission_instance_conflict",
        )
        self.assertTrue(FormSubmission._base_manager.filter(pk=existing.pk).exists())  # noqa: SLF001

    def test_legacy_create_cannot_create_submitted_form(self):
        self._grant_patient_submit_permission()
        data = self._generate_create_data(
            status=FormSubmissionStatusChoices.submitted.value
        )
        response = self.client.post(self.base_url, data, format="json")
        self.assertEqual(response.status_code, 400)

    # ── RETRIEVE ─────────────────────────────────────────────────────────

    def test_retrieve_with_encounter_permissions(self):
        self._grant_encounter_submit_permission()
        submission = self._create_form_submission(encounter=self.encounter)
        url = self._get_detail_url(submission.external_id)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], str(submission.external_id))

    def test_retrieve_with_patient_permissions(self):
        self._grant_patient_submit_permission()
        submission = self._create_form_submission()
        url = self._get_detail_url(submission.external_id)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_retrieve_without_permissions(self):
        submission = self._create_form_submission(encounter=self.encounter)
        url = self._get_detail_url(submission.external_id)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_retrieve_nonexistent(self):
        self._grant_encounter_submit_permission()
        url = self._get_detail_url(uuid.uuid4())
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    # ── UPDATE ───────────────────────────────────────────────────────────

    def test_update_with_encounter_permissions(self):
        self._grant_encounter_submit_permission()
        submission = self._create_form_submission(encounter=self.encounter)
        url = self._get_detail_url(submission.external_id)
        update_data = {
            "status": FormSubmissionStatusChoices.draft.value,
            "response_dump": {"updated": True},
            "expected_version": 1,
        }
        response = self.client.put(url, update_data, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["status"], FormSubmissionStatusChoices.draft.value
        )
        self.assertEqual(response.json()["response_dump"], {"updated": True})
        self.assertEqual(response.json()["resource_version"], 2)

    def test_update_with_patient_permissions(self):
        self._grant_patient_submit_permission()
        submission = self._create_form_submission()
        url = self._get_detail_url(submission.external_id)
        update_data = {
            "status": FormSubmissionStatusChoices.draft.value,
            "response_dump": {"updated": True},
            "expected_version": 1,
        }
        response = self.client.put(url, update_data, format="json")
        self.assertEqual(response.status_code, 200)

    def test_update_without_permissions(self):
        submission = self._create_form_submission(encounter=self.encounter)
        url = self._get_detail_url(submission.external_id)
        update_data = {
            "status": FormSubmissionStatusChoices.draft.value,
            "response_dump": {},
            "expected_version": 1,
        }
        response = self.client.put(url, update_data, format="json")
        self.assertEqual(response.status_code, 403)

    def test_update_completed_encounter(self):
        self._grant_encounter_submit_permission()
        completed_encounter = self.create_encounter(
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
            status=StatusChoices.completed.value,
        )
        submission = self._create_form_submission(encounter=completed_encounter)
        url = self._get_detail_url(submission.external_id)
        update_data = {
            "status": FormSubmissionStatusChoices.draft.value,
            "response_dump": {},
            "expected_version": 1,
        }
        response = self.client.put(url, update_data, format="json")
        self.assertEqual(response.status_code, 403)

    def test_legacy_update_can_discard_active_draft_with_expected_version(self):
        self._grant_encounter_submit_permission()
        submission = self._create_form_submission(encounter=self.encounter)
        url = self._get_detail_url(submission.external_id)
        update_data = {
            "status": FormSubmissionStatusChoices.entered_in_error.value,
            "response_dump": {"client": "copy"},
            "expected_version": 1,
        }
        response = self.client.put(url, update_data, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["status"],
            FormSubmissionStatusChoices.entered_in_error.value,
        )
        self.assertEqual(response.json()["resource_version"], 2)
        self.assertEqual(response.json()["response_dump"], {"key": "value"})

    # ── DELETE (unsupported) ─────────────────────────────────────────────

    def test_delete_not_supported(self):
        self._grant_encounter_submit_permission()
        submission = self._create_form_submission(encounter=self.encounter)
        url = self._get_detail_url(submission.external_id)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 405)


class TestFormSubmissionCreateDraftConcurrency(TransactionTestCase):
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
            self,
            facility=self.facility,
        )
        self.questionnaire_organization = CareAPITestBase.create_organization(self)
        self.patient = CareAPITestBase.create_patient(self)
        self.encounter = CareAPITestBase.create_encounter(
            self,
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
        )
        self.questionnaire = baker.make(
            Questionnaire,
            organization_cache=[self.questionnaire_organization.id],
            slug="concurrent-form-submission-questionnaire",
            title="Concurrent FormSubmission Questionnaire",
        )
        role = CareAPITestBase.create_role_with_permissions(
            self,
            [
                EncounterPermissions.can_submit_encounter_questionnaire.name,
                QuestionnairePermissions.can_submit_questionnaire.name,
            ],
        )
        CareAPITestBase.attach_role_facility_organization_user(
            self,
            self.organization,
            self.user,
            role,
        )
        CareAPITestBase.attach_role_organization_user(
            self,
            self.questionnaire_organization,
            self.user,
            role,
        )
        self.url = reverse("form_submission-idempotent-create-draft")

    def _payload(self):
        return {
            "client_request_id": str(uuid.uuid4()),
            "encounter": str(self.encounter.external_id),
            "form_instance_id": str(uuid.uuid4()),
            "patient": str(self.patient.external_id),
            "questionnaire": self.questionnaire.slug,
            "response_dump": {"note_text": "Concurrent draft"},
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

    def test_concurrent_exact_retries_create_one_draft_and_replay(self):
        payload = self._payload()

        responses = self._post_concurrently([payload, payload])

        self.assertEqual(sorted(code for code, _ in responses), [200, 201])
        self.assertEqual(
            sorted(body["replayed"] for _, body in responses),
            [False, True],
        )
        self.assertEqual(FormSubmission.objects.count(), 1)
        self.assertEqual(
            FormSubmissionCommand.objects.filter(command_type="create_draft").count(),
            1,
        )

    def test_concurrent_tabs_cannot_create_duplicate_root_drafts(self):
        first = self._payload()
        second = {
            **first,
            "client_request_id": str(uuid.uuid4()),
        }

        responses = self._post_concurrently([first, second])

        self.assertEqual(sorted(code for code, _ in responses), [201, 409])
        conflict = next(
            body for code, body in responses if code == status.HTTP_409_CONFLICT
        )
        self.assertEqual(
            conflict["errors"][0]["type"],
            "form_submission_instance_conflict",
        )
        self.assertNotIn("form_submission", conflict)
        self.assertEqual(FormSubmission.objects.count(), 1)
        self.assertEqual(
            FormSubmissionCommand.objects.filter(command_type="create_draft").count(),
            1,
        )
