# Operation planning extension — 11 September 2026

Owner: CARE Suriname urology clinical owner; implementation: Codex. Explicitly
approved by the owner before implementation. Status: local acceptance in progress.

## Boundary

Native TokenBooking remains the source of patient, practitioner, facility, slot,
status, capacity and cancellation. OperationPlan adds only the planned procedure
label/family and one stable native report identity. It does not assert that an
operation happened, create an encounter, author a report, or close an appointment.
No new permission or privileged role is introduced.

The scheduling viewset exposes:

- GET `appointments/operation-programme/?day=YYYY-MM-DD&surgeon=UUID&limit=25&offset=0`:
  permission-checked practitioner scope, Suriname midnight boundaries, counted
  pagination, joined plan/encounter/resource reads (no per-row fetches).
- GET/POST `appointments/{booking}/operation-plan/`: native booking read/write
  permission; explicit expected revision; terminal bookings rejected. Earlier
  planning values, actor and time are retained in the append-only revisions array.
- POST `appointments/{booking}/operation-report-slot/`: native encounter clinical
  read/write permissions, exact patient/facility, active status for first binding,
  stable replay and no rebinding. This reserves an identity, not clinical text.
  Actual report creation still independently requires questionnaire authorization.

All paths are below `/api/v1/facility/{facility}/`. Existing scheduling endpoints
are unchanged. Failed reads never authorize a write. Mutations respect the
existing facility workflow kill switch. Encounter-before-booking lock order is
consistent with clinical closure. A locked booking serializes planner changes
against reservation. OneToOne booking and unique form UUID prevent duplicates.

The locked FormSubmission create command checks reserved UUID context against
the plan. Reopening uses the existing form repository/series resolver, including
its immutable amendments and entered-in-error safeguards.

## Retention and change policy

Retain plans and their revision history with the associated clinical record;
there is no delete endpoint or automatic expiry. Protected foreign keys prevent
accidental cascade deletion. After report reservation, planning metadata is
frozen; clinical corrections use the existing report correction workflow.
Native rescheduling creates another booking: it does not silently move the
original plan or report. A replacement booking must be explicitly planned.
Future rescheduling UI must disclose this; do not copy clinical text.

## Patch inventory and deployment

New files: models/operation_plan.py; resources/scheduling/operation_plan.py;
api/viewsets/scheduling/operation_plan.py; migrations/0103_operation_plan.py;
tests/test_operation_plan.py. Integration: models/__init__.py,
api/viewsets/scheduling/booking.py, api/viewsets/form_submission.py.

1. Run focused tests in the isolated test backend, never reset the live database.
2. Apply additive migration 0103 after existing migrations 0101/0102.
3. Deploy the matching frontend plugin and verify secretary and clinician roles.
4. Preserve paper workflow until clinical acceptance is approved.

Rollback: disable the frontend entry and these new API actions, retaining the
table and reports. Do not reverse the migration after plans exist. Never drop
the table or delete reports to roll back presentation. Historical reports still
use native FormSubmission and remain readable outside the planner.

Upstream updates: review booking authorization argument order, resource identity,
terminal status definitions, native rescheduling/cancellation lock order, and
the locked idempotent form-create seam. Re-run permission, stale revision,
foreign-patient, stable reservation, kill-switch and form workflow tests. This
extension is upgrade-reviewable, not guaranteed conflict-free.

Verification: 11 planning tests and the native form workflow/API regression
suite (91 total) passed in the isolated backend. Migration 0103 applied locally
without resets. Full live acceptance is tracked by the frontend module README.
