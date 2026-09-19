# Final note-lab ownership extraction — 19 September 2026

Baseline: `5d79daccc`, branch `codex/suriname-clinical-workflows`.
Frontend contract: committed `f6b8953c5cac5772eb3681d7fdda3c08dea33557`.
This is local verification, not deployment or clinical acceptance.

## Scope and preserved work

The six initial dirty files were snapshotted before edits. Their v3 contract,
compact shared-date parser, unknown-date persistence, documentation and tests
remain in this change. Four files moved with `git mv` from
`care/emr/resources/form_submission/` to `care_suriname/resources/form_submission/`:
`commands.py`, `note_lab_text.py`, `note_labs.py`, `NOTE_LABS.md`.
The two dirty custom tests and their dependent `test_note_lab_stress.py` moved
from `care/emr/tests/` to `care_suriname/tests/`.

Direct production consumers: the existing native form-submission viewset safety
hook; plugin consult-closure/correspondence viewsets; correspondence source,
delivery and correction. Five adjacent native test modules have import-only
updates. Ruff reordered the changed imports. No compatibility shim remains.

The parser defects were reproduced before the move: the held edits rejected a
legacy descriptive heading and silently accepted Natrium while skipping malformed
`CRP:7.4 mg/L`. At baseline the legacy heading was accepted; compact rows were
unsupported (returned no rows). Focused regressions now require legacy parity
and atomic rejection of malformed supported rows. These are bounded compatibility
fixes, not a rewrite of the lab contract.

Reports consume stored note text; they do not parse rows independently. The
unchanged snapshot hash is shared by form artifacts and correspondence. Clinical
storage remains native request/report/observation plus existing provenance links.
No URLs, permissions, command names, tasks, model/schema/content-type state,
migrations, user draft-recovery or frontend source changed.

## Verification

Working-tree scoped Ruff and formatting pass. Django system check: no issues.
`makemigrations --check --dry-run`: no changes detected. Disposable verification
containers use the isolated `care-test` network and a dedicated test database;
no shared clinical database is reset or migrated.

The exact indexed Python source passed 198 application tests and a separate
92-test consumer gate (290 test entries). The 14 parser/ownership tests and three
intentional rollback cases also passed separately; they are subsets, not extra
unique tests. Scoped Ruff and format pass for 18 Python files. Staged Django
system check reports zero issues; `makemigrations --check --dry-run` reports no
changes. Working/index diff checks and staged secret scan pass.

Application gate: `manage.py test care_suriname.tests` plus
`care.emr.tests.test_form_submission_workflow`, `test_form_submission_artifact`,
`test_correspondence_compilation`, and `test_correspondence_continuity`.
Consumer gate: `care.emr.tests.test_consult_closure`,
`test_correspondence_delivery`, and `test_form_submission_api`.
Both ran serially in disposable `care_local` containers on network `care-test`,
with dedicated `DJANGO_TEST_DATABASE_NAME=test_codex_note_labs_*` databases.
The index was exported with `git checkout-index --all --prefix=...`; no excluded
working files were copied into it. Final evidence-only Markdown was updated after
the application runs; indexed production/test blobs remain byte-identical.

Infrastructure corrections, reproduced rather than attributed to this move:

- Scratch Django startup generates an ignored JWT-key file; a read-only mount
  initially refused it. Writable scratch fixes this; no generated key is staged.
- The first isolated rollback checks lacked generated static assets. Rendering
  Django's error page with `DEBUG=False` raises a missing
  `images/favicons/favicon.ico` error at both `5d79daccc` and the candidate.
  `manage.py collectstatic --noinput` in scratch fixes it. All three affected
  rollback checks and the final 198-test gate then pass.
- Ruff's read-only scratch cache error is avoided with `--no-cache`.
- Redundant initial full runs were interrupted in the six-case historical
  correspondence migration-replay class; those runs are not claimed as passed.
  That class has an import-only edit, imports successfully, and was excluded from
  the final application gate. Full migration rollback rehearsal/full backend
  suite were not completed: this task changes no model or migration.

There are no unresolved application-test failures in the completed gates.
Production source/runtime imports resolve directly to the plug, the four native
paths are absent, and the native exception file set is unchanged (ten before and
after). All unclaimed backend tracked/untracked source hashes match the initial
snapshot. Frontend `.env` and all three `.orig` hashes match the prior preserved
snapshot; frontend source is untouched.

## Browser evidence and retained synthetic records

The exact committed frontend was rebuilt in scratch with the existing local
backend mapped to port 4001; build passed (153 PWA precache entries). The initial
retained build mapped 4001 to backend 9002 and returned login 401; no credentials
or configuration were changed. The rebuilt frontend used backend 9000.

Account: owner-authorized Annand; only designated synthetic patient
`DEMO-SIM-0912-S05 Noor Cysto`, existing encounter
`0cfc3153-a167-4044-b38a-a32d2c620769`. No encounter was created/closed.
The patient-only note route correctly refused save with missing encounter context
and made no request; that unsaved note was discarded. Normal chart contact
navigation supplied the existing encounter for the persistence checks.

