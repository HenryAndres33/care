# Discharge documentation warnings — updated 14 September 2026

Current verification supersedes the historical blocker below: Sarwan's synthetic
admission 826017e3… passed finalized letter/PDF, preflight, discharge command,
discharged read-back and active census removal. The owner approved native author
verification; no role elevation or safety bypass occurred. During release audit,
22 combined reservation, documentation and native lifecycle/concurrency tests
passed in the isolated care-test database. Occupied-bed release was not tested
live; broader catalogue/permission concurrency remains a stated limitation.

Owner-approved local core patch, uncommitted. This is a safety prerequisite,
not evidence of complete live clinical acceptance. Read alongside
`ADMISSION_DOCUMENTATION.md` and `docs/development/encounter-discharge-core-patch.md`.

## Why server inspection is necessary

The owner decided that operational discharge must remain possible when the Urology
discharge summary or letter is incomplete. The server still inspects documentation
inside the transaction so the gap is visible, deterministic, and audit-bound rather
than silently ignored by the frontend.

Both `preflight-discharge` and `idempotent-discharge` inspect:

- the reserved `discharge` slot's current, submitted `urology-medisch-dossier`
  FormSubmission, belonging to this admission and patient;
- a finalized current correspondence revision compiled from that exact summary,
  with matching patient, encounter and facility;
- existing correspondence actionability checks, including source/review integrity,
  author/recipient/template availability and an available immutable PDF artifact.

Missing or unavailable documentation produces a sorted `warning_codes` list and does
not change `ready`. The immutable discharge command snapshot records the same warnings
with `documentation=null`. Authorization, active inpatient state, period, timestamp,
location history, bed release, device release, concurrency, integrity, and idempotency
remain hard server-side blockers. This does not change outpatient closure or generic
encounter status rules.

## Transaction and provenance

Authorization precedes documentation reads. The current form-series head is
locked before the encounter/bed locks, matching the existing amendment lock
order. The final command reruns both native blockers and documentation inspection inside
its transaction; a previous ready response is not authority to close. Native lifecycle, period, disposition,
bed release, device disassociation and idempotency guards remain unchanged.

When documentation is complete, the command snapshot records summary identity/version/
hash and letter identity/hash. Otherwise it records the exact documentation warning
codes; these are the durable open-action audit evidence. The documentation field stays
optional for replay of pre-existing commands. Exact authorized replay returns the committed result;
it does not require today's documentation state to repeat yesterday's mutation.

The letter scan is bounded to 50 candidates; excessive or invalid candidates produce
`discharge_letter_unavailable`. Missing summary, missing letter, and unavailable or
invalid finalized letter have distinct warning codes. Artifact availability here uses CARE metadata/integrity
checks, not an object-store byte download; browser delivery separately verifies
the PDF bytes. The entire correspondence catalogue is not locked by this gate;
broader concurrent permission/catalogue mutation acceptance remains unverified.

## Changed production files / rollback

- `resources/encounter/discharge_documentation.py`: focused policy helper.
- `api/viewsets/encounter_discharge.py`: preflight and command integration.
- `resources/encounter/discharge.py`: typed evidence in response schema.

No new database migration, reset, permission bypass or shadow clinical store.
The prior admission-documentation migration is a prerequisite, not introduced
by this gate. Upstreaming should make specialty policy a deliberate native
contract before adoption outside this Urology deployment. Rollback is code-only:
remove the policy integration/schema addition, preserving existing command
snapshots and the admission-documentation data. Such rollback removes documentation warning evidence and needs explicit owner approval;
never delete clinical or audit rows.

## Verification

Isolated database command:

```sh
docker exec care-test-backend-1 /.venv/bin/python manage.py test care.emr.tests.test_discharge_documentation care.emr.tests.test_encounter_discharge care.emr.tests.test_encounter_discharge_concurrency --keepdb --noinput
```

The focused suites pass 13 tests. Documentation tests cover missing, draft, wrong-source,
invalid and archived documentation as successful discharge with warnings, plus complete
documentation and exact replay. Lifecycle tests continue to prove hard blockers and
atomic bed/device release. PDF rendering and S3 are mocked in these
tests; permissions and the native command are real. Existing lifecycle/bed and
concurrency suites isolate the added policy with a stub. They do not prove the
new documentation gate's full multi-user concurrency behavior. Scoped Ruff passes.

Browser acceptance of the one-action frontend and a warning-bearing live synthetic
discharge is required after deployment. The automated backend result does not by itself
prove the visible workflow, census refresh, PDF retrieval, or occupied-bed release.
See the frontend admission simulation evidence.
