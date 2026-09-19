"""Real PostgreSQL patient/encounter locks serialize concurrent commands."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.db import close_old_connections, connection
from django.urls import reverse
from model_bakery import baker
from rest_framework.test import APIClient, APITransactionTestCase

from care.emr.models.condition import Condition
from care.emr.models.encounter import Encounter
from care.emr.models.patient import Patient
from care.facility.models import Facility
from care.users.models import User


class DiagnosisCommandConcurrencyTests(APITransactionTestCase):
    def setUp(self):
        terminology = patch(
            "care.emr.utils.valueset_coding_type.validate_valueset",
            side_effect=lambda field, slug, code: code,
        )
        terminology.start()
        self.addCleanup(terminology.stop)
        self.user = baker.make(User, is_superuser=True)
        self.patient = baker.make(Patient, name="Synthetic concurrent diagnosis")
        self.facility = baker.make(Facility, created_by=self.user)
        self.encounter = baker.make(
            Encounter,
            patient=self.patient,
            facility=self.facility,
            status="in_progress",
            encounter_class="amb",
            priority="routine",
        )
        self.url = reverse(
            "diagnosis-idempotent-create",
            kwargs={"patient_external_id": self.patient.external_id},
        )
        self.barrier = Barrier(2)

    def post(self, request_id):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '10s'")
            client = APIClient()
            client.force_authenticate(user=self.user)
            self.barrier.wait(timeout=5)
            response = client.post(
                self.url,
                {
                    "client_request_id": str(request_id),
                    "encounter": str(self.encounter.external_id),
                    "category": "chronic_condition",
                    "clinical_status": "active",
                    "verification_status": "confirmed",
                    "severity": None,
                    "code": {
                        "system": "http://test_system.care/test",
                        "code": "CONCURRENT",
                        "display": "Synthetic",
                    },
                    "clinical_domain": "urology",
                    "onset": {},
                    "abatement": {},
                    "note": "",
                },
                format="json",
            )
            return response.status_code
        finally:
            connection.close()

    def test_same_id_replays_one_record(self):
        request_id = uuid4()
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(self.post, [request_id, request_id]))
        self.assertEqual(sorted(statuses), [200, 201])
        self.assertEqual(Condition.objects.filter(patient=self.patient).count(), 1)

    def test_distinct_ids_reject_duplicate_active_diagnosis(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(self.post, [uuid4(), uuid4()]))
        self.assertEqual(sorted(statuses), [201, 409])
        self.assertEqual(Condition.objects.filter(patient=self.patient).count(), 1)
