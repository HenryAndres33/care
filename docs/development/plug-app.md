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

- **Columns and constraints on CARE's own tables** (the nine schema migrations
  0078/0079/0080/0081/0084/0086/0091/0093/0096, plus 0107): these stay in `emr`
  for good; there is nowhere else they can live.

## Phase 2 step 1 (18 September 2026): the artifact link now points custom → core

`ReportUpload.correspondence_revision` (a core table pointing at a plug model)
is gone. The link is `CorrespondenceLetterRevision.final_artifact`, a one-to-one
field on the custom revision table pointing at CARE's `ReportUpload`, which
remains the artifact store: same rows, same IDs, same download endpoints, so the
frontend is unchanged. Migration `emr.0107_letter_artifact_link_on_revision`
adds the column, copies the 33 existing links, drops the old column and its
unique constraint, and merges the two "generated artifact" branches of the
provenance check constraint (a generated artifact is now recognised by
`generated_at`, which also drives the immutability guard and the archive
refusal). It is reversible; the reverse path copies the links back before the
old constraints are re-created.

Because revisions are append-only, a finalized revision is inserted *after* its
artifact row, already carrying the link (`_create_revision(commit=False)` +
`_insert_revision_with_artifact` in the letter viewset; the correction viewset
uses the same pair).

Rehearsal tooling: `scripts/phase2/rehearse-migration.sh restored <dump>` and
`... empty` against the isolated test stack, with `scripts/phase2/verify_state.py`
asserting column presence, 33/33 links, no dangling or duplicate links,
provenance equality and constraint names. Both passed on 18 September 2026 with
the pre-phase2 dump (forward, back, forward) and from an empty database.

Test fix in passing: `test_correspondence_correction_migration` pinned "latest"
to migration 0090 and left the shared test database there for every test that
ran after it (the cause of the "relation does not exist" noise seen in `make
test`); it now returns to the graph's real leaf, and its fixture grants the
questionnaire-submit permission the tightened authorization requires.

**Caution for the laptop dev stack:** `scripts/celery-dev.sh` runs `migrate` on
start, so restarting the `celery` service applies pending migrations without
asking.
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

## Phase 2 step 2 (18 September 2026): the 34 models move to the plug, state only

**Inventory.** 34 model classes (13 modules under the former `care/emr/models/`
plus `FormSubmissionCommand` from `questionnaire.py` and
`FormSubmissionArtifactCommand` from `report/report_upload.py`), 34 tables, 34
owned sequences, no many-to-many through tables, no generic relations. All now
live in `care_suriname/models/` with `db_table` pinned to the existing `emr_*`
name, so every auto-derived index and constraint name is unchanged.

**Migrations, none deleted.** The applied `emr` migrations 0078..0107 are
untouched and still create the tables on an empty database. Two new
state-only migrations do the bookkeeping:

- `emr.0108_move_models_to_care_suriname`: `SeparateDatabaseAndState` with the
  autodetector's RemoveField/RemoveConstraint/RemoveIndex/DeleteModel operations
  as state operations and **no** database operations.
- `care_suriname.0001_move_models_to_care_suriname` (depends on `emr.0108`):
  the matching CreateModel/AddField/AddConstraint/AddIndex operations as state
  operations, no database operations, then one `RunPython` that relabels the 34
  content-type rows from `emr` to `care_suriname` **in place** (IDs and the 136
  attached Django permissions survive). It fails closed: a duplicate row, or a
  row already present under both labels, raises `ContentTypeRelabelError` and
  the transaction rolls back with 0001 not recorded. Reverse relabels back.

