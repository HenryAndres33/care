# The `care_suriname` plug app

**Since:** 18 September 2026 (Phase 1, code only). **Backup before the change:**
tag `backup/pre-plug-app-2026-09-18`, `output/backups/care-pre-plug-app-2026-09-18/`
(laptop database dump included).

## What it is

`care_suriname/` at the repository root is a Django app loaded through CARE's own
plug mechanism (`plugs/manager.py`). `plug_config.py` adds it to the app list
without a pip install (`LocalPlugManager.get_apps`), so it appears in
`INSTALLED_APPS`, gets `api/care_suriname/` (unused), and, through the seam
below, extends `api/v1/`.

It carries the Suriname code that plugs into CARE's registration points:

| In the plug | Was in core |
|---|---|
| `api/viewsets/*` (clinical text, term translation, correspondence x7, consult closure, workflow capability, admission note, discharge) | `care/emr/api/viewsets/*` |
| `v1_urls.py` (same prefixes, basenames and names) | `config/api_router.py`, `config/urls.py` |
| `tasks.py` (four 60 s correspondence scans) | `care/emr/tasks/__init__.py` |
| `checks.py` (deploy checks E001 to E007) | `care_suriname/checks.py`, imported from `care/emr/apps.py` |
| `extensions/encounter_admission_note.py` | `care/emr/extensions/` |
| `authorization.py` (three `can_*` methods, registered as handlers) | `care/security/authorization/{patient,encounter,questionnaire_response_template}.py` |
| `management/commands/*` (five commands, names unchanged) | `care/emr/management/commands`, `care/users/management/commands` |

Six core files are byte-identical to the upstream fork point again:
`config/api_router.py`, `care/emr/apps.py`, `care/emr/extensions/__init__.py`,
`care/emr/tasks/__init__.py`, `care/security/authorization/encounter.py`,
`care/security/authorization/questionnaire_response_template.py`.

## The one seam (`config/urls.py`)

Upstream mounts a plug's `urls` at `api/<plug>/`. Four lines extend this: if a plug
ships a `v1_urls` module it is included under `api/v1/` as well, so the 28 frontend
call sites keep their addresses. Only paths core does not define may live there.
Candidate upstream PR.

## What deliberately stays in core (this phase)

- **Models and migrations.** All tables stay in `care.emr`; no schema change.
  Moving them is Phase 2 (plan: keep `db_table`, state-only migrations, relabel
  content types, rehearse on the isolated test stack first). Blocked until the
  generated-PDF artifacts get a plug-owned table, because `ReportUpload` has a
  foreign key to the custom letter-revision model.
- **Mixins that core viewsets import** (`admission_documentation`,
  `emergency_admission`, `doctor_activation`, `clinical_no_store`,
  `operation_plan`): they are part of core patches and move with them or not at all.
- **`PatientAccess.find_roles_on_patient`** (completed-encounter access): it cannot
  be a plug override because `care/emr/resources/permissions.py` instantiates
  `PatientAccess` directly.
- **Write-time invariants** in eight core viewsets and the constraints on core
  tables: no upstream hook exists (see `docs/development/*-core-patch.md`).
- **Settings** in `config/settings/*`: env-driven already.

## Verification

```bash
make test path="care_suriname care.security.tests care.users.tests config.settings.tests care.emr.tests"
```

Known order-dependent failure (pre-existing, 18 September 2026): in the serial run of
the 20 modules above, `test_encounter_discharge_concurrency.
test_two_distinct_commands_serialize_to_one_discharge` errors with
`IntegrityError ... emr_patientidentifier ... fk_emr_patie` raised inside its
`ThreadPoolExecutor` worker; it passes alone and with its sibling module. The identical
command at tag `backup/pre-plug-app-2026-09-18` (before the plug app existed) produces
the same error, so it is a test-isolation issue in that test, not an effect of the move.

Note: `make test` reuses parallel clone databases (`--keepdb --parallel`). Clones are
not re-migrated, so after new migrations they report `relation ... does not exist`;
run once without `--parallel` or drop the clones.

## Rollback

`git checkout backup/pre-plug-app-2026-09-18 -- .` restores the previous layout;
no database action is needed for Phase 1.
