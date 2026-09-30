from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError

from care.emr.models.organization import Organization
from care.utils.tests.base import CareAPITestBase
from care_suriname.resources.suriname_districts import DISTRICTS


def run(*args):
    output = StringIO()
    call_command("provision_suriname_districts", *args, stdout=output)
    return output.getvalue()


class SurinameDistrictTests(CareAPITestBase):
    def country(self):
        return Organization.objects.create(
            org_type="govt",
            name="Suriname",
            metadata={"govt_org_type": "country", "govt_org_children_type": "district"},
        )

    def districts(self, country):
        return Organization.objects.filter(parent=country, deleted=False)

    def test_dry_run_writes_nothing(self):
        country = self.country()
        self.assertIn("dry-run", run())
        self.assertFalse(self.districts(country).exists())

    def test_apply_adds_only_missing_districts_and_is_idempotent(self):
        country = self.country()
        existing = Organization.objects.create(
            org_type="govt",
            name="Paramaribo",
            parent=country,
            metadata={"govt_org_type": "district"},
        )
        self.assertIn("created: 9", run("--apply"))
        self.assertIn("nothing to do", run("--apply"))
        districts = self.districts(country)
        self.assertEqual(
            sorted(districts.values_list("name", flat=True)), sorted(DISTRICTS)
        )
        self.assertEqual(districts.get(name="Paramaribo").id, existing.id)
        wanica = districts.get(name="Wanica")
        self.assertEqual(wanica.metadata, {"govt_org_type": "district"})
        self.assertEqual(wanica.level_cache, 1)
        self.assertEqual(wanica.root_org_id, country.id)

    def test_stops_without_exactly_one_country(self):
        with self.assertRaises(CommandError):
            run("--apply")
