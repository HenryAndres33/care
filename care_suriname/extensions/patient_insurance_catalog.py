"""Suriname health insurers and their plans (content, not mechanics).

Owner-provided list, 30 September 2026. Each group is one choice under
"Verzekering"; a group with plans asks for a plan and the insurance number.
`plan_field` is the stored key and must never change once a patient holds it.
To add a plan, append it to its group; to add an insurer, add a group with a
new, unused `plan_field`.
"""

from typing import NamedTuple


class InsurerGroup(NamedTuple):
    name: str
    plan_field: str | None
    plans: tuple[str, ...]


INSURER_GROUPS: tuple[InsurerGroup, ...] = (
    InsurerGroup(
        name="SZF",
        plan_field="plan_szf",
        plans=("SZF", "SZF premium", "SZF Bazo", "SZF BZV"),
    ),
    InsurerGroup(
        name="SURVAM",
        plan_field="plan_survam",
        plans=(
            "PZS-basis",
            "PZS-comfort",
            "PZX-X-comfort",
            "AZPAS-basis",
            "AZPAS-plus",
            "AZPAS-Suprême",
            "PARSASCO",
        ),
    ),
    InsurerGroup(name="Eigen rekening", plan_field=None, plans=()),
)
