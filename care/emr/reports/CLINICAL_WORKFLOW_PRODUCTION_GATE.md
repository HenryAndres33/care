# Clinical Workflow Production Gate

Date: 2026-07-20
Scope: CARE-native form, medication, document, correspondence and consult-close
workflow delivered by migrations `0078` through `0090`.

## Release decision and non-goals

The backend is disease-independent. BPH, hematuria, stones and other clinical
content remain questionnaire/template configuration; the backend owns generic
identity, authorization, versioning, evidence, delivery and closure rules.

Production starts fail-closed:

- `CLINICAL_WORKFLOW_MUTATIONS_ENABLED_FACILITIES=[]` enables no irreversible
  workflow mutation. Production may list explicit facility UUIDs only.
- `CORRESPONDENCE_DELIVERY_ENABLED_FACILITIES=[]` independently disables
  delivery creation, retry, worker claim, reconciliation and provider start.
- `CORRESPONDENCE_SYNTHETIC_DELIVERY_ENABLED=False`; the synthetic adapter is
  local/test-only and performs no network I/O. No production delivery adapter
  exists in this release.
- Post-close form addendum/reconciliation remains blocked. Encounter restart is
  not a substitute. A future audited command requires a separate clinical,
  privacy, migration and integrity review.

Read-only recovery remains available while mutations are disabled. Exact
idempotent replays return already committed results before a capability guard;
they never create another clinical side effect.

## Additive production module inventory

| Area | Additive modules | Risk / upstream touchpoint | Verification |
| --- | --- | --- | --- |
| Shared security and rollout | `care/emr/api/viewsets/clinical_no_store.py`, `care_suriname/api/viewsets/workflow_capability.py`, `care/emr/workflow_capabilities.py`, `care_suriname/checks.py` | Medium. DRF finalization, facility membership and Django deployment checks. | no-store success/error contract; off/on/wrong-facility and kill-switch tests; deploy checks. |
| Medication idempotency | `care/emr/resources/medication/request/idempotency.py` | Medium. Wraps native MedicationRequest and prescription creation. | exact replay/conflict/rollback/concurrency suite. |
| Form workflow | `care/emr/resources/form_submission/artifact.py`, `commands.py`, `structured_actions.py` | High. Native FormSubmission state and structured clinical links. | version/finalize/amend/artifact, authorization, wrong-patient and concurrency suites. |
| Form artifact rendering | `care/emr/reports/form_submission_artifact.py` | High. Immutable clinical PDF and object storage. | deterministic render/hash, compensation, authenticated retrieval tests. |
| Correspondence domain services | `care/emr/correspondence/{author,correction,delivery,delivery_adapters,letter,recipient,replacement,review,source}.py` | High. Frozen identity/content, delivery and correction integrity. | compilation through correction/recovery regression suites and tamper tests. |
| Correspondence API | `care_suriname/api/viewsets/correspondence.py`, `correspondence_review.py`, `correspondence_letter.py`, `correspondence_delivery.py`, `correspondence_continuity.py`, `correspondence_correction_case.py` | High. Authenticated clinical command boundary. | strict source/context, authorization, replay, race, integrity and no-store tests. |
| Correspondence models | `care/emr/models/correspondence.py`, `correspondence_review.py`, `correspondence_letter.py`, `correspondence_delivery.py`, `correspondence_correction.py` | High. Immutable evidence and state machines. | named uniqueness/check constraints, migration and model immutability tests. |
| Correspondence specs | `care/emr/resources/correspondence.py`, `correspondence_review.py`, `correspondence_letter.py`, `correspondence_delivery.py`, `correspondence_continuity.py`, `correspondence_correction.py`, `correspondence_replacement.py` | Medium. Public API schema and canonical hashes. | strict/extra-field rejection and canonical hash tests. |
| Correspondence rendering | `care/emr/reports/correspondence_compiler.py`, `correspondence_letter.py`, `template_versioning.py` | High. Clinical token resolution, HTML and PDF. | sandbox, size bounds, escaping, source hash and PDF tests. |
| Durable workers | `care/emr/tasks/correspondence_delivery.py`, `correspondence_correction.py` | High. Outbox, provider and repair side effects. | fencing, stale reclaim, no-resend and kill-switch tests. |
| Consult closure | `care/emr/models/consult_closure.py`, `care/emr/resources/consult_closure.py`, `care_suriname/api/viewsets/consult_closure.py` | High. Atomic terminal encounter/queue/resource transition. | preflight, atomic rollback, terminal guards, replay, corruption and recovery tests. |
| Operations | `care/emr/management/commands/clinical_workflow_readiness.py` | Low. Read-only aggregate inspection. | PHI-minimal output, absent-table and `--fail-on-risk` tests plus live ORM execution. |

