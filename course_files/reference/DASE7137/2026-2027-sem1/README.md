# DASE7137 2026-2027 Sem1 Reference Pack

This folder is the narrow local reference pack for single-file refresh previews.
It is intentionally separate from `course_files/pre_refresh/current_reference`.

Use this pack only for DASE7137 2026-2027 first-semester file refresh work.
Do not treat unrelated historical current-reference files as default semantic
sources. If a one-off source is relevant, pass it explicitly as a
`reference_pack_path` or convert the confirmed facts into this folder first.

## Files

- `timeline.json`: confirmed 2026-2027 first-semester weekly schedule rows.
- `calendar.json`: HKU 2026-2027 semester calendar facts used for date checks.
- `syllabus_rules.json`: course-level policy rules that should guide refreshes.
- `refresh_rules.md`: human-readable refresh policy for the per-file lane.
- `source_manifest.json`: provenance map for this reference pack.
- `sources/`: local copies of the source/evidence files used to build the JSON
  references.

## Source Basis

- `sources/Timeline reference.docx`
- `sources/calendar 24-25.pdf`
- `sources/Dates of Semesters 2026-2027.pdf`

## Rebuild

When a new timeline or university calendar reference is supplied, rebuild this
pack through the agent's local `rebuild_reference_pack` capability instead of
editing JSON by hand. The capability stages uploaded reference files and
regenerates the JSON files from supported timeline DOCX tables and HKU semester
calendar PDFs, or from already structured reference JSON.
