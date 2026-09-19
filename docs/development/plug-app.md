# The `care_suriname` plug app

## Current source and ownership

Final source: `0ed1b6e10ac1a7692f6114cc8647306ce2a43d9d` (19 September 2026).
**Backend source ownership is 100% under the documented definition:** all 35
custom models and all identified custom workflow/policy implementations belong
to `care_suriname`. Native differences are individually reviewed native-table
and write safeguards, integration calls, generic seams/fixes, configuration and
immutable applied migration history. This does not mean unmodified upstream,
zero plugin imports, independent packaging, deployment or clinical/security
certification. The final source gate retained 16 baseline failures; see the
[current fixture triage](2026-09-19-backend-cleanup-triage.md).

The [final report](2026-09-19-patient-access-ownership.md) records evidence and
browser limitations. The [current hunk inventory](2026-09-19-ownership-closure-hunks.md)
records 37 native production files, 175 hunks, +1,314/−249 against fork
`ece71a878b3764a476d713a163a2f5515db57581`. Root wiring, generic `plugs` modules
and test settings are separately counted. Earlier audits are historical.

## Architecture and installation

The repository contains the Django app at `care_suriname/`. `plug_config.py`
adds it through `LocalPlugManager.get_apps()` and `LOCAL_PLUG_APPS`; this local
installation requires no separate pip install. Install the pinned backend
requirements and retain the app in the plug configuration. A separately released
Python package is a future target, not a currently supported omission of the app.

`care_suriname/apps.py` explicitly registers checks, encounter extensions and
additional authorization handlers at Django startup. `tasks/__init__.py`
registers correspondence scans; `contributions.py` declares lazy actions,
model-free policies and settings. Keep registration tests: apparent absence of
ordinary imports is not proof that a module is unused.

Dependency direction is plug → CARE. Native models must not point forward to
plugin models. Custom models pin existing physical tables. See the
[plugin layout and rules](../../care_suriname/README.md).

Generic integration points:

- Standard plug URLs mount at `/api/care_suriname/`. Existing compatible endpoints
  retain `/api/v1/` through optional `v1_urls` registration in `config/urls.py`.
- `plugs.urls.with_priority_routes` admits collision-checked literal paths before
  native parameter routes. Only the patient directory currently opts in; no
  catch-all or host-route override is allowed.
- `plugs.viewset_actions.with_contributed_actions` adds plugin action method
  classes without overriding native CRUD/helpers. Diagnosis, medication and
  form/artifact commands use it; duplicate helpers/routes fail closed.
- `plugs.contributions` provides conflict-checked settings, schemas and policy
  callbacks. `plugs.authorization.patient_organization_ids` adds candidate
  organization scope for completed encounters. Native membership, permissions,
  read/write role semantics and list filtering remain authoritative; invalid or
  duplicate providers and callback errors abort lookup without an allow fallback.

## Frozen native exception allowlist

Ten native files contain twelve direct plugin imports at the final source:

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


Do not add or repurpose an exception. New actions use generic contributions.
Four action-mixin imports in three files are candidates for migration to that
seam after route/permission proof. Native safeguards cannot simply be removed:
locks, closed-write vetoes, immutability, provenance, constraints and native table
API fields remain intentional. Configuration and applied `emr`/`users` migrations
also remain. The audit-log exclusion branch is retained pending parity proof.
The current inventory is the complete residual list, not just this import table.

## Migration and deployment procedure

These are operator instructions for a separately authorized deployment. Source
completion does not establish which migrations are applied on a laptop/server.
This documentation cleanup applies none and restarts no service.

1. Inspect the target's actual revision and `python manage.py showmigrations --plan`.
   Back up PostgreSQL and matching MinIO/media (`scripts/care-suriname-backup.sh`),
   plus the draft wrapping-secret backup required by the draft-recovery contract.
2. Rehearse on a disposable isolated stack from both the target backup and an
   empty database, including forward → reverse → forward. The historical scripts
   `scripts/phase2/rehearse-migration.sh` and `rehearse-step2.sh` cover their pinned
   Phase 2 boundaries; do not treat their old expected plan as the current head.
3. Record **before** snapshots, row counts, table/schema checksums, content-type
   IDs, permissions, audit references and artifact links. Quiesce writes/workers
   for the authorized migration window. Do not restart a worker casually:
   `scripts/celery-dev.sh` runs migrations on startup.
4. Apply the reviewed plan with matching code and migrations, then compare all
   snapshots and run `python manage.py check` and
   `python manage.py makemigrations --check --dry-run`. Read back the affected API
   and stored artifacts before resuming writes. Record target and deployed SHA.

The extraction transitions remain relevant to plan review:

| Transition | Effect |
|---|---|
| `emr.0107` | Moves the artifact relationship to custom revision → native report; copies links and changes the provenance constraint. This is a physical schema/data change. |
| `emr.0108` + `care_suriname.0001` | Moves 34 models' state to the plug with pinned tables; relabels content types in place, preserving IDs and permissions. |
| `users.0029` + `care_suriname.0002` | Moves draft-key state and relabels its content type; keeps `users_draftrecoverykey`. |

State transfers do not rename/copy tables. Content-type relabels fail closed on
unexpected identities; do not delete duplicate identities to force a migration.
Applied historical migrations remain intact. Later command/policy extractions,
including final patient scope, are code-only and require no new migration.
Celery correspondence task module names changed during extraction; deploy matching
workers/beat together and verify outbox rescan continuity (four 60-second scans).

## Rollback

