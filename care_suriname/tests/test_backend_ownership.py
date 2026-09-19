"""Bound the remaining dependencies; this is not a claim of complete separation."""
# ruff: noqa: SLF001 -- Django's documented model metadata API.

import ast
from pathlib import Path

from django.apps import apps
from django.test import SimpleTestCase

# Audited after group 8 at 347517ddb34fc1a8170885e4b6b12af3f381114a. Any change requires
# reclassifying the dependency in the final backend separation audit.
EXPECTED_IMPORTS = {
    "care/emr/api/viewsets/encounter.py": {
        "care_suriname.api.viewsets.admission_documentation",
        "care_suriname.api.viewsets.emergency_admission",
        "care_suriname.models.consult_closure",
    },
    "care/emr/api/viewsets/form_submission.py": {
        "care_suriname.api.viewsets.clinical_no_store",
    },
    "care/emr/api/viewsets/medication_request.py": {
        "care_suriname.api.viewsets.clinical_no_store",
    },
    "care/emr/api/viewsets/report/report_upload.py": {
        "care_suriname.api.viewsets.clinical_no_store"
    },
    "care/emr/api/viewsets/scheduling/booking.py": {
        "care_suriname.api.viewsets.operation_plan"
    },
    "care/emr/api/viewsets/scheduling/schedule.py": {
        "care_suriname.resources.scheduling.conflicts"
    },
    "care/emr/api/viewsets/user.py": {"care_suriname.api.viewsets.doctor_activation"},
    "care/emr/models/report/template.py": {"care_suriname.reports.template_versioning"},
    "care/emr/utils/mfa.py": {"care_suriname.draft_recovery.auth"},
    "config/auth_views.py": {"care_suriname.draft_recovery.auth"},
}


class BackendOwnershipTests(SimpleTestCase):
    def test_native_plugin_imports_match_the_reviewed_boundary(self):
        root = Path(__file__).resolve().parents[2]
        actual = {}
        for directory in ("care", "config"):
            for path in (root / directory).rglob("*.py"):
                if {"tests", "migrations"}.intersection(path.parts):
                    continue
                if path.name.startswith("test"):
                    continue
                for node in ast.walk(ast.parse(path.read_text())):
                    names = []
                    if isinstance(node, ast.ImportFrom):
                        names = [node.module or ""]
                    elif isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    for name in names:
                        if name == "care_suriname" or name.startswith("care_suriname."):
                            actual.setdefault(
                                path.relative_to(root).as_posix(), set()
                            ).add(name)
        self.assertEqual(actual, EXPECTED_IMPORTS)

    def test_all_plugin_models_keep_their_existing_table_identity(self):
        models = list(apps.get_app_config("care_suriname").get_models())
        self.assertEqual(len(models), 35)
        for model in models:
            name = model.__name__
            native_app = "users" if name == "DraftRecoveryKey" else "emr"
            with self.subTest(model=name):
                self.assertTrue(model.__module__.startswith("care_suriname.models."))
                self.assertEqual(model._meta.db_table, f"{native_app}_{name.lower()}")
                with self.assertRaises(LookupError):
                    apps.get_model(native_app, name)

    def test_no_native_model_relation_points_into_the_plugin(self):
        # Check all native apps, including users, not only emr. Reverse relations
        # are expected; only fields physically declared on native models count.
        for model in apps.get_models():
            if not model.__module__.startswith("care."):
                continue
            for field in (*model._meta.local_fields, *model._meta.local_many_to_many):
                related = getattr(field, "related_model", None)
                if related is not None:
                    self.assertNotEqual(
                        related._meta.app_label,
                        "care_suriname",
                        f"{model._meta.label}.{field.name}",
                    )

    def test_native_core_does_not_define_custom_idempotent_actions(self):
        root = Path(__file__).resolve().parents[2]
        for path in (root / "care").rglob("*.py"):
            if {"tests", "migrations"}.intersection(path.parts):
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call):
                        continue
                    for keyword in decorator.keywords:
                        if keyword.arg == "url_path" and isinstance(
                            keyword.value, ast.Constant
                        ):
                            self.assertFalse(
                                str(keyword.value.value).startswith("idempotent-"),
                                f"Custom command reintroduced at {path}:{node.lineno}",
                            )

    def test_department_policy_is_not_implemented_in_native_source(self):
        # Configuration may retain its switch; native policy consumers may not.
        root = Path(__file__).resolve().parents[2]
        consumers = set()
        for path in (root / "care").rglob("*.py"):
            if {"tests", "migrations"}.intersection(path.parts):
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == "PATIENT_DEPARTMENT_LONGITUDINAL_ACCESS_ENABLED"
                ):
                    consumers.add(path.relative_to(root).as_posix())
        self.assertEqual(consumers, set())
