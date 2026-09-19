# Backend documentation cleanup and baseline-test triage — 19 September 2026

## Source and result

Baseline: `0ed1b6e10ac1a7692f6114cc8647306ce2a43d9d`.
Candidate: the uncommitted documentation and test-fixture changes recorded below.
The [final source-ownership report](2026-09-19-patient-access-ownership.md) and
[current inventory](2026-09-19-ownership-closure-hunks.md) remain authoritative.
Ownership completion is not authorization certification or deployment.

Fresh baseline: **241 tests, 225 pass, 16 failures, zero errors** (34.739 s).
Candidate: **241 tests, 241 pass, zero failures/errors** (40.672 s).
The baseline failure identities match the final source gate exactly. All test
assertion ASTs are unchanged. No production Python, migration or configuration
changed. Five setup lines now create the facility through a separate user.
`Facility.save()` automatically grants its creator the facility-admin root role;
using the tested actor as creator invalidated negative/limited-permission fixtures.
Positive cases now depend on their explicit roles. This is not a permission change.

A green gate alone did **not** close the separate care-team defect below. The
follow-up fix and focused regression are recorded in that section.

## Exact 16 baseline failures

IDs below are complete Django test labels. Baseline failure was `200 != 403`
except the first (`True is not false`). All 16 pass in the candidate gate.
Classification describes the evidence, not a blanket safety sign-off.

| Exact test label | Classification | Finding |
|---|---|---|
| `care.security.tests.test_patient_department_access.PatientDepartmentAccessTest.test_completed_encounter_of_other_department_gives_no_access` | Bad fixture | Facility-root membership authorizes both department scopes, defeating the intended department-only actor. |
| `care.emr.tests.test_encounter_api.EncounterAPITests.test_create_encounter_without_permissions` | Bad fixture | The actor described as lacking permissions inherited the facility creator admin role. |
| `care.emr.tests.test_encounter_api.EncounterAPITests.test_filter_by_patient_without_permission` | Bad fixture | The actor described as lacking permissions inherited the facility creator admin role. |
| `care.emr.tests.test_encounter_api.EncounterAPITests.test_retrieve_encounter_without_permissions` | Bad fixture | The actor described as lacking permissions inherited the facility creator admin role. |
| `care.emr.tests.test_encounter_api.EncounterAPITests.test_set_facility_identifier_without_permissions` | Bad fixture | The actor described as lacking permissions inherited the facility creator admin role. |
| `care.emr.tests.test_encounter_api.EncounterAPITests.test_update_encounter_without_permissions` | Bad fixture | The actor described as lacking permissions inherited the facility creator admin role. |
| `care.emr.tests.test_encounter_api.EncounterOrganizationAPITests.test_add_care_team_member_without_permissions` | Bad fixture | The actor described as lacking permissions inherited the facility creator admin role. |
| `care.emr.tests.test_encounter_api.EncounterOrganizationAPITests.test_add_encounter_organization_without_permissions` | Bad fixture | The actor described as lacking permissions inherited the facility creator admin role. |
| `care.emr.tests.test_encounter_api.EncounterOrganizationAPITests.test_add_treating_doctor_care_team_member` | Likely production defect; fixture also defective | Creator admin masks the expected actor denial. With explicit actor read/write permissions, the separate recipient-scope probe still returns 200; follow-up below. |
| `care.emr.tests.test_encounter_api.EncounterOrganizationAPITests.test_list_encounter_organizations_without_permissions` | Bad fixture | The actor described as lacking permissions inherited the facility creator admin role. |
| `care.emr.tests.test_encounter_api.EncounterOrganizationAPITests.test_remove_encounter_organization_without_permissions` | Bad fixture | The actor described as lacking permissions inherited the facility creator admin role. |
| `care.emr.tests.test_form_submission_api.TestFormSubmissionViewSet.test_list_with_encounter_filter_completed_encounter` | Bad fixture | Admin clinical-read access permits closed-contact history. The explicit submit-only role still receives the original expected 403 after removing the unintended admin grant. |
| `care.emr.tests.test_form_submission_api.TestFormSubmissionViewSet.test_list_with_encounter_filter_without_permissions` | Bad fixture | The actor described as lacking permissions inherited the facility creator admin role. |
| `care.emr.tests.test_form_submission_api.TestFormSubmissionViewSet.test_list_with_patient_filter_without_permissions` | Bad fixture | The actor described as lacking permissions inherited the facility creator admin role. |
| `care.emr.tests.test_form_submission_api.TestFormSubmissionViewSet.test_retrieve_without_permissions` | Bad fixture | The actor described as lacking permissions inherited the facility creator admin role. |
| `care.emr.tests.test_diagnosis_idempotent_api.TestDiagnosisUpdateAuthorizationRegression.test_read_only_user_cannot_update_chronic_condition` | Bad fixture | The purported read-only actor also inherited facility-admin write permissions. |

