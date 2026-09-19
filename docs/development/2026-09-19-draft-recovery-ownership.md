# Draft-recovery plugin ownership — 19 September 2026

Baseline: `590f1029e04ccde7d687056bd13b912fee6f93d9`, branch
`codex/suriname-clinical-workflows`. Upstream fork: `ece71a878`.
Scope: backend ownership only; no frontend change or deployment.

## Decision and evidence

This is custom Suriname behavior, introduced by `530eafb9e` (owner-bound keys
for encrypted Urology draft recovery), absent at the upstream fork. Its README
records the owner's 12 September approval. Current frontend producers/consumers
are entirely under `src/Plugins/urology/draft-recovery/`, plus the Urology form
workspace hook/surface. There is no native frontend consumer. Plugin v1 URL
registration and the plugin rewrap command already owned the entry points.

The feature lets the original clinician recover an encrypted, unsent browser
draft after a crash. The server retains wrapped random keys, not clinical draft
text. Potential usefulness to upstream CARE is not evidence of native ownership.

Evidence commands: `git show --stat 530eafb9e`, `git ls-tree -r ece71a878`,
`git diff ece71a878 -- config/auth_views.py care/emr/utils/mfa.py`, and `rg` for
DraftRecoveryKey, draft_recovery, care_authenticated_at, care_auth_method and the
key-release URLs across backend and frontend source. Frontend was read-only.

## Exact ownership and behavior

Eight `git mv` relocations:

- `care/users/draft_recovery/{__init__,crypto,service,views}.py` and `README.md`
  → `care_suriname/draft_recovery/`.
- `care/users/draft_recovery/models.py` → `care_suriname/models/draft_recovery.py`.
- `config/draft_recovery_auth.py` → `care_suriname/draft_recovery/auth.py`.
- `care/users/tests/test_draft_recovery.py` → plugin tests with the same filename.

All production callers now use plugin imports. The users model-discovery import
is removed; `care/users/models.py` matches the fork byte-for-byte. Historical
users0028 is unchanged. No native implementation shim remains. Crypto and auth
helper bytes match baseline; AST comparison with imports removed proves unchanged
service/view/rewrap/password-login/MFA bodies. Only model ownership/db_table and
the generic audit exclusion label change at runtime. Existing URLs, errors,
owner locks, UUIDs, AES-GCM/AAD, nonce/key formats, expiry/TLS rules, retained-key
read-back and rewrap command behavior are preserved. Environment names/defaults
and protected secret-file mounts are untouched.

## Migration design and invariants

- `users.0029_move_draft_recovery_to_care_suriname`: state-only DeleteModel,
  `SeparateDatabaseAndState(database_operations=[])`.
- `care_suriname.0002_move_draft_recovery`: state-only CreateModel with pinned
  `users_draftrecoverykey`, identical fields and active-owner constraint; then
  reversible content-type relabelling. Depends on users0029, plugin0001 and
  contenttypes0002. No native migration depends on the plugin; no cycle.
- Existing database metadata update is equivalent to
  `UPDATE django_content_type SET app_label='care_suriname' WHERE id=132`,
  plus the two normal migration-journal inserts. Row identity is never replaced.
- Zero schema SQL: no table rename, column/index/constraint change, row copy or
  key-row write. Permission IDs/links survive; app-qualified permission names
  intentionally become `care_suriname.<action>_draftrecoverykey`. No production
  permission-string consumer was found; endpoint authorization remains owner-only.
- Missing installed source identity or any target identity raises
  ContentTypeRelabelError. A true fresh database defers creation to post_migrate.

Initial laptop inventory: 3 key rows; content-type ID132 (`users`);
permission IDs525,526,527,528 (add/change/delete/view). No key bytes were emitted.
Schema comparison removes only pg_dump's random restrict/unrestrict marker lines.
Checksums cover the complete key rows, all content-type IDs/models (normalizing
only this intentional app-label change), and every auth_permission row. Full
schema equality covers columns, constraints, indexes, FKs and join tables.

## Verification evidence

Scratch evidence and exact indexed source export:
`/tmp/care-draft-ownership-20260919/`. A fresh laptop dump was restored only into
`care-test-db-1/codex_draft_rehearsal`; empty-install database is
`codex_draft_empty`. Unit databases are `test_codex_draft_ownership` and
`test_codex_draft_baseline`. Shared care_test was not reset.

- Restored database: forward, repeated post_migrate, reverse, injected duplicate
  target (fails; plugin0002 not recorded), cleanup of injected test identity,
  forward again: schema/key/CT-ID/all-permission invariants pass.
- Empty installation: forward, reverse, forward and repeated post_migrate all PASS;
  schema/key/CT-ID/permission checksums remain identical.
- Migration drift: no changes detected. Default squash-aware MigrationLoader
  collect_sql produces only no-op/Python comments, zero schema statements.
