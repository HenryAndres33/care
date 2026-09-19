# The `care_suriname` plug app

**Since:** 18 September 2026 (Phase 1, code only). **Backup before the change:**
tag `backup/pre-plug-app-2026-09-18`, `output/backups/care-pre-plug-app-2026-09-18/`
(laptop database dump included).

## Current closure status — 19 September 2026

**Custom model ownership is complete; full implementation separation is not.**
The eight identified implementation groups have been extracted, including all
six form/artifact commands. The fresh audit identifies one remaining custom
implementation: completed-department role policy in native PatientAccess.
There are 35 plugin models and 10 native
production files importing the plugin (12 statements). Native safety, generic
seams, configuration and immutable migration history remain documented.

See the [current decision and verification](2026-09-19-post-extraction-ownership-audit.md)
and [exact path/hunk inventory](2026-09-19-post-extraction-ownership-hunks.md).
These supersede broad closure statements below without rewriting prior evidence.

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
| `tasks/__init__.py` (four 60 s correspondence scans) | `care/emr/tasks/__init__.py` |
| `checks.py` (deploy checks E001 to E007) | `care_suriname/checks.py`, imported from `care/emr/apps.py` |
| `extensions/encounter_admission_note.py` | `care/emr/extensions/` |
| `authorization.py` (three `can_*` methods, registered as handlers) | `care/security/authorization/{patient,encounter,questionnaire_response_template}.py` |
| `management/commands/*` (five commands, names unchanged) | `care/emr/management/commands`, `care/users/management/commands` |

Seven core files are byte-identical to the upstream fork point again:
`config/api_router.py`, `care/emr/apps.py`, `care/emr/extensions/__init__.py`,
`care/emr/tasks/__init__.py`, `care/security/authorization/encounter.py`,
`care/security/authorization/questionnaire_response_template.py`,
`care/users/models.py`.

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
  `operation_plan`): implementation is plugin-owned, but core attaches these
  mixins. The final audit supersedes the earlier all-or-nothing claim: explicit
  plugin routes may replace custom action mixins using the existing v1 seam,
  subject to route/lookup/permission proof. Native safety vetoes remain necessary.
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


## Final note-lab extraction — 19 September 2026

The step-3 held-file exception above is historical and is now closed. The four
files moved with `git mv` to `care_suriname/resources/form_submission/`:
`commands.py`, `note_labs.py`, `note_lab_text.py`, `NOTE_LABS.md`. Three custom
note-lab test modules also moved to `care_suriname/tests/`. Existing dirty v3
compact/unknown-date changes were preserved; two reproduced parser defects were
fixed to match frontend `f6b8953c` (legacy heading and malformed-row rejection).

The core `care/emr/api/viewsets/form_submission.py` remains an intentional safety
patch. Its two imports now resolve directly to the plug; the transaction,
authorization, expected-version and command call sites are unchanged. There is
no safe registration hook that can enforce these atomic invariants outside that
native command transaction. A future generic transactional hook is an upstream
candidate, not an implemented seam. The set of exceptional native production
files has not expanded. Plugin callers and adjacent core tests import the new
paths directly; no compatibility module remains at the old paths.

No migration, model, content type, Celery task name, API URL or draft-recovery
change. The users-app draft-recovery decision and the enumerated native safety
patches remain separate. Verification, limitations and handoff:
[2026-09-19-note-lab-extraction.md](2026-09-19-note-lab-extraction.md).

Inventory correction: the abbreviated step-3 list omitted the already-existing
`care/emr/api/viewsets/report/report_upload.py` import of the clinical no-store
mixin. The complete unchanged production-core import file set is ten:
`api/viewsets/{condition,encounter,form_submission,medication_request,user,valueset}.py`,
`api/viewsets/scheduling/{booking,schedule}.py`,
`api/viewsets/report/report_upload.py`, and `models/report/template.py`
(all under `care/emr/`). The report no-store safeguard is an existing patch,
not a dependency introduced by the note-lab extraction.


## Draft-recovery ownership decision — 19 September 2026

The earlier users-app exception is closed: custom commit `530eafb9e` introduced
this contract for Urology's encrypted browser drafts; it is absent at upstream
fork `ece71a878` and has no native frontend consumer. It may be useful upstream,
but that does not make this implementation native CARE. Implementation, auth
helper, README and security tests now belong to `care_suriname`; no core shim.
`care/users/models.py` is byte-identical to the fork again.

