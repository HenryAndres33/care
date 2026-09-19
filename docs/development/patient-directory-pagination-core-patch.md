# Patient directory pagination core patch

## Current ownership — 19 September 2026

The historical native patch below is superseded by the
[plugin ownership move](2026-09-19-directory-and-constraint-ownership.md).
The endpoint/pagination now live in `care_suriname/api/viewsets/patient_directory.py`,
the DTOs in `care_suriname/resources/patient_directory.py`, and tests in
`care_suriname/tests/test_patient_directory.py`. The URL and counted response are
unchanged. `PatientViewSet.directory` is absent at the fork and now absent in
native CARE again; it was a custom endpoint using native Patient data, not an
upstream directory API. Native patient DELETE and the generic birth-date filter
remain unchanged. The original sections below record the pagination change;
they are not instructions to restore native ownership or remove paging today.

## Historical rationale

The facility-authorized patient directory previously returned only the first
bounded collection. A broad name search could therefore omit a matching patient
without telling the caller. For the Suriname registration and appointment
workflow this can cause an unnecessary duplicate patient registration.

## Touched backend files

- `care/emr/api/viewsets/patient.py`
- `care/emr/tests/test_patient_api.py`

The action still uses the existing facility authorization decision and minimal
`PatientDirectorySpec`. The only contract change is native CARE
limit/offset pagination with `count`, an explicit maximum page size of 100, and
allow-listed stable server ordering.

## Upstream update review

When updating CARE, verify whether upstream `PatientViewSet.directory` now has a
counted pagination contract. If it does, compare its authorization, filters,
ordering and response schema before dropping this patch. Keep the custom UI on
one canonical native endpoint; do not introduce a second patient directory.

## Rollback

Revert the `PatientDirectoryPagination` class and the pagination block in the
`directory` action together. The frontend must be reverted in the same release,
because it requires `{ count, results }` and sends `limit`, `offset` and
`ordering`.

## Verification

```bash
python manage.py test care.emr.tests.test_patient_api.TestPatientViewSet --keepdb
npx tsx src/Plugins/urology/patient-search/tests/patientSearchAdapter.test.ts
```
