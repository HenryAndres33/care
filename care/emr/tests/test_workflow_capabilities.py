from http import HTTPStatus
from types import SimpleNamespace
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse

from care.emr.tasks.correspondence_delivery import _prepare_reconciliation_lookup
from care.emr.workflow_capabilities import (
    WorkflowCapabilityDisabled,
    require_correspondence_delivery_enabled,
)
from care.utils.tests.base import CareAPITestBase


class TestWorkflowCapabilities(CareAPITestBase):
    def setUp(self):
        self.user = self.create_user()
        self.facility = self.create_facility(self.user, is_active=True)
        self.organization = self.create_facility_organization(
            self.facility,
            active=True,
        )
        self.role = self.create_role()
        self.attach_role_facility_organization_user(
            self.organization,
            self.user,
            self.role,
        )
        self.client.force_authenticate(self.user)
        self.url = reverse("workflow-capability-list")

    def test_contract_is_facility_scoped_default_off_and_no_store(self):
        with self.settings(
            CLINICAL_WORKFLOW_MUTATIONS_ENABLED_FACILITIES=[],
            CORRESPONDENCE_DELIVERY_ENABLED_FACILITIES=[],
        ):
            response = self.client.get(
                self.url,
                {"facility": str(self.facility.external_id)},
            )

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(
            response.json()["workflow_mutations"],
            {"enabled": False, "blocker_code": "workflow_mutations_disabled"},
        )
        self.assertEqual(
            response.json()["correspondence_delivery"],
            {"enabled": False, "blocker_code": "correspondence_delivery_disabled"},
        )

    def test_contract_enables_only_explicit_facility_and_denies_non_member(self):
        other_facility = self.create_facility(self.user, is_active=True)
        other_organization = self.create_facility_organization(
            other_facility,
            active=True,
        )
        self.attach_role_facility_organization_user(
            other_organization,
            self.user,
            self.role,
        )
        non_member_facility = self.create_facility(self.create_user(), is_active=True)
        with self.settings(
            CLINICAL_WORKFLOW_MUTATIONS_ENABLED_FACILITIES=[
                str(self.facility.external_id)
            ],
            CORRESPONDENCE_DELIVERY_ENABLED_FACILITIES=[str(self.facility.external_id)],
        ):
            enabled = self.client.get(
                self.url,
                {"facility": str(self.facility.external_id)},
            )
            wrong_facility = self.client.get(
                self.url,
                {"facility": str(other_facility.external_id)},
            )
            non_member = self.client.get(
                self.url,
                {"facility": str(non_member_facility.external_id)},
            )

        self.assertEqual(enabled.status_code, HTTPStatus.OK)
        self.assertTrue(enabled.json()["workflow_mutations"]["enabled"])
        self.assertTrue(enabled.json()["correspondence_delivery"]["enabled"])
        self.assertEqual(wrong_facility.status_code, HTTPStatus.OK)
        self.assertFalse(wrong_facility.json()["workflow_mutations"]["enabled"])
        self.assertFalse(wrong_facility.json()["correspondence_delivery"]["enabled"])
        self.assertEqual(non_member.status_code, HTTPStatus.FORBIDDEN)

    @override_settings(
        CLINICAL_WORKFLOW_MUTATIONS_ENABLED_FACILITIES=["*"],
        CORRESPONDENCE_DELIVERY_ENABLED_FACILITIES=[],
    )
    def test_delivery_kill_switch_blocks_before_provider_lookup_or_ledger_event(self):
        facility = SimpleNamespace(external_id=self.facility.external_id)
        delivery = SimpleNamespace(facility=facility)
        attempt = SimpleNamespace(delivery_id=1)
        with (
            patch(
                "care.emr.tasks.correspondence_delivery._locked_attempt",
                return_value=attempt,
            ),
            patch(
                "care.emr.tasks.correspondence_delivery._locked_delivery",
                return_value=delivery,
            ),
            patch(
                "care.emr.tasks.correspondence_delivery.latest_delivery_event"
            ) as latest_event,
        ):
            result = _prepare_reconciliation_lookup(
                "00000000-0000-4000-8000-000000000001",
                automatic=True,
            )

        self.assertIsNone(result)
        latest_event.assert_not_called()
        with self.assertRaises(WorkflowCapabilityDisabled) as exc:
            require_correspondence_delivery_enabled(self.facility.external_id)
        self.assertEqual(exc.exception.detail, "correspondence_delivery_disabled")