`sqlmigrate` prints no SQL for either; `makemigrations --check` is clean. The
dependency direction is plug → core only (the plug's 0001 depends on `emr`).

**Exact sequence a database at emr 0106 (the server) receives:**
`emr.0107` → `emr.0108` → `care_suriname.0001`. The rehearsal asserts this plan
textually before applying it.

**Rehearsal (`scripts/phase2/rehearse-step2.sh`, isolated stack only):**
1. `restored <dump at 0106>`: restore; assert the plan above; apply 0107;
   snapshot (34 row counts, the full list of `emr_*` tables, the 34 content-type
   rows, the 136 permission rows) and `pg_dump --schema-only`; apply 0108 +
   0001; assert the schema dump is **byte-identical** (only pg_dump's per-run
   session token filtered), row counts and table list unchanged, exactly one
   content type per model and under `care_suriname`, none left under `emr`,
   content-type IDs preserved, permission rows identical, `create_contenttypes`
   + `create_permissions` create nothing, all 34 resolve via
   `apps.get_model("care_suriname", …)` on their `emr_*` table and none via
   `emr`, audit-log exclusion resolves the new labels, no stale content types;
   migrate back to 0107 and assert the mirror image (schema identical again);
   insert a stray `care_suriname` content type and assert the forward migration
   aborts with `ContentTypeRelabelError` and is not recorded; remove it and
   migrate forward again with the full assertion set.
2. `empty`: migrate from zero, same final assertions.

Both passed on 18 September 2026 against the pre-phase2 dump.

**Code that still imports these models from core files** (the form-submission
and encounter viewsets, the correspondence services under `care/emr/`) now
imports from `care_suriname.models`; import lines only. Settings
`AUDIT_LOG_DOMAIN_LEDGER_MODELS` uses the new labels.

**Operator runbook (laptop, then server):**
```bash
bash scripts/care-suriname-backup.sh                     # database + MinIO, first
python manage.py showmigrations --plan | grep '\[ \]'     # expect 0108, 0001 (server: 0107 first)
python manage.py migrate --noinput
python scripts/phase2/verify_state.py snapshot           # only meaningful BEFORE migrate; see rehearsal
```
On a live database use the rehearsal order: `snapshot` before `migrate`, then
`verify_state.py moved` after. Rollback: `migrate care_suriname zero && migrate
emr 0107` together with checking out the previous commit (the code must roll
back with the state), or restore the backup set.

**Caution:** rolling back the migrations while keeping the new code running lets
`post_migrate` create `care_suriname` content types, after which a later forward
migration fails closed by design. Roll code and state back together.

## Phase 2 step 3 (19 September 2026): the remaining custom code modules move, no schema

76 files moved with `git mv` into mirrored paths under `care_suriname/`: the
correspondence services (`correspondence/`), the PDF and letter renderers and
their documents (`reports/`), the custom resources and specs (`resources/`, with
`condition_idempotency.py` and `medication_request_idempotency.py` flattened),
the clinical-text catalogs and their fixture CSVs, the correspondence Celery
tasks (`tasks/` is now a package whose `__init__` still registers the four
scans), `workflow_capabilities.py`, `staff_activation.py`, and the five mixins
core viewsets import (`api/viewsets/`). 70 files had dotted imports rewritten;
no migration imports any of them, so migration history is unaffected.

Celery task names follow the module path and therefore changed from
`care.emr.tasks.correspondence_*` to `care_suriname.tasks.correspondence_*`.
Nothing enqueues by name string, and the delivery/correction design is
outbox-based with 60 s rescans, so a message in flight across the deploy
restart is re-driven rather than lost.

Deliberately left in core, with the reason:
- `care/emr/resources/form_submission/{commands,note_labs,note_lab_text}.py`
  and `NOTE_LABS.md`: another agent has uncommitted work in them; move after
  that lands.
- `care/users/draft_recovery/` and `config/draft_recovery_auth.py`: the key
  model lives in the `users` app (migration users/0028); moving it is a second
  state-only relocation of one table, separate decision.
- The import lines in the patched core files (`form_submission.py`,
  `encounter.py`, `booking.py`, `user.py`, `medication_request.py`,
  `condition.py`, `schedule.py`, `report/template.py`, `valueset.py`): the
  patches themselves stay; only their imports now point at the plug.

After step 3 the custom code under `care/` is those two exceptions plus the
core patches documented in `docs/development/*-core-patch.md`.
