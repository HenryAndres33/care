from django.urls import reverse
from rest_framework import status

from care.emr.extensions.encounter_admission_note import (
    ADMISSION_NOTE_EXTENSION_NAME,
    EncounterAdmissionNoteExtension,
)
from care.emr.registries.extensions.registry import ExtensionRegistry
from care.utils.tests.base import CareAPITestBase


class EncounterAdmissionNoteExtensionTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_super_user()
        self.facility = self.create_facility(user=self.user, is_active=True)
        self.organization = self.create_facility_organization(facility=self.facility)
        self.patient = self.create_patient()
        self.client.force_authenticate(user=self.user)
        self.url = reverse("encounter-list")

    def payload(self, note):
        return {
            "patient": str(self.patient.external_id),
            "facility": str(self.facility.external_id),
            "organizations": [str(self.organization.external_id)],
            "status": "in_progress",
            "encounter_class": "imp",
            "priority": "elective",
            "period": {},
            "hospitalization": {"admit_source": "outp"},
            "extensions": {
                ADMISSION_NOTE_EXTENSION_NAME: {
                    "text": note,
                }
            },
        }

    def test_extension_is_provisioned_with_governance_metadata(self):
        extension = ExtensionRegistry.get_extension_obj(
            "encounter", ADMISSION_NOTE_EXTENSION_NAME
        )

        self.assertIsInstance(extension, EncounterAdmissionNoteExtension)
        self.assertEqual(extension.extension_version, "1.0.0")
        self.assertEqual(
            extension.governance_owner,
            "CARE Suriname Clinical Governance",
        )
        self.assertIn("encounter", extension.retention_policy.lower())
        self.assertIn("data migration", extension.migration_path.lower())

    def test_native_create_and_retrieve_round_trip(self):
        response = self.client.post(
            self.url,
            self.payload("Synthetic admission context"),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        detail_url = reverse(
            "encounter-detail",
            kwargs={"external_id": response.data["id"]},
        )
        read_response = self.client.get(
            detail_url,
            {
                "facility": self.facility.external_id,
                "patient": self.patient.external_id,
            },
        )

        self.assertEqual(read_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            read_response.data["extensions"][ADMISSION_NOTE_EXTENSION_NAME],
            {"text": "Synthetic admission context"},
        )

    def test_native_create_and_list_round_trip(self):
        response = self.client.post(
            self.url,
            self.payload("Synthetic list context"),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        list_response = self.client.get(
            self.url,
            {
                "facility": self.facility.external_id,
                "patient": self.patient.external_id,
            },
        )
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        encounter = next(
            item
            for item in list_response.data["results"]
            if item["id"] == response.data["id"]
        )
        self.assertEqual(
            encounter["extensions"][ADMISSION_NOTE_EXTENSION_NAME],
            {"text": "Synthetic list context"},
        )

    def test_invalid_notes_fail_closed(self):
        for note in ("   ", "x" * 4001):
            with self.subTest(note_length=len(note)):
                response = self.client.post(
                    self.url,
                    self.payload(note),
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unknown_admission_note_property_fails_closed(self):
        payload = self.payload("Synthetic admission context")
        payload["extensions"][ADMISSION_NOTE_EXTENSION_NAME]["unreviewed"] = True

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
