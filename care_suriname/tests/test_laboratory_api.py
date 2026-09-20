from django.test import override_settings
from django.urls import reverse

from care.emr.models.diagnostic_report import DiagnosticReport
from care.emr.models.observation import Observation
from care.emr.models.service_request import ServiceRequest
from care.utils.tests.base import CareAPITestBase
from care_suriname.tests.laboratory_fixtures import (
    create_command,
    laboratory_context,
    make_definition,
)


@override_settings(CLINICAL_WORKFLOW_MUTATIONS_ENABLED_FACILITIES=["*"])
class LaboratoryApiTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.context = laboratory_context(self)
        self.client.force_authenticate(self.context["user"])

    def test_authoritative_catalogue_exposes_revision_fingerprint_and_reference(self):
        response = self.client.get(
            reverse("laboratory-definition-list"),
            {"facility": str(self.context["facility"].external_id)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        definition = response.data["results"][0]
        self.assertEqual(definition["version"], 1)
        self.assertEqual(definition["version_kind"], "database_revision")
        self.assertEqual(len(definition["fingerprint"]), 64)
        self.assertEqual(
            definition["reference"]["catalogue_version"],
            "general-academic-adult-v1",
        )
        self.assertNotEqual(definition["version"], 0.1)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_invalid_command_is_safe_400_and_anonymous_routes_are_denied(self):
        command_url = reverse("laboratory-report-command")
        invalid = self.client.post(
            command_url,
            {"contract": "secret raw payload", "value": "patient-secret"},
            format="json",
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(
            invalid.data,
            {
                "errors": [
                    {
                        "type": "laboratory_payload_invalid",
                        "msg": "Laboratory command payload is invalid.",
                    }
                ]
            },
        )
        self.assertNotIn("patient-secret", str(invalid.data))

        self.client.force_authenticate(user=None)
        for url in (
            command_url,
            reverse("laboratory-definition-list"),
            reverse("laboratory-report-list"),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_create_detail_and_discovery_are_command_owned_and_verified(self):
        command = create_command(self.context)
        command["rows"][0]["value"]["unit"]["display"] = "Misleading client label"
        created = self.client.post(
            reverse("laboratory-report-command"),
            command,
            format="json",
        )

        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["command_result_version"], 1)
        self.assertFalse(created.data["replayed"])
        self.assertEqual(created["ETag"], f'"{command["report_id"]}:1"')
        row = created.data["report"]["rows"][0]
        self.assertEqual(row["row_id"], command["rows"][0]["row_id"])
        self.assertEqual(
            row["collection_group_id"],
            command["rows"][0]["collection_group_id"],
        )
        self.assertEqual(row["value"]["input"], "5,0")
        self.assertEqual(row["value"]["stored"], "5.0")
        self.assertEqual(row["value"]["unit"]["display"], "mmol/L")
        self.assertEqual(row["reference_provenance"]["status"], "interpreted")
        self.assertEqual(
            row["reference_provenance"]["catalogue_version"],
            "general-academic-adult-v1",
        )
        self.assertEqual(
            created.data["report"]["audit"]["created_by"]["id"],
            str(self.context["user"].external_id),
        )
        self.assertEqual(ServiceRequest.objects.count(), 1)
        self.assertEqual(DiagnosticReport.objects.count(), 1)
        self.assertEqual(Observation.objects.count(), 1)

        detail = self.client.get(
            reverse("laboratory-report-detail", args=[command["report_id"]])
        )
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["command_result_version"], 1)
        self.assertEqual(detail.data["report"], created.data["report"])

        listing = self.client.get(
            reverse("laboratory-report-list"),
            {
                "patient": command["patient"],
                "facility": command["facility"],
            },
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.data["count"], 1)
        self.assertEqual(listing.data["results"][0]["integrity"], "verified")
        self.assertEqual(listing.data["results"][0]["report_id"], command["report_id"])

        native = self.client.get(
            reverse(
                "observation-list",
                kwargs={"patient_external_id": command["patient"]},
            ),
            {"encounter": command["encounter"]},
        )
        self.assertEqual(native.status_code, 200, native.data)
        native_row = native.data["results"][0]
        self.assertEqual(native_row["interpretation"]["code"]["code"], "N")
        self.assertEqual(
            native_row["interpretation"]["code"]["system"],
            "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
        )
        self.assertEqual(native_row["reference_range"][0]["value"], ">=3.9 to <=5.6")
        native_report = self.client.get(
            reverse(
                "diagnostic_report-detail",
                kwargs={
                    "patient_external_id": command["patient"],
                    "external_id": command["report_id"],
                },
            )
        )
        self.assertEqual(native_report.status_code, 200, native_report.data)
        self.assertEqual(native_report.data["id"], command["report_id"])

    def test_unknown_collection_date_remains_readable_through_native_endpoint(self):
        command = create_command(self.context)
        command["rows"][0]["collected_at"] = {"kind": "unknown"}
        created = self.client.post(
            reverse("laboratory-report-command"), command, format="json"
        )
        self.assertEqual(created.status_code, 201, created.data)

        native = self.client.get(
            reverse(
                "observation-list",
                kwargs={"patient_external_id": command["patient"]},
            ),
            {"encounter": command["encounter"]},
        )

        self.assertEqual(native.status_code, 200, native.data)
        self.assertIsNone(native.data["results"][0]["effective_datetime"])

    def test_catalogue_requires_facility_and_discovery_is_patient_facility_scoped(self):
        self.assertEqual(
            self.client.get(reverse("laboratory-definition-list")).status_code,
            400,
        )
        listing_url = reverse("laboratory-report-list")
        self.assertEqual(self.client.get(listing_url).status_code, 400)
        other = laboratory_context(self)
        response = self.client.get(
            listing_url,
            {
                "patient": str(other["patient"].external_id),
                "facility": str(self.context["facility"].external_id),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)

    def test_non_loinc_code_system_never_receives_loinc_reference_overlay(self):
        make_definition(
            self.context["facility"],
            slug=f"f-{self.context['facility'].external_id}-collision",
            code={
                "system": "https://example.test/not-loinc",
                "code": "14749-6",
                "display": "Synthetic collision",
            },
        )

        response = self.client.get(
            reverse("laboratory-definition-list"),
            {"facility": str(self.context["facility"].external_id)},
        )

        collision = next(
            item
            for item in response.data["results"]
            if item["title"] == "Glucose"
            and item["code"]["system"] != "http://loinc.org"
        )
        self.assertIsNone(collision["reference"])
