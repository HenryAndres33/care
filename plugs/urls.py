"""Opt-in literal plug routes may precede native parameter routes, never exact ones."""

import re

from django.core.exceptions import ImproperlyConfigured
from django.urls import URLPattern, include, path, re_path
from django.urls.resolvers import RoutePattern
from rest_framework.urlpatterns import format_suffix_patterns


def _matching_routes(patterns, candidate, *, captured=False):
    for entry in patterns:
        match = entry.pattern.match(candidate)
        if match is None:
            continue
        remaining, args, kwargs = match
        has_parameters = captured or bool(args or kwargs)
        if isinstance(entry, URLPattern):
            yield has_parameters
        else:
            yield from _matching_routes(
                entry.url_patterns, remaining, captured=has_parameters
            )


def _route_names(patterns, namespace=""):
    for entry in patterns:
        if isinstance(entry, URLPattern):
            if entry.name:
                yield namespace + entry.name
        else:
            child_namespace = namespace
            if entry.namespace:
                child_namespace += entry.namespace + ":"
            yield from _route_names(entry.url_patterns, child_namespace)


def _literal_paths(patterns, prefix=""):
    for entry in patterns:
        pattern = entry.pattern
        if isinstance(pattern, RoutePattern):
            if pattern.converters:
                continue
            literal = str(pattern)
        else:
            # DRF's static action regexes use anchors and escaped literal dots.
            literal = (
                str(pattern).removeprefix("^").removesuffix("$").replace(r"\.", ".")
            )
            if not re.fullmatch(r"[\w/.-]*", literal):
                continue
        candidate = prefix + literal
        if isinstance(entry, URLPattern):
            yield candidate
        else:
            yield from _literal_paths(entry.url_patterns, candidate)


def _format_aliases(patterns):
    # Use regex entries like DefaultRouter so reverse(format=...) has no added slash.
    return format_suffix_patterns(
        [
            re_path(
                "^" + re.escape(str(entry.pattern)) + "$",
                entry.callback,
                entry.default_args,
                name=entry.name,
            )
            for entry in patterns
        ]
    )


def with_priority_routes(patterns, priority_patterns, *, prefix, format_suffixes=False):
    """Keep existing order unless a plug explicitly registers a literal path.

    Priority patterns must be flat path() entries without converters. They may
    disambiguate a native parameter route but cannot shadow an exact route or
    reuse an existing reverse name. Invalid registrations fail at URLConf load.
    No registration returns the original list unchanged.
    """
    if not priority_patterns:
        return patterns
    seen_paths = set()
    seen_names = set(_route_names(patterns))
    for entry in priority_patterns:
        if (
            not isinstance(entry, URLPattern)
            or not isinstance(entry.pattern, RoutePattern)
            or entry.pattern.converters
        ):
            raise ImproperlyConfigured(
                "Priority plug routes must be literal path() entries"
            )
        route = str(entry.pattern)
        if not route or route.startswith("/") or route in seen_paths:
            raise ImproperlyConfigured("Invalid or duplicate priority plug path")
        seen_paths.add(route)
        if entry.name and entry.name in seen_names:
            raise ImproperlyConfigured("Duplicate priority plug reverse name")
        if entry.name:
            seen_names.add(entry.name)
        if any(not captured for captured in _matching_routes(patterns, prefix + route)):
            raise ImproperlyConfigured("Priority plug route shadows an exact route")
    resolved_priority = (
        _format_aliases(priority_patterns) if format_suffixes else priority_patterns
    )
    if format_suffixes:
        for index, entry in enumerate(priority_patterns):
            others = priority_patterns[:index] + priority_patterns[index + 1 :]
            if list(_matching_routes(_format_aliases(others), str(entry.pattern))):
                raise ImproperlyConfigured("Duplicate priority plug format alias")
    # DEBUG's native DefaultRouter exposes format aliases too. Preserve these,
    # while checking aliases against every concrete host route, not only /path/.
    wrapped = [path(prefix, include(resolved_priority))]
    for literal in _literal_paths(patterns):
        if list(_matching_routes(wrapped, literal)):
            raise ImproperlyConfigured("Priority plug route shadows an exact route")
    return [*wrapped, *patterns]
