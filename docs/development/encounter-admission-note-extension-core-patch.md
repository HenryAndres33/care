# Encounter admission-note extension core patch

## Rationale and native contract

CARE's native `Encounter.extensions` registry is the persistence boundary for the
free admission note. The provisioned key is
`care_suriname_admission_note`, version `1.0.0`, with this value:

```json
{"text": "string, 1 to 4000 characters and not whitespace-only"}
```

No parallel model or fallback field exists. Unknown properties, blank text, and
text over 4000 characters fail closed. The extension remains optional.

The only safe post-create write contract is:

```text
POST /api/v1/encounter/{encounterId}/set-admission-note/
{"text": "string"}
```

It returns the native Encounter detail with HTTP `200`. The command locks the
Encounter row, requires native `can_update_encounter_obj` authorization, rejects
clinically closed encounters, and writes only the governed extension key plus
`updated_by` and `modified_date`. An exact retry performs no database write and
returns the same detail state.

## Governance and clinical-safety review

- Named owner: **CARE Suriname Clinical Governance**.
- Retention: the note is part of the clinical encounter record and follows the
  encounter's retention and disposal policy; it has no independent deletion path.
- Purpose: admission context only. It is not an order, diagnosis, attestation, or
  replacement for the paper legal record.
- Access and audit: post-create changes use the narrow command and native
  encounter authorization/audit fields. General Encounter PUT/PATCH is
  intentionally not used because it can close device and location associations.
  Clinically closed encounters remain immutable.
- Data minimization: no author or patient identifiers are duplicated inside the
  extension value.

## Migration and rollback

The stable key and `extension_version` are the migration boundary. A future
breaking schema change must first ship a reversible Django data migration over
`Encounter.extensions`, then raise the registered version and enforcement. Never
reinterpret existing text in place.

Rollback is safe while no persisted values use the key: remove the registration
import and extension module. Once values exist, keep the reader registered until
the reversible data migration has removed or transformed every value. Removing
the registry first would make later writes silently omit the unknown key.

## Touched files and upstream review points

- `care/emr/extensions/__init__.py`: startup registration import.
- `care/emr/extensions/encounter_admission_note.py`: schema and governance metadata.
- `care/emr/resources/encounter/admission_note.py`: exact command request contract.
- `care/emr/api/viewsets/encounter_admission_note.py`: locked, extension-only
  command implementation.
- `care/emr/resources/encounter/spec.py`: native list rendering through the
  existing `ExtensionListRenderer` pattern.
- `care/emr/tests/test_encounter_admission_note_extension.py`: registry,
  fail-closed validation, and native detail/list read-back coverage.
- `care/emr/tests/test_encounter_admission_note_command.py`: authorization,
  closure, idempotency, preservation, and association safety coverage.
- `config/api_router.py`: explicit command route.

During an upstream update, review the extension registry startup hook,
`ExtensionValidator`, encounter serialization, closed-encounter immutability, and
whether unknown extension keys have changed from omission to rejection.

## Verification

```bash
/.venv/bin/ruff check care/emr/extensions/encounter_admission_note.py \
  care/emr/tests/test_encounter_admission_note_extension.py
/.venv/bin/python manage.py test \
  care.emr.tests.test_encounter_admission_note_extension \
  care.emr.tests.test_encounter_admission_note_command --keepdb
/.venv/bin/python manage.py makemigrations --check --dry-run
git diff --check
```

Live verification uses a synthetic, non-superuser Doctor against port `9000`.
Record only endpoint/status/schema evidence and the cleanup result; do not retain
credentials, tokens, native identifiers, or clinical payloads.

### Sanitized live evidence

V2 admission invariant:

- `POST /api/v1/auth/login/` -> `200` for the synthetic non-superuser Doctor.
- First `POST /api/v1/encounter/` (`imp`, `in_progress`) -> `200`.
- Second active inpatient `POST /api/v1/encounter/` -> `400` with the normalized
  active-inpatient validation error.
- `GET /api/v1/encounter/{id}/` -> `200`; schema read-back confirmed
  `hospitalization.admit_source`.
- Explicit `POST /api/v1/encounter/{id}/idempotent-discharge/` -> `201`;
  subsequent inpatient readmission `POST` -> `200`.

V3 extension contract:

- `GET /api/v1/extensions/` -> `200`; the encounter definition exposed `name`,
  `version`, and `write_schema`.
- Native encounter create with the registered extension -> `200`.
- Native encounter detail read-back -> `200`; schema path
  `extensions.care_suriname_admission_note.text` matched the submitted synthetic
  value.
- The first patient/facility encounter LIST probe returned the encounter but
  omitted the extension. The generic `EncounterListSpec` was then wired to the
  existing native `ExtensionListRenderer`. The synthetic Doctor recheck returned
  `200`, found the target encounter, and read back
  `extensions.care_suriname_admission_note.text`.
- A later production-safety probe created the Encounter without a note, called
  `POST /api/v1/encounter/{id}/set-admission-note/` twice with identical text,
  and received `200` with equal responses both times. Detail and
  patient/facility LIST returned the extension, while active device and location
  associations remained unchanged.
- All probes reported exact synthetic-fixture cleanup as verified. No
  credentials, identifiers, tokens, or payload values were retained.
