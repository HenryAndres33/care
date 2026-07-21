# Native correspondence source-correction ledger

Slice 11-A establishes the disease-independent source boundary used by later
correspondence continuity projection. It does not infer clinical differences,
mutate an existing PDF, letter, review, or delivery, or claim that a GP has
been notified. It records only a finalized-form lineage advance that actually
occurred through the idempotent amendment/addendum command.

## Atomic source contract

Every workflow-finalized `FormSubmission` series has one mutable, hashed
`FormSubmissionSeriesHead`. The head names the exact current submission,
version, snapshot hash, finalizer, and finalization time. An amendment locks the
head first and then its current submission. The route target must still be that
current source and every stored hash must recompute exactly.

In the same database transaction, a valid amendment:

1. inserts the new immutable finalized `FormSubmission`;
2. advances and re-hashes the current series head;
3. inserts one immutable `CorrespondenceSourceCorrection` with old/new source,
   old/new version and snapshot hash, amendment type, bounded reason, author,
   time, historical head hash, sequence, and correction hash;
4. inserts exactly one pending `CorrespondenceCorrectionOutbox` row; and
5. inserts the existing idempotent form command.

Failure at any point rolls back all five effects. Exact command replay returns
the original result without another correction or outbox row. Competing command
keys serialize on the head; only one can advance a given current source.

Named database constraints prevent duplicate heads, duplicate current sources,
forked outgoing or incoming correction edges, duplicate correction sequences,
duplicate correction hashes, and duplicate outbox facts. `correction_hash` is
the stable projector idempotency key; unlike database IDs it is reproducible
from the exact source transition. The API maps named uniqueness races to a
non-leaking series-integrity conflict rather than an unhandled server error.

## Canonical lock order

Any operation that must prove a source current before a new external delivery
effect acquires the source-series head and current source before delivery,
attempt, revision, encounter, or other downstream locks. Initial send, retry,
worker claim, and locked provider preflight all follow that rule. Thus an
amendment and dispatch have two safe serial outcomes:

- dispatch proves the old source current first, after which its immutable
  history remains valid and the committed correction is queued; or
- amendment advances the head first, after which dispatch terminates as
  `source_not_current` before provider contact.

Historical delivery reads never acquire this live-source gate and continue to
validate their frozen immutable ledger.

## Migration 0086

`0086_correspondence_source_correction` validates all existing finalized form
series before inserting anything. Each accepted series must be a single linear
chain with one root, contiguous versions, exact `previous_version` links, one
patient/encounter/questionnaire context, exact recomputed snapshot hashes, and
a real finalizer and finalization time. Every non-root source must additionally
have exactly one non-deleted `FormSubmissionCommand` with `command_type=amend`.
Its actor, target/result, patient, encounter, questionnaire, expected version,
UUID-v4 request key, bounded server-ledger timing, and recomputed canonical
payload hash must all match the transition. The migration never fabricates an
actor or trusts the mutable finalizer/time fields alone.

After validation, it creates one head at the terminal source. For every actual
historical amendment/addendum transition it also recreates the historical head
hash, immutable correction fact, and pending outbox row in sequence. If any
series is malformed, the migration raises one PHI-free aggregate error count;
PostgreSQL rolls back the whole migration. The migration also tightens the
finalized-form constraint so future submitted rows require a real finalizer and
an exact lowercase SHA-256 hash. Validation and creation use two streaming
passes with row locks, a 500-row database iterator, and a hard maximum of 1,000
versions per series. Memory is therefore bounded to one accepted series; an
oversized series fails closed as malformed.

Reversing and reapplying 0086 is a local migration-verification procedure only.
It deterministically recreates the same correction hashes but generates new
database/external IDs and fresh pending outbox rows. Production rollback is
prohibited after any continuity projector has consumed an outbox. Production
recovery must roll forward while preserving the original correction/outbox
tables and stable `correction_hash` idempotency keys.

## Durable projector and continuity cases

Slice 11-B consumes the Slice 11-A outbox and projects a generic, disease-
independent continuity view. It does not contain BPH, haematuria, stone, or
other specialty-specific field mappings. It compares the finalized structured
form snapshots by stable JSON-Pointer-style references, escaping `~` and `/`,
and caps the display at 200 changes and each rendered value at 4,000 characters.
The full bounded input still contributes to `change_set_hash`; a synthetic
overflow row cannot collide with a clinical field reference.

