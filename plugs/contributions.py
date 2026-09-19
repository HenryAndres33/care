"""Optional, conflict-checked contributions from installed plug packages.

Contributions must be importable before Django app initialization. Providers must
not import models eagerly. Missing modules are optional; broken providers fail
startup instead of silently disabling validation. No registration order wins.
"""

from copy import deepcopy
from importlib import import_module

from django.core.exceptions import ImproperlyConfigured
from jsonschema.validators import validator_for

from plug_config import manager


def contributions(name):
    for app in manager.get_apps():
        module_name = f"{app}.contributions"
        try:
            module = import_module(module_name)
        except ModuleNotFoundError as error:
            if error.name == module_name:
                continue
            raise
        mapping = getattr(module, "CONTRIBUTIONS", {})
        if not isinstance(mapping, dict):
            message = f"{module_name}: CONTRIBUTIONS must be a dict"
            raise ImproperlyConfigured(message)
        if name in mapping:
            yield mapping[name]


def single(name, default):
    values = list(contributions(name))
    if len(values) > 1:
        message = f"Multiple providers for {name}"
        raise ImproperlyConfigured(message)
    return values[0] if values else default


def merge_mapping(name, defaults=None):
    result = deepcopy(defaults or {})
    for mapping in contributions(name):
        if not isinstance(mapping, dict) or result.keys() & mapping.keys():
            message = f"Invalid or conflicting mapping for {name}"
            raise ImproperlyConfigured(message)
        result.update(deepcopy(mapping))
    if name == "preference_schemas":
        for schema in result.values():
            validator_for(schema).check_schema(schema)
    return result


def apply_settings(namespace, profile, *, env=None):
    """Contribute JSON defaults, or explicit profile overrides after base settings.

    Base contributions cannot replace a host setting. Profile overrides can only
    replace settings contributed by the same mechanism. Environment parsing is
    performed only when requested, preserving a profile's explicit overrides.
    """
    defaults = merge_mapping("settings_base")
    values = defaults if profile == "base" else merge_mapping(f"settings_{profile}")
    if profile == "base" and namespace.keys() & values.keys():
        raise ImproperlyConfigured("Plug settings conflict with host settings")
    if profile != "base" and not values.keys() <= defaults.keys():
        raise ImproperlyConfigured("Profile overrides must target plug settings")
    for key, value in values.items():
        if not isinstance(key, str) or not key.isidentifier() or not key.isupper():
            raise ImproperlyConfigured("Invalid plug setting name")
        namespace[key] = env.json(key, default=value) if env is not None else value
