"""Opt-in, strictly structured note rows; never infer a result from prose."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from rest_framework.exceptions import ValidationError

START = "Gekoppeld laboratorium:"
END = "Einde gekoppeld laboratorium."
VISIBLE_START = "Labuitslagen:"
MAX_VALUE_LENGTH = 20
DEFAULT_SOURCE = "Handmatig ingevoerd via medische notitie"
REGISTER_FIELD = "register_note_labs"
# Specialty content is separate from registration mechanics.
TESTS = {
    "PSA initieel": ("2857-1", "PSA totaal", "ug/L", "µg/L"),
    "PSA actueel": ("2857-1", "PSA totaal", "ug/L", "µg/L"),
    "Testosteron actueel": ("14913-8", "Testosteron", "nmol/L", "nmol/L"),
    "Creatinine": ("14682-9", "Creatinine", "umol/L", "µmol/L"),
    "Ureum": ("22664-7", "Ureum", "mmol/L", "mmol/L"),
    "Hemoglobine": ("718-7", "Hemoglobine", "g/dL", "g/dL"),
    "CRP": ("1988-5", "CRP", "mg/L", "mg/L"),
    "D-dimeer FEU": ("48065-7", "D-dimeer FEU", "mg{FEU}/L", "mg/L FEU"),
    "Glucose": ("14749-6", "Glucose", "mmol/L", "mmol/L"),
    "Natrium": ("2951-2", "Natrium", "mmol/L", "mmol/L"),
    "Kalium": ("2823-3", "Kalium", "mmol/L", "mmol/L"),
}


@dataclass(frozen=True)
class NoteLab:
    slot: str
    value: str
    measured: date | None
    source: str

    @property
    def fingerprint(self):
        return hashlib.sha256(
            json.dumps(
                [
                    self.slot,
                    self.value,
                    self.measured.isoformat() if self.measured else None,
                    self.source,
                ],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()


def parse_note_labs(text):
    lines = [line.strip() for line in text.splitlines()]
    visible_starts = [
        index
        for index, line in enumerate(lines)
        if line == VISIBLE_START or line.startswith(f"{VISIBLE_START} ")
    ]
    technical = START in text or END in text
    if not technical and not visible_starts:
        return []
    legacy_heading = (
        len(visible_starts) == 1
        and START in lines
        and visible_starts[0] < lines.index(START)
    )
    if len(visible_starts) > 1 or (visible_starts and technical and not legacy_heading):
        raise ValidationError("Gebruik precies één gekoppeld labblok.")
    per_row_dates, bounded, measured, block_lines = _extract_block(
        lines, [] if legacy_heading else visible_starts
    )
    rows, seen = [], set()
    for line in block_lines:
        slot, separator, result = line.partition(": ")
        if not bounded and (not separator or slot not in TESTS):
            if any(line.startswith(f"{name}:") for name in TESTS):
                raise ValidationError("Vul de labwaarde en eenheid volledig in.")
            break
        if not separator or slot not in TESTS or slot in seen:
            raise ValidationError("Onbekende of dubbele gekoppelde labregel.")
        seen.add(slot)
        row = _parse_row(slot, result, per_row_dates=per_row_dates, measured=measured)
        if row:
            rows.append(row)
    if not bounded and not seen:
        raise ValidationError("Kies ten minste één gekoppelde labwaarde.")
    baseline = next((row for row in rows if row.slot == "PSA initieel"), None)
    current = next((row for row in rows if row.slot == "PSA actueel"), None)
    if (
        baseline
        and current
        and baseline.measured
        and current.measured
        and baseline.measured > current.measured
    ):
        raise ValidationError("Initieel PSA mag niet na het actuele PSA liggen.")
    return rows


def _extract_block(lines, visible_starts):
    visible = bool(visible_starts)
    if not visible and (lines.count(START) != 1 or lines.count(END) > 1):
        raise ValidationError("Gebruik precies één gekoppeld labblok.")
    start = visible_starts[0] if visible else lines.index(START)
    bounded = not visible and END in lines
    end = lines.index(END) if bounded else len(lines)
    if bounded and end <= start:
        raise ValidationError("Het gekoppelde labblok is onvolledig.")
    block_lines = list(filter(None, lines[start + 1 : end]))
    measured = None
    grouped = bool(block_lines and block_lines[0].startswith("Afnamedatum: "))
    per_row_dates = bounded or (visible and not grouped)
    if not per_row_dates:
        if not block_lines or not block_lines[0].startswith("Afnamedatum: "):
            raise ValidationError("Vul één afnamedatum voor het gekoppelde labblok in.")
        measured = _parse_date(block_lines.pop(0).removeprefix("Afnamedatum: "))
    return per_row_dates, bounded, measured, block_lines


def _parse_row(slot, result, *, per_row_dates, measured):
    if result.lower() in {"onbekend", "niet bepaald"}:
        return None
    unit = TESTS[slot][3]
    if per_row_dates:
        match = re.fullmatch(
            rf"([0-9]+(?:[.,][0-9]+)?) {re.escape(unit)}; "
            r"afnamedatum: (\d{4}-\d{2}-\d{2})(?:; bron: (.{1,200}))?",
            result,
        )
    else:
        match = re.fullmatch(
            rf"([0-9]+(?:[.,][0-9]+)?) {re.escape(unit)}"
            r"(?:; bron: (.{1,200}))?",
            result,
        )
    if not match:
        raise ValidationError("Vul de labwaarde en eenheid volledig in.")
    if per_row_dates:
        value, row_date, source = match.groups()
        measured = _parse_date(row_date, allow_unknown=False)
    else:
        value, source = match.groups()
    source = DEFAULT_SOURCE if source is None else source
    number = Decimal(value.replace(",", "."))
    if len(value) > MAX_VALUE_LENGTH:
        raise ValidationError("De labwaarde is te lang.")
    if "[[" in source or "***" in source or not source.strip():
        raise ValidationError("Vul de bron van de uitslag in.")
    return NoteLab(slot, format(number.normalize(), "f"), measured, source.strip())


def _parse_date(value, *, allow_unknown=True):
    if allow_unknown and value.lower() == "onbekend":
        return None
    try:
        measured = date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError("De afnamedatum is ongeldig.") from exc
    if measured > datetime.now(UTC).date():
        raise ValidationError("De afnamedatum mag niet in de toekomst liggen.")
    return measured
