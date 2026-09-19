# CARE Suriname backend plug

`care_suriname` contains backend behavior that belongs to the Suriname deployment rather than native CARE. It is loaded through CARE's plug mechanism and is the backend companion to `care_fe/src/Plugins/urology/`.

## Current state

The committed baseline is backend commit `799c8bb84`.

It includes:

- custom API endpoints while retaining their existing `/api/v1/` addresses;
- correspondence and admission-note extensions;
- periodic correspondence tasks and management commands;
- deployment checks;
- Suriname-specific authorization methods; and
- migration `emr 0107`, which reverses the letter-artifact relationship so no native CARE model points at a custom model.

The model extraction in Phase 2 step 2 is currently being developed in the working tree. Until that work is committed and its migration is deliberately applied, the 34 custom model classes and their database state must be treated as transitional. Do not infer deployment state from files visible in an uncommitted worktree.

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
- `management/commands/` — operational commands;
- `migrations/` — plug-owned migration state when the model move is complete;
- `models/` — plug-owned models when the model move is complete; and
- `tasks/` — Celery tasks and periodic scans.

Some paths may be transitional while Phase 2 is uncommitted. Use Git history and the migration plan rather than moving or deleting those files independently.

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
