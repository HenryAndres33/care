from django.conf import settings
from django.test import SimpleTestCase, override_settings

from care.audit_log.helpers import exclude_model
from care.emr.checks import clinical_workflow_deployment_checks


class TestProductionGate(SimpleTestCase):
    @override_settings(
        IS_PRODUCTION=True,
        SECRET_KEY="deployment-specific-secret",
        ALLOWED_HOSTS=["care.example.org"],
        CSRF_TRUSTED_ORIGINS=["https://care.example.org"],
        CORS_ALLOWED_ORIGINS=["https://care.example.org"],
        CLINICAL_WORKFLOW_MUTATIONS_ENABLED_FACILITIES=[],
        CORRESPONDENCE_DELIVERY_ENABLED_FACILITIES=[],
        AUDIT_LOG_ENABLED=True,
        AUDIT_LOG_DOMAIN_LEDGER_MODE=True,
    )
    def test_explicit_safe_production_configuration_passes(self):
        self.assertEqual(clinical_workflow_deployment_checks(None), [])

    @override_settings(
        IS_PRODUCTION=True,
        SECRET_KEY=settings.INSECURE_DEFAULT_SECRET_KEY,
        ALLOWED_HOSTS=["*"],
        CSRF_TRUSTED_ORIGINS=[],
        CORS_ALLOWED_ORIGINS=[],
        CLINICAL_WORKFLOW_MUTATIONS_ENABLED_FACILITIES=["*"],
        CORRESPONDENCE_DELIVERY_ENABLED_FACILITIES=["*"],
        AUDIT_LOG_ENABLED=True,
        AUDIT_LOG_DOMAIN_LEDGER_MODE=False,
    )
    def test_unsafe_production_configuration_fails_closed(self):
        errors = clinical_workflow_deployment_checks(None)

        self.assertEqual(
            {error.id for error in errors},
            {
                "care.E001",
                "care.E002",
                "care.E003",
                "care.E004",
                "care.E005",
                "care.E006",
                "care.E007",
            },
        )

    def test_clinical_models_use_domain_ledgers_not_value_diff_logs(self):
        exclude_model.cache_clear()
        try:
            for model_name in [
                "emr.FormSubmission",
                "emr.FormSubmissionCommand",
                "emr.FormSubmissionArtifactCommand",
                "emr.QuestionnaireResponse",
                "emr.MedicationRequest",
                "emr.ReportUpload",
                "emr.CorrespondenceCompilation",
                "emr.CorrespondenceDeliveryEvent",
                "emr.ConsultClosureRecoveryTask",
            ]:
                self.assertTrue(exclude_model(model_name), model_name)
        finally:
            exclude_model.cache_clear()

    def test_suriname_presentation_and_utc_worker_transport(self):
        self.assertEqual(settings.TIME_ZONE, "America/Paramaribo")
        self.assertEqual(settings.CELERY_TIMEZONE, "UTC")

    def test_test_settings_bind_only_the_fixed_synthetic_department_fixture(self):
        self.assertEqual(
            settings.CONSULT_CLOSE_REQUIRED_FORMS_BY_DEPARTMENT[
                "19d9ec24-cf5e-4944-93a9-a4900e1f4feb"
            ],
            ["urology-medisch-dossier"],
        )
