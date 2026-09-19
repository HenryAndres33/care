# Patient-directory and command-constant ownership — 19 September 2026

## Scope and result

Parent: `fcfdba421a23ebd7beb3067210cc7962021ef232`, clean
`codex/suriname-clinical-workflows`; upstream fork `ece71a878`.
This batch moves two plugin-only constraint constants and the existing read-only
patient directory. The owner additionally authorized a generic route-priority
seam after a resolver probe proved an ordinary late plugin URL would be shadowed.
No frontend source, migration, live patient data or production deployment changed.

Both constraint strings are unchanged:

- `formsub_cmd_client_request_id_uniq` now declared by
  `care_suriname/models/form_submission_command.py`.
- `formartifact_cmd_request_id_uniq` now declared by
  `care_suriname/models/form_submission_artifact_command.py`.

The plugin models no longer import these constants from native models; the native
models do not import plugin replacements. Class-level constraint identifiers and
`UniqueConstraint(client_request_id)` use exactly the original strings. Historical
migrations remain unchanged. The constants belong beside the only models that
use them; a third generic constants module would add an unnecessary dependency.

## Directory contract and consumers

`care_suriname/api/viewsets/patient_directory.py` owns the action and pagination;
`care_suriname/resources/patient_directory.py` owns its request and response types.
No new persistence model or duplicate patient service was introduced. An AST
comparison confirms the action body/decorators are identical except that the
request DTO reference now points to the plugin-owned class.

- GET `/api/v1/patient/directory/`; reverse name `patient-directory`.
- Required facility UUID, optional name/date_of_birth, limit 25 (1–100), offset ≥0,
  ordering allowlist ±name/phone_number/date_of_birth/external_id.
- Name is stripped; at least two characters if present; name or DOB required.
- Same `can_search_patient_directory` authorization with facility organization
  membership. Facility is the authorization context: the identity search still
  queries all matching native Patient rows, not only patients seen at that facility.
- Same default manager, name icontains and exact DOB filter; external_id tie-break
  except when external_id itself is the primary ordering key.
- Same `{count, results}` and six identity fields: id, name, gender, phone_number,
  date_of_birth, year_of_birth. No full chart permission is implied.
- Same authentication/permission classes, exception handler, middleware chain,
  GET-only method contract, OPTIONS name/description, DRF schema annotation and
  basename/detail metadata. Unauthenticated GET remains 403 in this configuration.
- DEBUG DefaultRouter format aliases, including `.json` reverse output without
  an added slash, are retained. Native UUID detail/PUT/PATCH/DELETE remain native.

Frontend consumers were read, not edited: `patientDirectoryApi.ts` is used by
`useUrologyPatientSearch` (patient finder, central admin patient page, programme
selection) and `useUrologyPatientTypeahead` (workspace header and Agenda filter).
Search uses server-side count/offset/order; typeahead requests six matches. There
is no native frontend directory API declaration. No URL/query key/default or UI
behavior change is required.

## Generic route-priority mechanism

At baseline, resolving `/api/v1/patient/directory/` selects `patient-directory`.
A temporary class in a read-only resolver probe removed only that action; the
same path then resolved as `patient-detail` with `external_id="directory"`.
The native matcher is `(?P<external_id>[^/.]+)`, and ordinary plugin includes
come later. No request or source mutation was needed to prove this collision.

`config/urls.py` gathers optional `priority_urlpatterns` from plugin v1 modules
and passes them to generic `plugs.urls.with_priority_routes` after ordinary routes
are assembled. This opts literal compatibility paths ahead of broad parameter
matching without changing normal plugin order or tightening native ID patterns.
There is no patient/Urology route string in core.

The helper accepts only flat literal `path()` declarations. It rejects duplicate
paths/reverse names, parameterized/regex/nested priority declarations, exact host
route collisions (even after a broad matcher), and collisions through generated
format aliases. Empty opt-in returns the original pattern list unchanged.
DEBUG aliases are generated with DRF's existing suffix utility from literal regex
entries to preserve DefaultRouter reverse behavior. This generic seam is an
upstream PR candidate; it is not a general plugin override mechanism.

`care_suriname/v1_urls.py` declares the one priority directory path. New plugin
features should still use `/api/care_suriname/`; priority legacy v1 paths require
a reviewed compatibility reason. No catch-all was added. Resolver tests verify
unknown descendants remain unknown and all native patient methods stay native.

