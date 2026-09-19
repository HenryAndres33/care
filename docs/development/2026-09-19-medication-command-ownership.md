# Medication command ownership — 19 September 2026

## Scope and preserved behavior

Baseline `dcf84ee9dc765d27b418cfbf1c7e350981337973`; branch
`codex/suriname-clinical-workflows`; fork `ece71a878b3764a476d713a163a2f5515db57581`.
Push destination henry-fork (HenryAndres33/care.git), same explicit branch.
Only medication command orchestration moved; no frontend, model, migration or
deployment change. The exact shared BUS claim precedes all edits.

`care_suriname/api/viewsets/medication_commands.py` (241 lines) owns both actions,
five private context/authorization/replay/validation/hash helpers and the named
constraint-error classifier. Native MedicationRequestViewSet (165 lines) adds
only the existing generic `with_contributed_actions("medication_request")`
registration. No new seam, priority change or feature-specific core workaround.
Every moved and retained function/method has the same AST as baseline, including
action/schema decorators. Native router registration is unchanged.

The native MedicationRequest row stores client_request_id and payload hash;
there is no separate custom command-ledger model to move. Its unconditional
unique constraint, soft-delete key reservation and all table metadata stay put.
The plugin owns read-context checks before replay, encounter row locking, repeated
replay checks, workflow gate, new-write authorization, reservation before side
effects, prescription assignment, native perform_create/audit and transactional
rollback. Only named idempotency violations are caught. URLs, strict flat specs,
status codes, normalization/hash, requester/actor/patient/encounter binding and
response shape are unchanged. Reconcile never creates a missing command.

Native perform_create/update/destroy retain their encounter locks, closed-state
checks and authorization. `resolve_created_prescription` remains native because
ordinary MedicationRequestSpec deserialization uses the same native behavior;
copying it into the plugin would duplicate generic prescription CRUD. The plugin
owns the command's invocation timing. ClinicalNoStoreResponseMixin remains an
intentional core import protecting all native medication responses, not merely
custom actions. Native spec/model files are unchanged.

Frontend consumers were traced read-only through medications/adapters/
careMedicationRoutes.ts, its repository/ordering boundary and integration README.
The create/reconcile URLs remain `/api/v1/patient/{patient}/medication/request/`
with suffixes `idempotent-create/` and `idempotent-reconcile/`. Existing native
regression tests stay in place; only the constraint-classifier mock target changes
to plugin ownership. New plugin guards verify action origin, all inherited native
CRUD/safety/finalize_response methods, exact reverse/dispatch metadata and native
UUID/unknown-path resolution.

## Reproducible verification

Commands: `git diff dcf84ee9d -- <claimed paths>`,
`git diff --numstat --unified=0 ece71a878 -- care config`, Python AST/import scans,
DRF nested-router URL and Spectacular schema dumps, `manage.py test` with
a disposable restored-database runner, Ruff check/format, `manage.py check`,
`manage.py makemigrations --check --dry-run`, diff/secret/hash checks.
Scratch evidence: `/tmp/care-medication-ownership-20260919/` (uncommitted).
Exact staged source is exported through git checkout-index and tested in a
disposable container. No shared services are restarted.

A read-only dump of care_test is freshly restored to task-only
`test_care_medication_codex` for each baseline/candidate/staged run. The runner
asserts the target, skips migration setup/teardown and uses temporary MinIO
buckets removed in finally. Schema-only dumps normalize only comments/blanks
and randomized restrict tokens. No credentials enter logs or documentation.

Initial baseline and candidate both reproduced a restored-data harness error:
TransactionTestCase reset_sequences resets existing RoleModel IDs to 1 and then
collides with restored roles. Only the scratch harness disables reset_sequences
for that class on both revisions; production/test source behavior is not changed.
With this correction both PostgreSQL concurrency tests pass (same-key 200/201,
cross-encounter changed-payload 201/409, exactly one prescription/medication/
QuestionnaireResponse and named constraint handling).

Corrected baseline: 84 tests, 80 pass, 4 failures. Candidate: 118 tests, 114 pass,
the same 4 failures by full identity/assertion, no errors. Coverage includes
create/replay/reconcile/missing-key/conflicts/deletion, hash canonicalization,
strict payloads, requester/session/context authorization, closure/replay, native
CRUD, prescription APIs, statement APIs, patient APIs and encounter closure.
Forced QuestionnaireResponse failure proves prescription and medication rollback.

The owner accepts no new failures; the four unresolved baseline failures are
not certified safe by this ownership move and are not changed here:

| Test | Assertion |
|---|---|
| `test_replay_requires_current_patient_and_encounter_authorization (care.emr.tests.test_medication_request_idempotency.TestMedicationRequestIdempotencyApi.test_replay_requires_current_patient_and_encounter_authorization)` | `200 != 403` |
| `test_create_medication_request_without_permission (care.emr.tests.test_medication_request.TestMedicationRequestApi.test_create_medication_request_without_permission)` | `200 != 403` |
| `test_list_medication_request_without_permissions (care.emr.tests.test_medication_request.TestMedicationRequestApi.test_list_medication_request_without_permissions)` | `200 != 403` |
| `test_update_medication_request_without_permission (care.emr.tests.test_medication_request.TestMedicationRequestApi.test_update_medication_request_without_permission)` | `200 != 403` |

