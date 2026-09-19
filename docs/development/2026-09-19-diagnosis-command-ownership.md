# Diagnosis-command ownership — 19 September 2026

## Scope and decision

Baseline `a744bc2270168a9746caa0416e8920f7f8fe1a42`, branch
`codex/suriname-clinical-workflows`; fork `ece71a878b3764a476d713a163a2f5515db57581`.
Only diagnosis command execution moves. Native Condition table/fields/constraints,
generic CRUD, closed-encounter checks, authorization and native save/audit paths
are unchanged. No model/migration change or migration application.

The native detail regex would capture idempotent-create after a simple late plugin
route move. The existing priority seam deliberately accepts literal full paths;
this endpoint has a parent parameter. Widening that mechanism would add risk.
Instead `plugs/viewset_actions.py` contributes only new DRF actions and private
helpers through the existing single-provider registry. The host class is returned
identically when absent; host overrides, public non-actions, regex paths, duplicate
paths/reverse names and reserved list/detail names fail closed. DRF retains exact
router order, parent kwargs, middleware/authentication and format aliases. No
Urology conditional or route string is added to core.

The two extracted methods (including decorators) have identical ASTs to baseline.
Every remaining native class method also has identical ASTs, compared within its
own class. The sole new native hook is the generic import/decorator. No plugin
implementation is copied back into core. Native chronological/update authorization
safety hunks remain; the full condition viewset is not restored to upstream.

## Runtime contract

`DiagnosisCommandActions` owns `_idempotency_response` and `idempotent_create`.
The original plugin specs/hash helpers remain unchanged. Native Condition itself
stores request identity/hash (there is no separate command-ledger model). The
patient row lock, encounter row lock, second replay check, native authorization,
active-duplicate query and native perform_create/audit remain in the same atomic
block. Exact replay200, create201, changed/deleted/actor replay409, duplicate409,
validation and completed-encounter behavior remain unchanged. The frontend calls
the same `/api/v1/patient/{patient}/diagnosis/idempotent-create/`; no frontend edit.

## Verification method

Commands used: `git diff a744bc227 -- <claimed paths>`,
`git diff --numstat --unified=0 ece71a878 -- care config`, Python AST comparisons,
Django `resolve`/`reverse`, DRF router URL inventory and Spectacular schema dump,
`manage.py test` with a restored disposable-database runner, Ruff check/format,
`manage.py check`, `manage.py makemigrations --check --dry-run`,
`git diff --check`, `git diff --cached --check`, and staged secret/hash scans.
Exact staged source is exported with `git checkout-index --prefix=<scratch>/`.
Scratch evidence: `/tmp/care-diagnosis-ownership-20260919/` (not committed).

A fresh dump of the local test database was restored to task-only
`test_care_diagnosis_codex` before each baseline/candidate/staged gate. The runner
asserts its target and skips migration setup/teardown. Temporary per-run MinIO
buckets are deleted in finally; no credentials enter reports. Schema-only dumps
are compared excluding only dump comments/blanks and randomized restrict tokens.
Native model and migration sources are unchanged.

Candidate: 138 tests, 109 pass, 29 failures. Baseline: 106 tests, 77 pass, the same
29 failures by full test identity and assertion. New command/rollback/concurrency
tests are copied into the exact baseline for comparison; baseline also retains
the two original native command tests, whereas candidate rehomes them. Native
CRUD authorization regression stays in native tests unchanged. Focused standalone
ownership/action tests: 13/13 pass. Real PostgreSQL two-thread tests establish
200/201 for same-ID replay and 201/409 for distinct-ID active duplicates.

These are unresolved baseline authorization/validation failures, not a security
certification. The owner accepts no new failures; this batch does not alter policy.

### Baseline failures (identical candidate assertions)