## Native restorations and residual metrics

- `care/emr/resources/patient/spec.py`: −17; byte-identical to fork again.
- `care/emr/api/viewsets/patient.py`: +1/−67; only the generic date-of-birth filter
  remains against fork (+2/−1). Native DELETE and all other patient behavior stay.
- `care/emr/models/questionnaire.py`: −1 constant, native invariants retained.
- `care/emr/models/report/report_upload.py`: −1 constant, native invariants retained.
- `config/urls.py`: +8 generic wiring lines; new `plugs/urls.py`: 119 lines.
- Four original directory tests (143 lines) rehomed from native patient tests;
  added contract, authentication, ordering, pagination and ownership coverage.

Current care/config non-test Python inventory: **37 files / 173 hunks /
+3,071/−225**, down from 38 / 178 / +3,149/−226. This count excludes test settings,
root plug wiring and the new generic helper. Including `plug_config.py` (+13/−1)
and `plugs/urls.py` (+119/−0), the non-test source envelope is **39 files,
+3,203/−226**. Counts are Git line deltas, not weighted completion scores.
Eight formerly changed native files now match the fork; the eighth is patient spec.
Native care/config production→plugin imports remain **12 files / 28 statements**.

F-category native file incidence falls **12 → 8**: the directory's two files and
the constants' two files no longer contain those custom implementation hunks.
The eight remaining F files are condition/form_submission/medication_request/
valueset viewsets, native condition model/spec, and settings base/config. Remaining
clusters are form/artifact orchestration, diagnosis commands, medication commands,
local terminology policy, Urology preference/required-form configuration and
clinical-domain vocabulary coupled to native Condition.

**This batch is complete; full separation is not.** Custom models remain 35/35
plugin-owned. Two of eight implementation clusters in the closure audit are now
closed: **25% of that specifically enumerated backlog** (33⅓% reduction in its
F-file incidence). A weighted overall backend completion percentage has not been
established and would be misleading. Do not call the remaining 75% of this backlog
75% of the entire backend.

## Verification and baseline failures

Focused tests cover directory permissions/minimum criteria, exact identity shape,
DOB-only and combined search, counted offset/default/capped paging, all eight
sort options, empty results, missing facility, unauthenticated GET, OPTIONS,
405/404 behavior, native UUID methods, schema/reverse, constant uniqueness,
registration and source ownership. Generic seam tests cover absent opt-in,
parameter/exact collisions, duplicate names, suffix aliases and unknown routes.

Baseline OpenAPI and candidate OpenAPI for the directory compare equal as parsed
JSON, including the existing inherited `meta` schema field (omitted from actual
directory JSON). Baseline also confirms unauthenticated 403 and `.json` reverse
without trailing slash. Initial new test expectations for these were corrected
against evidence, not by changing runtime behavior.

Tests run in a disposable restored copy of the existing isolated test database,
`test_care_directory_codex`, with a scratch DiscoverRunner that skips database
setup and teardown. **No migrations were run**, no schema files changed, and no
live database was reset. The same harness is used for baseline and candidate.
The test stack's storage credentials initially caused five profile-image errors,
followed by a missing collected favicon. Both reproduce at fcfdba421. The isolated
harness reads test MinIO credentials in memory, uses a temporary upload bucket,
collects static assets only in scratch and deletes that bucket afterward.

One broader failure remains at both baseline and candidate:
`PatientDepartmentAccessTest.test_completed_encounter_of_other_department_gives_no_access`
expects denial but receives access. Baseline source is unmodified. This is an
existing authorization-policy/test-fixture discrepancy, **not certified harmless**
and not fixed outside this batch. Directory-specific outsider/authorized-facility
tests pass. Record broader results with this failure visible; do not label the
whole backend suite green.

The exact staged-tree gate and final line counts are recorded below. No browser
or unit result here certifies unrelated clinical workflows or authorizes deployment.

## Signed-in browser evidence

Used the in-app Browser skill, owner-authorized account and existing synthetic
`DEMO-SIM-0912-S05 Noor Cysto`. No patient chart was opened or mutated. A temporary
SPA server on `127.0.0.1:4001` served the previously verified frontend staged build
from the f6b8953c note-lab pass. That origin uses the existing API fallback to
localhost:9000, avoiding the localhost:4001→9002 map without editing any frontend
source, .env or build. The independent frontend source audit is out of scope.

