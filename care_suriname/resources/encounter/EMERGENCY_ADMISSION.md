# Emergency admission handoff

Owner-approved extension, 11 September 2026. Owner: CARE Suriname maintainers.
GET/POST `encounter/{id}/admission-handoff/` reads/links an existing native
emergency encounter to an existing native inpatient admission. POST body:
`admission_id`. This is not admission creation or clinical closure.

Both encounters require clinical read and, on POST, encounter write permission.
New links require active status, matching patient/facility, emer/imp classes and
the facility kill switch. Deterministic encounter row locks and a unique emergency
FK serialize repeats; a conflicting destination is refused. No content is moved.
GET checks both resources. Existing exact links can be read/replayed after closure.
Retention follows both encounters using PROTECT; no edit/delete route.

Additive migration 0101 must precede frontend use. Rollback disables the mixin/UI
while retaining the table and audit records; never reverse after clinical use.
Upstream review: permissions, encounter lock ordering, active inpatient uniqueness,
closure restrictions and no-store responses. No generic encounter-update bypass.

Verification command (isolated stack only):
`python manage.py test care.emr.tests.test_emergency_admission --keepdb --noinput`.
Response is always JSON `{ "handoff": null | object }`; an unlinked encounter
must not return an empty body. The object includes emergency/admission/patient/
facility IDs and created_at. The frontend verifies context and GET read-back
before navigation. No clinical content is stored in this table.

11 September verification: 11 dedicated isolated endpoint tests pass (not the
earlier inherited 25-test count), Ruff passes and migration drift check is clean.
Migration 0101 applied to the live local stack without reset. As normal doctor
Sarwan, UI POST + GET read-back passed for an existing admission and a newly
created admission; repeat navigation reopened the same destination. Source
emergency encounters remained active and retained their notes. Exact identities
are recorded in the frontend guided-workflows/SPOED_WORKFLOW_PLAN.md.

This is not a full release: actual concurrent requests and permission changes
during a write were not browser-tested. Native consult closure currently requires
an appointment (`consult_closure.py`, `appointment_missing`); unscheduled
emergency closure is blocked and was not bypassed. This patch does not change it.
