# Form and artifact command ownership — 19 September 2026

## Scope and preserved behavior

Baseline `1923ded4237476a5b4638319b2be7def20a0d863`; branch
`codex/suriname-clinical-workflows`; fork `ece71a878b3764a476d713a163a2f5515db57581`.
All six form commands now live in care_suriname/api/viewsets/form_commands:
create-draft, update-draft, finalize, amend, enter-in-error and generate-artifact.
The existing generic additive viewset-action seam registers them on the same
native host. It now accepts a nonempty tuple of plain classes and private static
helpers, rejecting duplicate helpers, host overrides and route/name collisions.
No provider leaves the native class unchanged. No route-priority change.

The native form viewset shrinks from 1,589 to 264 lines (+3/-1,328), retaining
CRUD, all authorization, legacy draft guards, native update/version/immutability
checks, row locking and shared generic conflict responses. Its only direct
plugin import is the no-store response safety mixin. All command ledgers,
series/replay/hash/conflict policy, Urology validation, note-lab linkage and
artifact render/upload/replay/compensation move together without copying storage
models, API specs, renderer or services. No model, migration, frontend or schema
change. Every production module remains below the repository's 300-line limit.

Method inventory accounts for all 57 original methods exactly once: 53 have
identical ASTs (including every action/decorator and retained native method).
Four private static wrappers become instance methods solely to replace the old
FormSubmissionViewSet reference with self: response_dump_validation_response,
raise_version_conflict, raise_immutable_conflict and raise_non_draft_conflict
(all have a leading underscore). Normalizing only those binding changes gives
identical ASTs for all 57. This removes the circular native class dependency.
The historical artifact logger category is intentionally unchanged.

## Verification

Commands: git diff against baseline/fork, Python AST/import scans, DRF router
and Spectacular schema inventories, manage.py test, Ruff check/format,
manage.py check, makemigrations --check --dry-run, schema/diff/secret/hash checks.
Scratch evidence is /tmp/care-form-ownership-20260919 (not committed).
The exact index is exported with git checkout-index and tested in isolation.
Form, diagnosis and medication route inventories and OpenAPI output are
byte-identical to baseline: regex/order, URL names, format aliases, mappings,
kwargs and schemas. New guards protect six action origins, inherited native
CRUD/auth/lock/no-store identity, reverse/dispatch and unknown/native detail paths.

A fresh read-only dump of care_test is restored only into test_care_form_codex
for each run. The scratch runner asserts that target and skips migrations.
Temporary MinIO buckets are removed in finally. Initial baseline and candidate
both reproduced a restored-role sequence collision in a concurrency test;
the scratch harness disables reset_sequences for the three concurrency classes
identically on both revisions. Repository tests are not changed for this issue.
Corrected baseline: 187 tests, 179 pass, eight failures, zero errors.
Exact staged gate: 226 tests, 218 pass, the same eight baseline failures and
zero errors. The 39 additional ownership/seam/registration tests pass. Ruff and
format pass on 20 Python files; system check and migration drift are clean.
Normalized schema dumps are identical. All 1,370 unclaimed tracked hashes match
the pre-task snapshot. Index path and secret checks pass. No generated output
is staged. The exact staged route/OpenAPI comparison also passes.

The eight baseline failures are permission expectations (not new regressions):
four FormSubmission API list/retrieve checks return 200 rather than 403;
artifact completed-encounter creation returns 201 rather than 403; artifact replay
after closure returns 200 rather than 403; the inherited note-lab denied-permission
test fails in both note-lab classes (200 rather than 403). They remain unresolved.
This ownership extraction does not certify that authorization behavior as safe.
The gate also covers Urology operation responses, encounter/consult closure,
clinical no-store, workflow commands, native CRUD, version/hash/replay conflicts,
series heads, immutability, transaction rollback, real PostgreSQL concurrency,
real PDF rendering, storage/upload/ledger failure compensation and cleanup.

## Signed-in synthetic browser evidence

The unchanged committed frontend was served only on 127.0.0.1:4001 against the
existing local backend; no shared service or port 4000 was restarted. Annand used
synthetic patient 78fd6f11-e0f1-4be5-a988-04a351c7cc58 and existing encounter
0cfc3153-a167-4044-b38a-a32d2c620769. The note explicitly says softwaretest,
geen werkelijke patiëntenzorg and contains DEMO-SIM-20260919-FORM-OWNERSHIP.
Concept save, full refresh/read-back, finalize and immutable controls passed.
Create returned 201; update/finalize returned 200. PDF generation returned 201;
report download and object fetch returned 200. After refresh, generation returned
200/replayed=true with the same artifact ID and checksum.

