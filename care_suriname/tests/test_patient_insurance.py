from uuid import uuid4

from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker

from care.emr.extensions.base import ExtensionResource
from care.emr.extensions.validator import validate_extensions
from care.emr.models.questionnaire import FormSubmission, Questionnaire
from care.emr.registries.extensions.registry import ExtensionRegistry
from care.emr.resources.form_submission.spec import FormSubmissionStatusChoices
from care.utils.tests.base import CareAPITestBase
from care_suriname.extensions.patient_insurance import (
    PATIENT_INSURANCE_EXTENSION_NAME,
    PatientInsuranceExtension,
    insurance_display,
)
from care_suriname.reports.form_submission_artifact import (
    build_form_submission_artifact_html,
)

SURVAM = {
    "version": "1",
    "insurer": "SURVAM",
    "plan_survam": "PZS-basis",
    "policy_number": " 12345 ",
}


class PatientInsuranceExtensionTests(SimpleTestCase):
    def setUp(self):
        self.extension = PatientInsuranceExtension()

    def test_registered_at_startup_for_patients(self):
        handler = ExtensionRegistry.get_extension_obj(
            ExtensionResource.patient.value, PATIENT_INSURANCE_EXTENSION_NAME
        )
        self.assertIsInstance(handler, PatientInsuranceExtension)

    def test_native_patient_validation_stores_the_normalized_value(self):
        cleaned = validate_extensions(
            {PATIENT_INSURANCE_EXTENSION_NAME: SURVAM}, ExtensionResource.patient.value
        )
        self.assertEqual(
            cleaned[PATIENT_INSURANCE_EXTENSION_NAME]["policy_number"], "12345"
        )
        with self.assertRaises(ValueError):
            validate_extensions(
                {PATIENT_INSURANCE_EXTENSION_NAME: {"version": "1"}},
                ExtensionResource.patient.value,
            )

    def test_complete_insured_value_is_trimmed(self):
        self.assertEqual(self.extension.validate(SURVAM)["policy_number"], "12345")
        self.assertEqual(
            self.extension.serialize_extensions(SURVAM)["policy_number"], "12345"
        )

    def test_eigen_rekening_needs_no_plan_or_number(self):
        data = {"version": "1", "insurer": "Eigen rekening", "policy_number": ""}
        self.assertEqual(
            self.extension.validate(data), {"version": "1", "insurer": "Eigen rekening"}
        )

    def test_empty_object_of_a_legacy_patient_is_accepted(self):
        self.assertEqual(self.extension.validate({}), {})

    def test_incomplete_or_foreign_values_are_refused(self):
        refused = [
            {"version": "1"},
            {"version": "1", "insurer": "Assuria"},
            {"version": "1", "insurer": "SURVAM", "policy_number": "1"},
            {"version": "1", "insurer": "SURVAM", "plan_survam": "PZS-basis"},
            {**SURVAM, "plan_survam": "SZF premium"},
            {**SURVAM, "plan_szf": "SZF"},
            {"version": "1", "insurer": "Eigen rekening", "policy_number": "9"},
            {**SURVAM, "policy_number": "x" * 65},
            {**SURVAM, "note": "extra"},
        ]
        for data in refused:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.extension.validate(data)

    def test_display_line_and_legacy_fallback(self):
        stored = self.extension.validate(SURVAM)
        extensions = {PATIENT_INSURANCE_EXTENSION_NAME: stored}
        self.assertEqual(
            insurance_display(extensions), "SURVAM · PZS-basis · nr. 12345"
        )
        legacy = {"core": {"urology_patient_profile_v1": {"insurance_summary": "SZF"}}}
        self.assertEqual(insurance_display(legacy), "SZF")
        self.assertIsNone(insurance_display({}))


class PatientInsurancePdfTests(CareAPITestBase):
    def test_note_pdf_shows_the_insurance(self):
        user = self.create_user(username="insurance-doctor")
        facility = self.create_facility(user=user)
        organization = self.create_facility_organization(facility=facility)
        patient = self.create_patient(name="Synthetische Patiënt")
        patient.extensions = {
            PATIENT_INSURANCE_EXTENSION_NAME: PatientInsuranceExtension().validate(
                SURVAM
            )
        }
        patient.save(update_fields=["extensions"])
        encounter = self.create_encounter(
            patient=patient, facility=facility, organization=organization
        )
        submission = FormSubmission(
            questionnaire=baker.make(Questionnaire, slug="insurance-note"),
            patient=patient,
            encounter=encounter,
            status=FormSubmissionStatusChoices.submitted.value,
            response_dump={"anamnese": "Synthetisch"},
            workflow_finalized_at=timezone.now(),
            workflow_finalized_by=user,
            created_by=user,
        )

        html = build_form_submission_artifact_html(
            artifact_id=uuid4(), submission=submission, generated_at=timezone.now()
        )

        self.assertIn("Verzekering", html)
        self.assertIn("SURVAM · PZS-basis · nr. 12345", html)


class PatientInsuranceApiTests(CareAPITestBase):
    """Create, read back and update through CARE's own patient API."""

    def test_patient_api_stores_refuses_and_updates_insurance(self):
        from care.emr.locks.billing import PatientCreateLock
        from care.security.permissions.patient import PatientPermissions

        user = self.create_user()
        geo = self.create_organization(org_type="govt")
        role = self.create_role_with_permissions(
            permissions=[
                PatientPermissions.can_create_patient.name,
                PatientPermissions.can_write_patient.name,
                PatientPermissions.can_list_patients.name,
            ]
        )
        self.attach_role_organization_user(geo, user, role)
        self.client.force_authenticate(user=user)
        data = {
            "name": "Synthetische Verzekering",
            "gender": "male",
            "address": "Paramaribo",
            "phone_number": "+5978123456",
            "geo_organization": geo.external_id,
            "age": 50,
        }
        url = reverse("patient-list")

        PatientCreateLock().release()
        incomplete = {"version": "1", "insurer": "SURVAM"}
        response = self.client.post(
            url,
            {**data, "extensions": {PATIENT_INSURANCE_EXTENSION_NAME: incomplete}},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

        PatientCreateLock().release()
        response = self.client.post(
            url,
            {**data, "extensions": {PATIENT_INSURANCE_EXTENSION_NAME: SURVAM}},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        detail = reverse("patient-detail", kwargs={"external_id": response.data["id"]})
        read = self.client.get(detail).data["extensions"]
        self.assertEqual(
            read[PATIENT_INSURANCE_EXTENSION_NAME],
            {
                "version": "1",
                "insurer": "SURVAM",
                "plan_survam": "PZS-basis",
                "policy_number": "12345",
            },
        )

        own = {"version": "1", "insurer": "Eigen rekening", "plan_survam": ""}
        response = self.client.put(
            detail,
            {**data, "extensions": {PATIENT_INSURANCE_EXTENSION_NAME: own}},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            self.client.get(detail).data["extensions"][
                PATIENT_INSURANCE_EXTENSION_NAME
            ],
            {"version": "1", "insurer": "Eigen rekening"},
        )
