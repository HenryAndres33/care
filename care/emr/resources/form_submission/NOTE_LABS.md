# Notes to native laboratory results (17 September 2026)

Owner-approved additive extension. FormSubmission command transactions register
explicit, opt-in `Gekoppeld laboratorium:` rows on manual save or finalization.
Ordinary prose is never mined for results. Autosave (`register_note_labs=false`)
preserves input only. New clients send `note_lab_contract=v1`; old servers reject
that unknown command field, preventing a false success with note-only persistence.
The optional marker is omitted from legacy command hashes when absent.

Clinical storage remains native ServiceRequest → DiagnosticReport → Observation.
The new FormSubmissionLabLink holds only provenance and a fingerprint, unique by
note series and test slot. The existing command row lock, expected version and
idempotency key protect atomicity and repeated saves. Both native service-request
and diagnostic-report write permissions are required. No privilege is added.

Content starts with initial/current total PSA (LOINC 2857-1, UCUM ug/L) and total
testosterone (LOINC 14913-8, UCUM nmol/L; https://loinc.org/14913-8/).
Unknown/not measured creates no observation. Numeric rows require an explicit
measurement date; source is optional and defaults to manual-note provenance; no reference ranges or interpretation are
invented. Native effective_datetime uses midnight America/Paramaribo solely as a
date carrier, explicitly annotated “tijd onbekend”. The chart uses that date.

Confirmed rows cannot be removed/changed through narrative edits, including note
amendments. A separate native correction workflow and broader lab catalog remain
future work. Reusing a historical initial PSA across different notes is not yet
automatically reconciled; clinicians should not re-register existing history.

Apply migration 0106 before deploying the frontend. Rollback: disable the lab
insertion frontend and revert registration hooks/command marker only after
ensuring no pending notes need registration. Retain migration/link table and native
clinical results; never reverse the migration or delete results in a populated
system. Existing non-lab forms remain compatible.

## Optional source (17 September 2026)

The owner requested hiding source input/text in new PSA notes. Omitted `; bron:`
uses `Handmatig ingevoerd via medische notitie`. This describes entry provenance,
not a connected laboratory. An explicit source is preserved and must be nonempty
and resolved. The stored note/PDF contains no fabricated source; native request,
report and observation provenance carries the default. Existing source-bearing
fingerprints and immutable-row protections are unchanged. Deploy backend and
frontend parsers together before the source-free native SmartText update.
No migration or extra persistence model. On rollback keep optional-source reading
until all saved source-free notes can still be reopened/finalized; do not rewrite
clinical notes/results or substitute a fictitious laboratory.

## Multi-test catalog — 17 September 2026

Added explicit numeric variants: creatinine 14682-9 / umol/L, urea 22664-7 /
mmol/L, hemoglobin 718-7 / g/dL, CRP 1988-5 / mg/L, D-dimer FEU 48065-7 /
mg{FEU}/L, serum/plasma glucose 14749-6 / mmol/L, sodium 2951-2 / mmol/L,
potassium 2823-3 / mmol/L. Codes/specimens checked against official loinc.org
pages. See frontend note-labs/NOTE_ENTRY_DESIGN.md for links and exact text labels.
No unit conversion, eGFR calculation, reference intervals or clinical inference.
Deploy the additive frontend/backend catalogs together. Existing rows and links
remain unchanged. Once new rows exist, retain support on rollback so saved notes
remain readable/finalizable. No migration is required.
