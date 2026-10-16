# Submit completion before readback

Runs b220da9a1914 and ca9a889c8ce8 read the first assignment's original dates after
a save attempt. Full original-state readback subsequently matched. Three earlier
verified changes were retained. These failed runs did not retain the initial
submit response, so their precise historical server outcome remains unknown.

Current source inspection found that v2 clicked Save and then waited for the page's
existing DOMContentLoaded state. The source version installed its navigation wait
before clicking. With a delayed client submit, the v2 sequence immediately opened
the readback URL and cancelled the pending submission. A local browser regression
reproduced the same old-value mismatch and loss of delayed client-validation errors.

The repair installs POST/response/document listeners before the single approved
click. It waits for the observed form's navigation POST and resulting document;
an already-loaded old page cannot satisfy this boundary. Pending submissions retain
their original tab and listeners. Verification cannot navigate that tab while its
submit is pending. If its response later completes, the running listener resumes
readback. After restart, unchanged original values alone cannot clear a persisted
pending submission; matching approved values can still establish successful save.
Course identity, current-run approval, snapshot drift and date checks remain intact.

The original save error is now retained instead of being replaced by a later
readback mismatch. Diagnostic observations contain only target, request count,
response status, completion state and timestamps, without cookies, secrets, POST
contents or file data.

The production form was prepared in a temporary tab without saving: browser-native
validity, date controls and pre-existing validation messages were checked. In a
separate transport-blocked probe, the course POST was fulfilled locally instead of
forwarded. It contained the requested dates and correct save-button/module values.
Both Tiny editor cleanup requests were also held/blocked; this live probe did not
reproduce delayed client submission. It therefore does not prove the historical
failure was caused by the local timing defect. Server-response evidence from the
next freshly approved execution is still needed for end-to-end confirmation.

Evidence under course_agent_app/reports/shared_chrome:
- save-preparation-diagnosis-b220da9a1914.json
- submit-probe-b220da9a1914.json (explicitly transport-blocked; zero course writes)
- moodle_submit_observation.json (written by future approved executions)

Regression cases cover delayed submit, delayed client rejection, pending submission
protection, late completion, single-save execution, validation readback, and prior
approval/drift/recovery behavior.

Validation: 166 focused tests passed. After adding explicit server-status reporting
and restart/late-completion protections, all 28 tests in the affected submit and
date-readback classes passed. The source and current review state were snapshotted
under reports/shared_chrome/deployments/20261007-232437-submit-completion before
restarting for fresh planning. The pending old approval is not reused.
