# Finalized FormSubmission PDF Artifact

The FormSubmission artifact contract is generic and questionnaire-independent.
It stores a server-rendered PDF for one exact immutable finalized
`FormSubmission` version through CARE's native `ReportUpload` and report-bucket
download path.

## Generate

```text
POST /api/v1/form_submission/{form_submission_id}/idempotent-generate-artifact/
```

The strict request accepts only:

```json
{
  "client_request_id": "UUIDv4",
  "patient": "patient UUID",
  "encounter": "encounter UUID",
  "questionnaire": "questionnaire slug",
  "source_version": 2,
  "source_snapshot_hash": "64 lowercase SHA-256 hex characters"
}
```

The source ID is the route ID. The remaining references, version, and hash must
exactly match that source. The command returns
`{ client_request_id, replayed, artifact }`; the first creation is `201`, and
an exact replay is `200`. A new authorized key for an already-rendered exact
source also returns that one artifact with `replayed: true`.

`formartifact_cmd_request_id_uniq` durably reserves command keys, and
`formartifact_source_version_uniq` enforces one artifact per immutable source
row/version. Both constraints include soft-deleted rows.

## Safety and authorization

Generation requires current FormSubmission read permission and native
`encounter_report` generation permission. The FormSubmission and Encounter are
locked and all context and authorization checks run again inside the database
transaction. Exact command replay requires current source and report read
permission but does not require a new write authorization.

Only `submitted` workflow-finalized encounter-scoped sources are eligible. The
server recomputes the finalized snapshot hash and blocks stale, empty,
malformed, oversized, deeply nested, unresolved-placeholder, deleted, or
wrong-context sources. The request cannot provide a title, file bytes, or
clinical content.

The PDF uses only server-owned patient, encounter, questionnaire, audit, source,
artifact and generation metadata plus the immutable escaped `response_dump`.
The Report row stores patient, encounter, source ID/version/hash, generator,
generation time, MIME type, and PDF SHA-256 provenance. Completed generated
artifacts cannot be mutated or archived.

## Download and failures

The response includes a short-lived authenticated signed URL. The artifact can
also be reopened through:

```text
GET /api/v1/template_reports/{artifact_id}/
```

Report authorization is checked before that endpoint returns a signed URL.
Rendering, object storage, or command-ledger failure returns `503`, commits no
database artifact, and attempts object-store compensation; the caller should
retry the same key. If only URL signing fails, the artifact and command remain
stored and the `503` explicitly instructs an exact retry. Conflicts never
return an artifact body, and unavailable/deleted artifacts fail closed.

Artifact generation is an explicit post-finalization command. The
`idempotent-finalize` action does not generate a PDF automatically.

## Admission-note titles — 12 September 2026

`reports/form_submission_artifact.py` titles the printout from the admission
documentation reservation when one exists for the submission's series:
`admission` → "Opnamenotitie", `visit:<day>` → "Visitenotitie",
`discharge` → "Ontslagsamenvatting"; otherwise "Medisch dossier" (and
"Operatieverslag" for the operations questionnaire). Inpatient encounters
label the date "Opnamedatum" instead of "Consultdatum". Nothing new is stored;
the slot kind already lives on `AdmissionDocumentation`. Existing artifacts are
immutable and keep their old title. Tests: two in
`tests/test_form_submission_artifact.py`.
