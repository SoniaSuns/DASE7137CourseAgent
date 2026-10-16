from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MoodleAgentTool:
    tool_id: str
    risk: str
    purpose: str
    allowed_use: str
    stop_conditions: tuple[str, ...]
    evidence: tuple[str, ...]


TOOL_REGISTRY: dict[str, MoodleAgentTool] = {
    "claim_existing_moodle_session": MoodleAgentTool(
        tool_id="claim_existing_moodle_session",
        risk="read_only",
        purpose="Attach to the configured signed-in Moodle browser profile or open the target URL in that logged-in browser context.",
        allowed_use="Use before any Moodle operation. Do not enter credentials or create a fresh login.",
        stop_conditions=(
            "Moodle shows a login page or authentication prompt.",
            "The browser bridge cannot attach to the configured signed-in Moodle browser profile.",
            "The visible course or module does not match the operation target.",
        ),
        evidence=("current URL", "page title", "course/module heading"),
    ),
    "read_assignment_settings": MoodleAgentTool(
        tool_id="read_assignment_settings",
        risk="read_only",
        purpose="Read assignment settings before a write.",
        allowed_use="Use on the Moodle modedit page to capture title, visibility, and access restrictions.",
        stop_conditions=(
            "Assignment name field is missing or ambiguous.",
            "Visibility is not the expected hidden state.",
            "Access restriction state does not match the expected precondition.",
        ),
        evidence=("assignment name", "visibility selected option", "availability/access restriction JSON or UI summary"),
    ),
    "edit_assignment_title": MoodleAgentTool(
        tool_id="edit_assignment_title",
        risk="write",
        purpose="Change only the assignment name/title field.",
        allowed_use="Use only after action-time approval for the exact source and target titles.",
        stop_conditions=(
            "Current title is neither the expected source title nor an approved target/recovery state described by the task.",
            "Any field other than assignment name would need to change.",
            "Save button is missing or ambiguous.",
        ),
        evidence=("before title", "after-fill title", "post-save display title", "post-save settings title"),
    ),
    "set_access_restriction_time": MoodleAgentTool(
        tool_id="set_access_restriction_time",
        risk="write",
        purpose="Change an approved availability/access time field on a hidden or sandbox object.",
        allowed_use="Use only when the operation names the exact time field, old value, new value, and rollback value.",
        stop_conditions=(
            "The live Moodle field differs from the approved source value.",
            "The task is ambiguous between activity availability and Restrict access conditions.",
            "The target object is student-visible and no staff-only/sandbox protection is present.",
        ),
        evidence=("before time values", "after-fill time values", "post-save settings time values"),
    ),
    "upload_or_replace_file": MoodleAgentTool(
        tool_id="upload_or_replace_file",
        risk="write",
        purpose="Upload or replace a harmless approved test file on a hidden/sandbox Moodle resource.",
        allowed_use="Use only for explicitly approved local test files and hidden/sandbox targets.",
        stop_conditions=(
            "The Moodle target is not a File resource settings page or the filemanager/content area cannot be identified.",
            "The target file area contains production material that would be replaced.",
            "The target resource is visible to students and the operation does not explicitly allow visible same-file reupload.",
            "The approved local file path is missing or differs from the requested file.",
            "The browser tool cannot use a file chooser/input upload flow reliably.",
        ),
        evidence=(
            "local file path and filename",
            "target file area before",
            "uploaded file name/version",
            "target file area after",
            "final visibility",
        ),
    ),
    "create_file_resource": MoodleAgentTool(
        tool_id="create_file_resource",
        risk="write",
        purpose="Create one Moodle File resource in the approved sandbox course section.",
        allowed_use="Use only for explicitly approved local test files and hidden/sandbox course sections.",
        stop_conditions=(
            "The browser is not on the approved sandbox course or the requested course section cannot be identified.",
            "A resource with the exact approved title already exists but its file or visibility cannot be verified.",
            "The Moodle activity chooser, File resource form, or filemanager/content area cannot be identified.",
            "The new resource would be visible to students and the operation does not explicitly approve visibility.",
            "The approved local file path is missing or differs from the requested file.",
            "The browser tool cannot use a file chooser/input upload flow reliably.",
        ),
        evidence=(
            "local file path and filename",
            "target course section",
            "new resource title",
            "uploaded file name/version",
            "final visibility",
        ),
    ),
    "draft_or_schedule_announcement": MoodleAgentTool(
        tool_id="draft_or_schedule_announcement",
        risk="high_write",
        purpose="Create a draft or scheduled announcement only when recipients and send state are explicitly constrained.",
        allowed_use="Use only for draft/self-only/safe scheduled tests with explicit action-time approval.",
        stop_conditions=(
            "Student recipients are selected or would be selected by default.",
            "The tool cannot guarantee draft-only or self-only behavior.",
            "A scheduled message cannot be deleted before send time.",
        ),
        evidence=("recipient state", "draft/scheduled status", "deletion/rollback state when applicable"),
    ),
    "verify_moodle_state": MoodleAgentTool(
        tool_id="verify_moodle_state",
        risk="read_only",
        purpose="Verify the Moodle state after a write and before reporting success.",
        allowed_use="Use after every write and rollback.",
        stop_conditions=(
            "Final state cannot be read from the settings page.",
            "Any guardrail field changed unexpectedly.",
            "The final title/time/file/announcement state does not match the approved target.",
        ),
        evidence=("settings readback", "display page readback", "screenshot if available"),
    ),
}


