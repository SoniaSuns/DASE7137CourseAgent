# Codex WhatsApp Course Management Agent

This is the Codex-based runtime for sandbox Moodle course operations.

## Runtime Shape

1. Codex opens WhatsApp Web and listens only to the configured test chat.
2. Staff sends a natural-language course-management task.
3. Codex records a `course_task_intent` through `/agent/course-task-intent`.
4. Codex records a bounded `course_operation_manifest` through `/agent/course-operation-manifest`.
5. Codex sends one WhatsApp approval request with the current approval code.
6. After `OK <code>`, the local server executes the registered manifest:
   - APIM/API first when supported.
   - The capability resolver converts `capability` + `params` into stable
     executor lanes.
   - The app's Moodle browser tools for supported browser-backed capabilities
     such as exact File resource delete and section rename.
   - A generic `browser_moodle_task` Playwright DSL executor only as strict
     fallback when no stable capability/tool exists and the approval says so.
   - Codex Moodle browser task only for approved browser fallback operations
     not yet implemented as local app tools.
7. Codex or the local Moodle executor records/readbacks the final result.
8. The runtime returns to waiting for the next staff prompt when
   `CODEX_LISTENER_FOREVER=1`.

The first version is sandbox-only: course id `105330`.

WhatsApp and Moodle browser windows are scoped to the running agent, not to a
single approval. The agent should open or attach to them at startup, reuse them
for multiple prompts/approval cycles, and close them only when the agent stops.

## Planner / Tools / Verifiers

The runtime uses the AutoAgent-assisted design in
[`../docs/AUTOAGENT_COURSE_AGENT_DESIGN.md`](../docs/AUTOAGENT_COURSE_AGENT_DESIGN.md)
as a maintenance model only. It does not start AutoAgent, add an AutoAgent
runtime dependency, or give AutoAgent access to WhatsApp, Moodle, browser state,
cookies, `.env`, or API keys. The live app still uses the existing single
planner call plus the local approval server and executor adapters.

- `planner`: listens to the configured WhatsApp chat, interprets the staff task,
  records assumptions, and submits `course_task_intent` plus
  `course_operation_manifest`. It expresses Moodle writes as `capability` plus
  typed `params` first; `operation_kind` is only the resolved executor lane. It
  does not execute Moodle writes.
- `tools`: executes only the approved manifest through the project adapters:
  APIM/API first, stable Moodle browser tools for supported browser-backed
  capabilities, and generic browser fallback only when explicitly marked as a
  strict fallback.
- `verifiers`: checks sandbox course guard, approval boundary guard, Moodle
  readback/result evidence, and then reports PASS/BLOCKED/FAIL.

For maintainability, the planner prompt can be organized into these internal
sections without splitting the runtime into multiple model calls:

- `staff_prompt_intake`: newest staff prompt, command classification, and
  verified attachment metadata.
- `course_snapshot_reader`: current read-only sandbox course contents and
  recent verified file-resource context.
- `intent_profiler`: bounded `course_task_intent`, assumptions, and risk
  boundary.
- `capability_composer`: capability-first `course_operation_manifest` with
  typed params.
- `approval_narrator`: staff-facing approval text without internal ids, raw
  JSON, local paths, API names, or secrets.

The tool/verifier side keeps the same boundaries:

- `capability_resolver`: maps capability params to exact supported executor
  lanes and blocks ambiguous targets.
- `approval_gatekeeper`: accepts only the current-run WhatsApp `OK <code>` and
  rejects stale or unrelated replies.
- `executor_orchestrator`: runs approved operations through APIM or stable
  browser adapters and stops on blocked/failed operations.
- `schema_contract_verifier`, `sandbox_guard`, `approval_boundary_guard`, and
  `readback_verifier`: fail closed unless the manifest is valid, sandbox-only,
  inside the approved boundary, and readback confirms the result.

The runtime artifacts carry this structure explicitly:

```text
course_task_intent.pipeline_stage = planner
course_operation_manifest.pipeline_stage = tools
course_agent_result.pipeline_stage = verifiers
```

Supported planner capabilities are:

```text
create_announcement
create_forum_post
reply_forum_post
delete_forum_post
create_group
create_grouping
update_group_membership
create_group_signup_activity
create_assignment_activity
upload_file_resource
replace_file_resource
delete_file_resource
delete_modules_in_section
delete_module
rename_activity
rename_section
set_visibility
set_restriction_time
course_refresh_batch_upload
browser_moodle_task
```

