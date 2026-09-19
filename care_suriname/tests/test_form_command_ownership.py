"""Six plugin commands preserve native routing, CRUD and write safeguards."""

import ast
from pathlib import Path
from uuid import uuid4

from django.test import SimpleTestCase
from django.urls import resolve, reverse

from care.emr.api.viewsets.form_submission import FormSubmissionViewSet
from care_suriname.api.viewsets.form_commands import command_parts


class FormCommandOwnershipTests(SimpleTestCase):
    def test_all_actions_are_plugin_owned_and_native_methods_unchanged(self):
        host = FormSubmissionViewSet.__bases__[0]
        actions = FormSubmissionViewSet.get_extra_actions()
        self.assertEqual(
            {m.url_path for m in actions},
            {
                "idempotent-create-draft",
                "idempotent-update-draft",
                "idempotent-finalize",
                "idempotent-amend",
                "idempotent-enter-in-error",
                "idempotent-generate-artifact",
            },
        )
        for method in actions:
            self.assertTrue(
                method.__module__.startswith(
                    "care_suriname.api.viewsets.form_commands."
                )
            )
            self.assertNotIn(method.__name__, host.__dict__)
        for name in (
            "create",
            "update",
            "retrieve",
            "get_queryset",
            "validate_data",
            "authorize_create",
            "authorize_update",
            "authorize_retrieve",
            "_lock_submission",
            "_version_conflict",
            "_immutable_conflict",
            "_non_draft_conflict",
            "finalize_response",
        ):
            self.assertIs(
                getattr(FormSubmissionViewSet, name), getattr(host, name), name
            )

    def test_native_source_has_no_command_or_specialty_implementation(self):
        source = (
            Path(__file__).resolve().parents[2]
            / "care/emr/api/viewsets/form_submission.py"
        ).read_text()
        names = {
            n.name
            for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.FunctionDef)
        }
        moved = {
            name
            for part in command_parts()
            for name in vars(part)
            if not name.startswith("__")
        }
        self.assertFalse(names & moved)
        for symbol in (
            "FormSubmissionCommand",
            "FormSubmissionArtifactCommand",
            "client_request_id",
            "UROLOGY_OPERATIONS_QUESTIONNAIRE",
            "register_note_labs",
            "render_form_submission_artifact_pdf",
        ):
            self.assertNotIn(symbol, source)

    def test_six_reverse_names_urls_and_dispatch_are_preserved(self):
        for action in FormSubmissionViewSet.get_extra_actions():
            kwargs = {"external_id": str(uuid4())} if action.detail else {}
            url = reverse("form_submission-" + action.url_name, kwargs=kwargs)
            prefix = "/api/v1/form_submission/"
            if action.detail:
                prefix += kwargs["external_id"] + "/"
            self.assertEqual(url, prefix + action.url_path + "/")
            match = resolve(url)
            self.assertEqual(match.kwargs, kwargs)
            self.assertEqual(match.func.actions, {"post": action.__name__})
            self.assertIs(getattr(match.func.cls, action.__name__), action)

    def test_native_detail_and_unknown_paths_do_not_become_plugin_catchalls(self):
        for value in (str(uuid4()), "unknown"):
            match = resolve(f"/api/v1/form_submission/{value}/")
            self.assertEqual(match.url_name, "form_submission-detail")
            self.assertEqual(match.func.actions["put"], "update")
