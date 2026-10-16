# Project Context For Codex

## V2 shared-Chrome entry (owner requested 2026-10-05)

- Owner update (2026-10-15): proceed with independently confirmed items without letting uncertain items block the batch. One current WhatsApp OK remains required for the confirmed manifest. After completing the confirmed work, close as PARTIAL when unresolved items remain; list each named item, uncertainty and needed source/decision with blank lines between entries, and invite a new task for each. Do not automatically loop through unresolved items or repeatedly ask for clarification in the same task. Preserve technical-failure and uncertain-save/readback handling; partial completion is not whole-goal success.

- Owner update (2026-10-13), change management: `v2` is a git repository. Commit every change with a message that explains the incident or goal; never copy source into `reports/` as a backup. Deploy only with `deploy_v2.ps1` (clean committed HEAD, full `test_*.py` suite, idle listener); the old `deploy_*.ps1` scripts are archived in `deploy/history/` and must not be revived or copied. Put per-run report notes in `reports/trace_eval_20261009/failure_annotations.json`, not in code. Check `status_v2.ps1 -Metrics` (write-task first-run success, runs per task) when judging whether a fix helped. The directory is owned by another Windows account, so run git as `git -c safe.directory=C:/Users/Sonia/DASE_Agent/v2 ...`.
- Owner update (2026-10-13): approvals have no time limit (`approval_timeout_seconds: null`). Preflight re-reads Moodle before every save, so a late OK cannot write against a stale snapshot. A listener restart still invalidates a pending approval.
- The runtime must not import legacy modules. WhatsApp page helpers live in `course_agent_app/whatsapp_web.py`; import `ChromeApproval` from `chrome_approval`, not `approval_server`.
- Owner authorization (2026-10-12): every Blocked task is automatically diagnosed through Codex using exactly `gpt-6-astra` with reasoning effort `high`. Confirmed workflow defects may be repaired automatically in v2 through isolated regression reproduction, passing checks, independent patch review and an idle deployment boundary. Return genuine user source/scope conflicts as specific questions in the original chat. Infrastructure and uncertain diagnoses are technical outcomes, not presumed user errors. Never bypass approval/mention/scope/readback guards or replay a possible save. See `course_agent_app/auto_block_recovery.py`.

- Owner date-recovery default (2026-10-07): after a failure, return to inspection and planning. When shifting Moodle dates, preserve the original observed intervals between related settings and the existing time of day/enable/visibility states, using the grounded syllabus milestone as the anchor. Never collapse an existing multi-day submission window to a single instant or invent a fixed gap. Resolve anchors and conflicts through the planner; ask only when evidence or explicit constraints conflict. Preserve completed receipts, never replay an uncertain save, and obtain current-plan approval for corrected writes.

- Latest owner update: follow `../course_agent_v1` WhatsApp response rules. Both `DASE7137 - 2026` and `test approval` are groups: new tasks require a real mention of the current account. Read the actual profile name for structured mention-label matching, retaining exact account-ID checks where available. Same-requester replies to an active question/scope/approval may omit the mention, but must bind to the current run and question boundary; exact current OK/NO remains required for writes. An unknown chat type must never default to direct intake. Action-required replies address the requester in the original chat. Ignore outgoing, ordinary unbound chat, quoted/plain-text @ strings and stray approval commands. This supersedes the earlier test-chat exception and the requirement to re-mention on every follow-up. All changes stay in v2.

- Owner authorization (2026-10-06): the configured Codex planning service may receive the `files` library directory information and the relevant file contents selected for each task. `file_library_planner_access=true` records this standing permission; do not ask again for routine task-scoped reads. Do not send all file contents by default. Moodle writes still require their current-request approval.