Route inventory/OpenAPI are byte-identical to baseline. Scoped Ruff/format
(6 Python files), system check and migration drift pass. All native models and
migrations remain byte-identical. Generic no-provider behavior and collisions
are covered by the existing action/contribution seam tests.

## Progress and intentional residuals

Native core direct plugin imports remain 10 files, fall 26→24 statements. The
medication viewset retains only clinical_no_store; command specs and workflow
gate imports move with execution. Current care/config non-test Python delta
versus fork: 37 files/173 hunks/+2645/−249 (excludes tests/migrations, including
config/settings/test.py). Native medication viewset is +3/−225 versus baseline.
No additional whole core file is restored; eight previously restored files
remain fork-identical. Production sizes: native viewset 165, plugin commands 241,
plugin contributions 34 lines. Historical audits are corrected by dated additions.

Medication is group 7/8 complete once all recorded gates pass: 87.5% of the finite
audit backlog, not a weighted whole-backend percentage or clinical certification.
Only form/artifact command orchestration remains in that F backlog. Independent
packaging, upstream acceptance and unresolved baseline authorization are separate.

## Signed-in browser result

The unchanged committed frontend was served only at 127.0.0.1:4001, with the
existing local backend bind-mounted source. No service restart. Annand's existing
session opened designated synthetic patient 78fd6f11-e0f1-4be5-a988-04a351c7cc58
(DEMO-SIM-0912-S05), facility 77d446f3-659d-40d4-a69a-bfc0f5b7a255.
Normal Contactmomenten navigation identified existing active encounter
0cfc3153-a167-4044-b38a-a32d2c620769; no encounter was created.

| Browser check | Outcome |
|---|---|
| Chart medication review | Empty active list; review-only as before |
| Standalone /urology/medications without encounter | Record button correctly disabled |
| Same page with ?encounter=0cfc3153-a167-4044-b38a-a32d2c620769 | Record button enabled |
| Governed catalog, new treatment order, Tamsulosin 400 microgram tablet, 0.4 mg, 1dd1, oral | idempotent-create 201, replayed=false |
| Full refresh on same direct URL | Same medication/dose/route/prescriber, active |
| Delete confirmation | Native upsert 200, active list empty |
| Full refresh after cleanup | Active list remains empty |
| Console/network | No page console errors or HTTP failures; one canceled navigation ERR_ABORTED |

Synthetic MedicationRequest 418eff2b-f4c7-40f4-95bf-9df8420eb530 remains in audit
history as entered_in_error after supported cleanup. No real patient record was
used. The browser did not manufacture replay/concurrency requests; tests cover
those branches. Prescription side-effect/rollback is tested at the API level;
no PDF/report or frontend production build was rerun because neither rendering
nor runtime frontend source changed. Desktop flow verified; no mobile layout
change. The initial browser webview attachment timeout recovered on one retry;
control-tool telemetry errors were separate from the clean page console.
Tab closed and port 4001 stopped; port 4000 untouched.

## Exact staged result and preservation

Exact index export: 118 tests, 114 passed, the same four baseline assertions, no
errors/new failures. Both PostgreSQL concurrency tests pass. Ruff/format for six
Python files, system check, migration drift, route/OpenAPI parity and normalized
physical schema comparison pass. All moved/retained method ASTs match baseline.
All 1,370 unclaimed tracked backend hashes are unchanged. No model or migration
source changed. The task database and temporary MinIO buckets were removed.

Ownership group 7/8 is complete under the source-separation definition. Remaining
F group: form/artifact orchestration. The no-store import and shared prescription
helper are intentional native safety/generic behavior, not residual medication
command execution. Backend commit contains only the 11 exact claimed paths;
shared BUS coordination is appended separately and excluded from the commit.

### Exact task file changes

| Path | Added | Removed |
|---|---:|---:|
| `care/emr/api/viewsets/medication_request.py` | 3 | 225 |
| `care/emr/tests/test_medication_request_idempotency.py` | 3 | 2 |
| `care_suriname/README.md` | 9 | 0 |
| `care_suriname/api/viewsets/medication_commands.py` | 241 | 0 |
| `care_suriname/contributions.py` | 7 | 0 |
| `care_suriname/resources/MEDICATION_COMMANDS.md` | 35 | 0 |
| `care_suriname/tests/test_backend_ownership.py` | 0 | 2 |
| `care_suriname/tests/test_medication_command_ownership.py` | 90 | 0 |
| `docs/development/2026-09-19-final-backend-separation-audit.md` | 14 | 0 |
| `docs/development/2026-09-19-medication-command-ownership.md` | 170 | 0 |
| `docs/development/plug-app.md` | 15 | 6 |

Task totals: production +251/−225, tests +93/−4, docs +243/−6.
