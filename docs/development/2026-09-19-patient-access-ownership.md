# Completed-department policy ownership and closure — 19 September 2026

## Decision and scope

**Backend source-ownership separation is 100% under the documented definition.**
The final confirmed custom policy is now in care_suriname. This means no remaining
feature-specific implementation or policy body was found in native source beyond
individually documented native-table/write safeguards, small integration calls,
generic seams/fixes, configuration and immutable applied migration history.
It does not mean unmodified upstream, zero plugin imports, independently packaged
release, deployment, clinical acceptance or certification of authorization safety.
The 16 baseline permission failures were later traced to unintended facility-admin
fixture grants and corrected without changing their assertions. A separate
care-team nominee authorization defect found during that cleanup is now fixed and
covered. The browser negative-case gap below remains explicit.

Parent baseline: e6424e8d6f6c679afdd3a1cd5ef9f65bfdb6e583, clean and pushed.
Fork: ece71a878b3764a476d713a163a2f5515db57581, verified with git merge-base
HEAD origin/develop. Cached upstream a749b92794ac175db8839d3d75ca36402a196282;
not asserted to be the newest remote revision. Source identities for this report
are obtained with git log -1 --format=%H -- <this report>. Explicit destination:
henry-fork (HenryAndres33/care.git), codex/suriname-clinical-workflows.

Only the completed-encounter organization lookup moves. Native PatientAccess
keeps active encounter/location lookup, user membership filtering, geographic
organization/direct-patient roles, all RolePermission checks, superuser handling
and list filtering. Against fork, that file now has just two added lines: generic
hook import and scope union, with no deletions. No endpoint, model, schema,
settings default/value, authentication, frontend or migration change.

## Contract, query and security semantics

The plugin callback returns the union of existing facility_organization_cache IDs
for this patient's completed encounters. This includes native ancestor/facility
root IDs, exactly as before. It respects the unchanged default-enabled,
environment-overridable PATIENT_DEPARTMENT_LONGITUDINAL_ACCESS_ENABLED setting.
The query retains its manager/filter/selected column and absence of extra filters;
no hidden change to deleted rows, facility scope or encounter status semantics.
Disabled policy performs zero queries; enabled policy performs one.

The small generic plugs.authorization helper uses existing single-provider
registration. No provider returns an empty set. Duplicate providers fail before
execution, independent of registration order. Noncallable providers, non-set
results, nonpositive/noninteger IDs (including booleans), and callback exceptions
abort role lookup; none is treated as allow or silently ignored. Successful sets
are copied. Native membership and permission checks remain the final decision.
This is additive candidate scope, not a boolean allow/deny override. It cannot
remove native roles. A trusted plugin may expand scope and requires its own
security review. Superuser paths which already bypass role lookup stay native.

Despite the historical "read access" label, baseline find_roles_on_patient is
also used by native write-permission methods and direct PatientPermissionsMixin
serialization. The extraction preserves all these consumers. It does not invent
new permissions or silently remove a write permission held by a matching role.
Native get_filtered_patients remains unchanged; object access and list eligibility
are intentionally not made equivalent by this extraction. An unrelated object
retrieve remains concealed with native 404, not a newly invented 403 contract.

## Verification

Commands: git diff HEAD / fork with --numstat and --unified=0; AST imports/classes/
functions and feature-literal scans; Ruff check/format; Django check;
makemigrations --check --dry-run; exact staged checkout-index export; isolated
manage.py test gate; normalized schema dump comparison; diff/secret/hash scans.
Scratch: /tmp/care-patient-access-ownership-20260919 (not staged).

A read-only dump of care_test is restored only to test_care_access_codex for each
baseline/candidate/staged run. No live database reset or migration. The runner
asserts the isolated host/name, skips migration setup/teardown and disables
reset_sequences only for the existing restored-data form concurrency fixture,
identically on both revisions. Temporary upload buckets are cleaned in finally.

