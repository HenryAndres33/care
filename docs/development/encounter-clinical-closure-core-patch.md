# Encounter clinical-closure core patch

## Purpose

Native inpatient discharge leaves an Encounter in `discharged`, not
`completed`. CARE's historical `COMPLETED_CHOICES` intentionally does not
include `discharged`, so using that list as a general clinical write gate left
ordinary Encounter, medication, location, device, and form routes open after
administrative discharge.

This patch introduces one explicit policy:

- `COMPLETED_CHOICES` remains the narrower administrative/terminal set;
- `CLINICALLY_CLOSED_CHOICES` contains `discharged` plus every completed
  status;
- clinical write routes use `CLINICALLY_CLOSED_CHOICES`;
- billing and explicitly governed reconciliation routes keep their own policy.

This separation prevents a silent change in CARE's broader administrative
semantics while making discharge a real clinical boundary.

## Enforced behavior

For a clinically closed Encounter:

- ordinary Encounter `PUT` cannot mutate or reopen it;
- ordinary Encounter `PUT` cannot transition an open Encounter into a
  clinically closed state; a dedicated command is required;
- medication request create, update, and delete fail closed;
- bed/location create and update associations fail closed;
- device association fails closed;
- the discharge transaction releases existing device associations and closes
  their history at the authoritative discharge time;
- form finalize and amend fail closed;
- consult-closure preflight reports the Encounter as terminal;
- active-Encounter counting and the historical `live` filter classify
  `discharged` as closed, so immediate readmission remains possible.

Form `enter_in_error` remains an explicit permission-controlled reconciliation
path. Billing and charge-item handling are deliberately unchanged because
post-discharge financial completion can be valid administrative work.
`completed` restart remains a dedicated workflow and is not extended to
`discharged`.

Every changed clinical route locks or re-reads the authoritative Encounter
inside its transaction before applying the status gate. A stale client
projection therefore cannot bypass a concurrent discharge.

## Files

- `care/emr/resources/encounter/constants.py`
- `care/emr/api/viewsets/encounter.py`
- `care/emr/api/viewsets/medication_request.py`
- `care/emr/api/viewsets/location.py`
- `care/emr/api/viewsets/device.py`
- `care/emr/api/viewsets/form_submission.py`
- `care/emr/api/viewsets/consult_closure.py`
- `care/emr/tests/test_encounter_clinical_closure.py`
- `care/emr/tests/test_encounter_discharge.py`
- `care/emr/tests/test_location_api.py`
- `care/emr/tests/test_device_api.py`
- `care/emr/tests/test_form_submission_workflow.py`

## Upstream review checklist

After rebasing or upgrading CARE:

1. Confirm `StatusChoices.discharged` still represents a clinically closed
   inpatient episode.
2. Search for Encounter status checks using `COMPLETED_CHOICES`; classify every
   new write path as clinical, administrative, billing, or reconciliation.
3. Clinical writes must use `CLINICALLY_CLOSED_CHOICES` and lock or re-read the
   Encounter in the same transaction as the mutation.
4. Do not add `discharged` directly to `COMPLETED_CHOICES` without reviewing
   billing, restart, FHIR mapping, reporting, and reconciliation semantics.
5. Confirm `live=true` still means closed and `live=false` still means open in
   the existing CARE API before changing that legacy contract.
6. Re-run the discharge command, clinical-closure, Encounter, medication,
   location, device, form-submission, and consult-closure regression suites.

Useful review command:

```bash
rg -n "COMPLETED_CHOICES|CLINICALLY_CLOSED_CHOICES" care/emr
```

## Rollback

Revert the route-level substitutions and remove
`CLINICALLY_CLOSED_CHOICES`. No schema or data migration belongs to this
patch. Rolling it back reopens clinical mutation paths for already discharged
Encounters, so rollback requires an explicit clinical-risk decision and should
not be used as an operational recovery shortcut.

## Verification

From the backend checkout:

```bash
docker compose -f docker-compose.yaml -f docker-compose.local.yaml exec -T \
  backend ruff check \
  care/emr/resources/encounter/constants.py \
  care/emr/api/viewsets/encounter.py \
  care/emr/api/viewsets/medication_request.py \
  care/emr/api/viewsets/location.py \
  care/emr/api/viewsets/device.py \
  care/emr/api/viewsets/form_submission.py \
  care/emr/api/viewsets/consult_closure.py \
  care/emr/tests/test_encounter_clinical_closure.py

docker compose -f docker-compose.yaml -f docker-compose.local.yaml exec -T \
  backend python manage.py makemigrations --check --dry-run

docker compose -f docker-compose.yaml -f docker-compose.local.yaml exec -T \
  backend python manage.py test \
  care.emr.tests.test_encounter_clinical_closure \
  care.emr.tests.test_encounter_discharge \
  care.emr.tests.test_encounter_discharge_concurrency \
  care.emr.tests.test_encounter_api \
  care.emr.tests.test_medication_request \
  care.emr.tests.test_medication_request_idempotency \
  care.emr.tests.test_location_api \
  care.emr.tests.test_device_api \
  care.emr.tests.test_form_submission_workflow \
  care.emr.tests.test_consult_closure \
  --keepdb --noinput
```

The deployment smoke test must use synthetic data and the intended CARE role:
complete preflight and discharge in the browser, verify bed release and
authoritative read-back, prove the ordinary Encounter and child-resource
mutation routes return validation failures, prove immediate readmission still
succeeds, then delete the complete fixture. Never record credentials, tokens,
identifiers, or patient payloads in this document.

## Local production-smoke evidence — 2026-07-25

The final local smoke used a temporary non-superuser with the native Doctor
role and one fully synthetic inpatient Encounter with an assigned bed and
device:

- the admission overview rendered the active admission and assigned bed;
- discharge preflight returned ready with no blockers;
- the UI completed administrative discharge and, after a full reload, showed
  `discharged`, no assigned bed, disabled clinical actions, and one appended
  discharge history entry;
- authoritative Encounter read-back returned HTTP 200;
- exact command replay returned HTTP 200 with `replayed=true`;
- privileged ordinary Encounter update, medication create, location
  association, and device association attempts each returned HTTP 400;
- the bed and device were released, and both histories closed;
- device history ended at the authoritative discharge time;
- immediate inpatient readmission through the intended Doctor role returned
  HTTP 200;
- the browser console contained no errors;
- cleanup verification returned zero remaining commands, devices, Encounters,
  locations, patients, and temporary users.

No credential, token, identifier, or patient payload is retained here.

The final automated verification also passed:

- Ruff on all changed backend Python files;
- `makemigrations --check --dry-run` with no changes;
- 230 backend regressions;
- full frontend TypeScript compilation;
- four focused admission/discharge contract suites;
- full frontend ESLint in errors-only mode;
- the production Vite/PWA build.