- 105 focused and adjacent tests PASS: draft security/API/crypto/service/rewrap,
  ownership/content-type/audit-exclusion guards, startup registration, password
  changes, MFA/TOTP, users APIs, patient-access permissions and note-lab boundary.
- Scoped Ruff and formatting: 18 Python files pass. Django system check passes.
  Staged and working diff checks pass. Exact final-tree status: 105 passing tests on exported indexed Python; final complete
  index verification uses the same isolated container harness.
- Unclaimed tracked backend and dirty frontend hashes remain unchanged.

Initial harness failures were reproduced at exact baseline590f1029e, not attributed
to this change. Five user-profile image tests hit InvalidAccessKeyId and then a
missing collected favicon. Corrected *isolated test harness only*: credentials
read in memory from test MinIO, a dedicated temporary upload bucket (deleted in
finally), and collectstatic in scratch. Baseline48/48 and candidate105/105 then
pass; no source or shared environment configuration was changed.

The standalone `sqlmigrate` command fails at both baseline and candidate because
its replace_migrations=False loader reaches a missing historical facility0360
parent. Normal migrate/check and the default squash-aware loader work. This
pre-existing diagnostic remains; no historic migration was edited to hide it.

## Laptop application and endpoint checks

Rehearsals passed before laptop application. Existing care-backend-1 and
care-celery-1 were briefly paused, with no active database transaction, to stop
writes for a fresh backup and the migration. Backup:
`/home/henrywielzen/care-suriname-backups/draft-recovery-20260919/20260919-121633/`.
Database1.7M, MinIO2.9M, 2520 readable archive entries; pg_restore list,
tar integrity and SHA256 checks pass. No old backups pruned; mirror not configured.
The wrapping keyring was not copied into this clinical backup.

Applied only users0029 and plugin0002 through a one-shot local management container
(no published ports). Existing application/worker containers were unpaused in
finally, not restarted. Before/after full normalized schema and key/CT-ID/permission
checksums match. Keys3; CT132 now care_suriname; permissions525..528 unchanged.
System check clean, no model drift, and no pending migrations afterwards.
No production VM, frontend4000, clinical-record or deployment change.

Direct local authenticated HTTP GET against port9000, before state migration:
200, exact seven-field response, same owner/key IDs, AES-GCM material matching
existing wrapped storage, 900-second proof interval and no-store. A deliberately
expired proof returns403 with draft_recovery_reauthentication_required. Proof
was minted in a bounded backend verification process for the owner's authorized
local account; no password, token or key value was printed or persisted. Native
password/MFA proof creation is separately exercised by the automated suite.
No POST, new key, clinical write or browser operation was required. The identical post-migration HTTP checks also PASS..

The database backup contains wrapped keys. The separately protected wrapping
keyring remains unchanged and must still be managed/backed up separately; this
move does not certify disaster recovery without that keyring or independent
security review. No production VM or deployment was touched.

## Intentional remaining native boundaries

No unresolved draft-recovery ownership decision remains after successful local
application. Two existing proof hooks necessarily import the moved helper:
password login and MFA. There is no shared generic post-interactive-auth hook;
no broad auth seam was invented. The exact closed production import inventory
is 12 native files / 28 statements:

- `care/emr/api/viewsets/condition.py` — lines 29
- `care/emr/api/viewsets/encounter.py` — lines 63, 66, 67
- `care/emr/api/viewsets/form_submission.py` — lines 45, 46, 52, 57, 60, 63, 69, 75, 86, 87, 91, 96, 780
- `care/emr/api/viewsets/medication_request.py` — lines 31, 32, 37
- `care/emr/api/viewsets/report/report_upload.py` — lines 31
- `care/emr/api/viewsets/scheduling/booking.py` — lines 56
- `care/emr/api/viewsets/scheduling/schedule.py` — lines 39
- `care/emr/api/viewsets/user.py` — lines 41
- `care/emr/api/viewsets/valueset.py` — lines 19
- `care/emr/models/report/template.py` — lines 26
- `care/emr/utils/mfa.py` — lines 13
- `config/auth_views.py` — lines 18

The prior ten files retain clinical safety/compatibility behavior documented in
plug-app.md and the existing core-patch documents. Core table invariants and
immutable migration history, completed-encounter authorization, user preference
and directory support, settings and generic v1 plugin routing remain. This move
is not a claim that CARE core is unmodified or that every remaining patch has
been newly audited. Exact current non-test/non-migration care/config delta paths
against the fork (including two owning documents):

