# Deterministic Correspondence Compilation

CARE compiles correspondence from explicit immutable clinical sources. The
contract is generic: templates and finalized questionnaire responses define the
clinical content, so no disease, medication, or specialty is hard-coded.

## API

Compile or recover an outcome-unknown command:

`POST /api/v1/correspondence_compilation/idempotent-compile/`

The strict request body is:

```json
{
  "client_request_id": "UUIDv4",
  "patient": "patient UUID",
  "encounter": "encounter UUID",
  "facility": "facility UUID",
  "department": "facility-organization UUID",
  "encounter_reason": "active encounter TagConfig UUID",
  "form_submission": "finalized FormSubmission UUID",
  "form_source_version": 2,
  "form_source_hash": "64-character lowercase SHA-256",
  "form_artifact": "finalized-form ReportUpload UUID",
  "form_artifact_hash": "64-character lowercase SHA-256",
  "medication_actions": [
    {
      "id": "confirmed MedicationRequest UUID",
      "client_request_id": "original Slice 04 UUIDv4"
    }
  ],
  "template": "Template UUID",
  "template_version": 1,
  "template_hash": "64-character lowercase SHA-256",
  "author": "authenticated user UUID"
}
```

Unknown fields, non-v4 UUIDs, duplicate medication IDs, malformed hashes, and
more than 50 medication actions are rejected. The server derives encounter
date, readable names, identifiers, author credentials, clinical response
content, and medication content from the named server resources. It never
accepts browser-provided letter text, PHI, titles, timestamps, or fallback
values.

The response is:

```json
{
  "client_request_id": "UUIDv4",
  "replayed": false,
  "compilation": {
    "id": "compilation UUID",
    "status": "compiled",
    "patient": "patient UUID",
    "encounter": "encounter UUID",
    "facility": "facility UUID",
    "department": "facility-organization UUID",
    "encounter_reason": "TagConfig UUID",
    "form_submission": "FormSubmission UUID",
    "form_artifact": "ReportUpload UUID",
    "form_source_version": 2,
    "form_source_hash": "SHA-256",
    "form_artifact_hash": "SHA-256",
    "template": "Template UUID",
    "template_version": 1,
    "template_hash": "SHA-256",
    "author": "user UUID",
    "medication_sources": [],
    "source_provenance": {},
    "compiled_text": "readable immutable text",
    "compiled_html": "sanitized immutable HTML",
    "compiled_hash": "SHA-256",
    "compiled_at": "server timestamp"
  }
}
```

The first committed compilation returns `201` and `replayed: false`. An exact
retry with the same `client_request_id` returns the original snapshot with
`200` and `replayed: true`. A different authorized key starts an independent
letter attempt, even when it names the same clinical source set. The response includes an ETag of
`"{compilation_id}:{compiled_hash}"`. Retrieve the stored snapshot at
`GET /api/v1/correspondence_compilation/{compilation_id}/`.

## Integrity and authorization

`client_request_id` is reserved by `corrcompile_cmd_request_id_uniq`, including
soft-deleted rows. It identifies one letter attempt as well as its idempotent
retry boundary. `corrcompile_source_fingerprint_uniq` permits one compilation
for the canonical validated command. The v2 command hash includes the
authenticated actor, the request key, and every effective request field. Exact
replay of a previously committed v1 command remains supported; that legacy hash
is never used to create a new compilation.

New compilation locks and revalidates the FormSubmission, Encounter, artifact,
template, department, reason, and medication rows in one database transaction.
It rechecks current clinical read access, encounter-report write access, report
read access, template access, route context, exact versions, and recomputed
hashes before rendering or writing either the immutable compilation or command
ledger. Exact replay needs current read access and returns only the committed
snapshot; it does not substitute current-session or later template values.

Compilation accepts only a workflow-finalized, internally consistent
FormSubmission and its exact available Slice 06 artifact. Each named medication
must be an active/completed performed order with the exact original Slice 04
client key and a valid server-owned canonical payload hash, matching patient and
encounter, and exactly one link from the finalized FormSubmission. The payload
hash is resolved internally and stored in provenance; clients neither receive
nor recompute it. The linkage QuestionnaireResponse itself must be non-deleted
and `completed`; `entered_in_error`, deleted, malformed, missing, omitted, or
duplicate linkage rows fail closed. The facility, directly linked encounter
department, active encounter-reason tag, template, author, patient, and
encounter must match. For rendering, `content.values.reasonForVisit` from the
exact finalized form is the preferred presentation reason when it is present
and non-empty. The broader encounter tag remains frozen in provenance and is
used as the fallback.

The patient must have a server-owned name, DOB/year and configured identifier.
The author must be the current active, non-service, CARE-verified user with a
verified first/last name and exactly one active role membership in the named
department. The mandatory appendix always includes patient name, CARE reference,
DOB, identifiers and author name, immutable reference, role, facility,
department and qualification/registration state, even when a minimal template
does not reference them.

Templates run in a strict sandbox with undefined variables rejected and the
`safe` filter disabled. Output then passes a fail-closed HTML allowlist. The
server appends complete readable form, medication, and provenance blocks so a
template cannot omit the immutable source evidence. Draft, stale, unavailable,
deleted, archived, cross-context, ambiguous, malformed, placeholder-bearing,
unauthorized, syntactically invalid, or unsanitizable sources do not compile.

Rendering limits are 100,000 UTF-8 bytes of template source, 1,000 parsed AST
nodes, 500,000 UTF-8 bytes before/after template sanitization, and 2,000,000
UTF-8 bytes for final compiled HTML. Template loops, calls, macros, includes,
inheritance, block assignments, multiplication and exponentiation are rejected
as unbounded constructs. Globals and filters are unavailable. These checks occur
before expensive rendering while the transaction still revalidates every
locked source before commit.

Conflicting key reuse and stale/unavailable sources return a non-leaking `409`.
Invalid source content or template output returns `422`. Unexpected rendering
or transaction failure returns retryable `503` with no committed compilation.
Authentication/authorization use normal CARE `401`/`403` behavior and hidden
cross-context resources return `404`.

This boundary compiles and stores a source snapshot only. It does not create an
editable correspondence draft, finalize a legal letter/report, select or verify
a recipient, or send/deliver anything. Those remain later workflow contracts.
