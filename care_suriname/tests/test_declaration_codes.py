"""Contract: declaration code lists become native charge item definitions.

Owner decisions 30 Sep 2026: SZF / Assuria / AZP PO codes, codes only (no
prices, no invoicing); recorded as native ChargeItems on the visit.
"""

import re
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from care.emr.models.charge_item import ChargeItem
from care.emr.models.charge_item_definition import ChargeItemDefinition
from care.utils.tests.base import CareAPITestBase
from care_suriname.declaration_codes import catalog
from care_suriname.declaration_codes.definitions import (
    CODE_SYSTEM_BASE,
    planned_definitions,
)
from care_suriname.reports.form_submission_clinical_content import (
    render_clinical_content,
)

SLUG = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*[a-zA-Z0-9]$")
TOTAL = sum(len(code_list.codes) for code_list in catalog.CODE_LISTS)


class DeclarationCodeCatalogTests(CareAPITestBase):
    def test_codes_are_unique_and_slugs_valid_for_native_care(self):
        self.assertEqual(TOTAL, 56 + 17 + 28)
        for code_list in catalog.CODE_LISTS:
            codes = [code for code, _ in code_list.codes]
            self.assertEqual(len(codes), len(set(codes)), code_list.key)
        slugs = [item.slug_value for item in planned_definitions()]
        self.assertEqual(len(slugs), len(set(slugs)))
        for slug in slugs:
            self.assertTrue(5 <= len(slug) <= 50 and SLUG.match(slug), slug)  # noqa: PLR2004


class ConfigureDeclarationCodesTests(CareAPITestBase):
    def setUp(self):
        self.admin = self.create_super_user(username="configurator")
        self.facility = self.create_facility(user=self.admin)

    def run_command(self, *extra):
        out = StringIO()
        call_command(
            "configure_declaration_codes",
            "--facility",
            str(self.facility.external_id),
            "--user",
            "configurator",
            *extra,
            stdout=out,
        )
        return out.getvalue()

    def ours(self):
        return ChargeItemDefinition.objects.filter(
            facility=self.facility, derived_from_uri__startswith=CODE_SYSTEM_BASE
        )

    def test_dry_run_writes_nothing(self):
        output = self.run_command()
        self.assertIn(f"dry-run: facility {self.facility.name}: create {TOTAL}", output)
        self.assertEqual(self.ours().count(), 0)

    def test_apply_creates_price_free_definitions_once(self):
        self.run_command("--apply")
        self.assertEqual(self.ours().count(), TOTAL)
        cysto = self.ours().get(derived_from_uri=f"{CODE_SYSTEM_BASE}/szf/217031")
        self.assertEqual(cysto.title, "217031 Cystoscopie")
        self.assertEqual(cysto.slug, f"f-{self.facility.external_id}-szf-217031")
        self.assertEqual(cysto.status, "active")
        self.assertEqual(cysto.price_components, [])
        self.assertIn(
            f"create 0, update 0, unchanged {TOTAL}, retire 0",
            self.run_command("--apply"),
        )

    def test_removed_code_is_retired_not_deleted(self):
        self.run_command("--apply")
        fewer = catalog.CodeList("szf", "SZF", catalog.SZF.codes[1:])
        with patch(
            "care_suriname.declaration_codes.definitions.CODE_LISTS",
            (fewer, catalog.ASSURIA, catalog.AZP_PO),
        ):
            output = self.run_command("--apply")
        self.assertIn("retire 1", output)
        consult = self.ours().get(derived_from_uri=f"{CODE_SYSTEM_BASE}/szf/201000")
        self.assertEqual(consult.status, "retired")
        self.assertEqual(self.ours().count(), TOTAL)

    def test_hand_made_definition_with_our_slug_is_never_overwritten(self):
        ChargeItemDefinition.objects.create(
            facility=self.facility,
            status="active",
            title="Eigen",
            slug=f"f-{self.facility.external_id}-szf-217031",
            price_components=[],
        )
        with self.assertRaises(CommandError):
            self.run_command("--apply")
        self.assertEqual(self.ours().count(), 0)


