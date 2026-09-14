# Native inpatient discharge core patch

## Rationale

CARE already owns inpatient admission state through the native `Encounter`.
The urology admission UI therefore needs a narrow command boundary that updates
the native encounter and bed state atomically. A full Encounter `PUT` is not a
safe production command because it can overwrite unrelated care-team, priority,
period, hospitalization, or extension fields from a stale client projection.

This patch adds a partial, idempotent discharge command. It accepts only the
discharge disposition, discharge timestamp, advice, and bed-release intent. The
server locks the current Encounter and current FacilityLocation, revalidates
that the Encounter is an active inpatient, and commits the Encounter,
FacilityLocation, FacilityLocationEncounter history, current device
associations, DeviceEncounterHistory, and immutable command ledger in one
transaction.

## Live contract verification

The blocking Fase 0 check was completed against the local backend on port 9000
with the intended `care-doctor` fixture role and synthetic clinical data:

- login returned HTTP 200;
- `PUT /api/v1/encounter/{id}/` with `status=discharged` returned HTTP 200;
- authoritative Encounter read-back returned HTTP 200;
- status, appended status history, discharge disposition, `period.end`, and
  discharge advice were all confirmed;
- an immediate new inpatient admission for the same synthetic patient returned
  HTTP 200.

No password, token, identifier, or patient payload is recorded here.

## Production clinical-closure boundary

`discharged` is now enforced as a clinically closed status across the ordinary
Encounter, medication, location, device, form, active-count, and consult-close
paths. This deliberately does not redefine CARE's narrower
`COMPLETED_CHOICES`, because billing and governed administrative reconciliation
may remain valid after discharge.

The policy, route inventory, upstream checklist, rollback behavior, and
verification matrix are documented in
`docs/development/encounter-clinical-closure-core-patch.md`.

## API contract

`POST /api/v1/encounter/{id}/preflight-discharge/` accepts:

- `discharge_disposition`;
- timezone-aware `discharged_at`;
- `discharge_summary_advice` of at most 4000 characters;
- `release_bed`.

It returns HTTP 200 with `ready`, sorted hard `blocker_codes`, sorted non-blocking
`warning_codes` for missing or unavailable discharge documentation, and `checked_at`.

`POST /api/v1/encounter/{id}/idempotent-discharge/` accepts the same fields plus
a UUID-v4 `client_request_id`. A first commit returns HTTP 201. An exact replay
returns HTTP 200 with the original immutable result snapshot and
`replayed=true`. Reuse of the request ID with another actor, Encounter, or
payload returns HTTP 409 with `idempotency_conflict`. The committed immutable
result snapshot repeats any documentation `warning_codes`, so discharge remains
possible without silently losing the open documentation state.

An assigned bed makes `release_bed=false` a preflight blocker. This prevents a
discharged admission from retaining a current bed. Missing or contradictory
location history also fails closed. Existing device associations are always
released at the authoritative discharge timestamp; new device associations are
blocked once the Encounter is clinically closed.

## Authorization and audit

The caller must have both Encounter read/clinical-read access and Encounter
write/clinical-write access. The default Doctor role supplies these permissions
inside its authorized facility-organization boundary.

`EncounterDischargeCommand` is immutable, globally unique by
`client_request_id`, and binds the actor, Encounter, canonical payload hash,
command hash, and exact response snapshot. It is excluded from the generic
value-diff audit logger because this domain ledger already contains the
queryable command evidence and may contain clinical advice.

## Files

- `care/emr/api/viewsets/encounter_discharge.py`
- `care/emr/resources/encounter/discharge.py`
- `care/emr/resources/encounter/discharge_state.py`
- `care/emr/models/encounter_discharge.py`
- `care/emr/migrations/0099_encounter_discharge_command.py`
- `care/emr/tests/test_encounter_discharge.py`
- `care/emr/tests/test_encounter_discharge_concurrency.py`
- `care/emr/resources/encounter/constants.py`
- `care/emr/api/viewsets/encounter.py`
- `care/emr/api/viewsets/medication_request.py`
- `care/emr/api/viewsets/location.py`
- `care/emr/api/viewsets/device.py`
- `care/emr/api/viewsets/form_submission.py`
- `care/emr/api/viewsets/consult_closure.py`
- `care/emr/tests/test_encounter_clinical_closure.py`
- `docs/development/encounter-clinical-closure-core-patch.md`
- `care/emr/models/__init__.py`
- `config/api_router.py`
- `config/settings/base.py`

The closure patch is deliberately route-scoped and does not change the
Encounter schema.

## Upstream update review

On an upstream CARE update, verify:

1. `StatusChoices.discharged` remains a valid native Encounter status and the
   active-inpatient constraint still covers only `in_progress` and `on_hold`.
2. Encounter status history still uses `{"history": [...]}` with `status` and
   `moved_at` entries.
3. Encounter `period`, `hospitalization.discharge_disposition`, and
   `discharge_summary_advice` retain their current native meanings.
4. FacilityLocation and FacilityLocationEncounter remain the authoritative bed
   cache and bed-history resources.
5. Encounter and location assignment code continues to acquire the Encounter
   lock before the FacilityLocation lock, preserving lock order.
6. No upstream native idempotent discharge endpoint supersedes this patch.
7. The Doctor role continues to carry both non-clinical and clinical Encounter
   read/write permissions.
8. Every new Encounter-linked clinical write route uses the
   `CLINICALLY_CLOSED_CHOICES` policy described in
   `encounter-clinical-closure-core-patch.md`.

## Rollback

Disable frontend use of both command routes first. Then roll back application
code and reverse migration `0099_encounter_discharge_command`. Reversing the
migration deletes only the command ledger table; it does not reopen already
discharged Encounters or reoccupy released beds. Those clinical records require
an explicitly governed reconciliation workflow and must never be reconstructed
automatically from this ledger.

## Verification

Run from the canonical backend checkout:

```bash
docker compose exec backend bash -c "/.venv/bin/ruff check \
care/emr/api/viewsets/encounter_discharge.py \
care/emr/resources/encounter/discharge.py \
care/emr/resources/encounter/discharge_state.py \
care/emr/models/encounter_discharge.py \
care/emr/migrations/0099_encounter_discharge_command.py \
care/emr/tests/test_encounter_discharge.py \
care/emr/tests/test_encounter_discharge_concurrency.py \
config/api_router.py config/settings/base.py care/emr/models/__init__.py"
docker compose exec backend /.venv/bin/python \
  manage.py makemigrations --check --dry-run
docker compose exec backend /.venv/bin/python manage.py test \
  care.emr.tests.test_encounter_clinical_closure \
  care.emr.tests.test_encounter_discharge \
  care.emr.tests.test_encounter_discharge_concurrency \
  care.emr.tests.test_encounter_api --keepdb
```

Also repeat one create/read-back/replay with synthetic data and the intended
CARE role after deployment. Record only endpoint, status, schema, and test
result; never record credentials, tokens, identifiers, or patient payloads.

The 2026-07-25 local production smoke, post-discharge bypass results, device and
bed verification, browser reload, console check, and zero-count cleanup are
recorded in `encounter-clinical-closure-core-patch.md`.
