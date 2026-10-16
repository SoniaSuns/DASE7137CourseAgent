# Moodle page recovery and WhatsApp mention repair

The c067d37f3453 failure retained only a generic browser error. Its exact failed
page action cannot be reconstructed from the old logs. A fresh, read-only pass
successfully opened all 12 remaining target forms in the existing signed-in
profile; this establishes current accessibility, not the historical timeout cause.
Evidence: course_agent_app/reports/shared_chrome/moodle-read-diagnosis.json.

Moodle previously recovered login redirects but did not recover general read or
initialization failures. The browser now shares a three-attempt budget across each
inventory read, exact form read, navigation or unsaved preparation. It first
reopens the known GET route, then replaces its work tab (reconnecting to Chrome
if disconnected). It does not reload a possible POST response or repeat Save.
Retries retain course/form identity, snapshot-drift and date checks. Identity and
authorization failures are not transient retry candidates. Save remains outside
the recovery wrapper; uncertain writes use fresh readback only.

Bounded diagnostics record operation, stage, target, error type and application
stack frames without raw exception messages, credentials, cookies or field values.
The planning and incoming-reply failure paths now retain stage evidence too.
Collapsed form fields can be read from the DOM without clicking every summary.

The failed run also contained a real mentioned OK reply in its replanning history.
Startup had failed to discover the current account name. The observed current UI
places it in the own-account Me drawer title, rather than the older Edit-name
layout. That exact account-scoped title is now supported, preserving explicit
different-account, quoted-mention and plain-text exclusions. Live verification
observed the account name and returned to the chat list without sending a message.
No old approval is replayed on restart.

Deployment exposed a separate startup timeout when selecting a WhatsApp chat.
Startup now uses bounded transport reload/reconnect before establishing its
message baseline, retaining the existing unsent-draft guard. Previously this
recovery existed only after the polling loop had begun.

Validation: the 164-test focused suite passed after the Moodle recovery changes.
After adding current-profile UI coverage, 112 affected tests passed. Regression
cases cover transient and permanent reads, shared retry budgets, drift rejection,
one-save execution, diagnostic redaction and structured mention removal from an
approval. The owner task is resumed through fresh planning with its previous
verified results and remaining scope preserved.

Live recovery verification deliberately aborted two GET navigations in a temporary
work tab, then observed automatic tab replacement and 92 fields read from the exact
same assignment. Zero Save actions. Evidence:
course_agent_app/reports/shared_chrome/live-read-recovery-verification.json.
The additional reconnect/tab-ownership and startup-baseline tests also passed.

The deployed startup recovery subsequently recovered the real chat-open failure,
established both exact chat baselines, read both current courses (59 activities
and 10 groups each) and queued the failed date task for new planning. Current
account-name discovery succeeded in the deployed process.
