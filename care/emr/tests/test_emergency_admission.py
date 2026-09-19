from django.test import override_settings
from django.urls import reverse

from care.security.permissions.encounter import EncounterPermissions
from care.utils.tests.base import CareAPITestBase
from care_suriname.models.emergency_admission import EmergencyAdmission


@override_settings(CLINICAL_WORKFLOW_MUTATIONS_ENABLED_FACILITIES=["*"])
class EmergencyAdmissionTests(CareAPITestBase):
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
            role_name="Synthetic emergency doctor",
        )
        self.attach_role_facility_organization_user(self.organization, self.user, role)
        self.client.force_authenticate(user=self.user)
        self.emergency = self.create_encounter(
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
            status="in_progress",
            encounter_class="emer",
        )
        self.handoff_url = reverse(
            "encounter-admission-handoff",
            kwargs={"external_id": self.emergency.external_id},
        )

    def link(self):
        return self.client.post(
            self.handoff_url,
            {"admission_id": str(self.encounter.external_id)},
            format="json",
        )

    def test_handoff_replays_and_reads_without_moving_or_closing(self):
        first = self.link()
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data, self.link().data)
        self.assertEqual(first.data, self.client.get(self.handoff_url).data)
        self.assertEqual(EmergencyAdmission.objects.count(), 1)
        self.emergency.refresh_from_db()
        self.assertEqual(self.emergency.encounter_class, "emer")
        self.assertEqual(self.emergency.status, "in_progress")

    def test_wrong_patient_rejected(self):
        self.encounter.patient = self.create_patient()
        self.encounter.save()
        self.assertEqual(self.link().status_code, 400)
        self.assertFalse(EmergencyAdmission.objects.exists())

    def test_empty_handoff_is_explicit_json(self):
        result = self.client.get(self.handoff_url)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {"handoff": None})

    def test_wrong_source_class_rejected(self):
        self.emergency.encounter_class = "amb"
        self.emergency.save()
        self.assertEqual(self.link().status_code, 400)

    def test_replay_after_closure_preserves_link(self):
        first = self.link()
        self.encounter.status = "discharged"
        self.encounter.save()
        self.assertEqual(self.link().data, first.data)

    def test_closed_destination_rejected(self):
        self.encounter.status = "discharged"
        self.encounter.save()
        self.assertEqual(self.link().status_code, 400)

    def test_wrong_destination_class_rejected(self):
        self.encounter.encounter_class = "amb"
        self.encounter.save()
        self.assertEqual(self.link().status_code, 400)
        self.assertFalse(EmergencyAdmission.objects.exists())

    def test_closed_source_rejected(self):
        self.emergency.status = "discharged"
        self.emergency.save()
        self.assertEqual(self.link().status_code, 400)
        self.assertFalse(EmergencyAdmission.objects.exists())

    def test_unauthorized_actor_rejected(self):
        self.client.force_authenticate(user=self.create_user())
        self.assertEqual(self.link().status_code, 403)
        self.assertEqual(self.client.get(self.handoff_url).status_code, 403)

    def test_cannot_repoint_existing_link(self):
        self.assertEqual(self.link().status_code, 200)
        self.encounter.status = "discharged"
        self.encounter.save()
        other = self.create_encounter(
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
            status="in_progress",
            encounter_class="imp",
        )
        self.encounter = other
        self.assertEqual(self.link().status_code, 409)

    @override_settings(CLINICAL_WORKFLOW_MUTATIONS_ENABLED_FACILITIES=[])
    def test_handoff_kill_switch(self):
        self.assertEqual(self.link().status_code, 503)
        self.assertFalse(EmergencyAdmission.objects.exists())
