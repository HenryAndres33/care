import copy
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from threading import Barrier
from unittest.mock import patch
from uuid import uuid1, uuid4

from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from care.emr.correspondence.recipient import MAX_VERIFIED_RECIPIENT_RESULTS
from care.emr.correspondence.review import correspondence_review_hash
from care.emr.models.correspondence import CorrespondenceCompilation
from care.emr.models.correspondence_review import (
    CorrespondenceRecipient,
    CorrespondenceRecipientCommand,
    CorrespondenceReview,
    CorrespondenceReviewCommand,
)
from care.emr.signals.patient.facility_name_identifier import (
    FacilityPatientNameIdentifierConfig,
)
from care.emr.signals.patient.name_identifier import NameIdentifierConfig
from care.emr.signals.patient.phone_number_identifier import (
    PhoneNumberIdentifierConfig,
)
from care.emr.tests.test_correspondence_compilation import (
    CorrespondenceCompilationTestMixin,
)
from care.security.permissions.encounter import EncounterPermissions
from care.security.permissions.patient import PatientPermissions
from care.security.permissions.template import TemplatePermissions
from care.utils.tests.base import CareAPITestBase


class CorrespondenceReviewTestMixin(CorrespondenceCompilationTestMixin):
    def build_review_context(self):
        self.build_context()
        self.client.force_authenticate(user=self.user)
        compilation_response = self.client.post(
            self.url,
            self._payload(),
            format="json",
        )
        if compilation_response.status_code != HTTPStatus.CREATED:
            raise AssertionError(compilation_response.json())
        self.compilation = CorrespondenceCompilation.objects.get()
        self.recipient = self._recipient()
        self.discovery_url = reverse("correspondence-recipient-verified")
        self.manual_recipient_url = reverse(
            "correspondence-recipient-idempotent-manual"
        )
        self.review_url = reverse("correspondence-review-idempotent-bind")

    def _recipient(self, **overrides):
        values = {
            "patient": self.patient,
            "facility": self.facility,
            "recipient_kind": "healthcare_professional",
            "display_name": "Verified Community Clinician",
            "professional_role": "Primary care clinician",
            "qualification": "MD",
            "registration": "RECIPIENT-REG-1",
            "organization_name": "Community Health Practice",
            "postal_address": {
                "line": ["100 Care Street"],
                "city": "Paramaribo",
                "country": "SR",
            },
            "channel_type": "secure_endpoint",
            "channel_identifier": "directory:recipient:001",
            "source_type": "facility_governed_directory",
            "source_reference": f"recipient-{uuid4()}",
            "source_provenance": {
                "governance": "facility-directory",
                "evidence_reference": "VERIFY-001",
            },
            "active": True,
            "verified": True,
            "verified_by": self.user,
            "verified_at": timezone.now(),
            "created_by": self.user,
            "updated_by": self.user,
        }
        values.update(overrides)
        recipient = CorrespondenceRecipient(**values)
        recipient.save(force_insert=True)
        return recipient

    def _review_payload(self, **overrides):
        payload = {
            "client_request_id": str(uuid4()),
            "compilation": str(self.compilation.external_id),
            "compilation_hash": self.compilation.compiled_hash,
            "patient": str(self.patient.external_id),
            "encounter": str(self.encounter.external_id),
            "facility": str(self.facility.external_id),
            "department": str(self.organization.external_id),
            "author": str(self.user.external_id),
            "recipient": str(self.recipient.external_id),
            "recipient_version": self.recipient.resource_version,
            "recipient_hash": self.recipient.content_hash,
        }
        payload.update(overrides)
        return payload

    def _bind(self, payload=None):
        return self.client.post(
            self.review_url,
            payload or self._review_payload(),
            format="json",
        )


