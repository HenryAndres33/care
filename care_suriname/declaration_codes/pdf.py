"""The note's declaration codes on its PDF (owner, 30 September 2026).

Only codes chosen in the note and recorded in CARE at "Definitief maken"
(`clinicalActions.staged`, kind `declaration-code`, state `recorded`): they are
part of the finalized note, so the PDF of a version never changes. Codes the
secretary adds on the visit later are not printed on an already finalized
note.
"""

from html import escape
from typing import Any

from care_suriname.declaration_codes.definitions import CODE_SYSTEM_BASE


def _line(item: dict[str, Any]) -> str | None:
    code = item.get("code")
    if not isinstance(code, dict):
        return None
    system, value = code.get("system"), code.get("code")
    if not (
        isinstance(system, str)
        and system.startswith(f"{CODE_SYSTEM_BASE}/")
        and isinstance(value, str)
        and value
    ):
        return None
    display = code.get("display") if isinstance(code.get("display"), str) else ""
    quantity = item.get("quantity")
    times = (
        f" \N{MULTIPLICATION SIGN} {quantity}"
        if isinstance(quantity, int) and quantity > 1
        else ""
    )
    return f"{value} {display}".strip() + times


def render_declaration_codes_section(clinical_actions: Any) -> str:
    staged = (
        clinical_actions.get("staged") if isinstance(clinical_actions, dict) else None
    )
    lines = [
        line
        for item in staged or []
        if isinstance(item, dict)
        and item.get("kind") == "declaration-code"
        and item.get("state") == "recorded"
        and (line := _line(item))
    ]
    if not lines:
        return ""
    items = "".join(f"<li>{escape(line)}</li>" for line in lines)
    return (
        '<section class="note-medication">'
        f"<h3>Declaratiecodes</h3><ul>{items}</ul></section>"
    )