## Workflow Model

The long-running app remains the real state machine, but design reviews and
tests should use this event vocabulary:

```text
on_staff_message
  -> on_attachment_stage and on_course_readback
  -> on_intent_profile
  -> on_manifest_compose
  -> on_manifest_resolve
  -> on_approval_render
  -> on_approval_wait
  -> on_execute_manifest
  -> on_readback_verify
  -> on_result_report
  -> on_lifecycle_next
```

`RESULT` means the event produced a valid output and the next event may run.
`ABORT` means the current staff prompt is blocked/failed and no Moodle write
should happen. `GOTO` is reserved for bounded internal repair before approval,
such as one schema-fix pass; after approval, changes must come as a new staff
prompt with a new approval code.

`course_refresh_batch_upload` is the capability for local source-first file
refresh plus approved Moodle batch upload. Its params describe either a
`refresh_job` such as `dase7137_stale_cleanup_rebuild` or an already validated
`prepared_upload_manifest_path`; the resolver turns that into the
`course_refresh_batch_upload` execution lane. The local preapproval phase
materializes refreshed files, runs the date/visual audit, builds the upload
manifest, and creates a clean PDF approval attachment. After `OK <code>`, the
executor uploads only that prepared manifest.

Planner/runner split: the planner owns semantic matching from staff language to
capability. The runner owns mechanical intake, verified attachment downloads,
missing-input collection after a selected capability, capability resolution,
execution, and verification. Do not add WhatsApp-intake code that maps natural
language phrases directly to a capability or executor lane.

WhatsApp intake must filter the agent's own outgoing messages before planning.
Default self-message filtering covers lifecycle notices, approval/result/status
reports, reference upload waiting/timeout messages, and standalone attachment
notices such as the legacy `File received.` response. This filtering is based
on known agent output/provenance and prevents self-trigger loops; it is not a
staff-prompt semantic router. A WhatsApp document bubble with no meaningful
caption and no active planner-selected collection window is consumed silently;
it should not produce a visible standalone-file response or a planner call.
Restart and attachment-collection boundaries are relative to the current run's
observed wait/collection start time and structured message provenance. Runtime
must not hardcode wall-clock examples or chat-specific timestamps into routing.

`refresh_file_preview` is the local-only lane for the smaller daily workflow:
staff sends one old file as a WhatsApp attachment, the runner loads the local
timeline/calendar/rules reference pack, creates a derived refreshed file,
exports a PDF review copy when possible, audits it, and sends the PDF back to
WhatsApp for staff checking. This does not consume an approval code and does
not write Moodle. Its source file must be proven by structure: a verified
`local_path` from a WhatsApp attachment sent after the current staff prompt.
Historical local evidence files, older review outputs, previous Moodle uploads,
Downloads, guessed paths, and older WhatsApp bubbles are not valid source files
for a new prompt. The only review-output exception is the active file-refresh
review session: after the runner sends a PDF review copy, staff may reply with
revision instructions and the planner may use the active refreshed artifact as
the next `refresh_file_preview` source. Clear staff-requested text edits must
not be encoded or executed as direct `params.replacements`, and the runner must
not apply fixed replacement pairs inferred from the reference pack. Runtime date
matching belongs to the Date Planner Agent and is executed only through its
grounded `date_edits`; without a grounded plan, the refresh lane should block
instead of guessing. Uploading the refreshed file is a separate Moodle
upload/replace capability; it may use the active reviewed
artifact as source only when staff asks for upload/replace and the Moodle target
is clear or uniquely resolved, and it still needs its own current-run approval.

`refresh_file_batch_preview` is the local-only multi-file review lane. It accepts
ordered verified attachments from the current WhatsApp prompt, safely expands
bounded ZIP packages, reuses the single-file refresh/Date Planner/audit worker,
and sends each generated PDF as a separate WhatsApp document. One unverified
top-level source blocks the batch before processing; supported per-file refresh
failures may produce a partial batch result.

`finalize_file_batch_preview` is selected only for an explicit request to confirm
the exact active batch/version and generate a combined PDF. Runtime requires all
available per-file previews to have been delivered, includes only current
`READY_FOR_REVIEW` items unless staff explicitly names an eligible subset,
preserves stable input order, and reports excluded items. This is not Moodle
approval and never consumes an approval code. The operator-only
`run_file_refresh_batch_preview.py` command may use explicit absolute local file
paths for local testing, but that authority is never exposed to WhatsApp.

Operator-only local example:

