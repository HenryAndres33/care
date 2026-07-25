# Controlled core patch: clinical correspondence PDF

**Owner:** CARE Suriname correspondence workflow  
**Status:** authorized clinical-documentation extension  
**Migration:** `emr.0097_correspondence_recipient_command`

## Purpose

The correspondence backend already finalizes an immutable letter revision and
stores its generated PDF as a CARE `ReportUpload`. The original renderer exposed
an audit table containing internal UUIDs and SHA-256 values as the visible
clinical document. That output is useful for technical diagnosis but is not a
safe, readable paper letter for a hybrid hospital record.

CARE has no plugin hook for correspondence compilation or its final artifact
renderer. This controlled backend change is therefore required at both
authoritative boundaries. It separates clinician-facing content from technical
audit provenance: revision immutability, hashes, authorization, recipient
binding, atomic storage and audit records remain unchanged.

## Touched backend files

- `care/emr/reports/correspondence_letter.py`
- `care/emr/reports/correspondence_letter_styles.py`
- `care/emr/reports/correspondence_compiler.py`
- `care/emr/correspondence/presentation.py`
- `care/emr/api/viewsets/correspondence.py`
- `care/emr/api/viewsets/correspondence_review.py`
- `care/emr/models/correspondence_review.py`
- `care/emr/resources/correspondence.py`
- `care/emr/resources/correspondence_review.py`
- `care/emr/migrations/0097_correspondence_recipient_command.py`
- `care/emr/tests/test_correspondence_compilation.py`
- `care/emr/tests/test_correspondence_letter.py`
- `care/emr/tests/test_correspondence_review.py`
- `docs/development/correspondence-letter-pdf-core-patch.md`

## Output contract

- A4 document using the approved modern-clinical layout: configurable
  specialty title, facility subtitle, restrained teal accent, two-column
  patient metadata and print-safe whitespace. For the current urology template
  the configured title is `POLIKLINIEK UROLOGIE`; a redundant document-kind
  label is deliberately omitted.
- Frozen recipient name, role, organization and postal address.
- Patient name, date of birth and configured patient number.
- Letter date and encounter presentation date in readable Dutch notation.
- The selected finalized note's `reasonForVisit` as the visible subject. The
  active encounter tag remains frozen as technical CARE provenance and is used
  only when the finalized form has no explicit reason.
- Exact finalized plain-text letter revision as the body. Recognized clinical
  headings are rendered in bold with their text on the following line and
  consistent whitespace; text remains escaped before it reaches HTML/PDF.
- Exact clinician-selected finalized clinical note without encoded form
  identities or an internal field dump. The newest note is only a UI default;
  the frozen `submissionId`, resource version and snapshot hash determine the
  document source. A repeated `Reden van verwijzing` or `Reden van presentatie`
  block is omitted because the same finalized note reason already appears near
  the start of the letter; all following clinical sections remain intact.
- Frozen author identity and professional details as signature.
- Minimal document references in the footer for traceability.
- Controlled correction copies retain a prominent replacement banner.

Full UUID and hash provenance remains in the correspondence models and audit
ledger. It is intentionally not printed as a large clinical-facing table.
`CorrespondenceCompilation.source_provenance` remains the authoritative frozen
audit snapshot.

## Handmatig geadresseerde ontvangers

De Surinaamse papieren route moet ook een artsnaam kunnen vastleggen die nog
niet in de faciliteitsdirectory staat. De endpoint
`POST /api/v1/correspondence_recipient/idempotent-manual/` maakt daarom een
patiënt- en faciliteitsgebonden `CorrespondenceRecipient` met kanaal `postal`.
De naam wordt door een aangemelde, bevoegde medewerker ingevoerd en wordt als
zodanig in de bronprovenance gemarkeerd; er wordt geen adres, registratie of
veilige e-mailverbinding verzonnen.

Elke nieuwe frontend-briefsessie gebruikt een nieuwe
`client_request_id`. Die sleutel identificeert één onafhankelijke
briefpoging/compilatie, zodat een eerdere immutable review voor ontvanger A een
nieuwe brief aan ontvanger B niet blokkeert. Alleen een exacte netwerkherhaling
met dezelfde sleutel hergebruikt de eerdere compilatie; zo blijft
outcome-unknown-herstel veilig. Exacte retries van reeds opgeslagen v1-opdrachten
blijven leesbaar; de v1-hash wordt nooit voor een nieuwe compilatie gebruikt.

`CorrespondenceRecipientCommand` bewaart de opdracht-ID, payloadhash, actor,
patiënt, faciliteit en uitkomst. Hierdoor geeft een identieke netwerkherhaling
dezelfde ontvanger terug en wordt hergebruik van de opdracht-ID met andere
gegevens geweigerd. De bestaande immutable review bindt daarna exact deze
ontvanger aan de brief. De dagelijkse frontend kan zo één knop **PDF opslaan**
tonen zonder de audit- en autorisatiestappen te omzeilen.

The specialty title is configured on the selected CARE `Template.options` as
`letterhead_title`. This keeps the renderer reusable for another specialty
while making the urology letter explicitly recognizable. Editing a template creates a new resource version;
already finalized PDFs are never rewritten.

## Upstream update review

When updating CARE, inspect whether upstream adds a correspondence artifact
renderer or a supported report-template hook. If it does, compare its frozen
author/recipient semantics, correction-copy behavior and ReportUpload linkage
before replacing this patch. Never move final PDF generation to the browser.

Review changes in:

1. `care/emr/api/viewsets/correspondence_letter.py`;
2. `care/emr/reports/correspondence_letter.py`;
3. `care/emr/reports/correspondence_compiler.py`;
4. `care/emr/models/correspondence_letter.py`;
5. `care/emr/models/correspondence_review.py`.
6. `care/emr/resources/correspondence.py` command-fingerprint semantics.

Controleer bij een upstream-update bovendien of er een native idempotente
opdracht voor vrije/postale ontvangers is toegevoegd. Migreer pas daarna en
behoud de bevroren ontvangersnapshot van bestaande brieven.

## Verification

```bash
docker compose exec backend ruff check \
  care/emr/api/viewsets/correspondence.py \
  care/emr/correspondence/presentation.py \
  care/emr/reports/correspondence_compiler.py \
  care/emr/reports/correspondence_letter.py \
  care/emr/reports/correspondence_letter_styles.py \
  care/emr/resources/correspondence.py \
  care/emr/tests/test_correspondence_compilation.py \
  care/emr/tests/test_correspondence_letter.py
docker compose exec backend python manage.py test \
  care.emr.tests.test_correspondence_compilation \
  care.emr.tests.test_correspondence_letter \
  care.emr.tests.test_correspondence_review --keepdb --parallel 1
```

Also finalize one non-production test letter through port 4000 and visually
inspect the downloaded PDF for page breaks, missing recipient data and exposed
technical metadata.

The design-B acceptance fixture was rendered with WeasyPrint as a real A4 PDF.
The one-page hematuria example verified that the recipient block, two-column
metadata, clinical body, signature and footer neither overlap nor clip. Longer
letters may naturally continue on subsequent pages; the signature block is kept
together.

## Rollback

Revert the compiler, renderer, command fingerprint, style constant and focused
tests together.
Reverse migration `emr.0097` only after confirming that no manual-recipient
command is needed for audit or retry. Existing compilations, review snapshots
and PDF artifacts are immutable and remain as generated; rollback only affects
newly compiled and finalized letters. Do not overwrite historical artifacts.
