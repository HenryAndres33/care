import uuid
from http import HTTPStatus

from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

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
from care.utils.tests.base import CareAPITestBase

OUTBOX_TOKEN_NAMESPACE = uuid.UUID("d5b74ada-c3ae-4c23-850e-c969d21fab65")


class TestCorrespondenceContinuityMigration(TransactionTestCase):
    migrate_from = ("emr", "0086_correspondence_source_correction")
    migrate_to = ("emr", "0087_correspondence_continuity")

    def tearDown(self):
        # Return to the graph's real leaf, not a pinned name: pinning 0090
        # left the shared test database at 0090 for every later test.
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        return executor.loader.project_state([target]).apps

    def test_forward_schema_adds_nullable_system_actors_and_leased_outbox(self):
        self._migrate(self.migrate_from)

        apps = self._migrate(self.migrate_to)

        case_model = apps.get_model("emr", "CorrespondenceCorrectionCase")
        event_model = apps.get_model("emr", "CorrespondenceCorrectionEvent")
        outbox_model = apps.get_model("emr", "CorrespondenceCorrectionOutbox")
        self.assertTrue(case_model._meta.get_field("created_by").null)  # noqa: SLF001
        self.assertTrue(event_model._meta.get_field("actor").null)  # noqa: SLF001
        self.assertTrue(outbox_model._meta.get_field("claim_token").null)  # noqa: SLF001
        self.assertTrue(outbox_model._meta.get_field("lease_expires_at").null)  # noqa: SLF001

    def test_reverse_removes_only_slice_11b_schema(self):
        self._migrate(self.migrate_to)

        apps = self._migrate(self.migrate_from)

        self.assertNotIn(
            "CorrespondenceCorrectionCase",
            {model.__name__ for model in apps.get_models()},
        )
        outbox_model = apps.get_model("emr", "CorrespondenceCorrectionOutbox")
        field_names = {field.name for field in outbox_model._meta.fields}  # noqa: SLF001
        self.assertNotIn("claim_token", field_names)
        self.assertNotIn("lease_expires_at", field_names)


class TestCorrespondenceContinuityLegacyOutboxMigration(
    CorrespondenceCompilationTestMixin,
    TransactionTestCase,
):
    fake = CareAPITestBase.fake
    migrate_from = ("emr", "0086_correspondence_source_correction")
    migrate_to = ("emr", "0087_correspondence_continuity")
    create_user = CareAPITestBase.create_user
    create_facility = CareAPITestBase.create_facility
    create_facility_organization = CareAPITestBase.create_facility_organization
    create_patient = CareAPITestBase.create_patient
    create_encounter = CareAPITestBase.create_encounter
    create_role = CareAPITestBase.create_role
    create_role_with_permissions = CareAPITestBase.create_role_with_permissions
    attach_role_facility_organization_user = (
        CareAPITestBase.attach_role_facility_organization_user
    )

    def setUp(self):
        cache.clear()
        FacilityPatientNameIdentifierConfig.CACHED_CONFIG.clear()
        NameIdentifierConfig.CACHED_CONFIG.clear()
        PhoneNumberIdentifierConfig.CACHED_CONFIG.clear()
        self._migrate(self.migrate_to)
        self.client = APIClient()
        self.build_context()
        self.client.force_authenticate(user=self.user)
        for _index in range(4):
            response = self._amend_source()
            if response.status_code != HTTPStatus.CREATED:
                raise AssertionError(response.json())
            from care.emr.models.questionnaire import FormSubmission

            self.submission = FormSubmission.objects.get(
                external_id=response.json()["form_submission"]["id"]
            )
        apps = self._migrate(self.migrate_from)
        outbox_model = apps.get_model("emr", "CorrespondenceCorrectionOutbox")
        outboxes = list(outbox_model.objects.order_by("source_correction__sequence"))
        now = timezone.now()
        outbox_model.objects.filter(pk=outboxes[1].pk).update(
            status="processing",
            attempt_count=1,
            claimed_at=now,
            completed_at=None,
        )
        outbox_model.objects.filter(pk=outboxes[2].pk).update(
            status="completed",
            attempt_count=1,
            claimed_at=now,
            completed_at=now,
        )
        outbox_model.objects.filter(pk=outboxes[3].pk).update(
            status="failed_terminal",
            attempt_count=1,
            claimed_at=now,
            completed_at=now,
            safe_code="",
        )
        self.external_ids = [outbox.external_id for outbox in outboxes]
        self.updated_by_ids = {
            outbox.external_id: outbox.updated_by_id for outbox in outboxes
        }
        self.claimed_at = now

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    @staticmethod
    def _migrate(target):
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        return executor.loader.project_state([target]).apps

    def test_all_legacy_states_forward_and_reverse_without_actor_fabrication(self):
        apps = self._migrate(self.migrate_to)
        outbox_model = apps.get_model("emr", "CorrespondenceCorrectionOutbox")
        rows = {
            row.status: row
            for row in outbox_model.objects.filter(external_id__in=self.external_ids)
        }

        self.assertIsNone(rows["pending"].claim_token)
        self.assertIsNone(rows["pending"].lease_expires_at)
        for state in ["processing", "completed", "failed_terminal"]:
            row = rows[state]
            self.assertEqual(
                row.claim_token,
                uuid.uuid5(OUTBOX_TOKEN_NAMESPACE, str(row.external_id)),
            )
            self.assertIsNotNone(row.lease_expires_at)
            self.assertEqual(
                row.updated_by_id,
                self.updated_by_ids[row.external_id],
            )
        self.assertEqual(
            rows["processing"].lease_expires_at,
            self.claimed_at,
        )
        self.assertEqual(
            rows["failed_terminal"].safe_code,
            "legacy_failure_reason_unrecorded",
        )

        legacy_apps = self._migrate(self.migrate_from)
        legacy_outbox = legacy_apps.get_model(
            "emr",
            "CorrespondenceCorrectionOutbox",
        ).objects.get(external_id=rows["failed_terminal"].external_id)
        self.assertEqual(legacy_outbox.safe_code, "")
        self.assertNotIn(
            "correspondence_0087_safe_code_backfill",
            legacy_outbox.meta,
        )
