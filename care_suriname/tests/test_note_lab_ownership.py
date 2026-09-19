"""Guard the source boundary and the unchanged public command contract."""

import ast
from pathlib import Path

from django.test import SimpleTestCase

from care_suriname.resources.form_submission import commands, note_lab_text, note_labs


class NoteLabOwnershipTests(SimpleTestCase):
    def test_modules_are_plugin_owned_without_native_shims(self):
        root = Path(__file__).resolve().parents[2]
        for module in (commands, note_lab_text, note_labs):
            self.assertTrue(module.__name__.startswith("care_suriname."))
            self.assertFalse(
                (
                    root
                    / "care/emr/resources/form_submission"
                    / Path(module.__file__).name
                ).exists()
            )
        self.assertFalse(
            (root / "care/emr/resources/form_submission/NOTE_LABS.md").exists()
        )

    def test_no_native_implementation_imports_these_modules(self):
        root = Path(__file__).resolve().parents[2]
        consumers = set()
        prefix = "care_suriname.resources.form_submission."
        names = {prefix + name for name in ("commands", "note_labs", "note_lab_text")}
        for path in (root / "care").rglob("*.py"):
            if "tests" in path.parts or "migrations" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom) and node.module in names:
                    consumers.add(path.relative_to(root).as_posix())
        self.assertEqual(consumers, set())

    def test_command_contract_versions_are_preserved(self):
        for spec in (
            commands.FormSubmissionCommandSpec,
            commands.CreateDraftFormSubmissionSpec,
        ):
            schema = spec.model_json_schema()["properties"]["note_lab_contract"]
            self.assertEqual(schema["anyOf"][0]["enum"], ["v1", "v2", "v3"])
            self.assertIsNone(schema["default"])
