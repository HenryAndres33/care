"""Generic route priority never silently replaces an exact host endpoint."""

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.test import SimpleTestCase
from django.urls import Resolver404, include, path, re_path, resolve, reverse

from plugs.urls import with_priority_routes


def native(request, **kwargs):
    return HttpResponse("native")


def plugin(request, **kwargs):
    return HttpResponse("plugin")


class PriorityRouteTests(SimpleTestCase):
    def test_no_plugin_is_identity_and_keeps_unknown_routes_unknown(self):
        routes = [path("api/v1/patient/<uuid:external_id>/", native)]
        self.assertIs(with_priority_routes(routes, [], prefix="api/v1/"), routes)
        with self.assertRaises(Resolver404):
            resolve("/api/v1/unknown/", urlconf=tuple(routes))

    def test_literal_beats_parameter_but_other_matches_and_reverse_stay_native(self):
        routes = [
            path(
                "api/v1/",
                include(
                    [
                        path(
                            "patient/<str:external_id>/", native, name="native-detail"
                        ),
                        path("patient/search/", native, name="native-search"),
                    ]
                ),
            ),
        ]
        priority = [path("patient/directory/", plugin, name="plugin-directory")]
        result = tuple(with_priority_routes(routes, priority, prefix="api/v1/"))
        self.assertIs(
            resolve("/api/v1/patient/directory/", urlconf=result).func, plugin
        )
        self.assertIs(resolve("/api/v1/patient/uuid/", urlconf=result).func, native)
        self.assertIs(resolve("/api/v1/patient/search/", urlconf=result).func, native)
        self.assertEqual(
            reverse("plugin-directory", urlconf=result), "/api/v1/patient/directory/"
        )
        with self.assertRaises(Resolver404):
            resolve("/api/v1/patient/directory/extra/", urlconf=result)

    def test_duplicate_literal_paths_between_plugins_fail_closed(self):
        with self.assertRaises(ImproperlyConfigured):
            with_priority_routes(
                [], [path("same/", plugin), path("same/", native)], prefix="api/v1/"
            )

    def test_existing_exact_routes_cannot_be_shadowed_even_after_a_parameter_route(
        self,
    ):
        routes = [
            path("api/v1/<str:value>/", native),
            path("api/v1/", include([path("exact/", native)])),
        ]
        with self.assertRaises(ImproperlyConfigured):
            with_priority_routes(routes, [path("exact/", plugin)], prefix="api/v1/")

    def test_parameter_regex_and_nested_priority_patterns_are_rejected(self):
        for pattern in (
            path("<str:value>/", plugin),
            re_path(r"^exact/$", plugin),
            path("nested/", include([path("exact/", plugin)])),
        ):
            with (
                self.subTest(pattern=str(pattern)),
                self.assertRaises(ImproperlyConfigured),
            ):
                with_priority_routes([], [pattern], prefix="api/v1/")

    def test_duplicate_reverse_names_are_rejected(self):
        for routes, priority in (
            (
                [path("other/", native, name="same")],
                [path("new/", plugin, name="same")],
            ),
            (
                [],
                [path("one/", plugin, name="same"), path("two/", plugin, name="same")],
            ),
        ):
            with self.assertRaises(ImproperlyConfigured):
                with_priority_routes(routes, priority, prefix="api/v1/")

    def test_format_aliases_keep_reverse_contract_without_overriding_exact_alias(self):
        priority = [path("patient/directory/", plugin, name="directory")]
        result = tuple(
            with_priority_routes([], priority, prefix="api/v1/", format_suffixes=True)
        )
        self.assertIs(
            resolve("/api/v1/patient/directory.json", urlconf=result).func, plugin
        )
        self.assertEqual(
            reverse("directory", kwargs={"format": "json"}, urlconf=result),
            "/api/v1/patient/directory.json",
        )
        with self.assertRaises(ImproperlyConfigured):
            with_priority_routes(
                [path("api/v1/patient/directory.json", native)],
                priority,
                prefix="api/v1/",
                format_suffixes=True,
            )

    def test_two_plugins_cannot_collide_through_a_generated_format_alias(self):
        with self.assertRaises(ImproperlyConfigured):
            with_priority_routes(
                [],
                [path("directory/", plugin), path("directory.json/", native)],
                prefix="api/v1/",
                format_suffixes=True,
            )