| Test | Assertion |
|---|---|
| `test_create_diagnosis_with_organisation_user_with_permissions (care.emr.tests.test_diagnosis_api.TestDiagnosisViewSet.test_create_diagnosis_with_organisation_user_with_permissions)` | `200 != 403` |
| `test_create_diagnosis_with_permissions_and_no_association_with_facility (care.emr.tests.test_diagnosis_api.TestDiagnosisViewSet.test_create_diagnosis_with_permissions_and_no_association_with_facility)` | `200 != 403` |
| `test_create_diagnosis_without_permissions (care.emr.tests.test_diagnosis_api.TestDiagnosisViewSet.test_create_diagnosis_without_permissions)` | `200 != 403` |
| `test_create_diagnosis_without_permissions_on_facility (care.emr.tests.test_diagnosis_api.TestDiagnosisViewSet.test_create_diagnosis_without_permissions_on_facility)` | `200 != 403` |
| `test_delete_diagnosis_for_single_encounter_without_permission (care.emr.tests.test_diagnosis_api.TestDiagnosisViewSet.test_delete_diagnosis_for_single_encounter_without_permission)` | `204 != 403` |
| `test_delete_diagnosis_without_permission (care.emr.tests.test_diagnosis_api.TestDiagnosisViewSet.test_delete_diagnosis_without_permission)` | `204 != 403` |
| `test_list_diagnosis_for_single_encounter_without_permissions (care.emr.tests.test_diagnosis_api.TestDiagnosisViewSet.test_list_diagnosis_for_single_encounter_without_permissions)` | `200 != 403` |
| `test_list_diagnosis_with_permissions_and_encounter_status_as_completed (care.emr.tests.test_diagnosis_api.TestDiagnosisViewSet.test_list_diagnosis_with_permissions_and_encounter_status_as_completed)` | `200 != 403` |
| `test_list_diagnosis_without_permissions (care.emr.tests.test_diagnosis_api.TestDiagnosisViewSet.test_list_diagnosis_without_permissions)` | `200 != 403` |
| `test_retrieve_diagnosis_for_single_encounter_without_permissions (care.emr.tests.test_diagnosis_api.TestDiagnosisViewSet.test_retrieve_diagnosis_for_single_encounter_without_permissions)` | `200 != 403` |
| `test_retrieve_diagnosis_without_permissions (care.emr.tests.test_diagnosis_api.TestDiagnosisViewSet.test_retrieve_diagnosis_without_permissions)` | `200 != 403` |
| `test_update_diagnosis_for_closed_encounter_with_permissions (care.emr.tests.test_diagnosis_api.TestDiagnosisViewSet.test_update_diagnosis_for_closed_encounter_with_permissions)` | `400 != 403` |
| `test_update_diagnosis_for_single_encounter_without_permissions (care.emr.tests.test_diagnosis_api.TestDiagnosisViewSet.test_update_diagnosis_for_single_encounter_without_permissions)` | `400 != 403` |
| `test_update_diagnosis_without_permissions (care.emr.tests.test_diagnosis_api.TestDiagnosisViewSet.test_update_diagnosis_without_permissions)` | `400 != 403` |
| `test_read_only_user_cannot_update_chronic_condition (care.emr.tests.test_diagnosis_idempotent_api.TestDiagnosisUpdateAuthorizationRegression.test_read_only_user_cannot_update_chronic_condition)` | `200 != 403` |
| `test_create_symptom_with_organisation_user_with_permissions (care.emr.tests.test_symptom_api.TestSymptomViewSet.test_create_symptom_with_organisation_user_with_permissions)` | `200 != 403` |
| `test_create_symptom_with_permissions_and_no_association_with_facility (care.emr.tests.test_symptom_api.TestSymptomViewSet.test_create_symptom_with_permissions_and_no_association_with_facility)` | `200 != 403` |
| `test_create_symptom_without_permissions (care.emr.tests.test_symptom_api.TestSymptomViewSet.test_create_symptom_without_permissions)` | `200 != 403` |
| `test_create_symptom_without_permissions_on_facility (care.emr.tests.test_symptom_api.TestSymptomViewSet.test_create_symptom_without_permissions_on_facility)` | `200 != 403` |
| `test_delete_symptom_for_single_encounter_without_permission (care.emr.tests.test_symptom_api.TestSymptomViewSet.test_delete_symptom_for_single_encounter_without_permission)` | `204 != 403` |
| `test_delete_symptom_without_permission (care.emr.tests.test_symptom_api.TestSymptomViewSet.test_delete_symptom_without_permission)` | `204 != 403` |
| `test_list_symptoms_for_single_encounter_without_permissions (care.emr.tests.test_symptom_api.TestSymptomViewSet.test_list_symptoms_for_single_encounter_without_permissions)` | `200 != 403` |
| `test_list_symptoms_with_permissions_and_encounter_status_as_completed (care.emr.tests.test_symptom_api.TestSymptomViewSet.test_list_symptoms_with_permissions_and_encounter_status_as_completed)` | `200 != 403` |
| `test_list_symptoms_without_permissions (care.emr.tests.test_symptom_api.TestSymptomViewSet.test_list_symptoms_without_permissions)` | `200 != 403` |
| `test_retrieve_symptom_for_single_encounter_without_permissions (care.emr.tests.test_symptom_api.TestSymptomViewSet.test_retrieve_symptom_for_single_encounter_without_permissions)` | `200 != 403` |
| `test_retrieve_symptom_without_permissions (care.emr.tests.test_symptom_api.TestSymptomViewSet.test_retrieve_symptom_without_permissions)` | `200 != 403` |
| `test_update_symptom_for_closed_encounter_with_permissions (care.emr.tests.test_symptom_api.TestSymptomViewSet.test_update_symptom_for_closed_encounter_with_permissions)` | `400 != 403` |
| `test_update_symptom_for_single_encounter_without_permissions (care.emr.tests.test_symptom_api.TestSymptomViewSet.test_update_symptom_for_single_encounter_without_permissions)` | `400 != 403` |
| `test_update_symptom_without_permissions (care.emr.tests.test_symptom_api.TestSymptomViewSet.test_update_symptom_without_permissions)` | `400 != 403` |

