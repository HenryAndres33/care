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
MAX_VALUE_LENGTH = 20
DEFAULT_SOURCE = "Handmatig ingevoerd via medische notitie"
REGISTER_FIELD = "register_note_labs"
# Specialty content is separate from registration mechanics.
TESTS = {
    "PSA initieel": ("2857-1", "PSA totaal", "ug/L", "µg/L"),
    "PSA actueel": ("2857-1", "PSA totaal", "ug/L", "µg/L"),
    "Testosteron actueel": ("14913-8", "Testosteron", "nmol/L", "nmol/L"),
}


@dataclass(frozen=True)
class NoteLab:
    slot: str
    value: str
    measured: date
    source: str

    @property
    def fingerprint(self):
        return hashlib.sha256(
            json.dumps(
                [self.slot, self.value, self.measured.isoformat(), self.source],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()


def parse_note_labs(text):
    if START not in text and END not in text:
        return []
    lines = [line.strip() for line in text.splitlines()]
    if lines.count(START) != 1 or lines.count(END) != 1:
        raise ValidationError("Gebruik precies één volledig gekoppeld labblok.")
    start, end = lines.index(START), lines.index(END)
    if end <= start:
        raise ValidationError("Het gekoppelde labblok is onvolledig.")
    rows, seen = [], set()
    for line in filter(None, lines[start + 1 : end]):
        slot, separator, result = line.partition(": ")
        if not separator or slot not in TESTS or slot in seen:
            raise ValidationError("Onbekende of dubbele gekoppelde labregel.")
        seen.add(slot)
        if result.lower() in {"onbekend", "niet bepaald"}:
            continue
        unit = TESTS[slot][3]
        match = re.fullmatch(
            rf"([0-9]+(?:[.,][0-9]+)?) {re.escape(unit)}; "
            r"afnamedatum: (\d{4}-\d{2}-\d{2})(?:; bron: (.{1,200}))?",
            result,
        )
        if not match:
            raise ValidationError(
                "Vul waarde, eenheid, afnamedatum (JJJJ-MM-DD) volledig in."
            )
        value, measured, source = match.groups()
        source = DEFAULT_SOURCE if source is None else source
        number = Decimal(value.replace(",", "."))
        if len(value) > MAX_VALUE_LENGTH:
            raise ValidationError("De labwaarde is te lang.")
        try:
            measured_date = date.fromisoformat(measured)
        except ValueError as exc:
            raise ValidationError("De afnamedatum is ongeldig.") from exc
        if measured_date > datetime.now(UTC).date():
            raise ValidationError("De afnamedatum mag niet in de toekomst liggen.")
        if "[[" in source or "***" in source or not source.strip():
            raise ValidationError("Vul de bron van de uitslag in.")
        rows.append(
            NoteLab(
                slot, format(number.normalize(), "f"), measured_date, source.strip()
            )
        )
    baseline = next((row for row in rows if row.slot == "PSA initieel"), None)
    current = next((row for row in rows if row.slot == "PSA actueel"), None)
    if baseline and current and baseline.measured > current.measured:
        raise ValidationError("Initieel PSA mag niet na het actuele PSA liggen.")
    return rows
