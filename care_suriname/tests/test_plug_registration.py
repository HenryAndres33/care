# ruff: noqa: SLF001  (these tests inspect Django model _meta on purpose)
from django.apps import apps
from django.conf import settings
from django.test import SimpleTestCase
from django.urls import resolve

from care.security.authorization import AuthorizationController


class PlugRegistrationTests(SimpleTestCase):
    """The in-tree plug is installed and reaches every core seam it relies on."""

    def test_app_is_installed_as_a_plug(self):
        self.assertIn("care_suriname", settings.PLUGIN_APPS)
        self.assertTrue(apps.is_installed("care_suriname"))

    def test_v1_paths_resolve_to_plug_views(self):
        """The paths the frontend calls still resolve under api/v1/ (seam in config/urls.py)."""
        uuid = "00000000-0000-4000-8000-000000000000"
        cases = {
            f"/api/v1/consult_closures/{uuid}/": "consult-closure-detail",
            f"/api/v1/consult_closures/{uuid}/idempotent-close/": "consult-closure-idempotent-close",
            "/api/v1/correspondence_letter/": "correspondence-letter-list",
            "/api/v1/clinical_text_resource/": "clinical_text_resource-list",
            "/api/v1/workflow_capabilities/": "workflow-capability-list",
            "/api/v1/users/me/draft-recovery-key/": "draft-recovery-key",
            f"/api/v1/encounter/{uuid}/set-admission-note/": "encounter-set-admission-note",
            f"/api/v1/encounter/{uuid}/idempotent-discharge/": "encounter-idempotent-discharge",
        }
        for path, name in cases.items():
            with self.subTest(path=path):
                match = resolve(path)
                self.assertEqual(match.url_name, name)
                module = match.func.__module__
                self.assertTrue(
                    module.startswith("care_suriname."),
                    module,
                )

    def test_authorization_methods_are_served_by_the_plug(self):
        AuthorizationController.build_cache()
        actions = AuthorizationController.cache["actions"]
        for method in (
            "can_search_patient_directory",
            "can_mark_encounter_questionnaire_entered_in_error",
            "can_read_questionnaire_response_template",
        ):
            with self.subTest(method=method):
                self.assertIn(method, actions)
                self.assertEqual(
                    actions[method].__module__, "care_suriname.authorization"
                )

    def test_core_registration_files_are_upstream(self):
        # Guards against the registrations creeping back into core files.
        import inspect

        from config import api_router

        source = inspect.getsource(api_router)
        self.assertNotIn("care_suriname", source)
        self.assertNotIn("consult_closures", source)
        self.assertNotIn("correspondence", source)


class MovedModelsTests(SimpleTestCase):
    """Phase 2 step 2: the 34 Suriname models live in this app on their old tables."""

    MOVED = [
        "AdmissionDocumentation", "ClinicalTermTranslation", "ClinicalTextResource",
        "ConsultClosure", "ConsultClosureCommand", "ConsultClosureRecoveryTask",
        "CorrespondenceCompilation", "CorrespondenceCompileCommand",
        "CorrespondenceCorrectionCase", "CorrespondenceCorrectionCommand",
        "CorrespondenceCorrectionEvent", "CorrespondenceCorrectionOutbox",
        "CorrespondenceDelivery", "CorrespondenceDeliveryAttempt",
        "CorrespondenceDeliveryEvent", "CorrespondenceLetter",
        "CorrespondenceLetterCommand", "CorrespondenceLetterRevision",
        "CorrespondencePaperReconciliationAttestation", "CorrespondenceRecipient",
        "CorrespondenceRecipientCommand", "CorrespondenceReplacementAttempt",
        "CorrespondenceReview", "CorrespondenceReviewCommand",
        "CorrespondenceSourceCorrection", "CorrespondenceSyntheticProviderInvocation",
        "CorrespondenceSyntheticProviderReceipt", "EmergencyAdmission",
        "EncounterDischargeCommand", "FormSubmissionArtifactCommand",
        "FormSubmissionCommand", "FormSubmissionLabLink", "FormSubmissionSeriesHead",
        "OperationPlan",
    ]  # fmt: skip

    def test_all_moved_models_resolve_here_on_their_original_tables(self):
        for name in self.MOVED:
            with self.subTest(model=name):
                model = apps.get_model("care_suriname", name)
                self.assertEqual(model._meta.db_table, f"emr_{name.lower()}")
                with self.assertRaises(LookupError):
                    apps.get_model("emr", name)

    def test_no_model_in_this_app_references_a_core_table_by_the_wrong_label(self):
        # Every FK from a moved model to another moved model must use this app's label.
        for name in self.MOVED:
            model = apps.get_model("care_suriname", name)
            for field in model._meta.get_fields():
                related = getattr(field, "related_model", None)
                if related is not None and related.__name__ in self.MOVED:
                    self.assertEqual(
                        related._meta.app_label,
                        "care_suriname",
                        f"{name}.{field.name}",
                    )

    def test_no_core_model_references_a_moved_model(self):
        for model in apps.get_app_config("emr").get_models():
            for field in model._meta.local_fields:
                related = getattr(field, "related_model", None)
                if related is not None:
                    self.assertNotEqual(
                        related._meta.app_label,
                        "care_suriname",
                        f"{model.__name__}.{field.name}",
                    )