`users.0028` stays immutable. New `users.0029` removes model state with no SQL;
`care_suriname.0002` depends on it and plugin0001, recreates state with
`db_table="users_draftrecoverykey"`, then relabels the existing content type in
place. Dependencies point plugin → core only. No schema SQL, table rename, row
copy, cryptographic/AAD change, API or frontend change. Permission IDs/links are
preserved; their app-qualified names now use care_suriname. Missing installed
identity and any target identity fail closed; a fresh database with no content
types or keys defers creation to post_migrate.

Two owner-authorized, existing authentication-proof patches now import the
plugin helper directly: `config/auth_views.py` (successful password login) and
`care/emr/utils/mfa.py` (successful MFA). Neither has a shared generic interactive
authentication hook. Removing either would lock users out of their encrypted
drafts or weaken recency proof; inventing a wide auth seam is outside this move.
An upstream post-auth token-claims hook is a future candidate, not implemented.
The guard requires exactly these two native draft-recovery consumers. Settings
retain the secret-file/recent-auth configuration and use the plugin model label
for audit-value exclusion so key ciphertext is not logged.

The complete intentional production core→plugin import file list is now:

- `care/emr/api/viewsets/condition.py`
- `care/emr/api/viewsets/encounter.py`
- `care/emr/api/viewsets/form_submission.py`
- `care/emr/api/viewsets/medication_request.py`
- `care/emr/api/viewsets/report/report_upload.py`
- `care/emr/api/viewsets/scheduling/booking.py`
- `care/emr/api/viewsets/scheduling/schedule.py`
- `care/emr/api/viewsets/user.py`
- `care/emr/api/viewsets/valueset.py`
- `care/emr/models/report/template.py`
- `care/emr/utils/mfa.py`
- `config/auth_views.py`

The first ten remain the documented clinical safety/compatibility patches.
Native table invariants and immutable migration history, completed-encounter
patient access, user preferences/directory behavior, configuration and the generic
v1 registration seam also remain; this is not an unmodified CARE checkout.
The two auth imports replace existing custom-helper calls, not new behavior.

Exact rehearsal, backup, laptop application, test and endpoint evidence:
[2026-09-19-draft-recovery-ownership.md](2026-09-19-draft-recovery-ownership.md).
No production VM or deployment change. Roll back code and migration state together
as documented in [the module README](../../care_suriname/draft_recovery/README.md).


## Final closure audit correction — 19 September 2026

The [dated final audit](2026-09-19-final-backend-separation-audit.md) distinguishes
native safety from remaining custom implementation. In particular the first ten
imports are not uniformly unavoidable: several support separable custom actions,
and form-submission/diagnosis/medication command orchestration still lives in
native viewsets. The directory response/request types and endpoint, local
terminology expansion policy, recent-patient schema, Urology clinical-domain
vocabulary and two plugin-only constraint constants remain ownership work.
No runtime patch was removed in the audit. Existing immutable native migrations
remain historical; current custom model state and future custom model migrations
belong to the plugin. Native-table changes still require native migrations.

The new database-free `care_suriname.tests.test_backend_ownership` guard pins the
reviewed import boundary and checks all 35 model/table identities and all native
apps for forward model relations into the plugin. Together with registration,
note-lab and draft-recovery ownership checks, the scoped gate has 16 tests. This
is a bounded ownership gate, not proof that all remaining behavior is separated.

## First behavior-separation batch — 19 September 2026

The directory endpoint/request/response/pagination and its four original tests
now belong to `care_suriname`; native `resources/patient/spec.py` is byte-identical
to fork `ece71a878` again. Native `PatientViewSet` retains only its generic birth-date
filter delta (+2/−1), with native detail/update/DELETE untouched. Both command-only
constraint constants moved into their plugin command model modules; strings,
constraints and migration state are unchanged.

A routing assumption in the final audit was incomplete: removing the custom
patient action alone lets native `patient/<external_id>` capture “directory”
before ordinary plugin URLs. The owner authorized `plugs.urls.with_priority_routes`
and an optional `priority_urlpatterns` export from each plug's `v1_urls`.
Only flat, literal path() entries may opt in before parameter routes. Duplicate
literal paths, reverse names or collisions with exact existing URLs fail closed;
parameterized, regex or nested priority declarations are rejected. With no opt-in,
existing routing order is unchanged. DEBUG format aliases preserve native router
behavior. The one opted-in path is declared only in the plugin. Middleware,
authentication and authorization are unchanged. This is a generic upstream seam,
not permission to override exact host routes or add a plugin catch-all.

