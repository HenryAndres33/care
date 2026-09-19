# Diagnosis problem-list extension

## Scope

This additive CARE extension makes the native Condition/diagnosis resource the
durable source for the custom Urology problem list. It remains generic: the
same records support urology and general medical history.

## Data contract

- `clinical_domain`: `general` or `urology`, defaulting existing rows to
  `general`.
- `client_request_id`: nullable UUID, unique when present.
- `client_request_payload_hash`: canonical request/context SHA-256.
- Existing native `onset.onset_datetime` stores the optional diagnosis date.
- Existing native `note` stores the optional clinician-authored context.
- Migration: `0093_diagnosis_native_problem_list.py`.

## Command contract

`POST /api/v1/patient/{patientId}/diagnosis/idempotent-create/`

The command:

1. binds patient and encounter under row locks;
2. enforces current encounter write authorization;
3. rejects cross-patient encounters;
4. prevents duplicate active `patient + category + system + code` records;
5. returns the original record for an exact same-actor/same-patient replay;
6. returns HTTP 409 when the idempotency key is reused for different content
   or context;
7. retains the normal Condition questionnaire/audit create path.

Replay reads re-run object-bound read authorization so revoked access does not
leak the stored diagnosis.

## Upgrade review

Before rebasing onto a future CARE release, compare Condition fields,
Condition Pydantic specs, DiagnosisViewSet authorization hooks, questionnaire
registration, and the migration dependency on 0092. Re-run the focused tests
and migration drift check before enabling the feature.

## Verification

```bash
python manage.py test \
  care.emr.tests.test_diagnosis_idempotent_api \
  care.emr.tests.test_diagnosis_api --keepdb
python manage.py makemigrations --check --dry-run
```