```bash
.venv/bin/python run_file_refresh_batch_preview.py \
  --input "/absolute/path/Course Syllabus.docx" \
  --input "/absolute/path/weekly-files.zip"

.venv/bin/python run_file_refresh_batch_preview.py \
  --finalize-batch "BATCH-LOCAL-..."
```

`rebuild_reference_pack` is the local-only lane for maintaining the reference
pack itself. Staff can send timeline/calendar/syllabus reference attachments;
the planner inspects their content and passes verified WhatsApp `local_path`
values as `params.reference_files` objects with planner-selected role labels.
The runner stages those sources and regenerates `timeline.json`,
target `calendar.json`, optional `source_calendar.json`, `syllabus_rules.json`,
and `source_manifest.json` from supported timeline tables and HKU semester
calendar documents, or from already structured reference JSON. The planner
uses `source_calendar_reference` for the old academic-year calendar and
`target_calendar_reference` for the new academic-year calendar. The former
resolves old dates to source semester, calendar week, teaching week, and
weekday; the latter generates the refreshed schedule. It must not infer input
roles from hardcoded filenames or document containers. The stable
`refresh_rules.md` is kept in the same pack and is not overwritten during a
normal reference rebuild. The WhatsApp result is plain text only; generated
reference JSON files stay in the local reference pack and are not sent back as
WhatsApp attachments. If parsing fails, the runner reports the missing input
instead of sending a partial `source_manifest.json` attachment.

For the old course, the Timeline is authoritative over the source calendar.
The source calendar supplies academic-year, semester, calendar-week, weekday,
holiday, and suspension context. If an explicit Timeline teaching row lands on
a source-calendar suspension, keep the Timeline row and record
`timeline_overrides_source_calendar` evidence. Rows explicitly marked Holiday
or Reading Week in the Timeline remain non-teaching.

Reference intake must not depend on one exact WhatsApp trigger sentence.
Capability selection belongs to the planner: if the staff asks for new-academic-
year Sem 1 file-date refresh preparation without a specific old file, the
planner should select `rebuild_reference_pack`. If staff sends a specific old
course file and asks to update dates/content, the planner should select
`refresh_file_preview`; missing reference JSON is a mechanical precondition, so
runtime can collect/build reference files and then automatically continue to the
PDF review copy for the same old file. If both reference sources and an old file
are present, run `rebuild_reference_pack` first and `refresh_file_preview`
second; both are local-only. Tests should cover paraphrases instead of only one
literal prompt. The same collection flow can also repair a planner-selected
refresh workflow when the default reference pack is mechanically incomplete,
such as missing top-level `timeline.json`, `calendar.json`,
`syllabus_rules.json`, `source_manifest.json`, or `refresh_rules.md`.

Generated reference JSON is persistent local state. Normal agent startup,
single-file refresh, upload, retry, and restart flows must reuse the existing
pack instead of deleting it. Clear generated reference JSON only when staff
explicitly asks for a reset or a rebuild-from-raw test, and keep backups for
that test cleanup.

Keep this per-file reference pack intentionally narrow. It should contain only
the current timeline/calendar/syllabus policy and explicit refresh rules needed
for the attached file. Do not treat arbitrary files under
`course_files/pre_refresh/current_reference`, `sources`, or `backups` as default
rules or semantic evidence; pass a specific `reference_pack_path` when a source
is relevant.

## Configure

```env
WHATSAPP_MODE=codex_agent_web
COURSE_AGENT_FREE_TASK_MODE=1
CODEX_LISTENER_FOREVER=1
COURSE_AGENT_BROWSER_LIFECYCLE=agent
COURSE_AGENT_WHATSAPP_CHAT_NAME=test approval
COURSE_AGENT_NAME=codex

CODEX_BIN=/Applications/ChatGPT.app/Contents/Resources/codex
CODEX_MODEL=gpt-5.5
CODEX_AGENT_TIMEOUT=900
CODEX_SANDBOX=danger-full-access
CODEX_DANGEROUSLY_BYPASS_APPROVALS_AND_SANDBOX=0

MOODLE_COURSE_ID=105330
MOODLE_COURSE_URL=https://migrationtest-moodle.hku.hk/course/view.php?id=105330
MOODLE_API_URL=https://apidev.hku.hk/dase7137agent/
```

Keep `DASE7137_KEY` in the local environment or untracked `.env` only.

## Run

```bash
cd /Users/fanxuanhao/Documents/暑期实习/course_agent_app
.venv/bin/python run_course_agent_app.py
```

