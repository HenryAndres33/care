from types import SimpleNamespace
from unittest.mock import patch

from django.db import transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker
from rest_framework import status
from rest_framework.exceptions import ValidationError

from care.emr.api.viewsets.device import DeviceViewSet
from care.emr.api.viewsets.encounter import LiveFilter
from care.emr.api.viewsets.form_submission import FormSubmissionViewSet
from care.emr.api.viewsets.location import FacilityLocationEncounterViewSet
from care.emr.api.viewsets.medication_request import MedicationRequestViewSet
from care.emr.models.device import Device
from care.emr.models.location import FacilityLocation, FacilityLocationEncounter
from care.emr.models.medication_request import MedicationRequest
from care.emr.models.questionnaire import FormSubmission, Questionnaire
from care.emr.resources.encounter.constants import (
    CLINICALLY_CLOSED_CHOICES,
    COMPLETED_CHOICES,
    ClassChoices,
    EncounterPriorityChoices,
    StatusChoices,
)
from care.utils.tests.base import CareAPITestBase


class EncounterClinicalClosureTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_super_user()
        self.facility = self.create_facility(user=self.user)
        self.patient = self.create_patient()
        self.organization = self.create_facility_organization(
            facility=self.facility
        )
        self.encounter = self.create_encounter(
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
            status=StatusChoices.discharged.value,
            encounter_class=ClassChoices.imp.value,
        )
        self.client.force_authenticate(user=self.user)

    def _payload(self, encounter_status):
        return {
            "patient": str(self.patient.external_id),
            "facility": str(self.facility.external_id),
            "status": encounter_status,
            "encounter_class": ClassChoices.imp.value,
            "priority": EncounterPriorityChoices.elective.value,
            "discharge_summary_advice": "",
            "external_identifier": "clinical-closure-test",
            "organizations": [str(self.organization.external_id)],
        }

    def _detail_url(self, encounter):
        url = reverse(
            "encounter-detail",
            kwargs={"external_id": encounter.external_id},
        )
        return (
            f"{url}?facility={self.facility.external_id}"
            f"&patient={self.patient.external_id}"
        )

    def test_discharged_is_clinically_closed_but_not_administratively_completed(self):
        self.assertIn(
            StatusChoices.discharged.value,
            CLINICALLY_CLOSED_CHOICES,
        )
        self.assertNotIn(
            StatusChoices.discharged.value,
            COMPLETED_CHOICES,
        )

    def test_ordinary_update_rejects_discharged_encounter(self):
        response = self.client.put(
            self._detail_url(self.encounter),
            self._payload(StatusChoices.in_progress.value),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.encounter.refresh_from_db()
        self.assertEqual(self.encounter.status, StatusChoices.discharged.value)

    def test_ordinary_update_cannot_transition_to_discharged(self):
        active_encounter = self.create_encounter(
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
            status=StatusChoices.in_progress.value,
            encounter_class=ClassChoices.imp.value,
        )

        response = self.client.put(
            self._detail_url(active_encounter),
            self._payload(StatusChoices.discharged.value),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        active_encounter.refresh_from_db()
        self.assertEqual(
            active_encounter.status,
            StatusChoices.in_progress.value,
        )

    def test_live_filter_classifies_discharged_as_closed(self):
        closed = LiveFilter().filter(
            self.encounter.__class__.objects.all(),
            "true",
        )
        open_encounters = LiveFilter().filter(
            self.encounter.__class__.objects.all(),
            "false",
        )

        self.assertTrue(closed.filter(pk=self.encounter.pk).exists())
        self.assertFalse(open_encounters.filter(pk=self.encounter.pk).exists())

    @override_settings(MAX_ACTIVE_ENCOUNTERS_PER_PATIENT_IN_FACILITY=1)
    def test_discharged_encounter_does_not_block_immediate_readmission(self):
        response = self.client.post(
            reverse("encounter-list"),
            self._payload(StatusChoices.in_progress.value),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_medication_creation_is_rejected(self):
        medication = MedicationRequest(
            patient=self.patient,
            encounter=self.encounter,
            status="active",
            intent="order",
            do_not_perform=False,
        )
        view = MedicationRequestViewSet()
        view.request = SimpleNamespace(user=self.user)

        with self.assertRaises(ValidationError):
            view.perform_create(medication)

    def test_location_and_device_associations_are_rejected(self):
        location = baker.make(
            FacilityLocation,
            facility=self.facility,
            status="active",
            operational_status="operational",
            system_availability_status="available",
            name="Clinical closure room",
            description="Synthetic",
            mode="instance",
            form="ro",
        )
        association = FacilityLocationEncounter(
            location=location,
            encounter=self.encounter,
            status="active",
            start_datetime=timezone.now(),
        )
        location_view = FacilityLocationEncounterViewSet()
        location_view.kwargs = {
            "facility_external_id": self.facility.external_id,
            "location_external_id": location.external_id,
        }
        location_view.request = SimpleNamespace(user=self.user)

        with self.assertRaises(ValidationError):
            location_view.perform_create(association)

        device = baker.make(
            Device,
            facility=self.facility,
            status="active",
            availability_status="available",
            manufacturer="Synthetic",
        )
        device_view = DeviceViewSet()
        device_view.request = SimpleNamespace(
            user=self.user,
            data={"encounter": str(self.encounter.external_id)},
        )

        with (
            patch.object(
                device_view,
                "get_facility_obj",
                return_value=self.facility,
            ),
            patch.object(device_view, "get_object", return_value=device),
            self.assertRaises(ValidationError),
        ):
            device_view.associate_encounter(device_view.request)

    def test_form_amendment_is_rejected(self):
        questionnaire = baker.make(
            Questionnaire,
            version="1",
            title="Clinical closure form",
            subject_type="patient",
            status="active",
        )
        submission = baker.make(
            FormSubmission,
            questionnaire=questionnaire,
            patient=self.patient,
            encounter=self.encounter,
            status="submitted",
            workflow_finalized_at=timezone.now(),
            workflow_finalized_by=self.user,
            finalized_snapshot_hash="a" * 64,
        )
        view = FormSubmissionViewSet()
        view.request = SimpleNamespace(user=self.user)

        with (
            patch.object(view, "_authorize_questionnaire_submission"),
            transaction.atomic(),
            self.assertRaises(ValidationError),
        ):
            view._lock_and_authorize_write_context(  # noqa: SLF001
                submission,
                "amend",
            )
