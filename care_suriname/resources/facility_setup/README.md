# Facility setup export / import

Moves one facility's **setup** — never its patients — from one CARE database
to another. Built for the clean-start server (zero patients) described in
[`deploy/clean-start-inventory.md`](../../../deploy/clean-start-inventory.md).

## What travels

Facility (with its Suriname › Paramaribo geo path), departments, custom roles
with their permission slugs, patient identifier configs, active visit reasons,
locations, healthcare services, resource categories, lab tests
(ObservationDefinitions, including the plug's governed reference `meta`), lab
panels (ActivityDefinitions), letter templates, the urology forms, active
Smart Text and the Dutch diagnosis names.

Never: patients or anything attached to them, users, schedules, files,
draft-recovery keys, archived items, CARE demo facilities.

## Commands

```bash
# On the source (read-only; runs in a READ ONLY transaction):
python manage.py export_facility_setup \
    --facility-name "Academisch Ziekenhuis Paramaribo" --output azp-setup.json

# On the target, after migrate / sync_permissions_roles / sync_valueset and
# with one superuser:
python manage.py import_facility_setup --input azp-setup.json --user <superuser>
```

Optional: `--questionnaire SLUG` (repeatable; default the three urology forms),
`--exclude-clinical-text KEY` (repeatable), `--facility-name` on import.

## Rules

- **IDs are rebuilt.** Facility-scoped slugs are recreated as
  `f-<new facility id>-<name>`; panels, visit reasons, locations and services
  are re-linked by name. The system "Administration" root travels as a marker.
- **All or nothing.** The import is one transaction and only adds rows. A
  facility with the same name, a role with other permissions, an existing form
  slug or a Dutch name that differs stops it; nothing is written.
- **Review provenance is honest.** Approved Dutch names need a reviewer; the
  importing admin is recorded as reviewer, and the original reviewer and date
  are kept in `meta.care_suriname.imported_review`.
- Lab reference rules match by LOINC and unit, and their fingerprint is
  content-only, so the governed Hb mmol/L definition works unchanged.

## Rollback

The import only creates rows. On a fresh server, roll back by restoring the
pre-import database backup (`deploy/server-backup.sh`). Removing the code:
delete this directory, the two commands and `tests/test_facility_setup.py`;
no migration or native change is involved.

## Verification — 25 September 2026

- `care_suriname.tests.test_facility_setup`: 5 tests pass (round trip, demo
  material excluded, conflict rollback, same-name refusal, role conflict).
- Rehearsal: export of the laptop database (read-only) imported into a new,
  freshly migrated throwaway database on the isolated test Postgres: 0
  patients; 31/31 lab slugs on the new facility id; all 8 panels resolve to the
  new tests; Hb fingerprint identical; Uroloog 68 and Secretary 13
  permissions; both letter templates and all Smart Text byte-identical (md5);
  `configure_laboratory_reference_catalogue` dry-run: `definition=present:active`.
  The throwaway database was dropped afterwards; `care_test` was not touched.
