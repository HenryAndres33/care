# Department access after a completed consultation

Owner-reported defect, 14 September 2026: after closing an outpatient
consultation the treating urologist got "U kunt dit dossier niet openen".

Upstream CARE grants facility staff access to a patient only through an
*active* encounter, an organization membership on the patient's (geographic)
organization, or a direct patient-user link. Closing a consultation sets the
encounter to `completed`, so the department immediately loses the patient. That
fits a hospital that discharges and forgets; it does not fit a department that
keeps a longitudinal dossier.

## What this extension does

`PatientAccess.find_roles_on_patient` additionally counts the user's roles in
the facility organizations of the patient's **completed** encounters. Only the
status `completed` counts: cancelled, discontinued and entered-in-error
encounters never grant access. Location membership is not used for completed
encounters. Everything else (organization membership, direct links, active
encounters, every permission check built on these roles) is unchanged. No
migration, no data change, no new store.

The behaviour is controlled by `PATIENT_DEPARTMENT_LONGITUDINAL_ACCESS_ENABLED`
(`config/settings/config.py`, default `True`, environment-overridable).

## Roll back

Set `PATIENT_DEPARTMENT_LONGITUDINAL_ACCESS_ENABLED=false` in the backend
environment and restart, or remove the guarded block in
`care/security/authorization/patient.py`, the setting, this file and
`care/security/tests/test_patient_department_access.py`.

## Tests

`python manage.py test care.security.tests.test_patient_department_access
--settings=config.settings.test --keepdb --noinput` in the isolated test stack.

## Ownership correction — 19 September 2026

The completed-encounter query now lives in
`care_suriname/policies/patient_access.py`. Native PatientAccess contains only a
generic optional organization-scope contribution call at the original lookup
point. The environment switch remains unchanged. Both direct permission
serialization and authorization-controller callers retain the same role lookup.
The policy also affects any existing write permission held by the matching role;
this extraction does not silently narrow it to read-only decisions. Native list
filtering remains unchanged. The old instruction to remove the native guarded
block is historical; current code rollback must include contribution registration
and implementation. Contract and limits: `care_suriname/policies/README.md` and
`docs/development/2026-09-19-patient-access-ownership.md`.
