"""Contract: letters show the clinic's calendar day, not the UTC day.

Paramaribo is UTC-3; a letter finalized at 22:00 local time was dated the
next day because stored UTC timestamps were formatted without conversion.
"""

from datetime import UTC, date, datetime

from django.test import SimpleTestCase, override_settings

from care_suriname.correspondence.presentation import (
    dutch_correspondence_date,
    local_calendar_date,
)
from care_suriname.reports.correspondence_letter import _display_date

LATE_EVENING_UTC = datetime(2026, 9, 27, 1, 30, tzinfo=UTC)  # 26 Sep 22:30 SRT


@override_settings(TIME_ZONE="America/Paramaribo")
class CorrespondenceDateTests(SimpleTestCase):
    def test_late_evening_timestamp_keeps_the_local_day(self):
        self.assertEqual(local_calendar_date(LATE_EVENING_UTC), date(2026, 9, 26))
        self.assertEqual(_display_date(LATE_EVENING_UTC), "26-09-2026")

    def test_iso_strings_are_converted_too(self):
        self.assertEqual(
            dutch_correspondence_date("2026-09-27T01:30:00Z"), "26 september 2026"
        )
        self.assertEqual(_display_date("2026-09-27T01:30:00+00:00"), "26-09-2026")

    def test_dates_and_unparseable_values_are_left_alone(self):
        self.assertEqual(_display_date(date(1962, 6, 19)), "19-06-1962")
        self.assertEqual(_display_date("1962-06-19"), "19-06-1962")
        self.assertEqual(_display_date(1962), "1962")
        self.assertEqual(_display_date(None), "Niet vastgelegd")
        self.assertEqual(dutch_correspondence_date("onbekend"), "onbekend")
