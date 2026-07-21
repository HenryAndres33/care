# Verified Correspondence Recipient and Review Binding

Slice 08 adds an explicit review gate between deterministic correspondence
compilation and any future final-letter, PDF, attachment, or delivery workflow.
It is generic and contains no disease, specialty, facility, clinician, or
recipient defaults.

## Verified recipient directory

CARE's existing Organization, HealthcareService, encounter care-team, and
Patient relationships do not provide a patient-scoped verified external
recipient plus delivery-channel identity. `CorrespondenceRecipient` is the
narrow facility-governed extension for that gap. Each entry is scoped to one
patient and facility and may reference an existing Organization or
HealthcareService. It stores:

- recipient name, professional role, optional qualification/registration, and
  organization name;
- server-owned postal address and one explicit supported channel identifier
  (`postal`, `secure_email`, or `secure_endpoint`);
- source type/reference/provenance, verifier, and verification timestamp;
- active/verified flags plus monotonic `resource_version` and canonical
`content_hash`.

The current correspondence contract allowlists only
`recipient_kind=healthcare_professional`; the named `corrrecipient_kind_ck`
constraint and all discovery/bind validation fail closed for other kinds.
Postal-address and source-provenance JSON are canonicalized and bounded by
type, encoded size, depth, node/item count, key length, and string length.

The named `corrrecipient_source_scope_uniq` database constraint reserves each
patient/facility/source identity across soft deletion. There is deliberately no
browser-facing create/update endpoint: directory governance must populate or
change these records through an approved server/admin workflow.

Authorized discovery is:

`GET /api/v1/correspondence_recipient/verified/?patient={uuid}&facility={uuid}`

Both query parameters are required UUIDv4 values and unknown parameters are
rejected. The caller needs current patient clinical-read authorization and an
active CARE role membership in the facility. Only internally consistent,
active, verified entries are returned as `{ "results": [...] }`. Multiple
entries are returned for explicit user selection; CARE never guesses or
auto-selects a recipient. Discovery returns at most 50 valid candidates; 51 or
more returns a visible non-leaking `409 recipient_discovery_overflow` instead
of silently truncating or selecting.

## Immutable review binding

Create or recover the review gate with:

`POST /api/v1/correspondence_review/idempotent-bind/`

```json
{
  "client_request_id": "UUIDv4",
  "compilation": "Slice 07 compilation UUID",
  "compilation_hash": "64-character lowercase SHA-256",
  "patient": "patient UUID",
  "encounter": "encounter UUID",
  "facility": "facility UUID",
  "department": "facility-organization UUID",
  "author": "authenticated CARE user UUID",
  "recipient": "verified recipient UUID",
  "recipient_version": 1,
  "recipient_hash": "64-character lowercase SHA-256"
}
```

The strict command accepts no author signature text, recipient body, address,
channel, title, or clinical content from the browser. It locks and revalidates
the exact compilation, patient, encounter, facility, department, current user,
department membership/role, and recipient inside one transaction. New review
requires current clinical/report read and report-write authorization.

The authenticated user must be the immutable Slice 07 author, active,
non-service, CARE-verified, and have a non-empty first/last name plus exactly one
active role membership in the named department. The frozen author snapshot
contains the verified name, immutable user/membership IDs, role, facility,
department, and qualification/registration when recorded in CARE. No username,
session, facility, department, or clinician-name fallback is used.

The selected recipient must match the exact patient/facility and requested
version/hash. Inactive, unverified, deleted, malformed, cross-context, or stale
entries fail closed. The frozen recipient snapshot contains only server-owned
directory facts and verification provenance.

Verification is durable but not independent of its facility governor: the
record remains usable only while `verified_by` is still an active, verified,
non-service CARE user with an active same-facility role membership. Deactivation
or loss of that membership revokes discovery, replay, retrieve, and downstream
use until a governed record is reverified/versioned.

The response is:

```json
{
  "client_request_id": "UUIDv4",
  "replayed": false,
  "review_binding": {
    "id": "review UUID",
    "status": "reviewed",
    "compilation": "compilation UUID",
    "compilation_hash": "SHA-256",
    "patient": "patient UUID",
    "encounter": "encounter UUID",
    "facility": "facility UUID",
    "department": "department UUID",
    "author": "user UUID",
    "reviewer": "user UUID",
    "recipient": "recipient UUID",
    "recipient_version": 1,
    "recipient_hash": "SHA-256",
    "author_snapshot": {},
    "recipient_snapshot": {},
    "reviewed_at": "server timestamp",
    "review_hash": "SHA-256"
  }
}
```

First commit returns `201`; exact outcome-unknown retry and a different key for
the identical source command return the original binding with `200` and
`replayed: true`. Responses include an ETag. Retrieve a binding at
`GET /api/v1/correspondence_review/{review_id}/`.

Every retrieve, replay, and downstream availability check recomputes the review
hash and verifies the frozen author/recipient snapshots against all stored
review fields. Snapshot or hash corruption fails closed with `409`; GET never
returns an internally inconsistent binding.

`corrreview_cmd_request_id_uniq` reserves client keys and
`corrreview_compilation_uniq` allows only one immutable reviewed binding per
Slice 07 compilation, including soft-deleted rows. Same-key conflicts or a
second recipient for an already reviewed compilation return a non-leaking
`409`. Stale compilation/recipient versions or unavailable committed sources
also return `409`; invalid verified facts return `422`; unauthorized/session
failures use CARE `401`/`403`; hidden cross-context resources return `404`; and
an unexpected transaction/ledger failure returns retryable `503` with no
binding committed.

Exact replay requires current read authorization but returns the frozen
committed snapshots without current-session substitution. A genuinely new key
rechecks write authorization and all mutable sources under lock.

Future Slice 09/10 finalize, PDF, attach, and send commands must require this
binding's immutable ID/hash, verify that its compilation and recipient sources
remain available, and independently revalidate the current authenticated
session and action-specific permission. This boundary does not persist an
editable/final letter, render its PDF, select a recipient automatically, attach
an artifact, or send/deliver correspondence.