- All new work belongs in this `v2` directory. Preserve `../origin_course_agent` and `../course_agent_v1`.
- Owner update (2026-10-06): default authorized source library is `C:\Users\Sonia\DASE_Agent\files`, configured in `course_agent_app/chrome_operator_config.json`. Refresh and read `course_files/library_index.json` at each request; use typed `inspect_files` for relevant contents before asking for uploads/missing sources. Preserve originals, select exact versions, verify content hashes, and retain current-chat Moodle approval. Generated indexes/artifacts belong under v2. Library access does not imply that the new runtime already implements document editing or Moodle file upload.
- Start this version with `start_v2.ps1`; `-Check` performs read-only startup checks.
- `course_agent_app/run_shared_chrome_agent.py` is the new small runtime. It reuses the original WhatsApp browser I/O, the Codex command builder, and `approval_server.ChromeApproval` as its durable approval authority. Do not route this entry through the legacy sandbox/API execution pipeline below.
- Owner update (2026-10-06): default to the explicitly requested dedicated Chrome profile at `v2/.chrome_agent_profile`, using a local debugging port. The owner signs in once; reuse this profile for both WhatsApp and Moodle. `chrome_operator_config.json` selects `browser_mode=dedicated`; `existing` remains available only when explicitly selected. Never copy cookies or close the operator's original browser.
- Current Moodle profiles are Class A `140147` and Class B `140148` at `https://moodle.hku.hk/`. These explicit profiles override historical sandbox-only defaults for this entry.
- Monitor `DASE7137 - 2026` and `test approval`; bind every task, scope choice and approval to its originating chat/requester. Only fresh incoming messages are accepted.
- Browser inventory and actual editable forms ground the planner. Typed capabilities are `update_moodle_form`, `update_group_membership`, and `update_grouping_membership`. Extend typed tools for unsupported controls; never add staff-phrase routing or arbitrary generated browser code.
- Scope selection precedes a single current-plan WhatsApp OK. Never replay a possible save; reconcile by readback. Restart invalidates pending approvals.
- Read `README.md` for verified behavior and remaining limitations. Local fixture tests do not establish successful production Moodle writes.

This workspace is the summer internship course-operations automation project.

## Goal

Build a safe Codex-based Moodle course-operations workflow:

1. Accept free-form staff prompts in the configured WhatsApp Web test chat.
2. Plan Moodle changes with Codex using planner + tools + verifiers.
3. Request current-run WhatsApp approval before every Moodle write.
4. Execute through APIM/API where supported; use stable Playwright tools for sandbox browser fallback.
5. Read back Moodle state and report a clear result to WhatsApp.

## Runtime Shape

- Regular app entry: `course_agent_app/run_course_agent_app.py`.
- The course agent is long-running. One startup may process multiple staff prompts and approval cycles.
- WhatsApp and Moodle browser windows belong to the agent lifecycle, not a single approval. Reuse them until the process stops or recovery restart is required.
- Intended runtime defaults: `COURSE_AGENT_FREE_TASK_MODE=1`, `CODEX_LISTENER_FOREVER=1`, `COURSE_AGENT_BROWSER_LIFECYCLE=agent`.
- Playwright owns WhatsApp Web window lifecycle and message I/O. Codex is invoked for reasoning/planning artifacts (`course_task_intent`, `course_operation_manifest`).
- Each staff prompt is a separate bounded intent. Reusing browser windows must not reuse approval across prompts.
- Staff can stop the running app from the configured WhatsApp chat with `停止`, `退出`, `停止 agent`, `stop agent`, or `shutdown agent`. Terminal `Ctrl-C` is the local fallback.

## AutoAgent-Assisted Design Boundary

- `docs/AUTOAGENT_COURSE_AGENT_DESIGN.md` is a lightweight design reference for role split, workflow states, tests, and failure recovery.
- Do not add AutoAgent as a runtime dependency for the Moodle/WhatsApp agent.
- Do not let AutoAgent open WhatsApp Web, Moodle, browser profiles, cookies, local secret files, or execute manifests.
- Keep the runtime shape as one bounded planner call that may be internally organized into intake, snapshot, intent, capability composition, and approval narration sections.
- Use the AutoAgent-style workflow vocabulary (`RESULT`, `ABORT`, `GOTO`) for documentation and tests only; the existing `approval_server.py` state machine remains authoritative.

## Moodle Sandbox

