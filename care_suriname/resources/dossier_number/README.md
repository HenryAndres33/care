# Dossiernummer (paper-record number)

In the hybrid workflow every printed note and letter is filed in the paper
dossier. The Dossiernummer is how a printout is matched to that dossier.

## What it is

A native CARE patient identifier, configured once, instance-wide (CARE's
patient update refuses facility-scoped configs):

| Setting | Value | Why |
|---|---|---|
| display | Dossiernummer | label in registration and edit |
| system | `system.care-suriname/medical-record-number` | the urology header shows identifiers whose system matches `medical…record` as the MRN |
| use | official | the GP letter and PDF headers pick usual/official identifiers first |
| unique | true | two patients cannot share one paper dossier |
| required | false | an emergency patient may not have a dossier number yet |
| search | exact match only | `retrieve_partial_search=false` |

No code change was needed for registration (CARE's form renders instance
configs) or for the GP letter (`reports/correspondence_letter_metadata.py`).
The note PDF's running header now also shows `Patiëntnr.` via the same
`patient_record_identifier`, so a printed note carries the number.

## Create it

```bash
python manage.py provision_dossier_number_identifier                        # dry run
python manage.py provision_dossier_number_identifier --apply --user <superuser>
```

Idempotent: an existing config with this system is reported, never changed.
`export_facility_setup` carries instance identifier configs, so a clean-start
server receives it through the import.

## Rollback

Set the config's status to `inactive` in CARE (Centraal beheer › patient
identifier configs); stored numbers stay in the audit trail. Code: remove this
directory, the command, `tests/test_dossier_number.py` and the `identifier=`
argument in `reports/form_submission_artifact.py`.

## Verification — 25 September 2026

`care_suriname.tests.test_dossier_number` (dry run, apply, idempotence,
superuser required, note PDF header shows `Patiëntnr.`), together with
`care.emr.tests.test_form_submission_artifact` and
`care_suriname.tests.test_facility_setup`: 40 tests pass.