Baseline broad gate: 187 tests, 171 pass, 16 failures. Corrected candidate gate:
241 tests, 225 pass, the same 16 failures. No new runtime failure. New tests cover
same department, unrelated department/facility/user, missing membership/permission,
active/direct native access without contributions, disabled policy, each status,
query count, common write permissions, serialized permissions, unchanged list
scope, retrieve 200 versus unrelated 404, startup registration and generic
absence/duplicate/type/error semantics. Existing native tests are unchanged.
Final exact staged gate: **241 tests, 225 pass, the same 16 failures**, zero
errors. All additional tests pass. Ruff/format pass on eight Python files;
Django system check and migration drift are clean. Normalized physical schema
is identical. Exact staged SQL/role/permission output for all nine synthetic
patients matches baseline. Seven unaffected PatientAccess method ASTs are
identical; only the scope contribution point changes. All 1,387 unclaimed
tracked hashes are preserved; exact index path/diff/secret checks pass.

The original department-denial fixture gives its actor a facility creator/root
role, so access through the cached root is allowed on both revisions. New
isolated department-only fixtures remove that fixture-created membership. The
initial candidate's six new-test failures were fixture assumptions: root role,
root ID in scope, and expected 403 instead of native concealed 404. Correcting
test setup/expectations required no production authorization change. The 16
unchanged baseline failures comprise that department test, ten encounter API/
organization tests, four form read tests and one chronic-diagnosis update test.
Their full identities/assertions match baseline; they are not certified safe.

A separate read-only comparison on the existing local database evaluated all nine
DEMO-SIM patients as Annand at baseline and candidate. Exact SQL text, query counts,
role IDs, read/write results and disabled-policy results are byte-identical.
Annand is not a superuser. One patient depends on this completed-consultation rule:
3ca8d42d-5be0-472b-89c7-eedf09307aa5. The other eight remain unchanged too.

## Browser evidence and honest limitations

The unchanged frontend build was served only on 127.0.0.1:4001, signed in as
Annand. Direct chart navigation for synthetic patient
3ca8d42d-5be0-472b-89c7-eedf09307aa5 worked. Contactmomenten showed the completed
12 September consultation; selecting it showed status Afgerond, department and
saved notes. Refresh retained the same completed contact and readable notes.
Final URL: /facility/77d446f3-659d-40d4-a69a-bfc0f5b7a255/patient/
3ca8d42d-5be0-472b-89c7-eedf09307aa5/urology/chart?view=history&visit=
f44b0794-d1a4-4303-92a8-0e283d1b3add (line wraps only).
Patient, encounter, forms, diagnoses, allergies, medication, report and appointment
GET requests returned 200 after refresh; no HTTP or console errors then.
Initial network-change errors affected three panels and cleared on refresh;
canceled navigation requests were also recorded. No workflow was opened for edit,
no clinical record created/edited/deleted, and no real patient was opened.

The unchanged frontend automatically called token refresh and set_preferences
(recent-patient tracking). Thus this is a read-only clinical check, **not literally
zero background writes**. No preference implementation was changed or bypassed.
No denied synthetic patient exists for Annand among the nine designated fixtures:
baseline already permits all nine. The unrelated-patient negative browser case
was therefore not executed; no patient/user/role was created or altered to stage
it. Isolated tests prove unrelated user/department/facility denials and native
concealed 404, but that is not claimed as a browser pass. Port 4001 was stopped.
No port-4000/shared-service restart, migration or deployment occurred.

## Fresh ownership re-audit

Every remaining native production hunk was reconciled with the prior full review;
unclaimed runtime hashes prove only the PatientAccess hunk changed in native
care/config. The [current complete inventory](2026-09-19-ownership-closure-hunks.md)
records **37 files, 175 hunks, +1,314/−249** versus fork. Test settings, root wiring
and four generic plugs modules are separately listed. No unclassified native
hunk remains. PatientAccess is reclassified from B/F to B/C (generic integration).
No remaining F implementation was found. The additional hunk is a generic
care-team nominee authorization correction suitable for upstream. Added native classes remain only four
nested model Meta classes and the native FormSubmissionMutableSpec table/API
contract. No standalone custom production module/model/action engine remains.
Eight previously restored core paths remain byte-identical to fork.