- Target sandbox course only: `https://migrationtest-moodle.hku.hk/course/view.php?id=105330`.
- Sandbox course id: `105330`.
- APIM endpoint: `https://apidev.hku.hk/dase7137agent/`.
- Subscription header: `DASE7137-Agent-Subscription-Key`.
- Local key env var: `DASE7137_KEY`.
- Local secret file: `course_agent_app/.env`.
- Never print API keys, Moodle tokens, browser cookies, or approval secrets in logs, docs, or messages.
- Exception: when the repository owner explicitly requests a migration bundle for a confirmed private GitHub repository, `course_agent_app/.env` may be included in that dedicated bundle. Browser cookies/profiles remain excluded. If repository visibility changes or the bundle is exposed, rotate all included credentials immediately.

## Current API And Tool State

APIM currently supports readback plus several write routes:

- `core_course_get_contents`
- forum/groups/groupings/choices read calls
- `mod_forum_add_discussion`
- `core_group_create_groups`
- `core_group_delete_groups`
- `core_group_create_groupings`
- `core_group_delete_groupings`
- grouping assign/unassign calls
- `core_files_upload` for Moodle file API/draft area upload

`reply_forum_post` uses the stable `browser_forum_post_reply` lane. It opens
the exact sandbox discussion, resolves one exact Reply control, submits once,
and reads back exactly one new matching post. An API-native alternative remains
optional until APIM exposes `mod_forum_get_forum_discussions`,
`mod_forum_get_discussion_posts`, and `mod_forum_add_discussion_post`.

Moodle course File-resource create/update through API is implemented locally in `moodle_plugins/local_courseops_api` as `local_courseops_api_upload_file_resource`, but still depends on Moodle-side install/whitelist. Until then, course File resource upload uses the sandbox browser tool.

Current stable browser-backed course File-resource lanes:

- Upload: `browser_course_file_resource_upload`, via `course_agent_app/run_moodle_course_file_resource_upload.py`.
- Delete: `browser_course_file_resource_delete`, via `course_agent_app/run_moodle_course_file_resource_delete.py`.
- Forum reply: `browser_forum_post_reply`, via the app-owned exact discussion/post reply tool.
- Section rename: `browser_section_rename`, via the app-owned section rename tool.
- Module delete/visibility: use exact cmid/title/type tools and readback verification.

## Capability Planning

Do not treat `ALLOWED_OPERATION_KINDS` as the user's action list. They are executor lanes, not a fixed intent taxonomy.

Staff prompts stay natural-language/free-form. Codex should produce capability composition first, then the resolver maps capabilities to stable executor lanes.

Semantic interpretation and capability selection belong to the planner. Runtime code must not decide that a staff phrase "means" a specific capability by matching natural-language wording. Runtime may do mechanical intake work such as filtering agent messages, downloading attachments, validating verified local paths, collecting missing inputs after a planner-selected capability, resolving capability params to executor lanes, and enforcing safety checks.

When a workflow needs extra inputs, the planner should first select the intended capability with the information it has. Runtime may then request/collect the missing verified inputs and re-plan with those inputs. Do not bypass the planner by mapping prompt text directly to `operation_kind` or capability inside WhatsApp intake code.

## Intelligent Workflow Design Rule

- When an implementation area appears to need semantic judgment, intent classification, fuzzy matching, ambiguity resolution, content transformation, date/schedule reasoning, file-source selection, approval wording, or recovery decisions, design an explicit agent workflow for that intelligence instead of hardcoding special cases.
- The workflow should define bounded inputs, planner responsibilities, structured outputs or schemas, resolver/executor lanes, verification/readback expectations, and tests with representative, paraphrased or mixed-language, and negative cases.
- Runtime code may perform deterministic mechanics and safety checks, such as provenance filtering, attachment download, schema validation, exact ID resolution, sandbox enforcement, and approval-code enforcement. It must not smuggle semantic decisions into phrase lists, regex branches, magic filenames, fixed timestamps, or one-off examples.
- If a new intelligent behavior cannot be expressed by existing capabilities, add or extend a typed capability and resolver path before wiring it into WhatsApp intake or Moodle execution.

Current typed capabilities:

