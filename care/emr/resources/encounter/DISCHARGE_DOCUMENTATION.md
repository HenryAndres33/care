# Discharge documentation gate — 11 September 2026

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

## Why server enforcement is necessary

Previously the native discharge preflight could report ready even when the
Urology discharge letter was blocked. A disabled frontend button alone cannot
protect direct API calls or stale preflight results.

Both `preflight-discharge` and `idempotent-discharge` now require:

- the reserved `discharge` slot's current, submitted `urology-medisch-dossier`
  FormSubmission, belonging to this admission and patient;
- a finalized current correspondence revision compiled from that exact summary,
  with matching patient, encounter and facility;
- existing correspondence actionability checks, including source/review integrity,
  author/recipient/template availability and an available immutable PDF artifact.

The gate applies to every inpatient call through these discharge endpoints. It
does not infer a specialty from display names, skip documentation for missing
slots, or permit a caller to opt out. This deployment assumption must be reviewed
before reusing the endpoints for other specialties or legacy admissions. It
does not change outpatient closure or generic encounter status rules.

## Transaction and provenance

Authorization precedes documentation reads. The current form-series head is
locked before the encounter/bed locks, matching the existing amendment lock
order. The final command reruns the checks inside its transaction; a previous
ready response is not authority to close. Native lifecycle, period, disposition,
bed release, device disassociation and idempotency guards remain unchanged.

The command snapshot records summary identity/version/hash and letter
identity/hash. The response schema makes this field optional for replay of
pre-existing commands. Exact authorized replay returns the committed result;
it does not require today's documentation state to repeat yesterday's mutation.

The letter scan is bounded to 50 candidates and fails closed beyond that.
Missing summary, missing letter, and unavailable/invalid finalized letter have
distinct blocker codes. Artifact availability here uses CARE metadata/integrity
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
snapshots and the admission-documentation data. Such rollback removes a safety
guard and needs explicit owner approval; never delete clinical/audit rows.

## Verification

Isolated database command:

```sh
docker exec care-test-backend-1 /.venv/bin/python manage.py test care.emr.tests.test_discharge_documentation care.emr.tests.test_encounter_discharge care.emr.tests.test_encounter_discharge_concurrency --keepdb --noinput
```

14 tests pass. Six new HTTP tests exercise the real documentation gate, including
missing/draft/wrong-source letters, invalid summary, archived PDF after preflight,
successful discharge and exact replay. PDF rendering and S3 are mocked in these
tests; permissions and the native command are real. Existing lifecycle/bed and
concurrency suites isolate the added policy with a stub. They do not prove the
new documentation gate's full multi-user concurrency behavior. Scoped Ruff passes.

Browser preflight on synthetic admission `9b6dbdc3-7780-4439-9499-746854e3bcd0`
returned `ready:false`, `discharge_letter_required`, and disabled final closure.
No live discharge was performed. Final letter generation is blocked by missing
native reason configuration and the signed-in user's denied tag-config write
permission. Full completion/PDF/census read-back must be retested after normal
administrator configuration. See frontend admission `DISCHARGE_SIMULATION.md`.
