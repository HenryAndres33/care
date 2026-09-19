# Notes to native laboratory results (17 September 2026)

Owner-approved additive extension. FormSubmission command transactions register
explicit, opt-in `Labuitslagen:` rows on manual save or finalization.
Ordinary prose is never mined for results. Autosave (`register_note_labs=false`)
preserves input only. New clients send `note_lab_contract=v3`; the server still
accepts v1/v2 for saved legacy and transitional notes. Old servers reject that unknown command field,
preventing a false success with note-only persistence.
The optional marker is omitted from legacy command hashes when absent.

Clinical storage remains native ServiceRequest → DiagnosticReport → Observation.
The new FormSubmissionLabLink holds only provenance and a fingerprint, unique by
note series and test slot. The existing command row lock, expected version and
idempotency key protect atomicity and repeated saves. Both native service-request
and diagnostic-report write permissions are required. No privilege is added.

Content starts with initial/current total PSA (LOINC 2857-1, UCUM ug/L) and total
testosterone (LOINC 14913-8, UCUM nmol/L; https://loinc.org/14913-8/).
Unknown/not measured values create no observation. Numeric rows require a declared
measurement date (v3 also permits an explicit unknown date); source is optional
and defaults to manual-note provenance. No reference ranges or interpretation are
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

## Compact group date — 17 September 2026

V3 notes use a natural `Labuitslagen:` heading, one group-level
`Afnamedatum: JJJJ-MM-DD`, and consecutive
supported value/unit rows. The parser stops at the first non-lab line, so no
technical start or end marker is visible. V1 end-delimited rows and v2 compact
rows with the technical start marker remain supported for already saved notes
and immutable link verification. Natural per-row-date rows remain supported for
the existing PSA SmartText.

`Afnamedatum: onbekend` is stored truthfully: ServiceRequest occurrence and
Observation effective_datetime remain null, while native provenance states
`Afnamedatum onbekend.`. Existing dossier projection renders its unknown-date
label; graph code omits the undated point. No current date is substituted. Deploy
the backend before or with the v3 frontend; older servers reject the v3 command
field atomically.

## Plugin ownership — 19 September 2026

Commands, parser, registration and this contract live in
`care_suriname/resources/form_submission/`; the three note-lab test modules live
in `care_suriname/tests/`. No native compatibility shim remains. The existing
native FormSubmission viewset imports this registration service inside its
serialized create/update/finalize/amend commands: it is the already documented
write-time safety exception, not a new routing or permission mechanism.

Legacy descriptive `Labuitslagen: ...` headings before a technical block remain
readable. A malformed supported row (for example `CRP:7.4 mg/L`) rejects the whole
command instead of silently registering the preceding rows. Ordinary following
prose still ends compact blocks. Regression tests cover both cases.

PDF and correspondence paths use stored note text and the same finalized
snapshot hash; they do not parse laboratory rows independently. URLs, command
names, idempotency hashes, native identifiers and permission checks are unchanged.
No task, model, schema or content-type changes are part of this extraction.
