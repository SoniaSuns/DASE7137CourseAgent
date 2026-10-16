# Reliability validation — 2026-10-07

## Follow-up: collapsed controls in run d0ab8b7093b9

The follow-up failed with zero receipts and no write_started marker. Fresh reads
of all 15 selected forms passed their approval fingerprints, while the peerwork
date controls in both classes and the voting date controls were hidden inside
Bootstrap collapsed fieldsets. Selecting the unchanged first date value reproduced
a Playwright visibility timeout. Chrome and the Moodle session were usable.

The old origin/v1 browser DSL supports explicit clicks and Bootstrap section
toggles; its access-time lane uses a dedicated availability setter in
run_moodle_course_file_upload_batch.py. V2's generic observed-form executor had
only expanded HTML details elements. The old code's existence does not prove
every old-version task succeeds; this concrete missing UI handling is a v2
regression. Earlier fixture tests did not model this collapsed structure.

V2 now expands only the exact field's structural ancestors, using unique observed
display-only controls and verifying visibility. It never treats a submit button
as an expand control. prepare() separates local form filling and full value checks
from guarded submission, enabling a no-save rehearsal of the real selected forms.
The rehearsal blocks course writes and permits only observed page initialization
requests, including file-list reads, draft-session resume and drawer UI preference.
Every filled form is discarded by closing its owned temporary tab.

The 125-test focused suite passed, including Bootstrap 4/5 and nested controls,
ambiguous toggle rejection, save/readback, real drift, and sanitized action-specific
diagnostics. Live rehearsal results are recorded separately in
reports/shared_chrome/form-rehearsal-d0ab8b7093b9.json: all 15 forms prepared and
their requested values verified, zero Save clicks, zero blocked write attempts
in the final completed rehearsal. This proves pre-save operation, not production
save success. Generic browser failures no
longer assert a login/connection cause; execution errors record the exact stage,
target, field and application stack locations without raw browser error text.

Run b8d8b92df6ce failed during preflight, before any save: zero receipts and no
write_started marker. Original versus fresh evidence differed in editor readiness,
labels, enabled/disabled controls, unused date defaults and transient availability
node IDs. After waiting for Moodle initialization, consecutive reads of the
original failed target (4254879) had no field differences. Repeated reads of a
date-restricted target (4254917) produced identical canonical fingerprints despite
different client node IDs. See reports/shared_chrome/form_drift_diagnosis.json.

The native date plugin source served by this Moodle instance was inspected at
/availability/yui/src/form/js/form.js and
/availability/condition/date/yui/src/form/js/form.js. The date plugin stores exact
epoch seconds in pluginNode data and serializes them through the native form
update. Its five-minute selects cannot represent every existing timestamp. The
new executor changes only approved timestamps through that native backing state,
checks the full rule tree before and after, runs native validation, and reads back
after one guarded save. All five original restriction patches preserve existing
rule structure. Production checks in this repair were read-only.

119 relevant tests passed: the 116-test combined stability/health/recovery/intake/
delivery/planner-payload/library suite, plus three browser integration tests for
native restriction save/readback, missing approval, and real restriction drift.
Browser integration tests intercept Moodle requests locally. They are not evidence
of production Moodle writes. Tests retain rejection of changed manifests, possible
saves, cross-chat approval, true form drift and duplicate submission.

Health validation includes an accelerated 12-hour simulation crossing midnight,
one-chat outage while the other continues, exact-once intake and queue preservation,
heartbeat freshness, bounded recovery, draft protection, folded long replies and
uncertain click-response handling. Real continuous recording is separate and its
current duration/results must be read from health_observation.json, never inferred
from the simulation or from process existence.
