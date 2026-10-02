# Patient insurance (`care_suriname_insurance`)

Owner request and approval, 30 September 2026: record a patient's insurance
at registration, chosen from a list, with the insurance number; required; on
the note PDF.

## What it is

A native CARE **patient extension** registered by the plug
(`patient_insurance.py`, loaded in `apps.py`). CARE publishes its schema at
`GET /api/v1/extensions/`, so CARE's own registration form shows the fields
without any frontend code; the value is stored in `Patient.extensions`. No
model, table, migration or native file change.

Stored shape (schema version `1`):

```json
{"version": "1", "insurer": "SURVAM", "plan_survam": "PZS-basis", "policy_number": "12345"}
{"version": "1", "insurer": "Eigen rekening"}
{"version": "1", "insurer": "SZF", "plan_szf": "SZF", "policy_number": "1", "valid_until": "2027-03-31", "guarantor": "Werkgever"}
{"version": "1", "insurer": "Eigen rekening", "guarantor": "Werkgever"}
```

- `insurer`: one of the groups in `patient_insurance_catalog.py`
  (SZF, SURVAM, Eigen rekening).
- `plan_<insurer>`: the plan, only for the chosen insurer; each group's field
  appears in CARE's form only after that insurer is chosen (`if/then`).
  `x-ui.metadata.insurer` names the group so other readers (the urology edit
  screen) can build the list from the schema instead of copying it.
- `policy_number`: required for an insurer with plans, refused for
  Eigen rekening; 1–64 characters.
- `valid_until` (optional, "Geldig t/m"): insurance expiry, stored as
  `YYYY-MM-DD`, a real date; refused for Eigen rekening. CARE's own form
  renders `format: date` as a date picker that sends a full ISO datetime
  (local midnight as UTC); the backend stores its date in `TIME_ZONE`
  (America/Paramaribo). A datetime without a timezone is refused.
- `guarantor` (optional, "Garantsteller"): free text, 1–128 characters,
  allowed for every insurer including Eigen rekening.

## Rules

- The backend refuses an unknown insurer, a plan of another insurer, a missing
  plan or number, a number for Eigen rekening and unknown keys. Blank values
  are dropped and text is trimmed before validation and storage.
- `valid_until` and `guarantor` are for the printed patient sticker; they are
  not on the note PDF.
- An empty object is accepted and means "not recorded": CARE echoes `{}` for
  patients registered before this extension, and those must stay editable.
- Required at registration: the `version` field has a default, so CARE's form
  always sends this extension and enforces `insurer` (and, per insurer, plan
  and number). The urology edit screen requires it too. CARE has no
  create-only hook for extensions, so a direct API client that omits the key
  entirely is not forced to send it.
- The note PDF (`reports/form_submission_artifact.py`) prints a row
  "Verzekering", e.g. `PZS-basis 12345` (plan or insurer, then the policy number; owner 1 Oct 2026)
  (`insurance_display`). Patients without it show the older free text
  `core.urology_patient_profile_v1.insurance_summary` if present, otherwise
  "Niet geregistreerd". The urology edit screen removes that free text when a
  structured insurance is saved.

## Changing the list

Edit `patient_insurance_catalog.py` only. Adding a plan or a group is safe.
Never rename a `plan_field` or remove a plan that patients hold; stored values
would fail validation on their next edit. A different shape needs version `2`
and a reversible data migration.

## 2 October 2026 — sticker fields, still version 1

Owner approval (2 Oct 2026): `valid_until` and `guarantor` were added for the
patient sticker. Both are optional keys, so every stored version-1 value
(without them) stays valid; the schema keeps version `1` and there is no data
migration. Before this change `additionalProperties: false` refused these
keys, so no stored value can contain them in another shape.

Rollback of only these fields: remove the two properties, the `valid_until`
handling in `validate` and their tests, then restart the backend. Do this only
when no patient holds them (otherwise their next edit is refused); check with a
read-only query on `Patient.extensions` first, or keep the fields.

## Rollback

Remove the `import_module("care_suriname.extensions.patient_insurance")` line
in `apps.py` and the PDF row, then restart the backend. Stored values stay in
`Patient.extensions` untouched (CARE ignores unregistered keys) and return when
the extension is registered again.

Tests: `care_suriname/tests/test_patient_insurance.py`.
