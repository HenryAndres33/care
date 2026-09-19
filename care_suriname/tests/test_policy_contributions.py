"""Policy ownership and wire contracts independent of clinical record writes."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.conf import settings
from django.test import SimpleTestCase
from pydantic import TypeAdapter, ValidationError

from care.emr.models.condition import Condition
from care.emr.resources.condition.spec import ConditionSpec, ConditionUpdateSpec
from care_suriname.contributions import CONTRIBUTIONS
from care_suriname.policies.condition import ClinicalDomainChoices
from care_suriname.policies.preferences import PREFERENCE_SCHEMAS
from care_suriname.policies.terminology import expand


class PolicyContributionTests(SimpleTestCase):
    def test_api_enum_and_default_remain_identical(self):
        for spec in (ConditionSpec, ConditionUpdateSpec):
            field = spec.model_fields["clinical_domain"]
            self.assertIs(field.annotation, ClinicalDomainChoices)
            self.assertIs(field.default, ClinicalDomainChoices.general)
            adapter = TypeAdapter(field.annotation)
            self.assertEqual(adapter.json_schema()["enum"], ["general", "urology"])
            self.assertEqual(adapter.validate_python("urology"), "urology")
            with self.assertRaises(ValidationError):
                adapter.validate_python("unknown")
        field = Condition._meta.get_field("clinical_domain")  # noqa: SLF001
        self.assertEqual(field.default, "general")
        self.assertIsNone(field.choices)
        self.assertEqual(field.max_length, 64)

    def test_preferences_registered_before_app_ready(self):
        self.assertEqual(
            settings.PREFERENCE_SCHEMA["urology_recent_patients"],
            PREFERENCE_SCHEMAS["urology_recent_patients"],
        )
        schema = PREFERENCE_SCHEMAS["urology_recent_patients"]
        facilities = schema["properties"]["facilities"]
        self.assertEqual(facilities["maxProperties"], 50)
        self.assertEqual(facilities["additionalProperties"]["maxItems"], 8)
        self.assertIs(CONTRIBUTIONS["condition_clinical_domain"], ClinicalDomainChoices)

    def test_non_dutch_expansion_preserves_native_duplicates_and_language(self):
        concept = Mock()
        concept.model_dump.return_value = {"system": "s", "code": "1"}
        valueset = SimpleNamespace(search=Mock(return_value=[concept, concept]))
        params = {"search": "query", "count": 2, "display_language": "en-gb"}
        self.assertEqual(
            expand(valueset, params), [concept.model_dump(), concept.model_dump()]
        )
        valueset.search.assert_called_once_with(**params)

    def test_dutch_translation_precedes_native_deduplicates_and_truncates(self):
        native = Mock()
        native.model_dump.return_value = {"system": "s", "code": "1"}
        valueset = SimpleNamespace(
            slug="system-condition-code", search=Mock(return_value=[native])
        )
        translated = {"system": "s", "code": "1", "display": "Nederlands"}
        with (
            patch(
                "care_suriname.policies.terminology.search_approved_translation_concepts",
                return_value=[translated],
            ) as search,
            patch(
                "care_suriname.policies.terminology.resolve_concepts",
                return_value=[native.model_dump(), {"system": "s", "code": "2"}],
            ),
        ):
            result = expand(
                valueset, {"search": "query", "count": 1, "display_language": "NL-sR"}
            )
        self.assertEqual(result, [translated])
        valueset.search.assert_called_once_with(
            search="query", count=1, display_language="en-gb"
        )
        search.assert_called_once_with("query", "nl-SR", 1, "condition")

    def test_native_expansion_without_plugin_keeps_original_search_contract(self):
        from care.emr.api.viewsets.valueset import ValueSetViewSet

        concept = Mock()
        concept.model_dump.return_value = {"system": "s", "code": "1"}
        valueset = SimpleNamespace(search=Mock(return_value=[concept]))
        view = ValueSetViewSet()
        view.get_object = Mock(return_value=valueset)
        with patch("plugs.contributions.manager.get_apps", return_value=[]):
            response = view.expand(SimpleNamespace(data={"search": "x"}))
        self.assertEqual(response.data, {"results": [concept.model_dump()]})
        valueset.search.assert_called_once_with(
            search="x", count=10, display_language="en-gb"
        )

    def test_policy_literals_do_not_return_to_native_files(self):
        root = Path(__file__).resolve().parents[2]
        paths = [
            "care/emr/api/viewsets/valueset.py",
            "care/emr/resources/condition/spec.py",
            "config/settings/base.py",
            "config/settings/config.py",
            "config/settings/local.py",
            "config/settings/test.py",
        ]
        forbidden = {
            "urology",
            "urology_recent_patients",
            "urology-medisch-dossier",
            "nl-sr",
            "nl-SR",
        }
        for path in paths:
            constants = {
                node.value
                for node in ast.walk(ast.parse((root / path).read_text()))
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            }
            self.assertFalse(constants & forbidden, path)
