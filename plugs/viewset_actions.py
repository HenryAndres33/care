"""Add plug-owned DRF actions without replacing native methods or router order."""

import inspect
import re

from django.core.exceptions import ImproperlyConfigured

from plugs.contributions import single


def with_contributed_actions(key):
    """Decorate a host viewset with one optional, lazy action-method provider.

    A provider returns a plain class or tuple of plain classes containing only
    action methods and private helpers (including static helpers). Host attributes
    cannot be overridden. DRF retains responsibility for
    action ordering, nested lookup patterns, dispatch, format suffixes and schema.
    """

    def decorate(host):
        provider = single(f"viewset_actions:{key}", None)
        if provider is None:
            return host
        provided = provider()
        parts = provided if isinstance(provided, tuple) else (provided,)
        if not parts or any(
            not inspect.isclass(part) or part.__bases__ != (object,) for part in parts
        ):
            raise ImproperlyConfigured("Action provider must return plain classes")
        existing = host.get_extra_actions()
        paths = {(method.detail, method.url_path) for method in existing}
        names = {"list", "detail", *(method.url_name for method in existing)}
        additions = {}
        for name, descriptor in (item for part in parts for item in vars(part).items()):
            if name in {
                "__module__",
                "__doc__",
                "__dict__",
                "__weakref__",
                "__qualname__",
                "__firstlineno__",
                "__static_attributes__",
            }:
                continue
            method = (
                descriptor.__func__
                if isinstance(descriptor, staticmethod)
                else descriptor
            )
            if (
                name in additions
                or hasattr(host, name)
                or not inspect.isfunction(method)
            ):
                raise ImproperlyConfigured(
                    "Contributed actions cannot replace host attributes"
                )
            if isinstance(descriptor, staticmethod) and (
                not name.startswith("_") or hasattr(method, "mapping")
            ):
                raise ImproperlyConfigured("Only private helpers may be static")
            if hasattr(method, "mapping"):
                if not re.fullmatch(r"[\w-]+(?:/[\w-]+)*", method.url_path):
                    raise ImproperlyConfigured(
                        "Contributed action paths must be literal"
                    )
                route = (method.detail, method.url_path)
                if route in paths or method.url_name in names:
                    raise ImproperlyConfigured(
                        "Duplicate contributed action path or name"
                    )
                paths.add(route)
                names.add(method.url_name)
            elif not name.startswith("_") or name.startswith("__"):
                raise ImproperlyConfigured(
                    "Only actions and private helpers may be contributed"
                )
            additions[name] = descriptor
        return type(
            host.__name__,
            (host,),
            {
                "__module__": host.__module__,
                "__qualname__": host.__qualname__,
                **additions,
            },
        )

    return decorate
