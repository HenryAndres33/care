# Fresh backend ownership audit after group 8 — 19 September 2026

## Decision

**All eight planned extraction groups are complete; full source-ownership
separation is not yet 100%.** One feature-specific policy remains implemented
inside native CARE: completed-encounter department role lookup in
`care/security/authorization/patient.py:33–40`. This is a read-access expansion,
not a native-table write-safety guard. The earlier audit classified it B (plugin
integration) and omitted it from the eight-group F backlog. The fresh audit
corrects that classification to B/F rather than changing the completion definition.
No runtime authorization change is made by this audit.

Completion still means custom domain implementation and custom model state belong
to care_suriname; individually justified native-table/write safeguards, small
integration hooks, generic upstream candidates, deployment configuration and
immutable migration history may remain. Embedded specialty endpoint engines or
read-access policy are not accepted as safety exceptions. Eight/eight is 100% of
the finite original backlog, not a defensible percentage of all backend behavior.
The remaining policy needs a separate bounded extraction and permission review.

## Evidence and exact scope

Audited clean, pushed HEAD: `347517ddb34fc1a8170885e4b6b12af3f381114a`.
Actual fork from `git merge-base HEAD origin/develop`:
`ece71a878b3764a476d713a163a2f5515db57581`.
Cached origin/develop: `a749b92794ac175db8839d3d75ca36402a196282`; not claimed to
be the latest remote upstream. Branch: codex/suriname-clinical-workflows.
Push: explicit henry-fork HEAD:refs/heads/codex/suriname-clinical-workflows.

Commands: git status/rev-parse/merge-base/log/show, git diff --numstat and
--unified=0 FORK HEAD, AST Import/ImportFrom/ClassDef/FunctionDef scans,
rg consumer/module/feature-literal scans, Django app and migration-state inspection,
Ruff check/format, system check, makemigrations --check --dry-run, focused ownership
and registration tests, exact staged export, diff/secret/unclaimed-hash checks.
Every native production hunk was reviewed against the fork, not only touched
files from the last batch. The [current hunk inventory](2026-09-19-post-extraction-ownership-hunks.md)
records exact paths, added/deleted counts, zero-context hunk coordinates, dispositions,
all direct plugin imports, and every other non-plugin fork difference.

