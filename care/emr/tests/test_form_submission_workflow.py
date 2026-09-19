import copy
import json
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from threading import Barrier
from unittest.mock import patch
from uuid import uuid1, uuid4

from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.test import SimpleTestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker
from rest_framework.test import APIClient

from care.emr.models.medication_request import MedicationRequest
from care.emr.models.questionnaire import (
    FormSubmission,
    Questionnaire,
    QuestionnaireResponse,
)
from care.emr.registries.system_questionnaire.system_questionnaire import (
    InternalQuestionnaireRegistry,
)
from care.emr.resources.encounter.constants import StatusChoices
from care.emr.resources.form_submission.commands import (
    finalized_form_submission_snapshot_hash,
)
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
from care_suriname.correspondence.correction import (
    create_finalized_form_series_head,
    form_submission_series_head_integrity_valid,
    source_correction_integrity_valid,
)
from care_suriname.models.correspondence_correction import (
    CorrespondenceCorrectionOutbox,
    CorrespondenceSourceCorrection,
    FormSubmissionSeriesHead,
)
from care_suriname.models.form_submission_command import (
    FormSubmissionCommand,
)
from care_suriname.reports.form_submission_artifact import (
    MAX_SNAPSHOT_BYTES,
    MAX_SNAPSHOT_DEPTH,
    MAX_SNAPSHOT_NODES,
    MalformedFinalizedSnapshotError,
    validate_response_dump,
)


class TestFinalizedResponseDumpValidation(SimpleTestCase):
    def test_exact_depth_and_node_limits_are_inclusive(self):
        exact_depth = "leaf"
        for _ in range(MAX_SNAPSHOT_DEPTH - 1):
            exact_depth = [exact_depth]
        validate_response_dump({"value": exact_depth})

        exact_nodes = {"values": [0] * (MAX_SNAPSHOT_NODES - 3)}
        validate_response_dump(exact_nodes)

    def test_non_finite_non_json_and_cyclic_values_are_rejected(self):
        cyclic = {}
        cyclic["self"] = cyclic
        invalid_values = [
            {"value": float("nan")},
            {"value": float("inf")},
            {"value": float("-inf")},
            {"value": object()},
            cyclic,
        ]

        for response_dump in invalid_values:
            with (
                self.subTest(response_dump=type(response_dump).__name__),
                self.assertRaises(MalformedFinalizedSnapshotError),
            ):
                validate_response_dump(response_dump)


