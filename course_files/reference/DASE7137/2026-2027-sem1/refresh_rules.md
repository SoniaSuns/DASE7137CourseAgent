# DASE7137 Refresh Rules

Scope: single-file refresh previews for DASE7137, 2026-2027 first semester.

## Inputs

- Refresh exactly the file attached or explicitly referenced in the current staff prompt.
- Use this folder as the default reference pack:
  - `timeline.json` for week-level course schedule facts.
  - `calendar.json` for university semester periods, holiday/non-teaching days, teaching suspensions, and date checks.
  - `syllabus_rules.json` for hard policy guardrails.
  - `semester_schedule.json` for week-level class meeting dates. When staff has not supplied a new arrangement, rebuild derives this file from the rebuilt legacy timeline and marks it as a fallback in provenance.
- Ignore unrelated files outside this reference pack unless the staff prompt explicitly names them.

## Source Priority

When sources disagree, apply this order:

1. Current staff prompt instructions.
2. Current attached file content and structure.
3. `timeline.json` for lecture, workshop, submission, and week-date facts.
4. `semester_schedule.json` for lab, lecture, tutorial, visit, and session-facing class dates.
5. `calendar.json` for semester, holiday/non-teaching days, teaching suspensions, reading week, revision period, and assessment period facts.
6. Existing wording in the file when no reference source gives a replacement fact.

## Edit Boundary

- Preserve the file's original purpose, audience, section order, and assessment meaning.
- Make the smallest edits needed to refresh stale course dates or schedule facts.
- Do not change marks, grading weights, deliverables, staff names, policies, or technical requirements unless the current prompt or reference pack supports the change.
- Do not apply broad year or semester text replacements without checking the surrounding meaning.

## Date Handling

- Treat visible dates, week labels, semester labels, and schedule rows as refresh candidates.
- Ground date changes in `timeline.json`, `semester_schedule.json`, or `calendar.json`.
- Keep two week coordinates: use `teaching_week` for date/content mapping. For each visible Week column, the Date Planner must choose `calendar_week` or `teaching_week` from that table's numbering semantics; do not assume one display convention globally.
- Lab, lecture, tutorial, visit, and other session-facing dates must use target-week `teaching_dates` from `semester_schedule.json` and must not land on `calendar.json` entries where classes are suspended.
- Assignment/submission/deadline dates should preserve the old teaching-week number, weekday, and time when that source context is known; do not move deadlines solely because the mapped same-weekday date is a holiday or non-teaching day.
- Preserve dates that are citations, bibliography entries, software/version references, screenshot content, or historical examples.
- If a date appears course-facing but cannot be mapped to a reference fact, mark the result `NEEDS_REVIEW`.

## Output Status

- `READY_FOR_REVIEW`: all changed facts are grounded and no unresolved course-facing date remains.
- `NEEDS_REVIEW`: a refreshed file was produced, but at least one date, policy, layout, or meaning needs human checking.
- `BLOCKED`: the file cannot be safely refreshed from the available attachment and reference pack.

## Block Conditions

- The attached file is missing or cannot be opened.
- The file is PDF-only and the staff prompt did not authorize a PDF-copy refresh.
- The requested change depends on information not present in this reference pack.
- The requested change would rewrite course policy or assessment meaning without explicit support.