Native care/config non-test Python: **37 files, 174 hunks, +1,320/−249**.
Test settings, root plug_config.py and three generic plugs modules are listed
separately. Historical migrations, tests, docs and deployment files are not native
production implementation. Categories overlap; do not sum them as file totals.
Ten native import files contain **12 direct import statements** (down from the
original audit's 12 files/28 statements). Configuration strings and generic
contribution discovery are not counted as direct imports.

Eight former custom registration/type paths remain byte-identical to fork:
config/api_router.py; care/emr/apps.py; care/emr/extensions/__init__.py;
care/emr/tasks/__init__.py; care/security/authorization/encounter.py;
care/security/authorization/questionnaire_response_template.py; care/users/models.py;
care/emr/resources/patient/spec.py. No additional whole file was restored by group 8.

## Remaining custom policy and smallest next boundary

The native PatientAccess.find_roles_on_patient branch queries completed encounters,
collects their facility_organization_cache values and merges those departments into
the roles used for patient access. The default-enabled, environment-overridable
PATIENT_DEPARTMENT_LONGITUDINAL_ACCESS_ENABLED switch lives in config/settings/config.py.
Repository evidence: PATIENT_DEPARTMENT_ACCESS.md explicitly describes the owner’s
Suriname longitudinal-dossier requirement; commit fe0a59832 introduces it; the
branch is absent at fork. Five dedicated tests cover completed/other/no/cancelled
encounters and disabling the switch. This is custom policy despite generic-looking
ORM code and despite having no direct plugin import.

care_suriname/authorization.py deliberately excludes this behavior because
care/emr/resources/permissions.py directly instantiates PatientAccess (line 21).
Merely registering a replacement handler would leave permission serialization
inconsistent. A narrow generic role-contribution callback inside the common role
lookup can let the plugin supply completed-department IDs while preserving both
direct and controller callers. It must preserve permission boundaries, query/set
semantics, disabled behavior and all existing completed-encounter tests. Do not
remove or tighten access as an ownership cleanup. The authorization failures
reproduced in previous batches must remain separate regression evidence.

## What remains intentionally native

- Native-table constraints/fields and write-time safeguards: Condition,
  MedicationRequest, Encounter, FormSubmission, ReportUpload and Template;
  closed-encounter/device/location/booking/token locks and transition vetoes;
  immutable finalized forms/artifacts, optimistic version checks and matching
  serializer fields. These cover ordinary native writes, not just plugin commands.
- Generic fixes: date-of-birth filter, adjacent schedule interval comparison,
  encounter extension rendering, structured-resource model registry fallback,
  shared native prescription construction, user ProtectedError soft-delete fallback,
  eager-loaded report provenance, no-store response integration and token destroy
  recursion fix. The recursion fix is already present in cached origin/develop.
- Generic seams: optional collision-checked contribution registry/settings/preferences,
  valueset expansion callback, clinical-domain type contribution, literal plugin-v1
  route priority, and additive viewset action contributions. No specialty URL or
  implementation is hardcoded in these seams.
- Deployment/configuration: facility mutation/delivery gates, local synthetic mode,
  Paramaribo time zone, UTC Celery, draft-recovery settings, plugin wiring and
  audit exclusions. These remain configuration deltas under the definition;
  they are not a claim that the repository is upstream-identical or plugin-optional.
- Possible redundant safety patch: audit_log/helpers.py's domain-ledger exclusion
  can potentially use existing generic AUDIT_LOG model exclusions. No removal is
  justified without equivalent secret/clinical-value exclusion tests.

AST comparison finds only four added nested model Meta classes and the native
FormSubmissionMutableSpec base class among changed native production definitions.
No added standalone native production Python module remains. No native custom
idempotent action, directory DTO/pagination, Urology terminology engine, preference
schema, diagnosis/medication/form command engine, note-lab parser, draft-recovery
service, correspondence engine or custom model class remains. These negative
scans supplement full hunk review; names alone cannot prove arbitrary dynamic
behavior absent. The explicit department-policy counterexample prevents a blanket
"no custom implementation remains" statement.

## Exact direct-import exceptions and future removal

| Native file | Statements | Why it remains; possible boundary |
|---|---:|---|
| care/emr/api/viewsets/encounter.py | 3 | Admission/emergency action mixins are plugin-owned implementations mounted on native routes; existing generic action seam may replace their two imports after parity tests. ConsultClosure lookup is a locked veto on legacy restart; requires generic transition validation to remove safely. |
| care/emr/api/viewsets/form_submission.py | 1 | Clinical no-store mixin protects native CRUD and contributed commands; generic response/header hook or upstream no-store support needed. |
| care/emr/api/viewsets/medication_request.py | 1 | Same all-endpoint no-store requirement. |
| care/emr/api/viewsets/report/report_upload.py | 1 | Same no-store requirement for clinical artifact/report responses. |
| care/emr/api/viewsets/scheduling/booking.py | 1 | Plugin OperationPlanMixin mounts custom actions/helpers; now a candidate for the existing additive action seam, subject to route/host collision and permission tests. |
| care/emr/api/viewsets/scheduling/schedule.py | 1 | Plugin overlap validator is called while native resource rows are locked; a generic transactional schedule-validation hook could remove the import. |
| care/emr/api/viewsets/user.py | 1 | Plugin DoctorActivationMixin mounts an action; existing additive action seam is a plausible replacement after exact URL/schema/auth tests. |
| care/emr/models/report/template.py | 1 | Plugin content-hash helper runs on every native Template.save for provenance; generic version/hash lifecycle support could replace it. |
| care/emr/utils/mfa.py | 1 | Interactive-auth proof for draft recovery; needs common successful-interactive-auth token claim hook. |
| config/auth_views.py | 1 | Same proof for password login; same missing hook. |

These are **documented current integrations**, not ten demonstrably unavoidable
imports. In particular four action-mixin statements in three files now have a
credible generic replacement. Reducing coupling further is distinct from moving
implementation bodies, which already reside in the plugin. No broad seam or new
runtime refactor is introduced by this audit.

## Model, module and migration ownership

All **35 custom models** resolve under care_suriname.models and app label
care_suriname; 34 retain their physical emr_* table names, DraftRecoveryKey retains
users_draftrecoverykey. None resolves as a native emr/users model. Native model
fields have no forward relation to a plugin model; reverse relations are expected.
The plugin owns custom APIs/v1 URLs, services/resources, correspondence, closures,
commands/ledgers, tasks, checks, authorization extensions, note labs, draft recovery,
clinical vocabulary and preferences. Native Condition/MedicationRequest/FormSubmission/
ReportUpload tables and their generic safety metadata deliberately remain native.

Applied native history is immutable: emr 0078–0107 contains the original feature
DDL/data history; emr 0108 removes 34 model states with no database operations.
care_suriname 0001 depends on emr 0108, recreates those states with pinned db_table,
and relabels content types in place. users 0028 creates the original draft key;
users 0029 removes only state; care_suriname 0002 depends on users 0029 and plugin
0001, adopts the pinned table and relabels its content type. Dependencies are
one-directional. Current and future custom model state is plugin-owned.
No historical migration is edited, applied, reversed or replayed in this audit.
Content-type relabels were verified during their original rehearsals, not repeated
here. Runtime/app and migration-state ownership plus clean drift checks are the
current evidence; no new claim about historical backup restores is made.

## Verification and limitations

Exact staged gate: **58/58 tests pass**, with unused database setup skipped.
Scoped Ruff/format, Django system check, migration drift and diff checks pass.
Runtime app ownership and MigrationLoader(None) project state agree on all
35 plugin models and pinned tables; plugin leaf is 0002, emr 0108, users 0029.
The graph/state inspection performs no database access or migration replay.
Only docs and ownership guards change; no runtime source, model, route, migration or settings
value changes in this audit. The tests mechanically prohibit custom idempotent
actions returning anywhere under native care and bound the acknowledged department
policy consumer to its one reviewed file, in addition to existing import/model guards.
Full application correctness is not inferred from ownership tests. The group-8
[report](2026-09-19-form-command-ownership.md) records 226 tests (218 pass/eight
baseline failures), method/route/OpenAPI/schema parity and signed-in PDF evidence.
The synthetic finalized note/PDF remain because this browser cannot run the
frontend's native prompt() cleanup. No additional browser/data action in this audit.

Priority next steps: extract the one completed-department role policy through the
shared role lookup; separately assess the four removable action-mixin imports;
propose generic safety fixes/seams upstream. Resolve baseline authorization test
failures as their own behavior/security work, not by changing assertions during
ownership moves. A new final audit is required after the remaining policy move.

All 1,390 unclaimed tracked file hashes are unchanged; the index contains
only the seven claimed documentation/test paths, with no credentials, generated
artifacts, runtime sources or migration edits. No new failures.

## Exact audit change counts

| Path | Added | Removed |
|---|---:|---:|
| `care_suriname/README.md` | 8 | 0 |
| `care_suriname/tests/test_backend_ownership.py` | 36 | 1 |
| `docs/development/2026-09-19-final-backend-separation-audit.md` | 12 | 0 |
| `docs/development/2026-09-19-final-backend-separation-hunks.md` | 12 | 0 |
| `docs/development/2026-09-19-post-extraction-ownership-audit.md` | 194 | 0 |
| `docs/development/2026-09-19-post-extraction-ownership-hunks.md` | 701 | 0 |
| `docs/development/plug-app.md` | 17 | 4 |

Seven files +980/−5: tests +36/−1; docs +944/−4; production 0/0.
