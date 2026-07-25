# Patient directory pagination core patch

## Rationale

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
