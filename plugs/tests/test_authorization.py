"""Organization contributions cannot bypass native role/permission decisions."""

from unittest.mock import Mock, patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from plugs.authorization import patient_organization_ids


class PatientOrganizationContributionTests(SimpleTestCase):
    def test_absence_is_empty_and_does_not_inspect_patient(self):
        with patch("plugs.contributions.manager.get_apps", return_value=[]):
            self.assertEqual(patient_organization_ids(object(), object()), set())

    def test_exact_arguments_and_set_copy(self):
        user, patient = object(), object()
        ids = {1, 2}
        provider = Mock(return_value=ids)
        with patch("plugs.contributions.contributions", return_value=[provider]):
            result = patient_organization_ids(user, patient)
        provider.assert_called_once_with(user, patient)
        self.assertEqual(result, ids)
        result.add(3)
        self.assertEqual(ids, {1, 2})

    def test_duplicate_providers_fail_before_execution_in_either_order(self):
        first, second = Mock(), Mock()
        for providers in ([first, second], [second, first]):
            with (
                patch("plugs.contributions.contributions", return_value=providers),
                self.assertRaises(ImproperlyConfigured),
            ):
                patient_organization_ids(None, None)
        first.assert_not_called()
        second.assert_not_called()

    def test_invalid_provider_or_result_never_becomes_an_allow(self):
        for provider in (
            None,
            True,
            {},
            Mock(return_value=True),
            Mock(return_value=None),
        ):
            with (
                patch("plugs.contributions.contributions", return_value=[provider]),
                self.assertRaises(ImproperlyConfigured),
            ):
                patient_organization_ids(None, None)
        for result in ([1], {True}, {0}, {-1}, {"1"}, {1, None}):
            with (
                patch(
                    "plugs.contributions.contributions",
                    return_value=[Mock(return_value=result)],
                ),
                self.assertRaises(ImproperlyConfigured),
            ):
                patient_organization_ids(None, None)

    def test_provider_errors_abort_without_fallback(self):
        with (
            patch(
                "plugs.contributions.contributions",
                return_value=[Mock(side_effect=RuntimeError("unavailable"))],
            ),
            self.assertRaisesRegex(RuntimeError, "unavailable"),
        ):
            patient_organization_ids(None, None)