class TestFormSubmissionVersionedWorkflow(CareAPITestBase):
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
            slug="generic-versioned-form",
            title="Generic Versioned Form",
            organization_cache=[self.questionnaire_organization.id],
        )
        role = self.create_role_with_permissions(
            [
                PatientPermissions.can_view_clinical_data.name,
                EncounterPermissions.can_read_encounter_clinical_data.name,
                EncounterPermissions.can_submit_encounter_questionnaire.name,
                QuestionnairePermissions.can_submit_questionnaire.name,
            ]
        )
        self.attach_role_facility_organization_user(self.organization, self.user, role)
        self.attach_role_organization_user(
            self.questionnaire_organization,
            self.user,
            role,
        )
        self.client.force_authenticate(user=self.user)
        self.submission = self._draft()

    def _draft(self, **overrides):
        values = {
            "questionnaire": self.questionnaire,
            "patient": self.patient,
            "encounter": self.encounter,
            "status": FormSubmissionStatusChoices.draft.value,
            "response_dump": {"field": "initial"},
            "created_by": self.user,
            "updated_by": self.user,
        }
        values.update(overrides)
        return baker.make(FormSubmission, **values)

    def _url(self, submission, action):
        return reverse(
            f"form_submission-{action}",
            kwargs={"external_id": submission.external_id},
        )

    def _command(self, **overrides):
        command = {
            "client_request_id": str(uuid4()),
            "expected_version": self.submission.resource_version,
            "patient": str(self.patient.external_id),
            "encounter": str(self.encounter.external_id),
            "questionnaire": self.questionnaire.slug,
        }
        command.update(overrides)
        return command

    def _finalize(self, submission=None, **overrides):
        submission = submission or self.submission
        payload = self._command(
            expected_version=submission.resource_version, **overrides
        )
        return self.client.post(
            self._url(submission, "idempotent-finalize"), payload, format="json"
        )

    def _enter_in_error(self, submission=None, **overrides):
        submission = submission or self.submission
        overrides.setdefault("reason", "Incorrect clinical note")
        payload = self._command(
            expected_version=submission.resource_version,
            **overrides,
        )
        return self.client.post(
            self._url(submission, "idempotent-enter-in-error"),
            payload,
            format="json",
        )

    def _linked_medication(self, submission, **link_overrides):
        medication = baker.make(
            MedicationRequest,
            status="active",
            intent="order",
            category="outpatient",
            priority="routine",
            do_not_perform=False,
            patient=submission.patient,
            encounter=submission.encounter,
            requester=self.user,
            client_request_id=uuid4(),
            client_request_payload_hash="a" * 64,
            created_by=self.user,
            updated_by=self.user,
        )
        values = {
            "subject_id": submission.patient.external_id,
            "patient": submission.patient,
            "encounter": submission.encounter,
            "form_submission": submission,
            "status": "completed",
            "structured_response_type": "medication_request",
            "structured_responses": {
                "medication_request": {
                    "submit_type": "CREATE",
                    "id": str(medication.external_id),
                }
            },
            "created_by": self.user,
            "updated_by": self.user,
        }
        values.update(link_overrides)
        return medication, baker.make(QuestionnaireResponse, **values)

    def test_draft_update_requires_version_and_returns_versioned_contract(self):
        payload = self._command(response_dump={"field": "updated"})

        response = self.client.post(
            self._url(self.submission, "idempotent-update-draft"),
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json()),
            {"client_request_id", "replayed", "form_submission"},
        )
        form = response.json()["form_submission"]
        self.assertFalse(response.json()["replayed"])
        self.assertEqual(form["resource_version"], 2)
        self.assertEqual(form["response_dump"], {"field": "updated"})
        self.assertEqual(form["patient"], str(self.patient.external_id))
        self.assertEqual(form["encounter"], str(self.encounter.external_id))
        self.assertEqual(form["questionnaire"], self.questionnaire.slug)
        self.assertEqual(response["ETag"], f'"{self.submission.external_id}:2"')

    def test_two_tab_draft_update_returns_409_without_overwrite(self):
        first = self._command(response_dump={"tab": "one"})
        second = self._command(response_dump={"tab": "two"})

        first_response = self.client.post(
            self._url(self.submission, "idempotent-update-draft"),
            first,
            format="json",
        )
        second_response = self.client.post(
            self._url(self.submission, "idempotent-update-draft"),
            second,
            format="json",
        )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 409)
        self.assertEqual(second_response.json()["current_version"], 2)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.response_dump, {"tab": "one"})

    def test_identical_retry_replays_without_second_mutation(self):
        payload = self._command(response_dump={"retry": True})
        url = self._url(self.submission, "idempotent-update-draft")

        created = self.client.post(url, payload, format="json")
        replayed = self.client.post(url, payload, format="json")

        self.assertEqual(created.status_code, 200)
        self.assertEqual(replayed.status_code, 200)
        self.assertTrue(replayed.json()["replayed"])
        self.assertEqual(
            created.json()["form_submission"]["id"],
            replayed.json()["form_submission"]["id"],
        )
        self.assertEqual(FormSubmissionCommand.objects.count(), 1)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.resource_version, 2)

    def test_same_key_different_payload_is_non_leaking_409(self):
        payload = self._command(response_dump={"value": 1})
        url = self._url(self.submission, "idempotent-update-draft")
        self.client.post(url, payload, format="json")
        changed = {**payload, "response_dump": {"value": 2}}

        conflict = self.client.post(url, changed, format="json")

        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["errors"][0]["type"], "idempotency_conflict")
        self.assertNotIn("form_submission", conflict.json())

    def test_same_key_other_authorized_context_is_non_leaking_409(self):
        other_submission = self._draft()
        payload = self._command(response_dump={"value": 1})
        self.client.post(
            self._url(self.submission, "idempotent-update-draft"),
            payload,
            format="json",
        )

        conflict = self.client.post(
            self._url(other_submission, "idempotent-update-draft"),
            payload,
            format="json",
        )

        self.assertEqual(conflict.status_code, 409)
        self.assertNotIn("form_submission", conflict.json())

    def test_command_is_strict_and_requires_uuid_v4(self):
        url = self._url(self.submission, "idempotent-update-draft")
        extra = self._command(response_dump={}, misspelled_field=True)
        wrong_uuid_version = self._command(
            client_request_id=str(uuid1()), response_dump={}
        )

        self.assertEqual(self.client.post(url, extra, format="json").status_code, 400)
        self.assertEqual(
            self.client.post(url, wrong_uuid_version, format="json").status_code,
            400,
        )

    def test_finalize_is_explicit_immutable_workflow_snapshot(self):
        payload = self._command()
        url = self._url(self.submission, "idempotent-finalize")

        finalized = self.client.post(url, payload, format="json")
        replayed = self.client.post(url, payload, format="json")

        self.assertEqual(finalized.status_code, 200)
        self.assertEqual(replayed.status_code, 200)
        self.assertTrue(replayed.json()["replayed"])
        form = finalized.json()["form_submission"]
        self.assertEqual(form["status"], FormSubmissionStatusChoices.submitted.value)
        self.assertEqual(form["resource_version"], 2)
        self.assertIsNotNone(form["workflow_finalized_at"])
        self.assertEqual(
            form["workflow_finalized_by"]["id"], str(self.user.external_id)
        )
        self.assertEqual(len(form["finalized_snapshot_hash"]), 64)
        self.assertNotIn("attestation", form)

    def test_draft_can_be_marked_entered_in_error_with_audited_replay(self):
        request_id = str(uuid4())

        marked = self._enter_in_error(client_request_id=request_id)
        replayed = self._enter_in_error(client_request_id=request_id)

        self.assertEqual(marked.status_code, HTTPStatus.OK, marked.json())
        self.assertEqual(replayed.status_code, HTTPStatus.OK, replayed.json())
        self.assertTrue(replayed.json()["replayed"])
        self.submission.refresh_from_db()
        self.assertEqual(
            self.submission.status,
            FormSubmissionStatusChoices.entered_in_error.value,
        )
        self.assertEqual(self.submission.resource_version, 2)
        self.assertEqual(
            self.submission.entered_in_error_reason,
            "Incorrect clinical note",
        )
        self.assertEqual(self.submission.entered_in_error_by, self.user)
        self.assertIsNotNone(self.submission.entered_in_error_at)
        self.assertEqual(
            FormSubmissionCommand.objects.filter(command_type="enter_in_error").count(),
            1,
        )

    def test_finalized_note_is_hidden_as_error_without_erasing_snapshot(self):
        self.assertEqual(self._finalize().status_code, HTTPStatus.OK)
        self.submission.refresh_from_db()
        original_dump = copy.deepcopy(self.submission.response_dump)
        original_hash = self.submission.finalized_snapshot_hash
        original_version = self.submission.resource_version
        self.encounter.status = StatusChoices.completed.value
        self.encounter.save(update_fields=["status", "modified_date"])

        marked = self._enter_in_error(
            reason="Wrong patient narrative entered during consultation"
        )

        self.assertEqual(marked.status_code, HTTPStatus.OK, marked.json())
        self.submission.refresh_from_db()
        self.assertEqual(
            self.submission.status,
            FormSubmissionStatusChoices.entered_in_error.value,
        )
        self.assertEqual(self.submission.response_dump, original_dump)
        self.assertEqual(self.submission.finalized_snapshot_hash, original_hash)
        self.assertEqual(self.submission.resource_version, original_version)
        self.assertEqual(self.submission.entered_in_error_by, self.user)
        self.assertEqual(
            self.submission.entered_in_error_reason,
            "Wrong patient narrative entered during consultation",
        )
        head = FormSubmissionSeriesHead.objects.get(series_id=self.submission.series_id)
        self.assertTrue(form_submission_series_head_integrity_valid(head))
        self.assertFalse(
            FormSubmission.objects.filter(
                patient=self.patient,
                status__in=[
                    FormSubmissionStatusChoices.draft.value,
                    FormSubmissionStatusChoices.submitted.value,
                ],
            ).exists()
        )

    def test_finalize_enforces_exact_utf8_byte_boundary_before_persistence(self):
        empty_value_size = len(
            json.dumps(
                {"value": ""},
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        exact_dump = {"value": "x" * (MAX_SNAPSHOT_BYTES - empty_value_size)}
        oversized_dump = {
            "value": "sensitive-marker"
            + "x"
            * (MAX_SNAPSHOT_BYTES - empty_value_size - len("sensitive-marker") + 1)
        }
        self.assertEqual(
            len(
                json.dumps(
                    exact_dump,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ),
            MAX_SNAPSHOT_BYTES,
        )
        exact = self._draft(response_dump=exact_dump)
        oversized = self._draft(response_dump=oversized_dump)

        accepted = self._finalize(exact)
        rejected = self._finalize(oversized)

        self.assertEqual(accepted.status_code, HTTPStatus.OK, accepted.json())
        self.assertEqual(rejected.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(
            rejected.json()["errors"][0]["type"],
            "form_submission_response_invalid",
        )
        self.assertLess(len(json.dumps(rejected.json())), 256)
        self.assertNotIn("sensitive-marker", rejected.content.decode())
        oversized.refresh_from_db()
        self.assertEqual(oversized.status, FormSubmissionStatusChoices.draft.value)
        self.assertFalse(
            FormSubmissionCommand.objects.filter(
                target_submission=oversized,
                command_type="finalize",
            ).exists()
        )
        self.assertFalse(
            FormSubmissionSeriesHead.objects.filter(
                series_id=oversized.series_id
            ).exists()
        )

    def test_amend_enforces_exact_depth_and_node_boundaries(self):
        exact_depth = "leaf"
        for _ in range(MAX_SNAPSHOT_DEPTH - 1):
            exact_depth = [exact_depth]
        excessive_depth = [exact_depth]
        exact_nodes = {"values": [0] * (MAX_SNAPSHOT_NODES - 3)}
        excessive_nodes = {"values": [0] * (MAX_SNAPSHOT_NODES - 2)}
        cases = [
            ({"value": exact_depth}, HTTPStatus.CREATED),
            ({"value": excessive_depth}, HTTPStatus.BAD_REQUEST),
            (exact_nodes, HTTPStatus.CREATED),
            (excessive_nodes, HTTPStatus.BAD_REQUEST),
        ]

        for response_dump, expected_status in cases:
            with self.subTest(expected_status=expected_status, size=len(response_dump)):
                source = self._draft()
                self.assertEqual(self._finalize(source).status_code, HTTPStatus.OK)
                source.refresh_from_db()
                payload = self._command(
                    expected_version=source.resource_version,
                    amendment_type="amendment",
                    reason="Bounded generic correction",
                    response_dump=response_dump,
                )

                response = self.client.post(
                    self._url(source, "idempotent-amend"), payload, format="json"
                )

                self.assertEqual(response.status_code, expected_status, response.json())
                command_exists = FormSubmissionCommand.objects.filter(
                    target_submission=source,
                    command_type="amend",
                ).exists()
                self.assertEqual(command_exists, expected_status == HTTPStatus.CREATED)
                if expected_status == HTTPStatus.BAD_REQUEST:
                    self.assertEqual(
                        response.json()["errors"][0]["type"],
                        "form_submission_response_invalid",
                    )
                    self.assertLess(len(json.dumps(response.json())), 256)

    def test_amend_rejects_invalid_legacy_authoritative_source(self):
        oversized_dump = {"value": "sensitive-marker" + "x" * MAX_SNAPSHOT_BYTES}
        legacy = self._draft(response_dump=oversized_dump)
        legacy.status = FormSubmissionStatusChoices.submitted.value
        legacy.workflow_finalized_at = timezone.now()
        legacy.workflow_finalized_by = self.user
        legacy.finalized_snapshot_hash = finalized_form_submission_snapshot_hash(legacy)
        legacy.save(
            update_fields=[
                "status",
                "workflow_finalized_at",
                "workflow_finalized_by",
                "finalized_snapshot_hash",
                "modified_date",
            ]
        )
        create_finalized_form_series_head(submission=legacy, actor=self.user)
        payload = self._command(
            expected_version=legacy.resource_version,
            amendment_type="amendment",
            reason="Must fail before creating correction work",
            response_dump={"value": "valid replacement"},
        )

        response = self.client.post(
            self._url(legacy, "idempotent-amend"), payload, format="json"
        )

        self.assertEqual(response.status_code, HTTPStatus.CONFLICT, response.json())
        self.assertEqual(
            response.json()["errors"][0]["type"],
            "form_submission_series_integrity_failed",
        )
        self.assertNotIn("sensitive-marker", response.content.decode())
        self.assertEqual(
            FormSubmission.objects.filter(series_id=legacy.series_id).count(), 1
        )
        self.assertFalse(
            FormSubmissionCommand.objects.filter(
                target_submission=legacy,
                command_type="amend",
            ).exists()
        )
        self.assertFalse(
            CorrespondenceSourceCorrection.objects.filter(
                previous_submission=legacy
            ).exists()
        )
        self.assertFalse(CorrespondenceCorrectionOutbox.objects.exists())

    def test_exact_replays_precede_new_response_dump_limits(self):
        oversized_dump = {"value": "x" * MAX_SNAPSHOT_BYTES}
        finalize_source = self._draft(response_dump=oversized_dump)
        finalize_request_id = str(uuid4())

        with patch("care.emr.api.viewsets.form_submission.validate_response_dump"):
            finalized = self._finalize(
                finalize_source,
                client_request_id=finalize_request_id,
            )
        finalize_replay = self._finalize(
            finalize_source,
            client_request_id=finalize_request_id,
        )

        self.assertEqual(finalized.status_code, HTTPStatus.OK)
        self.assertEqual(finalize_replay.status_code, HTTPStatus.OK)
        self.assertTrue(finalize_replay.json()["replayed"])

        amend_source = self._draft()
        self.assertEqual(self._finalize(amend_source).status_code, HTTPStatus.OK)
        amend_source.refresh_from_db()
        amend_payload = self._command(
            client_request_id=str(uuid4()),
            expected_version=amend_source.resource_version,
            amendment_type="amendment",
            reason="Legacy bounded-response replay",
            response_dump=oversized_dump,
        )
        amend_url = self._url(amend_source, "idempotent-amend")
        with patch("care.emr.api.viewsets.form_submission.validate_response_dump"):
            amended = self.client.post(amend_url, amend_payload, format="json")
        amend_replay = self.client.post(amend_url, amend_payload, format="json")

        self.assertEqual(amended.status_code, HTTPStatus.CREATED)
        self.assertEqual(amend_replay.status_code, HTTPStatus.OK)
        self.assertTrue(amend_replay.json()["replayed"])
        self.assertEqual(
            FormSubmissionCommand.objects.filter(
                client_request_id=amend_payload["client_request_id"]
            ).count(),
            1,
        )

    def test_finalized_submission_rejects_command_legacy_and_model_updates(self):
        self.assertEqual(self._finalize().status_code, 200)
        self.submission.refresh_from_db()
        update_payload = self._command(
            expected_version=self.submission.resource_version,
            response_dump={"changed": True},
        )

        command_response = self.client.post(
            self._url(self.submission, "idempotent-update-draft"),
            update_payload,
            format="json",
        )
        legacy_response = self.client.put(
            reverse(
                "form_submission-detail",
                kwargs={"external_id": self.submission.external_id},
            ),
            {
                "status": FormSubmissionStatusChoices.draft.value,
                "response_dump": {"changed": True},
                "expected_version": self.submission.resource_version,
            },
            format="json",
        )

        self.assertEqual(command_response.status_code, 409)
        self.assertEqual(legacy_response.status_code, 409)
        self.submission.response_dump = {"changed": True}
        with self.assertRaises(DjangoValidationError):
            self.submission.save()

    def test_amendment_creates_traceable_new_finalized_row(self):
        self.assertEqual(self._finalize().status_code, 200)
        self.submission.refresh_from_db()
        original_dump = copy.deepcopy(self.submission.response_dump)
        payload = self._command(
            expected_version=self.submission.resource_version,
            amendment_type="amendment",
            reason="Corrected a transcription error",
            response_dump={"field": "corrected"},
        )

        amended = self.client.post(
            self._url(self.submission, "idempotent-amend"), payload, format="json"
        )
        replayed = self.client.post(
            self._url(self.submission, "idempotent-amend"), payload, format="json"
        )

        self.assertEqual(amended.status_code, 201)
        self.assertEqual(replayed.status_code, 200)
        self.assertTrue(replayed.json()["replayed"])
        new_id = amended.json()["form_submission"]["id"]
        new_row = FormSubmission.objects.get(external_id=new_id)
        self.assertEqual(new_row.previous_version, self.submission)
        self.assertEqual(new_row.series_id, self.submission.series_id)
        self.assertEqual(new_row.resource_version, self.submission.resource_version + 1)
        self.assertEqual(new_row.amendment_reason, payload["reason"])
        self.assertEqual(new_row.amendment_type, "amendment")
        self.assertEqual(new_row.created_by, self.user)
        self.assertEqual(new_row.workflow_finalized_by, self.user)
        self.assertIsNotNone(new_row.workflow_finalized_at)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.response_dump, original_dump)

    def test_finalize_creates_exact_current_series_head_without_correction(self):
        self.assertEqual(self._finalize().status_code, HTTPStatus.OK)
        self.submission.refresh_from_db()

        head = FormSubmissionSeriesHead.objects.select_related(
            "current_submission__questionnaire",
            "current_submission__patient",
            "current_submission__encounter",
            "current_submission__previous_version",
            "current_submission__workflow_finalized_by",
            "advanced_by",
        ).get(series_id=self.submission.series_id)

        self.assertEqual(head.current_submission, self.submission)
        self.assertEqual(head.current_version, self.submission.resource_version)
        self.assertEqual(
            head.current_snapshot_hash,
            self.submission.finalized_snapshot_hash,
        )
        self.assertTrue(form_submission_series_head_integrity_valid(head))
        self.assertFalse(CorrespondenceSourceCorrection.objects.exists())
        self.assertFalse(CorrespondenceCorrectionOutbox.objects.exists())

    def test_amendment_atomically_creates_exact_correction_and_pending_outbox(self):
        self.assertEqual(self._finalize().status_code, HTTPStatus.OK)
        self.submission.refresh_from_db()
        payload = self._command(
            expected_version=self.submission.resource_version,
            amendment_type="amendment",
            reason="Corrected a transcription error",
            response_dump={"field": "corrected"},
        )
        url = self._url(self.submission, "idempotent-amend")

        created = self.client.post(url, payload, format="json")
        replay = self.client.post(url, payload, format="json")

        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        self.assertEqual(replay.status_code, HTTPStatus.OK, replay.json())
        self.assertTrue(replay.json()["replayed"])
        result = FormSubmission.objects.get(
            external_id=created.json()["form_submission"]["id"]
        )
        head = FormSubmissionSeriesHead.objects.select_related(
            "current_submission__questionnaire",
            "current_submission__patient",
            "current_submission__encounter",
            "current_submission__previous_version",
            "current_submission__workflow_finalized_by",
            "advanced_by",
        ).get(series_id=self.submission.series_id)
        correction = CorrespondenceSourceCorrection.objects.select_related(
            "source_head",
            "previous_submission__questionnaire",
            "previous_submission__patient",
            "previous_submission__encounter",
            "previous_submission__previous_version",
            "previous_submission__workflow_finalized_by",
            "new_submission__questionnaire",
            "new_submission__patient",
            "new_submission__encounter",
            "new_submission__previous_version",
            "new_submission__workflow_finalized_by",
            "corrected_by",
        ).get()
        outbox = CorrespondenceCorrectionOutbox.objects.get()

        self.assertEqual(head.current_submission, result)
        self.assertTrue(form_submission_series_head_integrity_valid(head))
        self.assertEqual(correction.previous_submission, self.submission)
        self.assertEqual(correction.new_submission, result)
        self.assertEqual(correction.sequence, 1)
        self.assertTrue(source_correction_integrity_valid(correction))
        self.assertEqual(outbox.source_correction, correction)
        self.assertEqual(outbox.status, "pending")
        self.assertEqual(outbox.attempt_count, 0)
        self.assertEqual(outbox.available_at, correction.corrected_at)
        self.assertEqual(CorrespondenceSourceCorrection.objects.count(), 1)
        self.assertEqual(CorrespondenceCorrectionOutbox.objects.count(), 1)

    def test_amendment_rolls_back_source_head_and_command_if_outbox_insert_fails(self):
        self.assertEqual(self._finalize().status_code, HTTPStatus.OK)
        self.submission.refresh_from_db()
        head = FormSubmissionSeriesHead.objects.get(series_id=self.submission.series_id)
        payload = self._command(
            expected_version=self.submission.resource_version,
            amendment_type="amendment",
            reason="Must roll back completely",
            response_dump={"field": "not committed"},
        )

        with (
            patch.object(
                CorrespondenceCorrectionOutbox.objects,
                "create",
                side_effect=RuntimeError("synthetic outbox insert failure"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.client.post(
                self._url(self.submission, "idempotent-amend"),
                payload,
                format="json",
            )

        head.refresh_from_db()
        self.assertEqual(head.current_submission, self.submission)
        self.assertEqual(
            FormSubmission.objects.filter(series_id=self.submission.series_id).count(),
            1,
        )
        self.assertFalse(CorrespondenceSourceCorrection.objects.exists())
        self.assertFalse(CorrespondenceCorrectionOutbox.objects.exists())
        self.assertFalse(
            FormSubmissionCommand.objects.filter(command_type="amend").exists()
        )

    def test_tampered_series_head_fails_closed_without_new_source_or_command(self):
        self.assertEqual(self._finalize().status_code, HTTPStatus.OK)
        self.submission.refresh_from_db()
        FormSubmissionSeriesHead._base_manager.filter(  # noqa: SLF001
            series_id=self.submission.series_id
        ).update(head_hash="0" * 64)
        payload = self._command(
            expected_version=self.submission.resource_version,
            amendment_type="amendment",
            reason="Must not pass tampered provenance",
            response_dump={"field": "blocked"},
        )

        response = self.client.post(
            self._url(self.submission, "idempotent-amend"),
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, HTTPStatus.CONFLICT, response.json())
        self.assertEqual(
            response.json()["errors"][0]["type"],
            "form_submission_series_integrity_failed",
        )
        self.assertEqual(
            FormSubmission.objects.filter(series_id=self.submission.series_id).count(),
            1,
        )
        self.assertFalse(CorrespondenceSourceCorrection.objects.exists())
        self.assertFalse(
            FormSubmissionCommand.objects.filter(command_type="amend").exists()
        )

    def test_named_series_head_uniqueness_race_returns_safe_conflict(self):
        cause = RuntimeError("synthetic named constraint")
        cause.diag = type(
            "Diagnostic",
            (),
            {"constraint_name": FormSubmissionSeriesHead.SERIES_CONSTRAINT_NAME},
        )()
        error = IntegrityError("synthetic uniqueness race")
        error.__cause__ = cause

        with patch(
            "care.emr.api.viewsets.form_submission.create_finalized_form_series_head",
            side_effect=error,
        ):
            response = self._finalize()

        self.assertEqual(response.status_code, HTTPStatus.CONFLICT, response.json())
        self.assertEqual(
            response.json()["errors"][0]["type"],
            "form_submission_series_integrity_failed",
        )
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, FormSubmissionStatusChoices.draft)
        self.assertFalse(FormSubmissionSeriesHead.objects.exists())
        self.assertFalse(FormSubmissionCommand.objects.exists())

    def test_database_rejects_malformed_head_hash_finalizer_and_outbox_state(self):
        self.assertEqual(self._finalize().status_code, HTTPStatus.OK)
        self.submission.refresh_from_db()
        head = FormSubmissionSeriesHead.objects.get(series_id=self.submission.series_id)

        with self.assertRaises(IntegrityError), transaction.atomic():
            FormSubmissionSeriesHead._base_manager.filter(pk=head.pk).update(  # noqa: SLF001
                head_hash="not-a-sha256"
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            FormSubmission._base_manager.filter(pk=self.submission.pk).update(  # noqa: SLF001
                workflow_finalized_by=None
            )

        amended = self.client.post(
            self._url(self.submission, "idempotent-amend"),
            self._command(
                expected_version=self.submission.resource_version,
                amendment_type="amendment",
                reason="Create constrained outbox",
                response_dump={"field": "corrected"},
            ),
            format="json",
        )
        self.assertEqual(amended.status_code, HTTPStatus.CREATED, amended.json())
        outbox = CorrespondenceCorrectionOutbox.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            CorrespondenceCorrectionOutbox._base_manager.filter(pk=outbox.pk).update(  # noqa: SLF001
                status="processing", attempt_count=0, claimed_at=None
            )

    def test_amendment_clones_exact_structured_action_link_and_provenance(self):
        medication, source_link = self._linked_medication(self.submission)
        self.assertEqual(self._finalize().status_code, 200)
        self.submission.refresh_from_db()
        payload = self._command(
            expected_version=self.submission.resource_version,
            amendment_type="amendment",
            reason="Correct clinical wording",
            response_dump={"clinicalActions": {"state": "confirmed"}},
        )

        with patch.dict(
            InternalQuestionnaireRegistry._questionnaires,  # noqa: SLF001
            {},
            clear=True,
        ):
            response = self.client.post(
                self._url(self.submission, "idempotent-amend"),
                payload,
                format="json",
            )
        replay = self.client.post(
            self._url(self.submission, "idempotent-amend"), payload, format="json"
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(replay.status_code, 200)
        amended = FormSubmission.objects.get(
            external_id=response.json()["form_submission"]["id"]
        )
        cloned = QuestionnaireResponse.objects.get(form_submission=amended)
        self.assertNotEqual(cloned.pk, source_link.pk)
        self.assertEqual(cloned.structured_responses, source_link.structured_responses)
        self.assertEqual(cloned.patient, self.patient)
        self.assertEqual(cloned.encounter, self.encounter)
        self.assertEqual(cloned.status, "completed")
        lineage = cloned.meta["form_submission_linkage_lineage"]
        self.assertEqual(
            lineage["source_form_submission"], str(self.submission.external_id)
        )
        self.assertEqual(
            lineage["source_questionnaire_response"], str(source_link.external_id)
        )
        self.assertEqual(
            lineage["target"]["client_request_id"],
            str(medication.client_request_id),
        )
        self.assertEqual(
            lineage["target"]["client_request_payload_hash"],
            medication.client_request_payload_hash,
        )
        self.assertEqual(
            QuestionnaireResponse.objects.filter(form_submission=amended).count(), 1
        )
        self.assertEqual(
            QuestionnaireResponse.objects.filter(
                form_submission=self.submission
            ).count(),
            1,
        )

    def test_amendment_rejects_invalid_or_ambiguous_structured_action_links(self):
        cases = ["entered_in_error", "deleted", "malformed", "ambiguous"]
        for case in cases:
            with self.subTest(case=case):
                source = self._draft()
                medication, link = self._linked_medication(source)
                self.assertEqual(self._finalize(source).status_code, 200)
                source.refresh_from_db()
                if case == "entered_in_error":
                    QuestionnaireResponse._base_manager.filter(pk=link.pk).update(  # noqa: SLF001
                        status="entered_in_error"
                    )
                elif case == "deleted":
                    QuestionnaireResponse._base_manager.filter(pk=link.pk).update(  # noqa: SLF001
                        deleted=True
                    )
                elif case == "malformed":
                    QuestionnaireResponse._base_manager.filter(pk=link.pk).update(  # noqa: SLF001
                        structured_responses={
                            "medication_request": {"id": str(medication.external_id)}
                        }
                    )
                else:
                    baker.make(
                        QuestionnaireResponse,
                        subject_id=self.patient.external_id,
                        patient=self.patient,
                        encounter=self.encounter,
                        form_submission=source,
                        status="completed",
                        structured_response_type="medication_request",
                        structured_responses=copy.deepcopy(link.structured_responses),
                        created_by=self.user,
                        updated_by=self.user,
                    )
                payload = self._command(
                    expected_version=source.resource_version,
                    amendment_type="addendum",
                    reason="Additional context",
                    response_dump={"field": case},
                )

                response = self.client.post(
                    self._url(source, "idempotent-amend"), payload, format="json"
                )

                self.assertEqual(response.status_code, 409)
                self.assertEqual(
                    response.json()["errors"][0]["type"],
                    "structured_action_linkage_invalid",
                )
                self.assertNotIn("form_submission", response.json())
                self.assertEqual(
                    FormSubmission.objects.filter(series_id=source.series_id).count(),
                    1,
                )

    def test_amendment_and_cloned_links_roll_back_if_link_insert_fails(self):
        self._linked_medication(self.submission)
        self.assertEqual(self._finalize().status_code, 200)
        self.submission.refresh_from_db()
        payload = self._command(
            expected_version=self.submission.resource_version,
            amendment_type="amendment",
            reason="Must roll back",
            response_dump={"field": "corrected"},
        )

        with (
            patch.object(
                QuestionnaireResponse.objects,
                "bulk_create",
                side_effect=RuntimeError("synthetic linkage failure"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.client.post(
                self._url(self.submission, "idempotent-amend"),
                payload,
                format="json",
            )

        self.assertEqual(
            FormSubmission.objects.filter(series_id=self.submission.series_id).count(),
            1,
        )
        self.assertEqual(
            FormSubmissionCommand.objects.filter(command_type="amend").count(), 0
        )
        self.assertEqual(
            QuestionnaireResponse.objects.filter(
                form_submission=self.submission
            ).count(),
            1,
        )

    def test_addendum_is_supported_and_reason_is_required(self):
        self.assertEqual(self._finalize().status_code, 200)
        self.submission.refresh_from_db()
        missing_reason = self._command(
            expected_version=self.submission.resource_version,
            amendment_type="addendum",
            response_dump={"note": "additional context"},
        )
        blank_reason = {**missing_reason, "reason": "   "}
        valid = {
            **missing_reason,
            "client_request_id": str(uuid4()),
            "reason": "Context",
        }
        url = self._url(self.submission, "idempotent-amend")

        self.assertEqual(
            self.client.post(url, missing_reason, format="json").status_code, 400
        )
        self.assertEqual(
            self.client.post(url, blank_reason, format="json").status_code, 400
        )
        response = self.client.post(url, valid, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.json()["form_submission"]["amendment_type"], "addendum"
        )

    def test_wrong_patient_encounter_and_questionnaire_are_404_without_command(self):
        other_patient = self.create_patient()
        cases = [
            self._command(patient=str(other_patient.external_id), response_dump={}),
            self._command(encounter=None, response_dump={}),
            self._command(questionnaire="other", response_dump={}),
        ]
        url = self._url(self.submission, "idempotent-update-draft")

        for payload in cases:
            with self.subTest(payload=payload):
                self.assertEqual(
                    self.client.post(url, payload, format="json").status_code, 404
                )
        self.assertEqual(FormSubmissionCommand.objects.count(), 0)

    def test_patient_scoped_draft_preserves_null_encounter_and_authorization(self):
        role = self.create_role_with_permissions(
            [PatientPermissions.can_submit_patient_questionnaire.name]
        )
        self.attach_role_facility_organization_user(self.organization, self.user, role)
        patient_submission = self._draft(encounter=None)
        payload = self._command(
            expected_version=1,
            encounter=None,
            response_dump={"patient_scoped": True},
        )

        response = self.client.post(
            self._url(patient_submission, "idempotent-update-draft"),
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["form_submission"]["encounter"])

    def test_unauthenticated_and_unauthorized_commands_are_denied(self):
        payload = self._command(response_dump={})
        url = self._url(self.submission, "idempotent-update-draft")
        self.client.logout()
        unauthenticated = self.client.post(url, payload, format="json")
        other_user = self.create_user()
        self.client.force_authenticate(user=other_user)
        unauthorized = self.client.post(url, payload, format="json")

        self.assertEqual(unauthenticated.status_code, 403)
        self.assertEqual(unauthorized.status_code, 403)

    def test_exact_replay_after_encounter_closure_uses_current_read_permission(self):
        payload = self._command(response_dump={"saved": True})
        url = self._url(self.submission, "idempotent-update-draft")
        self.assertEqual(self.client.post(url, payload, format="json").status_code, 200)
        self.encounter.status = StatusChoices.completed.value
        self.encounter.save(update_fields=["status", "modified_date"])

        replay = self.client.post(url, payload, format="json")
        new_command = self._command(
            expected_version=2,
            response_dump={"saved": "again"},
        )
        denied = self.client.post(url, new_command, format="json")

        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(denied.status_code, 400)
        self.assertIn(
            "reconciliation workflow",
            str(denied.json()),
        )

    def test_deleted_command_result_fails_closed_and_keeps_key_reserved(self):
        payload = self._command(response_dump={"saved": True})
        url = self._url(self.submission, "idempotent-update-draft")
        self.assertEqual(self.client.post(url, payload, format="json").status_code, 200)
        FormSubmission._base_manager.filter(pk=self.submission.pk).update(deleted=True)  # noqa: SLF001

        conflict = self.client.post(url, payload, format="json")

        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(FormSubmissionCommand._base_manager.count(), 1)  # noqa: SLF001

    def test_command_and_mutation_roll_back_atomically(self):
        payload = self._command(response_dump={"must": "rollback"})
        url = self._url(self.submission, "idempotent-update-draft")

        with (
            patch.object(
                FormSubmissionCommand.objects,
                "create",
                side_effect=RuntimeError("synthetic command ledger failure"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.client.post(url, payload, format="json")

        self.submission.refresh_from_db()
        self.assertEqual(self.submission.resource_version, 1)
        self.assertEqual(self.submission.response_dump, {"field": "initial"})
        self.assertEqual(FormSubmissionCommand.objects.count(), 0)

    def test_legacy_update_requires_expected_version_and_is_draft_only(self):
        url = reverse(
            "form_submission-detail",
            kwargs={"external_id": self.submission.external_id},
        )
        missing_version = {
            "status": FormSubmissionStatusChoices.draft.value,
            "response_dump": {},
        }
        submitted = {
            **missing_version,
            "status": FormSubmissionStatusChoices.submitted.value,
            "expected_version": 1,
        }

        self.assertEqual(
            self.client.put(url, missing_version, format="json").status_code, 400
        )
        self.assertEqual(
            self.client.put(url, submitted, format="json").status_code, 400
        )

    def test_legacy_discard_is_optimistic_and_finalized_rows_stay_immutable(self):
        url = reverse(
            "form_submission-detail",
            kwargs={"external_id": self.submission.external_id},
        )
        stale_discard = {
            "status": FormSubmissionStatusChoices.entered_in_error.value,
            "response_dump": self.submission.response_dump,
            "expected_version": 2,
        }
        current_discard = {**stale_discard, "expected_version": 1}

        self.assertEqual(
            self.client.put(url, stale_discard, format="json").status_code, 409
        )
        discarded = self.client.put(url, current_discard, format="json")
        self.assertEqual(discarded.status_code, 200)
        self.assertEqual(discarded.json()["resource_version"], 2)
        self.assertEqual(
            self.client.put(
                url,
                {**current_discard, "expected_version": 2},
                format="json",
            ).status_code,
            409,
        )

        finalized = self._draft()
        self.assertEqual(self._finalize(finalized).status_code, 200)
        finalized.refresh_from_db()
        finalized_url = reverse(
            "form_submission-detail",
            kwargs={"external_id": finalized.external_id},
        )
        response = self.client.put(
            finalized_url,
            {
                "status": FormSubmissionStatusChoices.entered_in_error.value,
                "response_dump": finalized.response_dump,
                "expected_version": finalized.resource_version,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 409)


class TestFormSubmissionCommandConcurrency(TransactionTestCase):
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
            slug="concurrent-versioned-form",
            title="Concurrent Versioned Form",
            organization_cache=[self.questionnaire_organization.id],
        )
        role = CareAPITestBase.create_role_with_permissions(
            self,
            [
                EncounterPermissions.can_read_encounter_clinical_data.name,
                EncounterPermissions.can_submit_encounter_questionnaire.name,
                QuestionnairePermissions.can_submit_questionnaire.name,
            ],
        )
        CareAPITestBase.attach_role_facility_organization_user(
            self, self.organization, self.user, role
        )
        CareAPITestBase.attach_role_organization_user(
            self,
            self.questionnaire_organization,
            self.user,
            role,
        )
        self.submission = baker.make(
            FormSubmission,
            questionnaire=self.questionnaire,
            patient=self.patient,
            encounter=self.encounter,
            status=FormSubmissionStatusChoices.draft.value,
            response_dump={"field": "initial"},
            created_by=self.user,
            updated_by=self.user,
        )
        self.other_submission = baker.make(
            FormSubmission,
            questionnaire=self.questionnaire,
            patient=self.patient,
            encounter=self.encounter,
            status=FormSubmissionStatusChoices.draft.value,
            response_dump={"field": "initial"},
            created_by=self.user,
            updated_by=self.user,
        )
        self.url = reverse(
            "form_submission-idempotent-update-draft",
            kwargs={"external_id": self.submission.external_id},
        )

    def _payload(self, request_id, response_dump):
        return {
            "client_request_id": str(request_id),
            "expected_version": 1,
            "patient": str(self.patient.external_id),
            "encounter": str(self.encounter.external_id),
            "questionnaire": self.questionnaire.slug,
            "response_dump": response_dump,
        }

    def _post_concurrently(self, requests):
        barrier = Barrier(2)

        def post_request(request):
            url, payload = request
            close_old_connections()
            client = APIClient()
            client.force_authenticate(user=self.user)
            barrier.wait()
            response = client.post(url, copy.deepcopy(payload), format="json")
            close_old_connections()
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(post_request, requests))

    def test_concurrent_same_key_exact_command_mutates_once(self):
        payload = self._payload(uuid4(), {"same": True})

        responses = self._post_concurrently([(self.url, payload), (self.url, payload)])

        self.assertEqual([code for code, _ in responses], [200, 200])
        self.assertEqual(
            sorted(body["replayed"] for _, body in responses), [False, True]
        )
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.resource_version, 2)
        self.assertEqual(FormSubmissionCommand.objects.count(), 1)

    def test_concurrent_two_tab_commands_allow_one_and_conflict_one(self):
        payloads = [
            self._payload(uuid4(), {"tab": "one"}),
            self._payload(uuid4(), {"tab": "two"}),
        ]

        responses = self._post_concurrently(
            [(self.url, payload) for payload in payloads]
        )

        self.assertEqual(sorted(code for code, _ in responses), [200, 409])
        self.assertEqual(FormSubmissionCommand.objects.count(), 1)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.resource_version, 2)
        constraint_names = {
            constraint.name
            for constraint in FormSubmissionCommand._meta.constraints  # noqa: SLF001
        }
        self.assertIn(
            FormSubmissionCommand.IDEMPOTENCY_CONSTRAINT_NAME, constraint_names
        )

    def test_concurrent_same_key_different_targets_uses_named_constraint(self):
        request_id = uuid4()
        other_url = reverse(
            "form_submission-idempotent-update-draft",
            kwargs={"external_id": self.other_submission.external_id},
        )
        requests = [
            (self.url, self._payload(request_id, {"target": "one"})),
            (other_url, self._payload(request_id, {"target": "two"})),
        ]

        responses = self._post_concurrently(requests)

        self.assertEqual(sorted(code for code, _ in responses), [200, 409])
        conflict = next(body for code, body in responses if code == HTTPStatus.CONFLICT)
        self.assertNotIn("form_submission", conflict)
        self.assertEqual(FormSubmissionCommand.objects.count(), 1)
        versions = sorted(
            FormSubmission.objects.filter(
                pk__in=[self.submission.pk, self.other_submission.pk]
            ).values_list("resource_version", flat=True)
        )
        self.assertEqual(versions, [1, 2])

    def test_concurrent_exact_amendment_clones_structured_link_once(self):
        medication = baker.make(
            MedicationRequest,
            status="active",
            intent="order",
            category="outpatient",
            priority="routine",
            do_not_perform=False,
            patient=self.patient,
            encounter=self.encounter,
            requester=self.user,
            client_request_id=uuid4(),
            client_request_payload_hash="a" * 64,
            created_by=self.user,
            updated_by=self.user,
        )
        baker.make(
            QuestionnaireResponse,
            subject_id=self.patient.external_id,
            patient=self.patient,
            encounter=self.encounter,
            form_submission=self.submission,
            status="completed",
            structured_response_type="medication_request",
            structured_responses={
                "medication_request": {
                    "submit_type": "CREATE",
                    "id": str(medication.external_id),
                }
            },
            created_by=self.user,
            updated_by=self.user,
        )
        client = APIClient()
        client.force_authenticate(user=self.user)
        finalize = client.post(
            reverse(
                "form_submission-idempotent-finalize",
                kwargs={"external_id": self.submission.external_id},
            ),
            {
                "client_request_id": str(uuid4()),
                "expected_version": 1,
                "patient": str(self.patient.external_id),
                "encounter": str(self.encounter.external_id),
                "questionnaire": self.questionnaire.slug,
            },
            format="json",
        )
        self.assertEqual(finalize.status_code, 200)
        self.submission.refresh_from_db()
        amend_url = reverse(
            "form_submission-idempotent-amend",
            kwargs={"external_id": self.submission.external_id},
        )
        payload = {
            **self._payload(uuid4(), {"clinicalActions": {"state": "confirmed"}}),
            "expected_version": self.submission.resource_version,
            "amendment_type": "amendment",
            "reason": "Concurrent correction",
        }

        responses = self._post_concurrently(
            [(amend_url, payload), (amend_url, payload)]
        )

        self.assertEqual(sorted(code for code, _ in responses), [200, 201])
        amended = FormSubmission.objects.get(
            series_id=self.submission.series_id,
            resource_version=self.submission.resource_version + 1,
        )
        self.assertEqual(
            QuestionnaireResponse.objects.filter(form_submission=amended).count(), 1
        )
        self.assertEqual(
            FormSubmissionCommand.objects.filter(command_type="amend").count(), 1
        )
        self.assertEqual(CorrespondenceSourceCorrection.objects.count(), 1)
        self.assertEqual(CorrespondenceCorrectionOutbox.objects.count(), 1)

    def test_concurrent_two_key_amendment_creates_one_correction_and_one_outbox(self):
        client = APIClient()
        client.force_authenticate(user=self.user)
        finalized = client.post(
            reverse(
                "form_submission-idempotent-finalize",
                kwargs={"external_id": self.submission.external_id},
            ),
            {
                "client_request_id": str(uuid4()),
                "expected_version": 1,
                "patient": str(self.patient.external_id),
                "encounter": str(self.encounter.external_id),
                "questionnaire": self.questionnaire.slug,
            },
            format="json",
        )
        self.assertEqual(finalized.status_code, HTTPStatus.OK, finalized.json())
        self.submission.refresh_from_db()
        amend_url = reverse(
            "form_submission-idempotent-amend",
            kwargs={"external_id": self.submission.external_id},
        )
        payloads = [
            {
                **self._payload(uuid4(), {"tab": tab}),
                "expected_version": self.submission.resource_version,
                "amendment_type": "amendment",
                "reason": f"Concurrent correction {tab}",
            }
            for tab in ["one", "two"]
        ]

        responses = self._post_concurrently(
            [(amend_url, payload) for payload in payloads]
        )

        self.assertEqual(
            sorted(code for code, _body in responses),
            [HTTPStatus.CREATED, HTTPStatus.CONFLICT],
        )
        self.assertEqual(
            FormSubmission.objects.filter(series_id=self.submission.series_id).count(),
            2,
        )
        self.assertEqual(
            FormSubmissionCommand.objects.filter(command_type="amend").count(), 1
        )
        self.assertEqual(CorrespondenceSourceCorrection.objects.count(), 1)
        self.assertEqual(CorrespondenceCorrectionOutbox.objects.count(), 1)
        head = FormSubmissionSeriesHead.objects.get(series_id=self.submission.series_id)
        self.assertEqual(
            head.current_submission.resource_version,
            self.submission.resource_version + 1,
        )