Current native care/config non-test Python inventory: **37 files, 173 hunks,
+3,071/−225**; root generic `plugs/urls.py` adds 119 lines outside that count.
The existing root plugin wiring and test settings remain separately counted.
Core→plugin imports remain **12 files / 28 statements**. Eight restored native
files now match the fork. F-category file incidence falls **12 → 8**; two of the
eight enumerated implementation clusters are closed. This is 25% of that finite
closure backlog, not a claim that the whole backend is only 25% or already 100%
separated. Custom model ownership remains 35/35. Full evidence, known baseline
failure and remaining scope: [batch report](2026-09-19-directory-and-constraint-ownership.md).

## Policy separation batch — 19 September 2026

Terminology expansion policy, recent-patient schema, base/local/test required-form
maps and clinical-domain enum now belong to `care_suriname/policies`, exported by
model-free `care_suriname.contributions`. Generic `plugs.contributions` provides
conflict-checked single callbacks/types, additive schema maps and JSON settings
contributions. Native value-set authentication/object lookup/search fallback stay
native. Native Condition's open column and persisted `general` default remain;
no model choices or plugin model import were added. Settings initialization and
all environment/profile precedence match the baseline. No migration.

The direct valueset import is removed: **11 native files / 27 statements** remain.
Current native care/config non-test Python inventory is **37 files, 179 hunks,
+3,004/−249** versus fork `ece71a878`. The changed hunk count reflects generic seams,
not more custom policy. Generic `plugs/contributions.py` is counted separately,
as are `plugs/urls.py` and root `plug_config.py`.

Three more of the eight finite behavior-separation backlog clusters are closed:
**5/8 (62.5%)**, not a weighted overall completion percentage. Remaining F groups:
form/artifact command orchestration, diagnosis commands, medication commands
(three native viewsets). Native metadata, historical migrations, safety imports,
authorization safeguards and deployment configuration still remain intentional.
No additional native file became byte-identical to fork in this batch; eight
previously restored files remain identical. Full backend behavior separation is
still incomplete. See [exact evidence and limitations](2026-09-19-policy-ownership.md).

## Diagnosis action contribution — 19 September 2026

`plugs/viewset_actions.py` is a generic optional action contribution decorator.
It allows a single lazy plain-class provider, forbids overriding host methods,
rejects nonliteral action paths and duplicate/reserved reverse names, and leaves
the native class identical when no provider exists. DRF owns the router ordering,
parent parameters, authentication, schema and aliases. The diagnosis command
uses this seam; its implementation no longer lives in native CARE. This is an
upstream PR candidate, not a specialty-specific priority rule. See the
[diagnosis report](2026-09-19-diagnosis-command-ownership.md).

## Medication action contribution — 19 September 2026

Both medication create/reconcile commands use the existing additive action seam.
Only plugin orchestration moved; native prescription resolution is shared by
legacy CRUD and remains generic. The native no-store mixin import remains an
intentional response-safety exception, protecting native endpoints too. Direct
plugin imports fall 10 files/26 statements to10/24. No model/migration/schema or
route precedence change. See the [batch report](2026-09-19-medication-command-ownership.md).

## Form/artifact action contribution — 19 September 2026

Six commands now register from plugin-owned plain method classes. The generic
seam accepts a class or nonempty tuple of plain classes and private static
helpers; it rejects duplicate helper names as well as host overrides and route
collisions. No provider still returns the original host class. No routing change.
Native form viewset shrinks 1589→264 lines, retaining CRUD/auth/locking/version and
immutability guards plus no-store. Direct imports fall 10 files/24 statements to
10/12. See [evidence](2026-09-19-form-command-ownership.md). A fresh closure audit
follows; backlog completion alone is not proof of full source separation.

## Fresh post-group-8 audit correction — 19 September 2026

All eight enumerated extraction groups are complete at 347517ddb, but full source
ownership is **not 100%**: the completed-encounter department role policy still
lives in native PatientAccess. The earlier B-only classification understated this
custom read-access implementation; it is now B/F. Current native source metrics:
37 files, 174 hunks, +1,320/−249; direct plugin imports: ten files/12 statements.
Four action-mixin imports may now use the existing generic seam; they are not
proven unavoidable. Historical figures above remain revision-pinned evidence.
See the [fresh decision](2026-09-19-post-extraction-ownership-audit.md) and
[current exact inventory](2026-09-19-post-extraction-ownership-hunks.md).
