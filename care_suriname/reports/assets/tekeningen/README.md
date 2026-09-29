# Drawing images (note "Tekeningen")

Provided by the owner on 29 September 2026 for use in CARE Suriname: the two
schematics (`blaas.jpg`, `urinewegen.jpg`, 1122×1402, re-encoded as JPEG) and
nine stamps cut from the owner's stamp sheet with a transparent background
(256×256 PNG). The frontend holds byte-identical copies in
`care_fe/src/Plugins/urology/forms/note-drawings/assets/`; `SHA256SUMS` is the
same file in both repositories and is checked by
`care_suriname/tests/test_note_drawings.py`.

A changed image is a new id in `note_drawings_catalog.py` (and the frontend
catalog), never an edit in place: finalized notes refer to these ids.