No failure was demonstrated to require an outdated-expectation change or to be
flaky isolation in this gate. No denial assertion was changed to success, deleted,
skipped or weakened. The older discharge-concurrency failure belongs to a different
gate and was not reproduced or fixed here.

## Resolved production follow-up: nominated care-team user authorization

`care/emr/api/viewsets/encounter.py`, `set_care_team_members`, resolved each
`user_obj` but passed `request.user` to `can_view_encounter_obj`. Thus it checked
the actor repeatedly instead of the nominated user.

An isolated diagnostic reran the existing
`EncounterOrganizationAPITests.test_add_treating_doctor_care_team_member` with
only `EncounterPermissions.can_read_encounter.name` added to the actor's explicit
role, in addition to its existing write and patient clinical-read permissions.
The nominated `new_user` still has no membership. The unchanged 403 assertion
failed: **200 != 403** (one test, one failure, 0.284 s). The fix now passes
`user_obj` to the authorization controller. The existing treating-doctor test
was strengthened so the actor has both read and write permission while the
nominee has no membership; it now proves the nominee is rejected. Positive and
duplicate-member tests explicitly give the nominee encounter-read permission,
preserving their intended success and duplicate-validation coverage. Focused
verification ran all four affected modules on a freshly created isolated test
database: **100 tests passed**, Ruff check/format and Django system check clean.

## Reproduction and isolation

Scratch evidence: `/tmp/care-backend-cleanup-20260919/` contains
`run_gate.py`, `test_harness.py`, `run_tests.py`, `baseline-gate.log`,
`candidate-gate.log`, `checks.py`, `checks.log`, `care_team_probe.py`,
`care-team-probe.log`, `cleanup-inventory.json` and `link-check.log`.
These are local evidence, not required application files or deployed artifacts.

From the backend root the baseline/candidate command was
`python3 /tmp/care-backend-cleanup-20260919/run_gate.py <baseline|candidate>`.
The runner mounts the current worktree read-only: rerunning `baseline` after edits
would test current source, so reproduce the old result only with the pinned
baseline source and original fixtures. No duplicate checkout was served.
The exact labels, identical on both runs, are:

```text
care.security.tests
care.emr.tests.test_patient_api
care.emr.tests.test_user_api
care.emr.tests.test_encounter_api
care.emr.tests.test_form_submission_api
care.emr.tests.test_diagnosis_idempotent_api
care_suriname.tests.test_patient_directory
care_suriname.tests.test_patient_access_policy
care_suriname.tests.test_backend_ownership
care_suriname.tests.test_plug_registration
plugs.tests
```

Each run restored the existing final-audit dump to the task-owned
`test_care_cleanup_codex` on `care-test-db-1` (network `care-test`, never live DB).
The runner asserts host `db` and the exact database name; migration setup/teardown
is disabled. As in the final audit, the restored-data form concurrency fixture's
`reset_sequences` is disabled identically in baseline and candidate. This avoids
resetting sequences below restored identifiers; it is a harness accommodation,
not a tracked test change or a newly claimed isolation fix. Temporary upload
buckets use `codex-cleanup-<mode>` on test MinIO and are removed in `finally`.
Static assets write only to the container's `/tmp`. No migration was applied.
The task database was removed after all checks and the diagnostic probe.

