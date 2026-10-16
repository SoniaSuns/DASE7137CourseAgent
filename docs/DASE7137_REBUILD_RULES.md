# DASE7137 Rebuild And Refresh Rules

Detailed source-first, date-policy, and old-course rebuild rules archived from `AGENTS.md`. Use this when working on DASE7137 refresh/rebuild tasks, old-course backup migration, or stale-date cleanup.

## Safety Rules

- Keep implementation changes minimal: make the smallest scoped edit that
  satisfies the current request, and avoid new generators, broad refactors, or
  extra framework changes unless they are explicitly required or approved.
- File refreshes must be source-first. When a DOCX/PPTX/XLSX or other editable
  original exists for a student/staff-facing artifact, edit that corresponding
  original first, then regenerate the clean PDF preview and upload-ready copy.
  Treat `upload_ready` files as derived artifacts, not semantic editing sources.
  Patch a PDF directly only when the PDF is the authoritative original, no
  editable original exists after explicit search, or staff/agent approval
  explicitly authorizes a PDF-only patch; record that basis in the refresh
  report or approval evidence.
- Embedded screenshot/form dates are governed project-wide by the
  example-after-instruction strategy. If an old visible date appears only inside
  a non-authoritative illustrative screenshot, form mockup, file-browser view, or
  UI example, do not edit the screenshot pixels solely to scrub that date.
  Instead, edit the corresponding source document and add an explicit
  current-semester example immediately after the relevant instruction, using
  agent/staff/date-policy grounded dates. Record the screenshot evidence, the
  added example text, and the date basis in the audit or approval evidence. This
  exception is not allowed when the screenshot itself is the authoritative course
  deadline, release, event, Moodle setting, or only student-facing instruction;
  those cases remain blocked until refreshed or staff-reviewed.
- Activity creation and Moodle-state difference backfill must be planned by an
  independent Codex/planner decision artifact before any upload/activity
  manifest is executed. The artifact must consume current Moodle readback,
  source/backup activity inventory, proposed safe baseline operations, and
  missing-versus-existing diff evidence; it must return planning-only decisions
  such as create hidden activity/URL/group scaffold, upload missing clean file,
  skip existing item, or block for staff review. Local scripts may validate and
  execute approved decisions, but must not invent activity scope, file backfill
  scope, visibility, or skip/replace decisions when the decision artifact is
  absent or incomplete.
- For DASE7137 backup-to-sandbox stale-date cleanup/rebuild work, use
  `course_automation_probe/run_dase7137_stale_cleanup_rebuild.py` as the
  canonical project workflow instead of ad hoc manifests or previous
  upload-ready outputs. Hard boundaries: target sandbox course `105330`; source
  backup identity `128134 / DASE7137_1A_2025`; current semester policy
  `2026-2027 first semester`; no old IMSE7137/old7137 source paths and no prior
  RUN upload-ready files as semantic sources.
- DASE7137 stale-cleanup rebuild execution order is: obtain or consume validated
  Codex/planner date-policy output first; prefer Office/source-first refresh;
  materialize refreshed artifacts; run visual/date audit; allow only
  `PASS_NO_STALE_VISIBLE_DATE` visible File resources into upload manifests;
  obtain or consume validated Codex/planner activity-creation and diff-backfill
  policy output; include only policy-approved missing File resources and hidden
  activity/URL/group-scaffold decisions in final manifests; keep activity shells
  and URL items hidden; require exact action-time approval and readback
  verification for every Moodle write.
- DASE7137 1A visible File resource upload scope is intentionally incomplete
  until more source files are confirmed. The runtime agent/staff decision
  artifact
  `course_files/evidence/process/dase7137_visual_correspondence_20260709_1a/confirmed_correspondence_decision.json`
  controls which file-level correspondences may enter refresh/upload scope.
  Each selected file must use its matched Office correspondence original as the
  edit source. Do not use title-only matches, PDF fallback, backup-only Office
  files, or correspondence ids absent from that runtime decision for visible
  File resource upload.
- Even when File resources are incomplete, every DASE7137 backup activity type
  represented in the rebuild model must have focused tests covering its route:
  visible File resource, hidden folder/activity shell, or hidden URL item.
- After any file is refreshed, add a separate review note in the report or
  approval evidence confirming: the change is reasonable for the course context;
  separate child files/resources have settings aligned with the approved/current
  syllabus or timeline; and any release/restriction time is checked for actual
  student-availability behavior. Do not put this review note into the
  student/staff-facing file itself.
- Do not rely on `Hidden from students` for scheduled release. A hidden Moodle
  item will not become student-available merely because a date restriction is
  reached; scheduled release needs an approved visibility/restriction-time plan
  and post-change readback verification. Preferred scheduled-release setup is
  `Show on course page` plus a date restriction with `hide until available`
  behavior (`showc: false`), so students cannot see the file before release.
- Never modify the legacy live DASE7137/IMSE7137 Moodle course.
- Default all new Moodle course File resources to hidden unless visibility is explicitly approved.
- Every Moodle state-changing action needs action-time approval.
- Use browser writes only for explicit sandbox-supported operations such as File
  resource upload/replace/delete fallback, or when the user explicitly requests
  browser automation. Deleting a File resource is supported only for one exact
  sandbox target with current-run WhatsApp approval and readback verification.
- Do not leak `DASE7137_KEY`, Moodle tokens, browser cookies, or approval secrets in logs/final answers.
- Operation/result JSON may record metadata such as cmid and URLs, but must not record base64 file content.
