from unittest.mock import patch

from django.contrib.admin.models import LogEntry

from care.emr.models.organization import OrganizationUser
from care.utils.tests.base import CareAPITestBase


class DoctorActivationTests(CareAPITestBase):
    def setUp(self):
        self.admin = self.create_super_user(is_active=True, is_service_account=False)
        self.doctor = self.create_user(
            first_name="DEMO-SIM",
            last_name="Ramsoekh",
            verified=False,
            is_active=True,
            is_service_account=False,
            is_superuser=False,
        )
        self.facility = self.create_facility(user=self.admin, is_active=True)
        self.department = self.create_facility_organization(
            self.facility, active=True, org_type="dept"
        )
        self.role = self.create_role(
            name="Doctor",
            is_system=True,
            contexts=["FACILITY"],
            is_archived=False,
            temp_deleted=False,
        )
        self.appointment = self.attach_role_facility_organization_user(
            self.department, self.doctor, self.role
        )
        self.organization = self.create_organization(
            name="Doctor", org_type="role", active=True
        )
        self.member = self.create_role_with_permissions(
            ["can_read_questionnaire", "can_submit_questionnaire"], role_name="Member"
        )
        self.member.is_system = True
        self.member.contexts = ["ROLE_ORG"]
        self.member.save()
        self.url = f"/api/v1/users/{self.doctor.username}/clinical_activation/"
        self.payload = {"facility": str(self.facility.external_id)}
        self.client.force_authenticate(self.admin)

    def test_activation_is_audited_idempotent_and_does_not_grant_admin(self):
        before = self.client.get(self.url, self.payload)
        self.assertEqual(before.status_code, 200, before.data)
        self.assertEqual(before.data["status"], "missing")
        self.doctor.refresh_from_db()
        self.assertFalse(self.doctor.verified)
        for _ in range(2):
            result = self.client.post(self.url, self.payload)
            self.assertEqual(result.status_code, 200, result.data)
            self.assertEqual(result.data["status"], "configured")
        self.doctor.refresh_from_db()
        self.assertTrue(self.doctor.verified)
        self.assertFalse(self.doctor.is_superuser)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.role_id, self.role.id)
        self.assertEqual(OrganizationUser.objects.filter(user=self.doctor).count(), 1)
        audit = LogEntry.objects.get(object_id=str(self.doctor.pk))
        self.assertEqual(audit.user_id, self.admin.pk)
        self.assertIn("doctor_clinical_activation", audit.change_message)
        self.assertEqual(
            self.client.get(self.url, self.payload).data["status"], "configured"
        )

    def test_ordinary_user_cannot_activate_self_or_read_activation(self):
        self.client.force_authenticate(self.doctor)
        self.assertEqual(self.client.get(self.url, self.payload).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 403)
        self.doctor.refresh_from_db()
        self.assertFalse(self.doctor.verified)

    def test_unrelated_facility_cannot_activate(self):
        other = self.create_facility(user=self.admin)
        result = self.client.post(self.url, {"facility": str(other.external_id)})
        self.assertEqual(result.status_code, 400)
        self.assertFalse(OrganizationUser.objects.filter(user=self.doctor).exists())

    def test_invalid_identity_is_not_activated(self):
        for field, value in [
            ("first_name", " "),
            ("is_active", False),
            ("is_service_account", True),
        ]:
            original = getattr(self.doctor, field)
            setattr(self.doctor, field, value)
            self.doctor.save()
            self.assertEqual(self.client.post(self.url, self.payload).status_code, 400)
            setattr(self.doctor, field, original)
        self.doctor.refresh_from_db()
        self.assertFalse(self.doctor.verified)

    def test_removed_department_or_archived_role_fails_closed(self):
        self.department.active = False
        self.department.save()
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 400)
        self.department.active = True
        self.department.save()
        self.role.is_archived = True
        self.role.save()
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 400)

    def test_alternate_membership_is_not_replaced(self):
        other = self.create_role(name="Alternative")
        OrganizationUser.objects.create(
            user=self.doctor, organization=self.organization, role=other
        )
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 400)
        self.assertEqual(
            OrganizationUser.objects.get(user=self.doctor).role_id, other.id
        )

    def test_ambiguous_role_organizations_fail_closed(self):
        self.create_organization(name="Doctor", org_type="role", active=True)
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 400)

    def test_non_doctor_and_non_system_doctor_cannot_be_activated(self):
        self.role.name = "Secretary"
        self.role.save()
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 400)
        self.role.name = "Doctor"
        self.role.is_system = False
        self.role.save()
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 400)
        self.doctor.refresh_from_db()
        self.assertFalse(self.doctor.verified)

    def test_inactive_administrator_cannot_activate(self):
        self.admin.is_active = False
        self.admin.save()
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 403)
        self.doctor.refresh_from_db()
        self.assertFalse(self.doctor.verified)

    def test_audit_failure_rolls_back_membership_and_verification(self):
        with (
            patch(
                "care_suriname.staff_activation.LogEntry.objects.create",
                side_effect=RuntimeError,
            ),
            self.assertRaises(RuntimeError),
        ):
            self.client.post(self.url, self.payload)
        self.doctor.refresh_from_db()
        self.assertFalse(self.doctor.verified)
        self.assertFalse(OrganizationUser.objects.filter(user=self.doctor).exists())
