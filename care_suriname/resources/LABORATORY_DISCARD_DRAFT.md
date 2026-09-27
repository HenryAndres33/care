# Laboratory `discard_draft` command

Owner decision, 27 September 2026: lab results entered from inside a note are
kept as a concept (draft report) and reach CARE as a result only when the note
is made final ("Definitief maken"). Discarding the note, or deleting the lab
line from it, must cancel the concept.

`POST /api/care_suriname/laboratory/report-commands/` accepts
`action: "discard_draft"` (`specs.DiscardDraftCommand`, same command context
and `expected_version` rules as `finalize`). Only a `preliminary` report with a
`draft` service request can be discarded. The report, its service request and
its observations are marked `entered_in_error`; nothing is deleted. The
command is recorded and replay-safe like every other command. Discarded
reports are left out of the report list; a detail read still returns them
with status `entered_in_error` and no visible rows.

No model, migration or native file. Rollback: remove the spec, the dispatch
branch and `_discard_draft`; already-discarded drafts stay entered-in-error.
Tests: `care_suriname/tests/test_laboratory_commands.py`.
