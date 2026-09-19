import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from http import HTTPStatus
from threading import Barrier
from unittest.mock import patch
from uuid import uuid1, uuid4

from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import close_old_connections
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from care.emr.models.encounter import EncounterOrganization
from care.emr.models.medication_request import MedicationRequest
from care.emr.models.questionnaire import (
    FormSubmission,
    Questionnaire,
    QuestionnaireResponse,
)
from care.emr.models.report.report_upload import ReportUpload
from care.emr.models.report.template import Template
from care.emr.models.tag_config import TagConfig
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
from care.security.permissions.template import TemplatePermissions
from care.utils.tests.base import CareAPITestBase
from care_suriname.correspondence.correction import create_finalized_form_series_head
from care_suriname.models.correspondence import (
    CorrespondenceCompilation,
    CorrespondenceCompileCommand,
)
from care_suriname.reports.correspondence_compiler import (
    CorrespondenceCompilationError,
    compile_correspondence_html,
    readable_form_html,
)
from care_suriname.resources.correspondence import (
    CompileCorrespondenceSpec,
    canonical_correspondence_command_hash_v1,
)


class CorrespondenceCompilationTestMixin:
    def build_context(self):
        self.user = CareAPITestBase.create_user(
            self,
            first_name="Ada",
            last_name="Clinician",
            qualification="MD",
            doctor_medical_council_registration="REG-001",
            verified=True,
        )
        self.facility = CareAPITestBase.create_facility(self, user=self.user)
        self.organization = CareAPITestBase.create_facility_organization(
            self, facility=self.facility, name="Urology Department"
        )
        self.patient = CareAPITestBase.create_patient(
            self,
            name="Synthetic Patient",
            date_of_birth=date(1970, 1, 2),
            instance_identifiers=[{"config": "MRN", "value": "MRN-CORR-001"}],
        )
        self.encounter = CareAPITestBase.create_encounter(
            self,
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
        )
        self.encounter.period = {"start": "2026-07-20T09:30:00Z"}
        self.encounter.external_identifier = "ENC-CORR-001"
        self.reason = baker.make(
            TagConfig,
            facility=self.facility,
            facility_organization=self.organization,
            status="active",
            display="Visible haematuria",
            category="clinical",
            resource="encounter",
        )
        self.encounter.tags = [self.reason.id]
        self.encounter.save(
            update_fields=[
                "period",
                "external_identifier",
                "tags",
                "modified_date",
            ]
        )
        if not EncounterOrganization.objects.filter(
            encounter=self.encounter, organization=self.organization
        ).exists():
            baker.make(
                EncounterOrganization,
                encounter=self.encounter,
                organization=self.organization,
            )
        self.questionnaire_organization = CareAPITestBase.create_organization(self)
        self.questionnaire = baker.make(
            Questionnaire,
            organization_cache=[self.questionnaire_organization.id],
            slug="generic-correspondence-form",
            title="Generic Correspondence Form",
            version="2026.1",
        )
        questionnaire_role = CareAPITestBase.create_role_with_permissions(
            self, [QuestionnairePermissions.can_submit_questionnaire.name]
        )
        CareAPITestBase.attach_role_organization_user(
            self, self.questionnaire_organization, self.user, questionnaire_role
        )
        self.role = CareAPITestBase.create_role_with_permissions(
            self,
            [
                EncounterPermissions.can_read_encounter.name,
                EncounterPermissions.can_read_encounter_clinical_data.name,
                EncounterPermissions.can_submit_encounter_questionnaire.name,
                EncounterPermissions.can_write_encounter.name,
                PatientPermissions.can_view_clinical_data.name,
                TemplatePermissions.can_read_template.name,
            ],
            role_name="Correspondence Clinician",
        )
        CareAPITestBase.attach_role_facility_organization_user(
            self, self.organization, self.user, self.role
        )
        self.submission = self._finalized_submission()
        self.artifact = self._artifact(self.submission)
        self.medication = self._medication(self.submission)
        self.template = self._template()
        self.url = reverse("correspondence-compilation-idempotent-compile")

    def _finalized_submission(self, **overrides):
        values = {
            "questionnaire": self.questionnaire,
            "patient": self.patient,
            "encounter": self.encounter,
            "status": FormSubmissionStatusChoices.submitted.value,
            "response_dump": {
                "assessment": "Needs review <carefully>",
                "measurement": 45,
            },
            "resource_version": 2,
            "workflow_finalized_at": timezone.now(),
            "workflow_finalized_by": self.user,
            "created_by": self.user,
            "updated_by": self.user,
        }
        values.update(overrides)
        source = FormSubmission(**values)
        source.finalized_snapshot_hash = finalized_form_submission_snapshot_hash(source)
        source.save(force_insert=True)
        create_finalized_form_series_head(submission=source, actor=self.user)
        return source

    def _artifact(self, source, **overrides):
        values = {
            "template": None,
            "name": f"Finalized form {source.external_id}",
            "internal_name": f"{uuid4()}.pdf",
            "associating_id": str(source.encounter.external_id),
            "upload_completed": True,
            "report_type": "encounter_report",
            "patient": source.patient,
            "encounter": source.encounter,
            "form_submission": source,
            "source_version": source.resource_version,
            "source_snapshot_hash": source.finalized_snapshot_hash,
            "artifact_sha256": "b" * 64,
            "generated_at": timezone.now(),
            "generated_by": self.user,
            "created_by": self.user,
            "updated_by": self.user,
            "meta": {"mime_type": "application/pdf"},
        }
        values.update(overrides)
        artifact = ReportUpload(**values)
        artifact.save(force_insert=True, skip_internal_name=True)
        return artifact

    def _medication(self, source, **overrides):
        values = {
            "status": "active",
            "intent": "order",
            "category": "outpatient",
            "priority": "routine",
            "do_not_perform": False,
            "medication": {"code": "GENERIC-001", "display": "Generic therapy"},
            "patient": source.patient,
            "encounter": source.encounter,
            "dosage_instruction": [{"text": "One unit daily"}],
            "authored_on": timezone.now(),
            "requester": self.user,
            "client_request_id": uuid4(),
            "client_request_payload_hash": "a" * 64,
            "created_by": self.user,
            "updated_by": self.user,
        }
        values.update(overrides)
        medication = baker.make(MedicationRequest, **values)
        baker.make(
            QuestionnaireResponse,
            subject_id=source.patient.external_id,
            patient=source.patient,
            encounter=source.encounter,
            form_submission=source,
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
        return medication

    def _template(self, **overrides):
        values = {
            "facility": self.facility,
            "slug": "generic-correspondence-template",
            "name": "Generic Correspondence",
            "status": "active",
            "template_data": (
                "<h1>Clinical correspondence</h1>"
                "<p>{{ patient.name }}</p>"
                "<p>{{ encounter.reason }}</p>"
                "{% if medications %}<p>{{ medications[0].display }}</p>{% endif %}"
                "<div>{{ form.readable_html }}</div>"
            ),
            "template_type": "encounter_report",
            "default_format": "html",
            "context": "encounter_base",
            "created_by": self.user,
            "updated_by": self.user,
        }
        values.update(overrides)
        template = Template(**values)
        template.save(force_insert=True)
        return template

    def _payload(self, **overrides):
        payload = {
            "client_request_id": str(uuid4()),
            "patient": str(self.patient.external_id),
            "encounter": str(self.encounter.external_id),
            "facility": str(self.facility.external_id),
            "department": str(self.organization.external_id),
            "encounter_reason": str(self.reason.external_id),
            "form_submission": str(self.submission.external_id),
            "form_source_version": self.submission.resource_version,
            "form_source_hash": self.submission.finalized_snapshot_hash,
            "form_artifact": str(self.artifact.external_id),
            "form_artifact_hash": self.artifact.artifact_sha256,
            "medication_actions": [
                {
                    "id": str(self.medication.external_id),
                    "client_request_id": str(self.medication.client_request_id),
                }
            ],
            "template": str(self.template.external_id),
            "template_version": self.template.resource_version,
            "template_hash": self.template.content_hash,
            "author": str(self.user.external_id),
        }
        payload.update(overrides)
        return payload

    def _amendment_request(self, source=None):
        source = source or self.submission
        return (
            reverse(
                "form_submission-idempotent-amend",
                kwargs={"external_id": source.external_id},
            ),
            {
                "client_request_id": str(uuid4()),
                "expected_version": source.resource_version,
                "patient": str(source.patient.external_id),
                "encounter": str(source.encounter.external_id),
                "questionnaire": source.questionnaire.slug,
                "amendment_type": "amendment",
                "reason": "Correct correspondence source",
                "response_dump": {
                    "assessment": "Corrected source",
                    "measurement": 46,
                },
            },
        )

    def _amend_source(self, source=None):
        url, payload = self._amendment_request(source)
        return self.client.post(url, payload, format="json")


class TestCorrespondenceCompilationAPI(
    CorrespondenceCompilationTestMixin, CareAPITestBase
):
    def setUp(self):
        super().setUp()
        self.build_context()
        self.client.force_authenticate(user=self.user)

    def _compile(self, payload=None):
        return self.client.post(self.url, payload or self._payload(), format="json")

    def test_compiles_deterministic_sanitized_immutable_snapshot(self):
        response = self._compile()

        self.assertEqual(response.status_code, HTTPStatus.CREATED)
        self.assertEqual(
            set(response.json()), {"client_request_id", "replayed", "compilation"}
        )
        body = response.json()["compilation"]
        self.assertFalse(response.json()["replayed"])
        self.assertEqual(body["patient"], str(self.patient.external_id))
        self.assertEqual(body["encounter"], str(self.encounter.external_id))
        self.assertEqual(body["form_submission"], str(self.submission.external_id))
        self.assertEqual(body["form_source_version"], 2)
        self.assertEqual(
            body["form_source_hash"], self.submission.finalized_snapshot_hash
        )
        self.assertEqual(body["form_artifact"], str(self.artifact.external_id))
        self.assertEqual(body["template"], str(self.template.external_id))
        self.assertEqual(body["template_version"], self.template.resource_version)
        self.assertEqual(body["author"], str(self.user.external_id))
        self.assertIn("Synthetic Patient", body["compiled_text"])
        self.assertIn("Visible haematuria", body["compiled_text"])
        self.assertIn("Generic therapy", body["compiled_text"])
        self.assertIn("measurement", body["compiled_text"])
        self.assertIn("45", body["compiled_text"])
        self.assertNotIn("Source provenance", body["compiled_text"])
        self.assertNotIn("MRN-CORR-001", body["compiled_text"])
        self.assertNotIn("REG-001", body["compiled_text"])
        self.assertNotIn("Local clinician", body["compiled_text"])
        self.assertNotIn("<carefully>", body["compiled_html"])
        self.assertIn("&lt;carefully&gt;", body["compiled_html"])
        self.assertEqual(len(body["compiled_hash"]), 64)
        self.assertEqual(response["ETag"], f'"{body["id"]}:{body["compiled_hash"]}"')

        compilation = CorrespondenceCompilation.objects.get()
        self.assertEqual(
            compilation.source_provenance["encounter"]["date"], "2026-07-20T09:30:00Z"
        )
        self.assertEqual(
            compilation.source_provenance["encounter"]["date_display"],
            "20 juli 2026",
        )
        self.assertEqual(
            compilation.source_provenance["department"]["name"], "Urology Department"
        )
        self.assertEqual(
            compilation.source_provenance["author"]["display"], "Ada Clinician"
        )
        self.assertEqual(
            compilation.source_provenance["author"]["registration"], "REG-001"
        )
        self.assertEqual(
            compilation.source_provenance["patient"]["identifiers"][0]["value"],
            "MRN-CORR-001",
        )
        self.assertEqual(
            compilation.medication_sources[0]["id"], str(self.medication.external_id)
        )
        self.assertEqual(CorrespondenceCompileCommand.objects.count(), 1)

    def test_amended_finalized_form_clones_medication_link_and_compiles(self):
        amend_payload = {
            "client_request_id": str(uuid4()),
            "expected_version": self.submission.resource_version,
            "patient": str(self.patient.external_id),
            "encounter": str(self.encounter.external_id),
            "questionnaire": self.questionnaire.slug,
            "amendment_type": "amendment",
            "reason": "Corrected the finalized clinical narrative",
            "response_dump": {
                "assessment": "Corrected assessment",
                "clinicalActions": {
                    "actions": [
                        {
                            "kind": "medication-request",
                            "state": "confirmed",
                            "clientRequestId": str(self.medication.client_request_id),
                            "reference": {"id": str(self.medication.external_id)},
                        }
                    ]
                },
            },
        }
        amended_response = self.client.post(
            reverse(
                "form_submission-idempotent-amend",
                kwargs={"external_id": self.submission.external_id},
            ),
            amend_payload,
            format="json",
        )

        self.assertEqual(amended_response.status_code, HTTPStatus.CREATED)
        previous = self.submission
        amended = FormSubmission.objects.get(
            external_id=amended_response.json()["form_submission"]["id"]
        )
        source_link = QuestionnaireResponse.objects.get(
            form_submission=previous,
            structured_response_type="medication_request",
        )
        cloned_link = QuestionnaireResponse.objects.get(
            form_submission=amended,
            structured_response_type="medication_request",
        )
        self.assertNotEqual(source_link.pk, cloned_link.pk)
        self.assertEqual(
            source_link.structured_responses,
            cloned_link.structured_responses,
        )

        self.submission = amended
        self.artifact = self._artifact(amended)
        compiled = self._compile()

        self.assertEqual(compiled.status_code, HTTPStatus.CREATED)
        self.assertEqual(
            compiled.json()["compilation"]["form_submission"],
            str(amended.external_id),
        )
        self.assertEqual(
            compiled.json()["compilation"]["medication_sources"][0]["id"],
            str(self.medication.external_id),
        )

    def test_exact_retry_replays_but_new_key_starts_independent_letter(self):
        payload = self._payload()
        created = self._compile(payload)
        exact = self._compile(payload)
        another_key = self._compile({**payload, "client_request_id": str(uuid4())})

        self.assertEqual(created.status_code, HTTPStatus.CREATED)
        self.assertEqual(exact.status_code, HTTPStatus.OK)
        self.assertEqual(another_key.status_code, HTTPStatus.CREATED)
        self.assertTrue(exact.json()["replayed"])
        self.assertFalse(another_key.json()["replayed"])
        ids = {
            created.json()["compilation"]["id"],
            exact.json()["compilation"]["id"],
            another_key.json()["compilation"]["id"],
        }
        self.assertEqual(len(ids), 2)
        self.assertEqual(CorrespondenceCompilation.objects.count(), 2)
        self.assertEqual(CorrespondenceCompileCommand.objects.count(), 2)

    def test_template_reason_comes_from_exact_finalized_note(self):
        source = self._finalized_submission(
            response_dump={
                "content": {
                    "noteText": (
                        "Reden van presentatie:\n"
                        "Macroscopische hematurie\n\n"
                        "Anamnese:\n"
                        "Pijnloos bloedverlies."
                    ),
                    "values": {"reasonForVisit": "Macroscopische hematurie"},
                }
            }
        )
        self.submission = source
        self.artifact = self._artifact(source)
        self.medication = self._medication(source)

        compiled = self._compile()

        self.assertEqual(compiled.status_code, HTTPStatus.CREATED)
        body = compiled.json()["compilation"]
        self.assertIn("Macroscopische hematurie", body["compiled_text"])
        self.assertNotIn("Visible haematuria", body["compiled_text"])
        self.assertEqual(
            body["source_provenance"]["encounter"]["reason"],
            "Visible haematuria",
        )
        self.assertEqual(
            body["source_provenance"]["form"]["presentation_reason"],
            "Macroscopische hematurie",
        )

    def test_pre_v2_command_hash_still_supports_exact_replay(self):
        payload = self._payload()
        created = self._compile(payload)
        legacy_hash = canonical_correspondence_command_hash_v1(
            CompileCorrespondenceSpec.model_validate(payload),
            actor_id=self.user.external_id,
        )
        command = CorrespondenceCompileCommand.objects.get()
        CorrespondenceCompileCommand.objects.filter(pk=command.pk).update(
            payload_hash=legacy_hash
        )
        CorrespondenceCompilation.objects.filter(
            external_id=created.json()["compilation"]["id"]
        ).update(source_fingerprint=legacy_hash)

        replay = self._compile(payload)

        self.assertEqual(replay.status_code, HTTPStatus.OK)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(
            replay.json()["compilation"]["id"],
            created.json()["compilation"]["id"],
        )

    def test_replay_never_substitutes_current_session_or_updated_template(self):
        payload = self._payload()
        created = self._compile(payload)
        original = created.json()["compilation"]
        self.user.first_name = "Changed"
        self.user.save(update_fields=["first_name"])
        self.template.template_data = "<p>Changed template</p>"
        self.template.save(update_fields=["template_data"])

        replay = self._compile(payload)

        self.assertEqual(replay.status_code, HTTPStatus.OK)
        self.assertEqual(replay.json()["compilation"], original)
        frozen = CorrespondenceCompilation.objects.get(external_id=original["id"])
        self.assertEqual(frozen.source_provenance["author"]["display"], "Ada Clinician")
        self.assertNotIn(
            "Changed template", replay.json()["compilation"]["compiled_text"]
        )

    def test_amendment_preserves_exact_replay_but_blocks_new_compilation_key(self):
        payload = self._payload()
        created = self._compile(payload)
        amended = self._amend_source()
        exact = self._compile(payload)
        new_key = self._compile({**payload, "client_request_id": str(uuid4())})

        self.assertEqual(created.status_code, HTTPStatus.CREATED)
        self.assertEqual(amended.status_code, HTTPStatus.CREATED, amended.json())
        self.assertEqual(exact.status_code, HTTPStatus.OK)
        self.assertTrue(exact.json()["replayed"])
        self.assertEqual(new_key.status_code, HTTPStatus.CONFLICT)
        self.assertEqual(
            new_key.json()["errors"][0]["type"],
            "correspondence_source_stale",
        )
        self.assertEqual(CorrespondenceCompilation.objects.count(), 1)

    def test_retired_template_and_reason_preserve_replay_but_block_new_key(self):
        payload = self._payload()
        self.assertEqual(self._compile(payload).status_code, HTTPStatus.CREATED)
        self.template.status = "retired"
        self.template.save(update_fields=["status", "modified_date"])
        self.reason.status = "retired"
        self.reason.save(update_fields=["status", "modified_date"])

        exact = self._compile(payload)
        new_key = self._compile({**payload, "client_request_id": str(uuid4())})

        self.assertEqual(exact.status_code, HTTPStatus.OK)
        self.assertTrue(exact.json()["replayed"])
        self.assertIn(
            new_key.status_code,
            {HTTPStatus.NOT_FOUND, HTTPStatus.UNPROCESSABLE_ENTITY},
        )

    def test_exact_replay_requires_current_read_but_not_new_write_authorization(self):
        payload = self._payload()
        self.assertEqual(self._compile(payload).status_code, HTTPStatus.CREATED)

        with patch(
            "care_suriname.api.viewsets.correspondence.write_report_authorizer",
            side_effect=PermissionDenied("encounter is no longer writable"),
        ):
            exact = self._compile(payload)
            new_key = self._compile({**payload, "client_request_id": str(uuid4())})

        self.assertEqual(exact.status_code, HTTPStatus.OK)
        self.assertTrue(exact.json()["replayed"])
        self.assertEqual(new_key.status_code, HTTPStatus.FORBIDDEN)
        self.assertEqual(CorrespondenceCompileCommand.objects.count(), 1)

    def test_same_key_changed_payload_or_other_source_is_non_leaking_409(self):
        payload = self._payload()
        self.assertEqual(self._compile(payload).status_code, HTTPStatus.CREATED)

        changed = self._compile({**payload, "template_hash": "c" * 64})

        self.assertEqual(changed.status_code, HTTPStatus.CONFLICT)
        self.assertNotIn("compilation", changed.json())
        other = self._finalized_submission(response_dump={"other": "source"})
        other_artifact = self._artifact(other)
        other_medication = self._medication(other)
        other_payload = {
            **payload,
            "form_submission": str(other.external_id),
            "form_source_hash": other.finalized_snapshot_hash,
            "form_artifact": str(other_artifact.external_id),
            "form_artifact_hash": other_artifact.artifact_sha256,
            "medication_actions": [
                {
                    "id": str(other_medication.external_id),
                    "client_request_id": str(other_medication.client_request_id),
                }
            ],
        }
        conflict = self._compile(other_payload)
        self.assertEqual(conflict.status_code, HTTPStatus.CONFLICT)
        self.assertNotIn("compilation", conflict.json())

    def test_strict_request_rejects_unknown_fields_duplicate_actions_and_uuid_v1(self):
        cases = [
            self._payload(letter_body="browser supplied"),
            self._payload(client_request_id=str(uuid1())),
            self._payload(
                medication_actions=[
                    self._payload()["medication_actions"][0],
                    self._payload()["medication_actions"][0],
                ]
            ),
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertEqual(
                    self._compile(payload).status_code, HTTPStatus.BAD_REQUEST
                )
        self.assertEqual(CorrespondenceCompilation.objects.count(), 0)

    def test_draft_stale_and_tampered_form_sources_fail_closed(self):
        draft = baker.make(
            FormSubmission,
            questionnaire=self.questionnaire,
            patient=self.patient,
            encounter=self.encounter,
            status=FormSubmissionStatusChoices.draft.value,
            response_dump={"draft": True},
            created_by=self.user,
            updated_by=self.user,
        )
        draft_artifact = self.artifact
        draft_payload = self._payload(
            form_submission=str(draft.external_id),
            form_source_version=draft.resource_version,
            form_source_hash="d" * 64,
            form_artifact=str(draft_artifact.external_id),
        )
        self.assertEqual(self._compile(draft_payload).status_code, HTTPStatus.CONFLICT)

        stale = self._compile(self._payload(form_source_version=99))
        self.assertEqual(stale.status_code, HTTPStatus.CONFLICT)
        self.assertNotIn("compilation", stale.json())

        FormSubmission._base_manager.filter(pk=self.submission.pk).update(  # noqa: SLF001
            response_dump={"tampered": True}
        )
        tampered = self._compile()
        self.assertEqual(tampered.status_code, HTTPStatus.CONFLICT)
        self.assertEqual(CorrespondenceCompilation.objects.count(), 0)

    def test_wrong_patient_encounter_facility_department_reason_and_author_are_404(
        self,
    ):
        other_patient = self.create_patient()
        other_encounter = self.create_encounter(
            patient=other_patient,
            facility=self.facility,
            organization=self.organization,
        )
        other_department = self.create_facility_organization(
            facility=self.facility, name="Other Department"
        )
        other_reason = baker.make(
            TagConfig,
            status="active",
            resource="encounter",
            category="clinical",
        )
        cases = [
            {"patient": str(other_patient.external_id)},
            {"encounter": str(other_encounter.external_id)},
            {"facility": str(uuid4())},
            {"department": str(other_department.external_id)},
            {"encounter_reason": str(other_reason.external_id)},
            {"author": str(uuid4())},
        ]
        for change in cases:
            with self.subTest(change=change):
                response = self._compile(self._payload(**change))
                self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)
                self.assertNotIn("compilation", response.json())
        self.assertEqual(CorrespondenceCompilation.objects.count(), 0)

    def test_artifact_template_and_medication_source_validation(self):
        cases = []
        ReportUpload._base_manager.filter(pk=self.artifact.pk).update(is_archived=True)  # noqa: SLF001
        cases.append(self._compile())
        ReportUpload._base_manager.filter(pk=self.artifact.pk).update(is_archived=False)  # noqa: SLF001

        cases.append(self._compile(self._payload(form_artifact_hash="e" * 64)))
        cases.append(self._compile(self._payload(template_hash="f" * 64)))
        cases.append(
            self._compile(
                self._payload(
                    medication_actions=[
                        {
                            "id": str(self.medication.external_id),
                            "client_request_id": str(uuid4()),
                        }
                    ]
                )
            )
        )
        for response in cases:
            self.assertIn(
                response.status_code,
                [HTTPStatus.CONFLICT, HTTPStatus.UNPROCESSABLE_ENTITY],
            )
            self.assertNotIn("compilation", response.json())
        self.assertEqual(CorrespondenceCompilation.objects.count(), 0)

    def test_medication_key_tampering_and_missing_or_dirty_ledger_are_blocked(self):
        tampered = self._compile(
            self._payload(
                medication_actions=[
                    {
                        "id": str(self.medication.external_id),
                        "client_request_id": str(uuid4()),
                    }
                ]
            )
        )
        self.assertEqual(tampered.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)

        original_request_id = self.medication.client_request_id
        MedicationRequest._base_manager.filter(pk=self.medication.pk).update(  # noqa: SLF001
            client_request_id=None,
            client_request_payload_hash=None,
        )
        missing = self._compile()
        self.assertEqual(missing.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)

        MedicationRequest._base_manager.filter(pk=self.medication.pk).update(  # noqa: SLF001
            client_request_id=original_request_id,
            client_request_payload_hash="not-a-canonical-sha256",
        )
        dirty = self._compile()
        self.assertEqual(dirty.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)
        self.assertEqual(CorrespondenceCompilation.objects.count(), 0)

    def test_medication_action_list_must_name_every_linked_confirmed_action(self):
        self._medication(self.submission)

        omitted = self._compile()

        self.assertEqual(omitted.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)
        self.assertEqual(CorrespondenceCompilation.objects.count(), 0)

    def test_missing_or_ambiguous_medication_link_and_cross_context_are_blocked(self):
        QuestionnaireResponse.objects.all().delete()
        missing = self._compile()
        self.assertEqual(missing.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)

        baker.make(
            QuestionnaireResponse,
            subject_id=self.patient.external_id,
            patient=self.patient,
            encounter=self.encounter,
            form_submission=self.submission,
            structured_response_type="medication_request",
            structured_responses={"medication_request": {}},
        )
        malformed = self._compile()
        self.assertEqual(malformed.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)
        QuestionnaireResponse.objects.all().delete()

        for _ in range(2):
            baker.make(
                QuestionnaireResponse,
                subject_id=self.patient.external_id,
                patient=self.patient,
                encounter=self.encounter,
                form_submission=self.submission,
                structured_response_type="medication_request",
                structured_responses={
                    "medication_request": {"id": str(self.medication.external_id)}
                },
            )
        ambiguous = self._compile()
        self.assertEqual(ambiguous.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)

        other_patient = self.create_patient()
        MedicationRequest._base_manager.filter(pk=self.medication.pk).update(  # noqa: SLF001
            patient=other_patient
        )
        cross_context = self._compile()
        self.assertEqual(cross_context.status_code, HTTPStatus.NOT_FOUND)

    def test_entered_in_error_or_deleted_medication_link_is_blocked(self):
        response = QuestionnaireResponse._base_manager.get(  # noqa: SLF001
            form_submission=self.submission,
            structured_response_type="medication_request",
        )
        QuestionnaireResponse._base_manager.filter(pk=response.pk).update(  # noqa: SLF001
            status="entered_in_error"
        )
        entered_in_error = self._compile()
        self.assertEqual(entered_in_error.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)

        QuestionnaireResponse._base_manager.filter(pk=response.pk).update(  # noqa: SLF001
            status="completed",
            deleted=True,
        )
        deleted = self._compile()
        self.assertEqual(deleted.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)
        self.assertEqual(CorrespondenceCompilation.objects.count(), 0)

    def test_unresolved_invalid_conditional_and_unsafe_html_fail_closed(self):
        cases = [
            "<p>{{ missing_token }}</p>",
            "{% if patient %}<p>broken{% endif",
            "<script>alert(1)</script><p>Unsafe</p>",
            '<p onclick="alert(1)">Unsafe</p>',
            "<p>[[PLACEHOLDER: recipient]]</p>",
        ]
        for template_data in cases:
            with self.subTest(template_data=template_data):
                template = self._template(template_data=template_data)
                response = self._compile(
                    self._payload(
                        template=str(template.external_id),
                        template_version=template.resource_version,
                        template_hash=template.content_hash,
                    )
                )
                self.assertEqual(response.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)
                self.assertNotIn("compilation", response.json())

    def test_unresolved_placeholder_in_finalized_form_is_blocked(self):
        source = self._finalized_submission(
            response_dump={"required": "${missing_value}"}
        )
        artifact = self._artifact(source)
        medication = self._medication(source)
        response = self._compile(
            self._payload(
                form_submission=str(source.external_id),
                form_source_version=source.resource_version,
                form_source_hash=source.finalized_snapshot_hash,
                form_artifact=str(artifact.external_id),
                form_artifact_hash=artifact.artifact_sha256,
                medication_actions=[
                    {
                        "id": str(medication.external_id),
                        "client_request_id": str(medication.client_request_id),
                    }
                ],
            )
        )

        self.assertEqual(response.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)
        self.assertEqual(CorrespondenceCompilation.objects.count(), 0)

    def test_missing_verified_patient_or_author_identity_is_blocked(self):
        self.user.verified = False
        self.user.save(update_fields=["verified"])
        unverified_author = self._compile()
        self.assertEqual(unverified_author.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)

        self.user.verified = True
        self.user.save(update_fields=["verified"])
        self.patient.instance_identifiers = []
        self.patient.facility_identifiers = {}
        self.patient.save(
            update_fields=[
                "instance_identifiers",
                "facility_identifiers",
                "organization_cache",
                "users_cache",
            ]
        )
        missing_patient_identifier = self._compile()
        self.assertEqual(
            missing_patient_identifier.status_code,
            HTTPStatus.UNPROCESSABLE_ENTITY,
        )

    def test_unauthenticated_and_read_only_author_are_denied_without_commit(self):
        payload = self._payload()
        self.client.logout()
        unauthenticated = self._compile(payload)

        read_only = self.create_user()
        role = self.create_role_with_permissions(
            [
                EncounterPermissions.can_read_encounter.name,
                EncounterPermissions.can_read_encounter_clinical_data.name,
                PatientPermissions.can_view_clinical_data.name,
                TemplatePermissions.can_read_template.name,
            ]
        )
        self.attach_role_facility_organization_user(self.organization, read_only, role)
        self.client.force_authenticate(user=read_only)
        denied = self._compile(
            {
                **payload,
                "client_request_id": str(uuid4()),
                "author": str(read_only.external_id),
            }
        )

        self.assertEqual(unauthenticated.status_code, HTTPStatus.FORBIDDEN)
        self.assertEqual(denied.status_code, HTTPStatus.FORBIDDEN)
        self.assertEqual(CorrespondenceCompilation.objects.count(), 0)

    def test_command_ledger_failure_rolls_back_and_exact_key_can_retry(self):
        payload = self._payload()
        with patch.object(
            CorrespondenceCompileCommand.objects,
            "create",
            side_effect=RuntimeError("synthetic ledger failure"),
        ):
            failed = self._compile(payload)

        self.assertEqual(failed.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(
            failed.json()["errors"][0]["type"],
            "correspondence_compilation_failed",
        )
        self.assertEqual(CorrespondenceCompilation.objects.count(), 0)
        self.assertEqual(CorrespondenceCompileCommand.objects.count(), 0)

        recovered = self._compile(payload)
        self.assertEqual(recovered.status_code, HTTPStatus.CREATED)

    def test_retrieve_is_authenticated_authorized_and_snapshot_is_immutable(self):
        created = self._compile().json()["compilation"]
        url = reverse(
            "correspondence-compilation-detail",
            kwargs={"external_id": created["id"]},
        )
        retrieved = self.client.get(url)
        self.client.logout()
        unauthenticated = self.client.get(url)

        self.assertEqual(retrieved.status_code, HTTPStatus.OK)
        self.assertEqual(retrieved.json(), created)
        self.assertEqual(unauthenticated.status_code, HTTPStatus.FORBIDDEN)

        compilation = CorrespondenceCompilation.objects.get()
        compilation.compiled_text = "changed"
        with self.assertRaises(DjangoValidationError):
            compilation.save()

    def test_retired_live_sources_preserve_exact_historical_replay(self):
        payload = self._payload()
        created = self._compile(payload)
        self.assertEqual(created.status_code, HTTPStatus.CREATED)
        detail_url = reverse(
            "correspondence-compilation-detail",
            kwargs={"external_id": created.json()["compilation"]["id"]},
        )
        mutation_cases = [
            (FormSubmission, self.submission.pk),
            (ReportUpload, self.artifact.pk),
            (Template, self.template.pk),
            (MedicationRequest, self.medication.pk),
        ]
        for model, pk in mutation_cases:
            with self.subTest(model=model.__name__):
                model._base_manager.filter(pk=pk).update(deleted=True)  # noqa: SLF001
                replay = self._compile(payload)
                retrieved = self.client.get(detail_url)
                new_key = self._compile({**payload, "client_request_id": str(uuid4())})
                self.assertEqual(replay.status_code, HTTPStatus.OK)
                self.assertTrue(replay.json()["replayed"])
                self.assertEqual(retrieved.status_code, HTTPStatus.OK)
                self.assertIn(
                    new_key.status_code,
                    {
                        HTTPStatus.CONFLICT,
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                    },
                )
                model._base_manager.filter(pk=pk).update(deleted=False)  # noqa: SLF001

    def test_renderer_is_deterministic_for_fixed_sources_and_rejects_bad_markup(self):
        context = {
            "patient": {
                "id": "patient",
                "name": "Patient",
                "date_of_birth": "1970-01-02",
                "identifiers": [{"config": "MRN", "value": "MRN-1"}],
            },
            "encounter": {
                "id": "encounter",
                "date": "2026-07-20",
                "reason": "Reason",
                "facility": {"name": "Facility"},
                "department": {"name": "Department"},
            },
            "author": {
                "id": "author",
                "display": "Clinician",
                "professional_role": "Clinician",
                "qualification": "MD",
                "registration": "REG-1",
                "facility": {"name": "Facility"},
                "department": {"name": "Department"},
            },
            "form": {
                "id": "form",
                "version": 1,
                "hash": "a" * 64,
                "artifact_id": "artifact",
                "readable_html": "<p>Form</p>",
            },
            "template": {"id": "template", "version": 1, "hash": "b" * 64},
            "medications": [],
            "medications_readable_html": "<p>None</p>",
        }
        provenance = {"contract": "test"}
        compiled_at = timezone.now()
        args = {
            "compilation_id": uuid4(),
            "compiled_at": compiled_at,
            "template_data": "<p>{{ patient.name }}</p>",
            "context": context,
            "provenance": provenance,
        }

        first = compile_correspondence_html(**args)
        second = compile_correspondence_html(**args)

        self.assertEqual(first, second)
        self.assertIn("Patient", first[1])

    def test_compiler_removes_known_azp_full_document_template_chrome(self):
        context = {
            "patient": {
                "name": "DEMO Patient",
                "date_of_birth": "1970-01-02",
                "identifiers": [{"value": "+597000000"}],
            },
            "encounter": {"date": "2026-09-13", "reason": "Urineretentie"},
            "author": {"display": "Synthetic Clinician"},
            "form": {
                "readable_html": readable_form_html(
                    {"content": {"noteText": "Klinische notitie"}}
                )
            },
        }
        template_data = """
            <div>AZP</div>
            <div>moving lives forward</div>
            <div>Academisch Ziekenhuis Paramaribo</div>
            <div>Afdeling Urologie</div>
            <div>Flustraat 1 · Paramaribo, Suriname</div>
            <div>Centraal: +597 442222</div>
            <div>Polikliniek Urologie: +597 8629846 · toestel 251</div>
            <h1>Correspondentiebrief Urologie</h1>
            <div>Patiënt</div>
            <div>{{ patient.name }}</div>
            <div>Geboortedatum</div>
            <div>{{ patient.date_of_birth }}</div>
            <div>Patiëntnummer</div>
            <div>{{ patient.identifiers[0].value }}</div>
            <div>Datum</div>
            <div>{{ encounter.date }}</div>
            <p>Geachte collega,</p>
            <p>Bovengenoemde patiënt werd op de polikliniek beoordeeld.</p>
            <h2>Reden van komst</h2>
            <p>{{ encounter.reason }}</p>
            <h2>Samenvatting van de notitie</h2>
            {{ form.readable_html }}
            <p>Met collegiale groet,</p>
            <div>{{ author.display }}</div>
            <div>Doctor · Urologie</div>
            <div>AZP</div>
            <div>Correspondentiebrief · Afdeling Urologie</div>
            <div>Vertrouwelijke medische informatie</div>
        """

        compiled_html, compiled_text = compile_correspondence_html(
            compilation_id=uuid4(),
            compiled_at=timezone.now(),
            template_data=template_data,
            context=context,
            provenance={"contract": "test"},
        )

        self.assertTrue(compiled_text.startswith("Geachte collega,"))
        self.assertIn("Bovengenoemde patiënt", compiled_text)
        self.assertIn("Urineretentie", compiled_text)
        self.assertEqual(compiled_text.count("Klinische notitie"), 1)
        self.assertIn("Met collegiale groet,", compiled_text)
        for server_owned_value in (
            "Academisch Ziekenhuis Paramaribo",
            "Flustraat 1",
            "Patiëntnummer",
            "+597000000",
            "Synthetic Clinician",
            "Vertrouwelijke medische informatie",
        ):
            self.assertNotIn(server_owned_value, compiled_text)
            self.assertNotIn(server_owned_value, compiled_html)

    def test_readable_form_prefers_the_final_clinical_note_without_identifiers(self):
        rendered = str(
            readable_form_html(
                {
                    "schema": "care.urology.encounter-owned-form-submission",
                    "identity": {"patientId": "patient-secret-reference"},
                    "content": {
                        "narrativePreview": "Korte samenvatting",
                        "noteText": (
                            "Reden van verwijzing:\n"
                            "BPH-evaluatie wegens lower urinary tract symptoms.\n\n"
                            "Anamnese:\n"
                            "Mictieklachten sinds drie maanden."
                        ),
                        "values": {"internal_field": "technical value"},
                    },
                }
            )
        )

        self.assertNotIn("Laatste definitieve notitie", rendered)
        self.assertNotIn("Reden van verwijzing", rendered)
        self.assertNotIn("BPH-evaluatie wegens lower urinary tract symptoms.", rendered)
        self.assertIn("Anamnese:<br>Mictieklachten sinds drie maanden.", rendered)
        self.assertNotIn("patient-secret-reference", rendered)
        self.assertNotIn("internal_field", rendered)

    def test_readable_form_normalizes_generated_diagnosis_history_layout(self):
        readable = readable_form_html(
            {
                "content": {
                    "noteText": (
                        "Algemene voorgeschiedenis:\n"
                        "- Asthma — 01-01-2000: Allergische Asthma\n"
                        "Urologische voorgeschiedenis:\n"
                        "- Uretersteen — 01-07-2026: "
                        "CT IVP: Distale uretersteen van 10mm\n"
                        "Allergie:\nGeen"
                    ),
                },
            }
        )
        rendered = str(readable)

        self.assertIn(
            "Algemene voorgeschiedenis:<br>"
            "- Asthma:<br>"
            "  01-01-2000: Allergische Asthma",
            rendered,
        )
        self.assertIn(
            "Urologische voorgeschiedenis:<br>"
            "- Uretersteen:<br>"
            "  01-07-2026: CT IVP: Distale uretersteen van 10mm",
            rendered,
        )
        self.assertNotIn("—", rendered)

        _, compiled_text = compile_correspondence_html(
            compilation_id=uuid4(),
            compiled_at=timezone.now(),
            template_data="<div>{{ form.readable_html }}</div>",
            context={"form": {"readable_html": readable}},
            provenance={"contract": "test"},
        )
        self.assertIn(
            "Algemene voorgeschiedenis:\n- Asthma:\n  01-01-2000: Allergische Asthma",
            compiled_text,
        )
        self.assertNotIn("—", compiled_text)

    def test_renderer_rejects_unbounded_or_oversized_templates_and_output(self):
        context = {
            "patient": {
                "id": "patient",
                "name": "Patient",
                "date_of_birth": "1970-01-02",
                "identifiers": [{"config": "MRN", "value": "MRN-1"}],
                "large": "x" * 500_001,
            },
            "encounter": {
                "id": "encounter",
                "date": "2026-07-20",
                "reason": "Reason",
                "facility": {"name": "Facility"},
                "department": {"name": "Department"},
            },
            "author": {
                "id": "author",
                "display": "Clinician",
                "professional_role": "Clinician",
                "qualification": "MD",
                "registration": "REG-1",
                "facility": {"name": "Facility"},
                "department": {"name": "Department"},
            },
            "form": {
                "id": "form",
                "version": 1,
                "hash": "a" * 64,
                "artifact_id": "artifact",
                "readable_html": "<p>Form</p>",
            },
            "template": {"id": "template", "version": 1, "hash": "b" * 64},
            "medications": [],
            "medications_readable_html": "<p>None</p>",
        }
        common = {
            "compilation_id": uuid4(),
            "compiled_at": timezone.now(),
            "context": context,
            "provenance": {"contract": "test"},
        }
        templates = [
            "x" * 100_001,
            "{% for item in medications %}{{ item }}{% endfor %}",
            "{{ 'x' * 1000000 }}",
            "{{ patient.name }}" * 1_001,
            "{{ patient.large }}",
        ]
        for template_data in templates:
            with (
                self.subTest(template_data=template_data[:80]),
                self.assertRaises(CorrespondenceCompilationError),
            ):
                compile_correspondence_html(
                    **common,
                    template_data=template_data,
                )


class TestCorrespondenceCompilationConcurrency(
    CorrespondenceCompilationTestMixin, TransactionTestCase
):
    fake = CareAPITestBase.fake
    reset_sequences = True

    def setUp(self):
        cache.clear()
        FacilityPatientNameIdentifierConfig.CACHED_CONFIG.clear()
        NameIdentifierConfig.CACHED_CONFIG.clear()
        PhoneNumberIdentifierConfig.CACHED_CONFIG.clear()
        self.build_context()
        self.url = reverse("correspondence-compilation-idempotent-compile")

    def _post_concurrently(self, requests):
        barrier = Barrier(2)

        def post_request(request):
            url, payload = (
                request if isinstance(request, tuple) else (self.url, request)
            )
            close_old_connections()
            client = APIClient()
            client.force_authenticate(user=self.user)
            barrier.wait()
            response = client.post(url, copy.deepcopy(payload), format="json")
            close_old_connections()
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(post_request, requests))

    def test_concurrent_same_key_compiles_once(self):
        payload = self._payload()
        responses = self._post_concurrently([payload, payload])

        self.assertEqual(sorted(code for code, _ in responses), [200, 201])
        self.assertEqual(CorrespondenceCompilation.objects.count(), 1)
        self.assertEqual(CorrespondenceCompileCommand.objects.count(), 1)

    def test_concurrent_distinct_keys_create_independent_letters(self):
        responses = self._post_concurrently([self._payload(), self._payload()])

        self.assertEqual(
            sorted(code for code, _ in responses),
            [HTTPStatus.CREATED, HTTPStatus.CREATED],
        )
        self.assertEqual(CorrespondenceCompilation.objects.count(), 2)
        self.assertEqual(CorrespondenceCompileCommand.objects.count(), 2)

    def test_amendment_and_compilation_have_only_serial_outcomes(self):
        responses = self._post_concurrently(
            [
                self._amendment_request(),
                (self.url, self._payload()),
            ]
        )

        self.assertEqual(responses[0][0], HTTPStatus.CREATED)
        self.assertIn(
            responses[1][0],
            {HTTPStatus.CREATED, HTTPStatus.CONFLICT},
        )
        expected_compilations = 1 if responses[1][0] == HTTPStatus.CREATED else 0
        self.assertEqual(
            CorrespondenceCompilation.objects.count(), expected_compilations
        )
        self.assertEqual(
            CorrespondenceCompileCommand.objects.count(), expected_compilations
        )

    def test_concurrent_same_key_different_sources_is_non_leaking(self):
        other = self._finalized_submission(response_dump={"other": True})
        other_artifact = self._artifact(other)
        other_medication = self._medication(other)
        request_id = str(uuid4())
        first = self._payload(client_request_id=request_id)
        second = self._payload(
            client_request_id=request_id,
            form_submission=str(other.external_id),
            form_source_hash=other.finalized_snapshot_hash,
            form_artifact=str(other_artifact.external_id),
            form_artifact_hash=other_artifact.artifact_sha256,
            medication_actions=[
                {
                    "id": str(other_medication.external_id),
                    "client_request_id": str(other_medication.client_request_id),
                }
            ],
        )

        responses = self._post_concurrently([first, second])

        self.assertEqual(sorted(code for code, _ in responses), [201, 409])
        conflict = next(body for code, body in responses if code == HTTPStatus.CONFLICT)
        self.assertNotIn("compilation", conflict)
        self.assertEqual(CorrespondenceCompilation.objects.count(), 1)
        self.assertEqual(CorrespondenceCompileCommand.objects.count(), 1)
