"""Early plug contributions must not depend on Django app-ready registration."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from plugs.contributions import apply_settings, contributions, merge_mapping, single


class ContributionTests(SimpleTestCase):
    def test_absent_provider_keeps_native_default(self):
        with patch("plugs.contributions.manager.get_apps", return_value=[]):
            sentinel = object()
            self.assertIs(single("hook", sentinel), sentinel)
            self.assertEqual(merge_mapping("map", {"native": 1}), {"native": 1})
            settings = {"NATIVE": 1}
            apply_settings(settings, "base")
            self.assertEqual(settings, {"NATIVE": 1})

    def test_missing_optional_module_but_not_broken_provider_is_ignored(self):
        with (
            patch("plugs.contributions.manager.get_apps", return_value=["example"]),
            patch("plugs.contributions.import_module") as importer,
        ):
            importer.side_effect = ModuleNotFoundError(name="example.contributions")
            self.assertEqual(list(contributions("hook")), [])
            importer.side_effect = ModuleNotFoundError(name="dependency")
            with self.assertRaises(ModuleNotFoundError):
                list(contributions("hook"))

    def test_duplicate_hooks_and_mapping_keys_fail_closed(self):
        with (
            patch("plugs.contributions.contributions", return_value=[1, 2]),
            self.assertRaises(ImproperlyConfigured),
        ):
            single("hook", None)
        with (
            patch("plugs.contributions.contributions", return_value=[{"native": 2}]),
            self.assertRaises(ImproperlyConfigured),
        ):
            merge_mapping("map", {"native": 1})

    def test_mapping_is_copied_and_invalid_schema_rejected(self):
        original = {"plug": {"items": []}}
        with patch("plugs.contributions.contributions", return_value=[original]):
            result = merge_mapping("map")
            result["plug"]["items"].append(1)
            self.assertEqual(original["plug"]["items"], [])
        with patch(
            "plugs.contributions.contributions",
            return_value=[{"plug": {"type": "not-a-json-schema-type"}}],
        ):
            from jsonschema.exceptions import SchemaError

            with self.assertRaises(SchemaError):
                merge_mapping("preference_schemas")

    def test_settings_environment_and_profile_precedence(self):
        values = {
            "settings_base": {"PLUGIN_POLICY": {}},
            "settings_local": {"PLUGIN_POLICY": {"local": []}},
        }
        with patch(
            "plugs.contributions.contributions", side_effect=lambda k: [values[k]]
        ):
            namespace = {}
            env = SimpleNamespace(json=Mock(return_value={"environment": []}))
            apply_settings(namespace, "base", env=env)
            self.assertEqual(namespace["PLUGIN_POLICY"], {"environment": []})
            env.json.assert_called_once_with("PLUGIN_POLICY", default={})
            apply_settings(namespace, "local")
            self.assertEqual(namespace["PLUGIN_POLICY"], {"local": []})
            with self.assertRaises(ImproperlyConfigured):
                apply_settings(namespace, "base")

    def test_profile_cannot_override_native_setting(self):
        values = {"settings_base": {}, "settings_local": {"NATIVE": 1}}
        with (
            patch(
                "plugs.contributions.contributions", side_effect=lambda k: [values[k]]
            ),
            self.assertRaises(ImproperlyConfigured),
        ):
            apply_settings({"NATIVE": 0}, "local")
