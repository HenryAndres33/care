"""Plugin actions retain the native nested router, CRUD and schema contract."""

import ast
from pathlib import Path
from uuid import uuid4

from django.test import SimpleTestCase
from django.urls import resolve, reverse

from care.emr.api.viewsets.condition import DiagnosisViewSet
from care_suriname.api.viewsets.diagnosis_commands import DiagnosisCommandActions


class DiagnosisCommandOwnershipTests(SimpleTestCase):
    def test_command_is_plugin_owned_but_native_crud_and_permissions_are_unchanged(
        self,
    ):
        self.assertIs(
            DiagnosisViewSet.idempotent_create,
            DiagnosisCommandActions.idempotent_create,
        )
        host = DiagnosisViewSet.__bases__[0]
        for name in (
            "create",
            "update",
            "destroy",
            "retrieve",
            "authorize_create",
            "authorize_update",
            "authorize_retrieve",
            "perform_create",
            "get_queryset",
        ):
            self.assertIs(getattr(DiagnosisViewSet, name), getattr(host, name), name)
        self.assertNotIn("idempotent_create", host.__dict__)

    def test_native_source_does_not_define_custom_commands(self):
        root = Path(__file__).resolve().parents[2]
        source = (root / "care/emr/api/viewsets/condition.py").read_text()
        tree = ast.parse(source)
        methods = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        self.assertFalse({"idempotent_create", "_idempotency_response"} & methods)
        self.assertNotIn("care_suriname", source)
        self.assertNotIn("client_request_id", source)

    def test_exact_url_reverse_and_parameter_contract(self):
        patient = str(uuid4())
        url = reverse(
            "diagnosis-idempotent-create", kwargs={"patient_external_id": patient}
        )
        self.assertEqual(url, f"/api/v1/patient/{patient}/diagnosis/idempotent-create/")
        match = resolve(url)
        self.assertEqual(match.kwargs, {"patient_external_id": patient})
        self.assertEqual(match.func.actions, {"post": "idempotent_create"})
        self.assertIs(
            match.func.cls.idempotent_create, DiagnosisCommandActions.idempotent_create
        )
        self.assertEqual(match.func.initkwargs["basename"], "diagnosis")
        self.assertFalse(match.func.initkwargs["detail"])

    def test_uuid_detail_and_unknown_action_still_resolve_natively(self):
        patient, diagnosis = str(uuid4()), str(uuid4())
        detail = resolve(f"/api/v1/patient/{patient}/diagnosis/{diagnosis}/")
        self.assertEqual(detail.url_name, "diagnosis-detail")
        self.assertEqual(detail.func.actions["put"], "update")
        self.assertEqual(
            resolve(f"/api/v1/patient/{patient}/diagnosis/unknown/").url_name,
            "diagnosis-detail",
        )