class NativeChargeItemContractTests(CareAPITestBase):
    """The frontend records a code as a native ChargeItem without a price."""

    def setUp(self):
        self.user = self.create_super_user()
        self.facility = self.create_facility(user=self.user)
        self.organization = self.create_facility_organization(facility=self.facility)
        self.patient = self.create_patient()
        self.encounter = self.create_encounter(
            self.patient, self.facility, self.organization
        )
        self.client.force_authenticate(user=self.user)

    def test_code_only_charge_item_on_the_visit(self):
        url = reverse(
            "charge_item-list",
            kwargs={"facility_external_id": self.facility.external_id},
        )
        response = self.client.post(
            url,
            {
                "title": "217031 Cystoscopie",
                "status": "billable",
                "quantity": "1",
                "unit_price_components": [],
                "encounter": str(self.encounter.external_id),
                "code": {
                    "system": f"{CODE_SYSTEM_BASE}/szf",
                    "code": "217031",
                    "display": "Cystoscopie",
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        item = ChargeItem.objects.get(external_id=response.json()["id"])
        self.assertEqual(item.code["code"], "217031")
        self.assertEqual(item.encounter_id, self.encounter.id)
        self.assertEqual(item.patient_id, self.patient.id)
        self.assertEqual(float(item.total_price or 0), 0.0)


class DeclarationCodeCorrectionTests(CareAPITestBase):
    """Same-day correction by whoever recorded the code (owner, 30 Sep 2026)."""

    def setUp(self):
        self.admin = self.create_super_user()
        self.facility = self.create_facility(user=self.admin)
        self.organization = self.create_facility_organization(facility=self.facility)
        role = self.create_role_with_permissions(
            ["can_create_charge_item", "can_read_charge_item"]
        )
        self.recorder = self.create_user()
        self.colleague = self.create_user()
        for user in (self.recorder, self.colleague):
            self.attach_role_facility_organization_user(self.organization, user, role)
        self.patient = self.create_patient()
        self.encounter = self.create_encounter(
            self.patient, self.facility, self.organization
        )

    def record(self, code_system=f"{CODE_SYSTEM_BASE}/szf"):
        self.client.force_authenticate(user=self.recorder)
        response = self.client.post(
            reverse(
                "charge_item-list",
                kwargs={"facility_external_id": self.facility.external_id},
            ),
            {
                "title": "217031 Cystoscopie",
                "status": "billable",
                "quantity": "1",
                "unit_price_components": [],
                "encounter": str(self.encounter.external_id),
                "code": {"system": code_system, "code": "217031"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["id"]

    def correct(self, user, charge_item_id):
        self.client.force_authenticate(user=user)
        return self.client.post(
            f"/api/care_suriname/declaration-codes/charge-items/{charge_item_id}/enter-in-error/",
            {},
            format="json",
        )

    def test_recorder_corrects_on_the_same_day_and_history_stays(self):
        item_id = self.record()
        response = self.correct(self.recorder, item_id)
        self.assertEqual(response.status_code, 200, response.content)
        item = ChargeItem.objects.get(external_id=item_id)
        self.assertEqual(item.status, "entered_in_error")
        self.assertTrue(self.correct(self.recorder, item_id).json()["replayed"])

    def test_someone_else_cannot(self):
        item_id = self.record()
        self.assertEqual(self.correct(self.colleague, item_id).status_code, 403)
        self.assertEqual(ChargeItem.objects.get(external_id=item_id).status, "billable")

    def test_not_the_next_day(self):
        item_id = self.record()
        ChargeItem.objects.filter(external_id=item_id).update(
            created_date=timezone.now() - timedelta(days=1)
        )
        self.assertEqual(self.correct(self.recorder, item_id).status_code, 403)

    def test_only_declaration_codes(self):
        item_id = self.record(code_system="http://example.org/other")
        self.assertEqual(self.correct(self.recorder, item_id).status_code, 403)


class DeclarationCodesPdfTests(SimpleTestCase):
    def dump(self, staged):
        return {
            "content": {
                "noteText": "Cystoscopie verricht.",
                "clinicalActions": {
                    "schema": "care.urology.form-clinical-actions",
                    "version": 1,
                    "actions": [],
                    "staged": staged,
                },
            }
        }

    def staged(self, state="recorded", system=f"{CODE_SYSTEM_BASE}/szf", **extra):
        return {
            "kind": "declaration-code",
            "slot": "declaration-code:szf:217031",
            "state": state,
            "quantity": 2,
            "code": {"system": system, "code": "217031", "display": "Cystoscopie"},
            **extra,
        }

    def test_recorded_note_codes_print(self):
        html = render_clinical_content(self.dump([self.staged()]))
        self.assertIn("<h3>Declaratiecodes</h3>", html)
        self.assertIn("<li>217031 Cystoscopie \N{MULTIPLICATION SIGN} 2</li>", html)

    def test_pending_or_foreign_codes_do_not(self):
        for staged in (
            [self.staged(state="pending")],
            [self.staged(system="https://elders.org/x")],
            [],
        ):
            self.assertNotIn(
                "Declaratiecodes", render_clinical_content(self.dump(staged))
            )
