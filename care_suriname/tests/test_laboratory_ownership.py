from pathlib import Path
from unittest import TestCase

from django.urls import reverse


class LaboratoryOwnershipTests(TestCase):
    def test_routes_remain_under_plugin_namespace(self):
        self.assertEqual(
            reverse("laboratory-definition-list"),
            "/api/care_suriname/laboratory/definitions/",
        )
        self.assertEqual(
            reverse("laboratory-report-command"),
            "/api/care_suriname/laboratory/report-commands/",
        )
        self.assertEqual(
            reverse("laboratory-report-list"),
            "/api/care_suriname/laboratory/reports/",
        )

    def test_implementation_is_plugin_owned_and_has_no_custom_persistence(self):
        root = Path(__file__).resolve().parents[1]
        package = root / "resources" / "laboratory_commands"
        self.assertTrue(package.is_dir())
        source = "\n".join(path.read_text() for path in package.glob("*.py"))
        self.assertNotIn("class LaboratoryReport(models.Model)", source)
        self.assertNotIn("api/v1/", source)
        self.assertFalse(
            list((root / "migrations").glob("*laboratory*")),
            "Laboratory commands must reuse native resources without a plugin table.",
        )
