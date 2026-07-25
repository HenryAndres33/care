import copy
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import close_old_connections
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from care.emr.models.correspondence_letter import (
    CorrespondenceLetter,
    CorrespondenceLetterCommand,
    CorrespondenceLetterRevision,
)
from care.emr.models.correspondence_review import CorrespondenceReview
from care.emr.models.report.report_upload import ReportUpload
from care.emr.reports.correspondence_letter import build_correspondence_letter_html
from care.emr.resources.correspondence_letter import (
    correspondence_letter_body_hash,
    correspondence_letter_revision_hash,
)
from care.emr.signals.patient.facility_name_identifier import (
    FacilityPatientNameIdentifierConfig,
)
from care.emr.signals.patient.name_identifier import NameIdentifierConfig
from care.emr.signals.patient.phone_number_identifier import (
    PhoneNumberIdentifierConfig,
)
from care.emr.tests.test_correspondence_review import CorrespondenceReviewTestMixin
from care.utils.tests.base import CareAPITestBase


class TestCorrespondenceLetterAPI(
    CorrespondenceReviewTestMixin,
    CareAPITestBase,
):
    def setUp(self):
        super().setUp()
        self.build_review_context()
        response = self._bind()
        if response.status_code != HTTPStatus.CREATED:
            raise AssertionError(response.json())
        self.review = CorrespondenceReview.objects.get()
        self.create_url = reverse("correspondence-letter-idempotent-create")
        self.list_url = reverse("correspondence-letter-list")
        self.put_patcher = patch.object(
            ReportUpload.files_manager, "put_object", return_value={}
        )
        self.read_patcher = patch.object(
            ReportUpload.files_manager,
            "read_signed_url",
            return_value="https://object.example/authenticated-correspondence",
        )
        self.delete_patcher = patch.object(
            ReportUpload.files_manager, "delete_object", return_value={}
        )
        self.render_patcher = patch(
            "care.emr.api.viewsets.correspondence_letter.render_correspondence_letter_pdf",
            return_value=b"%PDF-1.7\nsynthetic-correspondence",
        )
        self.put_object = self.put_patcher.start()
        self.read_signed_url = self.read_patcher.start()
        self.delete_object = self.delete_patcher.start()
        self.render_pdf = self.render_patcher.start()
        self.addCleanup(self.put_patcher.stop)
        self.addCleanup(self.read_patcher.stop)
        self.addCleanup(self.delete_patcher.stop)
        self.addCleanup(self.render_patcher.stop)

    def _context(self):
        return {
            "review_binding": str(self.review.external_id),
            "review_hash": self.review.review_hash,
            "patient": str(self.patient.external_id),
            "encounter": str(self.encounter.external_id),
            "facility": str(self.facility.external_id),
            "department": str(self.organization.external_id),
            "author": str(self.user.external_id),
        }

    def _create_payload(self, **overrides):
        payload = {
            "client_request_id": str(uuid4()),
            **self._context(),
            "body": "Dear colleague,\n\nPlease review this patient.",
        }
        payload.update(overrides)
        return payload

    def _revision_payload(self, revision, **overrides):
        payload = {
            "client_request_id": str(uuid4()),
            **self._context(),
            "expected_version": revision.resource_version,
        }
        payload.update(overrides)
        return payload

    def _create(self, payload=None):
        return self.client.post(
            self.create_url,
            payload or self._create_payload(),
            format="json",
        )

    def _list(self, review=None, **overrides):
        review = review or self.review
        query = {
            "review_binding": str(review.external_id),
            "patient": str(review.patient.external_id),
            "encounter": str(review.encounter.external_id),
        }
        query.update(overrides)
        return self.client.get(self.list_url, query)

    def _second_review_for_same_encounter(self):
        source = self._finalized_submission(
            response_dump={"assessment": "Second independent correspondence"}
        )
        artifact = self._artifact(source)
        medication = self._medication(source)
        compilation_response = self.client.post(
            self.url,
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
            ),
            format="json",
        )
        self.assertEqual(
            compilation_response.status_code,
            HTTPStatus.CREATED,
            compilation_response.json(),
        )
        compilation = compilation_response.json()["compilation"]
        review_response = self._bind(
            self._review_payload(
                compilation=compilation["id"],
                compilation_hash=compilation["compiled_hash"],
            )
        )
        self.assertEqual(
            review_response.status_code,
            HTTPStatus.CREATED,
            review_response.json(),
        )
        return CorrespondenceReview.objects.get(
            external_id=review_response.json()["review_binding"]["id"]
        )

    def _revise(self, revision, payload=None):
        payload = payload or self._revision_payload(
            revision,
            body="Dear colleague,\n\nThis is the revised clinical letter.",
        )
        return self.client.post(
            reverse(
                "correspondence-letter-idempotent-revise",
                kwargs={"external_id": revision.external_id},
            ),
            payload,
            format="json",
        )

    def _finalize(self, revision, payload=None):
        return self.client.post(
            reverse(
                "correspondence-letter-idempotent-finalize",
                kwargs={"external_id": revision.external_id},
            ),
            payload or self._revision_payload(revision),
            format="json",
        )

    def _created_revision(self):
        response = self._create()
        self.assertEqual(response.status_code, HTTPStatus.CREATED, response.json())
        return CorrespondenceLetterRevision.objects.get(
            external_id=response.json()["correspondence"]["id"]
        )

    def test_create_stores_server_draft_and_exposes_versions_etag_and_scoped_reads(
        self,
    ):
        payload = self._create_payload(body="Clinical draft body")
        response = self._create(payload)

        self.assertEqual(response.status_code, HTTPStatus.CREATED, response.json())
        data = response.json()["correspondence"]
        self.assertEqual(data["body"], "Clinical draft body")
        self.assertEqual(data["resource_version"], 1)
        self.assertEqual(data["status"], "draft")
        self.assertEqual(response["ETag"], f'"{data["id"]}:{data["revision_hash"]}"')
        detail = self.client.get(
            reverse(
                "correspondence-letter-detail",
                kwargs={"external_id": data["id"]},
            )
        )
        listing = self._list()
        self.assertEqual(detail.status_code, HTTPStatus.OK)
        self.assertEqual(listing.status_code, HTTPStatus.OK)
        self.assertEqual(listing.json()["count"], 1)
        self.assertEqual(listing.json()["results"][0]["resource_version"], 1)

    def test_list_isolated_by_exact_review_with_two_letters_in_same_encounter(self):
        first_revision = self._created_revision()
        second_review = self._second_review_for_same_encounter()
        second_create = self._create(
            self._create_payload(
                review_binding=str(second_review.external_id),
                review_hash=second_review.review_hash,
                body="Second independent letter lineage",
            )
        )
        self.assertEqual(second_create.status_code, HTTPStatus.CREATED)

        first_page = self._list(self.review)
        second_page = self._list(second_review)

        self.assertEqual(first_page.status_code, HTTPStatus.OK)
        self.assertEqual(second_page.status_code, HTTPStatus.OK)
        self.assertEqual(first_page.json()["count"], 1)
        self.assertEqual(second_page.json()["count"], 1)
        self.assertEqual(
            first_page.json()["results"][0]["id"],
            str(first_revision.external_id),
        )
        self.assertNotIn("Second independent letter lineage", str(first_page.json()))
        self.assertNotIn(first_revision.body, str(second_page.json()))

    def test_list_traverses_more_than_default_page_with_exact_count(self):
        first = self._created_revision()
        previous = first
        for version in range(2, 17):
            body = f"Immutable correspondence revision {version}"
            revision = CorrespondenceLetterRevision(
                letter=first.letter,
                previous_revision=previous,
                resource_version=version,
                status="draft",
                source_review_hash=self.review.review_hash,
                body=body,
                body_hash=correspondence_letter_body_hash(body),
                created_by=self.user,
                updated_by=self.user,
            )
            revision.revision_hash = correspondence_letter_revision_hash(revision)
            revision.save(force_insert=True)
            previous = revision

        first_page = self._list()
        second_page = self._list(offset=14)
        bounded_middle_page = self._list(limit=5, offset=5)

        self.assertEqual(first_page.status_code, HTTPStatus.OK)
        self.assertEqual(second_page.status_code, HTTPStatus.OK)
        self.assertEqual(bounded_middle_page.status_code, HTTPStatus.OK)
        self.assertEqual(first_page.json()["count"], 16)
        self.assertEqual(second_page.json()["count"], 16)
        self.assertEqual(bounded_middle_page.json()["count"], 16)
        self.assertEqual(len(first_page.json()["results"]), 14)
        self.assertEqual(len(second_page.json()["results"]), 2)
        self.assertEqual(len(bounded_middle_page.json()["results"]), 5)
        self.assertEqual(
            [
                item["resource_version"]
                for item in bounded_middle_page.json()["results"]
            ],
            [11, 10, 9, 8, 7],
        )
        versions = [
            item["resource_version"]
            for item in first_page.json()["results"] + second_page.json()["results"]
        ]
        self.assertEqual(versions, list(range(16, 0, -1)))

    def test_list_rejects_wrong_context_unknown_params_and_unsafe_pagination(self):
        self._created_revision()
        cases = [
            self._list(patient=str(uuid4())),
            self._list(encounter=str(uuid4())),
            self._list(review_binding=str(uuid4())),
        ]
        for response in cases:
            self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)
            self.assertNotIn("results", response.json())

        unknown = self._list(browser_cache_key="untrusted")
        excessive = self._list(limit=101)
        zero_limit = self._list(limit=0)
        negative_offset = self._list(offset=-1)
        missing_review = self.client.get(
            self.list_url,
            {
                "patient": str(self.patient.external_id),
                "encounter": str(self.encounter.external_id),
            },
        )
        for response in [
            unknown,
            excessive,
            zero_limit,
            negative_offset,
            missing_review,
        ]:
            self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
            self.assertNotIn("results", response.json())

    def test_list_requires_current_authorization_before_returning_lineage(self):
        self._created_revision()
        self.client.force_authenticate(user=None)

        response = self._list()

        self.assertIn(
            response.status_code,
            {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN},
        )
        self.assertNotIn("results", response.json())

    def test_create_exact_replay_and_key_conflict_are_safe_and_non_leaking(self):
        payload = self._create_payload()
        created = self._create(payload)
        replay = self._create(payload)
        conflict = self._create({**payload, "body": "Different clinical content"})

        self.assertEqual(created.status_code, HTTPStatus.CREATED)
        self.assertEqual(replay.status_code, HTTPStatus.OK)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(
            replay.json()["correspondence"]["id"],
            created.json()["correspondence"]["id"],
        )
        self.assertEqual(conflict.status_code, HTTPStatus.CONFLICT)
        self.assertNotIn("correspondence", conflict.json())
        self.assertEqual(CorrespondenceLetter.objects.count(), 1)
        self.assertEqual(CorrespondenceLetterRevision.objects.count(), 1)
        self.assertEqual(CorrespondenceLetterCommand.objects.count(), 1)

    def test_revise_creates_new_immutable_version_and_two_tab_write_conflicts(self):
        first = self._created_revision()
        stale_payload = self._revision_payload(first, body="Tab A content")
        tab_a = self._revise(first, stale_payload)
        tab_b = self._revise(
            first,
            self._revision_payload(first, body="Tab B conflicting content"),
        )

        self.assertEqual(tab_a.status_code, HTTPStatus.CREATED, tab_a.json())
        self.assertEqual(tab_b.status_code, HTTPStatus.CONFLICT)
        second = CorrespondenceLetterRevision.objects.get(
            external_id=tab_a.json()["correspondence"]["id"]
        )
        first.refresh_from_db()
        self.assertEqual(first.body, "Dear colleague,\n\nPlease review this patient.")
        self.assertEqual(second.previous_revision, first)
        self.assertEqual(second.resource_version, 2)
        self.assertEqual(second.body, "Tab A content")
        first.body = "attempted overwrite"
        with self.assertRaises(DjangoValidationError):
            first.save()

    def test_revise_exact_replay_returns_original_committed_revision(self):
        first = self._created_revision()
        payload = self._revision_payload(first, body="A traceable revision")
        created = self._revise(first, payload)
        replay = self._revise(first, payload)

        self.assertEqual(created.status_code, HTTPStatus.CREATED)
        self.assertEqual(replay.status_code, HTTPStatus.OK)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(
            replay.json()["correspondence"]["revision_hash"],
            created.json()["correspondence"]["revision_hash"],
        )
        self.assertEqual(CorrespondenceLetterRevision.objects.count(), 2)

    def test_exact_replay_needs_current_read_but_not_current_write_permission(self):
        payload = self._create_payload()
        self.assertEqual(self._create(payload).status_code, HTTPStatus.CREATED)

        with patch(
            "care.emr.api.viewsets.correspondence_letter.write_report_authorizer",
            side_effect=PermissionDenied("encounter is no longer writable"),
        ):
            replay = self._create(payload)
            new_command = self._create({**payload, "client_request_id": str(uuid4())})

        self.assertEqual(replay.status_code, HTTPStatus.OK)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(new_command.status_code, HTTPStatus.FORBIDDEN)

    def test_finalize_creates_immutable_revision_and_native_report_artifact(self):
        draft = self._created_revision()
        response = self._finalize(draft)

        self.assertEqual(response.status_code, HTTPStatus.CREATED, response.json())
        data = response.json()["correspondence"]
        final = CorrespondenceLetterRevision.objects.get(external_id=data["id"])
        artifact = ReportUpload.objects.get(correspondence_revision=final)
        self.assertEqual(final.status, "finalized")
        self.assertEqual(final.previous_revision, draft)
        self.assertEqual(final.resource_version, 2)
        self.assertEqual(artifact.patient, self.patient)
        self.assertEqual(artifact.encounter, self.encounter)
        self.assertEqual(artifact.source_snapshot_hash, final.revision_hash)
        self.assertEqual(data["artifact_status"], "available")
        self.assertEqual(data["artifact"]["id"], str(artifact.external_id))
        self.assertEqual(
            data["artifact"]["download_url"],
            "https://object.example/authenticated-correspondence",
        )
        self.put_object.assert_called_once()
        final.body = "attempted overwrite"
        with self.assertRaises(DjangoValidationError):
            final.save()

    def test_finalize_exact_replay_and_outcome_unknown_recovery_do_not_duplicate(self):
        draft = self._created_revision()
        payload = self._revision_payload(draft)
        created = self._finalize(draft, payload)
        replay = self._finalize(draft, payload)

        self.assertEqual(created.status_code, HTTPStatus.CREATED)
        self.assertEqual(replay.status_code, HTTPStatus.OK)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(
            created.json()["correspondence"]["artifact"]["id"],
            replay.json()["correspondence"]["artifact"]["id"],
        )
        self.assertEqual(
            ReportUpload.objects.filter(correspondence_revision__isnull=False).count(),
            1,
        )
        self.assertEqual(CorrespondenceLetterRevision.objects.count(), 2)

    def test_finalized_or_non_latest_revision_cannot_be_mutated(self):
        first = self._created_revision()
        revised = self._revise(first)
        second = CorrespondenceLetterRevision.objects.get(
            external_id=revised.json()["correspondence"]["id"]
        )
        finalized = self._finalize(second)

        old_revision = self._revise(
            first,
            self._revision_payload(first, body="Late old-tab content"),
        )
        final_revision = CorrespondenceLetterRevision.objects.get(
            external_id=finalized.json()["correspondence"]["id"]
        )
        after_final = self._revise(
            final_revision,
            self._revision_payload(final_revision, body="Post-final edit"),
        )
        self.assertEqual(old_revision.status_code, HTTPStatus.CONFLICT)
        self.assertEqual(after_final.status_code, HTTPStatus.CONFLICT)
        self.assertEqual(CorrespondenceLetterRevision.objects.count(), 3)

    def test_wrong_context_is_404_and_stale_review_is_non_leaking_409(self):
        wrong = self._create(self._create_payload(patient=str(uuid4())))
        payload = self._create_payload()
        CorrespondenceReview._base_manager.filter(pk=self.review.pk).update(  # noqa: SLF001
            review_hash="0" * 64
        )
        stale = self._create(payload)

        self.assertEqual(wrong.status_code, HTTPStatus.NOT_FOUND)
        self.assertEqual(stale.status_code, HTTPStatus.CONFLICT)
        self.assertNotIn("correspondence", wrong.json())
        self.assertNotIn("correspondence", stale.json())

    def test_source_amendment_preserves_exact_replays_but_blocks_new_mutations(self):
        create_payload = self._create_payload()
        created = self._create(create_payload)
        first = CorrespondenceLetterRevision.objects.get(
            external_id=created.json()["correspondence"]["id"]
        )
        revise_payload = self._revision_payload(
            first,
            body="Dear colleague,\n\nReviewed before source correction.",
        )
        revised = self._revise(first, revise_payload)
        second = CorrespondenceLetterRevision.objects.get(
            external_id=revised.json()["correspondence"]["id"]
        )
        amended = self._amend_source()

        exact_create = self._create(create_payload)
        exact_revise = self._revise(first, revise_payload)
        new_create = self._create({**create_payload, "client_request_id": str(uuid4())})
        new_revise = self._revise(
            second,
            self._revision_payload(
                second,
                body="This must not be written from a stale source.",
            ),
        )
        new_finalize = self._finalize(second)

        self.assertEqual(amended.status_code, HTTPStatus.CREATED, amended.json())
        self.assertEqual(exact_create.status_code, HTTPStatus.OK)
        self.assertEqual(exact_revise.status_code, HTTPStatus.OK)
        self.assertTrue(exact_create.json()["replayed"])
        self.assertTrue(exact_revise.json()["replayed"])
        for response in [new_create, new_revise, new_finalize]:
            self.assertEqual(response.status_code, HTTPStatus.CONFLICT)
            self.assertEqual(
                response.json()["errors"][0]["type"],
                "correspondence_review_stale",
            )

    def test_current_author_and_write_permission_are_rechecked_for_new_mutations(self):
        with patch(
            "care.emr.api.viewsets.correspondence_letter.write_report_authorizer",
            side_effect=PermissionDenied("closed encounter"),
        ):
            denied = self._create()

        self.assertEqual(denied.status_code, HTTPStatus.FORBIDDEN)
        self.assertEqual(CorrespondenceLetter.objects.count(), 0)

        self.user.verified = False
        self.user.save(update_fields=["verified"])
        unverified = self._create()
        self.assertEqual(unverified.status_code, HTTPStatus.FORBIDDEN)

    def test_author_profile_change_preserves_replay_but_blocks_new_letter(self):
        payload = self._create_payload()
        self.assertEqual(self._create(payload).status_code, HTTPStatus.CREATED)
        self.user.first_name = "Changed"
        self.user.save(update_fields=["first_name"])

        exact = self._create(payload)
        new_key = self._create({**payload, "client_request_id": str(uuid4())})

        self.assertEqual(exact.status_code, HTTPStatus.OK)
        self.assertTrue(exact.json()["replayed"])
        self.assertEqual(new_key.status_code, HTTPStatus.CONFLICT)

    def test_frozen_review_or_soft_deleted_sources_block_retrieve_and_replay(self):
        payload = self._create_payload()
        created = self._create(payload)
        revision = CorrespondenceLetterRevision.objects.get()
        CorrespondenceLetterRevision._base_manager.filter(pk=revision.pk).update(  # noqa: SLF001
            deleted=True
        )

        detail = self.client.get(
            reverse(
                "correspondence-letter-detail",
                kwargs={"external_id": revision.external_id},
            )
        )
        replay = self._create(payload)
        self.assertEqual(created.status_code, HTTPStatus.CREATED)
        self.assertIn(detail.status_code, {HTTPStatus.NOT_FOUND, HTTPStatus.CONFLICT})
        self.assertEqual(replay.status_code, HTTPStatus.CONFLICT)
        self.assertNotIn("correspondence", replay.json())

    def test_dirty_revision_is_not_returned_by_list_or_retrieve(self):
        revision = self._created_revision()
        CorrespondenceLetterRevision._base_manager.filter(pk=revision.pk).update(  # noqa: SLF001
            body="database-tampered body"
        )

        detail = self.client.get(
            reverse(
                "correspondence-letter-detail",
                kwargs={"external_id": revision.external_id},
            )
        )
        listing = self._list()

        self.assertEqual(detail.status_code, HTTPStatus.CONFLICT)
        self.assertEqual(listing.status_code, HTTPStatus.CONFLICT)
        self.assertNotIn("body", str(detail.json()))
        self.assertNotIn("body", str(listing.json()))

    def test_deleted_final_artifact_replay_returns_history_without_download(self):
        draft = self._created_revision()
        payload = self._revision_payload(draft)
        self.assertEqual(self._finalize(draft, payload).status_code, HTTPStatus.CREATED)
        artifact = ReportUpload.objects.get(correspondence_revision__isnull=False)
        ReportUpload._base_manager.filter(pk=artifact.pk).update(deleted=True)  # noqa: SLF001

        replay = self._finalize(draft, payload)

        self.assertEqual(replay.status_code, HTTPStatus.OK)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(
            replay.json()["correspondence"]["artifact_status"],
            "unavailable",
        )
        self.assertIsNone(replay.json()["correspondence"]["artifact"]["download_url"])
        self.assertEqual(
            CorrespondenceLetterCommand.objects.filter(command_type="finalize").count(),
            1,
        )

    def test_xss_is_escaped_in_final_html_and_placeholders_cannot_finalize(self):
        draft = self._created_revision()
        draft.body = '<script>alert("x")</script> & clinical text'
        html = build_correspondence_letter_html(
            artifact_id=uuid4(),
            revision=draft,
            generated_at=draft.created_date,
        )
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

        placeholder_payload = self._revision_payload(
            draft,
            body="Dear {{ recipient_name }}",
        )
        revised = self._revise(draft, placeholder_payload)
        placeholder = CorrespondenceLetterRevision.objects.get(
            external_id=revised.json()["correspondence"]["id"]
        )
        blocked = self._finalize(placeholder)
        self.assertEqual(blocked.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)
        self.assertEqual(
            ReportUpload.objects.filter(correspondence_revision__isnull=False).count(),
            0,
        )

    def test_final_pdf_html_is_a_clinical_letter_without_audit_dump(self):
        draft = self._created_revision()
        artifact_id = uuid4()
        html = build_correspondence_letter_html(
            artifact_id=artifact_id,
            revision=draft,
            generated_at=draft.created_date,
        )

        self.assertNotIn("MEDISCHE BRIEF", html)
        self.assertIn(self.patient.name, html)
        self.assertIn(self.review.recipient_snapshot["display_name"], html)
        self.assertIn("<p>Dear colleague,</p>", html)
        self.assertIn("<p>Please review this patient.</p>", html)
        self.assertIn("Presentatiedatum", html)
        self.assertIn("Vertrouwelijk medisch document", html)
        self.assertNotIn(str(artifact_id), html)
        self.assertNotIn(str(draft.letter.external_id), html)
        self.assertNotIn("Revision SHA-256", html)
        self.assertNotIn("Review binding", html)
        self.assertNotIn("Patient CARE reference", html)

    def test_final_pdf_uses_configured_urology_specialty_letterhead(self):
        draft = self._created_revision()
        draft.letter.review.compilation.template.options = {
            "letterhead_title": "POLIKLINIEK UROLOGIE",
        }

        html = build_correspondence_letter_html(
            artifact_id=uuid4(),
            revision=draft,
            generated_at=draft.created_date,
        )

        self.assertIn("POLIKLINIEK UROLOGIE", html)
        self.assertNotIn("SPECIALISTENBRIEF", html)
        self.assertIn('class="specialty-name"', html)

    def test_final_pdf_formats_clinical_sections_and_normalizes_reason_heading(self):
        draft = self._created_revision()
        draft.letter.review.compilation.source_provenance["form"][
            "presentation_reason"
        ] = "Macroscopische hematurie"
        draft.body = (
            "Geachte collega,\n\n"
            "Reden van presentatie: Hematurie\n\n"
            "Anamnese:\nSinds twee dagen macroscopische hematurie.\n\n"
            "Beleid:\nCT-urografie en cystoscopie."
        )

        html = build_correspondence_letter_html(
            artifact_id=uuid4(),
            revision=draft,
            generated_at=draft.created_date,
        )

        self.assertIn(
            '<section class="subject"><span>Onderwerp</span>'
            "Macroscopische hematurie</section>",
            html,
        )
        self.assertIn("<h2>Reden van komst</h2><p>Hematurie</p>", html)
        self.assertIn(
            "<h2>Anamnese</h2><p>Sinds twee dagen macroscopische hematurie.</p>",
            html,
        )
        self.assertIn("<h2>Beleid</h2><p>CT-urografie en cystoscopie.</p>", html)

    def test_pdf_subject_recovers_note_reason_for_older_compilation_snapshot(self):
        draft = self._created_revision()
        compilation = draft.letter.review.compilation
        compilation.source_provenance["form"].pop("presentation_reason", None)
        compilation.form_submission.response_dump = {
            "content": {
                "values": {"reasonForVisit": "Macroscopische hematurie"},
            }
        }

        html = build_correspondence_letter_html(
            artifact_id=uuid4(),
            revision=draft,
            generated_at=draft.created_date,
        )

        self.assertIn(
            '<section class="subject"><span>Onderwerp</span>'
            "Macroscopische hematurie</section>",
            html,
        )

    def test_pdf_formats_diagnosis_history_without_em_dash(self):
        draft = self._created_revision()
        draft.body = (
            "Algemene voorgeschiedenis:\n"
            "- Asthma — 01-01-2000: Allergische Asthma\n\n"
            "Urologische voorgeschiedenis:\n"
            "- Uretersteen — 01-07-2026: "
            "CT IVP: Distale uretersteen van 10mm"
        )

        html = build_correspondence_letter_html(
            artifact_id=uuid4(),
            revision=draft,
            generated_at=draft.created_date,
        )

        self.assertIn('<ul class="history-list">', html)
        self.assertIn(
            '<span class="history-diagnosis">Asthma:</span>'
            '<div class="history-detail">'
            "01-01-2000: Allergische Asthma</div>",
            html,
        )
        self.assertIn(
            '<span class="history-diagnosis">Uretersteen:</span>'
            '<div class="history-detail">'
            "01-07-2026: CT IVP: Distale uretersteen van 10mm</div>",
            html,
        )
        self.assertNotIn("—", html)

    def test_existing_closing_and_author_are_not_duplicated_in_pdf(self):
        draft = self._created_revision()
        author_name = self.review.author_snapshot["display"]
        draft.body = f"Geachte collega,\n\nKlinische tekst.\n\nMet vriendelijke groet,\n{author_name}"

        html = build_correspondence_letter_html(
            artifact_id=uuid4(),
            revision=draft,
            generated_at=draft.created_date,
        )

        self.assertEqual(html.count("Met vriendelijke groet"), 1)
        self.assertEqual(html.count(author_name), 1)

    def test_storage_failure_rolls_back_final_revision_command_and_artifact(self):
        draft = self._created_revision()
        self.put_object.side_effect = RuntimeError("synthetic storage failure")

        response = self._finalize(draft)

        self.assertEqual(response.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(CorrespondenceLetterRevision.objects.count(), 1)
        self.assertEqual(
            CorrespondenceLetterCommand.objects.filter(command_type="finalize").count(),
            0,
        )
        self.assertEqual(
            ReportUpload.objects.filter(correspondence_revision__isnull=False).count(),
            0,
        )

    def test_unknown_fields_are_rejected_without_side_effects(self):
        payload = self._create_payload(browser_title="Untrusted PHI title")
        response = self._create(payload)

        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(CorrespondenceLetter.objects.count(), 0)

    def test_unauthenticated_and_wrong_authenticated_author_do_not_leak(self):
        payload = self._create_payload()
        self.client.force_authenticate(user=None)
        unauthenticated = self._create(payload)
        other = self.create_user(
            first_name="Other", last_name="Clinician", verified=True
        )
        self.attach_role_facility_organization_user(self.organization, other, self.role)
        self.client.force_authenticate(user=other)
        wrong_author = self._create(payload)

        self.assertIn(
            unauthenticated.status_code,
            {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN},
        )
        self.assertEqual(wrong_author.status_code, HTTPStatus.NOT_FOUND)
        self.assertNotIn("correspondence", wrong_author.json())
        self.assertEqual(CorrespondenceLetter.objects.count(), 0)

    def test_command_hash_covers_author_context_and_body(self):
        payload = self._create_payload()
        self.assertEqual(self._create(payload).status_code, HTTPStatus.CREATED)
        changed = copy.deepcopy(payload)
        changed["review_hash"] = "f" * 64
        response = self._create(changed)

        self.assertEqual(response.status_code, HTTPStatus.CONFLICT)
        self.assertNotIn("correspondence", response.json())


class TestCorrespondenceLetterConcurrency(
    CorrespondenceReviewTestMixin,
    TransactionTestCase,
):
    fake = CareAPITestBase.fake
    reset_sequences = True

    def setUp(self):
        cache.clear()
        FacilityPatientNameIdentifierConfig.CACHED_CONFIG.clear()
        NameIdentifierConfig.CACHED_CONFIG.clear()
        PhoneNumberIdentifierConfig.CACHED_CONFIG.clear()
        self.client = APIClient()
        self.build_review_context()
        response = self._bind()
        if response.status_code != HTTPStatus.CREATED:
            raise AssertionError(response.json())
        self.review = CorrespondenceReview.objects.get()
        self.create_url = reverse("correspondence-letter-idempotent-create")

    def _context(self):
        return {
            "review_binding": str(self.review.external_id),
            "review_hash": self.review.review_hash,
            "patient": str(self.patient.external_id),
            "encounter": str(self.encounter.external_id),
            "facility": str(self.facility.external_id),
            "department": str(self.organization.external_id),
            "author": str(self.user.external_id),
        }

    def _post_concurrently(self, requests):
        barrier = Barrier(2)

        def post(request):
            url, payload = request
            close_old_connections()
            client = APIClient()
            client.force_authenticate(user=self.user)
            barrier.wait()
            response = client.post(url, copy.deepcopy(payload), format="json")
            close_old_connections()
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(post, requests))

    def test_concurrent_exact_create_commits_one_revision_and_command(self):
        payload = {
            "client_request_id": str(uuid4()),
            **self._context(),
            "body": "Concurrent exact draft",
        }
        responses = self._post_concurrently(
            [(self.create_url, payload), (self.create_url, payload)]
        )

        self.assertEqual(sorted(code for code, _ in responses), [200, 201])
        self.assertEqual(CorrespondenceLetter.objects.count(), 1)
        self.assertEqual(CorrespondenceLetterRevision.objects.count(), 1)
        self.assertEqual(CorrespondenceLetterCommand.objects.count(), 1)

    def test_concurrent_two_tab_revisions_allow_one_writer(self):
        created = self.client.post(
            self.create_url,
            {
                "client_request_id": str(uuid4()),
                **self._context(),
                "body": "Original concurrent draft",
            },
            format="json",
        )
        revision = CorrespondenceLetterRevision.objects.get(
            external_id=created.json()["correspondence"]["id"]
        )
        url = reverse(
            "correspondence-letter-idempotent-revise",
            kwargs={"external_id": revision.external_id},
        )
        common = {
            **self._context(),
            "expected_version": 1,
        }
        responses = self._post_concurrently(
            [
                (
                    url,
                    {
                        "client_request_id": str(uuid4()),
                        **common,
                        "body": "Concurrent tab A",
                    },
                ),
                (
                    url,
                    {
                        "client_request_id": str(uuid4()),
                        **common,
                        "body": "Concurrent tab B",
                    },
                ),
            ]
        )

        self.assertEqual(sorted(code for code, _ in responses), [201, 409])
        conflict = next(body for code, body in responses if code == HTTPStatus.CONFLICT)
        self.assertNotIn("correspondence", conflict)
        self.assertEqual(CorrespondenceLetterRevision.objects.count(), 2)

    def test_amendment_and_letter_creation_have_only_serial_outcomes(self):
        payload = {
            "client_request_id": str(uuid4()),
            **self._context(),
            "body": "Concurrent source-guarded draft",
        }
        responses = self._post_concurrently(
            [
                self._amendment_request(),
                (self.create_url, payload),
            ]
        )

        self.assertEqual(responses[0][0], HTTPStatus.CREATED)
        self.assertIn(
            responses[1][0],
            {HTTPStatus.CREATED, HTTPStatus.CONFLICT},
        )
        expected_letters = 1 if responses[1][0] == HTTPStatus.CREATED else 0
        self.assertEqual(CorrespondenceLetter.objects.count(), expected_letters)
        self.assertEqual(CorrespondenceLetterRevision.objects.count(), expected_letters)
        self.assertEqual(CorrespondenceLetterCommand.objects.count(), expected_letters)
