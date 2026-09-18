from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from model_bakery import baker
from rest_framework import status

from care.emr.models import EncounterOrganization
from care.emr.models.device import Device, DeviceEncounterHistory
from care.emr.models.location import FacilityLocation, FacilityLocationEncounter
from care.emr.resources.encounter.constants import ClassChoices, StatusChoices
from care.emr.resources.location.spec import (
    FacilityLocationOperationalStatusChoices,
    LocationAvailabilityStatusChoices,
    LocationEncounterAvailabilityStatusChoices,
)
from care.security.permissions.encounter import EncounterPermissions
from care.security.permissions.patient import PatientPermissions
from care.utils.tests.base import CareAPITestBase
from care_suriname.extensions.encounter_admission_note import (
    ADMISSION_NOTE_EXTENSION_NAME,
)


class EncounterAdmissionNoteCommandTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.facility = self.create_facility(user=self.user, is_active=True)
        self.organization = self.create_facility_organization(facility=self.facility)
        self.patient = self.create_patient()
        self.encounter = self.create_encounter(
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
            status=StatusChoices.in_progress.value,
            encounter_class=ClassChoices.imp.value,
            extensions={},
        )
        role = self.create_role_with_permissions(
            [
                EncounterPermissions.can_read_encounter.name,
                EncounterPermissions.can_read_encounter_clinical_data.name,
                EncounterPermissions.can_write_encounter.name,
                PatientPermissions.can_view_clinical_data.name,
            ],
            role_name="Synthetic admission-note doctor",
        )
        self.attach_role_facility_organization_user(
            self.organization,
            self.user,
            role,
        )
        self.client.force_authenticate(user=self.user)
        self.url = reverse(
            "encounter-set-admission-note",
            kwargs={"external_id": self.encounter.external_id},
        )

    def post_note(self, text="Synthetic admission context"):
        return self.client.post(self.url, {"text": text}, format="json")

    def test_success_and_native_detail_read_back(self):
        response = self.post_note()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["extensions"][ADMISSION_NOTE_EXTENSION_NAME],
            {"text": "Synthetic admission context"},
        )
        detail_response = self.client.get(
            reverse(
                "encounter-detail",
                kwargs={"external_id": self.encounter.external_id},
            ),
            {
                "facility": self.facility.external_id,
                "patient": self.patient.external_id,
            },
        )
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            detail_response.data["extensions"][ADMISSION_NOTE_EXTENSION_NAME],
            {"text": "Synthetic admission context"},
        )

    def test_exact_retry_is_idempotent(self):
        first = self.post_note()
        self.encounter.refresh_from_db()
        first_modified_date = self.encounter.modified_date

        second = self.post_note()
        self.encounter.refresh_from_db()

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data, first.data)
        self.assertEqual(self.encounter.modified_date, first_modified_date)

    def test_blank_and_too_long_notes_fail_closed(self):
        for text in ("   ", "x" * 4001):
            with self.subTest(note_length=len(text)):
                response = self.post_note(text)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.encounter.refresh_from_db()
        self.assertNotIn(
            ADMISSION_NOTE_EXTENSION_NAME,
            self.encounter.extensions,
        )

    def test_permission_is_required(self):
        self.client.force_authenticate(user=self.create_user())

        response = self.post_note()

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.encounter.refresh_from_db()
        self.assertEqual(self.encounter.extensions, {})

    def test_clinically_closed_encounter_is_immutable(self):
        self.encounter.status = StatusChoices.discharged.value
        self.encounter.save(update_fields=["status", "modified_date"])

        response = self.post_note()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.encounter.refresh_from_db()
        self.assertEqual(self.encounter.extensions, {})

    def test_unrelated_extensions_are_preserved(self):
        self.encounter.extensions = {"other_extension": {"preserved": True}}
        self.encounter.save(update_fields=["extensions", "modified_date"])

        response = self.post_note()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.encounter.refresh_from_db()
        self.assertEqual(
            self.encounter.extensions["other_extension"],
            {"preserved": True},
        )

    def test_device_and_location_associations_are_unchanged(self):
        location = baker.make(
            FacilityLocation,
            facility=self.facility,
            status="active",
            operational_status=FacilityLocationOperationalStatusChoices.O.value,
            system_availability_status=(
                LocationAvailabilityStatusChoices.reserved.value
            ),
            name="Synthetic bed",
            description="Synthetic admission-note test bed",
            mode="instance",
            form="bd",
            current_encounter=self.encounter,
        )
        association = baker.make(
            FacilityLocationEncounter,
            location=location,
            encounter=self.encounter,
            status=LocationEncounterAvailabilityStatusChoices.active.value,
            start_datetime=timezone.now() - timedelta(hours=1),
            end_datetime=None,
        )
        self.encounter.current_location = location
        self.encounter.save(update_fields=["current_location", "modified_date"])
        device = baker.make(
            Device,
            facility=self.facility,
            status="active",
            availability_status="in_use",
            manufacturer="Synthetic",
            current_encounter=self.encounter,
        )
        device_history = baker.make(
            DeviceEncounterHistory,
            device=device,
            encounter=self.encounter,
            start=timezone.now() - timedelta(hours=1),
            end=None,
        )
        encounter_state = {
            "status": self.encounter.status,
            "period": self.encounter.period,
            "hospitalization": self.encounter.hospitalization,
            "priority": self.encounter.priority,
            "care_team": self.encounter.care_team,
            "facility_organization_cache": self.encounter.facility_organization_cache,
        }
        organization_ids = list(
            EncounterOrganization.objects.filter(encounter=self.encounter).values_list(
                "organization_id", flat=True
            )
        )

        response = self.post_note()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.encounter.refresh_from_db()
        location.refresh_from_db()
        association.refresh_from_db()
        device.refresh_from_db()
        device_history.refresh_from_db()
        self.assertEqual(self.encounter.current_location, location)
        self.assertEqual(location.current_encounter, self.encounter)
        self.assertEqual(association.status, "active")
        self.assertIsNone(association.end_datetime)
        self.assertEqual(device.current_encounter, self.encounter)
        self.assertIsNone(device_history.end)
        self.assertEqual(
            {
                "status": self.encounter.status,
                "period": self.encounter.period,
                "hospitalization": self.encounter.hospitalization,
                "priority": self.encounter.priority,
                "care_team": self.encounter.care_team,
                "facility_organization_cache": (
                    self.encounter.facility_organization_cache
                ),
            },
            encounter_state,
        )
        self.assertEqual(
            list(
                EncounterOrganization.objects.filter(
                    encounter=self.encounter
                ).values_list("organization_id", flat=True)
            ),
            organization_ids,
        )
