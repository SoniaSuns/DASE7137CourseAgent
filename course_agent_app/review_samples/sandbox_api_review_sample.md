# Sandbox API Review Sample

This file is a reviewer-facing sample attachment for the WhatsApp approval flow.
It is safe to attach because it contains only sandbox/test metadata.

## Proposed Moodle Action

- Course: DASE7137 Agent sandbox
- Moodle course id: `105330`
- Course URL: `https://migrationtest-moodle.hku.hk/course/view.php?id=105330`
- Operation kind: `announcement_api`
- Moodle web service function: `mod_forum_add_discussion`
- Target forum: resolved uniquely from fresh current-course readback

## Proposed Announcement

Subject:

```text
[API TEST] Sandbox announcement RUN-TEMPLATE
```

Body:

```html
<p>This is a sandbox-only Moodle API announcement test.</p>
```

## File Change Progress Context

- Change ID: `file-change-20260625-001`
- File count: `1`
- Agent groups: `2`
- Expected total agent count: `3`
- Review status: `empty_group_simulation_created`
- Empty grouping: `[API TEST] Course Automation POC` / Moodle grouping id `25094`

## Agent Groups

| Group | Expected agents | Moodle group | Moodle group id | Purpose |
| --- | ---: | --- | ---: | --- |
| Course File Planning Agents | 2 | `[API TEST] Course File Planning Agents` | `301966` | Classify files and prepare proposed changes |
| Approval and Moodle API Agents | 1 | `[API TEST] Approval and Moodle API Agents` | `301967` | Prepare approval messages and execute sandbox APIM actions |

## Reviewer Decision

Approve only if the operation should publish the sandbox announcement above.
This sample does not authorize real-course changes, file uploads, access
restriction edits, grades, or deletion.