`__init__.py` package markers are additive but contain no clinical behavior.
Tests and the report documents in `care/emr/reports/` are release evidence, not
runtime extension points.

## Unavoidable CARE core glue inventory

These are generic platform changes, not BPH-specific forks.

| Core file | Why it is touched | Risk | Update verification |
| --- | --- | --- | --- |
| `care/audit_log/helpers.py` | Routes workflow models away from generic full-value log output to their immutable domain ledgers. | High: global audit selection. | domain-ledger exclusion test; verify upstream audit matcher semantics. |
| `care/emr/apps.py` | Registers deployment security checks. | Low. | `manage.py check --deploy`; preserve all upstream `ready()` imports. |
| `care/emr/api/viewsets/form_submission.py` | Native versioned draft/finalize/amend/artifact commands, locking, auth, rollout and no-store. | High. | legacy FormSubmission plus workflow/artifact suites. |
| `care/emr/api/viewsets/medication_request.py` | Atomic idempotent create/reconcile, terminal guard, rollout and no-store. | High. | native MedicationRequest/prescription plus idempotency suites. |
| `care/emr/api/viewsets/report/report_upload.py` | Generated-artifact query efficiency, immutability and no-store. | Medium. | native report tests plus form/letter artifact suites. |
| `care/emr/api/viewsets/encounter.py` | Serializes status mutation and reserves terminal close for the closure command. | High. | encounter, closure and terminal-race tests. |
| `care/emr/api/viewsets/{device,location}.py` | Prevents terminal encounter association changes and aligns lock order. | High. | device/location legacy tests and closure rollback/race tests. |
| `care/emr/api/viewsets/scheduling/{booking,token}.py` | Locks and finalizes queue/booking state with consult closure. | High. | booking/token legacy tests and closure suite. |
| `care/emr/models/__init__.py` | Exposes additive models to Django. | Low. | Django app load and migration drift. |
| `care/emr/models/medication_request.py` | Stores idempotency key and canonical request hash. | Medium. | constraints and replay tests. |
| `care/emr/models/questionnaire.py` | Adds form versions, immutable final snapshot and command ledger. | High. | migration, version, correction and immutability tests. |
| `care/emr/models/report/{report_upload,template}.py` | Adds provenance/hash/version fields and immutable generated artifacts. | High. | artifact, compilation and template version tests. |
| `care/emr/registries/system_questionnaire/system_questionnaire.py` | Maps built-in structured actions to native CARE resource models. | Medium. | structured-action ownership/rollback tests. |
| `care/emr/resources/form_submission/spec.py` | Adds optimistic version and lineage to the public form contract. | Medium. | strict schema and legacy API tests. |
| `care/emr/resources/medication/request/spec.py` | Extracts reusable native prescription resolution. | Medium. | prescription and idempotent rollback tests. |
| `care/emr/resources/report/{report_upload,template}/spec.py` | Exposes generated provenance and template hashes. | Medium. | serialization/retrieve tests. |
| `care/emr/tasks/__init__.py` | Registers four durable delivery/correction scans. | Medium. | Celery registration/import and worker suites. |
| `config/api_router.py` | Registers additive APIs only. | Low. | URL reverse and API suites. |
| `config/settings/{base,local,test}.py` | Fail-closed rollout, no-network delivery, timezone, audit and test isolation. | High. | production checks, capability, timezone and settings tests. |
| `scripts/celery-dev.sh` | Makes local worker settings explicit. | Low. | shell/settings test. |

Future extraction candidates are router auto-registration, a supported clinical
command hook around Encounter terminal transitions, FormSubmission lifecycle
hooks, and a standalone Django app for the correspondence ledgers. Until CARE
offers those boundaries, keeping these patches small, generic and separately
tested is safer than copying core models into a clinical plugin.

## Migration inventory and rollback class

The chain is linear: `0077 -> 0078 -> ... -> 0090`. Never renumber an applied
migration to accommodate a future upstream migration; resolve the graph with a
new merge/compatibility migration after analysis.

