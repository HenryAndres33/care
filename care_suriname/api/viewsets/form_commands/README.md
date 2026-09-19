# Form submission commands

This package owns six existing `/api/v1/form_submission/` commands: create draft,
update draft, finalize, amend, enter in error and generate artifact. The native
router still registers their exact action metadata through
`plugs.viewset_actions.with_contributed_actions("form_submission")`.

`command_parts()` returns plain method classes. The generic host checks every
name and route before composing them; no host attribute can be replaced. Private
staticmethod descriptors retain their binding. Duplicate helpers/actions/routes
fail closed. With no provider, native class identity and CRUD are unchanged.

- `draft.py`: create, context locks and create replay.
- `actions.py`: detail command declarations.
- `execution.py`: transactional dispatcher, series/head ordering and write checks.
- `mutations.py`: draft/finalize/amend/error mutations and note-lab registration.
- `responses.py`: command ledger replay, responses and specialty validation.
- `artifact_action.py`: artifact transaction and compensation orchestration.
- `artifact_source.py`: source validation, rendering, upload and cleanup.
- `artifact_replay.py`: artifact ledger/reuse and provenance checks.
- `artifact_responses.py`: signed-download response and error contracts.
- `errors.py`: private exceptions and named-constraint extraction.

Native FormSubmission retains generic CRUD, authorization, optimistic-version
checks, immutable/draft vetoes and submission row locking. Native models retain
their write/constraint safeguards. The no-store response mixin remains an
intentional native import. All feature-specific series/ledger, Urology operation,
structured-link, note-lab, workflow gate and artifact compensation logic lives
here or in the existing plugin resources/models/reports.

Four private response/raise wrappers now use instance dispatch instead of
hardcoding the native viewset class. Everything else retains identical method
ASTs, including all six action/schema decorators. Logger category is deliberately
preserved for operational compatibility. Transaction boundaries, lock ordering,
hashes, PDF renderer, source snapshot and storage provenance are unchanged.

Rollback this code-only batch together (contribution, seam support, plugin
methods and core removal); no schema/data rollback or compatibility shim.
See [verification](../../../../docs/development/2026-09-19-form-command-ownership.md).
