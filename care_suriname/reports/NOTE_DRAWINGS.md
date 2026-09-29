# Note drawings ("Tekeningen") — 29 September 2026

**What.** A Medisch Dossier note ("Verslag") can hold up to six drawings: a
schematic (bladder opened, or urinary tract) with numbered stamps (tumor, CIS,
stolsel, litteken/resectie, biopsie, steen, cyste, stenose, stent), freehand
lines and a legend with a typed size and note per number. The frontend module
is `care_fe/src/Plugins/urology/forms/note-drawings/`.

**Why.** Owner request (29 Sep 2026, after a colleague's feedback): surgeons
document cystoscopy findings by drawing on a bladder schematic instead of
writing. The paper file and the GP get the PDF, so the drawing must print.

**Where it lives.** Only inside the note's own `FormSubmission.response_dump`:
`content.clinicalActions.drawings` (a list of
`{number, template, stamps[{kind,x,y,scale,rotation,mirrored,size,note}],
strokes[{color, points}]}` in the schematic's 1122×1402 pixel space). No
model, no migration, no FileUpload: the drawing is part of the immutable,
hash-covered finalized note and of every corrected version.

**Plug code.**

- `note_drawings_catalog.py` — template/stamp ids, Dutch labels, colours and
  limits; images in `assets/tekeningen/` (plug package data, inlined as data
  URIs; WeasyPrint fetches nothing). Same files and `SHA256SUMS` as the
  frontend's `note-drawings/assets/`. A changed image gets a new id.
- `note_drawings_validation.py` — exact structure, known ids, canvas bounds,
  limits (6 drawings, 30 stamps, 30 lines, 3000 point characters, 40/160
  text characters). Called by `form_commands/responses.py`
  `_note_drawings_validation_response` at `finalize` (stored draft) and
  `amend` (new version) in `form_commands/execution.py`: 422
  `note_drawings_invalid`. Drafts are not checked (like the bounded-JSON
  check), so a draft never loses typing; the frontend decoder has the same
  rules.
- `note_drawings.py` — `render_narrative_with_drawings`, used for the
  narrative in `form_submission_clinical_content.py`: each figure (picture
  plus caption "Afbeelding 1 – Cystoscopie" and a footnote legend, no table)
  takes the place of its own note line `- Afbeelding N: …` under the
  schematic's heading ("Cystoscopie:" / "URS:"); a drawing whose line was
  removed prints after the text. CSS in `form_submission_artifact.py`. A
  malformed list (never finalizable) prints "konden niet worden weergegeven"
  instead of a half drawing. The first test version's "- Tekening N (Blaas)"
  lines are recognised too.

- `note_drawings_stent.py` — on URS a `stent` stamp is drawn as a thin green
  line along its side's ureter (x < 561 = rechts), curled in pelvis and
  bladder, under the other stamps. Same points as the frontend's
  `drawingStentPaths.ts` (SHA-256 checked by tests on both sides).

Owner decisions, 29 Sep 2026 (after the first live test): the schematics are
named **Cystoscopie** and **URS**; the word "Tekeningen" does not appear on
the PDF; the legend is a footnote under the picture.

**Native CARE.** No native file, import or table touched.

**Tests.** `care_suriname/tests/test_note_drawings.py` (render, escaping, PDF
bytes, validation, asset manifest).

**Rollback.** Revert the commit. Notes finalized with drawings keep them in
their stored JSON (harmless); without this code their PDF simply omits the
drawings, and the frontend must be rolled back together (it would otherwise
offer drawings that no longer print).
