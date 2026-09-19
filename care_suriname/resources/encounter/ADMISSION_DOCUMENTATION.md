# Admission documentation reservation (11 September 2026)

Owner approved the minimal backend extension after the ward-workflow audit.
Owner: CARE Suriname clinical application maintainers.

## Native ownership and scope

`POST /api/v1/encounter/{external_id}/documentation-slot/` accepts `kind`:
`admission`, `visit`, or `discharge`, with optional ISO `visit_date` for visits.
The immutable reservation contains admission, slot, form-instance UUID, creator
and creation time only. Clinical text, revisions, finalization and PDFs remain
native admission-owned FormSubmission resources. A reservation is NOT a saved
note or proof that a round occurred.

This supersedes the proposed child-encounter approach for these three note
actions. Keeping notes on the inpatient encounter preserves existing clinical
closure authorization and avoids separate child lifecycle/permission copies.
Acute-event and transfer legacy integrations are not enabled by this patch.

The admission row lock serializes reservation with discharge; the database
constraint enforces one slot per admission. Visits use America/Paramaribo dates;
new past/future slots are rejected. Existing slots may be resolved again without
creating another record. FormSubmission's series/version and idempotent commands
remain responsible for concurrent text edits. No browser-only clinical fallback.
Read-clinical and write-encounter authorization are required. New reservations
respect the existing facility workflow kill switch and active inpatient status.

## Deployment / review

Files: `models/admission_documentation.py`, model registration, migration 0100,
`api/viewsets/admission_documentation.py`, one mixin import/base in EncounterViewSet,
and `tests/test_admission_documentation.py`. No new router or configuration.
Apply additive migration 0100 before exposing the frontend actions. Do not reset
or restore databases. Applied on the canonical local backend on 11 September.

Retention follows the admission record; protected relations and no delete API
prevent silent loss. Rollback: disable frontend entries and remove the action
mixin while retaining the table and existing notes. Do not reverse the migration
after use. Upstream review must recheck Encounter authorization, row-lock order,
FormSubmission series uniqueness and post-discharge write rejection.

## Verification

Isolated test stack: 22 tests passed across test_admission_documentation,
test_encounter_discharge and test_encounter_admission_note_command. The eight
new tests cover replay, kind separation, foreign-user denial, inactive/non-inpatient
rejection, Suriname day validation, database uniqueness and kill-switch refusal.
No multi-threaded concurrency test or multi-user browser acceptance claimed yet.
Live browser resolution/save/reload/finalization of an admission-owned note passed.
Frontend acceptance and remaining issues are in its admission/documentation README.