## Cleanup inventory and items deliberately retained

Tracked-file scan found **143 empty files**, including **137 `__init__.py` package
markers**, and eight docstring-only package markers. Keep these: Python/Django
package discovery, migrations, management commands and plugin import behavior
must not be inferred from direct import counts alone.

The six other empty files remain intentionally untouched: `CONTRIBUTORS.txt`,
`care/emr/utils/decimal_context.py`, `care/media/.gitkeep`,
`care/static/fonts/.gitkeep`, `care/static/sass/custom_bootstrap_vars.scss`, and
`docs/_static/.gitkeep`. The decimal module is also empty at the upstream fork;
it is not a leftover custom implementation. Placeholders and upstream template
files offer no demonstrated post-extraction behavior cleanup.

There are **no byte-identical tracked Markdown duplicates**. Overlapping dated
reports are distinct evidence, not redundant copies. The old plug guide is
preserved verbatim inside `plug-app-implementation-history.md` with a historical
banner; active architecture, installation, migration/deployment, rollback and the
frozen 10-file/12-import allowlist now live in the operational guide. Former
section anchors have links to the archive. Four intermediate audits remain at
their inbound-link paths with unmistakable superseded banners.

No production module was proven unreachable. Retain AppConfig side-effect imports,
Celery autodiscovery and task index imports, five plugin management commands,
all migrations, model registries, lazy contributions and empty plug URL modules.
The existing ownership/startup/route guards pass. Retain the audit-log exclusion
branch and direct safety integrations pending explicit parity proof. This bounded
scan is not a proof that every module is reachable under arbitrary configuration.

## Validation and boundaries

- Ruff check and format check pass for all four touched Python test files.
- Django system check passes; `makemigrations --check --dry-run` reports no changes.
- The 241-test gate includes native security/API tests and plugin ownership,
  startup/registration/contribution guards; all pass after fixture repair.
- Local Markdown target/fragment checks and preserved historical-anchor checks
  pass. External URLs were not crawled; no external factual claim changed.
- `git diff --check` passes. Changed-path review confirms production source is
  unchanged; assertion AST comparison proves assertions are unchanged.
- No staging, commit, push, live DB writes, migration application, shared-service
  restart or deployment. Frontend edits are restricted to append-only BUS claims.

## Exact file changes

Backend-relative paths and added/removed lines are recorded below. New documents
are included even though untracked. No production files are touched.

| File | Added | Removed |
|---|---:|---:|
| `care/emr/tests/test_diagnosis_idempotent_api.py` | 2 | 1 |
| `care/emr/tests/test_encounter_api.py` | 4 | 2 |
| `care/emr/tests/test_form_submission_api.py` | 2 | 1 |
| `care/security/tests/test_patient_department_access.py` | 2 | 1 |
| `care_suriname/README.md` | 33 | 23 |
| `docs/development/2026-09-19-backend-cleanup-triage.md` | 179 | 0 |
| `docs/development/2026-09-19-final-backend-separation-audit.md` | 7 | 0 |
| `docs/development/2026-09-19-final-backend-separation-hunks.md` | 7 | 0 |
| `docs/development/2026-09-19-post-extraction-ownership-audit.md` | 7 | 0 |
| `docs/development/2026-09-19-post-extraction-ownership-hunks.md` | 7 | 0 |
| `docs/development/plug-app-implementation-history.md` | 463 | 0 |
| `docs/development/plug-app.md` | 194 | 439 |

Backend total: **12 files, +907/−467** (tests +10/−5; production +0/−0).
Coordination only: `../care_fe/.agents/BUS.md`, +6/−0 for this task's claim and release; unrelated parallel entries are excluded.