ACTION_TOOL_CHAINS: dict[str, tuple[str, ...]] = {
    "edit_assignment_title": (
        "claim_existing_moodle_session",
        "read_assignment_settings",
        "edit_assignment_title",
        "verify_moodle_state",
    ),
    "set_access_restriction_time": (
        "claim_existing_moodle_session",
        "read_assignment_settings",
        "set_access_restriction_time",
        "verify_moodle_state",
    ),
    "upload_or_replace_file": (
        "claim_existing_moodle_session",
        "upload_or_replace_file",
        "verify_moodle_state",
    ),
    "create_file_resource": (
        "claim_existing_moodle_session",
        "create_file_resource",
        "verify_moodle_state",
    ),
    "draft_or_schedule_announcement": (
        "claim_existing_moodle_session",
        "draft_or_schedule_announcement",
        "verify_moodle_state",
    ),
    "create_or_update_hidden_marker": (
        "claim_existing_moodle_session",
        "verify_moodle_state",
    ),
}


CODEX_BROWSER_RULES: tuple[str, ...] = (
    "Do not log in, enter credentials, inspect cookies/local storage/passwords, or create a new Moodle session.",
    "Start with `browser tabs` and use the raw CDP `targetId` returned by that call, not suggested aliases such as `t3`.",
    "Include the current raw `targetId` on every snapshot, navigate, act, and evaluate call.",
    "After every navigation or Moodle save, call `browser tabs` again and continue with the current raw `targetId`.",
    "If a browser call returns `targetId must match request targetId`, take one fresh tabs+snapshot and retry once with the raw targetId; if it repeats, stop and report BLOCKED.",
    "For assignment settings reads, prefer evaluate selectors `#id_name`, `[name=\"visible\"]`, and `[name=\"availabilityconditionsjson\"]`.",
    "For title edits, set only `#id_name` and dispatch input/change events, or use a fill action against a ref from a fresh snapshot.",
    "For saving, prefer a verified `Save and display` button and record the exact save count. A proven fallback is `document.getElementById('id_submitbutton').click()`.",
    "If a write succeeds but rollback is interrupted, immediately run a rollback-only continuation from the verified live state.",
)


ACTION_EXECUTION_OUTLINES: dict[str, tuple[str, ...]] = {
    "edit_assignment_title": (
        "Precheck title, visibility, access restriction JSON, course id, and module id.",
        "Save only the approved title change.",
        "Verify the display page and settings page after the save.",
        "Save only the approved rollback title.",
        "Verify final title, visibility, and access restrictions from the settings page.",
    ),
    "set_access_restriction_time": (
        "Precheck title, visibility, access restriction JSON, and existing Restrict access UI summary.",
        "Confirm whether this is an add-first restriction operation or an update to an existing condition.",
        "Proceed only when source value, target value, expected JSON, rollback JSON, save method, and `showc` intent are approved.",
        "Save the approved access restriction change and verify the exact JSON/readback state.",
        "Run the approved rollback and verify the original access restriction JSON is restored.",
    ),
    "upload_or_replace_file": (
        "Precheck target resource visibility and file area contents.",
        "Proceed only when the local test file path, expected uploaded filename/version, and rollback/delete behavior are approved.",
        "Upload or replace only the approved harmless test file.",
        "Verify the file area after save.",
        "Run the approved rollback and verify the file area returns to the approved baseline.",
    ),
    "create_file_resource": (
        "Precheck the approved sandbox course URL, target section, and local file path.",
        "Stop before saving if the approved resource title already exists and cannot be verified as the same file.",
        "Create only one File resource in the approved section.",
        "Upload only the approved harmless local test file and set the approved visibility.",
        "Verify the course page, resource display page, settings page, uploaded filename, and visibility after save.",
    ),
    "draft_or_schedule_announcement": (
        "Precheck recipient state, draft/schedule controls, and any default send behavior.",
        "Proceed only when draft-only, self-only, or safe scheduled behavior is guaranteed and approved.",
        "Do not send to students or leave a scheduled message that can send without rollback approval.",
        "Verify draft/scheduled state after save.",
        "Delete or rollback the item before it can send, then verify final state.",
    ),
}


def select_moodle_agent_tools(operation: dict) -> list[MoodleAgentTool]:
    action = operation.get("action", "")
    tool_ids = ACTION_TOOL_CHAINS.get(action)
    if tool_ids is None:
        tool_ids = ("claim_existing_moodle_session", "verify_moodle_state")
    return [TOOL_REGISTRY[tool_id] for tool_id in tool_ids]


