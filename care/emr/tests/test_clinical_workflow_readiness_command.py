import io
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from care_suriname.management.commands import clinical_workflow_readiness as readiness


class ClinicalWorkflowReadinessCommandTests(SimpleTestCase):
    def _safe_report(self):
        return {
            "contract": "clinical-workflow-readiness-v1",
            "actorless_legacy_form_submissions": {
                "available": True,
                "count": 0,
                "migration_0086_blocked": False,
            },
            "pending_consult_closure_recovery": {
                "available": True,
                "count": 0,
                "oldest_age_seconds": None,
                "safe_code_buckets": {},
            },
            "correspondence_correction_outbox": {
                "available": True,
                "outstanding_count": 0,
                "failed_terminal_count": 0,
                "oldest_outstanding_age_seconds": None,
                "status_buckets": {},
                "safe_code_buckets": {},
            },
            "correspondence_delivery_latest_state": {
                "available": True,
                "outstanding_count": 0,
                "failed_or_unknown_count": 0,
                "oldest_outstanding_age_seconds": None,
                "state_buckets": {},
                "safe_code_buckets": {},
            },
        }

    def test_json_output_contract_contains_only_aggregate_evidence(self):
        report = self._safe_report()
        report["pending_consult_closure_recovery"].update(
            {
                "count": 2,
                "oldest_age_seconds": 3600,
                "safe_code_buckets": {"projection_unstable": 2},
            }
        )
        stdout = io.StringIO()

        with patch.object(readiness, "build_readiness_report", return_value=report):
            call_command("clinical_workflow_readiness", stdout=stdout)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload, report)
        serialized = stdout.getvalue().lower()
        for forbidden in [
            "patient_name",
            "patient_id",
            "encounter_id",
            "external_id",
            "response_dump",
            "letter_body",
            "reason",
        ]:
            self.assertNotIn(forbidden, serialized)

    def test_fail_on_risk_prints_report_then_returns_safe_nonzero_error(self):
        report = self._safe_report()
        report["actorless_legacy_form_submissions"].update(
            {"count": 1, "migration_0086_blocked": True}
        )
        stdout = io.StringIO()

        with (
            patch.object(readiness, "build_readiness_report", return_value=report),
            self.assertRaisesRegex(
                CommandError,
                "clinical_workflow_readiness_failed",
            ),
        ):
            call_command(
                "clinical_workflow_readiness",
                fail_on_risk=True,
                stdout=stdout,
            )

        self.assertEqual(json.loads(stdout.getvalue()), report)

    def test_actorless_pre_0086_query_uses_only_count_and_no_identifiers(self):
        queryset = MagicMock()
        queryset.count.return_value = 3
        manager = MagicMock()
        manager.filter.return_value = queryset
        columns = {
            readiness.FormSubmission._meta.db_table: {  # noqa: SLF001
                "status",
                "workflow_finalized_by_id",
            }
        }

        form_submission_model = SimpleNamespace(
            _base_manager=manager,
            _meta=readiness.FormSubmission._meta,  # noqa: SLF001
        )
        with patch.object(readiness, "FormSubmission", form_submission_model):
            result = readiness._actorless_legacy_submissions(columns)  # noqa: SLF001

        manager.filter.assert_called_once_with(
            status="submitted",
            workflow_finalized_by__isnull=True,
        )
        self.assertEqual(
            result,
            {"available": True, "count": 3, "migration_0086_blocked": True},
        )

    def test_safe_code_and_age_helpers_fail_closed_without_echoing_free_text(self):
        self.assertEqual(
            readiness._safe_code_bucket("projection_unstable"),  # noqa: SLF001
            "projection_unstable",
        )
        self.assertEqual(
            readiness._safe_code_bucket("Patient Jane Doe: free text"),  # noqa: SLF001
            "invalid_safe_code",
        )
        now = datetime(2026, 7, 20, 12, tzinfo=UTC)
        self.assertEqual(
            readiness._age_seconds(  # noqa: SLF001
                now - timedelta(seconds=61),
                now=now,
            ),
            61,
        )
        self.assertEqual(
            readiness._age_seconds(now + timedelta(seconds=10), now=now),  # noqa: SLF001
            0,
        )

    def test_missing_predeploy_tables_are_reported_without_querying_models(self):
        with (
            patch.object(readiness, "_database_columns", return_value={}),
            patch.object(
                readiness.FormSubmission._base_manager,  # noqa: SLF001
                "filter",
            ) as form_filter,
        ):
            report = readiness.build_readiness_report()

        self.assertFalse(report["actorless_legacy_form_submissions"]["available"])
        self.assertFalse(report["pending_consult_closure_recovery"]["available"])
        self.assertFalse(report["correspondence_correction_outbox"]["available"])
        self.assertFalse(report["correspondence_delivery_latest_state"]["available"])
        form_filter.assert_not_called()
