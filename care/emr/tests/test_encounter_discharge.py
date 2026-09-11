from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker
from rest_framework import status

from care.emr.models.device import Device, DeviceEncounterHistory
from care.emr.models.encounter_discharge import EncounterDischargeCommand
from care.emr.models.location import FacilityLocation, FacilityLocationEncounter
from care.emr.resources.encounter.constants import (
    ClassChoices,
    DischargeDispositionChoices,
    StatusChoices,
)
from care.emr.resources.location.spec import (
    FacilityLocationOperationalStatusChoices,
    LocationAvailabilityStatusChoices,
    LocationEncounterAvailabilityStatusChoices,
)
from care.security.permissions.encounter import EncounterPermissions
from care.security.permissions.patient import PatientPermissions
from care.utils.tests.base import CareAPITestBase


class EncounterDischargeTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        # Lifecycle tests isolate documentation, covered by DischargeDocumentationTests.
        documentation = patch(
            "care.emr.api.viewsets.encounter_discharge.lock_discharge_documentation",
            return_value=([], None),
        )
        documentation.start()
        self.addCleanup(documentation.stop)
        self.user = self.create_user()
        self.facility = self.create_facility(user=self.user)
        self.patient = self.create_patient(name="Synthetic discharge patient")
        self.organization = self.create_facility_organization(facility=self.facility)
        self.encounter = self.create_encounter(
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
            encounter_class=ClassChoices.imp.value,
            status=StatusChoices.in_progress.value,
            period={
                "start": (timezone.now() - timedelta(days=1)).isoformat(),
            },
            status_history={
                "history": [
                    {
                        "status": StatusChoices.in_progress.value,
                        "moved_at": (timezone.now() - timedelta(days=1)).isoformat(),
                    }
                ]
            },
            hospitalization={"admit_source": "outp"},
        )
        role = self.create_role_with_permissions(
            [
                EncounterPermissions.can_read_encounter.name,
                EncounterPermissions.can_read_encounter_clinical_data.name,
                EncounterPermissions.can_write_encounter.name,
                EncounterPermissions.can_write_encounter_clinical_data.name,
                PatientPermissions.can_view_clinical_data.name,
            ],
            role_name="Synthetic doctor",
        )
        self.attach_role_facility_organization_user(
            self.organization,
            self.user,
            role,
        )
        self.client.force_authenticate(user=self.user)
        self.preflight_url = reverse(
            "encounter-preflight-discharge",
            kwargs={"external_id": self.encounter.external_id},
        )
        self.command_url = reverse(
            "encounter-idempotent-discharge",
            kwargs={"external_id": self.encounter.external_id},
        )
        self.discharged_at = timezone.now() - timedelta(minutes=1)

    def _payload(self, **overrides):
        payload = {
            "client_request_id": str(uuid4()),
            "discharge_disposition": DischargeDispositionChoices.home.value,
            "discharged_at": self.discharged_at.isoformat(),
            "discharge_summary_advice": "Drink voldoende water.",
            "release_bed": True,
        }
        payload.update(overrides)
        return payload

    def _preflight(self, **overrides):
        payload = self._payload(**overrides)
        payload.pop("client_request_id")
        return self.client.post(self.preflight_url, payload, format="json")

    def _assign_bed(self):
        location = baker.make(
            FacilityLocation,
            facility=self.facility,
            status="active",
            operational_status=FacilityLocationOperationalStatusChoices.O.value,
            system_availability_status=(
                LocationAvailabilityStatusChoices.reserved.value
            ),
            name="Synthetic bed",
            description="Synthetic discharge test bed",
            mode="instance",
            form="bd",
            current_encounter=self.encounter,
        )
        association = baker.make(
            FacilityLocationEncounter,
            location=location,
            encounter=self.encounter,
            status=LocationEncounterAvailabilityStatusChoices.active.value,
            start_datetime=timezone.now() - timedelta(hours=2),
            end_datetime=None,
        )
        self.encounter.current_location = location
        self.encounter.save(update_fields=["current_location", "modified_date"])
        return location, association

    def _assign_device(self):
        device = baker.make(
            Device,
            facility=self.facility,
            status="active",
            availability_status="in_use",
            manufacturer="Synthetic",
            current_encounter=self.encounter,
        )
        history = baker.make(
            DeviceEncounterHistory,
            device=device,
            encounter=self.encounter,
            start=timezone.now() - timedelta(hours=2),
            end=None,
        )
        return device, history

    def test_preflight_commit_replay_bed_release_and_readmission(self):
        location, association = self._assign_bed()
        device, device_history = self._assign_device()
        preflight = self._preflight()
        self.assertEqual(preflight.status_code, status.HTTP_200_OK, preflight.data)
        self.assertTrue(preflight.data["ready"], preflight.data)
        self.assertEqual(preflight.data["blocker_codes"], [])

        payload = self._payload(discharge_summary_advice="  Herstel rustig.  ")
        created = self.client.post(self.command_url, payload, format="json")
        replay = self.client.post(self.command_url, payload, format="json")

        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        self.assertFalse(created.data["replayed"])
        self.assertEqual(replay.status_code, status.HTTP_200_OK, replay.data)
        self.assertTrue(replay.data["replayed"])
        self.assertEqual(replay.data["discharge"], created.data["discharge"])
        self.assertEqual(created["Cache-Control"], "no-store")
        self.assertEqual(EncounterDischargeCommand.objects.count(), 1)

        self.encounter.refresh_from_db()
        location.refresh_from_db()
        association.refresh_from_db()
        device.refresh_from_db()
        device_history.refresh_from_db()
        self.assertEqual(self.encounter.status, StatusChoices.discharged.value)
        self.assertEqual(
            self.encounter.hospitalization["discharge_disposition"],
            DischargeDispositionChoices.home.value,
        )
        self.assertEqual(self.encounter.hospitalization["admit_source"], "outp")
        self.assertEqual(self.encounter.discharge_summary_advice, "Herstel rustig.")
        self.assertEqual(
            self.encounter.status_history["history"][-1],
            created.data["discharge"]["status_history_entry"],
        )
        self.assertEqual(
            self.encounter.period["end"],
            created.data["discharge"]["discharged_at"],
        )
        self.assertIsNone(self.encounter.current_location_id)
        self.assertIsNone(location.current_encounter_id)
        self.assertEqual(
            location.system_availability_status,
            LocationAvailabilityStatusChoices.available.value,
        )
        self.assertEqual(
            association.status,
            LocationEncounterAvailabilityStatusChoices.completed.value,
        )
        self.assertEqual(association.end_datetime, self.discharged_at)
        self.assertIsNone(device.current_encounter_id)
        self.assertEqual(device_history.end, self.discharged_at)

        readmission = self.create_encounter(
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
            encounter_class=ClassChoices.imp.value,
            status=StatusChoices.in_progress.value,
        )
        self.assertIsNotNone(readmission.pk)

    def test_preflight_reports_time_state_and_bed_blockers(self):
        self._assign_bed()
        future = timezone.now() + timedelta(hours=1)
        response = self._preflight(
            discharged_at=future.isoformat(),
            release_bed=False,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertFalse(response.data["ready"])
        self.assertEqual(
            set(response.data["blocker_codes"]),
            {"bed_release_required", "discharged_at_in_future"},
        )

        self.encounter.status = StatusChoices.discharged.value
        self.encounter.save(update_fields=["status", "modified_date"])
        stale = self._preflight()
        self.assertFalse(stale.data["ready"])
        self.assertIn("encounter_state_stale", stale.data["blocker_codes"])

    def test_idempotency_conflict_and_new_command_after_discharge(self):
        request_id = str(uuid4())
        original = self._payload(client_request_id=request_id)
        created = self.client.post(self.command_url, original, format="json")
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)

        conflicting = self.client.post(
            self.command_url,
            {**original, "discharge_summary_advice": "Andere inhoud"},
            format="json",
        )
        self.assertEqual(conflicting.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(conflicting.data["blocker_codes"], ["idempotency_conflict"])

        new_command = self.client.post(
            self.command_url,
            self._payload(),
            format="json",
        )
        self.assertEqual(new_command.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("encounter_state_stale", new_command.data["blocker_codes"])
        self.assertEqual(EncounterDischargeCommand.objects.count(), 1)

    def test_command_insert_failure_rolls_back_encounter_and_bed(self):
        location, association = self._assign_bed()
        with patch.object(
            EncounterDischargeCommand,
            "save",
            side_effect=IntegrityError("forced"),
        ):
            response = self.client.post(
                self.command_url,
                self._payload(),
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            response.data["blocker_codes"],
            ["discharge_commit_conflict"],
        )
        self.encounter.refresh_from_db()
        location.refresh_from_db()
        association.refresh_from_db()
        self.assertEqual(self.encounter.status, StatusChoices.in_progress.value)
        self.assertEqual(self.encounter.current_location_id, location.id)
        self.assertEqual(location.current_encounter_id, self.encounter.id)
        self.assertEqual(
            association.status,
            LocationEncounterAvailabilityStatusChoices.active.value,
        )
        self.assertIsNone(association.end_datetime)
        self.assertFalse(EncounterDischargeCommand.objects.exists())

    def test_replay_fails_closed_when_command_integrity_is_corrupt(self):
        payload = self._payload()
        created = self.client.post(self.command_url, payload, format="json")
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        EncounterDischargeCommand._base_manager.update(command_hash="0" * 64)  # noqa: SLF001

        replay = self.client.post(self.command_url, payload, format="json")

        self.assertEqual(replay.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            replay.data["blocker_codes"],
            ["discharge_integrity_failed"],
        )

    def test_preflight_and_command_require_clinical_write_permission(self):
        unauthorized = self.create_user()
        self.client.force_authenticate(user=unauthorized)

        preflight = self._preflight()
        command = self.client.post(
            self.command_url,
            self._payload(),
            format="json",
        )

        self.assertEqual(preflight.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(command.status_code, status.HTTP_403_FORBIDDEN)
        self.encounter.refresh_from_db()
        self.assertEqual(self.encounter.status, StatusChoices.in_progress.value)
        self.assertFalse(EncounterDischargeCommand.objects.exists())

    def test_inconsistent_bed_state_blocks_without_partial_write(self):
        location, _association = self._assign_bed()
        location.current_encounter = None
        location.save(update_fields=["current_encounter", "modified_date"])

        response = self.client.post(
            self.command_url,
            self._payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("bed_assignment_inconsistent", response.data["blocker_codes"])
        self.encounter.refresh_from_db()
        self.assertEqual(self.encounter.status, StatusChoices.in_progress.value)
        self.assertFalse(EncounterDischargeCommand.objects.exists())
