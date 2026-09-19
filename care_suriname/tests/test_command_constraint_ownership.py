"""Command identifiers stay plugin-owned without changing database constraints."""
# ruff: noqa: SLF001 -- Django's documented model metadata API.

import ast
from pathlib import Path

from django.db.models import UniqueConstraint
from django.test import SimpleTestCase

from care_suriname.models import (
    form_submission_artifact_command,
    form_submission_command,
)


class CommandConstraintOwnershipTests(SimpleTestCase):
    def test_command_class_and_database_constraint_names_stay_identical(self):
        cases = (
            (
                form_submission_command.FormSubmissionCommand,
                form_submission_command.FORM_SUBMISSION_COMMAND_IDEMPOTENCY_CONSTRAINT,
                "formsub_cmd_client_request_id_uniq",
            ),
            (
                form_submission_artifact_command.FormSubmissionArtifactCommand,
                form_submission_artifact_command.FORM_ARTIFACT_COMMAND_IDEMPOTENCY_CONSTRAINT,
                "formartifact_cmd_request_id_uniq",
            ),
        )
        for model, constant, expected in cases:
            with self.subTest(model=model.__name__):
                self.assertEqual(constant, expected)
                self.assertEqual(model.IDEMPOTENCY_CONSTRAINT_NAME, expected)
                constraints = [
                    constraint
                    for constraint in model._meta.constraints
                    if constraint.name == expected
                ]
                self.assertEqual(len(constraints), 1)
                self.assertIsInstance(constraints[0], UniqueConstraint)
                self.assertEqual(constraints[0].fields, ("client_request_id",))

    def test_native_production_cannot_redeclare_or_import_plugin_constants(self):
        names = {
            "FORM_SUBMISSION_COMMAND_IDEMPOTENCY_CONSTRAINT",
            "FORM_ARTIFACT_COMMAND_IDEMPOTENCY_CONSTRAINT",
        }
        root = Path(__file__).resolve().parents[2]
        for directory in ("care", "config"):
            for path in (root / directory).rglob("*.py"):
                if {"tests", "migrations"}.intersection(path.parts):
                    continue
                tree = ast.parse(path.read_text())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Name):
                        self.assertNotIn(node.id, names, str(path))
                    elif isinstance(node, ast.ImportFrom):
                        self.assertTrue(
                            names.isdisjoint(alias.name for alias in node.names),
                            str(path),
                        )
                    elif isinstance(node, ast.Attribute):
                        self.assertNotIn(node.attr, names, str(path))
