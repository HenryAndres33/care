# Diagnosis commands

`../api/viewsets/diagnosis_commands.py` owns `DiagnosisCommandActions`. The lazy
`viewset_actions:diagnosis` contribution attaches only new action/helper methods
to the native DiagnosisViewSet through a generic decorator. No native CRUD or
authorization method can be replaced. With no contribution the host class is
returned unchanged; duplicate contributions/actions fail closed.

The existing POST `/api/v1/patient/{patient_external_id}/diagnosis/idempotent-create/`
keeps its DRF nested-router position, reverse name, schema, authentication and
format aliases. Ordinary late plugin URLs lose to the native detail regex; the
literal-only priority seam cannot express this parent parameter. The action seam
therefore preserves routing without widening priority matching.

Request/response specs and canonical hashing stay in `condition_idempotency.py`.
An exact replay returns 200; creation returns 201; changed/deleted/other-actor
replay and active duplicate return 409. The native Condition row stores command
identity/hash; there is no separate command-ledger model. Patient and encounter
row locks, second replay check, native authorization, duplicate detection and
creation/audit remain inside the same transaction. Integrity-error replay,
clinical-domain values, payload normalization and closed-encounter behavior are
unchanged. Core guards protect the same write paths as before.

Tests live under `care_suriname/tests/test_diagnosis_command*.py`; generic host
tests live in `plugs/tests/test_viewset_actions.py`. The existing native chronic
update authorization regression remains in native tests and is a known baseline
failure, not fixed by this ownership move. See
[dated evidence](../../docs/development/2026-09-19-diagnosis-command-ownership.md).