Retained synthetic submission: bcf86d53-6cd5-4e81-a78a-426d98d2af8c.
Retained synthetic artifact: b86415d4-aed7-4057-b6fa-6dca5e8a0654, source version 3.
The 11,284-byte stored PDF checksum is
1bcc6e7503339c8de942334d5546a35dd942e0dd8ee61563835358c6e63e817c;
its recomputed source hash is
1babab82383c971c06181d6eac0bdd34e486f3ac5fc5bbe9fe30c604cafd90f3.
Both match API provenance. The browser captured successful download responses
but no response body; a read-only retrieval of that exact stored artifact proved
the byte hash. PDF text and rendered page were inspected: exact synthetic note,
correct patient/author/date, finalized status, one clean page without clipping.

Supported UI removal was attempted but the in-app browser throws
"prompt() is not supported" before the request. No enter-in-error command was
sent. The clearly marked finalized synthetic note and immutable PDF therefore
remain intentionally; no existing note or encounter was changed. No frontend
workaround or direct database deletion was used. This cleanup limitation is not
a backend regression: the same unchanged frontend bundle calls window.prompt.
Network inspection found no failed form/artifact request. Two existing
HTTP-only draft-recovery-key requests returned 403, and error telemetry returned
403; one navigation was canceled (ERR_ABORTED). Electron CSP warnings and the
unsupported cleanup prompt were the console findings. Port 4001 was stopped.

## Ownership status

Direct native imports of care_suriname fall from ten files/24 statements to ten
files/12 statements. No core file becomes fully identical to fork in this batch:
the native form file deliberately retains generic CRUD and safety patches.
All eight finite backlog groups are extracted. A separate fresh audit must still
inspect every remaining native hunk before any full-source-ownership claim.
No deployment, migration or real-patient write occurred. Only the authorized
synthetic note/artifact and their audit/command records were created locally.

## Exact committed scope

| Path | Added | Removed |
|---|---:|---:|
| `care/emr/api/viewsets/form_submission.py` | 3 | 1328 |
| `care/emr/tests/test_form_submission_artifact.py` | 2 | 2 |
| `care/emr/tests/test_form_submission_workflow.py` | 7 | 3 |
| `care_suriname/README.md` | 11 | 0 |
| `care_suriname/api/viewsets/form_commands/README.md` | 39 | 0 |
| `care_suriname/api/viewsets/form_commands/__init__.py` | 25 | 0 |
| `care_suriname/api/viewsets/form_commands/actions.py` | 68 | 0 |
| `care_suriname/api/viewsets/form_commands/artifact_action.py` | 165 | 0 |
| `care_suriname/api/viewsets/form_commands/artifact_replay.py` | 154 | 0 |
| `care_suriname/api/viewsets/form_commands/artifact_responses.py` | 109 | 0 |
| `care_suriname/api/viewsets/form_commands/artifact_source.py` | 157 | 0 |
| `care_suriname/api/viewsets/form_commands/draft.py` | 214 | 0 |
| `care_suriname/api/viewsets/form_commands/errors.py` | 18 | 0 |
| `care_suriname/api/viewsets/form_commands/execution.py` | 253 | 0 |
| `care_suriname/api/viewsets/form_commands/mutations.py` | 145 | 0 |
| `care_suriname/api/viewsets/form_commands/responses.py` | 197 | 0 |
| `care_suriname/contributions.py` | 7 | 0 |
| `care_suriname/tests/test_backend_ownership.py` | 0 | 12 |
| `care_suriname/tests/test_form_command_ownership.py` | 99 | 0 |
| `care_suriname/tests/test_note_lab_ownership.py` | 2 | 2 |
| `docs/development/2026-09-19-final-backend-separation-audit.md` | 11 | 0 |
| `docs/development/2026-09-19-form-command-ownership.md` | 143 | 0 |
| `docs/development/plug-app.md` | 16 | 6 |
| `plugs/tests/test_viewset_actions.py` | 30 | 0 |
| `plugs/viewset_actions.py` | 25 | 8 |

production: +1540/−1336; tests: +140/−19; docs: +220/−6.
