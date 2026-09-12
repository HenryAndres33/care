from datetime import timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker
from rest_framework.exceptions import ValidationError

from care.emr.models.operation_plan import OperationPlan
from care.emr.models.scheduling import SchedulableResource, TokenBooking, TokenSlot
from care.emr.resources.scheduling.operation_plan import validate_planned_form_identity
from care.security.permissions.encounter import EncounterPermissions
from care.security.permissions.schedule import SchedulePermissions
from care.utils.tests.base import CareAPITestBase


@override_settings(CLINICAL_WORKFLOW_MUTATIONS_ENABLED_FACILITIES=["*"])
class OperationPlanTests(CareAPITestBase):
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
            encounter_class="amb",
        )
        self.planner = self.create_user()
        planning = [
            SchedulePermissions.can_list_booking.name,
            SchedulePermissions.can_write_booking.name,
        ]
        planner_role = self.create_role_with_permissions(
            planning, role_name="Synthetic planner"
        )
        doctor_role = self.create_role_with_permissions(
            [
                *planning,
                EncounterPermissions.can_read_encounter.name,
                EncounterPermissions.can_read_encounter_clinical_data.name,
                EncounterPermissions.can_write_encounter.name,
            ],
            role_name="Synthetic surgeon",
        )
        self.attach_role_facility_organization_user(
            self.organization, self.user, doctor_role
        )
        self.attach_role_facility_organization_user(
            self.organization, self.planner, planner_role
        )
        self.resource = baker.make(
            SchedulableResource,
            facility=self.facility,
            user=self.user,
            resource_type="practitioner",
        )
        self.slot = baker.make(
            TokenSlot,
            resource=self.resource,
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
        )
        self.booking = baker.make(
            TokenBooking,
            token_slot=self.slot,
            patient=self.patient,
            booked_by=self.planner,
            status="booked",
        )
        self.client.force_authenticate(self.user)
        self.kwargs = {
            "facility_external_id": self.facility.external_id,
            "external_id": self.booking.external_id,
        }

    def plan(self, **changes):
        return self.client.post(
            reverse("appointments-operation-plan", kwargs=self.kwargs),
            {
                "procedure_key": "turp",
                "procedure_label": "TURP",
                "expected_revision": 0,
                **changes,
            },
            format="json",
        )

    def reserve(self, encounter=None, revision=1):
        return self.client.post(
            reverse("appointments-operation-report-slot", kwargs=self.kwargs),
            {
                "encounter_id": str((encounter or self.encounter).external_id),
                "expected_revision": revision,
            },
            format="json",
        )

    def test_planner_can_plan_but_cannot_reserve_clinical_report(self):
        self.client.force_authenticate(self.planner)
        self.assertEqual(self.plan().status_code, 200)
        self.assertEqual(self.reserve().status_code, 403)
        self.assertIsNone(OperationPlan.objects.get().encounter_id)

    def test_reservation_is_stable_and_does_not_create_forms(self):
        from care.emr.models.questionnaire import FormSubmission

        self.assertEqual(self.plan().status_code, 200)
        before = FormSubmission.objects.count()
        first = self.reserve()
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data, self.reserve().data)
        self.assertEqual(FormSubmission.objects.count(), before)
        self.assertEqual(OperationPlan.objects.count(), 1)

    def test_stale_revision_and_edit_after_start_are_rejected(self):
        self.plan()
        self.assertEqual(self.plan().status_code, 400)
        self.assertEqual(
            self.plan(
                expected_revision=1, procedure_label="TURBT", procedure_key="turbt"
            ).status_code,
            200,
        )
        self.assertEqual(len(OperationPlan.objects.get().revisions), 2)
        self.assertEqual(self.reserve().status_code, 400)
        self.assertEqual(self.reserve(revision=2).status_code, 200)
        self.assertEqual(self.plan(expected_revision=2).status_code, 400)

    def test_foreign_patient_cannot_bind(self):
        self.plan()
        other = self.create_encounter(
            patient=self.create_patient(),
            facility=self.facility,
            organization=self.organization,
            status="in_progress",
        )
        self.assertEqual(self.reserve(other).status_code, 400)

    def test_cannot_rebind(self):
        self.plan()
        self.reserve()
        other = self.create_encounter(
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
            status="in_progress",
        )
        self.assertEqual(self.reserve(other).status_code, 400)

    def test_closed_encounter_cannot_start(self):
        self.plan()
        self.encounter.status = "completed"
        self.encounter.save()
        self.assertEqual(self.reserve().status_code, 403)
        self.assertIsNone(OperationPlan.objects.get().encounter_id)

    def test_cancelled_booking_cannot_plan(self):
        self.booking.status = "cancelled"
        self.booking.save()
        self.assertEqual(self.plan().status_code, 400)

    def test_foreign_user_cannot_read_or_write(self):
        self.client.force_authenticate(self.create_user())
        self.assertEqual(self.plan().status_code, 403)
        self.assertEqual(
            self.client.get(
                reverse("appointments-operation-plan", kwargs=self.kwargs)
            ).status_code,
            403,
        )

    def test_programme_uses_scoped_suriname_day(self):
        self.plan()
        response = self.client.get(
            reverse(
                "appointments-operation-programme",
                kwargs={"facility_external_id": self.facility.external_id},
            ),
            {
                "day": timezone.localdate(timezone=ZoneInfo("America/Paramaribo")),
                "surgeon": self.user.external_id,
            },
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["plan"]["procedure_key"], "turp")

    def test_reserved_uuid_rejects_wrong_form_context(self):
        self.plan()
        self.reserve()
        plan = OperationPlan.objects.get()
        validate_planned_form_identity(
            plan.form_instance_id,
            SimpleNamespace(slug="urology-operaties"),
            self.patient,
            self.encounter,
        )
        with self.assertRaises(ValidationError):
            validate_planned_form_identity(
                plan.form_instance_id,
                SimpleNamespace(slug="urology-medisch-dossier"),
                self.patient,
                self.encounter,
            )

    @override_settings(CLINICAL_WORKFLOW_MUTATIONS_ENABLED_FACILITIES=[])
    def test_kill_switch(self):
        self.assertEqual(self.plan().status_code, 503)