class TestCorrespondenceReviewAPI(
    CorrespondenceReviewTestMixin,
    CareAPITestBase,
):
    def setUp(self):
        super().setUp()
        self.build_review_context()

    def _manual_recipient_payload(self, **overrides):
        payload = {
            "client_request_id": str(uuid4()),
            "patient": str(self.patient.external_id),
            "facility": str(self.facility.external_id),
            "display_name": "  Dr.   Chigaroe  ",
        }
        payload.update(overrides)
        return payload

    def test_manual_recipient_is_verified_persisted_and_discoverable(self):
        response = self.client.post(
            self.manual_recipient_url,
            self._manual_recipient_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, HTTPStatus.CREATED)
        recipient = response.json()["recipient"]
        self.assertEqual(recipient["display_name"], "Dr. Chigaroe")
        self.assertEqual(recipient["source_type"], "manual_clinical_entry")
        self.assertEqual(recipient["channel_type"], "postal")
        self.assertEqual(
            recipient["postal_address"],
            {
                "address_status": "not_supplied",
                "recipient_line": "Dr. Chigaroe",
            },
        )
        self.assertEqual(CorrespondenceRecipientCommand.objects.count(), 1)
        discovery = self.client.get(
            self.discovery_url,
            {
                "patient": str(self.patient.external_id),
                "facility": str(self.facility.external_id),
            },
        )
        self.assertEqual(discovery.status_code, HTTPStatus.OK)
        self.assertIn(
            "Dr. Chigaroe",
            [item["display_name"] for item in discovery.json()["results"]],
        )

    def test_manual_recipient_exact_retry_is_idempotent(self):
        payload = self._manual_recipient_payload()

        created = self.client.post(
            self.manual_recipient_url, payload, format="json"
        )
        replayed = self.client.post(
            self.manual_recipient_url, payload, format="json"
        )

        self.assertEqual(created.status_code, HTTPStatus.CREATED)
        self.assertEqual(replayed.status_code, HTTPStatus.OK)
        self.assertTrue(replayed.json()["replayed"])
        self.assertEqual(
            created.json()["recipient"]["id"],
            replayed.json()["recipient"]["id"],
        )
        self.assertEqual(CorrespondenceRecipientCommand.objects.count(), 1)

    def test_manual_recipient_rejects_reused_request_for_other_name(self):
        payload = self._manual_recipient_payload()
        self.client.post(self.manual_recipient_url, payload, format="json")

        conflict = self.client.post(
            self.manual_recipient_url,
            {**payload, "display_name": "Dr. Anders"},
            format="json",
        )

        self.assertEqual(conflict.status_code, HTTPStatus.CONFLICT)
        self.assertEqual(
            conflict.json()["errors"][0]["type"], "idempotency_conflict"
        )

    def test_verified_recipient_discovery_is_scoped_strict_and_never_auto_selects(self):
        second = self._recipient(display_name="Second Verified Recipient")

        response = self.client.get(
            self.discovery_url,
            {
                "patient": str(self.patient.external_id),
                "facility": str(self.facility.external_id),
            },
        )
        extra = self.client.get(
            self.discovery_url,
            {
                "patient": str(self.patient.external_id),
                "facility": str(self.facility.external_id),
                "recipient": str(self.recipient.external_id),
            },
        )

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(len(response.json()["results"]), 2)
        self.assertEqual(
            {item["id"] for item in response.json()["results"]},
            {str(self.recipient.external_id), str(second.external_id)},
        )
        self.assertEqual(extra.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(CorrespondenceReview.objects.count(), 0)

    def test_discovery_hides_inactive_unverified_deleted_and_dirty_entries(self):
        inactive = self._recipient(active=False)
        unverified = self._recipient(
            verified=False,
            verified_by=None,
            verified_at=None,
        )
        deleted = self._recipient()
        dirty = self._recipient()
        CorrespondenceRecipient._base_manager.filter(pk=deleted.pk).update(  # noqa: SLF001
            deleted=True
        )
        CorrespondenceRecipient._base_manager.filter(pk=dirty.pk).update(  # noqa: SLF001
            content_hash="0" * 64
        )

        response = self.client.get(
            self.discovery_url,
            {
                "patient": str(self.patient.external_id),
                "facility": str(self.facility.external_id),
            },
        )

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(
            [item["id"] for item in response.json()["results"]],
            [str(self.recipient.external_id)],
        )
        self.assertNotIn(str(inactive.external_id), str(response.json()))
        self.assertNotIn(str(unverified.external_id), str(response.json()))

    def test_discovery_fails_visibly_when_verified_matches_exceed_bound(self):
        for index in range(MAX_VERIFIED_RECIPIENT_RESULTS):
            self._recipient(display_name=f"Bounded Recipient {index:03d}")

        response = self.client.get(
            self.discovery_url,
            {
                "patient": str(self.patient.external_id),
                "facility": str(self.facility.external_id),
            },
        )

        self.assertEqual(response.status_code, HTTPStatus.CONFLICT)
        self.assertEqual(
            response.json()["errors"][0]["type"],
            "recipient_discovery_overflow",
        )
        self.assertNotIn("results", response.json())

    def test_bind_freezes_verified_author_recipient_channel_and_review_provenance(self):
        response = self._bind()

        self.assertEqual(response.status_code, HTTPStatus.CREATED)
        self.assertEqual(
            set(response.json()),
            {"client_request_id", "replayed", "review_binding"},
        )
        binding = response.json()["review_binding"]
        self.assertFalse(response.json()["replayed"])
        self.assertEqual(binding["compilation"], str(self.compilation.external_id))
        self.assertEqual(binding["recipient"], str(self.recipient.external_id))
        self.assertEqual(binding["author"], str(self.user.external_id))
        self.assertEqual(binding["reviewer"], str(self.user.external_id))
        self.assertEqual(
            binding["author_snapshot"]["professional_role"],
            "Correspondence Clinician",
        )
        self.assertEqual(binding["author_snapshot"]["registration"], "REG-001")
        self.assertEqual(
            binding["recipient_snapshot"]["channel"]["identifier"],
            "directory:recipient:001",
        )
        self.assertNotIn("Local clinician", str(binding))
        self.assertEqual(
            response["ETag"], f'"{binding["id"]}:{binding["review_hash"]}"'
        )

    def test_exact_retry_and_new_key_reuse_the_same_review(self):
        payload = self._review_payload()
        created = self._bind(payload)
        exact = self._bind(payload)
        another_key = self._bind({**payload, "client_request_id": str(uuid4())})

        self.assertEqual(created.status_code, HTTPStatus.CREATED)
        self.assertEqual(exact.status_code, HTTPStatus.OK)
        self.assertEqual(another_key.status_code, HTTPStatus.OK)
        self.assertTrue(exact.json()["replayed"])
        self.assertTrue(another_key.json()["replayed"])
        self.assertEqual(CorrespondenceReview.objects.count(), 1)
        self.assertEqual(CorrespondenceReviewCommand.objects.count(), 2)

    def test_exact_replay_requires_current_read_but_not_new_write_authorization(self):
        payload = self._review_payload()
        self.assertEqual(self._bind(payload).status_code, HTTPStatus.CREATED)
        with patch(
            "care_suriname.api.viewsets.correspondence_review.write_report_authorizer",
            side_effect=PermissionDenied("encounter is no longer writable"),
        ):
            exact = self._bind(payload)
            new_key = self._bind({**payload, "client_request_id": str(uuid4())})

        self.assertEqual(exact.status_code, HTTPStatus.OK)
        self.assertTrue(exact.json()["replayed"])
        self.assertEqual(new_key.status_code, HTTPStatus.FORBIDDEN)
        self.assertEqual(CorrespondenceReviewCommand.objects.count(), 1)

    def test_same_key_conflict_and_second_recipient_are_non_leaking(self):
        payload = self._review_payload()
        self.assertEqual(self._bind(payload).status_code, HTTPStatus.CREATED)
        second = self._recipient()
        changed = {
            **payload,
            "recipient": str(second.external_id),
            "recipient_version": second.resource_version,
            "recipient_hash": second.content_hash,
        }

        key_conflict = self._bind(changed)
        source_conflict = self._bind({**changed, "client_request_id": str(uuid4())})

        self.assertEqual(key_conflict.status_code, HTTPStatus.CONFLICT)
        self.assertEqual(source_conflict.status_code, HTTPStatus.CONFLICT)
        self.assertNotIn("review_binding", key_conflict.json())
        self.assertNotIn("review_binding", source_conflict.json())

    def test_stale_compilation_or_recipient_version_and_hash_return_409(self):
        cases = [
            self._review_payload(compilation_hash="0" * 64),
            self._review_payload(recipient_version=99),
            self._review_payload(recipient_hash="1" * 64),
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                response = self._bind(payload)
                self.assertEqual(response.status_code, HTTPStatus.CONFLICT)
                self.assertNotIn("review_binding", response.json())

    def test_wrong_patient_encounter_facility_department_author_or_recipient_is_404(
        self,
    ):
        other_patient = self.create_patient()
        other_facility = self.create_facility(user=self.user)
        other_recipient = self._recipient(patient=other_patient)
        cases = [
            {"patient": str(other_patient.external_id)},
            {"encounter": str(uuid4())},
            {"facility": str(other_facility.external_id)},
            {"department": str(uuid4())},
            {"author": str(uuid4())},
            {
                "recipient": str(other_recipient.external_id),
                "recipient_version": other_recipient.resource_version,
                "recipient_hash": other_recipient.content_hash,
            },
        ]
        for change in cases:
            with self.subTest(change=change):
                response = self._bind(self._review_payload(**change))
                self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)
                self.assertNotIn("review_binding", response.json())

    def test_inactive_unverified_deleted_or_malformed_recipient_fails_closed(self):
        cases = [
            {"active": False},
            {"verified": False},
            {"channel_type": "unverified_email"},
        ]
        for mutation in cases:
            with self.subTest(mutation=mutation):
                recipient = self._recipient(**mutation)
                response = self._bind(
                    self._review_payload(
                        recipient=str(recipient.external_id),
                        recipient_version=recipient.resource_version,
                        recipient_hash=recipient.content_hash,
                    )
                )
                self.assertEqual(response.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)

        for field in ["postal_address", "source_provenance"]:
            with self.subTest(field=field):
                recipient = self._recipient()
                CorrespondenceRecipient._base_manager.filter(pk=recipient.pk).update(  # noqa: SLF001
                    **{field: {}}
                )
                response = self._bind(
                    self._review_payload(
                        recipient=str(recipient.external_id),
                        recipient_version=recipient.resource_version,
                        recipient_hash=recipient.content_hash,
                    )
                )
                self.assertEqual(response.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)

        deleted = self._recipient()
        CorrespondenceRecipient._base_manager.filter(pk=deleted.pk).update(  # noqa: SLF001
            deleted=True
        )
        deleted_response = self._bind(
            self._review_payload(
                recipient=str(deleted.external_id),
                recipient_version=deleted.resource_version,
                recipient_hash=deleted.content_hash,
            )
        )
        self.assertEqual(deleted_response.status_code, HTTPStatus.CONFLICT)

    def test_recipient_kind_and_directory_json_are_model_hardened(self):
        invalid_kind = self._recipient()
        with self.assertRaises(IntegrityError), transaction.atomic():
            CorrespondenceRecipient._base_manager.filter(  # noqa: SLF001
                pk=invalid_kind.pk
            ).update(recipient_kind="non_clinical_contact")

        too_deep = {"a": {"b": {"c": {"d": {"e": {"f": "value"}}}}}}
        cases = [
            ("postal_address", {}),
            ("postal_address", too_deep),
            ("postal_address", {"line": ["x" * 9000]}),
            ("source_provenance", []),
            ("source_provenance", {"evidence": "x" * 17000}),
        ]
        for field, value in cases:
            with self.subTest(field=field, value_type=type(value).__name__):
                recipient = self._recipient()
                setattr(recipient, field, value)
                with self.assertRaises(DjangoValidationError):
                    recipient.save(update_fields=[field])

    def test_verifier_revocation_hides_live_recipient_but_preserves_history(self):
        verifier = self.create_user(
            first_name="Directory",
            last_name="Verifier",
            verified=True,
        )
        self.attach_role_facility_organization_user(
            self.organization,
            verifier,
            self.role,
        )
        recipient = self._recipient(verified_by=verifier)
        payload = self._review_payload(
            recipient=str(recipient.external_id),
            recipient_version=recipient.resource_version,
            recipient_hash=recipient.content_hash,
        )
        created = self._bind(payload)
        self.assertEqual(created.status_code, HTTPStatus.CREATED)
        review_url = reverse(
            "correspondence-review-detail",
            kwargs={"external_id": created.json()["review_binding"]["id"]},
        )

        verifier.is_active = False
        verifier.save(update_fields=["is_active"])
        discovery = self.client.get(
            self.discovery_url,
            {
                "patient": str(self.patient.external_id),
                "facility": str(self.facility.external_id),
            },
        )
        replay = self._bind(payload)
        retrieved = self.client.get(review_url)
        new_key = self._bind(
            {**payload, "client_request_id": str(uuid4())}
        )

        self.assertEqual(discovery.status_code, HTTPStatus.OK)
        self.assertNotIn(str(recipient.external_id), str(discovery.json()))
        self.assertEqual(replay.status_code, HTTPStatus.OK)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(retrieved.status_code, HTTPStatus.OK)
        self.assertEqual(new_key.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)

    def test_missing_or_ambiguous_author_facts_and_membership_block_review(self):
        self.user.verified = False
        self.user.save(update_fields=["verified"])
        unverified = self._bind()
        self.assertEqual(unverified.status_code, HTTPStatus.CONFLICT)

        self.user.verified = True
        self.user.first_name = ""
        self.user.save(update_fields=["verified", "first_name"])
        missing_name = self._bind()
        self.assertEqual(missing_name.status_code, HTTPStatus.CONFLICT)

        self.user.first_name = "Ada"
        self.user.save(update_fields=["first_name"])
        membership = self.organization.facilityorganizationuser_set.get(user=self.user)
        membership.delete()
        missing_membership = self._bind()
        self.assertEqual(
            missing_membership.status_code,
            HTTPStatus.FORBIDDEN,
        )

    def test_ambiguous_author_membership_blocks_review(self):
        self.attach_role_facility_organization_user(
            self.organization,
            self.user,
            self.role,
        )
        response = self._bind()
        self.assertEqual(response.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)

    def test_wrong_user_unauthorized_and_expired_session_do_not_commit(self):
        payload = self._review_payload()
        unauthorized = self.create_user(verified=True)
        self.client.force_authenticate(user=unauthorized)
        denied = self._bind({**payload, "author": str(unauthorized.external_id)})
        self.client.logout()
        expired = self._bind(payload)

        self.assertEqual(denied.status_code, HTTPStatus.FORBIDDEN)
        self.assertEqual(expired.status_code, HTTPStatus.FORBIDDEN)
        self.assertEqual(CorrespondenceReview.objects.count(), 0)

    def test_current_read_authorized_wrong_user_is_hidden_and_write_is_rechecked(self):
        other = self.create_user(
            first_name="Other",
            last_name="Clinician",
            verified=True,
        )
        role = self.create_role_with_permissions(
            [
                EncounterPermissions.can_read_encounter.name,
                EncounterPermissions.can_read_encounter_clinical_data.name,
                PatientPermissions.can_view_clinical_data.name,
                TemplatePermissions.can_read_template.name,
            ]
        )
        self.attach_role_facility_organization_user(self.organization, other, role)
        self.client.force_authenticate(user=other)
        wrong_user = self._bind(self._review_payload(author=str(other.external_id)))
        self.assertEqual(wrong_user.status_code, HTTPStatus.NOT_FOUND)

        self.client.force_authenticate(user=self.user)
        with patch(
            "care_suriname.api.viewsets.correspondence_review.write_report_authorizer",
            side_effect=PermissionDenied("encounter is not writable"),
        ):
            denied = self._bind()
        self.assertEqual(denied.status_code, HTTPStatus.FORBIDDEN)
        self.assertEqual(CorrespondenceReview.objects.count(), 0)

    def test_ledger_failure_rolls_back_and_exact_key_can_retry(self):
        payload = self._review_payload()
        with patch.object(
            CorrespondenceReviewCommand.objects,
            "create",
            side_effect=RuntimeError("synthetic review ledger failure"),
        ):
            failed = self._bind(payload)

        self.assertEqual(failed.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(CorrespondenceReview.objects.count(), 0)
        self.assertEqual(CorrespondenceReviewCommand.objects.count(), 0)
        recovered = self._bind(payload)
        self.assertEqual(recovered.status_code, HTTPStatus.CREATED)

    def test_retrieve_requires_auth_and_review_snapshot_is_immutable(self):
        created = self._bind().json()["review_binding"]
        url = reverse(
            "correspondence-review-detail",
            kwargs={"external_id": created["id"]},
        )
        retrieved = self.client.get(url)
        self.client.logout()
        expired = self.client.get(url)

        self.assertEqual(retrieved.status_code, HTTPStatus.OK)
        self.assertEqual(retrieved.json(), created)
        self.assertEqual(
            retrieved["ETag"], f'"{created["id"]}:{created["review_hash"]}"'
        )
        self.assertEqual(expired.status_code, HTTPStatus.FORBIDDEN)
        review = CorrespondenceReview.objects.get()
        review.recipient_snapshot = {"changed": True}
        with self.assertRaises(DjangoValidationError):
            review.save()

    def test_retrieve_and_replay_reject_tampered_review_hash(self):
        payload = self._review_payload()
        created = self._bind(payload).json()["review_binding"]
        review = CorrespondenceReview.objects.get()
        CorrespondenceReview._base_manager.filter(pk=review.pk).update(  # noqa: SLF001
            review_hash="0" * 64
        )
        url = reverse(
            "correspondence-review-detail",
            kwargs={"external_id": created["id"]},
        )

        retrieved = self.client.get(url)
        replay = self._bind(payload)

        self.assertEqual(retrieved.status_code, HTTPStatus.CONFLICT)
        self.assertEqual(replay.status_code, HTTPStatus.CONFLICT)
        self.assertNotIn("review_binding", replay.json())

    def test_snapshot_field_alignment_is_checked_beyond_review_hash(self):
        created = self._bind().json()["review_binding"]
        review = CorrespondenceReview.objects.select_related(
            *self._review_related_fields()
        ).get()
        author_snapshot = copy.deepcopy(review.author_snapshot)
        author_snapshot["id"] = str(uuid4())
        recipient_snapshot = copy.deepcopy(review.recipient_snapshot)
        recipient_snapshot["patient"] = str(uuid4())
        review.author_snapshot = author_snapshot
        review.recipient_snapshot = recipient_snapshot
        forged_hash = correspondence_review_hash(review)
        CorrespondenceReview._base_manager.filter(pk=review.pk).update(  # noqa: SLF001
            author_snapshot=author_snapshot,
            recipient_snapshot=recipient_snapshot,
            review_hash=forged_hash,
        )

        response = self.client.get(
            reverse(
                "correspondence-review-detail",
                kwargs={"external_id": created["id"]},
            )
        )

        self.assertEqual(response.status_code, HTTPStatus.CONFLICT)

    @staticmethod
    def _review_related_fields():
        return [
            "compilation__patient",
            "compilation__encounter",
            "compilation__facility",
            "compilation__department",
            "compilation__author",
            "patient",
            "encounter",
            "facility",
            "department",
            "author",
            "reviewer",
            "recipient__patient",
            "recipient__facility",
            "recipient__organization",
            "recipient__healthcare_service",
            "recipient__verified_by",
        ]

    def test_recipient_change_preserves_replay_but_blocks_new_binding(self):
        payload = self._review_payload()
        created = self._bind(payload).json()["review_binding"]
        old_version = self.recipient.resource_version
        old_hash = self.recipient.content_hash
        self.recipient.display_name = "Changed Directory Name"
        self.recipient.save(update_fields=["display_name"])

        self.assertEqual(self.recipient.resource_version, old_version + 1)
        self.assertNotEqual(self.recipient.content_hash, old_hash)

        replay = self._bind(payload)
        new_key = self._bind(
            {**payload, "client_request_id": str(uuid4())}
        )

        self.assertEqual(replay.status_code, HTTPStatus.OK)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(new_key.status_code, HTTPStatus.CONFLICT)
        stored = CorrespondenceReview.objects.get()
        self.assertEqual(
            stored.recipient_snapshot["display_name"],
            created["recipient_snapshot"]["display_name"],
        )

    def test_source_amendment_preserves_exact_replay_but_blocks_new_binding(self):
        payload = self._review_payload()
        created = self._bind(payload)
        amended = self._amend_source()
        exact = self._bind(payload)
        new_key = self._bind(
            {**payload, "client_request_id": str(uuid4())}
        )

        self.assertEqual(created.status_code, HTTPStatus.CREATED)
        self.assertEqual(amended.status_code, HTTPStatus.CREATED, amended.json())
        self.assertEqual(exact.status_code, HTTPStatus.OK)
        self.assertTrue(exact.json()["replayed"])
        self.assertEqual(new_key.status_code, HTTPStatus.CONFLICT)
        self.assertEqual(
            new_key.json()["errors"][0]["type"],
            "correspondence_review_source_stale",
        )

    def test_request_is_strict_and_requires_uuid_v4(self):
        extra = self._bind(self._review_payload(letter_body="browser supplied"))
        uuid_v1 = self._bind(self._review_payload(client_request_id=str(uuid1())))
        missing_recipient = self._review_payload()
        del missing_recipient["recipient"]
        missing = self._bind(missing_recipient)

        self.assertEqual(extra.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(uuid_v1.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(missing.status_code, HTTPStatus.BAD_REQUEST)


class TestCorrespondenceReviewConcurrency(
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

    def _post_concurrently(self, payloads):
        barrier = Barrier(2)

        def post(payload):
            url, body = (
                payload
                if isinstance(payload, tuple)
                else (self.review_url, payload)
            )
            close_old_connections()
            client = APIClient()
            client.force_authenticate(user=self.user)
            barrier.wait()
            response = client.post(url, body, format="json")
            close_old_connections()
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(post, payloads))

    def test_concurrent_same_key_binds_once(self):
        payload = self._review_payload()
        responses = self._post_concurrently([payload, payload])
        self.assertEqual(sorted(code for code, _ in responses), [200, 201])
        self.assertEqual(CorrespondenceReview.objects.count(), 1)
        self.assertEqual(CorrespondenceReviewCommand.objects.count(), 1)

    def test_concurrent_distinct_keys_same_sources_reuse_one_binding(self):
        payload = self._review_payload()
        responses = self._post_concurrently(
            [payload, {**payload, "client_request_id": str(uuid4())}]
        )
        self.assertEqual(sorted(code for code, _ in responses), [200, 201])
        self.assertEqual(CorrespondenceReview.objects.count(), 1)
        self.assertEqual(CorrespondenceReviewCommand.objects.count(), 2)

    def test_amendment_and_review_binding_have_only_serial_outcomes(self):
        responses = self._post_concurrently(
            [
                self._amendment_request(),
                self._review_payload(),
            ]
        )

        self.assertEqual(responses[0][0], HTTPStatus.CREATED)
        self.assertIn(
            responses[1][0],
            {HTTPStatus.CREATED, HTTPStatus.CONFLICT},
        )
        expected_reviews = 1 if responses[1][0] == HTTPStatus.CREATED else 0
        self.assertEqual(CorrespondenceReview.objects.count(), expected_reviews)
        self.assertEqual(
            CorrespondenceReviewCommand.objects.count(), expected_reviews
        )

    def test_concurrent_same_key_different_recipient_is_non_leaking(self):
        request_id = str(uuid4())
        first = self._review_payload(client_request_id=request_id)
        second_recipient = self._recipient()
        second = self._review_payload(
            client_request_id=request_id,
            recipient=str(second_recipient.external_id),
            recipient_version=second_recipient.resource_version,
            recipient_hash=second_recipient.content_hash,
        )
        responses = self._post_concurrently([first, second])
        self.assertEqual(sorted(code for code, _ in responses), [201, 409])
        conflict = next(body for code, body in responses if code == HTTPStatus.CONFLICT)
        self.assertNotIn("review_binding", conflict)
        self.assertEqual(CorrespondenceReview.objects.count(), 1)
