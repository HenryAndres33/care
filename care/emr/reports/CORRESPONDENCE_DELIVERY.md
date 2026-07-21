# Native correspondence delivery ledger

Slice 10 adds a disease-independent delivery boundary for the immutable final
letter/PDF produced by Slice 09. It does not contain BPH, urology, medication,
or custom-form field rules. The delivery source is always one exact finalized
revision, artifact, review binding, recipient version, patient, encounter,
facility, department, and verified CARE author.

## Production gate

There is no production transport in this slice. Base settings keep
`CORRESPONDENCE_SYNTHETIC_DELIVERY_ENABLED = False`; unsupported recipients and
all deployments without an explicitly approved adapter fail closed. No SMTP,
`mailto:`, recipient-controlled URL, HTTP endpoint, or dynamic import is used.

Local and test settings may enable `synthetic_no_network`. That adapter accepts
only a server-created recipient with `source_type=synthetic_test_fixture` and
`channel_type=secure_endpoint`. It never dereferences `channel_identifier` and
uses no network. Its durable, no-PHI invocation marker and provider receipt
exist solely to test worker crash and idempotency behavior.

## API contract

Explicitly confirm an initial delivery:

`POST /api/v1/correspondence_delivery/idempotent-send/`

```json
{
  "client_request_id": "uuid-v4",
  "correspondence_revision": "uuid-v4",
  "resource_version": 2,
  "revision_hash": "sha256",
  "artifact": "uuid-v4",
  "artifact_sha256": "sha256",
  "review_binding": "uuid-v4",
  "review_hash": "sha256",
  "patient": "uuid-v4",
  "encounter": "uuid-v4",
  "facility": "uuid-v4",
  "department": "uuid-v4",
  "author": "uuid-v4",
  "recipient": "uuid-v4",
  "recipient_version": 1,
  "recipient_hash": "sha256",
  "confirmed": true
}
```

`confirmed` accepts only literal `true`. Unknown or missing fields return `400`
before a ledger row exists. The first commit returns `201`; an exact replay of
the same command key and canonical payload returns `200`. Conflicting key reuse
or another initial delivery for the same revision returns `409` without a
duplicate effect.

Retry a definitely not-delivered attempt:

`POST /api/v1/correspondence_delivery/{delivery_id}/idempotent-retry/`

```json
{
  "client_request_id": "uuid-v4",
  "expected_event_sequence": 3,
  "expected_event_hash": "sha256",
  "confirmed": true
}
```

Retry is allowed only when the exact current event is `failed_retryable`. An
`outcome_unknown`, acknowledged, stale, terminal, or mismatched event cannot be
resent. Every retry reruns source currentness, worker authorization, artifact,
adapter, and full-ledger checks and uses the original stable provider
idempotency key.

Authorized historical reads are:

- `GET /api/v1/correspondence_delivery/{delivery_id}/`
- `GET /api/v1/correspondence_delivery/?patient={uuid}&encounter={uuid}`
- the list optionally accepts `correspondence_revision={uuid}` plus bounded
  CARE `limit`/`offset` pagination.

Historical retrieval validates the frozen revision, artifact, review,
delivery, attempt hashes, event chain, and current read authorization. It does
not make a previously delivered record disappear because a live source or
recipient later changed; such drift still blocks every new send/retry.

## Durable states and certainty

The append-only event ledger exposes these exact state/certainty pairs:

- `dispatch_pending` / `not_attempted`
- `dispatching` / `attempting`
- `acknowledged` / `acknowledged`
- `failed_retryable` / `not_delivered`
- `failed_terminal` / `not_delivered`
- `outcome_unknown` / `unknown`

Only `acknowledged` means sent. A provider acknowledgement reference is bounded,
server-generated, hashed, and mandatory for that state. `outcome_unknown` is
never displayed or treated as sent and never triggers a blind resend.

If dispatch is rejected before provider contact, the terminal event remains
`failed_terminal` / `not_delivered` and carries one exact PHI-free diagnostic
code: `adapter_unavailable`, `source_not_current`, or `authorization_revoked`.
The claim phase first appends `dispatching` with `claim_rejected` so the event
chain remains explicit; the locked preflight uses the same three terminal
codes. These codes are operational reasons only and never contain patient,
recipient, form, or letter text.

## Worker and crash recovery

The database row and first `dispatch_pending` event are the durable outbox.
Celery is only a wake-up mechanism; the periodic scanner recovers missed queue
notifications. Processing has three phases:

1. lock the delivery, complete attempt/event ledger, revision, source, current
   author membership/role, and artifact; verify all canonical hashes; rerun the
   exact report-write authorization; append `dispatching` and commit;
2. immediately repeat the locked ledger/currentness/authorization/artifact
   preflight and, while that transaction still owns the delivery lock, record a
   durable provider-start fence keyed by stable provider key plus attempt
   number; only after that transaction commits may the allowlisted adapter run
   outside the ledger transaction;
3. lock and verify the complete ledger again and append the provider outcome.

The no-network adapter has one global outcome fence: a receipt is unique by the
stable provider idempotency key, while its attempt number remains immutable
audit data. If no provider-start marker exists, lookup can prove
`failed_retryable`; a scanner that records that result prevents a later worker
from passing its locked preflight. Once the start fence exists, a crash or
missing receipt is `outcome_unknown`, never definitely not delivered. A durable
receipt recovers the stored acknowledgement without invoking delivery again,
including when lookup markers were appended while the provider was still
running. A corrupt/mismatched marker or receipt yields `outcome_unknown`. The
scanner performs at most five automatic lookup-only reconciliation events per
unknown attempt; later reconciliation remains lookup-only and never resends.

`attempt_hash` binds the request key, canonical payload hash, command, actor and
audit actors, server timestamp, adapter identity, stable provider key, attempt
number, delivery hash, and previous terminal event. Every event hash binds that
attempt hash and the prior event hash. Tampering before claim, between claim and
provider, or before outcome append blocks the provider/append and writes only a
PHI-free safe log containing resource UUIDs and exception class.

## Slice 11 correction delivery boundary

`supersedes` and `correction_case_reference` are immutable delivery-lineage
fields. Ordinary send and retry routes require both to remain null. Slice 11-C
may populate them only through the dedicated correction-case command boundary:
the replacement delivery must supersede the case's original delivery and carry
that case's external ID. A retry appends an attempt to the same replacement
delivery; it never creates or resends the original delivery.

Terminal replacement events use the same immutable delivery ledger and
synthetic transport contract as ordinary correspondence. Their case projection
is a separate durable, non-sending step with a periodic repair scanner. This
keeps provider contact inside the delivery worker while preserving correction,
paper-reconciliation, and resolution semantics in the correction ledger.

Migration `0085_correspondence_delivery_ledger` creates the immutable delivery,
attempt, event, synthetic provider-invocation, and synthetic provider-receipt
tables and their named database constraints. It does not migrate or infer sent
state from browser storage or legacy correspondence.