The outbox state machine is bounded and lease-fenced:

- `pending`: unclaimed and eligible at `available_at`; `attempt_count` may be
  non-zero after a safe reschedule;
- `processing`: claimed with incremented attempt count, `claimed_at`, a new
  UUID claim token, and a finite lease;
- `completed`: projection committed, with claim and completion timestamps;
- `failed_terminal`: integrity/policy failure requiring operator review, with
  a PHI-free `safe_code`.

Ready and stale-claim indexes cover `(status, available_at, id)` and
`(status, claimed_at, id)`. Workers claim with `select_for_update(skip_locked)`,
verify the entire source/correction chain, and process each series in order.
Every completion, release, and terminal write must match the current claim
token, so a worker whose lease expired cannot overwrite the result of its
replacement. A 60-second scanner requeues ready/stale work and another
60-second scanner repairs correction-case delivery state after a lost broker
enqueue. Neither scanner sends correspondence.

An affected historical delivery gets one hashed `CorrespondenceCorrectionCase`
and an append-only hash-chained event ledger. A definitely-not-delivered
original does not need a case and instead requires regeneration from the live
source. Acknowledged delivery requires a correction; unknown or in-flight
delivery requires outcome resolution. Later delivery acknowledgement or
certainty changes are projected without resending. Replacement drafting,
notification acknowledgement, paper reconciliation, and final case resolution
are implemented by the Slice 11-C replacement command boundary below.

## Replacement correction workflow (Slice 11-C)

Slice 11-C is a generic correspondence capability. It has no BPH, haematuria,
stone, urology, questionnaire-slug, or clinical-field mapping. Specialty
plugins and configurable forms provide the finalized source, template, and
medication links; the core only enforces reusable source, audit, document,
delivery, and correction invariants.

`POST /api/v1/correspondence_correction_cases/{case}/idempotent-command/`
accepts one strict command at a time:

- `start_replacement` compiles the exact current finalized form, artifact,
  medication actions, active template, verified author, and the original
  verified recipient into a new immutable replacement attempt. A second
  same-source attempt is allowed only after the current replacement delivery's
  verified terminal ledger is exactly `failed_terminal`; it becomes attempt
  N+1 and supersedes the prior attempt without resending the original;
- `revise_replacement` creates another immutable draft revision;
- `finalize_replacement` creates a finalized revision and controlled-copy PDF;
- `send_replacement` creates one new delivery with
  `supersedes=original_delivery` and
  `correction_case_reference=case.external_id`;
- `retry_replacement` adds an attempt to that same replacement delivery after
  an exact retryable terminal event;
- `attest_paper` records the explicit human attestation
  `corrected_copy_filed_prior_copy_reconciled`; and
- `resolve` closes the case only with explicit mode
  `replacement_acknowledged` or, where the immutable delivery ledger proves
  it, `original_not_delivered`.

Every request supplies a UUID-v4 `client_request_id` plus the expected case
version and case hash. Exact replay returns the original immutable result
snapshot; key reuse with different content fails closed. Each successful
command advances and re-hashes the case, inserts an immutable command record,
and appends a hash-chained case event in the same transaction. The result
snapshot uses the exact JSON-safe replacement representation used by the API,
so reload and replay cannot reconstruct state from mutable rows.

The lock order is source-series head and current finalized source, then the
correction case, then replacement revision/artifact/delivery resources. A
second source amendment therefore makes the old replacement attempt historical
and requires a new immutable attempt from the new current source; it never
rewrites or reuses the prior attempt. Ordinary letter finalize/revise/send and
ordinary delivery retry routes reject replacement-linked objects. Only the
dedicated correction-case boundary may mutate this lineage, and no correction
command resends the original delivery.

Case integrity validates every historical replacement attempt in contiguous
attempt-number and supersession order, not only the current attempt. Every
command/event attempt reference must belong to that case and its validated
history, so a later attempt cannot hide tampering in an earlier attempt.

The controlled-copy PDF visibly identifies itself as a correction and includes
the case reference, replacement attempt/source version, revision fingerprint,
and superseded delivery reference. PDF creation sets paper reconciliation to
`required`; printing or generating the file never claims that staff filed it.
Resolution by replacement additionally requires both an acknowledged
replacement delivery ledger and the exact human paper attestation.

