from contextlib import nullcontext
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from care.security.models import RoleModel
from care.security.permissions.base import PermissionController
from care.security.roles.role import SECRETARY_ROLE, RoleContext, RoleController

EXPECTED_SECRETARY_PERMISSIONS = {
    "can_create_patient",
    "can_write_patient",
    "can_list_patients",
    "can_write_schedule",
    "can_list_schedule",
    "can_list_booking",
    "can_write_booking",
    "can_reschedule_booking",
    "can_view_facility_organization",
    "can_list_facility_organization_users",
    "can_read_facility",
    "can_list_facility_locations",
    "can_read_healthcare_service",
}


def get_declared_secretary_permissions():
    return {
        slug
        for slug, metadata in PermissionController.get_permissions().items()
        if SECRETARY_ROLE in metadata.roles
    }


class SecretaryRolePolicyTest(TestCase):
    def test_role_is_facility_scoped(self):
        self.assertIn(SECRETARY_ROLE, RoleController.get_roles())
        self.assertEqual(SECRETARY_ROLE.contexts, [RoleContext.FACILITY])

    def test_permission_contract_is_exact_and_least_privilege(self):
        self.assertEqual(
            get_declared_secretary_permissions(), EXPECTED_SECRETARY_PERMISSIONS
        )

    @patch(
        "care.security.management.commands.sync_permissions_roles.Lock",
        side_effect=lambda *args, **kwargs: nullcontext(),
    )
    def test_sync_persists_exact_permission_contract(self, _lock):
        call_command("sync_permissions_roles")

        role = RoleModel.objects.get(name="Secretary", is_system=True)
        self.assertEqual(role.contexts, [RoleContext.FACILITY.value])
        self.assertSetEqual(
            set(role.rolepermission_set.values_list("permission__slug", flat=True)),
            EXPECTED_SECRETARY_PERMISSIONS,
        )
