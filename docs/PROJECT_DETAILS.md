# Course Agent v2

## Shared agent with execution in each user's Chrome

The separate `moodle_remote_extension` connects to an authenticated per-user
workspace on this computer, while Moodle reads/saves run in the requesting user's
own Chrome/account. It does not route remote tasks through the owner's browser.
Direct browser connection, isolated file uploads, task history, device-bound approval and
durable no-replay receipts are implemented. Build the credential-free package with
`prepare_remote_workspace.ps1`. The service listens on loopback port 18791 and the
explicitly configured trusted LAN endpoint `http://192.168.0.124:18791` (subnet
`192.168.0.0/24`). Run `enable_remote_lan.ps1` as administrator for the narrow
Windows firewall rule. Public Internet access still requires an HTTPS proxy.

Remote writes currently cover inspected ordinary Moodle forms. Activity movement,
group membership and Moodle resource upload/replacement have not yet been ported
to this executor. See [installation, invitations, HTTPS templates and exact scope](../docs/REMOTE_WORKSPACE.md).
The existing owner-only WhatsApp and local extension remain separate.

## Autonomous evidence analysis

The planner prefers the existing readers and Moodle tools. It can now delegate
an `explore` decision to a real Codex CLI worker when a reader is unavailable,
document layout needs code, or additional observed Moodle sources are needed.
The worker writes and executes Python through a private WASI MCP server, selects
alternative parsers and calculations, and returns source-cited findings. Standard
reader failures automatically enter this path; they are not proof of missing files.

The authenticated read broker discovers links on current course/module pages,
downloads observed course resources, and exposes participant/gradebook pagination.
It uses bounded GET requests without redirects, clicks or generated JavaScript.
Relative last-access ages are explicitly omitted from participant snapshots;
roster identities and memberships remain subject to freshness checks.
The worker receives selected file copies and sanitized HTML, not browser access,
credentials or arbitrary network access. Source hashes are rechecked before a
Plan and a save; restart/renewal requires fresh analysis. Findings cannot authorize
writes. Existing typed operations, one current Plan confirmation, preflight and
readback remain mandatory. Uncertain items retain the skip-and-report policy.

From `course_agent_app`, install dependencies and the pinned analysis runtime:

```powershell
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
..\.venv\Scripts\python.exe setup_exploration_runtime.py
..\.venv\Scripts\python.exe -m unittest test_autonomous_exploration test_exploration_wasi test_readonly_transport_privacy -q
```

`chrome_operator_config.json` enables `autonomous_exploration` with six analysis
calls per run and a 300-second worker timeout. Each call selects at most twelve
sources, 32 MiB each and 64 MiB total including derived copies. Artifacts stay in
`reports/exploration/`. The opt-in `verify_autonomous_exploration.py` uses real
Codex credentials and fictional data to verify actual Python MCP usage and
source citations; it performs no Moodle writes.

This is autonomous Python analysis, not an unrestricted host shell. WASI exposes
only the task workspace plus read-only Python runtime/packages, with bounded
memory, execution fuel and output. Native extensions, subprocesses, network and
OCR are unavailable. This CPython build lacks zlib: ZIP-based Office files get
lossless uncompressed analysis copies; compressed PDFs still need the standard
reader or another supported parser. Dynamic Moodle controls remain the job of
existing typed browser tools. Multi-user isolation, Teams/Graph synchronization
and arbitrary new write capabilities are separate work.

## Repeatable simple-command checks and capability boundaries

From `course_agent_app`, run:

```powershell
..\.venv\Scripts\python.exe run_simple_command_checks.py
```

The runner produces `reports/simple_command_checks/report.html` and `results.json`
with individual outcomes, elapsed time, source commit and working-tree status.
Failures, errors, unexpected successes, expected failures and skips prevent a
complete PASS. It composes existing immutable suites with new named regressions;
normal `test_*.py` discovery also includes the new cases. No runtime restart is
needed for these test/report additions. Chromium and the project dependencies
must already be installed in the virtual environment.

Coverage includes exact activity/section renaming, hiding, anchored movement,
date form edits, file replacement/upload, HTML/Office reading, participant and
grade identity, confirmation binding, stale state and readback without replay.
All browser requests in these fixtures are fulfilled locally. Command sentences
describe cases; typed operations are supplied explicitly, not inferred by a live
model. This is a unit/local-browser regression report, not proof of arbitrary
natural-language understanding, current production writes or complex syllabus
alignment. Historical task metrics also mix old defects and complex tasks; they
are not a measured success rate for simple commands.

Current reuse limits: `chrome_course_scope.py` fixes the two course profiles;
the Chrome bridge uses one local owner; execution shares a queue/browser; the
library snapshots a local folder. These are not a general multi-tenant service
or an implemented Teams/GitHub synchronization connector.

Further development areas (the analysis/download fallback above is now implemented):

1. Build a synthetic semantic acceptance corpus: paraphrases, ambiguous names,
   changed document layouts and competing syllabus versions, each with reviewed
   expected targets, source evidence, field changes and uncertainties. Measure
   interpretation separately from executor regression checks.
2. Give complex tasks item-level completion criteria and evidence references
   (file hash, page/sheet/row, course/activity ID). Verify placement, ordering,
   opening/due/cutoff dates and visibility against those criteria after execution.
   Preserve the current one-Plan approval and skip/report uncertain-item policy.
3. Extend structured reader outputs and semantic acceptance coverage beyond the
   current autonomous analysis/download fallback. Distinguish unavailable reader,
   incomplete pagination, denied access and absent source. Never treat technical
   failure as proof that the user failed to supply a file.
4. Move course/source configuration out of fixed profiles and isolate user
   identity, credentials, course permissions, browser sessions, history and
   approvals. Validate two users with overlapping file names and different access
   before claiming reusable multi-user capability.
5. Keep teaching documents in Teams/SharePoint and code, schemas, synthetic test
   fixtures and CI in GitHub. Add a read-only Graph connector with initial full
   enumeration, durable delta cursor, stable item IDs, content hashes, renames,
   deletion markers, retry/backoff and permission-loss handling. Publish a new
   source snapshot only after a complete successful pull; pin a task's versions
   and invalidate stale proposals. Syncing a source must not itself write Moodle.