Replacement delivery events are projected durably into the case by the worker
hook and a 60-second repair scanner. The projector locks the source head first,
records terminal facts for historical attempts, and updates the actionable
case projection only when the delivery belongs to the current attempt. It
never contacts a provider or creates a delivery attempt.

## Continuity read contract

`GET /api/v1/correspondence_continuity/` requires exactly `compilation`,
`patient`, and `encounter` UUID query parameters. It returns the frozen source,
immutable historical chain, exact live authoritative source only when proven,
bounded changes, correction case, required action, and explicit action policy.
The continuity endpoint and every other correspondence, consult-closure,
FormSubmission, MedicationRequest, and ReportUpload response are protected by
the shared clinical-response guard. Successful, replayed, validation-error and
authorization-error responses carry `Cache-Control: no-store`,
`Pragma: no-cache`, and vary by authorization/cookie. Successful continuity
responses have a continuity hash and ETag.

Slice 11-C raises the continuity contract to version 2. The correction-case
payload contains `resolution_mode` and one exact `replacement` snapshot with
the immutable attempt source bindings, current revision/artifact, delivery
state and terminal event hash, retry eligibility, and paper attestation. The
same snapshot shape is returned by correction commands, so the client can
resume safely after reload using only the latest case version/hash and never
guess whether a send, retry, acknowledgement, or paper step completed.

- `400`: malformed, missing, or extra query input;
- `403`: caller cannot read the scoped encounter;
- `404`: compilation is outside the supplied patient/encounter scope;
- `409`: current head or source lineage cannot be proven;
- `503` with `Retry-After`: recoverable projector or delivery-repair lag;
- `200` with `source_state=integrity_failed`: durable terminal projector or
  immutable correction-case integrity failure, with frozen history retained.

The endpoint never substitutes a frozen source as the current source and never
enables send/retry/draft actions when the live permission, current-source,
recipient, review, revision, artifact, or integrity checks fail.

## Migration 0087 and deployment

Drain old correction and delivery workers before applying
`0087_correspondence_continuity` and deploying Slice 11-B workers. The migration
adds the case/event ledger and claim-token leases. For legacy non-pending rows it
creates deterministic UUID-v5 tokens. A legacy `processing` lease is made
immediately expired because the old worker cannot possess that token; the new
scanner safely reclaims it. Legacy terminal failures without a recorded reason
receive the PHI-free code `legacy_failure_reason_unrecorded` and a reversible
metadata marker. Existing user actor IDs are preserved; the migration never
fabricates a user.

Schema reversal is tested for development only. Reversing 0087 deletes the
continuity case/event ledger and removes claim fencing, so production rollback
is prohibited once any Slice 11-B projector has run or any case/event exists.
It is also prohibited after later replacement/acknowledgement work begins.
Production recovery is roll-forward while preserving the original ledgers.

`0088_correspondence_replacement_workflow` adds replacement attempts, the
idempotent command ledger, paper attestations, case replacement projections,
resolution mode, and replacement event links. Deploy it only after Slice 11-B
workers are drained, then deploy the API and replacement projector/scanner as
one release. Reversing 0088 deletes replacement workflow evidence and is
therefore prohibited after the first replacement command, controlled copy,
delivery, or paper attestation. Production recovery is roll-forward.

## Operator runbook

For `failed_terminal` or `source_state=integrity_failed`:

1. Stop automated retries for the affected outbox/series; do not delete or edit
   correction, case, event, delivery, or frozen artifact rows.
2. Record the PHI-free `safe_code`, outbox external ID, correction hash, and
   timestamps in the incident record. Do not copy clinical values into logs.
3. Verify source head, finalized snapshot, correction chain, frozen review,
   revision, artifact, and delivery ledger through controlled diagnostics.
4. Repair the root cause by a reviewed roll-forward change. Only then return a
   recoverable outbox to `pending`; never reuse or invent a claim token.
5. Let the scanners converge projection/read state. Do not manually resend an
   original or correction merely because a continuity read returns `503`.

Hash mismatch, forked lineage, or missing immutable history is not recoverable
by blind retry. Escalate it as an integrity incident and preserve all evidence.