In free-task mode this starts the agent loop: it waits for a prompt, completes
that prompt's approval/execution/verification cycle, then waits for the next
prompt without reopening WhatsApp or Moodle windows.

`run_course_agent_app.py` is the regular runtime entry: Playwright owns the
WhatsApp and Moodle browser windows, while Codex is called for the planner step
that produces `course_task_intent` and `course_operation_manifest`.

## WhatsApp Messages

The long-running app sends a short lifecycle notice when it starts and when it
stops:

```text
Course agent is running.
Send a Moodle request here when you are ready.
Course: 105330 sandbox
```

Free-task approval messages are intentionally short and user-facing:

```text
Moodle approval

I will create the file resource in course 105330 sandbox.
Task: upload this file to week1
Target: Course Syllabus
Section: Week 1.
File: Course Syllabus.pdf.
Visibility: Hidden.
I will use Moodle browser and then read Moodle back to check it.
Safety: Sandbox only; write only after OK.

Reply OK 123456 to approve.
Reply NO 123456 to reject it.
```

Verification/result messages use:

```text
Moodle result

Done. I checked Moodle after the change and the readback passed.
Task: upload this file to week1
Target: Course Syllabus
Change: 1 Moodle write verified.
I checked Moodle: Readback passed.
Course: 105330 sandbox
Evidence: <result artifact name, when available>
Run: <run id>
URL: <Moodle URL>
```

Visible WhatsApp messages must not expose `operation_kind`, `browser_steps`,
raw JSON/Python dicts, cmid values, API names, or local paths unless debugging
is explicitly requested.

## Stop

The user can stop the running agent from the configured WhatsApp chat with an
explicit lifecycle command:

```text
停止
退出
停止 agent
stop agent
shutdown agent
```

The agent replies that it stopped, then the app closes its WhatsApp/Moodle
browser contexts and terminates the local approval server. For local operator
recovery, `Ctrl-C` in the terminal remains available.

For dry-run task generation without launching Codex:

```bash
CODEX_AGENT_NO_RUN=1 ./run_demo_flow.sh --skip-install
```

For the old preconfigured approval smoke flow, set:

```env
COURSE_AGENT_FREE_TASK_MODE=0
```

## Safety Contract

- Only sandbox course `105330` is allowed.
- Natural-language uncertainty may be handled by recording assumptions.
- Operation kinds are execution lanes, not user intent categories. The user may
  phrase Moodle tasks freely; the planner should understand the prompt and
  generate ordered capability operations with typed params.
- The app provides current read-only Moodle course contents to the planner
  before planning. For bounded broad prompts such as deleting or hiding all
  items in a named section, Codex must use that readback to enumerate exact
  Moodle targets itself instead of asking staff to provide inventory.
- `rename_section` uses the app-owned section settings tool; it must not be
  planned as raw generated clicks. `delete_modules_in_section` resolves readback
  to exact cmid/title/type targets and uses the stable cmid delete tool.
  `delete_module` covers one exact readback-resolved module and resolves to the
  same stable browser delete executor lane.
- `browser_moodle_task` is strict fallback. It must include
  `requires_browser_fallback=true` or `fallback_allowed=true`, a concise
  fallback reason, `browser_goal`, compact approval fields, `browser_steps`, and
  `readback_expectations`. Supported steps are constrained primitives such as
  `goto`, `click`, `fill`, `select`, `check`, `press`, `assert_text`,
  `screenshot`, and `readback_course_contents`; generated code or arbitrary
  JavaScript is not an allowed execution format.
- Any bounded sandbox Moodle UI task that the browser can perform may be planned
  from a normal staff prompt through `browser_moodle_task` when no stable
  capability covers it. This does not allow unbounded cleanup, credentials,
  live-course writes, server/admin/plugin changes, grade/student-record changes,
  participant/student-data export, or arbitrary code.
- Delete is supported for exact sandbox Moodle targets when the approval request
  clearly states the bounded item/scope and the manifest includes exact
  cmid/title/course details.
- Making content student-visible, changing source files, or broadening write
  scope must be plain in the current WhatsApp approval boundary.
- Moodle writes still require WhatsApp approval and readback verification.
- The current-run WhatsApp `OK <code>` is the final Moodle write/delete approval
  for the bounded manifest; do not ask staff for reapproval or a second local
  approval phrase.
- Browser writes are used only through app-owned Moodle tools or strict
  fallback Moodle browser tasks when API/APIM and stable tools cannot perform
  the operation.
