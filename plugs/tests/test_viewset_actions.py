"""Additive action composition preserves the host and DRF's native route ordering."""

from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from rest_framework.decorators import action
from rest_framework.routers import SimpleRouter
from rest_framework.viewsets import GenericViewSet

from plugs.viewset_actions import with_contributed_actions


class HostViewSet(GenericViewSet):
    def list(self, request):
        return "native list"

    def retrieve(self, request, pk=None):
        return "native detail"

    def authorize_create(self, instance):
        return "native permission"


class ExtraActions:
    def _command_context(self):
        return self.authorize_create(None)

    @action(detail=False, methods=["post"], url_path="command")
    def command(self, request):
        return self._command_context()


class ViewsetActionContributionTests(SimpleTestCase):
    def compose(self, methods):
        with patch("plugs.viewset_actions.single", return_value=lambda: methods):
            return with_contributed_actions("resource")(HostViewSet)

    def test_absence_returns_same_host_identity(self):
        with patch("plugs.viewset_actions.single", return_value=None):
            self.assertIs(
                with_contributed_actions("resource")(HostViewSet), HostViewSet
            )

    def test_native_methods_and_authorization_are_inherited_not_replaced(self):
        composed = self.compose(ExtraActions)
        self.assertIs(composed.list, HostViewSet.list)
        self.assertIs(composed.retrieve, HostViewSet.retrieve)
        self.assertIs(composed.authorize_create, HostViewSet.authorize_create)
        self.assertEqual(composed().command(None), "native permission")
        self.assertEqual(composed.__name__, HostViewSet.__name__)
        self.assertEqual(composed.__module__, HostViewSet.__module__)

    def test_drf_places_command_before_native_detail(self):
        router = SimpleRouter()
        router.register("resource", self.compose(ExtraActions), basename="resource")
        self.assertEqual(
            [p.name for p in router.urls],
            ["resource-list", "resource-command", "resource-detail"],
        )
        self.assertEqual(router.urls[1].callback.actions, {"post": "command"})

    def test_cannot_override_native_permission_or_dispatch(self):
        for name in (
            "authorize_create",
            "list",
            "initial",
            "dispatch",
            "permission_classes",
        ):
            methods = type("InvalidActions", (), {name: lambda self: None})
            with self.assertRaises(ImproperlyConfigured):
                self.compose(methods)

    def test_only_literal_actions_and_private_helpers(self):
        class PlainPublicMethod:
            def execute(self):
                pass

        class RegexAction:
            @action(detail=False, methods=["post"], url_path=".*")
            def custom(self, request):
                pass

        for methods in (PlainPublicMethod, RegexAction):
            with self.assertRaises(ImproperlyConfigured):
                self.compose(methods)

    def test_duplicate_action_path_or_reverse_name_rejected(self):
        class DuplicateActions:
            @action(detail=False, methods=["post"], url_path="command")
            def first(self, request):
                pass

            @action(detail=False, methods=["get"], url_path="command")
            def second(self, request):
                pass

        class ReservedName:
            @action(detail=False, methods=["post"], url_name="list")
            def custom(self, request):
                pass

        for methods in (DuplicateActions, ReservedName):
            with self.assertRaises(ImproperlyConfigured):
                self.compose(methods)

    def test_multiple_parts_preserve_static_helpers_and_refuse_collisions(self):
        class Helpers:
            @staticmethod
            def _constant():
                return "unchanged"

        composed = self.compose((ExtraActions, Helpers))
        self.assertEqual(composed._constant(), "unchanged")  # noqa: SLF001
        self.assertEqual(composed()._constant(), "unchanged")  # noqa: SLF001
        self.assertEqual(composed().command(None), "native permission")
        self.assertIs(composed.list, HostViewSet.list)
        for parts in ((ExtraActions, ExtraActions), (Helpers, Helpers), (), (object,)):
            with self.assertRaises(ImproperlyConfigured):
                self.compose(parts)

    def test_static_public_methods_and_non_method_descriptors_are_rejected(self):
        class PublicStatic:
            @staticmethod
            def execute():
                return None

        class PropertyHelper:
            @property
            def _value(self):
                return None

        for methods in (PublicStatic, PropertyHelper):
            with self.assertRaises(ImproperlyConfigured):
                self.compose(methods)
