from copy import deepcopy
from uuid import uuid4

from django.test import override_settings
from django.urls import reverse

from care.emr.models.diagnostic_report import DiagnosticReport
from care.emr.models.observation import Observation
from care.emr.models.service_request import ServiceRequest
from care.security.permissions.patient import PatientPermissions
from care.security.permissions.service_request import ServiceRequestPermissions
from care.utils.tests.base import CareAPITestBase
from care_suriname.resources.laboratory_commands.projection import aggregate_fingerprint
from care_suriname.tests.laboratory_fixtures import (
    create_command,
    laboratory_context,
    later_command,
    result_row,
)


@override_settings(CLINICAL_WORKFLOW_MUTATIONS_ENABLED_FACILITIES=["*"])
class LaboratoryCommandTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.context = laboratory_context(self)
        self.client.force_authenticate(self.context["user"])
        self.url = reverse("laboratory-report-command")

    def post(self, payload):
        return self.client.post(self.url, payload, format="json")

    def create(self, rows=None):
        command = create_command(self.context, rows=rows)
        response = self.post(command)
        self.assertEqual(response.status_code, 201, response.data)
        return command, response

    def test_exact_replay_returns_current_aggregate_without_duplication(self):
        command, created = self.create()

        replay = self.post(command)

        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.data["replayed"])
        self.assertEqual(replay.data["report"], created.data["report"])
        self.assertEqual(ServiceRequest.objects.count(), 1)
        self.assertEqual(DiagnosticReport.objects.count(), 1)
        self.assertEqual(Observation.objects.count(), 1)

        changed = deepcopy(command)
        changed["rows"][0]["value"]["input"] = "6.0"
        collision = self.post(changed)
        self.assertEqual(collision.status_code, 409)
        self.assertEqual(collision.data["errors"][0]["type"], "idempotency_conflict")

    def test_transaction_rolls_back_when_later_row_is_invalid(self):
        valid = result_row(self.context["definition"])
        invalid = result_row(self.context["definition"])
        invalid["value"]["unit"] = {
            "system": "http://unitsofmeasure.org",
            "code": "mg/dL",
            "display": "mg/dL",
        }

        response = self.post(create_command(self.context, rows=[valid, invalid]))

        self.assertEqual(response.status_code, 422)
        self.assertEqual(ServiceRequest.objects.count(), 0)
        self.assertEqual(DiagnosticReport.objects.count(), 0)
        self.assertEqual(Observation.objects.count(), 0)

    def test_diagnostic_permission_is_required_for_create_and_replay(self):
        command, _ = self.create()
        user = self.create_user(username="synthetic-lab-service-only")
        role = self.create_role_with_permissions(
            [
                PatientPermissions.can_view_clinical_data.name,
                ServiceRequestPermissions.can_write_service_request.name,
            ]
        )
        self.attach_role_facility_organization_user(
            self.context["organization"], user, role
        )
        self.client.force_authenticate(user)

        replay = self.post(command)
        response = self.post(create_command(self.context))

        self.assertEqual(replay.status_code, 403)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ServiceRequest.objects.count(), 1)
        self.assertEqual(DiagnosticReport.objects.count(), 1)
        self.assertEqual(Observation.objects.count(), 1)

    def test_closed_encounter_rejects_create_without_partial_resources(self):
        encounter = self.context["encounter"]
        encounter.status = "discharged"
        encounter.save(update_fields=["status", "modified_date"])

        response = self.post(create_command(self.context))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["errors"][0]["type"], "state_conflict")
        self.assertEqual(ServiceRequest.objects.count(), 0)
        self.assertEqual(DiagnosticReport.objects.count(), 0)

    def test_wrong_patient_and_facility_contexts_create_nothing(self):
        other_patient = self.create_patient(name="Synthetic wrong-context patient")
        other_facility = self.create_facility(user=self.context["user"])
        mismatches = (
            ("patient", str(other_patient.external_id)),
            ("facility", str(other_facility.external_id)),
        )

        for field, value in mismatches:
            with self.subTest(field=field):
                command = create_command(self.context)
                command[field] = value

                response = self.post(command)

                self.assertEqual(response.status_code, 404)
                self.assertEqual(ServiceRequest.objects.count(), 0)
                self.assertEqual(DiagnosticReport.objects.count(), 0)
                self.assertEqual(Observation.objects.count(), 0)

    def test_unknown_collection_groups_remain_distinct_and_consistent(self):
        first_group = str(uuid4())
        second_group = str(uuid4())
        rows = [
            result_row(
                self.context["definition"],
                collection_group_id=first_group,
                collected_at={"kind": "unknown"},
            ),
            result_row(
                self.context["definition"],
                collection_group_id=second_group,
                collected_at={"kind": "unknown"},
            ),
        ]
        _, created = self.create(rows)

        self.assertEqual(
            {row["collection_group_id"] for row in created.data["report"]["rows"]},
            {first_group, second_group},
        )

        conflicting = [deepcopy(rows[0]), deepcopy(rows[0])]
        conflicting[1]["row_id"] = str(uuid4())
        conflicting[1]["collected_at"] = {
            "kind": "known",
            "value": "2026-09-19T09:00:00-03:00",
        }
        response = self.post(create_command(self.context, rows=conflicting))
        self.assertEqual(response.status_code, 400)

    def test_one_collection_moment_persists_different_row_specimens(self):
        group_id = str(uuid4())
        rows = [
            result_row(
                self.context["definition"],
                collection_group_id=group_id,
                specimen="serum",
            ),
            result_row(
                self.context["definition"],
                collection_group_id=group_id,
                specimen="whole_blood",
            ),
        ]

        _, created = self.create(rows)

        self.assertEqual(
            [row["specimen"] for row in created.data["report"]["rows"]],
            ["serum", "whole_blood"],
        )

    def test_finalize_rejects_catalogue_and_patient_context_changes(self):
        command, _ = self.create()
        finalize = later_command(command, "finalize", 1)
        definition = self.context["definition"]
        definition.title = "Changed without revision increment"
        definition.save(update_fields=["title", "modified_date"])

        catalogue_changed = self.post(finalize)

        self.assertEqual(catalogue_changed.status_code, 409)
        self.assertEqual(
            catalogue_changed.data["errors"][0]["type"], "catalogue_changed"
        )
        definition.title = "Glucose"
        definition.save(update_fields=["title", "modified_date"])
        definition.deleted = True
        definition.save(update_fields=["deleted", "modified_date"])
        deleted = self.post(finalize)
        self.assertEqual(deleted.status_code, 409)
        self.assertEqual(deleted.data["errors"][0]["type"], "catalogue_changed")
        definition.deleted = False
        definition.save(update_fields=["deleted", "modified_date"])
        patient = self.context["patient"]
        patient.gender = "female"
        patient.save(update_fields=["gender", "modified_date"])

        context_changed = self.post(finalize)

        self.assertEqual(context_changed.status_code, 409)
        self.assertEqual(
            context_changed.data["errors"][0]["type"],
            "reference_context_changed",
        )

    def test_year_of_birth_only_patient_gets_a_reference_and_context_drift(self):
        patient = self.context["patient"]
        patient.date_of_birth = None
        patient.year_of_birth = 1986
        patient.save(update_fields=["date_of_birth", "year_of_birth", "modified_date"])

        command, created = self.create()

        row = created.data["report"]["rows"][0]
        self.assertEqual(row["reference_provenance"]["status"], "interpreted")
        self.assertEqual(row["interpretation"]["code"]["code"], "N")
        patient.year_of_birth = 2010
        patient.save(update_fields=["year_of_birth", "modified_date"])

        context_changed = self.post(later_command(command, "finalize", 1))

        self.assertEqual(context_changed.status_code, 409)
        self.assertEqual(
            context_changed.data["errors"][0]["type"],
            "reference_context_changed",
        )

    def test_finalize_correction_audit_and_history(self):
        group_id = str(uuid4())
        rows = [
            result_row(self.context["definition"], collection_group_id=group_id),
            result_row(self.context["definition"], collection_group_id=group_id),
        ]
        command, _ = self.create(rows)
        finalized = self.post(later_command(command, "finalize", 1))
        self.assertEqual(finalized.status_code, 200, finalized.data)
        self.assertEqual(finalized.data["report"]["status"], "final")
        self.assertIsNotNone(finalized.data["report"]["audit"]["finalized_by"])
        finalized_report = DiagnosticReport.objects.get()
        state = finalized_report.meta["care_suriname"]["laboratory_command"]
        self.assertEqual(
            state["aggregate_fingerprint"], aggregate_fingerprint(finalized_report)
        )

        replacement = result_row(
            self.context["definition"],
            collection_group_id=group_id,
            collected_at={
                "kind": "known",
                "value": "2026-09-18T08:30:00-03:00",
            },
        )
        correction = later_command(
            command,
            "correct",
            2,
            reason="Synthetic transcription correction",
            replacements=[
                {
                    "replaces_observation_id": rows[0]["row_id"],
                    "row": replacement,
                }
            ],
        )
        rejected = self.post(correction)
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(
            rejected.data["errors"][0]["type"], "collection_group_conflict"
        )
        correction["replacements"][0]["row"]["collection_group_id"] = str(uuid4())
        correction["client_request_id"] = str(uuid4())
        corrected = self.post(correction)
        self.assertEqual(corrected.status_code, 200, corrected.data)
        self.assertEqual(corrected.data["report"]["rows"][-1]["status"], "amended")
        self.assertEqual(
            corrected.data["report"]["rows"][-1]["correction_reason"],
            "Synthetic transcription correction",
        )
        self.assertIsNotNone(corrected.data["report"]["audit"]["latest_corrected_by"])

        detail = self.client.get(
            reverse("laboratory-report-detail", args=[command["report_id"]]),
            {"include_history": "true"},
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.data["report"]["rows"]), 3)

    def test_out_of_band_observation_change_is_reported_as_drift(self):
        command, _ = self.create()
        observation = Observation.objects.get()
        observation.note = "Native writer changed this row"
        observation.save(update_fields=["note", "modified_date"])

        response = self.post(
            later_command(
                command,
                "update_draft",
                1,
                source=command["source"],
                rows=command["rows"],
            )
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["errors"][0]["type"], "aggregate_drift")

    def test_final_read_uses_immutable_snapshot_after_catalogue_change(self):
        command, created = self.create()
        finalized = self.post(later_command(command, "finalize", 1))
        self.assertEqual(finalized.status_code, 200, finalized.data)
        stored_reference = created.data["report"]["rows"][0]["reference_provenance"]
        definition = self.context["definition"]
        definition.title = "Later catalogue title"
        definition.save(update_fields=["title", "modified_date"])

        detail = self.client.get(
            reverse("laboratory-report-detail", args=[command["report_id"]])
        )

        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["command_result_version"], 2)
        self.assertEqual(
            detail.data["report"]["rows"][0]["reference_provenance"],
            stored_reference,
        )