- Known-date `.lab` insertion: native date setter plus bubbling input/change
  events were needed after ordinary automation did not update React state.
  Apply became enabled; editor showed `2026-09-17`, creatinine `85 µmol/L`,
  CRP `7.4 mg/L`, no unresolved markers. The same marked content was saved in
  the existing encounter, refreshed and read back, finalized, and rendered as
  a one-page PDF. PDF DOM and visual inspection confirmed date/values/units and
  synthetic patient identity.
- Unknown-date `.lab` insertion: selected CRP, chose **Datum onbekend**, entered
  `3.2`, applied, saved and refreshed. `Afnamedatum: onbekend` and `3.2 mg/L`
  remained unchanged, with no unresolved markers. A read-only query limited to
  these two notes confirms unknown request/observation dates are null and known
  dates are `2026-09-17T03:00:00Z` (midnight Paramaribo).
- Save/read/finalize/PDF operations succeeded. The browser network buffer later
  reported eviction, so it is not a complete all-session network audit. Captured
  save/PDF requests succeeded; navigation canceled some in-flight reads.
- Cleanup attempted only on the marked unknown-date note. Existing frontend
  Delete calls `window.prompt()`: the in-app browser raises `prompt() is not
  supported`, exposes no dialog, and sends no deletion request. This is unchanged
  frontend code, not a backend regression. Its automatic Sentry telemetry returned
  403. No frontend workaround or direct database cleanup was attempted.
- The browser tab and isolated 4001 server were stopped; listener absence checked.
  Port 4000, production VM/deployment and shared backend services were not restarted.

Intentionally retained because browser audit-preserving deletion was unavailable:

| Marked synthetic note | CARE identifier | State |
| --- | --- | --- |
| DEMO-SIM-20260919-PLUGIN-KNOWN | `2be5c15a-116d-451e-baf6-401c9f84a93c` | Finalized, one PDF |
| DEMO-SIM-20260919-PLUGIN-UNKNOWN | `067f6f25-fd8d-4c19-853b-7286387b7e42` | Draft |

Three native request/report/observation chains and their provenance links remain:
known creatinine report `e8c01aab-f510-48b6-ad5f-b35cacf6cedf`, known CRP report
`29547c2e-f791-4976-a083-a602acb44e0c`, unknown CRP report
`d96eb03c-4c58-4515-812e-b7a896b5c640`. These are clearly marked software tests on
the designated synthetic patient. Clinical audit/history is not hard-deleted.
Only ordinary app writes required for these checks (including automatic draft
recovery/preference requests) occurred; draft-recovery implementation is untouched.

## Claim and handoff

Exact paths were claimed in shared `care_fe/.agents/BUS.md` before edits. That
shared cross-repository record is not swept into the backend commit. Source verification is complete; the exact task index is reserved for the
owner-authorized backend commit and explicit fork-branch push. Native safety-patch
exceptions remain documented in `plug-app.md`; their file set is unchanged.
Users-app draft recovery is a separate decision. This report is the backend
handoff; no frontend handoff/source files are edited by this extraction.

## Production file inventory

| Current path | Lines | Change |
| --- | ---: | --- |
| `care_suriname/resources/form_submission/commands.py` | 164 | Relocated held module |
| `care_suriname/resources/form_submission/note_labs.py` | 180 | Relocated held module |
| `care_suriname/resources/form_submission/note_lab_text.py` | 165 | Relocated held module |
| `care/emr/api/viewsets/form_submission.py` | 1589 | Import path/order only |
| `care_suriname/api/viewsets/consult_closure.py` | 1733 | Import path/order only |
| `care_suriname/api/viewsets/correspondence.py` | 984 | Import path/order only |
| `care_suriname/correspondence/source.py` | 214 | Import path/order only |
| `care_suriname/correspondence/delivery.py` | 750 | Import path/order only |
| `care_suriname/correspondence/correction.py` | 1529 | Import path/order only |

The ten pre-existing native production import files are unchanged as a set.
The earlier plug-app list omitted report/report_upload.py; the dated correction
now records its existing clinical no-store dependency explicitly. No exception
was added. The three relocated production modules are all below 300 lines; the
large existing viewsets changed only their import blocks.

## Exact additional paths and history

Import-only adjacent test edits:
`care/emr/tests/test_correspondence_compilation.py`,
`care/emr/tests/test_correspondence_continuity.py`,
`care/emr/tests/test_correspondence_correction_migration.py`,
`care/emr/tests/test_form_submission_artifact.py`,
`care/emr/tests/test_form_submission_workflow.py`.
New guard: `care_suriname/tests/test_note_lab_ownership.py`.
Documentation: relocated `NOTE_LABS.md`, `care_suriname/README.md`,
`docs/development/plug-app.md`, and this report. Shared BUS contains only the
appended task claim/release; unrelated records are preserved and not committed
through the backend repository.

All seven relocations used `git mv`. The parser's held rewrite falls below Git's
default rename-similarity threshold; `git diff -M25%` / `git log --follow -M25%`
recognize its history. Original additions were `6d67a737f` (note labs) and
`680105326` (custom workflow commands). The absence of native compatibility shims
is intentional. Use rename-aware counts when separating movement from edits.

The users-app draft-recovery decision and documented core safety patches remain;
this completes the previously held four-module exception, not a fresh audit of
all unrelated native CARE deltas. No deploy, production VM action, real patient
write, backend service restart, model/schema/content-type change or migration was
performed. Ephemeral test-database setup/teardown is separate from live schema.
