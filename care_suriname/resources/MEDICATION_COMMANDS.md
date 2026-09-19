# Medication commands

`../api/viewsets/medication_commands.py` owns the existing create and replay-only
reconcile actions plus their validation/hash/context helpers. The lazy
`viewset_actions:medication_request` contribution uses the existing generic
additive action seam. Native router, permissions, closed-encounter locks, CRUD,
QuestionnaireResponse/audit write machinery and cache-control remain native.
No action can override a host attribute; no new seam or route priority is added.

POST `/api/v1/patient/{patient}/medication/request/idempotent-create/` returns
201 for a new command and 200 for an exact replay. Reconcile accepts the same
strict flat request and returns 200 for an exact committed command or 404 without
writing. Payload/context conflicts and deleted targets return 409 without leaking
the resource. Hash normalization and specs stay in medication_request_idempotency.py.
The native MedicationRequest row stores the command ID/hash; no separate ledger
model exists. The unconditional unique constraint reserves IDs after soft delete.

Execution retains read-context authorization before replay, encounter row lock,
repeated context/replay checks, workflow mutation gate and new-write authorization.
The command reserves its key before prescription and QuestionnaireResponse side
effects. All writes roll back together. Only the named idempotency IntegrityError
is caught for replay; other integrity failures propagate unchanged.

`resolve_created_prescription` deliberately remains a generic native helper: it
was factored from native MedicationRequestSpec.deserialize and is used by legacy
CRUD as well as this command. The plugin owns when the command invokes it, not
a duplicated prescription implementation. The no-store mixin still protects all
medication responses, including native CRUD, and remains an enumerated core
import until a separately reviewed generic response-policy hook exists.

Existing native regression tests continue covering legacy CRUD and the shared
prescription model; their command-module patch target follows ownership. New
plugin ownership tests prevent the custom action bodies returning to core.
Rollback this code-only batch as a unit; no database rollback or migration.
See [verification](../../docs/development/2026-09-19-medication-command-ownership.md).
