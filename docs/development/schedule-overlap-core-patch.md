# Controlled core patch: schedule overlap enforcement

**Owner:** CARE Suriname scheduling workflow  
**Status:** authorized clinical-integrity exception  
**Migration:** none

## Purpose

CARE schedule templates for the same schedulable resource must not create
overlapping availability. The frontend may provide an early warning, but the
backend is the authoritative enforcement point.

CARE has no plugin seam for enforcing invariants across Schedule writes. A
minimal core change is therefore necessary: frontend-only validation would be
racy and could be bypassed by native or third-party API clients.

## Touched files

- `care/emr/api/viewsets/scheduling/schedule.py`
- `care_suriname/resources/scheduling/conflicts.py`
- `care/emr/resources/scheduling/schedule/spec.py`
- `care/emr/tests/test_schedule_api.py`
- `docs/development/schedule-overlap-core-patch.md`

## Implementation

- `care_suriname/resources/scheduling/conflicts.py` contains the shared
  cross-template conflict check.
- Schedule create/update and availability create acquire a database row lock on
  the related `SchedulableResource` inside `transaction.atomic()` before the
  conflict check and write.
- Schedule deletion acquires the same resource-row lock. Availability creation
  then re-reads and locks the active Schedule after obtaining the resource lock,
  so it cannot attach a new availability to a concurrently soft-deleted
  schedule.
- The query only considers active schedules for the same resource whose date
  ranges intersect. `Schedule.objects` uses CARE's `BaseManager`, which excludes
  soft-deleted rows. A weekly time range conflicts only when that weekday
  actually occurs inside the intersecting dates.
- Time windows use half-open interval semantics. Adjacent windows such as
  `09:00-10:00` and `10:00-11:00` are valid.
- Conflicts return HTTP 400 on the existing endpoint with the existing request
  and success-response contracts unchanged. No database migration is required.

The conflict response is a normal DRF validation error on `availabilities`:

```json
{
    "availabilities": [
        "Availability overlaps with an existing schedule for this resource"
    ]
}
```

No request bodies, credentials, patient data, or generated secrets are logged
or persisted by this patch.

## Update review

When rebasing onto a newer CARE version, review changes in:

1. `ScheduleViewSet.perform_create` and `perform_update`;
2. `AvailabilityViewSet.perform_create`;
3. `ScheduleCreateSpec`, `ScheduleUpdateSpec`, and availability serialization;
4. the `Schedule`, `Availability`, and `SchedulableResource` models.

Retain the resource-row lock on every path that can add availability. Run the
targeted schedule API tests after resolving upstream changes.

The overlap regressions remain in the existing cohesive
`test_schedule_api.py` fixture because moving them would duplicate its facility,
role, authorization, and schedule factories. The production conflict module
remains below the workspace file-size target.

```bash
docker compose exec backend bash -c \
  "python manage.py test care.emr.tests.test_schedule_api --keepdb --parallel 1"
```

Lint the complete controlled patch:

```bash
docker compose exec backend bash -c \
  "ruff check care/emr/api/viewsets/scheduling/schedule.py care_suriname/resources/scheduling/conflicts.py care/emr/resources/scheduling/schedule/spec.py care/emr/tests/test_schedule_api.py"
```

## Rollback

Revert only the touched hunks and delete `conflicts.py`; no schema rollback is
needed. After rollback, restart the backend and rerun the schedule API tests.
Do not roll back while overlapping templates created under this protection are
still being edited: first audit active schedules per resource, because removing
the backend guard reopens the race for every API client.
