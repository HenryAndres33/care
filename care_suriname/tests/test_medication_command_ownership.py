"""Medication command ownership preserves native dispatch, safety and CRUD."""

import ast
from pathlib import Path
from uuid import uuid4

from django.test import SimpleTestCase
from django.urls import resolve, reverse

from care.emr.api.viewsets.medication_request import MedicationRequestViewSet
from care_suriname.api.viewsets.medication_commands import MedicationCommandActions


class MedicationCommandOwnershipTests(SimpleTestCase):
    def test_actions_are_plugin_owned_and_native_safety_is_inherited(self):
        host = MedicationRequestViewSet.__bases__[0]
        for name in ("idempotent_create", "idempotent_reconcile"):
            self.assertIs(
                getattr(MedicationRequestViewSet, name),
                getattr(MedicationCommandActions, name),
            )
            self.assertNotIn(name, host.__dict__)
        for name in (
            "create",
            "update",
            "destroy",
            "retrieve",
            "perform_create",
            "authorize_create",
            "authorize_update",
            "get_queryset",
            "finalize_response",
            "validate_data",
        ):
            self.assertIs(
                getattr(MedicationRequestViewSet, name), getattr(host, name), name
            )

    def test_native_source_has_no_custom_command_execution(self):
        root = Path(__file__).resolve().parents[2]
        source = (root / "care/emr/api/viewsets/medication_request.py").read_text()
        methods = {
            n.name
            for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.FunctionDef)
        }
        self.assertFalse(
            {
                "idempotent_create",
                "idempotent_reconcile",
                "_idempotency_response",
                "_is_idempotency_constraint_violation",
            }
            & methods
        )
        for symbol in (
            "client_request_id",
            "canonical_medication_request_hash",
            "require_workflow_mutations_enabled",
            "resolve_created_prescription",
        ):
            self.assertNotIn(symbol, source)

    def test_exact_command_url_schema_metadata_and_dispatch(self):
        patient = str(uuid4())
        for action in ("create", "reconcile"):
            url = reverse(
                f"medication-request-idempotent-{action}",
                kwargs={"patient_external_id": patient},
            )
            self.assertEqual(
                url,
                f"/api/v1/patient/{patient}/medication/request/idempotent-{action}/",
            )
            match = resolve(url)
            self.assertEqual(match.kwargs, {"patient_external_id": patient})
            self.assertEqual(match.func.actions, {"post": f"idempotent_{action}"})
            self.assertIs(
                getattr(match.func.cls, f"idempotent_{action}"),
                getattr(MedicationCommandActions, f"idempotent_{action}"),
            )
            self.assertFalse(match.func.initkwargs["detail"])
            self.assertEqual(match.func.initkwargs["basename"], "medication-request")

    def test_uuid_and_unknown_paths_keep_native_detail_resolution(self):
        patient = str(uuid4())
        for value in (str(uuid4()), "unknown"):
            match = resolve(f"/api/v1/patient/{patient}/medication/request/{value}/")
            self.assertEqual(match.url_name, "medication-request-detail")
            self.assertEqual(match.func.actions["put"], "update")
