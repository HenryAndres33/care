# Final backend separation audit — 19 September 2026

## Decision

**Not 100% complete under the full implementation-separation definition.** The
35 custom models, their current app state, and the extracted standalone modules
are plugin-owned. Native CARE still implements custom patient-directory,
idempotent-command, terminology and preference behavior. Calling all of this
“unavoidable safety hooks” would conceal remaining work. This audit changes only
documentation and database-free ownership tests; it does not remove any safeguard.

Completion means: custom domain implementations and custom model state belong to
`care_suriname`; native differences are individually justified native-table/write
safety guards, narrowly necessary integrations, generic upstream candidates,
configuration and immutable applied migration history. Custom endpoint engines or
Urology policy embedded in native classes are **not** an accepted safety exception.
The custom-model/standalone-module milestone meets its narrower definition;
the comprehensive behavior-separation milestone does not.

## Reproducible scope and metrics

- Audited HEAD: `db5051c64368310fcf8622cb119a1d541e3ec513` on
  `codex/suriname-clinical-workflows`; worktree initially clean.
- Actual fork: `ece71a878b3764a476d713a163a2f5515db57581`, computed with
  `git merge-base HEAD origin/develop` (not the frontend's different fork).
- Cached upstream: `origin/develop` =
  `a749b92794ac175db8839d3d75ca36402a196282`, committed 8 September 2026.
  No claim that this cached ref is the latest remote upstream.
- Push destination: `henry-fork` (`git@github.com:HenryAndres33/care.git`), explicit
  `HEAD:refs/heads/codex/suriname-clinical-workflows`.
- **38 changed non-test Python files under care/config, 178 hunks, +3,149/−226.**
  This includes local development configuration, identified separately below.
  Adding `config/settings/test.py` and root `plug_config.py` gives **40 files,
  +3,173/−227**. Deployment/build/scripts are outside this native Python count.
- **12 native production import files, 28 import statements** referencing the
  plugin. Ten before draft recovery; MFA and password authentication add two.
- Seven former registration/model-import files are byte-identical to the fork:
  `config/api_router.py`, `care/emr/apps.py`, `care/emr/extensions/__init__.py`,
  `care/emr/tasks/__init__.py`, `care/security/authorization/encounter.py`,
  `care/security/authorization/questionnaire_response_template.py`,
  `care/users/models.py`.

Commands used: `git status --short`, `git rev-parse HEAD`, `git remote -v`,
`git merge-base HEAD origin/develop`, `git log`, `git show REV:path`,
`git diff --numstat BASE HEAD`, `git diff --unified=0 BASE HEAD -- PATH`,
`rg` ownership/consumer scans, and Python AST scans of imports and definitions
introduced since the fork. Pinned full revisions above make all hunk coordinates
reproducible independently of this documentation commit.

[The exact inventory](2026-09-19-final-backend-separation-hunks.md) lists every
native source path, functional intent, disposition, addition/deletion count and
unified-zero hunk header, plus every other non-plugin changed path. It also gives
all 28 plugin import statements with line numbers. No environment values or
credentials are reproduced.

Categories overlap within files; they must not be added together as exclusive
file totals:

| Category | Native files containing such hunks | Disposition |
|---|---:|---|
| A: native clinical/data safety | 23 | Keep effective on every native write path; upstream transactional hooks could reduce patches |
| B: plugin integration | 13 | 12 import files plus direct PatientAccess policy; necessity differs by integration |
| C: generic upstream candidate | 20 | Review independent fixes separately from custom policy |
| D: config/non-production | 3 | base/config/local settings; test settings and root wiring counted separately |
| E: possible redundant patch | 1 | Audit-log branch has an existing generic alternative; not approved for removal |
| F: remaining custom implementation/policy | 12 | Outstanding ownership work, including mixed native model/spec/config files |

## Remaining implementation, with concrete counterexamples

| Native path and HEAD lines | Remaining custom behavior | Next boundary and risk |
|---|---|---|
| `care/emr/api/viewsets/patient.py:51,213,232`; `care/emr/resources/patient/spec.py:235` | Directory pagination, request DTO, identity-only response and search action | Plugin-owned explicit v1 route + DTO; keep URL, permissions, stable ordering, name minimum and pagination. Low/medium scope; read authorization must be proven |
| `care/emr/api/viewsets/form_submission.py:280–1538` | Six custom command actions (artifact is one of six), replay/series/ledger orchestration, artifact rendering/upload compensation and Urology validation wrapper | Plugin command service/actions with original transaction boundary; keep legacy write vetoes. High risk; not an import-only move |
| `care/emr/api/viewsets/condition.py:168,218` | Custom diagnosis idempotent command execution | Plugin command action/service; native row constraints and authorization remain. Medium/high risk |
| `care/emr/api/viewsets/medication_request.py:171–385` | Custom idempotent creation/reconciliation, prescription side-effect and replay handling | Plugin command orchestration; retain native closed-encounter locks and constraints. High risk |
| `care/emr/api/viewsets/valueset.py:43,82` | Local concept-kind slugs, nl/nl-SR locale policy, approved translation merge/deduplication | Plugin policy behind a narrow generic expansion hook. Existing expand has no hook; not all of its current logic must remain native |
| `config/settings/config.py:325`; `config/settings/base.py:71` | Urology recent-patient preference schema and department-to-required-form policy | Existing configurable schema/settings are a possible boundary; validate defaults/overrides and bounded payload behavior. No removal in this audit |
| `care/emr/resources/condition/spec.py:48`; `care/emr/models/condition.py` | Urology clinical-domain vocabulary/field on native Condition | Mixed native-table contract; do not duplicate the model or silently change payloads. Requires a separate compatibility decision |
| `care/emr/models/questionnaire.py:13`; `care/emr/models/report/report_upload.py:14` | Plugin-only command constraint-name constants | Move into their plugin command-model boundary, preserving exact constraint strings. Low risk; currently live imports, not dead code |

These counterexamples disprove “no custom class/service/command remains under
care/config.” In particular the added `PatientDirectoryPagination`, nested
`DirectoryRequestSpec`, `PatientDirectorySpec`, `ClinicalDomainChoices`, and
form-command exception/helper classes still exist. New native model `Meta`
constraints and `FormSubmissionMutableSpec` are native-table contracts, not new
standalone custom models. AST comparison and consumer review found no additional
new standalone production Python modules under care/config; substantial additions
inside existing native files remain. Static scans cannot prove the absence of
arbitrary dynamic imports or semantic policy by names alone.

## Why the 12 import files remain, and possible exits

| Native file (under care/emr unless otherwise shown) | Current dependency | Potential exit |
|---|---|---|
| `api/viewsets/condition.py` | Plugin request/hash for native custom action | Move command action; no new hook necessarily required |
| `api/viewsets/encounter.py` | Admission mixins; ConsultClosure terminal restart veto | Remount custom actions using existing v1 registration; native restart veto needs a generic lifecycle/transition hook |
| `api/viewsets/form_submission.py` | Command/spec/model, correction, note-lab, artifact and workflow helpers | Move engine separately; preserve native transactional safety and legacy-write checks |
| `api/viewsets/medication_request.py` | No-store, command specs/hash and workflow gate | Move custom actions; generic no-store mechanism could remove mixin import |
| `api/viewsets/report/report_upload.py` | Clinical no-store response mixin | Generic response cache policy; retain generated-artifact archive safeguard |
| `api/viewsets/scheduling/booking.py` | OperationPlanMixin | Explicit plugin action routes are feasible in principle; prove lookup/permissions and route precedence |
| `api/viewsets/scheduling/schedule.py` | Overlap validation inside native locked writes | Generic resource transaction/validation hook or upstream overlap feature |
| `api/viewsets/user.py` | DoctorActivationMixin | Explicit plugin clinical-activation action with exact username lookup/auth parity |
| `api/viewsets/valueset.py` | Translation resolver/search | Generic expansion hook plus plugin-owned locale/merge policy |
| `models/report/template.py` | Content hashing during native save | Upstream versioned-template hashing implementation/hook |
| `utils/mfa.py` | Interactive authentication-proof token | Shared post-auth token-claims hook; removing call breaks draft recency proof |
| `config/auth_views.py` | Same proof for successful password login | Same missing auth hook |

The admission/documentation, doctor activation and operation-plan implementations
already live in the plugin; their route attachment remains a core mixin. It is
**not proven unavoidable**: the existing generic v1 mount can register explicit
custom action URLs once duplicate native actions are removed. It cannot override
native routes: native URLs are included first. Remounting must preserve lookup,
queryset, permissions and middleware behavior. This audit does not implement or
runtime-certify that design.

Completed-encounter patient department access is another native policy patch,
without a direct plugin import: native permissions instantiate `PatientAccess`
directly. A registration override alone cannot replace it. The original “ten
imports” is historical, not the present count; import count alone also misses
native-only custom implementations such as the directory.

## What is owned by the plugin now

Runtime app-registry checks confirm **35 models**: 34 on their existing `emr_*`
tables and `DraftRecoveryKey` on `users_draftrecoverykey`. None is registered under
its old app. No field declared by any native `care.*` model (including users and
many-to-many fields) points into the plugin. Reverse relations from plugin models
to native patient/encounter/user/report resources are expected.

The plugin owns correspondence services/viewsets/reporting, closures, note-lab
parsers/commands, draft recovery and authentication helper, custom model modules,
workflow capability service, five management commands, three registered
authorization methods, the encounter extension and checks. `apps.py.ready()`
registers checks/extension/authorization. `tasks/__init__.py` connects four 60-second
correspondence scans; task implementations are in two plugin task modules.
`v1_urls.py` owns the extracted APIs and explicit encounter actions. Not every
custom API is there: the native actions enumerated above are outstanding.
`plug_config.py` explicitly includes the local app; an empty external `plugs` list
does **not** disable it. Plugin-absent runtime support is not established or tested.

The note-lab and draft-recovery ownership tests verify old implementation paths
are absent with no native compatibility shims. Existing registration tests verify
representative URL resolution, the three authorization handlers and all 34 earlier
model identities. The new guard extends this to all 35 models, all native app
relations, and the exact reviewed set of imported plugin modules per native file.
It bounds debt; it does not bless every dependency as permanent.

## Migration reconciliation

**33 historical native migration files** remain relative to the fork:
`emr.0078` through `emr.0108` (31), and `users.0028` / `users.0029` (2).
The appendix gives each exact filename. Earlier migrations created custom tables
and native field/invariant changes; they remain immutable applied history.

- `emr.0108` removes the 34 custom model states with
  `SeparateDatabaseAndState(database_operations=[])`.
- `care_suriname.0001` depends on emr0108, the historical facility leaf and the
  swappable user model; it adopts the existing 34 tables in state and relabels
  content types. Its state operation has no schema SQL; its RunPython does change
  content-type metadata, so “no database operation at all” would be wrong.
- `users.0029` depends on users0028 and removes DraftRecoveryKey state only.
  `care_suriname.0002` depends on users0029, plugin0001 and contenttypes0002;
  state-only adoption pins the old table, followed by content-type relabelling.
- Handoff dependencies point plugin → native, not native → plugin. Future custom
  model migrations belong to the plugin. Future changes to native-table columns
  and constraints still belong to their native app, with documented justification.
- Read-only laptop migration listing shows emr0108, users0029 and plugin0001/0002
  applied. `makemigrations --check --dry-run` reports **No changes detected**.
  No migration was created, applied, reversed or replayed during this audit.

Prior rehearsal/content-type/permission/backup evidence remains in the dated
[note-lab](2026-09-19-note-lab-extraction.md) and
[draft-recovery](2026-09-19-draft-recovery-ownership.md) reports. It is historical
evidence, not a rehearsal rerun here. Upstream upgrades must reconcile migration
graph divergence; do not renumber or rewrite already-applied historical migrations
as a shortcut.

## Corrections to earlier descriptions and upstream candidates

The original 18 September audit correctly identified the directory, custom
command actions and preference policy as separable. Extraction of their helpers
or tables did not close those implementation gaps. The old statement that code
under care is only two held exceptions plus safety patches is too broad. The
note-lab report describes its own extraction accurately, but treating all 1,511
added form-viewset lines as necessary safety hooks is inaccurate. Its six command
actions include artifact generation; it is not “six plus an artifact action.”

The plug guide's old tasks.py path is now tasks/__init__.py. The blanket statement
that mixins must move together with core patches is not established. Current
model count is 35, restored-file count seven, and core import count 12. Dated
historical counts in prior reports remain preserved with this explicit correction.

Generic upstream candidates: extension list rendering, structured-resource model
lookup, adjacent-slot overlap handling, locking/atomicity corrections, native
provenance/template version support, optional v1 plug routing, and a narrowly
specified post-auth token-claims hook. Review generic behavior separately from
Urology-specific policy. The token authorize_destroy recursion fix is already in
cached origin/develop `a749b9279`; avoid a duplicate PR. Audit exclusion may use the
existing `AUDIT_LOG.models.exclude.models` mechanism; the original audit cited
`globals.exclude.models`, which is the wrong configuration branch. Preserve
ciphertext and domain-ledger exclusion before removing any redundant branch.

## Verification and limitations

- 16 database-free ownership/registration tests pass, including three new guards.
  Django explicitly skips setup of the unused default database. No broad suite,
  migration replay, synthetic records, browser or write endpoint is needed for
  this docs/guard-only change.
- Read-only DB connections use `PGOPTIONS=-c default_transaction_read_only=on`.
- Scoped Ruff lint/format, Django system checks, migration drift, diff checks,
  staged secret scan and exact staged-tree checks are recorded in the final gate
  below. No runtime source, migration or configuration content is staged.
- Static guard scans Import/ImportFrom, not arbitrary constructed imports. Schema
  ownership is verified via Django metadata and migration state; row checksums and
  content-type IDs were not re-audited because this turn makes no DB change.
- Runtime regression suites from previous moves are not substituted for a browser
  or concurrency proof of any proposed future extraction. The remaining commands
  are high-risk specifically because of transaction, authorization and replay
  semantics. Existing broad-suite order dependence is documented in plug-app;
  that suite is not rerun by this read-only audit.

Recommended order: relocate the two constraint constants; prove and extract the
read-only patient directory; separate terminology/preference policy; prove explicit
plugin mixin route attachment; only then tackle diagnosis/medication/form command
orchestration with transaction and rollback tests. Submit independent generic fixes
upstream. Keep native write guards until an upstream hook actually covers every
bypass path. These are recommendations, not changes authorized by this audit.

### Final staged-tree gate

The four claimed paths were staged explicitly, then the complete index was
exported with `git checkout-index --all --prefix=/tmp/care-backend-closure-20260919/staged/`.
The export was checked byte-for-byte against indexed blobs. In a disposable
`care_local` container with that export mounted at /app, the combined gate ran:

```sh
ruff check --no-cache care_suriname/tests/test_backend_ownership.py
ruff format --no-cache --check care_suriname/tests/test_backend_ownership.py
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test care_suriname.tests.test_backend_ownership care_suriname.tests.test_plug_registration care_suriname.tests.test_note_lab_ownership care_suriname.tests.test_draft_recovery_ownership.DraftRecoveryOwnershipTests --noinput --verbosity 2
```

Result: lint/format pass, system check reports no issues, no migration drift,
**16/16 tests pass**, default database setup skipped. The content-type TestCase
class and all DB-writing tests were deliberately excluded. This uses an exact
index export in an isolated container, not the mutable working directory.
Working and staged diff checks and staged credential/private-key/token pattern
scans pass. All 1,345 unclaimed tracked backend file hashes match the pre-audit
snapshot. No runtime source or migration is in the index; no generated files or
scratch evidence are staged. No test failure required baseline reproduction.
The shared BUS claim/release is append-only in the frontend repository and is not
included in the backend commit. No service restart, migration operation, frontend
source change, clinical write, port-4000 change or deployment occurred.

## Follow-up correction: directory/constant batch — 19 September 2026

The [first implementation batch](2026-09-19-directory-and-constraint-ownership.md)
closes the directory and two command-constant clusters. The earlier inventory
above and its appendix remain pinned to db5051c64; do not rewrite those historical
hunks. Current care/config non-test Python deltas are 37 files / 173 hunks /
+3,071/−225, plus a new 119-line generic `plugs/urls.py` route-priority mechanism.
Patient spec is restored byte-for-byte; patient viewset retains only +2/−1 for the
generic birth-date filter. Core plugin imports remain 12 files / 28 statements.
F-category native file incidence is now eight rather than twelve.

Correction to the directory recommendation: an ordinary late v1 plugin path does
not win after removing the native action. The native patient detail regex accepts
“directory” as external_id. A runtime resolver probe proved this at fcfdba421; the
owner approved a narrow generic, opt-in literal-route priority seam with collision,
reverse/schema, authentication and native UUID/method guards. It preserves normal
plugin ordering and rejects exact-route replacement. No route is hardcoded in
core. The earlier assertion that an ordinary explicit v1 route alone could own
this URL was incomplete.

Remaining F clusters: form-submission command/artifact orchestration, diagnosis
commands, medication commands, local terminology expansion policy, Urology
preference/required-form configuration, and clinical-domain vocabulary coupled to
native Condition. Two of eight identified clusters are closed (25% of this audit
backlog); no defensible weighted overall completion percentage is established.
Full behavior separation remains incomplete. See the batch report for the
pre-existing cross-department authorization test failure reproduced at fcfdba421;
this extraction does not resolve or certify that unrelated policy.

## Subsequent policy batch correction — 19 September 2026

The earlier F entries for valueset expansion, recent-patient/required-document
settings and clinical-domain vocabulary are now closed by plugin policies plus
small generic contributions. Do not rewrite the historical hunk appendix.
Native Condition retains its existing table field/default and native specs retain
the field contract; these no longer define the specialty vocabulary. Local/test
UUID maps moved as well as the base map, preserving profile precedence.

Current care/config non-test source: 37 files, 179 hunks, +3004/−249. Native
core-to-plugin imports: 11 files/27 statements (valueset removed). F implementation
incidence falls 8 → 3 files: condition, form_submission and medication_request
viewsets. Five/eight explicit backlog clusters are closed (62.5% of that backlog);
100% behavior separation is not yet reached. Generic contribution infrastructure
outside care/config is separately accounted in the
[policy batch report](2026-09-19-policy-ownership.md), including the reproduced
baseline permission-test failure and synthetic browser audit artifact.

## Subsequent diagnosis-command correction — 19 September 2026

The command action and replay helper moved from native condition.py to plugin
ownership without changing their ASTs. The native host retains a generic action
decorator plus its existing CRUD/auth/write-safety methods. Native plugin imports
are now 10 files/26 statements. Native care/config non-test Python remains 37
files (config/settings/test.py is excluded consistently); current hunk metrics
and verification are in the [batch report](2026-09-19-diagnosis-command-ownership.md).
The earlier inventories remain historical evidence.

F implementation incidence is now two native viewsets: medication_request and
form_submission. Six/eight finite backlog clusters are closed (75% of that
backlog, not a weighted whole-backend percentage). Full separation remains
incomplete. The reproduced native diagnosis/symptom authorization failures are
unresolved and must not be read as certified safe by this extraction.

## Subsequent medication-command correction — 19 September 2026

Medication create/reconcile execution and command helpers are now plugin-owned,
using the existing generic additive action seam. Native CRUD/locks/authorization,
shared prescription resolution and no-store response policy remain unchanged.
No new direct plugin import or generic seam; imports 10/26→10/24.

Only form/artifact orchestration remains in the finite F backlog: 7/8 groups
closed (87.5% of this backlog, not a weighted whole-backend percentage). Historical
hunk inventories above remain pinned to their recorded revisions. The
[medication report](2026-09-19-medication-command-ownership.md) records exact current
metrics, baseline failures and verification. Ownership completion does not
certify unresolved baseline authorization behavior.

## Subsequent form/artifact extraction — 19 September 2026

The final enumerated F group is extracted: all six commands and their series,
ledger, specialty validation and artifact compensation now belong to
care_suriname/api/viewsets/form_commands. The native viewset retains generic
CRUD/write safety and no-store. Core imports are 10 files/12 statements.
The [batch report](2026-09-19-form-command-ownership.md) records exact gates and
baseline failures. All 8 finite backlog groups are addressed; a fresh audit of
every current native delta is still required before a 100% claim. Historical
inventories above remain evidence at their recorded revisions.
