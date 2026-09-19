from copy import deepcopy
from uuid import uuid4

from django.test import SimpleTestCase
from django.urls import reverse
from model_bakery import baker
from rest_framework.exceptions import ValidationError

from care.emr.models.diagnostic_report import DiagnosticReport
from care.emr.models.observation import Observation
from care.emr.models.questionnaire import FormSubmission, Questionnaire
from care.emr.models.service_request import ServiceRequest
from care.security.permissions.diagnostic_report import DiagnosticReportPermissions
from care.security.permissions.encounter import EncounterPermissions
from care.security.permissions.patient import PatientPermissions
from care.security.permissions.questionnaire import QuestionnairePermissions
from care.security.permissions.service_request import ServiceRequestPermissions
from care.utils.tests.base import CareAPITestBase
from care_suriname.models.form_submission_lab import FormSubmissionLabLink
from care_suriname.resources.form_submission.note_lab_text import parse_note_labs

TEXT = """DEMO-SIM-NOTE-LABS; software simulation, not actual care.
Gekoppeld laboratorium:
PSA initieel: 6 µg/L; afnamedatum: 2025-01-01; bron: DEMO extern lab
PSA actueel: 8 µg/L; afnamedatum: 2025-02-01; bron: DEMO extern lab
Testosteron actueel: 12,5 nmol/L; afnamedatum: 2025-02-01; bron: DEMO extern lab
Einde gekoppeld laboratorium."""
COMPACT_TEXT = """Labuitslagen: PSA actueel, Testosteron actueel
Afnamedatum: 2025-02-01
PSA actueel: 8 µg/L
Testosteron actueel: 12,5 nmol/L

Beleid:
Controle."""


class NoteLabTextTests(SimpleTestCase):
    def test_legacy_descriptive_heading_preserves_rows_and_fingerprints(self):
        self.assertEqual(
            parse_note_labs("Labuitslagen: extern\n" + TEXT), parse_note_labs(TEXT)
        )

    def test_malformed_supported_row_never_partially_parses(self):
        for prefix in ("", "Natrium: 140 mmol/L\n"):
            with self.subTest(prefix=prefix), self.assertRaises(ValidationError):
                parse_note_labs(
                    "Labuitslagen:\nAfnamedatum: 2026-09-17\n" + prefix + "CRP:7.4 mg/L"
                )

    def test_only_opted_in_rows_are_read(self):
        self.assertEqual(parse_note_labs("PSA was 8; follow up later"), [])
        rows = parse_note_labs(TEXT)
        self.assertEqual([row.value for row in rows], ["6", "8", "12.5"])

    def test_source_can_be_omitted_but_not_left_unresolved(self):
        rows = parse_note_labs(TEXT.replace("; bron: DEMO extern lab", ""))
        self.assertTrue(
            all(
                row.source == "Handmatig ingevoerd via medische notitie" for row in rows
            )
        )
        self.assertEqual(parse_note_labs(TEXT)[0].source, "DEMO extern lab")
        for source in ["", "  ", "[[*BRON*]]"]:
            with self.subTest(source=source), self.assertRaises(ValidationError):
                parse_note_labs(TEXT.replace("DEMO extern lab", source))

    def test_unknown_is_not_zero(self):
        text = "Gekoppeld laboratorium:\nPSA initieel: onbekend\nEinde gekoppeld laboratorium."
        self.assertEqual(parse_note_labs(text), [])
        self.assertEqual(
            parse_note_labs(TEXT.replace("6 µg/L", "0 µg/L"))[0].value, "0"
        )

    def test_incomplete_invalid_or_ambiguous_rows_fail(self):
        invalid = [
            TEXT.replace("2025-01-01", "2025-02-30"),
            TEXT.replace("2025-01-01", "2999-01-01"),
            TEXT.replace("2025-01-01", "2025-03-01"),
            TEXT.replace("6 µg/L", "-6 µg/L"),
            TEXT.replace("6 µg/L", "6 nmol/L"),
            TEXT.replace("DEMO extern lab", "[[*LABBRON*]]"),
            TEXT.replace("PSA actueel", "PSA initieel"),
            TEXT.replace("Einde gekoppeld laboratorium.", ""),
        ]
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(ValidationError):
                parse_note_labs(text)

    def test_equivalent_decimals_have_same_fingerprint(self):
        self.assertEqual(
            parse_note_labs(TEXT)[0].fingerprint,
            parse_note_labs(TEXT.replace("6 µg/L", "6,00 µg/L"))[0].fingerprint,
        )

    def test_compact_block_reuses_group_date_and_stops_at_following_prose(self):
        rows = parse_note_labs(COMPACT_TEXT)
        self.assertEqual([row.value for row in rows], ["8", "12.5"])
        self.assertTrue(all(row.measured.isoformat() == "2025-02-01" for row in rows))

    def test_compact_block_allows_truthful_unknown_date(self):
        [row] = parse_note_labs(
            "Labuitslagen: CRP\nAfnamedatum: onbekend\nCRP: 7,4 mg/L"
        )
        self.assertIsNone(row.measured)

    def test_natural_heading_supports_existing_per_row_psa_dates(self):
        rows = parse_note_labs(
            "Labuitslagen:\n"
            "PSA actueel: 8 µg/L; afnamedatum: 2025-02-01\n\n"
            "Beleid:\nControle"
        )
        self.assertEqual(rows[0].value, "8")

    def test_transitional_compact_start_remains_readable(self):
        [row] = parse_note_labs(
            "Gekoppeld laboratorium:\nAfnamedatum: 2025-02-01\nCRP: 7 mg/L"
        )
        self.assertEqual(row.value, "7")


class NoteLabCommandTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.facility = self.create_facility(user=self.user)
        self.organization = self.create_facility_organization(facility=self.facility)
        self.questionnaire_organization = self.create_organization()
        self.patient = self.create_patient()
        self.encounter = self.create_encounter(
            patient=self.patient, facility=self.facility, organization=self.organization
        )
        self.questionnaire = baker.make(
            Questionnaire,
            slug="note-lab-test",
            organization_cache=[self.questionnaire_organization.id],
        )
        permissions = [
            PatientPermissions.can_view_clinical_data.name,
            EncounterPermissions.can_read_encounter_clinical_data.name,
            EncounterPermissions.can_submit_encounter_questionnaire.name,
            QuestionnairePermissions.can_submit_questionnaire.name,
        ]
        role = self.create_role_with_permissions(permissions)
        self.attach_role_facility_organization_user(self.organization, self.user, role)
        self.attach_role_organization_user(
            self.questionnaire_organization, self.user, role
        )
        self.client.force_authenticate(user=self.user)
        self.submission = baker.make(
            FormSubmission,
            questionnaire=self.questionnaire,
            patient=self.patient,
            encounter=self.encounter,
            status="draft",
            response_dump={"field": "before"},
            created_by=self.user,
            updated_by=self.user,
        )

    def allow_labs(self):
        role = self.create_role_with_permissions(
            [
                ServiceRequestPermissions.can_write_service_request.name,
                DiagnosticReportPermissions.can_write_diagnostic_report.name,
            ]
        )
        self.attach_role_facility_organization_user(self.organization, self.user, role)

    def dump(self, text=TEXT, *, register=True):
        return {
            "schema": "care.urology.encounter-owned-form-submission",
            "version": 2,
            "identity": {"formType": "medisch-dossier"},
            "content": {
                "noteText": text,
                "narrativePreview": text,
                "values": {"register_note_labs": register},
            },
        }

    def command(self, **extra):
        return {
            "client_request_id": str(uuid4()),
            "expected_version": self.submission.resource_version,
            "patient": str(self.patient.external_id),
            "encounter": str(self.encounter.external_id),
            "questionnaire": self.questionnaire.slug,
            **extra,
        }

    def update(self, dump=None, command=None):
        return self.client.post(
            reverse(
                "form_submission-idempotent-update-draft",
                kwargs={"external_id": self.submission.external_id},
            ),
            command or self.command(response_dump=dump or self.dump()),
            format="json",
        )

    def test_atomic_native_results_and_repeat_save(self):
        self.allow_labs()
        payload = self.command(response_dump=self.dump())
        response = self.update(command=payload)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Observation.objects.count(), 3)
        self.assertEqual(DiagnosticReport.objects.count(), 3)
        self.assertEqual(FormSubmissionLabLink.objects.count(), 3)
        psa = Observation.objects.get(value__value="8")
        self.assertEqual(psa.patient_id, self.patient.id)
        self.assertEqual(psa.encounter_id, self.encounter.id)
        self.assertEqual(psa.main_code["code"], "2857-1")
        self.assertEqual(psa.value["unit"]["code"], "ug/L")
        self.assertEqual(psa.effective_datetime.date().isoformat(), "2025-02-01")
        replay = self.update(command=payload)
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.data["replayed"])
        self.submission.refresh_from_db()
        self.assertEqual(self.update().status_code, 200)
        self.assertEqual(Observation.objects.count(), 3)

    def test_source_free_note_registers_once_with_manual_provenance(self):
        self.allow_labs()
        text = TEXT.replace("; bron: DEMO extern lab", "")
        self.assertEqual(self.update(self.dump(text)).status_code, 200)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.response_dump["content"]["noteText"], text)
        self.assertEqual(Observation.objects.count(), 3)
        for request in ServiceRequest.objects.all():
            self.assertIn("Handmatig ingevoerd via medische notitie", request.note)
            self.assertNotIn("Externe uitslag", request.note)
        self.assertEqual(self.update(self.dump(text)).status_code, 200)
        self.assertEqual(Observation.objects.count(), 3)

    def test_compact_unknown_date_is_native_and_remains_unknown(self):
        self.allow_labs()
        text = "Labuitslagen: CRP\nAfnamedatum: onbekend\nCRP: 7,4 mg/L"
        response = self.update(self.dump(text))
        self.assertEqual(response.status_code, 200, response.data)
        observation = Observation.objects.get()
        self.assertIsNone(observation.effective_datetime)
        self.assertIn("Afnamedatum onbekend.", observation.note)
        self.submission.refresh_from_db()
        self.assertEqual(self.update(self.dump(text)).status_code, 200)
        self.assertEqual(Observation.objects.count(), 1)

    def test_removing_an_existing_explicit_source_is_rejected(self):
        self.allow_labs()
        self.assertEqual(self.update().status_code, 200)
        self.submission.refresh_from_db()
        response = self.update(self.dump(TEXT.replace("; bron: DEMO extern lab", "")))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Observation.objects.count(), 3)

    def test_denied_lab_permission_rolls_back_note_and_results(self):
        response = self.update()
        self.assertEqual(response.status_code, 403, response.data)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.resource_version, 1)
        self.assertEqual(self.submission.response_dump, {"field": "before"})
        self.assertEqual(ServiceRequest.objects.count(), 0)
        self.assertEqual(Observation.objects.count(), 0)

    def test_autosave_keeps_incomplete_input_without_creating_results(self):
        response = self.update(
            self.dump(TEXT.replace("6 µg/L", "[[*VALUE*]] µg/L"), register=False)
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Observation.objects.count(), 0)

    def test_invalid_explicit_save_rolls_back_everything(self):
        self.allow_labs()
        response = self.update(self.dump(TEXT.replace("12,5 nmol/L", "bad nmol/L")))
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(DiagnosticReport.objects.count(), 0)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.resource_version, 1)

    def test_confirmed_result_change_or_removal_is_rejected(self):
        self.allow_labs()
        self.assertEqual(self.update().status_code, 200)
        self.submission.refresh_from_db()
        for text in [TEXT.replace("8 µg/L", "9 µg/L"), "Note without lab block"]:
            response = self.update(self.dump(text))
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Observation.objects.count(), 3)
        self.assertTrue(Observation.objects.filter(value__value="8").exists())
        self.assertEqual(
            self.update({"field": "replace clinical schema"}).status_code, 400
        )

    def test_stale_and_wrong_patient_commands_cannot_create_labs(self):
        self.allow_labs()
        stale = self.command(response_dump=self.dump())
        wrong = deepcopy(stale)
        wrong["patient"] = str(uuid4())
        response = self.update(command=wrong)
        self.assertEqual(response.status_code, 404, response.data)
        self.assertEqual(Observation.objects.count(), 0)
        self.assertEqual(self.update(self.dump(register=False)).status_code, 200)
        response = self.update(command=stale)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(Observation.objects.count(), 0)

    def test_finalize_registers_autosaved_values_once(self):
        self.allow_labs()
        self.assertEqual(self.update(self.dump(register=False)).status_code, 200)
        self.submission.refresh_from_db()
        response = self.client.post(
            reverse(
                "form_submission-idempotent-finalize",
                kwargs={"external_id": self.submission.external_id},
            ),
            self.command(),
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Observation.objects.count(), 3)

    def test_create_with_labs_is_atomic_and_replayable(self):
        self.allow_labs()
        payload = self.command(
            response_dump=self.dump(),
            form_instance_id=str(uuid4()),
            note_lab_contract="v3",
        )
        payload.pop("expected_version")
        url = reverse("form_submission-idempotent-create-draft")
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Observation.objects.count(), 3)
        replay = self.client.post(url, payload, format="json")
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(Observation.objects.count(), 3)

    def test_changed_native_result_blocks_note_registration(self):
        self.allow_labs()
        self.assertEqual(self.update().status_code, 200)
        self.submission.refresh_from_db()
        observation = Observation.objects.get(value__value="8")
        observation.value = {**observation.value, "value": "9"}
        observation.save()
        self.assertEqual(self.update().status_code, 400)
        self.assertEqual(Observation.objects.count(), 3)