The 12 direct plugin imports across ten native files are unchanged and rechecked:

| Native path | Count | Disposition and future hook |
|---|---:|---|
| care/emr/api/viewsets/encounter.py | 3 | Two plugin-owned action mixins (generic action seam candidates); locked ConsultClosure legacy-restart veto (native transition-safety integration). |
| care/emr/api/viewsets/form_submission.py | 1 | All-endpoint clinical no-store mixin; generic response policy upstream candidate. |
| care/emr/api/viewsets/medication_request.py | 1 | Same native and contributed response safety. |
| care/emr/api/viewsets/report/report_upload.py | 1 | Same clinical report response safety. |
| care/emr/api/viewsets/scheduling/booking.py | 1 | Plugin-owned operation actions; generic action seam candidate, no operation engine in native source. |
| care/emr/api/viewsets/scheduling/schedule.py | 1 | Plugin overlap validator invoked within native resource locks; generic transactional validation hook candidate. |
| care/emr/api/viewsets/user.py | 1 | Plugin doctor-activation action; generic action seam candidate. |
| care/emr/models/report/template.py | 1 | Plugin hash helper inside native save/provenance invariant; generic lifecycle hook candidate. |
| care/emr/utils/mfa.py | 1 | Interactive authentication-proof callback for draft recovery; common post-auth claim hook candidate. |
| config/auth_views.py | 1 | Same callback for password authentication. |

These are explicit integration exceptions, not proof all imports are unavoidable.
Four mixin imports in three files could plausibly use the existing action seam
later. Native table guards/constraints, closed-write and immutability rules,
API fields matching those tables, generic fixes and extension seams remain.
Configuration (including timezone, workflow gates, audit exclusions and the
policy switch) and applied emr/users migration history remain intentional.
The audit-log exclusion branch remains possible redundant safety code pending
parity proof. Token destroy recursion is already fixed in cached upstream.

All 35 custom models and current migration state remain plugin-owned with pinned
physical tables; no new models or migration edits. Native current state has no
forward relation into a plugin model. Source ownership does not imply the plugin
can be omitted while these documented direct integrations remain. Static scans
cannot prove arbitrary dynamic behavior absent; the conclusion combines full
hunk review, runtime registration guards and exact unchanged-source verification.

## Exact changes and production file sizes

| Production file | Current lines |
|---|---:|
| `care/security/authorization/patient.py` | 133 |
| `plugs/authorization.py` | 29 |
| `care_suriname/contributions.py` | 48 |
| `care_suriname/policies/patient_access.py` | 17 |
| `care_suriname/authorization.py` | 70 |

| Task file | Added | Removed |
|---|---:|---:|
| `care/security/authorization/PATIENT_DEPARTMENT_ACCESS.md` | 14 | 0 |
| `care/security/authorization/patient.py` | 3 | 9 |
| `care_suriname/README.md` | 10 | 0 |
| `care_suriname/authorization.py` | 3 | 4 |
| `care_suriname/contributions.py` | 7 | 0 |
| `care_suriname/policies/README.md` | 31 | 0 |
| `care_suriname/policies/patient_access.py` | 17 | 0 |
| `care_suriname/tests/test_backend_ownership.py` | 3 | 3 |
| `care_suriname/tests/test_patient_access_policy.py` | 147 | 0 |
| `docs/development/2026-09-19-ownership-closure-hunks.md` | 715 | 0 |
| `docs/development/2026-09-19-patient-access-ownership.md` | 201 | 0 |
| `docs/development/2026-09-19-post-extraction-ownership-audit.md` | 10 | 0 |
| `docs/development/2026-09-19-post-extraction-ownership-hunks.md` | 10 | 0 |
| `docs/development/plug-app.md` | 25 | 11 |
| `plugs/authorization.py` | 29 | 0 |
| `plugs/tests/test_authorization.py` | 69 | 0 |

production: +59/−13; tests: +219/−3; docs: +1016/−11.
