"""Optional organization scope; native membership and permission checks remain final."""

from django.core.exceptions import ImproperlyConfigured

from plugs.contributions import single

_MISSING = object()


def patient_organization_ids(user, patient):
    """Add candidate organization IDs, never an allow/deny decision.

    Exactly one provider is permitted; no registration order wins. Absence adds
    nothing. Invalid providers/results and exceptions abort the lookup rather
    than falling back to a potentially partial authorization result.
    """
    provider = single("patient_organization_ids", _MISSING)
    if provider is _MISSING:
        return set()
    if not callable(provider):
        raise ImproperlyConfigured("Patient organization provider must be callable")
    result = provider(user, patient)
    if not isinstance(result, set) or any(
        type(pk) is not int or pk <= 0 for pk in result
    ):
        raise ImproperlyConfigured(
            "Patient organization provider must return positive integer IDs"
        )
    return result.copy()
