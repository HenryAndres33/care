# CARE Suriname backend plug

`care_suriname` contains backend behavior that belongs to the Suriname deployment rather than native CARE. It is loaded through CARE's plug mechanism and is the backend companion to `care_fe/src/Plugins/urology/`.

## Current state

The extraction baseline is backend commit `5d79daccc` (Phase 2 step 3).

It includes:

- custom API endpoints while retaining their existing `/api/v1/` addresses;
- correspondence and admission-note extensions;
- periodic correspondence tasks and management commands;
- deployment checks;
- Suriname-specific authorization methods; and
- migration `emr 0107`, which reverses the letter-artifact relationship so no native CARE model points at a custom model.

Phase 2 step 2 (`572771c63`) moved the 34 custom models with pinned tables and state-only migrations; step 3 moved the custom code modules. The final held note-lab modules now live in `resources/form_submission/`, with their contract in [NOTE_LABS.md](resources/form_submission/NOTE_LABS.md). This code-only move requires no migration. Deployment remains a separate action; see the dated extraction evidence in the development documentation.

## Boundary rules

The dependency direction is:

```text
care_suriname plug -> CARE core
```

Native CARE models must not have foreign keys to plug-owned models. If a relationship is required, put the relationship on the plug-owned side or introduce a plug-owned linking model.

Existing endpoints may remain below `/api/v1/` through the documented compatibility seam so deployed frontends do not need a coordinated URL migration. New plug-only endpoints should normally use CARE's standard plug prefix:

```text
/api/care_suriname/
```

Add a new `/api/v1/` route only when compatibility with an existing client requires it, and record the reason in the compatibility document.

Registration must happen through the plug's `AppConfig` and documented CARE extension mechanisms. Startup registration for checks, extensions, authorization handlers, and tasks must have tests that prove registration after Django starts.

## Source layout

The main areas are:

- `api/` — Suriname API viewsets, serializers, and URL registration;
- `authorization/` — additional permission methods;
- `checks/` — deployment and configuration checks;
- `extensions/` — CARE extension registrations;
- `draft_recovery/` — owner-bound encrypted-draft key release and authentication proof;
- `management/commands/` — operational commands;
- `migrations/` — plug-owned migration state;
- `models/` — plug-owned models;
- `resources/form_submission/` — custom command contracts and note-lab parsing/registration; and
- `tasks/` — Celery tasks and periodic scans.

Draft recovery is plugin-owned, with its original `users_draftrecoverykey` table pinned and paired state-only users0029/plugin0002 migrations. The native safety patches remain intentional. See [draft recovery](draft_recovery/README.md) and the dated ownership evidence before migration or rollback. No production deployment is implied.

## Database and migration rules

Moving a model between Django apps is a schema-sensitive change even when its table name stays the same. For a state-only move:

1. Pin every moved model to its existing table with `db_table`.
2. Preserve applied migration history; do not rewrite migrations that have reached a database.
3. Test from a restored database and from an empty database.
4. Test forward, reverse, and forward migration paths.
5. Compare row counts and relevant checksums before and after.
6. Preserve content-type IDs, permissions, audit references, indexes, and constraints.
7. Run `makemigrations --check` after migration.
8. Back up the database and MinIO objects together before applying it to a deployed environment.

A migration is not considered deployed merely because its code is mounted by a development container. Record separately whether it has been rehearsed, applied on the laptop, and applied on the server.

## Packaging

The plug is currently stored in the same repository as the CARE backend for practical development and deployment. Its target boundary is still an independently installable Python package, following CARE's public backend plug pattern.

Code in this app must not depend on sharing a source repository with CARE. Keep imports pointed from the plug into CARE's public or documented interfaces. Package extraction can then happen later without redesigning the clinical behavior.

For the offline clinic, deterministic installation is more important than downloading a package at runtime. A package may be built and pinned during deployment while still running fully offline afterward.

## Verification

For code changes, run the checks appropriate to the affected area, including:

- Ruff lint and formatting checks;
- Django system checks;
- targeted tests for the changed endpoints, permissions, tasks, or migrations;
- startup-registration tests for extension hooks; and
- the full backend suite before deployment when migrations or shared behavior change.

The known serial concurrency-test failure must only be treated as pre-existing when the identical command also fails at the recorded baseline in an isolated worktree.

Operational details and the migration rehearsal procedure are in [`docs/development/plug-app.md`](../docs/development/plug-app.md). The exact frontend/backend compatibility baseline and deployment state are recorded in [`care_fe/docs/plugin-compatibility.md`](../../care_fe/docs/plugin-compatibility.md).

## Patient directory and command constants — 19 September 2026

The existing read-only `/api/v1/patient/directory/` now belongs to
`api/viewsets/patient_directory.py`, with request/identity types in
`resources/patient_directory.py`. It searches native Patient rows; facility
membership authorizes the lookup, not full chart access. It deliberately does
not limit patients to an encounter in that facility. The URL, permission,
validation, six identity fields, stable ordering and counted pagination remain
unchanged. Frontend search, header/agenda typeahead, programme selection and
admin directory still use the same endpoint without frontend edits.

`v1_urls.priority_urlpatterns` opts this one literal path into the generic
`plugs.urls.with_priority_routes` seam. It must precede native patient-ID matching;
normal plugin routes retain their old order. Duplicate exact paths/names and
parameterized priority registrations fail closed. Native UUID lookup and DELETE
stay native; debug format aliases and schema/reverse metadata are preserved.

Each command model now owns its own idempotency constraint-name constant in
`models/form_submission_command.py` or `models/form_submission_artifact_command.py`.
Names and model constraints are unchanged; no migration is required. Roll back
this code-only batch as a unit (route registration, plugin implementation and
native removals), never by adding a second directory. No database rollback.
See [verification and remaining work](../docs/development/2026-09-19-directory-and-constraint-ownership.md).