- `create_announcement`
- `create_forum_post`
- `reply_forum_post`
- `delete_forum_post`
- `create_group`
- `inspect_group_structure`
- `create_grouping`
- `update_grouping_membership`
- `update_group_membership`
- `delete_group`
- `delete_grouping`
- `create_group_signup_activity`
- `create_assignment_activity`
- `upload_file_resource`
- `replace_file_resource`
- `delete_file_resource`
- `delete_modules_in_section`
- `delete_module`
- `rename_activity`
- `rename_section`
- `set_visibility`
- `set_restriction_time`
- `refresh_file_preview`
- `refresh_file_batch_preview`
- `finalize_file_batch_preview`
- `rebuild_reference_pack`
- `course_refresh_batch_upload`
- `browser_moodle_task`

Group structure operations use sanitized APIM Group/Grouping lists plus one
stable, read-only browser detail pass for member counts, Grouping relationships,
and Choicegroup dependencies/settings. Exact-name resolution, current-run
approval, action-time snapshot drift checks, and post-write detailed readback
are mandatory. Candidate Group membership identities discovered during name
search must never be returned or stored; only the staff-supplied current-run
query may be carried in the bounded manifest.

`update_group_membership` supports `add`, `remove`, and `move` with exactly one
staff-supplied participant reference: a positive numeric Moodle `user_id` or a
non-empty `user_name` query. Source/target Group names must resolve uniquely
from detailed readback. Name search is transient in the stable browser lane and
must resolve by normalized case-insensitive containment to exactly one numeric
option value in every relevant Group page before any write; zero, multiple, or
cross-page inconsistent matches block the operation. Candidate display text
must not be returned or persisted. The lane must persist only sanitized count
deltas/results and must roll back a newly added target membership if the source
removal step of a move fails.

Capability rules:

- Always provide current read-only Moodle course contents to the planner before planning.
- For bounded broad prompts like "delete everything in General section", use readback to enumerate exact cmid/title/module targets; do not ask staff to manually provide inventory.
- If staff asks to delete a named module/activity without giving a section, infer the section from readback only when the match is unique and bounded. If the same target appears in multiple sections, block with a clear reason or request a narrower approval boundary.
- For "everything in a section", target modules/items inside the section, not the section object itself, unless staff explicitly asks to delete the section.
- `rename_section` must use the app-owned section rename tool, not raw planner browser steps.
- `delete_modules_in_section` must resolve readback targets to exact cmid/title/type operations and use stable cmid delete tooling.
- `delete_module` is for one exact readback-resolved module target and must resolve to the stable `browser_module_delete` executor lane.
- `browser_moodle_task` is strict fallback only. It is allowed only when no stable capability/tool can express the task, the target is sandbox `105330`, approval visibly says browser fallback, the browser-step DSL is constrained, and readback expectations are explicit.
- Never let Codex invent arbitrary JavaScript, generated code, non-sandbox navigation, or unknown Moodle write operations.

## WhatsApp Prompt Input

- A WhatsApp document attachment plus its caption is part of the staff prompt.
- The app should download attached files into local evidence/staging and expose verified `local_path` values to the planner.
- When the planner selects a capability that operates on a current attachment, the file source must resolve from the verified `local_path` values attached to the current staff prompt.
- File-source operations must use only WhatsApp attachments sent after the current staff prompt, except for the active file-refresh review artifact during the review loop or its immediately requested upload/replace step. Historical local evidence files, older review outputs, previous Moodle uploads, Downloads, guessed paths, and older WhatsApp bubbles must not satisfy a new prompt.
- `refresh_file_batch_preview` accepts ordered verified current-prompt attachment paths, including ZIP packages, and returns each generated review PDF separately. Every top-level source must pass provenance before the batch starts; ZIP members inherit only the verified parent attachment provenance.
- `finalize_file_batch_preview` is a separate local-only action selected only after explicit semantic confirmation of the exact active batch/version. It merges delivered `READY_FOR_REVIEW` items in stable input order, reports excluded items, sends one combined PDF, and never consumes a Moodle approval code.
- The trusted local `run_file_refresh_batch_preview.py` CLI may accept explicit absolute regular-file paths for operator-only testing. This exception does not make arbitrary local paths available to the WhatsApp planner, does not send WhatsApp messages, and does not write Moodle.
- If an attachment cannot be downloaded to a verified local path, block with a human-readable reason instead of inventing a path.
- If the planner omits a resource name for an attached upload, default to the attachment filename stem.