- `care/audit_log/helpers.py`
- `care/emr/api/viewsets/condition.py`
- `care/emr/api/viewsets/device.py`
- `care/emr/api/viewsets/encounter.py`
- `care/emr/api/viewsets/form_submission.py`
- `care/emr/api/viewsets/location.py`
- `care/emr/api/viewsets/medication_request.py`
- `care/emr/api/viewsets/patient.py`
- `care/emr/api/viewsets/report/report_upload.py`
- `care/emr/api/viewsets/scheduling/booking.py`
- `care/emr/api/viewsets/scheduling/schedule.py`
- `care/emr/api/viewsets/scheduling/token.py`
- `care/emr/api/viewsets/user.py`
- `care/emr/api/viewsets/valueset.py`
- `care/emr/models/__init__.py`
- `care/emr/models/condition.py`
- `care/emr/models/encounter.py`
- `care/emr/models/medication_request.py`
- `care/emr/models/questionnaire.py`
- `care/emr/models/report/report_upload.py`
- `care/emr/models/report/template.py`
- `care/emr/registries/system_questionnaire/system_questionnaire.py`
- `care/emr/resources/condition/spec.py`
- `care/emr/resources/encounter/constants.py`
- `care/emr/resources/encounter/spec.py`
- `care/emr/resources/form_submission/spec.py`
- `care/emr/resources/medication/request/spec.py`
- `care/emr/resources/patient/spec.py`
- `care/emr/resources/report/report_upload/spec.py`
- `care/emr/resources/report/template/spec.py`
- `care/emr/resources/scheduling/schedule/spec.py`
- `care/emr/utils/mfa.py`
- `care/security/authorization/PATIENT_DEPARTMENT_ACCESS.md`
- `care/security/authorization/patient.py`
- `config/auth_views.py`
- `config/settings/AZP_CLOSURE_POLICY.md`
- `config/settings/base.py`
- `config/settings/config.py`
- `config/settings/local.py`
- `config/settings/test.py`
- `config/urls.py`

## Task file inventory and totals

22 logical files (8 renames). production +19/-18; migration +114/-0; tests +140/-6; docs +337/-3.

Rename-aware `git diff --cached --numstat`; final file lengths include unchanged
native content. Tests and documentation are separated from production below.

| Final path (old path for renames) | Added | Removed | Final lines |
|---|---:|---:|---:|
| `care/emr/utils/mfa.py` | 1 | 1 | 62 |
| `care/users/migrations/0029_move_draft_recovery_to_care_suriname.py` | 14 | 0 | 14 |
| `care/users/models.py` | 0 | 4 | 268 |
| `care_suriname/README.md` | 2 | 1 | 92 |
| `care_suriname/draft_recovery/README.md` (from `care/users/draft_recovery/README.md`) | 33 | 2 | 126 |
| `care_suriname/draft_recovery/__init__.py` (from `care/users/draft_recovery/__init__.py`) | 0 | 0 | 0 |
| `care_suriname/draft_recovery/auth.py` (from `config/draft_recovery_auth.py`) | 0 | 0 | 11 |
| `care_suriname/draft_recovery/crypto.py` (from `care/users/draft_recovery/crypto.py`) | 0 | 0 | 95 |
| `care_suriname/draft_recovery/service.py` (from `care/users/draft_recovery/service.py`) | 3 | 3 | 68 |
| `care_suriname/draft_recovery/views.py` (from `care/users/draft_recovery/views.py`) | 7 | 3 | 108 |
| `care_suriname/management/commands/rewrap_draft_recovery_keys.py` | 3 | 3 | 23 |
| `care_suriname/migrations/0002_move_draft_recovery.py` | 100 | 0 | 100 |
| `care_suriname/models/__init__.py` | 1 | 0 | 16 |
| `care_suriname/models/draft_recovery.py` (from `care/users/draft_recovery/models.py`) | 1 | 1 | 29 |
| `care_suriname/tests/test_draft_recovery.py` (from `care/users/tests/test_draft_recovery.py`) | 32 | 5 | 292 |
| `care_suriname/tests/test_draft_recovery_ownership.py` | 107 | 0 | 107 |
| `care_suriname/tests/test_plug_registration.py` | 1 | 1 | 118 |
| `care_suriname/v1_urls.py` | 1 | 1 | 129 |
| `config/auth_views.py` | 1 | 1 | 236 |
| `config/settings/base.py` | 1 | 1 | 758 |
| `docs/development/2026-09-19-draft-recovery-ownership.md` | 247 | 0 | 247 |
| `docs/development/plug-app.md` | 55 | 0 | 302 |


The shared frontend BUS contains this task's exact claim/release only and is
outside the backend commit. No frontend source/configuration was changed. One current frontend document,
`care_fe/docs/core-patches/phase1-draft-key-service.md:16`, still links to the old
backend README; it is deliberately untouched under the frontend-out-of-scope
instruction. Its replacement target is `care_suriname/draft_recovery/README.md`.
The frontend 14 September audit is historical and also remains unchanged.
Commit/push identity is recorded by Git and the completion message; target is
explicitly henry-fork/codex/suriname-clinical-workflows, never upstream origin.