| Screen/action | Outcome |
|---|---|
| Staff login | Signed in successfully; credentials not written to source/evidence |
| `/facility/77d446f3-659d-40d4-a69a-bfc0f5b7a255/urology/patients/search` | Synthetic-only name search returns one correct identity; HTTP 200 |
| Refresh same URL | Name retained; same result and URL |
| Direct `/admin/patients` | Same synthetic identity, expected directory controls |
| Synthetic name plus `-no-match` | Honest empty-result message; HTTP 200 |
| Admin at 390×844 | Search controls and synthetic row usable |
| Facility direct deep link at 390×844 | Same result and dossier action visible; no chart opened |

All observed directory requests retained facility/limit=25/offset=0/ordering=name
and returned 200. Console errors: none. HTTP errors: none. One facility-detail GET
was canceled (`net::ERR_ABORTED`, canceled=true) during navigation; not a directory
failure. Authentication token-refresh POSTs occurred; **no clinical/data mutation
requests** were observed. Viewport override reset, browser tab closed, isolated
server stopped and absence of a port-4001 listener confirmed. Port 4000 was untouched.

## Exact staged-tree gate and file metrics

The index was exported with `git checkout-index --all` to scratch and all indexed
blobs compared byte-for-byte to the export. One-shot `care_local` containers ran
against that tree with test configuration; no dependency on excluded dirty files.

- **33 focused tests pass** (nine directory API tests plus 24 ownership/route/
  registration/constant tests).
- Combined focused + native patient/user/security gate: **119 run, 118 pass,
  one existing department-access failure**. Baseline: 90 run, 89 pass, that exact
  same failure. Four baseline native directory tests now run from the plugin.
  No new failures; the broader suite is not wholly green.
- Scoped Ruff lint and format: all 17 Python files pass.
- Django system check: no issues. `makemigrations --check --dry-run`: no changes.
- Directory OpenAPI JSON: identical to baseline. Action AST: identical after
  normalizing only the relocated request-class reference.
- Working/index diff checks, excluded-path checks and staged secret scans pass.
  All 1,336 unclaimed tracked backend hashes are preserved.
- Normalized restored-test schema hash before/after staged tests is identical:
  `335c6f874dbd6be787af1d4f50ddd49770396ab7482a570f4ce97db0d476b1d1`.
  This compares the same restored database before/after tests. A raw original
  dump versus restored dump differs in PostgreSQL's equivalent array-cast
  deparsing; it is not claimed byte-identical across restore. No migration replay.
- Temporary test upload buckets are removed by the harness; only the batch's
  disposable database is removed afterward. No shared test database is reset.
- Shared BUS claim/release stays outside the backend commit. No frontend,
  environment, migration or generated artifact is staged. No deployment.

Changed production files (whole-file length is distinct from task-only diff):

| Path | Current lines | Task + / − |
|---|---:|---:|
| `care/emr/api/viewsets/patient.py` | 518 | 1 / 67 |
| `care/emr/models/questionnaire.py` | 313 | 0 / 1 |
| `care/emr/models/report/report_upload.py` | 147 | 0 / 1 |
| `care/emr/resources/patient/spec.py` | 295 | 0 / 17 |
| `care_suriname/api/viewsets/patient_directory.py` | 64 | 64 / 0 |
| `care_suriname/models/form_submission_artifact_command.py` | 40 | 2 / 1 |
| `care_suriname/models/form_submission_command.py` | 55 | 2 / 1 |
| `care_suriname/resources/patient_directory.py` | 45 | 45 / 0 |
| `care_suriname/v1_urls.py` | 145 | 16 / 0 |
| `config/urls.py` | 127 | 8 / 0 |
| `plugs/urls.py` | 119 | 119 / 0 |

Other changed paths are `care/emr/tests/test_patient_api.py`,
`care_suriname/tests/{test_command_constraint_ownership,test_patient_directory,
test_patient_directory_ownership}.py`, `plugs/tests/{__init__,test_priority_urls}.py`,
`care_suriname/README.md`, this report, the dated final audit correction,
`docs/development/patient-directory-pagination-core-patch.md`, and
`docs/development/plug-app.md`. Commit numstat is the exact task-only line ledger;
no rename-detection assumptions are needed to interpret the rehomed tests.
