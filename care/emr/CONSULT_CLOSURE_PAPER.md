# Paper correspondence at consultation closure

Outpatient correspondence leaves CARE on paper during the pilot. The native
consult-closure outcome `paper_prepared` means that the latest correspondence
compilation for the finalized encounter source has a reviewed recipient, a
finalized immutable letter revision, and an available verified PDF artifact.
It stores the exact compilation as closure evidence and deliberately stores no
delivery id or delivery event.

The server derives this evidence under the same transaction as the existing
preflight. A missing or stale compilation, review, final revision, or PDF keeps
the encounter open with `correspondence_incomplete`. Existing
`delivery_acknowledged`, correction, and no-correspondence rules are unchanged.
Closing and exact retries remain idempotent.

Evidence lookup follows the physician-selected finalized note and searches for
its finalized letter artifact. A newer draft or unreviewed compilation cannot
obscure that artifact. Multiple finalized note series do not cause a false
`form_not_finalized` blocker when the selected source is itself finalized and
passes the required-slug and artifact-integrity checks.

Outpatient consultations without an appointment use
`care.standard.unscheduled-consult-close`. That policy requires null appointment
and queue-token identities and persists booking/token states as `not_required`.
Booked consultations continue to require exact booked queue evidence, and
emergency consultations retain their separate emergency policy.

Rollback is code-only: stop presenting `paper_prepared` and remove its additive
resource/viewset branch. Previously stored closure rows remain truthful because
their compilation stays immutable and their delivery fields remain null.

## Booked consultations without a queue token (14 September 2026)

Suriname books consultations in the agenda but does not use wachtrijnummers
(CARE queue tokens); 20 of 21 live bookings had none, and closure was blocked
with `token_missing`. Under `care.standard.consult-close` the appointment
evidence (booking in `in_consultation`, exact modified timestamp) stays
required and the booking is fulfilled on close. Queue-token evidence is now
optional: absent entirely, or complete and exact when a token exists. The
stored closure records `token_status = not_required` in that case
(migration 0105 widens the check constraint). Emergency and unscheduled
policies are unchanged. Rollback: revert the guarded block in the preflight,
the spec validator and migration 0105 together.

## Explicit not-required with an unused compilation — 14 September 2026

A compilation is an immutable source snapshot and may be created before the
clinician decides that no GP correspondence is required. For
`correspondence_outcome = not_required`, an otherwise valid closure may therefore
retain unreviewed compilations for the selected finalized form series. CARE still
fails closed when that series has any active recipient review, letter revision,
delivery, source correction, correction outbox, or correction case. No record is
deleted, archived, or mutated to obtain closure.
