from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from care.security.permissions.encounter import EncounterPermissions
from care.utils.tests.base import CareAPITestBase
from care_suriname.models.admission_documentation import AdmissionDocumentation


@override_settings(CLINICAL_WORKFLOW_MUTATIONS_ENABLED_FACILITIES=["*"])
class AdmissionDocumentationTests(CareAPITestBase):
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
            status="in_progress",
            encounter_class="imp",
        )
        role = self.create_role_with_permissions(
            [
                EncounterPermissions.can_read_encounter.name,
                EncounterPermissions.can_read_encounter_clinical_data.name,
                EncounterPermissions.can_write_encounter.name,
            ],
            role_name="Synthetic ward doctor",
        )
        self.attach_role_facility_organization_user(self.organization, self.user, role)
        self.client.force_authenticate(user=self.user)
        self.url = reverse(
            "encounter-documentation-slot",
            kwargs={"external_id": self.encounter.external_id},
        )

    def reserve(self, **body):
        return self.client.post(self.url, body or {"kind": "visit"}, format="json")

    def test_repeat_reuses_identity_and_keeps_admission(self):
        first = self.reserve()
        self.assertEqual(first.status_code, 200, first.data)
        second = self.reserve()
        self.assertEqual(first.data, second.data)
        self.assertEqual(first.data["admission_id"], str(self.encounter.external_id))
        self.assertEqual(first.data["patient_id"], str(self.patient.external_id))
        self.assertEqual(AdmissionDocumentation.objects.count(), 1)

    def test_kinds_have_separate_stable_identities(self):
        identities = [
            self.reserve(kind=kind).data["form_instance_id"]
            for kind in ("admission", "visit", "discharge")
        ]
        self.assertEqual(len(set(identities)), 3)

    def test_foreign_user_cannot_reserve(self):
        self.client.force_authenticate(user=self.create_user())
        self.assertEqual(self.reserve().status_code, 403)
        self.assertEqual(AdmissionDocumentation.objects.count(), 0)

    def test_closed_admission_cannot_allocate(self):
        self.encounter.status = "discharged"
        self.encounter.save()
        self.assertEqual(self.reserve().status_code, 400)
        self.assertEqual(AdmissionDocumentation.objects.count(), 0)

    def test_outpatient_cannot_allocate(self):
        self.encounter.encounter_class = "amb"
        self.encounter.save()
        self.assertEqual(self.reserve().status_code, 400)

    def test_visit_uses_suriname_day_and_rejects_backdating(self):
        today = timezone.localdate(timezone=ZoneInfo("America/Paramaribo"))
        self.assertEqual(self.reserve().data["slot"], f"visit:{today}")
        response = self.reserve(kind="visit", visit_date=str(today - timedelta(days=1)))
        self.assertEqual(response.status_code, 400)

    def test_database_rejects_duplicate_slot(self):
        self.reserve()
        slot = AdmissionDocumentation.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            AdmissionDocumentation.objects.create(
                admission=self.encounter, slot=slot.slot, created_by=self.user
            )

    @override_settings(CLINICAL_WORKFLOW_MUTATIONS_ENABLED_FACILITIES=[])
    def test_kill_switch_blocks_new_reservations(self):
        self.assertEqual(self.reserve().status_code, 503)
        self.assertEqual(AdmissionDocumentation.objects.count(), 0)
