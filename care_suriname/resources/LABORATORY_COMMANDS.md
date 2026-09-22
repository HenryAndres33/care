# Laboratory command API

Status: **IMPLEMENTED — generic native unknown-date parity approved and verified**  
Date: 20 September 2026

## Boundary

The module orchestrates native CARE `ServiceRequest`, `DiagnosticReport`,
`Observation` and `ObservationDefinition` resources. It adds no model, migration
or native-core route. Its guarantees cover command-owned reports. Native writers
can still change those resources; verified reads and later commands detect such
changes through an aggregate fingerprint and fail closed with
`aggregate_drift`.

## Routes

All routes require native authentication and authorization and return
`Cache-Control: no-store`.

```text
GET  /api/care_suriname/laboratory/definitions/?facility={uuid}
POST /api/care_suriname/laboratory/report-commands/
GET  /api/care_suriname/laboratory/reports/?patient={uuid}&facility={uuid}
GET  /api/care_suriname/laboratory/reports/{report_id}/?include_history=false
```

The definitions response contract is
`care-suriname-laboratory-catalogue-v1`. Each active laboratory definition
returns its native UUID and slug, positive database revision, a 64-character
fingerprint, native code/type/unit/method/body site/ranges, and exact governed
`reference` metadata when the LOINC and unit-system/code triple is configured.
The fingerprint covers deletion/status, semantic fields, configured range
metadata and the canonical textbook catalogue metadata/provenance. CARE's
resource schema `version: 0.1` is never used as the definition revision.

The list route is paginated with `limit` (1–50) and `offset`, and may filter
`encounter` and `status`. It returns only command-owned reports with report,
request and encounter IDs, source, version, modified time and `integrity` equal
to `verified` or `drift`. Legacy native reports are not claimed by this API.

## Command envelope

Every POST uses strict JSON (`extra=forbid`):

```json
{
    "contract": "care-suriname-laboratory-command-v1",
    "action": "create_draft",
    "client_request_id": "uuid-v4",
    "expected_version": 0,
    "patient": "uuid-v4",
    "facility": "uuid-v4",
    "encounter": "uuid-v4",
    "service_request_id": "uuid-v4",
    "report_id": "uuid-v4"
}
```

`create_draft` and `update_draft` also send one external-lab source and the
complete intended row snapshot. `finalize` sends only the envelope. `correct`
sends a nonblank reason and replacement rows with new stable UUIDs and the
current visible observation UUIDs they replace.

A result row is:

```json
{
    "row_id": "uuid-v4",
    "collection_group_id": "uuid-v4",
    "definition": {
        "id": "uuid-v4",
        "slug": "full-facility-scoped-slug",
        "version": 1,
        "fingerprint": "sha256"
    },
    "collected_at": { "kind": "known", "value": "2026-09-19T08:30:00-03:00" },
    "specimen": "serum",
    "confirmed_reference_context": ["fasting_confirmed"],
    "value": {
        "kind": "quantity",
        "input": "5,0",
        "unit": {
            "system": "http://unitsofmeasure.org",
            "code": "mmol/L",
            "display": "client display is not authoritative"
        }
    }
}
```

`collected_at` is either a timezone-aware, non-future known datetime or
`{"kind":"unknown"}`. The server never substitutes the current time. Supported
values are quantity, decimal, integer, string and boolean. Numeric input keeps
the exact entered string and stores a canonical decimal string; it accepts comma
or dot plain notation and enforces CARE's 20 total/6 fractional digit limit.
Blank rows are omitted by the client and at least one row is required.

`specimen` is explicit recorded context: `blood`, `plasma`, `serum`,
`whole_blood`, or null/omitted for unknown. A range rule is never proof of the
actual specimen. Confirmed context accepts only:

```text
fasting_confirmed, nonpregnant_confirmed, no_anticoagulant,
no_renal_failure, no_anemia, no_hemoglobinopathy, no_hiv,
stable_red_cell_turnover
```

Empty means unknown, never false. Rows with one `collection_group_id` must have
identical collection time, specimen and confirmed context. Corrections validate
the resulting visible snapshot, ignoring historical entered-in-error rows.

The server resolves definition code, method, body site and unit display. It
validates submitted quantity unit system/code exactly and discards a misleading
client display. It applies governed reference rules only for the exact LOINC
system plus exact unit system/code. A method-specific rule requires the native
definition method; missing context produces an unavailable provenance result.
No conversion, inferred specimen, invented method or invented range occurs.

## Native state and lifecycle

`create_draft` atomically creates a draft ServiceRequest, preliminary
DiagnosticReport and final Observations. `update_draft` applies a complete
snapshot and soft-deletes removed draft rows. `finalize` accepts no values,
revalidates definitions and reference context, and changes the request/report to
completed/final. A DOB, recorded-sex, catalogue or selected-reference change
after draft review returns a conflict and requires a reviewed draft update.
Final observations retain immutable range and provenance snapshots.

