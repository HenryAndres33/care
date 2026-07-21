# Consult closure workflow (Slice 12)

Slice 12 provides a disease-independent, server-orchestrated boundary for
closing an outpatient consultation. It binds the current encounter, native
CARE booking/token state, finalized form source and PDF artifact, complete
medication inventory, and correspondence outcome into one immutable evidence
snapshot. BPH, haematuria, stones, and later custom forms use the same command;
only the configured required questionnaire changes.

## API

All responses use `Cache-Control: no-store`.

- `POST /api/v1/consult_closures/{encounter}/preflight/`
- `POST /api/v1/consult_closures/{encounter}/idempotent-close/`
- `POST /api/v1/consult_closures/{encounter}/idempotent-resolve-recovery/`
- `GET /api/v1/consult_closures/{encounter}/`

Preflight returns an exact command candidate containing `encounter`, the
authoritative timestamps and states, policy and preflight hashes, finalized
form/artifact evidence, medication actions, and correspondence evidence. The
client must echo that candidate unchanged with a unique `client_request_id`
and `confirmed: true`. An exact replay returns the original immutable result;
a reused key with different context or payload returns
`idempotency_conflict`.

## Required-form policy

`CONSULT_CLOSE_REQUIRED_FORMS_BY_DEPARTMENT` maps a normalized department name
or department external UUID to required questionnaire slugs. The default is:

```json
{"urology": ["urology-medisch-dossier"]}
```

The current implementation deliberately accepts one required form series per
department. Preflight locks the current finalized series head and rejects a
different, stale, draft, placeholder-containing, or artifact-less source. Add
departments by configuration; do not add diagnosis-specific close commands.

## Correspondence outcomes

- `not_required`: accepted only when the entire form series has no compilation
  or correction case and no incomplete correction outbox.
- `delivery_acknowledged`: requires the exact current compilation, frozen
  review, delivery ledger, and latest acknowledged delivery event. Correction
  or replacement lineage makes this outcome stale.
- `correction_resolved`: the request supplies no compilation. The server
  derives the one resolved correction case and its original compilation from
  the locked current form series and verifies its frozen lineage.

## Atomic close and lock order

The close transaction writes the encounter status/history and period end,
clears native location/device associations, fulfills the booking and token,
clears subqueue pointers, and inserts the immutable closure and command ledger.
Any failure rolls back all changes.

The enforced lock order is:

1. finalized form series head/current source;
2. encounter;
3. closure/recovery ledger;
4. booking and token;
5. location, device, medication, and correspondence auxiliaries.

Ordinary Encounter, TokenBooking, Token, FormSubmission, Device, Location, and
MedicationRequest routes reject terminal-state bypasses. Consult closures also
block the legacy encounter restart endpoint. These are small generic CARE
integrity guards, not urology- or BPH-specific forks.

## Recovery and integrity

Authoritative GET and replay revalidate terminal native state plus the complete
closure/command hash chain. Drift or tampering never returns a successful
closure. It creates at most one immutable pending recovery task for the
encounter and returns safe code `closure_integrity_failed`. Recovery responses
contain only `id`, `status`, `safe_code`, `recovery_hash`, `created_at`, and
`resolved_at`. `recovery_hash` is exposed only when the record passes server
integrity validation; an invalid record returns `null`, which disables the
resolve action.

The projection first reads a closure reference only to determine the required
source-series lock. After locking source, Encounter, and closure/recovery rows
in the documented order, it compares the locked latest closure with that
reference. A mismatch causes a fresh-transaction retry and never creates a
recovery task. This prevents a GET waiting on a concurrent close from
misclassifying the newly committed closure.

Recovery resolution is an explicit, authorized, idempotent reconcile command.
It requires the exact pending recovery ID/opening hash and a unique client
request ID. The server reruns the complete authoritative integrity projection;
it refuses resolution while any closure ledger, form, medication,
correspondence, encounter, booking, token, subqueue, location, or device
invariant remains corrupt. Once repaired, the controlled `pending -> resolved`
transition preserves the opening hash and records resolver, request, payload,
resolution timestamp, and resolution hash. Exact replay returns the same safe
result. Migration `0090_consult_closure_recovery_resolution` adds this audit
state.

Post-close form amendment/reconciliation is intentionally fail-closed in Slice
12. Slice 13 must introduce an explicit audited reconciliation/addendum command
before that path is enabled; ordinary mutation must not be used.

## Verification and deployment

Run:

```bash
python manage.py test care.emr.tests.test_consult_closure --keepdb
ruff check care/emr/models/consult_closure.py care/emr/resources/consult_closure.py care/emr/api/viewsets/consult_closure.py care/emr/tests/test_consult_closure.py
python manage.py makemigrations --check --dry-run
python manage.py check
```

Apply migrations `0089_consult_closure_workflow` and
`0090_consult_closure_recovery_resolution` before enabling the frontend close
action. Roll back only while no closure, command, or recovery row exists; after
clinical use, preserve the ledger and roll forward.
