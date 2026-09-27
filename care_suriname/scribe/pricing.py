"""Estimated Vertex AI cost per call, for the usage log only (never billing truth).

Prices in USD per million tokens, global endpoint, standard tier, from
https://cloud.google.com/vertex-ai/generative-ai/pricing (read 26 September 2026).
Gemini 3.8 Flash has an introductory price until 31 December 2026.
"""

from datetime import date

from django.utils import timezone

INTRO_PRICE_ENDS = date(2026, 12, 31)

# model: (input incl. audio, output incl. thinking)
PRICES = {
    "gemini-3.8-flash": (1.50, 7.50),
    "gemini-3.5-flash-lite": (0.30, 2.50),
}
INTRO_PRICES = {"gemini-3.8-flash": (0.75, 3.75)}


def estimate_cost_usd(
    model: str, input_tokens: int, output_tokens: int, today: date | None = None
) -> float | None:
    today = today or timezone.localdate()
    prices = (INTRO_PRICES if today <= INTRO_PRICE_ENDS else {}).get(
        model, PRICES.get(model)
    )
    if prices is None:
        return None
    return round((input_tokens * prices[0] + output_tokens * prices[1]) / 1e6, 5)