## No Hardcoded Staff Phrases

- Do not implement workflow routing by matching one exact staff message such as a single WhatsApp phrase.
- Staff prompts are free-form and may be English, Chinese, mixed-language, abbreviated, or reordered. Runtime intake should stay mechanical where possible, such as filtering agent messages and tracking verified attachments.
- Runtime must ignore outgoing/self-generated agent messages before planning, including lifecycle notices, approval/result/status reports, reference upload waiting/timeout messages, and any legacy standalone attachment notices such as `File received.`. This is default self-message filtering based on known agent output/provenance, not semantic routing of staff prompts.
- Restart and attachment-collection boundaries must be computed from the current run's observed wait/collection start time plus structured message provenance. Do not hardcode wall-clock examples, chat-specific timestamps, or fixed date phrases into runtime routing.
- A WhatsApp document with no meaningful caption and no active planner-selected collection window is not a staff prompt. Consume/baseline it silently instead of sending a visible `File received.` response or planning from it.
- Runtime may open input-collection windows only after a planner-selected capability needs missing attachments or other verified local inputs.
- Do source validation through structured provenance, not phrase lists. For `refresh_file_preview`, `refresh_file_batch_preview`, upload, and replace workflows, the source must be a verified current WhatsApp attachment from the current prompt boundary or the matching active file-refresh review artifact; do not use older local review copies/drafts or previous Moodle uploads as the source for a new prompt.
- Missing reference-pack files are a mechanical precondition, not a semantic trigger. If the planner selects a refresh workflow while the default reference pack is incomplete, runtime may collect reference attachments and re-plan.
- The generated reference pack is persistent local state for file refresh. Do not delete `timeline.json`, `calendar.json`, `syllabus_rules.json`, `source_manifest.json`, or `refresh_rules.md` during normal agent startup, single-file refresh, upload, or retry flows. Clear generated reference JSON only for an explicit staff-requested reset/rebuild-from-raw test, and keep backups when doing so.
- Old-file date refresh and reference-pack rebuild are separate intents. If staff sends a specific old course file and asks to update dates/content, the planner should select `refresh_file_preview`; if the reference pack is incomplete, runtime may collect/build references and then automatically continue to the PDF review copy for that old file. If staff asks to prepare/rebuild timeline/calendar/rules/reference JSON without a specific old file to revise, the planner should select `rebuild_reference_pack` only.
- A PDF review copy starts an active file-refresh review session. In that session, staff may send follow-up revision instructions without re-uploading the old file; planner may use the active review artifact as `refresh_file_preview.params.file_path`, but must not encode requested edits as direct replacements. Date matching belongs to the Date Planner Agent and is executed only through its grounded `date_edits`; the refresh lane must not execute explicit replacement pairs or fixed replacements inferred from the reference pack. Outside the active review session, historical review outputs are not valid source files.
- Uploading or replacing Moodle content from a reviewed file is a later, separate Moodle write step. It may use the active reviewed artifact as the file source only when the Moodle target is clear or uniquely resolved, and it still requires current-run WhatsApp approval.
- Tests for intake triggers should include at least one representative phrase, one paraphrase or mixed-language variant, and a negative case when the boundary is risky.
- Visible agent replies in WhatsApp should stay natural-language English unless a workflow explicitly requires another language.

## Approval And WhatsApp Messages

Every Moodle state-changing action needs action-time WhatsApp approval from the current run. The staff's `OK <approval-code>` is the Moodle write approval for the bounded operation(s) in that approval request. WhatsApp staff approval is the final authority for that bounded manifest; do not add a second reapproval gate or ask for a second execute confirmation.

Approval messages for free-form Moodle tasks should be readable at a glance and use this shape:

```text
Moodle approval
Task: <original staff prompt>
Plan: <plain-language action>
Target: <human-readable Moodle item/section>
Course: 105330 sandbox
Scope: <plain-language boundary, when useful>
Method: API | Moodle browser
Safety: <approval boundary/risk note>
Verify: Readback after action

Reply:
OK 123456
NO 123456
```

Result/verify messages should use `Status: Done|Blocked|Failed|Waiting`, then `Task`, `Target`, `Change`, `Verify`, optional `Reason`, `Course`, `Run`, optional `Evidence`, and `URL`.