def render_browser_rules_for_agent(browser_profile: str = "user") -> str:
    browser_profile = (browser_profile or "user").strip() or "user"
    lines = [
        "## Codex Moodle Browser Rules",
        "",
        "These rules are mandatory when Codex controls Moodle through the browser tool.",
        "",
        f'- Use the configured signed-in Moodle browser profile by setting `profile="{browser_profile}"` in every browser tool call.',
    ]
    lines.extend(f"- {item}" for item in CODEX_BROWSER_RULES)
    return "\n".join(lines).rstrip() + "\n"


def render_execution_outline_for_agent(operation: dict) -> str:
    action = operation.get("action", "")
    outline = ACTION_EXECUTION_OUTLINES.get(
        action,
        (
            "Run a read-only precheck against the live Moodle target.",
            "Proceed only if the operation action, approved target state, rollback state, and save count are explicit.",
            "Verify Moodle state after any write and after rollback.",
            "Stop and report BLOCKED if the live state does not match preconditions.",
        ),
    )
    lines = [
        "## Action Execution Outline",
        "",
        f"Action: `{action}`",
        "",
    ]
    lines.extend(f"{index}. {item}" for index, item in enumerate(outline, start=1))
    return "\n".join(lines).rstrip() + "\n"


def render_toolkit_for_agent(operation: dict) -> str:
    tools = select_moodle_agent_tools(operation)
    lines = [
        "## Moodle Agent Tool Selection",
        "",
        f"Selected action: `{operation.get('action', '')}`",
        "",
        "The agent must choose from these tools for this operation. If none of the selected tools can satisfy the live Moodle state safely, stop and report the blocker.",
        "",
    ]
    for index, tool in enumerate(tools, start=1):
        lines.extend(
            [
                f"### {index}. `{tool.tool_id}`",
                "",
                f"- Risk: {tool.risk}",
                f"- Purpose: {tool.purpose}",
                f"- Allowed use: {tool.allowed_use}",
                "- Stop conditions:",
                *(f"  - {item}" for item in tool.stop_conditions),
                "- Required evidence:",
                *(f"  - {item}" for item in tool.evidence),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_operation_summary(operation: dict) -> str:
    fields = [
        ("Batch", operation.get("batch_id", "")),
        ("Run", operation.get("approval_run_id", "")),
        ("Operation", operation.get("operation_id", "")),
        ("Action", operation.get("action", "")),
        ("Risk", operation.get("risk_level", "")),
        ("Course URL", operation.get("target_course_url", "")),
        ("Settings URL", operation.get("target_settings_url", "")),
        ("Target object", operation.get("target_object", "")),
        ("Object type", operation.get("object_type", "")),
    ]
    lines = ["## Operation Summary", ""]
    for label, value in fields:
        if value:
            lines.append(f"- {label}: {value}")
    if operation.get("original_title") or operation.get("target_title"):
        lines.extend(
            [
                f"- Original title: {operation.get('original_title', '')}",
                f"- Target title: {operation.get('target_title', '')}",
            ]
        )
    extra_fields = [
        ("Expected access restrictions JSON", "expected_access_restrictions_json"),
        ("Time field scope", "time_field_scope"),
        ("Time field name", "time_field_name"),
        ("Source value", "source_value"),
        ("Source availability JSON", "source_availabilityconditionsjson"),
        ("Target condition type", "target_condition_type"),
        ("Target value", "target_value"),
        ("Target availability JSON", "target_availabilityconditionsjson"),
        ("Rollback value", "rollback_value"),
        ("Rollback availability JSON", "rollback_availabilityconditionsjson"),
        ("Save method", "save_method"),
        ("Show condition intent", "showc_intent"),
        ("Precheck report", "precheck_report"),
        ("Source file path", "source_file_path"),
        ("Target file path", "target_file_path"),
        ("Rollback file path", "rollback_file_path"),
        ("Allow agent target discovery", "allow_agent_target_discovery"),
        ("Allow visible existing resource reupload", "allow_visible_existing_resource_reupload"),
        ("Target discovery start URL", "target_discovery_start_url"),
        ("Target discovery query", "target_discovery_query"),
        ("Target discovery section", "target_discovery_section"),
        ("Target discovery expected type", "target_discovery_expected_type"),
        ("Expected existing filename", "expected_existing_filename"),
        ("Expected uploaded filename", "expected_uploaded_filename"),
        ("Expected rollback filename", "expected_rollback_filename"),
        ("Delete existing before upload", "delete_existing_before_upload"),
        ("Delete-before-upload filename", "delete_before_upload_filename"),
        ("Delete-before-upload scope", "delete_before_upload_scope"),
        ("File area hint", "file_area_hint"),
        ("Filemanager field hint", "filemanager_field_hint"),
        ("Recipient policy", "recipient_policy"),
        ("Subject", "subject"),
        ("Schedule time", "schedule_time"),
        ("Rollback action", "rollback_action"),
    ]
    for label, key in extra_fields:
        value = operation.get(key)
        if value:
            lines.append(f"- {label}: {value}")
    if operation.get("expected_visibility"):
        lines.append(f"- Expected visibility: {operation.get('expected_visibility')}")
    return "\n".join(lines).rstrip() + "\n"
