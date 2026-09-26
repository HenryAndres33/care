from datetime import date, datetime

from django.utils import timezone

_DUTCH_MONTHS = (
    "januari",
    "februari",
    "maart",
    "april",
    "mei",
    "juni",
    "juli",
    "augustus",
    "september",
    "oktober",
    "november",
    "december",
)


def local_calendar_date(value) -> date | None:
    """The calendar day of a date, datetime or ISO string in the clinic's zone.

    Timestamps are stored in UTC; after 21:00 in Paramaribo (UTC-3) the UTC
    day is already tomorrow, so aware values are converted to TIME_ZONE first.
    Naive values and plain dates are taken as written.
    """
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip())
        except ValueError:
            try:
                return date.fromisoformat(value.strip()[:10])
            except ValueError:
                return None
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.date()
    return value if isinstance(value, date) else None


def dutch_correspondence_date(value: str) -> str:
    parsed = local_calendar_date(value)
    if parsed is None:
        return value
    return f"{parsed.day} {_DUTCH_MONTHS[parsed.month - 1]} {parsed.year}"


def correspondence_presentation_reason(response_dump, *, fallback: str) -> str:
    """Prefer the exact finalized note reason over a broad encounter tag."""

    if isinstance(response_dump, dict):
        content = response_dump.get("content")
        if isinstance(content, dict):
            values = content.get("values")
            if isinstance(values, dict):
                reason = values.get("reasonForVisit")
                if isinstance(reason, str) and reason.strip():
                    return reason.strip()
    return fallback
