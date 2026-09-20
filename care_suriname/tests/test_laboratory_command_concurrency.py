from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier, Event
from uuid import uuid4

from django.db import close_old_connections, connection
from django.test import override_settings
from django.urls import reverse
from model_bakery import baker
from rest_framework.test import APIClient, APITransactionTestCase

from care.emr.models.diagnostic_report import DiagnosticReport
from care.emr.models.encounter import Encounter
from care.emr.models.observation import Observation
from care.emr.models.patient import Patient
from care.facility.models import Facility
from care.users.models import User
from care_suriname.tests.laboratory_fixtures import (
    create_command,
    later_command,
    make_definition,
)


@override_settings(CLINICAL_WORKFLOW_MUTATIONS_ENABLED_FACILITIES=["*"])
class LaboratoryCommandConcurrencyTests(APITransactionTestCase):
    def setUp(self):
        self.user = baker.make(User, is_superuser=True)
        self.patient = baker.make(
            Patient,
            name="Synthetic concurrent laboratory patient",
            date_of_birth=date(1980, 5, 4),
            gender="male",
        )
        self.facility = baker.make(Facility, created_by=self.user)
        self.encounter = baker.make(
            Encounter,
            patient=self.patient,
            facility=self.facility,
            status="in_progress",
            encounter_class="amb",
            priority="routine",
        )
        self.definition = make_definition(self.facility)
        self.context = {
            "user": self.user,
            "patient": self.patient,
            "facility": self.facility,
            "encounter": self.encounter,
            "definition": self.definition,
        }
        self.command_url = reverse("laboratory-report-command")

    def request(self, method, url, payload=None, barrier=None):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '10s'")
            client = APIClient()
            client.force_authenticate(self.user)
            if barrier:
                barrier.wait(timeout=5)
            if method == "post":
                response = client.post(url, payload, format="json")
            else:
                response = client.get(url)
            return response.status_code
        finally:
            connection.close()

    def test_same_create_command_serializes_to_one_aggregate(self):
        command = create_command(self.context)
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    self.request,
                    "post",
                    self.command_url,
                    command,
                    barrier,
                )
                for _ in range(2)
            ]
        self.assertEqual(sorted(item.result() for item in futures), [200, 201])
        self.assertEqual(DiagnosticReport.objects.count(), 1)
        self.assertEqual(Observation.objects.count(), 1)

    def test_parallel_verified_read_and_update_complete_without_deadlock(self):
        command = create_command(self.context)
        client = APIClient()
        client.force_authenticate(self.user)
        created = client.post(self.command_url, command, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        update = later_command(
            command,
            "update_draft",
            1,
            source=command["source"],
            rows=command["rows"],
        )
        detail_url = reverse("laboratory-report-detail", args=[command["report_id"]])
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            read = pool.submit(self.request, "get", detail_url, None, barrier)
            write = pool.submit(
                self.request,
                "post",
                self.command_url,
                update,
                barrier,
            )
        self.assertEqual(read.result(), 200)
        self.assertEqual(write.result(), 200)
        report = DiagnosticReport.objects.get()
        self.assertEqual(
            report.meta["care_suriname"]["laboratory_command"]["version"], 2
        )

    def test_competing_updates_from_same_version_have_one_winner(self):
        command = create_command(self.context)
        client = APIClient()
        client.force_authenticate(self.user)
        created = client.post(self.command_url, command, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        first = later_command(
            command,
            "update_draft",
            1,
            source={"kind": "external_lab", "label": "Synthetic lab A"},
            rows=command["rows"],
        )
        second = {
            **first,
            "client_request_id": str(uuid4()),
            "source": {"kind": "external_lab", "label": "Synthetic lab B"},
        }
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    self.request,
                    "post",
                    self.command_url,
                    payload,
                    barrier,
                )
                for payload in (first, second)
            ]
        self.assertEqual(sorted(item.result() for item in futures), [200, 409])
        report = DiagnosticReport.objects.get()
        self.assertEqual(
            report.meta["care_suriname"]["laboratory_command"]["version"], 2
        )

    def test_native_change_completed_before_command_lock_is_rejected(self):
        command = create_command(self.context)
        client = APIClient()
        client.force_authenticate(self.user)
        created = client.post(self.command_url, command, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        native_write_done = Event()

        def native_write():
            close_old_connections()
            try:
                observation = Observation.objects.get()
                observation.note = "Synthetic parallel native change"
                observation.save(update_fields=["note", "modified_date"])
                native_write_done.set()
            finally:
                connection.close()

        def plugin_update():
            native_write_done.wait(timeout=5)
            update = later_command(
                command,
                "update_draft",
                1,
                source=command["source"],
                rows=command["rows"],
            )
            return self.request("post", self.command_url, update)

        with ThreadPoolExecutor(max_workers=2) as pool:
            native = pool.submit(native_write)
            plugin = pool.submit(plugin_update)
        native.result()
        self.assertEqual(plugin.result(), 409)
        observation = Observation.objects.get()
        self.assertEqual(observation.note, "Synthetic parallel native change")