Do not expose `operation_kind`, `browser_steps`, raw JSON/Python dicts, cmid values, local paths, API names, or secrets in visible WhatsApp approval/result messages unless staff explicitly asks for diagnostics.

File-change approvals with a review file should be sent as a WhatsApp document attachment with the approval text as the caption. The browser driver must send attachments end-to-end; do not ask the operator to paste captions or click send manually.

## Safety Rules

- Keep implementation changes minimal and scoped.
- Never modify the legacy live DASE7137/IMSE7137 Moodle course.
- Target Moodle writes only to sandbox course `105330` unless a future multi-course profile is explicitly implemented.
- Default new Moodle course File resources to visible unless hidden visibility is explicitly approved.
- When a stable sandbox browser read/write page redirects to Moodle login
  because the reusable session expired, recover with the configured local
  `.env`/Keychain credentials, return to the exact approved sandbox URL, and
  retry the interrupted read once. Fail closed if credentials, the login form,
  or the restored target cannot be verified. Never log or report credentials.
- Do not rely on `Hidden from students` for scheduled release. Scheduled release needs approved visibility/restriction-time setup and readback verification.
- Use browser writes only for explicit sandbox-supported operations such as File resource upload/replace/delete fallback, stable section/module tools, or explicitly approved strict fallback.
- Operation/result JSON may record metadata such as cmid and URLs, but must not record base64 file content.
- Student/staff-facing files and approval previews must not reveal automation/revision traces in visible text, filenames, or metadata.
- Source-file refresh work must be source-first: edit Office/source originals when available, regenerate clean PDF review/upload copies, and treat `upload_ready` outputs as derived artifacts.
- For DASE7137 stale-date cleanup, old-course backup migration, and source-first rebuild work, use `docs/DASE7137_REBUILD_RULES.md` plus the canonical project scripts instead of ad hoc manifests.

## Key Files

- `course_agent_app/run_course_agent_app.py`: regular long-running WhatsApp/Moodle app entry.
- `course_agent_app/approval_server.py`: orchestration/state-machine layer.
- `course_agent/`: Codex env/model helpers, contracts, capability registry, resolver, schemas.
- `whatsapp_approval/`: approval command/message formatting module.
- `moodle_operations/`: APIM execution and Moodle operation package helpers.
- `course_agent_app/run_moodle_course_file_resource_upload.py`: sandbox browser File-resource uploader.
- `course_agent_app/run_moodle_course_file_resource_delete.py`: exact sandbox File-resource delete tool.
- `course_automation_probe/run_dase7137_stale_cleanup_rebuild.py`: canonical DASE7137 stale-cleanup rebuild workflow.
- `moodle_api_smoke_test.py`: APIM smoke tests; loads `course_agent_app/.env`.
- `moodle_plugins/local_courseops_api/`: Moodle plugin source for course File resource upload.

## Reference Docs

- AutoAgent-assisted role/workflow/test design: `docs/AUTOAGENT_COURSE_AGENT_DESIGN.md`.
- Historical run/evidence notes: `docs/COURSE_AGENT_HISTORY.md`.
- DASE7137 rebuild/source-first/date-policy rules: `docs/DASE7137_REBUILD_RULES.md`.
- API capability/ITS request: `MOODLE_API_CAPABILITY_REQUEST.md`.

## Useful Commands

Start the long-running course agent app:

```bash
cd course_agent_app
CODEX_AGENT_NO_RUN=0 CODEX_MOODLE_AGENT_NO_RUN=0 CODEX_LISTENER_FOREVER=1 COURSE_AGENT_FREE_TASK_MODE=1 COURSE_AGENT_BROWSER_LIFECYCLE=agent WHATSAPP_MODE=codex_agent_web .venv/bin/python run_course_agent_app.py
```

Check runtime status:

```bash
curl -s http://localhost:8787/status | python -m json.tool
```

Run focused course-agent tests:

```bash
cd course_agent_app
PYTHONPATH=/Users/fanxuanhao/Documents/暑期实习 .venv/bin/python -m unittest test_codex_course_agent.py
```
