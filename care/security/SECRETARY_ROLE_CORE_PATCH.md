# Controlled core patch: Secretary role

## Rationale and safety boundary

CARE Suriname needs a facility-wide secretary who can register patient
demographics, find patients, manage provider availability and create or change
appointments without receiving the broad clinical access of the native `Staff`
role. Native CARE roles and permission associations remain the single source of
truth; the urology frontend only selects the role by name.

The `Secretary` system role is facility-scoped and receives exactly:

- patient create, demographic update and directory list;
- schedule list/write plus booking list/write/reschedule;
- read-only facility, location, healthcare-service and facility-provider
  directory access required by those workflows.

It deliberately does **not** receive clinical-data access, questionnaire or
questionnaire-response access, document access, encounter writes, medication
writes, diagnosis writes, user creation, organization management or facility
management.

## Touched core files

- `care/security/roles/role.py`
- `care/security/permissions/patient.py`
- `care/security/permissions/schedule.py`
- `care/security/permissions/facility_organization.py`
- `care/security/permissions/facility.py`
- `care/security/permissions/location.py`
- `care/security/permissions/healthcare_service.py`

`care/security/tests/test_secretary_role.py` locks the exact allow-list. Any
future permission addition must therefore be explicit and reviewed.

The generic user directory and geographic organization-user endpoints are not
part of the appointment path and are intentionally excluded. Provider lookup
uses the facility-organization membership endpoint, bounded to the active
facility.

## Deployment and verification

After deploying the backend code, run the idempotent native sync command once:

```bash
python manage.py sync_permissions_roles
python manage.py test care.security.tests.test_secretary_role --keepdb
```

Existing accounts linked as `Staff` are not changed automatically. Reassign
secretary accounts to `Secretary` through the native organization-user API or
recreate the intended test account through the CARE Suriname employee wizard.

Verify with a synthetic secretary account linked to the facility root:

1. patient search, patient creation and demographic correction succeed;
2. provider and schedule lists load;
3. appointment create, reschedule and cancellation succeed;
4. clinical chart, questionnaire responses and documents are denied.

## Upstream-update review points

Before upgrading CARE, compare the touched permission enums and scheduling
authorization controllers. Reconfirm that appointment creation still requires
`can_write_booking`, provider availability requires the schedule permissions,
and no new clinical permission is pulled transitively into these workflows.
Rerun the exact allow-list test after every upstream merge.

## Rollback

1. stop assigning new accounts to `Secretary`;
2. move existing secretary assignments to an approved temporary custom role or
   disable the accounts;
3. revert the role and permission-enum changes;
4. run `python manage.py sync_permissions_roles` again.

The sync removes the retired system role and its permission associations. It
does not delete user accounts or patient and appointment records.
