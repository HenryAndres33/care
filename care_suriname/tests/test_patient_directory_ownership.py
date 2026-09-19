"""Real URL resolution, native behavior and source ownership boundaries."""

import ast
from pathlib import Path

from django.test import SimpleTestCase
from django.urls import Resolver404, resolve, reverse
from drf_spectacular.generators import SchemaGenerator

from care.emr.api.viewsets.patient import PatientViewSet
from care_suriname.api.viewsets.patient_directory import PatientDirectoryViewSet
from care_suriname.v1_urls import priority_urlpatterns


class PatientDirectoryOwnershipTests(SimpleTestCase):
    def test_route_reverse_and_native_patient_methods(self):
        url = reverse("patient-directory")
        self.assertEqual(url, "/api/v1/patient/directory/")
        match = resolve(url)
        self.assertIs(match.func.cls, PatientDirectoryViewSet)
        self.assertEqual(match.func.actions["get"], "directory")
        self.assertTrue(set(match.func.actions).issubset({"get", "head"}))
        detail = resolve("/api/v1/patient/00000000-0000-4000-8000-000000000001/")
        self.assertIs(detail.func.cls, PatientViewSet)
        self.assertEqual(detail.func.actions["get"], "retrieve")
        self.assertEqual(detail.func.actions["put"], "update")
        self.assertEqual(detail.func.actions["patch"], "partial_update")
        self.assertEqual(detail.func.actions["delete"], "destroy")
        self.assertIs(resolve("/api/v1/patient/search/").func.cls, PatientViewSet)
        with self.assertRaises(Resolver404):
            resolve("/api/v1/patient/directory/not-a-route/")

    def test_authentication_permissions_and_exception_handling_are_inherited(self):
        self.assertEqual(
            PatientDirectoryViewSet.authentication_classes,
            PatientViewSet.authentication_classes,
        )
        self.assertEqual(
            PatientDirectoryViewSet.permission_classes,
            PatientViewSet.permission_classes,
        )
        self.assertIs(
            PatientDirectoryViewSet.get_exception_handler,
            PatientViewSet.get_exception_handler,
        )

    def test_schema_still_exposes_get_with_identity_only_response(self):
        schema = SchemaGenerator(patterns=priority_urlpatterns).get_schema(public=True)
        operation = schema["paths"]["/patient/directory/"]
        self.assertIn("get", operation)
        self.assertNotIn("post", operation)
        fields = schema["components"]["schemas"]["PatientDirectorySpec"]["properties"]
        self.assertEqual(
            set(fields),
            {
                "id",
                "name",
                "gender",
                "phone_number",
                "date_of_birth",
                "year_of_birth",
                "meta",
            },
        )

    def test_core_cannot_reintroduce_directory_contract_or_action(self):
        root = Path(__file__).resolve().parents[2]
        forbidden = {
            "PatientDirectorySpec",
            "PatientDirectoryPagination",
            "DirectoryRequestSpec",
            "PatientDirectoryRequestSpec",
        }
        for path in (root / "care").rglob("*.py"):
            if {"tests", "migrations"}.intersection(path.parts):
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    self.assertNotIn(node.name, forbidden, str(path))
                if isinstance(node, ast.ImportFrom):
                    self.assertTrue(
                        forbidden.isdisjoint(alias.name for alias in node.names),
                        str(path),
                    )
        self.assertNotIn(
            "directory",
            {action.__name__ for action in PatientViewSet.get_extra_actions()},
        )