Teams channel files use SharePoint storage ([Microsoft documentation](https://learn.microsoft.com/en-us/sharepoint/teams-connected-sites)).
Graph supports incremental file tracking using `nextLink` and `deltaLink`
([v1.0 delta API](https://learn.microsoft.com/en-us/graph/api/driveitem-delta?view=graph-rest-1.0)).
GitHub workflows can automate test jobs on repository events
([GitHub documentation](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflows)).
The proposed split uses each system for its relevant role; moving documents to
GitHub alone does not add parsing, synchronization or Moodle verification.

## Moodle participants and gradebook exploration

Each course overview now exposes read-only Participants and Grades targets.
Codex can inspect the current roster and groups, discover assessment identities,
and read every observed gradebook page for a selected assessment. Staff exports
are supplementary evidence, not prerequisites for reading available Moodle data.
Participant completeness requires the unfiltered course filter and matching count;
placeholder rows and edit controls are not treated as participants or group names.

Numeric grade edits reuse `update_moodle_form` for observed individual grade
records. The Plan identifies the assessment/student and score, including an
explicit gradebook override when required. Overrides take precedence over
activity-generated scores. Course, assessment, student and grade-record identity,
current form fingerprint, score range and lock state are checked before saving.
Feedback, exclusion, visibility and locks are preserved. A batch may contain
multiple student grades under one current approval. Save/readback and no-replay
behavior remain shared with the existing executor, including exact rediscovery
after restart. Scale grades and implicit grade clearing are not implemented.

The library reader supports HTML/HTM tables without executing scripts or loading
remote resources. Excel inspection includes formulas and explicitly labelled
saved calculation results, not a claim of fresh recalculation. Grade checks can
compare current values and group consistency without demanding unrelated penalty
or rounding decisions. A technical read failure must not be called a missing file.

This expands the existing Codex planner's available observations and operations;
it does not give generated code unrestricted browser writes. Live read-only
inspection and unsaved preparation are separate from verified live grade saves.

## Chrome chat side panel

Run `./prepare_chrome_chat.ps1` to build the locally paired extension at
`course_agent_app/reports/chrome_chat/extension`. In Chrome, open
`chrome://extensions`, enable Developer mode, choose **Load unpacked**, and select
that folder. Pin **Moodle Course Chat** and click its icon to open the chat sidebar.
Keep this machine-specific folder private: it contains the local connection token.

The sidebar accepts English or Chinese requests for the existing Class A/B agent.
Read the complete Plan and click **确认执行** once to authorize its exact changes;
**取消** rejects that Plan. `/tasks`, `/status`, and `/resume <task-id>` use the
existing task history, scoped to this conversation. Uncertain items retain the
existing skip-and-report policy; a new entry does not fix unresolved syllabus
evidence or add unsupported Moodle operations or file uploads.

`chrome_chat_enabled` enables the authenticated loopback bridge on port 18790.
The existing listener must be running with its dedicated Moodle/WhatsApp session;
initial startup still checks WhatsApp readiness. During listening, local intake
continues through WhatsApp polling failures. Both channels share one task queue,
planner and browser executor. A pending task in either channel can hold the other
in the queue. Replies and confirmations stay in their originating channel.

The extension requests only the side-panel permission and loopback host access;
it does not read browser cookies or arbitrary tabs. HTTP threads persist requests;
the listener thread validates current run, reply boundary and Plan identity before
using the existing approval authority. Request IDs deduplicate network retries.
Restarted approvals are invalidated and unconsumed local requests are not replayed.
Moodle preflight, exact manifest checks and post-save readback remain in force.
This extension is built locally, not published to the Chrome Web Store.

Sidebar implementation follows the official
[Chrome sidePanel API](https://developer.chrome.com/docs/extensions/reference/api/sidePanel)
and [extension network request documentation](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests).

WhatsApp approvals put the exact OK/NO reply commands first, followed by the
plan with course and target on one line per operation. They omit the repeated
request and routine implementation boilerplate, while retaining actual field,
date, placement and visibility changes. Status and partial reports also omit
the repeated request. Full task text and source citations remain in run state.
Multipart plans still require delivery of every part before approval is enabled.
Partial reports group planner/review/verification findings by observed course
and activity identity, showing one entry per file. Exact duplicate reasons are
removed after identity binding; distinct findings and qualifications remain.
Original findings remain in state; unknown identities stay separate. Planner
instructions request concise reasons without repeating alignment uncertainties
in the separate impact list or repeating per-item closure instructions.

## 2026-10-13 change management and approval time limit

- `v2` is now a git repository (`.gitignore` excludes `.env`, the Chrome
  profile, `.venv` and `course_agent_app/reports/`). Run git with
  `-c safe.directory=C:/Users/Sonia/DASE_Agent/v2` because the folder is owned
  by another Windows account.
- Approvals no longer expire. `approval_timeout_seconds` in
  `chrome_operator_config.json` is `null`; set a positive integer to restore a
  limit. Execution preflight still rejects any Moodle state that changed after
  the plan was approved. A restart still invalidates a pending approval, and an
  unanswered approval keeps other requests waiting until it gets OK or NO.
- `deploy_v2.ps1 [-Execute] [-WaitForIdle]` replaces the per-incident deploy
  scripts (archived in `deploy/history/`). It deploys a clean committed HEAD
  after the full test suite passes and the listener is idle, backs up runtime
  state, and the listener reports the commit as `status.json` `source_commit`.
- `status_v2.ps1 -Metrics` reports write-task first-run success, eventual
  completion and runs per task from retained run state.
- The runtime no longer loads legacy modules: WhatsApp helpers moved unchanged
  to `whatsapp_web.py`, and `course_agent` resolves names lazily.

WhatsApp intake recognizes repeated structured mentions of the current account.
An own-number link left immediately after a real mention by message editing is
removed from the command; other phone numbers and ordinary text are preserved.
Rich-text list metadata cannot replace the outer message body. During listening,
changed message bodies are tracked as revisions: the latest revision replaces
unconsumed queued content and identical scans do not enqueue again. Edits must
still satisfy the group mention/requester rules. Edited OK/NO messages never
authorize writes; send a fresh current approval. Startup baselines do not replay
chat history. Operator recovery can revalidate one exact missed edited body.

Blocked responses now enqueue durable automatic diagnosis using Codex
`gpt-6-astra` with `model_reasoning_effort="high"`; no model fallback is allowed.
`auto_block_recovery.py` distinguishes workflow defects, genuine user source or
scope questions, infrastructure failures and uncertain technical cases. Model
calls are read-only and output structured diagnoses/patches, with redacted task
traces and current source. The model cannot directly write Moodle or deploy code.

Workflow patches run in an isolated v2 source copy. A new regression must fail
by assertion on the old implementation, pass on the patch, and pass the unchanged
full suite. Existing tests and authorization/supervisor boundaries are protected.
An independent patch review and live source hash checks precede deployment.
Backups and rollback protect partial installs. The listener activates repairs
only with no active planner, approval, queued input or uncertain save, then
gracefully restarts and replans the same task with fresh approval. A same-failure
limit prevents an unlimited repair loop. User questions remain requester-bound
in the original chat. During diagnosis, progress is reported every three minutes.

Configuration: `course_agent_app/chrome_operator_config.json`,
`auto_block_recovery.enabled`. Fixed model/effort are recorded in every job.
Reports: `course_agent_app/reports/auto_recovery/<run>-<failure-hash>/` contains
the request, diagnosis, staged patch, before/after/full regression logs,
independent review, activation backups and final handling status.

Inventory refresh also discards forms of disappeared targets and reports their
unavailability to planning. Unknown section IDs may be resolved only from a
cited source Week cell and a unique currently observed same-course section.
Module IDs and existing conflicting destinations are never guessed or replaced.

An empty per-item syllabus citation inherits the observed target course's
single inspected source binding only when its exact row exists. Explicit
conflicting source IDs remain errors. This avoids asking the planner to repeat
an already determined ID across every item. Inheritance is traced before strict
validation and independent review; it never carries approval or changes a
destination. See `course_agent_app/reports/citation_inheritance_20261012/fix.txt`.

Before syllabus validation, source bindings can be normalized only when all
placement citations already agree on the sole inspected structured source.
Other inspected documents remain supplemental evidence. Competing structured
sources are never silently selected between. An explicit source-table `Week`
header resolves the column index only when its value agrees with the proposed
destination; rows and destinations are unchanged. Both normalizations are
traced. File-read requests are automatically processed in groups of six within
the existing overall inspection budget. See
`course_agent_app/reports/source_binding_recovery_20261012/fix.txt`.

Repeated `inspect_files` and form inspections reuse available evidence instead
of failing the task. Cached file reads validate source hashes; exact pagination
and the eight-round inspection budget remain enforced. Inspection continuations
retain the previous correction feedback. See
`course_agent_app/reports/inspection_recovery_20261012/fix.txt`.

## 2026-10-10 syllabus alignment and complete-goal verification

Schedule-based placement now carries an explicit course/source binding and one
disposition for every current activity: a cited destination, an explained
exclusion, or a tracked uncertainty. Word sources expose original table rows.
Code checks source hashes, exact row/column, destination week, course identity,
full inventory coverage, and each move's agreement with the mapping. A separate
bounded model review checks topic meaning and exclusions; it cannot approve or
execute writes. Legacy lecture/workshop ordinal labels do not override a
distinctive topic match. Approval messages include the source row and week.
Rejected future items are retained as unresolved. They cannot be written or
count as aligned, but they do not block an independent accepted batch. Section
prerequisites must have an accepted placement purpose and cannot affect rejected
descendants. If the current batch still needs clarification after replanning,
the task asks a scoped question instead of terminating with a generic Blocked.
Semantic review has two correction attempts independent of coordinate/schema
repairs. Review feedback first returns to planning, including cases where the
reviewer says an unresolved item already has adequate source evidence. After
that bounded correction, rejected writes and their transitive dependencies or
ordering anchors are deferred before offering the independent reviewed batch.
The full goal remains tracked. The real 21-operation incident is a regression
fixture: 20 independent moves survive, with all 59 goal items retained. See
`course_agent_app/reports/review_recovery_20261010/fix.txt` for diagnosis.

The original goal scope survives expired approvals and section-visibility
prerequisites. A completed batch stays interrupted until the entire mapped goal
is read back. Outstanding items trigger inspection and a fresh proposal, with
at most three automatic follow-ups before an explicit clarification. Previous
receipts are retained and uncertain saves are never replayed. Move approval
renewal must pass fresh planning/review. Source-row grounding currently requires
an inspected Word table; other schedule formats must remain unresolved until
supported evidence is supplied, rather than falling back to a history summary.

`test_schedule_alignment.py` covers the Class A wrong-week and lost-scope cases.
`evaluate_schedule_reviewer.py --run` explicitly calls the configured service
with the authorized Class A source and a three-item fixture inventory, without
connecting to Moodle or WhatsApp. Its results and the incident comparison are
under `course_agent_app/reports/syllabus_alignment_20261010/`. Model-review
traces use the existing trace store. Status now exposes alignment item counts,
whole-goal verification and unresolved findings separately from batch receipts.
The reviewer receives mechanically resolved before/after section titles and the
exact cited cells for each material, plus relevant text content. It does not
reinterpret numeric section IDs from raw edit-form controls. A live 59-item
replay covers the discovered current-section-versus-proposed-section confusion.
Attached filenames remain part of topic evidence. Legacy content dates are
reported separately from placement. The live replay retained ambiguous items;
it did not establish that all 59 original proposed placements were acceptable.

For an owner-requested correction of a settled current task, the operator-only
`--recheck-task-id` and `--recheck-instruction-file` options preserve its identity
and receipts, re-read syllabus evidence and require a new WhatsApp approval.
They cannot resume a possible save or accept an old approval.

## 2026-10-09 section visibility and active-task instructions

Same-task `/resume TASK-ID additional instructions` now sends the additional
instructions into planning even while a question or approval is pending; it
invalidates the previous offered approval and preserves the task identity.

`set_section_visibility` supports observed show/hide actions for an exact section.
Its approval lists every affected item. Section visibility is a separate first
stage because it changes descendant visibility options. After verified readback,
the runtime automatically inspects and plans the remaining original goal under
the same task ID, preserving completed receipts and the no-move-replay evidence.
New file edits still require approval. Pending course editor responses retain
their page and use readback without replay. Showing a section alone is never
reported as proof that every requested file is visible.

Course inventory enables the dedicated session's Edit mode UI when needed to
read the actual available section controls; this does not change course contents.

## 2026-10-09 destination visibility before moving

Course inventory now exposes each section's shown/hidden state. Move resolution
rejects an implicit move of a non-hidden activity into a hidden section and sends
the planner back to a visibility choice before offering a write. Execution reads
the destination again and rejects a changed visibility state before moving.
After the requester accepts hiding, `params.visibility_policy=inherit_hidden`
can propose that outcome; the approval summary explicitly says the activity will
be hidden from students. Default policy remains preservation. Section-wide
visibility changes are not silently included in a file move.

Readback compares the selected visibility option's meaning, not only its numeric
value. A destination may change the options (for example 1 can mean shown on the
page in one section and available only by link in another). Only contextual
visibility value/options may differ; all other settings and exact order must
match. Automatic preservation is permitted only when the original meaning has
one exact enabled counterpart in the new form.

A settled, already-applied move with visibility-only differences automatically
returns to planning, preserving its exact position and forbidding move replay.
If the original visibility meaning is unavailable, the planner asks which
available outcome to use before further writes. It does not invent a replacement
numeric value or unhide the whole week. Pending submissions and other setting
changes remain protected. Recovery retains the task identity, earlier receipts
and remaining scope and requires a current-plan approval for new changes.

## 2026-10-09 automatic recent-result context

Every planner call now receives `recent_task_results`, newest first, from the
same chat and requester. `recent_result_limit` in `chrome_operator_config.json`
defaults to 10 and accepts 5–10. Existing retained results work immediately.
`state/task_memory.json` persists a bounded result summary per task alongside
the existing ten callable task identities. Evicted tasks are not reimported
from audit files; the current task is excluded to avoid duplicating its own
continuation context. Fewer available results means fewer injected entries.

Summaries include request, answer, refinements, verified result counts, bounded
change descriptions, failure/pending state and source task/run identity. No
approval code, executable manifest, full historical form or file body is copied.
The injected history has a 24,000-character budget; lengthy older entries may be
omitted. Historical results are context, not current Moodle evidence or approval.
Recent results appear in the existing planner input trace for each run.

A natural-language message containing one task ID now supplies an explicit
reference for semantic planning. During a question/scope/unexecuted approval,
the same requester may use `resume T-...` or a natural reference to another
settled task as context for the current goal. The current identity is retained,
pending approval is invalidated, and fresh evidence/approval are still required.
An executing task, uncertain save, another requester, or unavailable task ID
cannot be overridden. `/resume` while idle retains its existing continuation
meaning; `/task` and `/tasks` remain read-only.

## 2026-10-09 background renderer and RPC classification

Run 155b878a91a5 verified five section renames before its sixth click timed out
with no pointer/click/submit events, a hidden unfocused document, no form POST,
and two generic AJAX requests. A local HTML probe in the actual dedicated Chrome
confirmed that background animation frames stopped, then resumed with CDP focus
emulation. The Moodle work page now retains its own focus-emulation session,
independent of WhatsApp's session, including while an original submit is pending.

Save observation records sanitized RPC method names and paths (no query strings
or request arguments). Only exact known course-state/template reads and editor
session resume calls are counted separately as background traffic. Unknown RPCs,
mixed batches, navigation POSTs and actual input events retain the pending-save
protection. This is not a blanket exemption for Moodle AJAX requests.

Operator retirement also supports the previous observation format that had a
zero-event probe but counted background RPCs as writes: the exact section-name
tab, immutable plan, saved zero-event evidence and still-live same-document
zero-event probe must all agree. New observation formats cannot use this special
recovery. Independent readback follows, preserving previous receipts and requiring
fresh approval for the remaining plan.

## 2026-10-09 save-dispatch recovery

Before saving, the dedicated Moodle tab is activated and the observed Save button
passes a Playwright trial click. A failed readiness check may re-read/re-prepare
once, before any actual click or save intent. The actual click records pointer,
click and submit events plus same-origin write requests. A timeout is classified
as `not_dispatched` only when the same-document probe survives with all event and
write counts zero. Missing probes, observed clicks or requests retain the pending
submit protection. Delayed JavaScript submission is never retried just because
POST count is temporarily zero. Sanitized click/readiness diagnostics are stored.

Proven non-dispatch returns to read-only reconciliation and fresh planning/approval
instead of reporting an endless pending response. Exact section forms now support
the same full original-state readback as module/group forms. This closes a second
gap that previously prevented a failed section rename from re-entering planning.

Legacy traces cannot prove absence of input events retroactively. The explicit
operator flag `--retire-unobserved-section-submit RUN` supports only the inspected
legacy zero-POST section-name case: authenticate the immutable manifest and exact
marked edit tab, verify its prepared field, retire that agent tab, independently
read the saved state, then re-plan with fresh approval. It never assumes the old
save failed, clicks Save, closes an unmarked user tab or handles observed POSTs.
This is an operator recovery action, not an automatic timeout fallback.

## 2026-10-09 blocked-task repair instructions

A mentioned follow-up from the same requester is now assessed while a task is
waiting for readback. For an interrupted activity move, the runtime first proves
that the response settled, the complete course layout matches the approved move,
and the only settings difference is the visibility value. It then preserves the
original task ID, verified receipts and request, archives the old manifest, and
submits the new instruction with current evidence for fresh planning/approval.
The already-applied move is replaced in the remaining scope by a settings-repair
proposal and cannot be proposed as another move. No uncertain operation is marked
verified merely to unblock the queue. The model interprets the follow-up; there
is no special routing for "invisible" or a particular language.

Pending submits, inconsistent manifests, changed content or unexpected placement
do not enter this bounded recovery path. Those cases still require further
readback/diagnosis. A failed assessment is recorded once per queued message and
runtime version rather than retried on every poll.

For newly approved moves, a confirmed move that changes only visibility may
restore the approved visibility once as part of that same move. This preservation
write requires the executing immutable manifest; it cannot run before approval,
repeat after failure or be invoked from readback recovery. Final layout and full
settings verification still apply. New user-requested settings changes continue
to require their fresh manifest approval.

## 2026-10-09 run traces and failure evaluation

Open `course_agent_app/reports/trace_eval_20261009/diagnostic-overview.html` for the
Chinese workflow map, current tool/status evidence, deduplicated failure catalog,
and the representative regression cases. Refresh with
`python course_agent_app/build_diagnostic_report.py` using the v2 virtual environment.
From the workspace, `./v2/evaluate_v2.ps1` runs the cases, compares the previous
results and refreshes the report. These are deterministic workflow/tool tests,
not new live-model or production-write evaluations.

After runtime `2026-10-09-trace-eval` is deployed, new calls produce local artifacts
in `reports/shared_chrome/state/traces/<run>/`: model prompt/schema, model decision
text and structured result, typed tool inputs/outputs, duration and exception
frames. Run `trace_report.py <run>` with the v2 Python to generate `replay.html`.
Replay only reads recorded files; it never re-executes a browser action. The first
recorded exception is an investigation starting point; later retries may recover.
Historical missing traces cannot be reconstructed. Internal hidden model reasoning,
individual DOM calls and full network traffic are not captured. Binary contents
are represented by hashes and sizes. Secrets and approval codes are redacted;
task-selected source text can be present in these local artifacts. Traces currently
have no automatic retention limit and are separate from the ten callable task codes.

Trace write failures do not interrupt tool execution; status exposes
`trace_write_error` and `planner_trace_write_error`. A failed disk write may leave
an incomplete trace. Current active questions/approvals are preserved while the
deployment helper waits for an idle boundary. Report snapshots explicitly show
the actually running version rather than assuming new code is already loaded.

## 2026-10-09 observability

From PowerShell run `./v2/status_v2.ps1` (or `./status_v2.ps1` inside v2).
`-Follow` refreshes every five seconds; `-Json` provides structured output.
This reader works immediately with older running listeners and explicitly marks
step timing as unavailable instead of inventing progress. It never opens Chrome,
sends a message, changes a task, or treats a stale heartbeat as a running service.

The instrumented listener also accepts `@agent /status` or `@agent 状态` in the
original chat. `/task T-...` includes current step information for a matching run.
Task details remain bound to their requester; status commands do not approve work.

Listener health and task progress are separate: the heartbeat reports WhatsApp
read freshness while task telemetry reports task/run identity, current step and
target, step duration, time since actual progress, inspected-form counts, planning
round/repair and input size, preflight/execution counts and verified receipts.
Planning with no progress for 300 seconds or another active step for 90 seconds
is marked `slow_progress`, not declared dead. Waiting for a question, scope,
approval, delivery verification or save readback is shown explicitly. Expired
approval is visible even before another reply changes the stored task phase.

`reports/shared_chrome/health.json` includes task telemetry, refreshed by the
existing heartbeat thread without accessing Playwright. `state/observability/events.jsonl`
contains bounded events with a single rotated backup (2 MiB each, approximately).
Only IDs, counters, stage names and error locations are logged: no message text,
approval codes, browser URLs, file bodies, cookies or raw exception messages.
An event-log write failure is visible but does not interrupt an in-flight save.
Planner metrics attach only when both run and planner-attempt IDs match.

`deploy_observability.ps1 -Execute -WaitForIdle` waits for current work and
deliveries to finish before switching the listener. It does not cancel an active
question or approval. The read-only status tool is available during that wait.

## 2026-10-09 continuation and draft-content repair

Resume commands also accept `continue taskT-...`, `continue task T-...`,
`Please continue T-...`, and `继续任务T-...`. A message containing a task
reference that cannot be parsed prompts for an unambiguous command instead of
creating an unrelated task. Unknown, evicted and cross-chat/requester codes
remain unavailable.

For a pure continuation of previously selected form edits or activity moves,
the runtime refreshes exactly the remaining operations and requests a new
approval without asking the planner to invent another scope. Verified completed
operations are omitted. Additional instructions still go through planning with
the saved task context. No historical approval is inherited.

Editor draft image/file addresses are now backed by SHA-256 content readback in
the connected Chrome session. Only the temporary draft item number is replaced
for comparison, and only with verified identical bytes. Host, file path, filename,
HTML and actual contents remain significant. Missing content evidence never
relaxes comparison. Move failures name the affected activity and changed fields.
Validation: `course_agent_app/reports/resume_content_20261009/`.

## 2026-10-09 task memory

Every task has a visible `T-<12 hexadecimal characters>` ID, separate from the
six-digit approval code. The ID stays the same when continuing or retrying that
task. In the originating WhatsApp chat, the original requester can mention the
agent and send:

- `/tasks` or `任务列表`: list callable task IDs.
- `/task T-...` or `查看任务 T-...`: show its saved status and progress.
- `/resume T-... [additional instructions]` or `继续任务 T-... [补充要求]`:
  load its original goal, refinements, completed receipts and remaining scope.

The agent retains at most **10 callable task IDs in total**, across both chats.
New tasks evict the oldest inactive ID; continuing a task does not consume another
slot or move its creation order. Active tasks and uncertain saves are protected.
Evicted IDs cannot be recalled, including through old run aliases. Old audit
files remain local for diagnosis and are not a fallback lookup mechanism.

Continuation re-reads current course inventory and known affected forms, then
plans remaining work with a new approval. Completed tasks can be checked or
extended with additional instructions. Old approvals are never inherited and
an uncertain save must first be reconciled. Ordinary unmentioned group messages
still do not start tasks. Memory is restricted to the originating chat/requester.

The bounded registry is `course_agent_app/reports/shared_chrome/state/task_memory.json`.
Existing run IDs are imported once and remain accepted as aliases while retained.

## 2026-10-08 capability implementation

The shared-Chrome entry now supports moving existing activities between sections
and reordering them within a section (`move_module`). Plans use observed section
database IDs and activity IDs, not section position or guessed week numbers.
Each move retains the existing activity. Batch validation derives the next layout
from earlier verified operations without changing the approved manifest.
Sections are inspectable forms, so their names can be changed with the ordinary
observed-field capability.

`upload_file_resource` publishes an inspected, hash-checked source from `files` as
a new resource in an observed section. `replace_file_resource` replaces the bytes
of a single-file resource while retaining its activity ID, filename and settings.
Both use the resource file picker only after approval and compare downloaded
content hashes after saving. Multi-file packages are explicitly unsupported.
Original library files are never edited by these capabilities.

Observed rich text uses native TinyMCE with validated formatting markup. The
availability editor can add/remove date, group and grouping conditions through
the native Moodle model, retaining unsupported plugin conditions unchanged.
These changes require the same current-request approval as other form edits.

Group operations follow the current work tab after recovery and wait for the
actual submission response before readback. Batch preflight/execution now polls
WhatsApp between operations; new work stays queued, and a stop preserves verified
receipts. A slow single browser call can still delay intake until that call ends.

Validation artifacts: `course_agent_app/reports/implementation_20261008/`.
`rehearse_extended_capabilities.py` opens a temporary live tab and exercises local
form preparation without Save or upload. Local fixture writes are not production
Moodle validation. Office/PDF rewriting, cross-course copying, deleting activities
or groups, and arbitrary availability plugins remain separate unimplemented work.

## 2026-10-07 reliability repair

Owner default: rescheduling preserves the observed original gaps between related
dates. The planner receives complete local-calendar interval evidence, chooses the
syllabus anchor and derives related dates with the same offsets. Disabled dates
stay disabled and hidden items stay hidden. An ambiguous anchor or conflict with
fixed/future-only constraints prompts a focused question. Failures return through
fresh readback and bounded replanning; corrected writes require a new approval.

Assignment date ranges are validated as complete dates before approval and before
any save in the selected manifest. Equal opening/due times are rejected with a
specific explanation; alternative dates are chosen by the planner/requester.
Server validation errors survive follow-up readback. A pending readback now
acknowledges mentioned requests instead of silently skipping them. Exact original
state readback preserves prior receipts, archives the interrupted task and queues
its remaining scope for new planning behind already received requests. Mixed
state remains locked. See `docs/DATE_INTERVAL_READBACK_2026-10-07.md`.

Follow-up repair: the executor now opens the exact field's Bootstrap 4/5 or HTML
details ancestors before interacting. The old generic implementation missed
Bootstrap collapsed fieldsets. `prepare()` verifies all filled values without
saving; `rehearse_moodle_forms.py <failed run ID>` exercises the selected real
forms under a course-write request guard and discards each temporary tab. Execution
failures now retain sanitized stage/target/field diagnostics in the state directory
instead of suggesting login failure for every browser exception.

Moodle form reads now wait for registered page initialization and stable controls.
Approval fingerprints ignore only transient availability `nodeUID` metadata and
the default clock values of disabled date components with an unchecked enable
checkbox. Enabled dates, real restriction conditions, visibility and other form
settings still invalidate approval if changed.

Existing access-restriction timestamps use `chrome_availability.py`, a bounded
native Moodle date-model executor. It preserves rule structure, operators,
visibility, other conditions and exact epoch seconds; native validation and
fresh post-save readback are mandatory. The 2026-10-08 native tree editor extends
this with date/group/grouping condition addition and removal.

`health.json` under `course_agent_app/reports/shared_chrome` records a heartbeat
every five seconds plus each chat's last successful intake read. A live PID alone
does not imply working intake. Focus, reload and CDP recovery have bounded backoff;
drafts and uncertain sends are preserved. Proven pre-send failures may retry;
uncertain clicks are read back without sending again. The existing real-mention,
requester binding and current-run approval rules remain in force.

Operator-only `--replan-unwritten-run <run ID>` archives an exact failed task with
no possible or verified save, preserves its conversation and scope, and starts
fresh planning and approval. It never reuses an old OK or replays a save.

`watch_listener_health.py --duration-hours 24` records real continuous health
without browser actions or messages. Its `health_observation.json` distinguishes
running from completed observation. The 12-hour simulated outage/recovery test
does not substitute for an actual overnight run. Regression and diagnostic
details are in `docs/RELIABILITY_VALIDATION_2026-10-07.md`.

## WhatsApp 响应规则（按 course_agent_v1 对齐）

`chrome_operator_config.json` 的 `mention_only_chats` 同时包含 `DASE7137 - 2026` 和
`test approval`。两个群的新任务都必须通过 WhatsApp 的 @ 联系人功能提及当前 agent 账号。
普通群聊、@ 其他成员、手工输入普通 @ 文本、引用消息里的 @ 和账号自身发出的消息不触发新任务。
当前账号名称从真实个人主页读取，仅用于匹配结构化提及；显式账号 ID 不匹配时不会用同名放行。

与 v1 一致，已接受任务中，同一请求者对当前问题或修改范围的回答可以不重复 @。
审批仍要求原请求者在原群回复当前 `OK 六位码` / `NO 六位码`；错误码、旧码、其他人的回答
以及无任务的审批命令不触发新任务。回复在接收时绑定当前运行和当前问题，不能被后来的问题挪用。
任务完成后，新请求重新要求 @。需要操作的提问、范围选择和审批请求会 @ 原请求者；
沿用 v1 的发送器，无法插入真实提及时使用名字前缀，不能向其他聊天发送。

界面上的“群组视频通话”等结构用于确认群聊，无法判断时保守执行群聊规则，不能默认成私聊。
当前界面真实提及使用 `span[role="button"][tabindex="0"]`，也纳入结构化提及识别；
仍需账号 ID 或已读取的账号名称准确匹配，普通文字和引用中的 @ 不算触发。
本次移植仅涉及 WhatsApp 接收、绑定和回复规则；v2 的浏览器与课程执行入口保持独立。
参考 v1：`docs/BROWSER_FIRST_COURSE_AGENT_WORKFLOW.md`、`course_agent_app/task_dispatcher.py`、
`course_agent_app/run_course_agent_app.py` 的 `wait_for_staff_task_context` 和 `send_message_to_chat`。

## 默认文件库

`course_agent_app/chrome_operator_config.json` 中的 `file_library_root` 默认指向
`C:\Users\Sonia\DASE_Agent\files`。每次新请求都会扫描该目录，刷新
`course_files/library_index.json`；可阅读的目录在
`course_files/library_index.md`。这两份本机生成的目录不上传仓库，新增文件无需手工登记。

规划器先看索引，再按任务选择文件读取正文，支持 Word、PowerPoint、Excel（含 `.xls`）、
PDF 和 UTF-8 文本；长内容分页读取。不会把全库正文默认送入规划器。文件名不足以判断版本时，
先检查相关文件内容；仍不明确才询问。目标学期为 2026-2027 Semester 1，旧学期资料只作为源文件。
选中的文件在读取和执行相关 Moodle 计划前检查 SHA-256；任务期间源文件变化会阻止使用旧证据。
原文件保持不变，索引和后续生成物放在 v2。扫描版 PDF 暂无 OCR，提取不到正文会明确标记。

此接入提供文件发现和只读内容检查；文档改写、Moodle 文件上传仍需接入相应执行工具，不能把
“找到文件”报告为“修改或上传完成”。Moodle 写入继续使用原聊天的当前计划批准。

用户已于 2026-10-06 明确授权向当前配置的 Codex 规划服务提供目录和按需选中的正文，
`file_library_planner_access` 已设为 `true`。每个新请求刷新索引，规划器按任务选择源文件。
规划服务只接收相对文件名、文件 ID、格式及选中的正文页，
绝对路径、文件大小与 SHA-256 保留在本地。授权不会自动开启全库正文读取或 Moodle 写入。

手工刷新索引：`.\.venv\Scripts\python.exe course_agent_app\chrome_file_library.py`

基于 `origin_course_agent` 的独立工作副本；原版本及 `course_agent_v1` 未被替换。

## 启动

2026-10-06 按用户选择，默认使用 `v2/.chrome_agent_profile` 中的专用 Chrome。首次在打开的窗口中登录 Moodle、扫码登录 WhatsApp，以后复用登录状态。保留一个 WhatsApp Web 标签页。这个模式无需在每次连接时点击 Allow，程序不复制原浏览器的 cookie。

```powershell
cd C:\Users\Sonia\DASE_Agent\v2
.\start_v2.ps1 -Check  # 只读检查，不发消息、不修改 Moodle
.\start_v2.ps1         # 持续监测并响应新命令
```

配置位于 `course_agent_app/chrome_operator_config.json`：监测 `DASE7137 - 2026` 和 `test approval`；支持 Class A（140147）及 Class B（140148）。两个聊天分别确认、分别回复。只有启动后收到的、有可核实时间和发送人信息的新传入消息才作为请求；账号自己的发出消息不作为命令。WhatsApp `stop agent` / `停止` 或本地 Ctrl-C 可停止。

### 浏览器模式

当前默认是 `chrome_operator_config.json` 中的 `browser_mode: dedicated`。也可以单独准备专用浏览器：

```powershell
.\start_v2_dedicated_chrome.ps1 -SetupOnly
# 在打开的新 Chrome 中首次登录 Moodle、扫码登录 WhatsApp。
.\start_v2_dedicated_chrome.ps1
```

这个窗口使用相同 Chrome 程序，但登录数据独立保存于 `v2/.chrome_agent_profile`，以后复用；不能自动继承当前窗口的 cookie。程序只开放本机调试端口，不复制原配置或关闭原窗口。两个启动方式使用同一任务锁，不能同时运行。
这是 Chrome 官方支持的[独立用户目录与调试端口方式](https://developer.chrome.com/blog/remote-debugging-port?hl=en)。

如以后明确选择复用原来的日常 Chrome，可把 `browser_mode` 改为 `existing`，在那个 Chrome 中启用 `chrome://inspect/#remote-debugging` 并允许连接。该模式的新连接会要求 Allow；持续运行期间共用一个连接。

运行状态：`course_agent_app/reports/shared_chrome/status.json`。原有 `.env` 只用于本机配置，不应分享。启动入口是 `start_v2.ps1`；复制保留的旧入口仍含旧沙盒逻辑。

## 工作流程

1. 从当前 Chrome 读取两门课程的章节、活动、分组及 Grouping。
2. 规划器按请求语义选择需要检查的真实设置表单，检查关联时间、分组关系和相关活动。
3. 用户选择“仅请求内容”、全部关联修改，或指定部分关联项；可用自然语言回答。
4. 生成包含实际字段标签及修改前后值的批准请求。用户在原聊天回复 `OK 六位码` 或 `NO 六位码`。
5. 重新检查选定范围并保存，重新打开表单/成员关系页核对。一次批准只覆盖该不可变计划。

可在等待批准时发送修订说明；旧批准随即失效，重新读取并展示新计划。日期组件与其启用复选框按页面真实结构处理。同一表单的可选关联字段在用户选择后合并成一次保存。

## 当前能力边界

- 更新活动设置表单中实际可见/可展开的文本、单选、多选、复选框及日期组件，包括名称、截止时间、分组模式等标准字段。
- 新建及编辑 Group / Grouping 的标准表单字段。
- 依据当前请求提供的成员编号或姓名查询，添加、移除或移动成员。姓名查询只在浏览器内瞬时匹配，候选姓名不保存；移动失败时核对源组并回退新加的目标关系。
- 将已有 Group 加入/移出已有 Grouping。
- 提出相关修改范围与未解决的依赖；未选择的关联项不执行。

不是“任意 Moodle 操作”的承诺：富文本排版、隐藏 JSON 控件中的复杂访问限制、删除分组/活动、任意新建活动，以及文件/教学大纲编辑尚未接入这个新入口。遇到这些需求，规划器应说明缺口及替代路径，不能删掉需求后宣称完成。源版本中的旧实现仍保留，但新入口不会擅自跳转旧沙盒执行。

## 验证与恢复

2026-10-07 监听恢复：连接 WhatsApp 时保持一个 `Emulation.setFocusEmulationEnabled` 会话，避免隐藏窗口下
聊天切换持续超时；关闭连接时释放该会话，不关闭用户的 Chrome。状态/错误通知未确认时仅保留待核实文本，
不能让异常从错误处理器中冒出而终止监听。

范围选择支持规划器的 `revise_scope` 决策：当前请求人明确排除原先请求中的项目时，可缩小到已展示的剩余项目，
仍禁止增加未展示的项目、空范围或缺少依赖的组合。语义判断由规划器完成，无固定口令匹配；最终保存仍需当前清单批准。

WhatsApp 长消息验证使用 `chrome_delivery.py`：根据已观察到的 `caption-read-more-button` 控件分段展开，
核对完整正文，并用新消息 ID 排除发送前已有消息；不要求长气泡完全位于视口内，不依赖“Read more”的语言。
发送后暂未确认时保留 `pending_delivery`，后续仅做读取确认，不重复点击发送，也不把传输错误交给课程规划器重新解释。
审批只有全文核实后才标记为已送达；重启仍使旧审批失效。

操作员恢复未获批准的发送失败可用 `--retry-undelivered-run <run ID>`：保留原聊天、请求人、后续规则和已选范围，
重新读取并规划，生成新的批准流程。已有成功/可能写入、已送达审批或清单校验不符时禁止此恢复方式。
2026-10-06 的真实故障验证：原发送的 6,240 字符审批被分段折叠，旧校验返回 0；修复后全文精确匹配成功。

2026-10-06 修复：多活动规划会无损共享重复的字段描述与日期选项，避免把相同下拉列表重复发送几十次。
实际失败输入约 266 万字符，超过 Codex CLI 的 1,048,576 字符限制；同一证据压缩后为 518,761 字符，真实调用已成功。
原始表单、字段值和执行前校验仍保持完整。规划进度记录在 `reports/shared_chrome/planner/planner_progress.json`；
失败时记录脱敏诊断，区分输入上限、登录和服务限制，不再把所有退出错误报告为登录问题。

Moodle 跳回登录页时，先尝试页面上真实的 HKU Portal 入口复用专用 Chrome 的 SSO 会话，然后返回原课程地址。
没有 Portal 入口时才尝试已配置的同域 Moodle 登录表单。需要重新输入密码或完成身份验证时仍由操作者完成。

操作者明确要求重跑时，可使用 `run_shared_chrome_agent.py --run --retry-failed-run <原 run ID>`。
仅允许恢复尚未提出写入方案、没有任何写入记录的真实 @ 请求规划失败；保留原聊天、发起人和任务文本，
归档原状态，创建全新运行和批准流程。普通启动不会重放历史请求，旧批准不会恢复。

WhatsApp 轮询中的浏览器超时会标记为 `reconnecting_whatsapp` 并继续读取尝试，保留内存中的
消息去重和待处理队列，不重放 Moodle 保存。Moodle 登录过期时，只在当前课程同域的已验证
登录表单使用本地配置的账号尝试恢复一次，之后回到中断的精确读取地址并校验课程。
登录仍未恢复时，WhatsApp 监测保持运行，新任务会明确说明需要登录。重启不会自动补处理
停机期间的历史消息；未处理的指令需在服务恢复后重新发送。

已通过当前 Chrome 真实会话读取两门课：各 15 个章节、58 个活动、10 个分组；准确打开两个指定聊天；真实 Codex 调用返回结构化规划结果。未进行未经 WhatsApp 批准的生产 Moodle 写入，也未发送测试聊天消息。

回归测试含真实 Playwright 浏览器和本地拦截的 Moodle 表单，覆盖批准→保存→回读、跨聊天隔离、同表单范围合并、变化拦截、重复批准、保存响应丢失、成员移动回退。运行：

```powershell
cd course_agent_app
..\.venv\Scripts\python.exe -m unittest test_shared_chrome_agent -v
```

保存响应丢失时先只读核对，不重复点击。重启后不会重用旧批准；若上次保存未确认，先进行只读恢复。无法确认的保存会暂停后续写入，需核实实际 Moodle 状态。队列在重启时归档而不自动重放历史请求；请重新发送尚未处理的命令。

Chrome 原生远程调试机制参考：[Chrome 官方说明](https://developer.chrome.com/blog/chrome-devtools-mcp-debug-your-browser-session)。
# 失败案例统一汇总

固定入口：`course_agent_app/reports/trace_eval_20261009/failed_requests.csv`，可用 Excel 打开。沿用原有清单，按 Run 去重，记录请求、出错层面、原始错误、原因、处理情况和证据。审批过期、中断、已恢复异常会单独标注；无法确认根因的案例明确注明。

`watch_failure_report.py --watch` 每 30 秒读取本地状态和 trace 并合并到同一文件；正常启动 `start_v2.ps1` 时自动启动，重复启动由文件锁防止。它不操作浏览器，不重试 Moodle 保存。状态见同目录 `failure_report_status.json` 的 `last_success`、`cases`、`error`。若 Excel 占用导致替换失败，保留原文件并在下一轮重试。

同目录 `failed_requests.json` 保存持久历史，`failure_annotations.json` 保存已确认的人工诊断，`diagnostic-overview.html` 提供可读概览。任务编号只保留 10 个的机制不会删除这些历史记录。后续修复时应更新诊断注释文件，避免直接编辑会被自动生成的 CSV 覆盖。

# Partial task completion

The configured listener proceeds with independent, reviewed operations and asks for one
current WhatsApp OK for that manifest. Uncertain items and their dependent operations are
excluded. After the confirmed batch, remaining items are reported individually with blank
lines and the missing source or decision needed for a new task. `PARTIAL` is terminal and
does not count as successful completion of the original goal. It does not auto-requeue
unresolved items. Technical failures and saves requiring readback keep their existing
recovery rules. This behavior is controlled by `defer_uncertain_items`.

If the own-account profile name is temporarily unavailable, intake retries the profile
read every 30 seconds. New group messages whose addressing is undecided remain eligible
for reinspection, while historical messages are never replayed merely because the account
name becomes available. `named_mention_identity_ready` in runtime status exposes this
condition. Operator-verified missed messages can be recovered with
`deploy_v2.ps1 -Execute -RecoverMentioned` after recording the exact candidate; startup
revalidates its original sender, text, timestamp and actual mention before queueing it.