| Migration | Forward effect | Data operation / rollback class |
| --- | --- | --- |
| `0078_medicationrequest_idempotency` | Medication request key/hash and constraints. | Schema-reversible before use; evidence-destructive after activation. |
| `0079_formsubmission_versioned_workflow` | Form version/finalization/lineage fields and command ledger. | Backfills legacy versions; reverse data step is intentionally noop. Forward-only after use. |
| `0080_form_submission_artifact` | Artifact command plus ReportUpload provenance/constraints. | Schema reverse deletes artifact evidence; forward-only after use. |
| `0081_correspondence_compilation` | Template version/hash backfill and compilation/command ledgers. | Reverse backfill is noop; forward-only after use. |
| `0082_correspondencerecipient_correspondencereview_and_more` | Verified recipient, frozen review and review command. | Schema reverse destroys review evidence. |
| `0083_correspondencerecipient_kind_constraint` | Restricts current recipient workflow to healthcare professional. | Constraint-reversible only before activation. |
| `0084_correspondence_letter_workflow` | Letter/revision/command and correspondence artifact provenance. | Schema reverse destroys revisions/artifacts. |
| `0085_correspondence_delivery_ledger` | Delivery, attempt, event and synthetic test receipt/invocation ledgers. | Schema reverse destroys dispatch evidence; prohibited after any attempt. |
| `0086_correspondence_source_correction` | Authoritative form-series head, source correction and outbox. | Forward backfill, reverse noop. Actorless finalized legacy rows are a preflight blocker; never fabricate an actor. |
| `0087_correspondence_continuity` | Correction case/event and leased/fenced outbox state. | Lease backfill has a reverse function; table/constraint reverse still destroys clinical continuity evidence. |
| `0088_correspondence_replacement_workflow` | Correction command, replacement attempt and paper attestation. | Pre-state validation, reverse noop; forward-only after use. |
| `0089_consult_closure_workflow` | Closure, command and recovery task. | Schema reverse destroys terminal transition evidence. |
| `0090_consult_closure_recovery_resolution` | Audited recovery-resolution actor/request/payload/hash. | Schema reverse destroys recovery-resolution evidence. |

After the first clinical mutation, `0078` through `0090` are operationally
forward-only. A rollback means flags off, code compatibility/roll-forward fix,
or full database plus object-store restore to the same pre-activation point. It
never means migrating this chain backward on a live clinical database.

## Queryable audit evidence

Generic value-diff audit excludes the workflow models, preventing clinical
payload copies in console/Sentry. Operational logs contain only external
resource IDs, safe codes and exception classes. Clinical evidence remains
queryable in protected CARE tables:

| Stage | Evidence |
| --- | --- |
| Form | `FormSubmission`, `FormSubmissionCommand`, `FormSubmissionSeriesHead`, `CorrespondenceSourceCorrection` |
| Medication | `MedicationRequest.client_request_id/client_request_payload_hash` and linked completed `QuestionnaireResponse` |
| Artifact | `FormSubmissionArtifactCommand` and `ReportUpload` source version/hash/artifact SHA-256/generator/time |
| Compilation/review | `CorrespondenceCompileCommand`, frozen `CorrespondenceCompilation`, `CorrespondenceReviewCommand`, frozen `CorrespondenceReview` |
| Letter | `CorrespondenceLetterCommand`, immutable `CorrespondenceLetterRevision`, exact `ReportUpload` artifact |
| Delivery | `CorrespondenceDeliveryAttempt`, append-only `CorrespondenceDeliveryEvent`, provider invocation/receipt test ledgers |
| Correction | source correction/outbox/case/event/command, replacement attempt and paper attestation |
| Closure | `ConsultClosure`, `ConsultClosureCommand`, `ConsultClosureRecoveryTask` including resolution request/payload/audit hashes |

Clinical APIs require the normal patient/encounter/report authorization.
Operators use aggregate tooling below; they must not export table contents into
incident chat, tickets, console logs or monitoring labels.

## Deployment runbook

### 1. Change control and backups

1. Record application commit/image digest, migration leafs, enabled facilities,
   provider state and worker version in the change ticket.
2. Put irreversible workflow mutations and delivery flags at `[]`.
3. Take a transactionally consistent PostgreSQL backup and verify restore to an
   isolated database. Snapshot the clinical object-storage bucket at the same
   cutover point and retain encryption/key-version metadata separately.
4. Confirm database and object-store backup timestamps describe one recoverable
   point. Do not proceed on an untested or partial backup.
5. Run `python manage.py clinical_workflow_readiness --fail-on-risk`. Resolve
   actorless finalized forms through governed source-data remediation before
   `0086`; never invent an actor. Resolve/triage pending recovery, failed
   outbox and unknown/terminal delivery evidence before changing versions.

### 2. Configuration preflight

1. Provide a non-default `DJANGO_SECRET_KEY` from the secret manager.
2. Configure explicit `DJANGO_ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` and
   `CORS_ALLOWED_ORIGINS`; wildcards are prohibited.
