from datetime import date

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


def dutch_correspondence_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value[:10])
    except (TypeError, ValueError):
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