Select the exact transition and matching previous code; never restore an old
layout over a dirty worktree. Code-only ownership batches roll back as complete
units without a database rollback. For schema/state changes keep migration files
available while reversing, and restore matching code before resuming services.

- Draft-key ownership rollback targets `users 0028`, reversing plugin0002 and
  users0029 together. Never reverse users0028 or delete wrapping keys/ciphertext
  while unsent drafts remain. Follow the [draft recovery runbook](../../care_suriname/draft_recovery/README.md).
- The earlier 34-model move reverses plugin0001/emr0108 back to emr0107. First
  account for the later draft-key dependency; the old Phase 2 zero-app recipe is
  not a standalone current rollback command.
- Reversing emr0107 restores the former artifact relationship; verify all links
  and constraints. Restore the coordinated database/media backup if required.

Do not leave new code running with old app state: `post_migrate` can create target
content types that make the next forward relabel fail. Rehearse the complete
chosen rollback and verify content types, permissions, keys and artifacts.

## Verification

Run Ruff/format for touched Python, focused permission/API tests, startup and
ownership guards, Django check, migration drift and `git diff --check`.
Use a disposable database on the isolated test stack, never reset the development
DB or shared `care_test`. The [cleanup triage](2026-09-19-backend-cleanup-triage.md)
records the exact current baseline/candidate gate and unresolved follow-ups.
The full backend suite remains a deployment gate when migrations/shared behavior
change. Previously reported discharge-concurrency isolation failures belong to
another suite and must be reproduced at the same baseline before attribution.

## Historical implementation record

The [Phase 1/2 and subsequent extraction history](plug-app-implementation-history.md)
preserves the original dated chronology, old counts and transition-specific
rehearsals. Those entries are superseded for current ownership and operations.
The following anchors preserve former deep links:

<a id="current-closure-status--19-september-2026"></a>
[Historical: Current closure status — 19 September 2026](plug-app-implementation-history.md#current-closure-status--19-september-2026)

<a id="what-it-is"></a>
[Historical: What it is](plug-app-implementation-history.md#what-it-is)

<a id="the-one-seam-configurlspy"></a>
[Historical: The one seam (`config/urls.py`)](plug-app-implementation-history.md#the-one-seam-configurlspy)

<a id="what-deliberately-stays-in-core-this-phase"></a>
[Historical: What deliberately stays in core (this phase)](plug-app-implementation-history.md#what-deliberately-stays-in-core-this-phase)

<a id="phase-2-step-1-18-september-2026-the-artifact-link-now-points-custom--core"></a>
[Historical: Phase 2 step 1 (18 September 2026): the artifact link now points custom → core](plug-app-implementation-history.md#phase-2-step-1-18-september-2026-the-artifact-link-now-points-custom--core)

[Historical: Verification](plug-app-implementation-history.md#verification)

[Historical: Rollback](plug-app-implementation-history.md#rollback)

<a id="phase-2-step-2-18-september-2026-the-34-models-move-to-the-plug-state-only"></a>
[Historical: Phase 2 step 2 (18 September 2026): the 34 models move to the plug, state only](plug-app-implementation-history.md#phase-2-step-2-18-september-2026-the-34-models-move-to-the-plug-state-only)

<a id="phase-2-step-3-19-september-2026-the-remaining-custom-code-modules-move-no-schema"></a>
[Historical: Phase 2 step 3 (19 September 2026): the remaining custom code modules move, no schema](plug-app-implementation-history.md#phase-2-step-3-19-september-2026-the-remaining-custom-code-modules-move-no-schema)

<a id="final-note-lab-extraction--19-september-2026"></a>
[Historical: Final note-lab extraction — 19 September 2026](plug-app-implementation-history.md#final-note-lab-extraction--19-september-2026)

<a id="draft-recovery-ownership-decision--19-september-2026"></a>
[Historical: Draft-recovery ownership decision — 19 September 2026](plug-app-implementation-history.md#draft-recovery-ownership-decision--19-september-2026)

<a id="final-closure-audit-correction--19-september-2026"></a>
[Historical: Final closure audit correction — 19 September 2026](plug-app-implementation-history.md#final-closure-audit-correction--19-september-2026)

<a id="first-behavior-separation-batch--19-september-2026"></a>
[Historical: First behavior-separation batch — 19 September 2026](plug-app-implementation-history.md#first-behavior-separation-batch--19-september-2026)

<a id="policy-separation-batch--19-september-2026"></a>
[Historical: Policy separation batch — 19 September 2026](plug-app-implementation-history.md#policy-separation-batch--19-september-2026)

<a id="diagnosis-action-contribution--19-september-2026"></a>
[Historical: Diagnosis action contribution — 19 September 2026](plug-app-implementation-history.md#diagnosis-action-contribution--19-september-2026)

<a id="medication-action-contribution--19-september-2026"></a>
[Historical: Medication action contribution — 19 September 2026](plug-app-implementation-history.md#medication-action-contribution--19-september-2026)

<a id="formartifact-action-contribution--19-september-2026"></a>
[Historical: Form/artifact action contribution — 19 September 2026](plug-app-implementation-history.md#formartifact-action-contribution--19-september-2026)

<a id="fresh-post-group-8-audit-correction--19-september-2026"></a>
[Historical: Fresh post-group-8 audit correction — 19 September 2026](plug-app-implementation-history.md#fresh-post-group-8-audit-correction--19-september-2026)

<a id="generic-patient-organization-scope--19-september-2026"></a>
[Historical: Generic patient organization scope — 19 September 2026](plug-app-implementation-history.md#generic-patient-organization-scope--19-september-2026)
