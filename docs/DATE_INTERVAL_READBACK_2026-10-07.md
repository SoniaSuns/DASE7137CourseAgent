# Date validation and readback queue repair

Run cd180d7ad9e6 completed three verified Class A changes before stopping on
Interactive Presentation (4254926). Fresh readback again matched all three
approved results. The fourth activity matched its complete original observed
fingerprint: opening 2026-11-08 18:59 and due 2026-11-15 18:59. The selected patch
would set both to 2026-11-02 18:59, an invalid zero-duration assignment window.

[Moodle assignment validation](https://raw.githubusercontent.com/moodle/moodle/MOODLE_405_STABLE/mod/assign/mod_form.php)
requires due strictly after opening; cutoff cannot precede due/opening, and grading
reminder cannot precede due/opening. The local deterministic validator mirrors
these platform constraints without choosing replacement dates. Date selection,
syllabus interpretation and resolving conflicting owner preferences remain planner
responsibilities. Checks occur before approval delivery and in the whole-manifest
preflight before any save. Approval and mismatch messages show complete dates.

The original failure discarded the server validation error during follow-up
readback, leaving only "Day, Day". Future rejected-form evidence is retained,
including validation text hidden in collapsed fieldsets. No old save was repeated
to recover the lost error message.

WhatsApp intake remained healthy, but NEEDS_READBACK skipped queued mentioned
requests silently. It now acknowledges a queued request once and performs read-only
reconciliation. Exact original observed-state match releases the task lock with an
explicit "changes not present" result, not an assertion that no save ever happened.
Mixed values or read errors remain locked. Approved-manifest and write-marker
integrity are checked first. Successful receipts are preserved and the remaining
goal is queued for fresh planning/approval behind already received requests.

The owner-requested interrupted queued message may be restored only by exact ID
from this restart's archived inbox; approval commands and unmentioned requests
cannot use this recovery path. Both old task state and completed receipts are
archived before another task occupies the chat state.

Validation: 133 focused suite tests passed plus the added browser integration test
for a hidden server validation rejection, failed readback, original-state recovery
and no second save. Other cases cover equal/reversed dates, disabled dates,
cutoff/grading dependencies, full-date review, mixed readback, manifest tampering,
one-time waiting notices, remaining-goal preservation and exact queued recovery.

Owner clarification: failed date changes return to inspection and planning, using
the original observed gaps between related settings. The planner receives signed
local-calendar minute offsets, enabled states and complete dates, chooses a
syllabus-grounded anchor, and preserves the original intervals and time of day.
Disabled dates stay disabled. Explicit fixed-date conflicts still require scope
clarification; runtime does not invent dates or a fixed seven-day interval.

Date preflight conflicts now queue fresh planning with the remaining scope and
completed receipts. Possible saves trigger immediate read-only reconciliation;
they are never replayed. Operator recovery of an unapproved planning question
preserves prior receipts and requires a fresh plan approval. Regression validation:
141 focused tests passed, including variable original intervals, disabled dates,
planning-question recovery and rejection of already-offered approvals.
