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

    def test_without_country_it_stops_unless_asked_to_create_it(self):
        with self.assertRaises(CommandError):
            run("--apply")
        self.assertIn("country 'Suriname' to create", run("--create-country"))
        self.assertFalse(Organization.objects.filter(name="Suriname").exists())
        self.assertIn("created country", run("--apply", "--create-country"))
        country = Organization.objects.get(name="Suriname", parent__isnull=True)
        self.assertEqual(country.metadata["govt_org_type"], "country")
        self.assertEqual(self.districts(country).count(), len(DISTRICTS))
        self.assertIn("nothing to do", run("--apply", "--create-country"))

    def test_existing_unmarked_suriname_is_not_changed(self):
        unmarked = Organization.objects.create(org_type="govt", name="Suriname")
        with self.assertRaises(CommandError):
            run("--apply", "--create-country")
        unmarked.refresh_from_db()
        self.assertEqual(unmarked.metadata, {})
        self.assertFalse(self.districts(unmarked).exists())

    def test_mark_country_types_existing_rows_and_adds_the_rest(self):
        unmarked = Organization.objects.create(org_type="govt", name="Suriname")
        paramaribo = Organization.objects.create(
            org_type="govt", name="Paramaribo", parent=unmarked
        )
        other = Organization.objects.create(
            org_type="govt", name="Elders", parent=unmarked
        )
        self.assertIn("to mark", run("--mark-country"))
        unmarked.refresh_from_db()
        self.assertEqual(unmarked.metadata, {})
        output = run("--apply", "--mark-country")
        self.assertIn("marked: country + 1 district(s)", output)
        unmarked.refresh_from_db()
        paramaribo.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(unmarked.metadata["govt_org_type"], "country")
        self.assertEqual(unmarked.metadata["govt_org_children_type"], "district")
        self.assertEqual(paramaribo.metadata, {"govt_org_type": "district"})
        self.assertEqual(other.metadata, {})
        names = set(self.districts(unmarked).values_list("name", flat=True))
        self.assertEqual(names, {*DISTRICTS, "Elders"})
        self.assertEqual(
            Organization.objects.filter(parent=unmarked, name="Paramaribo").count(), 1
        )
        self.assertIn("nothing to do", run("--apply", "--mark-country"))

    def test_mark_country_refuses_a_suriname_of_another_type(self):
        Organization.objects.create(
            org_type="govt", name="Suriname", metadata={"govt_org_type": "state"}
        )
        with self.assertRaises(CommandError):
            run("--apply", "--mark-country")

    def test_dry_run_lists_the_top_level(self):
        self.country()
        Organization.objects.create(org_type="govt", name="Elders")
        output = run()
        self.assertIn("'Elders' type=- children=0", output)
        self.assertIn("'Suriname' type=country children=0", output)
