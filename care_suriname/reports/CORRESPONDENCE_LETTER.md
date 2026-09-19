# Native correspondence letter workflow

Slice 09 adds a generic CARE-native editable correspondence aggregate. It is
independent of disease, specialty, medication, and presentation-layer fields.
The immutable Slice 08 review binding is the only accepted source boundary.

## API contract

Create the initial server-backed draft:

`POST /api/v1/correspondence_letter/idempotent-create/`

```json
{
  "client_request_id": "uuid-v4",
  "review_binding": "uuid-v4",
  "review_hash": "sha256",
  "patient": "uuid-v4",
  "encounter": "uuid-v4",
  "facility": "uuid-v4",
  "department": "uuid-v4",
  "author": "uuid-v4",
  "body": "Plain-text clinical correspondence"
}
```

Create a new immutable draft revision:

`POST /api/v1/correspondence_letter/{revision_id}/idempotent-revise/`

The request is the create command plus `expected_version`. The route revision
must be the current draft and its version must equal `expected_version`.

Finalize the current draft and create its stored PDF:

`POST /api/v1/correspondence_letter/{revision_id}/idempotent-finalize/`

The request contains the common context and `expected_version`; it contains no
browser title, recipient, signature, HTML, artifact metadata, or replacement
body. CARE finalizes the exact stored draft body.

Read routes are:

- `GET /api/v1/correspondence_letter/{revision_id}/`
- `GET /api/v1/correspondence_letter/?review_binding={uuid}&patient={uuid}&encounter={uuid}&limit={1..100}&offset={0..}`

The list requires the exact immutable review binding and never mixes another
letter/correction lineage from the same encounter. CARE authorizes and
revalidates that binding and its patient/encounter context before querying
revisions. The default page size is 14, the maximum is 100, and `offset` allows
every page to be retrieved. The response `count` is the total non-soft-deleted
revision-row count for that exact lineage before `limit`/`offset`; `results`
contains only the requested page. Any
dirty/unavailable revision encountered on a page fails the complete page closed
instead of serializing partial clinical content. Unknown query parameters and
out-of-range pagination values return `400`.

Commands return `201` for the first commit and `200` for an exact replay:

```json
{
  "client_request_id": "uuid-v4",
  "replayed": false,
  "correspondence": {
    "id": "revision-uuid",
    "letter": "series-uuid",
    "resource_version": 1,
    "status": "draft",
    "body": "Plain-text clinical correspondence",
    "body_hash": "sha256",
    "revision_hash": "sha256",
    "artifact": null
  }
}
```

Responses include an ETag derived from the immutable revision ID/hash. A
finalized response contains the native ReportUpload artifact ID, SHA-256,
authenticated signed download URL, generator identity, timestamp and MIME type.

## Safety properties

- UUIDv4 command keys are unconditionally unique across soft deletion. The
  validated command, current actor, route target and complete source context are
  canonically hashed. Conflicting key reuse is a non-leaking `409`.
- Every mutation locks and revalidates the exact review, compilation, recipient,
  patient, encounter, facility, department and current author. Exact replay
  requires current read permission; a new mutation additionally requires
  current write permission and the original verified author.
- Each write creates a new `CorrespondenceLetterRevision`; earlier and finalized
  rows cannot be updated. `corrletter_revision_ver_uniq` and row locking prevent
  two-tab overwrite. A stale `expected_version` returns `409`.
- Finalization uses the stored normalized plain-text body, escapes it into
  server-owned HTML, rejects unresolved placeholders, renders a PDF, and stores
  one patient/encounter-linked ReportUpload under
  `corrartifact_revision_uniq`. No executable browser HTML or browser title is
  accepted.
- Review/source/hash drift, deletion, archival, malformed artifacts and frozen
  snapshot corruption fail closed. Storage or transaction failure returns
  retryable `503`; database rows roll back and an uploaded object is compensated.
- This slice creates no delivery attempt, sent state, recipient write API,
  localStorage migration, or source amendment. Slice 10 must consume the exact
  finalized revision/artifact and independently reauthorize before delivery.

## Slice 11 historical-read dependency

Slice 09 deliberately uses current `reviewed_binding_available` validation for
commands and reads. Therefore later recipient, template, review-source, or
verification invalidation currently makes even a previously finalized artifact
unavailable through the correspondence-revision route. New draft mutations,
finalization, attachment, and delivery must continue to fail on stale live
sources. Slice 11 must introduce the separate historical-read policy: an
authorized caller can verify and retrieve the frozen immutable revision/PDF
from stored hashes and snapshots, while CARE reports current-source drift,
revocation, warning, supersession, and correction lineage without treating the
historical artifact as eligible for a new action. Slice 09 does not claim that
split yet.

Migration `0084_correspondence_letter_workflow` creates the aggregate and adds
the nullable ReportUpload provenance link. It does not modify existing
correspondence compilations or artifacts.

## Printout corrections — 12 September 2026

`reports/correspondence_letter.py`:

- **Patiëntnummer** now comes only from identifiers whose configuration is a
  record number. Phone-number, e-mail and name identifier systems are skipped
  (a synthetic letter printed the patient's phone number here). Identifiers
  with `use` usual/official rank first; with no eligible identifier the field
  prints "Niet vastgelegd". AZP currently has no MRN identifier configured, so
  letters print "Niet vastgelegd" until an administrator adds one under
  Settings → Patiënt-ID.
- **One closing per letter.** When the template body already ends with a
  closing ("Met collegiale groet," etc.), the server sign-off omits its own
  greeting line and prints only the frozen author identity, role and facility.
- **Role label in Dutch** for CARE's built-in role names (Doctor → Arts, …)
  via `professional_role_label`; custom role names print unchanged.

Tests: `tests/test_correspondence_letter.py` (four new). Rollback: revert the
file; no data or schema change.

## AZP PDF letterhead — 13 September 2026

The final server-rendered PDF owns the visual letterhead; editable native
template content owns only the salutation and clinical prose. For Academisch
Ziekenhuis Paramaribo, `correspondence_letter_branding.py` supplies the accepted
AZP logo, Flustraat 1 address, central number and Urology clinic number. The
hospital name appears once, while the frozen clinician and department remain in
the sign-off. Other facilities keep the same generic layout without AZP assets.

The profile is presentation-only: no clinical snapshot, recipient, identifier,
delivery, or database schema changes. Rollback is the branding module plus the
letterhead/style integration in `correspondence_letter.py` and
`correspondence_letter_styles.py`.

## Legacy native-template normalization — 13 September 2026

The final renderer owns the letterhead, patient metadata and frozen signature.
`correspondence_template_body.py` therefore recognizes the exact old AZP
full-document prefix and removes its server-owned prefix/footer during new
compilation. The match requires the hospital name, Flustraat 1, Urology letter
title, patient-number label and Dutch salutation; partial or ordinary clinical
text is left unchanged. This prevents a phone identifier from reappearing in
the editable body even though the official metadata already rejects it.

Existing compilations and finalized PDFs are immutable and are not rewritten.
Rollback: remove the normalizer call/module; no schema or data migration exists.
