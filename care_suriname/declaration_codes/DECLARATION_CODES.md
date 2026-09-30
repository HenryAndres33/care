# Declaration codes ("Declaratiecodes") — 30 September 2026

**What.** Three urology code lists for insurers and the AZP administration,
recorded per visit as **native CARE ChargeItems**, codes only:

| List (`key`) | Source | Used for plans |
|---|---|---|
| SZF (`szf`) | SZF Verrichtingenlijst BAZO & Regulieren, Urologie | SZF, SZF premium, SZF Bazo, SZF BZV |
| Assuria (`assuria`) | Assuria "CODES UROLOOG" sheet | AZPAS-basis, AZPAS-plus, AZPAS-Suprême |
| AZP (`azp-po`) | AZP Assortiment Vakgroep Urologie Survam (PO…) | every other plan and Eigen rekening |

The plan-to-list table is frontend content (`declaratiecodes`, next phase);
this plug holds the lists (`catalog.py`).

**Why.** Owner decisions, 30 Sep 2026: the uroloog (from the note, saved at
"Definitief maken") and the secretary (sidebar page "Declaratiecodes") record
what was done; which code applies follows the patient's insurance plan. **No
prices** (they change with time and insurer) and **no invoicing** in CARE.

**How.**

- `catalog.py` — the lists (content). A code is never reused; removing a line
  retires its definition.
- `definitions.py` — one native `ChargeItemDefinition` per code in a facility:
  slug `szf-217031`, title "217031 Cystoscopie", `derived_from_uri`
  `https://care-suriname.sr/declaratiecodes/szf/217031`, empty
  `price_components`, `can_edit_charge_item=False`. The native model has no
  code field; the frontend reads system + code from `derived_from_uri` and
  puts them on each ChargeItem's native `code` (a free Coding is accepted).
- `manage.py configure_declaration_codes --facility <external id> --user
  <superuser> [--apply]` — dry-run by default; idempotent (create / update /
  unchanged / retire); refuses when a hand-made definition already uses one
  of these slugs.
- Recording (frontend): native `POST /api/v1/facility/{id}/charge_item/` with
  title, `status: billable`, quantity, `unit_price_components: []`,
  `encounter`, `code`. The patient's default account is used automatically;
  nothing is ever invoiced unless someone creates an invoice.

- Correction: `POST /api/care_suriname/declaration-codes/charge-items/<id>/
  enter-in-error/` (`api/viewsets/declaration_codes.py`): only ChargeItems with
  a declaration code; allowed to whoever recorded it on the same day
  (Paramaribo time, needs `can_create_charge_item`) or to holders of native
  `can_cancel_charge_item`; uses CARE's own `handle_charge_item_cancel`, sets
  `entered_in_error`, nothing is deleted.
- Note PDF (`pdf.py`, called from `reports/form_submission_clinical_content.py`):
  a "Declaratiecodes" section lists the codes chosen in the note and recorded
  at "Definitief maken" (`clinicalActions.staged`, kind `declaration-code`,
  state `recorded`) - part of the finalized note, so a version's PDF never
  changes. Codes added on the visit later are not printed on it.

**Native CARE.** No native file, table or import touched. Permissions: the
Secretary role needs `can_read_charge_item` and `can_create_charge_item`
(database configuration, recorded in CODING_RULES §7 when applied).

**Tests.** `care_suriname/tests/test_declaration_codes.py`: list integrity,
slug validity, dry-run, apply, idempotency, retire, conflict refusal, the
native code-only ChargeItem contract on a visit, same-day correction rules,
and the PDF section.

**Rollback.** Retire the definitions (`status=retired`, e.g. by removing the
lists and re-running with `--apply`) and revert the commit. Recorded
ChargeItems stay as native history.
