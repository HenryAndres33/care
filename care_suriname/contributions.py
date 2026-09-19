"""Early configuration contributions: no model imports or Django setup required."""

from care_suriname.policies.condition import ClinicalDomainChoices
from care_suriname.policies.preferences import PREFERENCE_SCHEMAS
from care_suriname.policies.settings import SETTING_DEFAULTS


def expand_valueset(valueset, request_params):
    # Models are available when an authenticated expansion request reaches us.
    from care_suriname.policies.terminology import expand

    return expand(valueset, request_params)


def diagnosis_actions():
    from care_suriname.api.viewsets.diagnosis_commands import DiagnosisCommandActions

    return DiagnosisCommandActions


CONTRIBUTIONS = {
    "viewset_actions:diagnosis": diagnosis_actions,
    "valueset_expand": expand_valueset,
    "condition_clinical_domain": ClinicalDomainChoices,
    "preference_schemas": PREFERENCE_SCHEMAS,
    **{f"settings_{profile}": values for profile, values in SETTING_DEFAULTS.items()},
}
