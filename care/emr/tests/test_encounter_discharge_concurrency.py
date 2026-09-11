from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.db import close_old_connections
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker
from rest_framework.test import APIClient, APITransactionTestCase

from care.emr.models.encounter import Encounter, EncounterOrganization
from care.emr.models.encounter_discharge import EncounterDischargeCommand
from care.emr.models.organization import FacilityOrganization
from care.emr.models.patient import Patient
from care.emr.resources.encounter.constants import (
    ClassChoices,
    EncounterPriorityChoices,
    StatusChoices,
)
from care.facility.models import Facility
from care.users.models import User


class EncounterDischargeConcurrencyTests(APITransactionTestCase):
    reset_sequences = True

    def setUp(self):
        # Exercise command replay/bed locks separately from documentation validation.
        documentation = patch(
            "care.emr.api.viewsets.encounter_discharge.lock_discharge_documentation",
            return_value=([], None),
        )
        documentation.start()
        self.addCleanup(documentation.stop)
        self.user = baker.make(User, is_superuser=True)
        self.facility = baker.make(Facility, created_by=self.user)
        self.patient = baker.make(Patient, name="Synthetic concurrent discharge")
        self.organization = baker.make(
            FacilityOrganization,
            facility=self.facility,
        )
        self.encounter = baker.make(
            Encounter,
            patient=self.patient,
            facility=self.facility,
            encounter_class=ClassChoices.imp.value,
            status=StatusChoices.in_progress.value,
            priority=EncounterPriorityChoices.elective.value,
            period={
                "start": (timezone.now() - timedelta(days=1)).isoformat(),
            },
            status_history={"history": []},
            hospitalization={},
        )
        EncounterOrganization.objects.create(
            encounter=self.encounter,
            organization=self.organization,
        )
        self.url = reverse(
            "encounter-idempotent-discharge",
            kwargs={"external_id": self.encounter.external_id},
        )
        self.start = Barrier(2)

    def _post(self, advice):
        close_old_connections()
        client = APIClient()
        client.force_authenticate(user=self.user)
        self.start.wait(timeout=5)
        response = client.post(
            self.url,
            {
                "client_request_id": str(uuid4()),
                "discharge_disposition": "home",
                "discharged_at": (timezone.now() - timedelta(minutes=1)).isoformat(),
                "discharge_summary_advice": advice,
                "release_bed": True,
            },
            format="json",
        )
        close_old_connections()
        return response.status_code

    def test_two_distinct_commands_serialize_to_one_discharge(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(self._post, ["Eerste", "Tweede"]))

        self.assertEqual(sorted(statuses), [201, 409])
        self.assertEqual(EncounterDischargeCommand.objects.count(), 1)
        self.encounter.refresh_from_db()
        self.assertEqual(self.encounter.status, StatusChoices.discharged.value)
        self.assertEqual(len(self.encounter.status_history["history"]), 1)