`correct` keeps the report final, marks the replaced observation
`entered_in_error`, creates an `amended` child with parent UUID and reason, and
recomputes ServiceRequest occurrence from current visible rows. History is
available with `include_history=true`.

Each mutation runs in one transaction. Locks use patient → encounter → service
request → diagnostic report → definitions ordered by database ID → observations
ordered by database ID. Existing definitions and observations are locked before
the aggregate fingerprint is checked, preventing the plugin from silently
overwriting a native change observed before its lock. Creation checks both
service-request and diagnostic-report write authorization before saving either
resource. Clinically closed encounters and disabled workflow mutations fail.

## Retry, version and read-back

The report metadata stores a bounded current command version, latest receipt,
source, audit snapshots and aggregate fingerprint. Exact latest-command replay
by the same current authorized actor returns 200 with `replayed:true`; a reused
ID with different payload returns `idempotency_conflict`. Competing commands at
one expected version serialize and one receives `version_conflict`. Replay never
bypasses current authorization.

Create returns 201. Other successful commands and replay return 200. Responses
include `ETag: "{report_id}:{command_result_version}"`, plus:

- report/request/patient/facility/encounter/status/source;
- `audit.created_by`, `finalized_by`, `latest_corrected_by` as stored
  `{id,display}` objects with stored timestamps;
- each stable `row_id` (equal to native observation external ID),
  `collection_group_id`, explicit specimen/context, authoritative code/method/
  body site/unit, exact input and canonical value;
- stored interpretation and reference range with min/max, inclusive flags, unit,
  rule ID and category;
- full `reference_provenance`, including selected rule/applicability/source,
  catalogue version/fingerprint or an explicit unavailable reason; and
- correction parent/reason.

Detail GET returns the same report in
`{contract,command_result_version,report}` after authorization and aggregate
verification. Clients must use this durable read-back before showing success.

## Errors and operational limit

Strict payload failures are safe 400 `laboratory_payload_invalid` responses and
do not echo clinical input. Definition mismatch/deletion returns
`catalogue_changed`; stale version, replay collision, invalid state, collection
conflict, correction target and aggregate drift are 409 conflicts. Definition
value/unit mismatch is 422 `laboratory_result_invalid`. Every failure rolls back.

The plugin detects out-of-band mutations on its next verified read or command.
It cannot prevent all native endpoints from writing the same resources. Urology
consumers should use these verified plugin reads for command-owned reports; a
future generic native lifecycle seam would be required for an all-writer
invariant across CARE.

## Approved native unknown-date parity

Explicit unknown collection time maps correctly to the existing nullable
`Observation.effective_datetime` database field. The native read resource
previously declared that field non-nullable, so its ordinary Observation
endpoint raised a serialization error for a valid null model value. The
approved generic fix is one line in `care/emr/resources/observation/spec.py`:

```diff
-    effective_datetime: datetime
+    effective_datetime: datetime | None
```

This contains no plug import, Suriname label or specialty rule; it aligns the
generic read schema with the existing native model and is suitable for an
upstream CARE bug-fix proposal. The owner approved this exact one-line change.
A focused plugin regression creates an explicit unknown-date observation and
reads it through the native Observation list; plugin detail and native
Observation/DiagnosticReport reads were also verified with a bounded synthetic
unknown-date aggregate.

The plugin-only alternative is to read command-owned rows exclusively through
the verified plugin detail endpoint. That does not repair native patient-chart
or other generic Observation consumers, which can encounter the same nullable
model row and fail serialization. Storing a fabricated datetime would destroy
the legally meaningful distinction between unknown and known collection time.
No existing plugin extension hook can change a native Pydantic resource field.
For those reasons the approved one-line generic type correction is the smallest
safe solution. Its inventory, upstream-parity rationale and rollback condition
are recorded in the native ownership documentation. Rollback remains unsafe
while any persisted Observation has a null effective date, because restoring
the non-null read schema would make those valid records unreadable through
native Observation and DiagnosticReport APIs.
## Age at collection with a year of birth only — 22 September 2026

Native CARE may hold `year_of_birth` without `date_of_birth`. The governed
reference evaluation then uses an inclusive age interval (two consecutive ages,
because the birthday within that year is unknown); a rule applies only when
every age in the interval satisfies it. An interval that straddles 18 stays
`age_at_collection_required`; one entirely below 18 stays
`pediatric_reference_not_available`. The stored `reference_context` gains
`birth_year` only when `birth_date` is null, so rows written before this change
keep an identical context when a date of birth exists, while an added or changed
year of birth is detected as `reference_context_changed`. Plugin-only; no native
change.