## Signed-in synthetic browser evidence

Unchanged committed frontend served only on 127.0.0.1:4001, using the existing
local backend bind-mounted source; no shared service restart. Browser initially
timed out attaching a webview; one documented recovery opened it successfully.
Account annand; facility `77d446f3-659d-40d4-a69a-bfc0f5b7a255`; synthetic patient
`78fd6f11-e0f1-4be5-a988-04a351c7cc58` (DEMO-SIM-0912-S05). No real patient data used.

| Step | Observed result |
|---|---|
| Patient search, Dossier openen, Diagnoses beheren | Synthetic patient; zero active diagnoses |
| Search prostaat, choose Benigne prostaathyperplasie | Approved Dutch label and native form |
| Save clearly marked SYNTHETISCHE TEST note | POST idempotent-create201, replayed=false, clinical_domain=urology |
| Full page refresh on chart?view=diagnosis | One active diagnosis and exact test note remain |
| Urologieoverzicht, expand diagnosis | Same label/domain/note |
| Verwijderen, confirm Uit probleemlijst verwijderen | Native detail GET/OPTIONS/PUT200, active list returns to zero |
| Console/network | No console errors or HTTP failures; two canceled ERR_ABORTED navigation requests |

Created diagnosis `82a7b516-c21e-451b-866d-fe6e7d670952` is removed from the active
list; its synthetic audit/command history is intentionally retained by supported
CARE behavior. Browser did not force replay requests; deterministic tests cover
replay/hash/concurrency. No UI/layout code changed; desktop workflow verified.
Verification tab closed and isolated server stopped. Port4000 untouched.

## Residual work

Core plugin imports fall 11 files/27 statements to 10/26. No new direct plugin
import remains in condition.py. Eight previously restored core files remain
byte-identical to fork. Medication-command and form/artifact orchestration are
the two remaining F groups. Six/eight finite audit clusters are implemented
(75% of that backlog, not a weighted overall completion percentage). Generic
action contribution is an upstream-PR candidate. Shared deployment/frontend and
production VM are untouched; no schema SQL, migration or data transformation.

## Exact staged-tree closure

The isolated index export ran 138 tests: 109 pass, the same 29 baseline failures
with identical full identities/assertions, no errors/new failures. Ruff check
and format pass for all 10 changed Python files. System check reports no issues;
makemigrations --check --dry-run reports no changes. Staged diagnosis/symptom
route inventory and OpenAPI are byte-identical to baseline. Physical PostgreSQL
schema is byte-identical after removing only dump metadata. All moved and retained
method ASTs match; 1,362 unclaimed tracked backend hashes are preserved. No model
or migration file changed. Index paths equal the exact 15-file BUS claim. Added
line secret-pattern and diff checks pass. Task-only database and upload buckets
removed; no listener remains on4001.

Current care/config non-test Python delta versus fork: 37 files, 175 hunks,
+2,867/−249 (excludes tests and migrations, including config/settings/test.py).
Native condition loses 139 lines and gains only the generic import/decorator,
net −137. New generic infrastructure outside care/config is72 lines. The two
custom native command tests move into plugin tests; native permission regression
is retained. The eight previously restored files remain byte-identical to fork;
no additional complete core file is restored in this batch.

### Exact file line counts

| Path | Added | Removed |
|---|---:|---:|
| `care/emr/api/viewsets/condition.py` | 2 | 139 |
| `care/emr/tests/test_diagnosis_idempotent_api.py` | 0 | 91 |
| `care_suriname/README.md` | 10 | 0 |
| `care_suriname/api/viewsets/diagnosis_commands.py` | 149 | 0 |
| `care_suriname/contributions.py` | 7 | 0 |
| `care_suriname/resources/DIAGNOSIS_COMMANDS.md` | 28 | 0 |
| `care_suriname/tests/test_backend_ownership.py` | 0 | 3 |
| `care_suriname/tests/test_diagnosis_command_concurrency.py` | 89 | 0 |
| `care_suriname/tests/test_diagnosis_command_ownership.py` | 69 | 0 |
| `care_suriname/tests/test_diagnosis_commands.py` | 175 | 0 |
| `docs/development/2026-09-19-diagnosis-command-ownership.md` | 179 | 0 |
| `docs/development/2026-09-19-final-backend-separation-audit.md` | 16 | 0 |
| `docs/development/plug-app.md` | 17 | 6 |
| `plugs/tests/test_viewset_actions.py` | 106 | 0 |
| `plugs/viewset_actions.py` | 72 | 0 |

Task totals: production +230/−139, tests +439/−94, docs +250/−6.
The shared frontend BUS claim/release is append-only coordination and is excluded
from this backend commit. Commit/push target: henry-fork,
refs/heads/codex/suriname-clinical-workflows. No deployment.
