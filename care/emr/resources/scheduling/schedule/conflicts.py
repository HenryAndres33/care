import datetime
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_time
from rest_framework.exceptions import ValidationError

from care.emr.models.scheduling.schedule import Schedule

SCHEDULE_OVERLAP_MESSAGE = (
    "Availability overlaps with an existing schedule for this resource"
)


@dataclass(frozen=True)
class WeeklyTimeRange:
    day_of_week: int
    start_time: datetime.time
    end_time: datetime.time


def _as_datetime(value: datetime.datetime | str) -> datetime.datetime:
    if isinstance(value, str):
        parsed_value = parse_datetime(value)
        if parsed_value is None:
            raise ValueError("Invalid schedule datetime")
        value = parsed_value
    if settings.USE_TZ and timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _as_local_date(value: datetime.datetime) -> datetime.date:
    if timezone.is_aware(value):
        value = timezone.make_naive(value, timezone.get_current_timezone())
    return value.date()


def _as_time(value: datetime.time | str) -> datetime.time:
    if isinstance(value, datetime.time):
        return value
    parsed_value = parse_time(value)
    if parsed_value is None:
        raise ValueError("Invalid availability time")
    return parsed_value


def _read_value(value, key: str):
    if isinstance(value, Mapping):
        return value[key]
    return getattr(value, key)


def _weekly_ranges(availability_groups: Iterable) -> list[WeeklyTimeRange]:
    ranges = []
    for group in availability_groups:
        for window in _read_value(group, "availability"):
            ranges.append(
                WeeklyTimeRange(
                    day_of_week=int(_read_value(window, "day_of_week")),
                    start_time=_as_time(_read_value(window, "start_time")),
                    end_time=_as_time(_read_value(window, "end_time")),
                )
            )
    return ranges


def _weekday_occurs_between(
    day_of_week: int, start_date: datetime.date, end_date: datetime.date
) -> bool:
    days_until_weekday = (day_of_week - start_date.weekday()) % 7
    return days_until_weekday <= (end_date - start_date).days


def _time_ranges_overlap(left: WeeklyTimeRange, right: WeeklyTimeRange) -> bool:
    return left.start_time < right.end_time and right.start_time < left.end_time


def has_resource_schedule_conflict(
    *,
    resource_id: int,
    valid_from: datetime.datetime | str,
    valid_to: datetime.datetime | str,
    availability_groups: Iterable,
    exclude_schedule_id: int | None = None,
) -> bool:
    proposed_valid_from = _as_datetime(valid_from)
    proposed_valid_to = _as_datetime(valid_to)
    proposed_ranges = _weekly_ranges(availability_groups)

    schedules = Schedule.objects.filter(
        resource_id=resource_id,
        valid_from__lte=proposed_valid_to,
        valid_to__gte=proposed_valid_from,
    ).prefetch_related("availability_set")
    if exclude_schedule_id is not None:
        schedules = schedules.exclude(id=exclude_schedule_id)

    for schedule in schedules:
        overlap_start = max(
            _as_local_date(proposed_valid_from),
            _as_local_date(schedule.valid_from),
        )
        overlap_end = min(
            _as_local_date(proposed_valid_to),
            _as_local_date(schedule.valid_to),
        )
        if overlap_start > overlap_end:
            continue

        existing_ranges = _weekly_ranges(schedule.availability_set.all())
        for proposed_range in proposed_ranges:
            if not _weekday_occurs_between(
                proposed_range.day_of_week, overlap_start, overlap_end
            ):
                continue
            for existing_range in existing_ranges:
                if proposed_range.day_of_week != existing_range.day_of_week:
                    continue
                if _time_ranges_overlap(proposed_range, existing_range):
                    return True
    return False


def assert_no_resource_schedule_conflict(**kwargs) -> None:
    if has_resource_schedule_conflict(**kwargs):
        raise ValidationError({"availabilities": [SCHEDULE_OVERLAP_MESSAGE]})
