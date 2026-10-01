"""Contract: Tekeningen in an operation report reach its one PDF.

Owner request (1 Oct 2026, operation-report rebuild): an operation report
(questionnaire ``urology-operaties``) stores drawings exactly as a note does,
in ``content.clinicalActions.drawings``; the operation validator accepts
them, the finalized snapshot hash covers them, and the stored-record PDF
prints the figures beside the narrative, as for a note.
"""

from copy import deepcopy
from uuid import uuid4

from django.utils import timezone
from model_bakery import baker

from care.emr.models.questionnaire import FormSubmission, Questionnaire
from care.emr.resources.form_submission.spec import FormSubmissionStatusChoices
from care.utils.tests.base import CareAPITestBase
from care_suriname.reports.form_submission_artifact import (
    build_form_submission_artifact_html,
    render_form_submission_artifact_pdf,
)
from care_suriname.reports.note_drawings_validation import (
    InvalidNoteDrawingsError,
    validate_note_drawings,
)
from care_suriname.resources.form_submission.commands import (
    finalized_form_submission_snapshot_hash,
)
from care_suriname.resources.form_submission.urology_operation import (
    validate_urology_operation_response_dump,
)

_STONE = {
    "kind": "steen",
    "mirrored": False,
    "note": "pyelum rechts",
    "rotation": 0,
    "scale": 0.5,
    "size": "8 mm",
    "x": 400,
    "y": 500,
}


def _operation_dump(drawings):
    """The frontend's operation report dump (operationFormContent.ts)."""
    return {
        "schema": "care.urology.encounter-owned-form-submission",
        "version": 3,
        "identity": {"formType": "operaties"},
        "content": {
            "noteText": "OPERATIEVERLOOP\nDEMO-SIM URS rechts, steen verwijderd.",
            "narrativePreview": "OPERATIEVERLOOP",
            "values": {
                "operation.schema": "care.urology.operation-documentation",
                "operation.schemaVersion": "1",
                "operation.clinicalConfirmation": True,
            },
            "clinicalActions": {
                "schema": "care.urology.form-clinical-actions",
                "version": 1,
                "actions": [],
                "drawings": drawings,
            },
        },
    }


def _drawing(**change):
    return {
        "number": 1,
        "template": "urology-urinewegen-v1",
        "stamps": [_STONE],
        "strokes": [],
        "text": "DEMO-SIM steen in pyelum",
    } | change


def _finalized_operation(test, response_dump):
    user = test.create_user(username="operation-drawings-doctor")
    facility = test.create_facility(user=user)
    organization = test.create_facility_organization(facility=facility)
    patient = test.create_patient(name="DEMO-SIM Operatie")
    encounter = test.create_encounter(
        patient=patient, facility=facility, organization=organization
    )
    return FormSubmission(
        questionnaire=baker.make(Questionnaire, slug="urology-operaties"),
        patient=patient,
        encounter=encounter,
        status=FormSubmissionStatusChoices.submitted.value,
        response_dump=response_dump,
        workflow_finalized_at=timezone.now(),
        workflow_finalized_by=user,
        created_by=user,
    )


class OperationReportDrawingsTests(CareAPITestBase):
    def _submission(self, response_dump):
        return _finalized_operation(self, response_dump)

    def test_operation_validator_and_drawing_validation_accept_the_report(self):
        dump = _operation_dump([_drawing()])
        validate_urology_operation_response_dump(dump)
        validate_note_drawings(dump)
        broken = _operation_dump([_drawing(template="onbekend")])
        validate_urology_operation_response_dump(broken)
        with self.assertRaises(InvalidNoteDrawingsError):
            validate_note_drawings(broken)

    def test_pdf_prints_the_figures_beside_the_operation_narrative(self):
        submission = self._submission(
            _operation_dump(
                [_drawing(), _drawing(number=2, template="urology-blaas-v1")]
            )
        )
        html = build_form_submission_artifact_html(
            artifact_id=uuid4(), submission=submission, generated_at=timezone.now()
        )
        self.assertIn("Operatieverslag", html)
        self.assertIn('<div class="note-with-drawings">', html)
        column = html.index('<div class="note-drawings-column">')
        self.assertLess(column, html.index("OPERATIEVERLOOP"))
        self.assertEqual(html.count('<figure class="note-drawing"'), 2)
        self.assertIn("Afbeelding 1 \N{EN DASH} URS", html)
        self.assertIn("Afbeelding 2 \N{EN DASH} Cystoscopie", html)
        self.assertIn("1 Steen 8 mm, pyelum rechts", html)
        self.assertIn("DEMO-SIM steen in pyelum", html)
        self.assertTrue(render_form_submission_artifact_pdf(html).startswith(b"%PDF"))

    def test_snapshot_hash_covers_the_drawings(self):
        submission = self._submission(_operation_dump([_drawing()]))
        before = finalized_form_submission_snapshot_hash(submission)
        moved = deepcopy(submission.response_dump)
        moved["content"]["clinicalActions"]["drawings"][0]["stamps"][0]["x"] = 401
        submission.response_dump = moved
        self.assertNotEqual(before, finalized_form_submission_snapshot_hash(submission))


class OperationReportHeaderTests(CareAPITestBase):
    def _submission(self, response_dump):
        return _finalized_operation(self, response_dump)

    def test_header_shows_operation_date_procedure_and_surgeon(self):
        dump = _operation_dump([])
        dump["content"]["values"] |= {
            "operation.procedureDate": "2026-10-01",
            "operation.procedureLabel": "Ureterorenoscopie (URS)",
            "operation.surgeonDisplay": "DEMO-SIM Operateur",
        }
        html = build_form_submission_artifact_html(
            artifact_id=uuid4(),
            submission=self._submission(dump),
            generated_at=timezone.now(),
        )
        self.assertIn("Datum ingreep", html)
        self.assertIn("01-10-2026", html)
        self.assertIn("Ureterorenoscopie (URS)", html)
        self.assertIn("DEMO-SIM Operateur", html)
        self.assertNotIn("Consultdatum", html)

    def test_missing_operation_values_print_as_not_recorded(self):
        html = build_form_submission_artifact_html(
            artifact_id=uuid4(),
            submission=self._submission(_operation_dump([])),
            generated_at=timezone.now(),
        )
        self.assertIn("Datum ingreep", html)
        self.assertGreaterEqual(html.count("Niet geregistreerd"), 3)
