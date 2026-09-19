# Urology TURP clinical-text catalog core patch

## Rationale

The TURP Smart Text template and its reusable lists are native, facility-scoped
`ClinicalTextResource` records. They must be reproducible after a database
restore or Playwright snapshot reset; browser-created configuration alone is
not a deployable source of truth.

## Controlled core surface

- `urology_turp.py` declares catalog version 2: one template, sixteen lists and
  one choice preset. It mirrors the frontend TURP clinical-text contract.
- `provision_urology_turp_clinical_text.py` validates the declarations with the
  existing Pydantic write specification and performs an audited, transactional,
  idempotent upsert for explicitly named facilities.
- The command requires a superuser through `--user` so created and changed
  records retain a named, authorized CARE actor. Unchanged records do not
  receive artificial version increments.

No model, migration, authorization rule or API contract is changed.

## Upstream-update review points

When updating CARE, recheck `ClinicalTextResource`,
`ClinicalTextResourceWriteSpec`, allowed template scopes and the uniqueness of
`(facility, kind, key)`. When changing the frontend `.turp` template or lists,
update this versioned catalog in the same release and compare all 18 normalized
resources before deployment.

## Rollback

Revert the catalog, command and focused test files. Do not delete provisioned
resources or reset their versions. If the clinical content itself must be
changed, publish a newer reviewed catalog version through the same command so
the audit and version history remain visible.

## Verification

```bash
ruff format care/emr/clinical_text_catalogs \
  care/emr/management/commands/provision_urology_turp_clinical_text.py \
  care/emr/tests/test_provision_urology_turp_clinical_text.py
ruff check care/emr/clinical_text_catalogs \
  care/emr/management/commands/provision_urology_turp_clinical_text.py \
  care/emr/tests/test_provision_urology_turp_clinical_text.py
python manage.py test \
  care.emr.tests.test_provision_urology_turp_clinical_text --keepdb
```

For the standard fixture database, provision after `load_fixtures` and before
creating the database snapshot:

```bash
python manage.py provision_urology_turp_clinical_text \
  --facility-name "FACILITY WITH PATIENTS" --user admin
```
