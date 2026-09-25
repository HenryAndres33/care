# Clean-start server: setup inventory

Read-only inventory of the laptop database (`care-db-1`, 25 September 2026,
queries run with `default_transaction_read_only=on`). Purpose: start the
server with **zero patients** while keeping the setup the app depends on.
Code (frontend and backend) is not in this list: it reaches the server through
GitHub (`update.sh`, `ship-frontend.sh`).

Only one facility is real: **Academisch Ziekenhuis Paramaribo** (laptop
facility id `77d446f3-659d-40d4-a69a-bfc0f5b7a255`). "FACILITY WITH PATIENTS",
"SECONDARY FACILITY", their 21 teams and the three "Supplier …" organizations
are CARE demo fixtures and are not carried over.

## 1. Carry over (setup; exists only in the database unless noted)

| What | Laptop rows (AZP) | How it gets to the server |
|---|---:|---|
| Facility AZP, department org "Urologie AZP", geo orgs Suriname / Paramaribo | 1 + 1 + 2 | recreate; new facility id |
| Custom role **Uroloog** (Doctor + `can_generate_report_for_completed_encounter`, 68 permissions) | 1 | recreate (pilot-scope D2) |
| Custom role **Secretary** (13 permissions) | 1 | recreate |
| System roles and permissions | 10 roles | `sync_permissions_roles` (code) |
| Lab tests (Observation Definitions) | 31 | **export/import** — not in code |
| Lab panels (Activity Definitions) + category "Laboratorium" | 8 + 1 | **export/import** — not in code |
| Governed Hb mmol/L definition and reference rules | in the lab tests' `meta` | export/import (fingerprint is content-only) |
| PDF letter templates: *Correspondentiebrief Urologie*, *Poliklinische huisartsbrief Urologie* | 2 | **export/import** — not in code; critical for the GP PDF |
| Visit reasons (encounter tag configs) | 11 active | export/import |
| Forms: *Urologie Medisch Dossier*, *Vochtbalans*, *Urologie operatieverslag* | 3 | export/import (the first two are not in code) |
| Smart Text: dictionaries 34, lists 54, presets 2, templates 9 active | 99 | export/import (`copy_clinical_text_catalog` only copies within one database) |
| Dutch diagnosis-name translations (reviewed) | 71 | export/import |
| Healthcare service "Urologie AZP"; locations *Afdeling Urologie* (ward), *Polikliniek Urologie* (room) | 1 + 2 | recreate |
| Patient identifier configs: "Patient Name" (facility), "Patient Phone Number" (global) | 2 | recreate |
| Facility monetary config | 1 (empty default) | nothing: CARE creates it on first use |

Facility-scoped slugs carry the facility id (`f-<facility id>-…`); CARE
regenerates that prefix when a definition is created for the new facility, so
the import must create through CARE, not copy rows.

## 2. Do not carry over

- **Every patient and everything attached**: 43 patients, 73 encounters, notes,
  lab reports and results, letters and their delivery ledgers, appointments and
  tokens, files (MinIO), draft-recovery keys (the server has its own).
- **All 24 user accounts**: every one is a test, demo or simulation account
  (annand, sarwan, james-castillion, rafa, qa-*, sim-*, *-demo, care-*). The
  test password in `care_fe/CODING_RULES.md` is local-only. Colleagues get
  their own accounts.
- **Practitioner schedules** (7): they belong to the test accounts; recreate
  per real colleague.
- **Test items inside the real facility**:
  - visit reason "DEMO-SIM — Observatie niersteen" (already archived);
  - archived Smart Text templates `.dossier`, `.politest` ("Politest prefill")
    and `.turb` (left out automatically: only active items travel);
  - form "Urologie Medisch Dossier (test)".
- CARE demo forms: Death Form, Medicine, Doctors Notes NCG, Ongoing Medication,
  Feedback Form (NCG), Respiratory Support, Respiratory Status, Diagnosis.

## 3. Owner decisions before the setup script

1. **Colleague accounts**: name, username and role (Uroloog or Secretary) per
   person.
2. **Paper-record number**: the real facility has no MRN / dossier-number
   identifier (only name and phone; the demo facility has one). In a hybrid
   paper workflow the paper file number is how a PDF is matched to the paper
   record. Add one before real patients are registered?
3. ~~Smart Text test material~~: resolved — `.politest` is one of the three
   archived templates, which are never exported.
4. After the new facility exists, `CLINICAL_WORKFLOW_MUTATIONS_ENABLED_FACILITIES`
   in the server `.env` must name its **new** id (see `README.md`, step 3).

## 4. Scripts and rehearsal

`export_facility_setup` and `import_facility_setup` exist; see
[`care_suriname/resources/facility_setup/README.md`](../care_suriname/resources/facility_setup/README.md).
Rehearsed 25 September 2026 into a fresh throwaway database: every count above
arrived, 0 patients, templates and Smart Text byte-identical.

Still to do before colleagues use it:

1. Browser check on a clean-start facility: login, a note, a lab entry and both
   PDFs.
2. Server: backup, clear, migrate, import, set
   `CLINICAL_WORKFLOW_MUTATIONS_ENABLED_FACILITIES`, create colleague accounts.