3. Keep `DJANGO_TIME_ZONE=America/Paramaribo` for Suriname presentation unless
   the deployment has an approved local requirement. Database timestamps and
   Celery transport/scheduling remain UTC. Clients present Dutch or English
   locale explicitly; stored clinical dates remain ISO/UTC with provenance.
4. Keep synthetic delivery false. Keep both facility flags empty.
5. Run `python manage.py check --deploy --settings=config.settings.production`.
   `care.E001` through `care.E007` are hard deployment failures.

### 3. Schema and application rollout

1. Inspect `showmigrations emr` and `migrate --plan`; the intended suffix must
   be exactly `0078` through `0090` in order.
2. Run `makemigrations --check --dry-run` against the release image.
3. Apply migrations once, then deploy web and Celery worker/beat from the same
   image. Do not run a new worker against an old schema.
4. Run the bounded backend regression, health checks, readiness report and a
   read-only capability request for an authorized test facility.
5. Enable `CLINICAL_WORKFLOW_MUTATIONS_ENABLED_FACILITIES` for one named pilot
   facility, observe, then expand explicitly. Keep delivery disabled because
   this release has no production provider adapter.

## Monitoring and alert thresholds

Run `clinical_workflow_readiness` at least every five minutes and retain only
its aggregate JSON. Suggested initial alerts:

- any actorless finalized form: deployment blocker;
- any pending consult-closure recovery: page the clinical application operator;
- correction outbox pending/processing older than 300 seconds: warning; older
  than 900 seconds or any terminal failure: page;
- delivery `dispatch_pending`/`dispatching` older than 300 seconds: warning;
  any `outcome_unknown` or integrity safe code: page and prohibit blind resend;
- repeated `workflow_mutations_disabled` outside a planned disabled window:
  configuration/client warning, not clinical failure;
- any `care.E001`-`care.E007`, migration drift, hash-integrity failure or worker
  version mismatch: stop rollout.

Metrics/log labels may contain stage, facility UUID, count, age bucket and
bounded safe code only. Never patient, encounter, recipient, document body,
form value, medication text, filename or signed URL.

## Incident and recovery runbook

1. Disable delivery first, then workflow mutations for affected facility UUIDs.
   Read-only clinical evidence remains available.
2. Do not delete, edit, soft-delete or manually `UPDATE` a command/event row.
   Preserve logs, image digest, DB snapshot and object-store snapshot.
3. Run the readiness command and application health checks. Record aggregate
   counts/ages/safe codes only.
4. For unknown delivery outcome, reconcile through the existing provider lookup
   contract; never resend blindly. With no production provider adapter, keep the
   case blocked for operator review.
5. For a closure integrity failure, use only the authorized idempotent recovery
   resolution after restoring integrity. Do not restart the encounter.
6. For post-close clinical correction, stop: addendum/reconciliation is not
   implemented. Escalate to clinical governance and use the approved external
   downtime/correction procedure without changing CARE evidence.
7. Deploy a forward-compatible fix and re-run focused integrity/replay tests.
   Re-enable one facility only after clinical owner, privacy/security and
   backend approvals are documented.

## CARE upstream update / rebase procedure

1. Freeze feature flags and take verified backups. Fetch the intended upstream
   CARE tag/commit into a dedicated compatibility branch.
2. Rebuild this inventory from `git diff --name-status <old-care>..<workflow>`.
   Compare every core glue file above with upstream using semantic review; do
   not accept a textual conflict resolution without understanding changed lock,
   authorization, serializer and lifecycle semantics.
3. Reapply additive modules first. Reconcile upstream model/app labels and API
   conventions without renaming applied migrations.
4. Reapply one core patch group at a time: registration/settings, model/schema,
   form/medication/report, terminal Encounter/resources, workers/audit.
5. If upstream introduces a conflicting migration number, keep deployed
   `0078`-`0090` immutable and add a merge/compatibility migration. Validate the
   graph on a restored production-size copy.
6. Re-run migration drift/plan, Django security checks, Ruff, the combined
   relevant backend regression and the full port-4000 browser workflow. Verify
   fail-closed flags before enabling any facility.
7. Update this inventory with removed/new touchpoints. Prefer upstream hooks
   when they preserve the same locks, authorization and immutable evidence;
   delete an old core patch only after equivalent tests pass.

## Acceptance evidence

Final Slice-13 commands and exact results are recorded in
`Real Life Workflow plan.md`. The shared local test database observed during
development was stale and is not migration evidence; release tests use unique,
disposable PostgreSQL test database names and build the migration graph from a
clean state.
