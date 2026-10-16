from __future__ import annotations

import json
import os
import re
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from .agent_tools import (
    render_browser_rules_for_agent,
    render_execution_outline_for_agent,
    render_operation_summary,
    render_toolkit_for_agent,
)
from .api_client import (
    DEFAULT_API_URL,
    DEFAULT_COURSE_ID,
    DEFAULT_COURSE_URL,
    execute_api_operation,
)
from .operation_loader import load_operation, resolve_operation_path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT = PROJECT_ROOT / "course_agent_app"
REPORTS = ROOT / "reports"
AGENT_BROWSER_MODES = {
    "agent_browser_action",
    "moodle_agent_browser_action",
    "codex_browser_action",
    "codex_moodle_browser_action",
}
API_ACTION_MODES = {"api_action", "moodle_api_action", "apim_action"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def runtime_python_hint() -> str:
    return r".venv\Scripts\python.exe" if os.name == "nt" else ".venv/bin/python"


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _append_ledger(event: dict) -> None:
    with (ROOT / "approval_ledger.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": _now(), **event}, ensure_ascii=False) + "\n")


def _operation_kind(env: dict[str, str]) -> str:
    if env.get("MOODLE_OPERATION_TEMPLATE", "").strip() or env.get(
        "MOODLE_OPERATION_TEMPLATE_PATH", ""
    ).strip():
        return "template"
    kind = env.get("MOODLE_OPERATION_KIND", "").strip().lower()
    if kind:
        return kind
    mode = env.get("MOODLE_EXECUTION_MODE", "").strip().lower()
    if mode in API_ACTION_MODES:
        return "announcement_api"
    if mode in {"browser_action", "moodle_browser_action", "direct_browser_action"} | AGENT_BROWSER_MODES:
        return "direct_title_edit"
    return "hidden_marker"


def normalize_write_policy(value: str | None, default: str = "precheck_only") -> str:
    policy = (value or default).strip().lower().replace("-", "_")
    aliases = {
        "precheck": "precheck_only",
        "readonly": "precheck_only",
        "read_only": "precheck_only",
        "execute": "execute_only",
        "write": "execute_only",
        "write_only": "execute_only",
        "no_revert": "execute_only",
        "execute_and_rollback": "execute_and_revert",
        "write_and_revert": "execute_and_revert",
        "write_and_rollback": "execute_and_revert",
    }
    return aliases.get(policy, policy)


def approved_browser_write_policy(env: dict[str, str]) -> str:
    return normalize_write_policy(
        env.get("MOODLE_AGENT_BROWSER_WRITE_POLICY")
        or env.get("MOODLE_BROWSER_WRITE_POLICY")
        or "execute_only"
    )


def _operation_from_template(env: dict[str, str]) -> dict | None:
    template = env.get("MOODLE_OPERATION_TEMPLATE", "").strip() or env.get(
        "MOODLE_OPERATION_TEMPLATE_PATH", ""
    ).strip()
    if not template:
        return None
    path = resolve_operation_path(template, ROOT)
    _operation_path, operation = load_operation(path)
    operation["batch_id"] = env.get("BATCH_ID", operation.get("batch_id", "SMOKE-MOODLE-WA-001"))
    operation["approval_run_id"] = env.get(
        "APPROVAL_RUN_ID", operation.get("approval_run_id", "")
    )
    operation["planned_at"] = _now()
    operation["template_path"] = str(path)
    return operation


def _direct_title_edit_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    operation_id = env.get("MOODLE_OPERATION_ID", "SANDBOX-D1-TITLE-EDIT-UNSUPPORTED")
    target_module_id = env.get("MOODLE_TARGET_MODULE_ID", "")
    target_course_url = env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL)
    settings_url = env.get(
        "MOODLE_TARGET_SETTINGS_URL",
        f"https://migrationtest-moodle.hku.hk/course/modedit.php?update={target_module_id}&return=1",
    )
    original_title = env.get("MOODLE_ORIGINAL_TITLE", "Announcements")
    target_title = env.get(
        "MOODLE_TARGET_TITLE",
        "Announcements [API TEST TITLE - UNSUPPORTED]",
    )
    write_policy = approved_browser_write_policy(env)
    execute_phrase = f"Approved Moodle execute {batch_id} RUN={approval_run_id} OP={operation_id}"
    revert_phrase = f"Approved Moodle revert {batch_id} RUN={approval_run_id} OP={operation_id}"
    guardrails = [
        "sandbox course only",
        "this action is unsupported by the current Moodle APIM whitelist and should be blocked in api_action mode",
        "do not change visibility",
        "do not change access restrictions",
        "do not change dates, grades, submission settings, or notifications",
        "do not delete or upload Moodle files",
        "do not use the legacy real DASE7137/IMSE7137 Moodle course",
    ]
    if write_policy == "execute_and_revert":
        guardrails.append("approved safe-write demo performs exactly one title save and one rollback save")
    elif write_policy == "execute_only":
        guardrails.append("approved visible demo performs exactly one title save and leaves the target title in place")
    return {
        "batch_id": batch_id,
        "approval_run_id": approval_run_id,
        "operation_id": operation_id,
        "operation_kind": "direct_title_edit",
        "risk_level": "low",
        "target_course_url": target_course_url,
        "target_module_id": target_module_id,
        "target_settings_url": settings_url,
        "section_name": env.get("MOODLE_SECTION_NAME", "Week 12 Project Interaction Development (Apr 8)"),
        "target_object": env.get("MOODLE_TEST_OBJECT_NAME", original_title),
        "object_type": "Assignment",
        "action": "edit_assignment_title",
        "original_title": original_title,
        "target_title": target_title,
        "planned_at": _now(),
        "expected_visibility": "Hide on course page",
        "guardrails": guardrails,
        "browser_execution": {
            "script": "run_moodle_browser_action.py",
            "execute_command": (
                f"{runtime_python_hint()} run_moodle_browser_action.py "
                f"--operation reports/{batch_id}_{approval_run_id}_operation.json "
                "--execute-approved-change "
                f"--approval-phrase '{execute_phrase}'"
            ),
            "revert_command": (
                f"{runtime_python_hint()} run_moodle_browser_action.py "
                f"--operation reports/{batch_id}_{approval_run_id}_operation.json "
                "--revert-approved-change "
                f"--approval-phrase '{revert_phrase}'"
            ),
            "execute_approval_phrase": execute_phrase,
            "revert_approval_phrase": revert_phrase,
            "default_mode": write_policy,
        },
    }


def _filename_hint(path_text: str) -> str:
    if not path_text or "TBD" in path_text:
        return path_text
    return Path(path_text).name


def _split_path_list(value: str) -> list[str]:
    if not value.strip():
        return []
    separators = [os.pathsep, "\n", ","]
    items = [value]
    for separator in separators:
        expanded: list[str] = []
        for item in items:
            expanded.extend(item.split(separator))
        items = expanded
    return [item.strip() for item in items if item.strip()]


def _approval_review_attachments(env: dict[str, str]) -> list[dict]:
    attachments: list[dict] = []
    highlighted_path = (
        env.get("MOODLE_HIGHLIGHTED_REVIEW_FILE_PATH", "").strip()
        or env.get("MOODLE_REVIEW_ATTACHMENT_PATH", "").strip()
        or env.get("MOODLE_APPROVAL_ATTACHMENT_PATH", "").strip()
    )
    if highlighted_path:
        attachments.append(
            {
                "kind": "highlighted_review_file",
                "label": env.get(
                    "MOODLE_HIGHLIGHTED_REVIEW_FILE_LABEL",
                    "Highlighted changed-region review file",
                ),
                "path": highlighted_path,
            }
        )

    for path in _split_path_list(env.get("MOODLE_APPROVAL_ATTACHMENT_PATHS", "")):
        if path == highlighted_path:
            continue
        attachments.append(
            {
                "kind": "approval_attachment",
                "label": "Approval attachment",
                "path": path,
            }
        )
    return attachments


def _file_upload_replace_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    operation_id = env.get("MOODLE_OPERATION_ID", "SANDBOX-FILE-UPLOAD-UNSUPPORTED")
    allow_discovery = env.get("MOODLE_ALLOW_AGENT_TARGET_DISCOVERY", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    target_module_id = env.get(
        "MOODLE_TARGET_MODULE_ID",
        "" if allow_discovery else "TBD_HIDDEN_FILE_RESOURCE_MODULE_ID",
    )
    target_course_url = env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL)
    default_settings_url = (
        ""
        if allow_discovery
        else "TBD_HIDDEN_FILE_RESOURCE_SETTINGS_URL"
        if "TBD" in target_module_id
        else f"https://migrationtest-moodle.hku.hk/course/modedit.php?update={target_module_id}&return=1"
    )
    settings_url = env.get("MOODLE_TARGET_SETTINGS_URL", default_settings_url)
    source_file_path = env.get(
        "MOODLE_SOURCE_FILE_PATH",
        env.get("MOODLE_BASELINE_FILE_PATH", "test_upload_files/moodle_upload_demo_v1.txt"),
    )
    target_file_path = env.get(
        "MOODLE_TARGET_FILE_PATH",
        env.get("MOODLE_UPLOAD_FILE_PATH", "test_upload_files/moodle_upload_demo_v2.txt"),
    )
    rollback_file_path = env.get("MOODLE_ROLLBACK_FILE_PATH", source_file_path)
    approval_attachments = _approval_review_attachments(env)
    highlighted_review_file_path = next(
        (
            attachment["path"]
            for attachment in approval_attachments
            if attachment.get("kind") == "highlighted_review_file"
        ),
        "",
    )
    delete_first = env.get("MOODLE_UPLOAD_DELETE_FIRST", "0").strip().lower() in {"1", "true", "yes", "on"}
    allow_visible_reupload = env.get("MOODLE_ALLOW_VISIBLE_FILE_REUPLOAD", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    write_policy = approved_browser_write_policy(env)
    execute_phrase = f"Approved Moodle upload {batch_id} RUN={approval_run_id} OP={operation_id}"
    revert_phrase = f"Approved Moodle upload rollback {batch_id} RUN={approval_run_id} OP={operation_id}"
    guardrails = [
        "use only harmless approved local test files",
        "sandbox course only",
        "the current APIM whitelist cannot create/update File resources; use this action only with the approved browser-agent fallback unless the custom plugin is installed",
        "do not use the legacy real DASE7137/IMSE7137 Moodle course",
        "do not change title, visibility, dates, grades, access restrictions, or notifications",
        (
            "approved demo may remove the existing file from the filemanager draft area before reuploading the same approved file"
            if delete_first
            else "do not delete any Moodle file unless the rollback step explicitly approves it"
        ),
        "run-id-bound action-time approval is required before upload, replace, delete, or rollback",
    ]
    if write_policy == "execute_and_revert":
        guardrails.append("approved upload demo performs exactly one upload save and one rollback save")
    elif write_policy == "execute_only":
        guardrails.append("approved upload demo performs exactly one upload save and leaves the target file in place")
    return {
        "batch_id": batch_id,
        "approval_run_id": approval_run_id,
        "operation_id": operation_id,
        "operation_kind": "file_upload_replace",
        "risk_level": "medium",
        "target_course_url": target_course_url,
        "target_module_id": target_module_id,
        "target_settings_url": settings_url,
        "allow_agent_target_discovery": allow_discovery,
        "allow_visible_existing_resource_reupload": allow_visible_reupload,
        "target_discovery_start_url": target_course_url,
        "target_discovery_query": env.get("MOODLE_TARGET_DISCOVERY_QUERY", _filename_hint(target_file_path)),
        "target_discovery_section": env.get("MOODLE_TARGET_DISCOVERY_SECTION", env.get("MOODLE_SECTION_NAME", "")),
        "target_discovery_expected_type": "File resource",
        "section_name": env.get("MOODLE_SECTION_NAME", "TBD_SANDBOX_OR_HIDDEN_SECTION"),
        "target_object": env.get("MOODLE_TEST_OBJECT_NAME", "TBD_HIDDEN_TEST_FILE_RESOURCE"),
        "object_type": "File resource",
        "action": "upload_or_replace_file",
        "source_file_path": source_file_path,
        "target_file_path": target_file_path,
        "rollback_file_path": rollback_file_path,
        "highlighted_review_file_path": highlighted_review_file_path,
        "approval_attachments": approval_attachments,
        "expected_existing_filename": env.get("MOODLE_EXPECTED_EXISTING_FILENAME", _filename_hint(source_file_path)),
        "expected_uploaded_filename": env.get("MOODLE_EXPECTED_UPLOADED_FILENAME", _filename_hint(target_file_path)),
        "expected_rollback_filename": env.get("MOODLE_EXPECTED_ROLLBACK_FILENAME", _filename_hint(rollback_file_path)),
        "delete_existing_before_upload": delete_first,
        "delete_before_upload_filename": env.get(
            "MOODLE_DELETE_BEFORE_UPLOAD_FILENAME",
            env.get("MOODLE_EXPECTED_EXISTING_FILENAME", _filename_hint(target_file_path)),
        ),
        "delete_before_upload_scope": "filemanager draft area before final save",
        "file_area_hint": env.get("MOODLE_FILE_AREA_HINT", "File resource content filemanager"),
        "filemanager_field_hint": env.get("MOODLE_FILEMANAGER_FIELD_HINT", "contentfiles / files_filemanager"),
        "planned_at": _now(),
        "expected_visibility": env.get(
            "MOODLE_EXPECTED_VISIBILITY",
            "Show on course page" if allow_visible_reupload else "Hide on course page",
        ),
        "guardrails": guardrails,
        "browser_execution": {
            "execute_approval_phrase": execute_phrase,
            "revert_approval_phrase": revert_phrase,
            "default_mode": write_policy,
        },
    }


def _file_api_upload_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    operation_id = env.get("MOODLE_OPERATION_ID", "SANDBOX-FILE-API-UPLOAD")
    upload_file_path = env.get(
        "MOODLE_UPLOAD_FILE_PATH",
        env.get("MOODLE_TARGET_FILE_PATH", "test_upload_files/moodle_upload_demo_v2.txt"),
    )
    expected_filename = env.get("MOODLE_EXPECTED_UPLOADED_FILENAME", _filename_hint(upload_file_path))
    approval_attachments = _approval_review_attachments(env)
    approval_attachment_paths = {str(attachment.get("path", "")).strip() for attachment in approval_attachments}
    if upload_file_path and "TBD" not in upload_file_path and upload_file_path not in approval_attachment_paths:
        approval_attachments.append(
            {
                "kind": "approval_attachment",
                "label": "Moodle API upload file",
                "path": upload_file_path,
            }
        )

    contextid = env.get("MOODLE_FILE_UPLOAD_CONTEXTID", "").strip()
    contextlevel = env.get("MOODLE_FILE_UPLOAD_CONTEXTLEVEL", "user").strip()
    instanceid = (
        env.get("MOODLE_FILE_UPLOAD_INSTANCEID", "").strip()
        or env.get("MOODLE_USER_ID", "").strip()
    )
    component = env.get("MOODLE_FILE_UPLOAD_COMPONENT", "user").strip()
    filearea = env.get("MOODLE_FILE_UPLOAD_FILEAREA", "draft").strip()
    itemid = env.get("MOODLE_FILE_UPLOAD_ITEMID", "0").strip()
    filepath = env.get("MOODLE_FILE_UPLOAD_FILEPATH", "/").strip() or "/"
    return {
        "batch_id": batch_id,
        "approval_run_id": approval_run_id,
        "operation_id": operation_id,
        "operation_kind": "file_api_upload",
        "risk_level": "medium",
        "target_course_url": env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL),
        "target_course_id": env.get("MOODLE_COURSE_ID", DEFAULT_COURSE_ID),
        "section_name": env.get(
            "MOODLE_FILE_UPLOAD_SECTION_NAME",
            env.get("MOODLE_SECTION_NAME", "Moodle API file upload test"),
        ),
        "target_object": env.get(
            "MOODLE_FILE_UPLOAD_OBJECT_NAME",
            expected_filename or "Moodle API upload test file",
        ),
        "object_type": "Moodle API file upload",
        "action": "upload_file_api",
        "target_file_path": upload_file_path,
        "upload_file_path": upload_file_path,
        "expected_uploaded_filename": expected_filename,
        "contextid": contextid,
        "contextlevel": contextlevel,
        "instanceid": instanceid,
        "component": component,
        "filearea": filearea,
        "itemid": itemid,
        "filepath": filepath,
        "approval_attachments": approval_attachments,
        "planned_at": _now(),
        "api_execution": {
            "api_url": env.get("MOODLE_API_URL", DEFAULT_API_URL),
            "key_env": "DASE7137_KEY",
            "wsfunction": env.get("MOODLE_FILE_UPLOAD_WSFUNCTION", "core_files_upload"),
        },
        "guardrails": [
            "sandbox APIM only; do not use browser writes for Moodle changes",
            "upload only the harmless approved local test file",
            "this API upload test must not create, replace, delete, or publish course File resources",
            "default target is the Moodle user draft/private file area, not a visible course module",
            "do not use the legacy real DASE7137/IMSE7137 Moodle course",
            "subscription key must come from DASE7137_KEY and must not be written to files",
        ],
    }


def _course_file_resource_upload_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    operation_id = env.get("MOODLE_OPERATION_ID", "SANDBOX-COURSE-FILE-RESOURCE-UPLOAD")
    upload_file_path = env.get(
        "MOODLE_UPLOAD_FILE_PATH",
        env.get("MOODLE_TARGET_FILE_PATH", "test_upload_files/moodle_upload_demo_v2.txt"),
    )
    expected_filename = env.get("MOODLE_EXPECTED_UPLOADED_FILENAME", _filename_hint(upload_file_path))
    resource_name = env.get(
        "MOODLE_RESOURCE_NAME",
        env.get("MOODLE_TEST_OBJECT_NAME", expected_filename or "Moodle API uploaded course file"),
    )
    approval_attachments = _approval_review_attachments(env)
    approval_attachment_paths = {str(attachment.get("path", "")).strip() for attachment in approval_attachments}
    if upload_file_path and "TBD" not in upload_file_path and upload_file_path not in approval_attachment_paths:
        approval_attachments.append(
            {
                "kind": "approval_attachment",
                "label": "Moodle course file upload",
                "path": upload_file_path,
            }
        )

    cmid = env.get("MOODLE_TARGET_MODULE_ID", env.get("MOODLE_RESOURCE_CMID", "0")).strip() or "0"
    mode = "replace existing File resource" if cmid.isdigit() and cmid != "0" else "create new File resource"
    return {
        "batch_id": batch_id,
        "approval_run_id": approval_run_id,
        "operation_id": operation_id,
        "operation_kind": "course_file_resource_upload",
        "risk_level": "medium",
        "target_course_url": env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL),
        "target_course_id": env.get("MOODLE_COURSE_ID", DEFAULT_COURSE_ID),
        "section_name": env.get("MOODLE_SECTION_NAME", "Moodle API file upload test"),
        "sectionnum": env.get("MOODLE_SECTION_NUM", "0"),
        "target_object": resource_name,
        "object_type": "Moodle File resource",
        "action": "upload_course_file_resource",
        "resource_name": resource_name,
        "target_module_id": cmid,
        "target_file_path": upload_file_path,
        "upload_file_path": upload_file_path,
        "expected_uploaded_filename": expected_filename,
        "intro": env.get("MOODLE_RESOURCE_INTRO", ""),
        "visible": env.get("MOODLE_RESOURCE_VISIBLE", "1").strip().lower() in {"1", "true", "yes", "on"},
        "replacefiles": env.get("MOODLE_REPLACE_FILES", "1").strip().lower() not in {"0", "false", "no", "off"},
        "approval_attachments": approval_attachments,
        "planned_at": _now(),
        "api_execution": {
            "api_url": env.get("MOODLE_API_URL", DEFAULT_API_URL),
            "key_env": "DASE7137_KEY",
            "wsfunction": env.get(
                "MOODLE_COURSE_FILE_UPLOAD_WSFUNCTION",
                "local_courseops_api_upload_file_resource",
            ),
        },
        "guardrails": [
            "sandbox course only: migrationtest Moodle course id 105330 unless staff explicitly approves another course",
            f"operation mode: {mode}",
            "requires the local_courseops_api Moodle plugin and APIM whitelist",
            "new resources default to visible unless MOODLE_RESOURCE_VISIBLE=0 is explicitly approved",
            "upload only the approved local file named in this operation package",
            "do not use the legacy real DASE7137/IMSE7137 Moodle course",
            "subscription key must come from DASE7137_KEY and must not be written to files",
        ],
    }


def _browser_course_file_resource_upload_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    operation_id = env.get("MOODLE_OPERATION_ID", "SANDBOX-BROWSER-COURSE-FILE-RESOURCE-UPLOAD")
    target_course_url = env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL)
    target_course_id = env.get("MOODLE_COURSE_ID", DEFAULT_COURSE_ID)
    if (
        target_course_id != DEFAULT_COURSE_ID
        or "migrationtest-moodle.hku.hk" not in target_course_url
        or f"id={DEFAULT_COURSE_ID}" not in target_course_url
    ):
        raise ValueError(
            "Browser course File resource upload is enabled only for the migrationtest "
            f"sandbox course {DEFAULT_COURSE_ID}."
        )
    upload_file_path = env.get(
        "MOODLE_UPLOAD_FILE_PATH",
        env.get("MOODLE_TARGET_FILE_PATH", "test_upload_files/moodle_upload_demo_v2.txt"),
    )
    expected_filename = env.get("MOODLE_EXPECTED_UPLOADED_FILENAME", _filename_hint(upload_file_path))
    resource_name = env.get(
        "MOODLE_RESOURCE_NAME",
        env.get("MOODLE_TEST_OBJECT_NAME", expected_filename or "Browser uploaded course file"),
    )
    visible = env.get("MOODLE_RESOURCE_VISIBLE", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    approval_attachments = _approval_review_attachments(env)
    approval_attachment_paths = {str(attachment.get("path", "")).strip() for attachment in approval_attachments}
    if upload_file_path and "TBD" not in upload_file_path and upload_file_path not in approval_attachment_paths:
        approval_attachments.append(
            {
                "kind": "approval_attachment",
                "label": "Moodle browser course file upload",
                "path": upload_file_path,
            }
        )

    write_policy = approved_browser_write_policy(env)
    execute_phrase = f"Approved Moodle create file resource {batch_id} RUN={approval_run_id} OP={operation_id}"
    return {
        "batch_id": batch_id,
        "approval_run_id": approval_run_id,
        "operation_id": operation_id,
        "operation_kind": "browser_course_file_resource_upload",
        "risk_level": "medium",
        "target_course_url": target_course_url,
        "target_course_id": target_course_id,
        "section_name": env.get("MOODLE_SECTION_NAME", "General"),
        "sectionnum": env.get("MOODLE_SECTION_NUM", "0"),
        "target_object": resource_name,
        "object_type": "Moodle File resource",
        "action": "create_file_resource",
        "resource_name": resource_name,
        "target_module_id": "0",
        "target_settings_url": "",
        "create_new_resource": True,
        "target_file_path": upload_file_path,
        "upload_file_path": upload_file_path,
        "expected_uploaded_filename": expected_filename,
        "intro": env.get("MOODLE_RESOURCE_INTRO", ""),
        "visible": visible,
        "expected_visibility": "Show on course page" if visible else "Hide on course page",
        "approval_attachments": approval_attachments,
        "planned_at": _now(),
        "browser_execution": {
            "execute_approval_phrase": execute_phrase,
            "revert_approval_phrase": "",
            "default_mode": write_policy,
        },
        "guardrails": [
            "browser automation fallback is allowed only for the migrationtest sandbox course id 105330",
            "create exactly one Moodle File resource in the approved section",
            "new resources default to visible unless MOODLE_RESOURCE_VISIBLE=0 is explicitly approved",
            "upload only the approved local file named in this operation package",
            "do not change grades, dates, access restrictions, groups, completion, or notifications",
            "do not create duplicate resources if the exact approved title/file is already present",
            "do not use the legacy real DASE7137/IMSE7137 Moodle course",
            "run-id-bound action-time approval is required before opening the browser upload/save path",
        ],
    }


def _browser_course_file_resource_delete_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    operation_id = env.get("MOODLE_OPERATION_ID", "SANDBOX-BROWSER-COURSE-FILE-RESOURCE-DELETE")
    target_course_url = env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL)
    target_course_id = env.get("MOODLE_COURSE_ID", DEFAULT_COURSE_ID)
    if (
        target_course_id != DEFAULT_COURSE_ID
        or "migrationtest-moodle.hku.hk" not in target_course_url
        or f"id={DEFAULT_COURSE_ID}" not in target_course_url
    ):
        raise ValueError(
            "Browser course File resource delete is enabled only for the migrationtest "
            f"sandbox course {DEFAULT_COURSE_ID}."
        )
    target_module_id = env.get("MOODLE_TARGET_MODULE_ID", env.get("MOODLE_RESOURCE_CMID", "")).strip()
    resource_name = env.get(
        "MOODLE_RESOURCE_NAME",
        env.get("MOODLE_TARGET_TITLE", env.get("MOODLE_TEST_OBJECT_NAME", "")),
    ).strip()
    resource_url = env.get("MOODLE_RESOURCE_URL", env.get("MOODLE_TARGET_RESOURCE_URL", "")).strip()
    write_policy = approved_browser_write_policy(env)
    execute_phrase = f"Approved Moodle delete file resource {batch_id} RUN={approval_run_id} OP={operation_id}"
    return {
        "batch_id": batch_id,
        "approval_run_id": approval_run_id,
        "operation_id": operation_id,
        "operation_kind": "browser_course_file_resource_delete",
        "risk_level": "high",
        "target_course_url": target_course_url,
        "target_course_id": target_course_id,
        "section_name": env.get("MOODLE_SECTION_NAME", "General"),
        "target_object": resource_name,
        "object_type": "Moodle File resource",
        "action": "delete_file_resource",
        "resource_name": resource_name,
        "target_module_id": target_module_id,
        "target_resource_url": resource_url,
        "target_settings_url": (
            f"https://migrationtest-moodle.hku.hk/course/modedit.php?update={target_module_id}&return=1"
            if target_module_id.isdigit()
            else ""
        ),
        "expected_existing_filename": env.get("MOODLE_EXPECTED_EXISTING_FILENAME", ""),
        "planned_at": _now(),
        "browser_execution": {
            "execute_approval_phrase": execute_phrase,
            "revert_approval_phrase": "",
            "default_mode": write_policy,
        },
        "guardrails": [
            "browser automation delete is allowed only for the migrationtest sandbox course id 105330",
            "delete exactly one Moodle File resource whose cmid and title match the approved manifest",
            "run read-only APIM course-content preflight before opening the delete URL",
            "stop before deletion if the cmid is missing, not a File resource, title does not match, or more than one target is plausible",
            "do not delete sections, forums, assignments, quizzes, groups, files inside other resources, or any legacy real-course object",
            "the current-run WhatsApp OK is the action-time approval for this delete; do not ask staff for a second approval phrase",
            "verify through read-only APIM after deletion that the cmid is absent from sandbox course 105330",
        ],
    }


def _announcement_api_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    operation_id = env.get("MOODLE_OPERATION_ID", "SANDBOX-ANNOUNCEMENT-API")
    source = _manifest_operation_from_env(env)
    action = env.get("MOODLE_FORUM_ACTION", "").strip() or str(source.get("action") or "publish_announcement")
    subject = env.get("MOODLE_FORUM_POST_SUBJECT") or env.get(
        "MOODLE_ANNOUNCEMENT_SUBJECT",
        str(source.get("subject") or f"[API TEST] Sandbox announcement {approval_run_id or 'RUN-TEMPLATE'}"),
    )
    body = env.get("MOODLE_FORUM_POST_MESSAGE") or env.get(
        "MOODLE_ANNOUNCEMENT_BODY",
        str(
            source.get("message")
            or source.get("body")
            or (
                "<p>This is a sandbox-only Moodle API announcement test for the "
                "DASE7137 Agent integration.</p>"
            )
        ),
    )
    forum_name = env.get("MOODLE_TARGET_FORUM_NAME", str(source.get("target_forum_name") or source.get("forum_name") or ""))
    explicit_forum_id = (
        env.get("MOODLE_TARGET_FORUM_ID")
        or str(source.get("target_forum_id") or source.get("forumid") or "").strip()
    )
    forum_id = explicit_forum_id
    target_post_id = (
        env.get("MOODLE_FORUM_POST_ID")
        or str(source.get("target_post_id") or source.get("parent_post_id") or "")
    ).strip()
    target_discussion_id = (
        env.get("MOODLE_FORUM_DISCUSSION_ID")
        or str(source.get("target_discussion_id") or source.get("discussion_id") or "")
    ).strip()
    target_discussion_url = (
        env.get("MOODLE_FORUM_DISCUSSION_URL")
        or str(source.get("target_discussion_url") or source.get("discussion_url") or "")
    ).strip()
    target_discussion_title = str(
        source.get("target_discussion_title")
        or source.get("discussion_title")
        or source.get("target_subject")
        or ""
    ).strip()
    wsfunction = (
        "mod_forum_add_discussion_post"
        if action in {"reply_forum_post", "reply_to_forum_post"}
        else "mod_forum_add_discussion"
    )
    if approval_run_id:
        subject = subject.replace("RUN-TEMPLATE", approval_run_id)
        body = body.replace("RUN-TEMPLATE", approval_run_id)
    course_id = env.get("MOODLE_COURSE_ID", DEFAULT_COURSE_ID)
    approval_attachments = _approval_review_attachments(env)
    operation = {
        "batch_id": batch_id,
        "approval_run_id": approval_run_id,
        "operation_id": operation_id,
        "operation_kind": "announcement_api",
        "risk_level": "medium",
        "target_course_url": env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL),
        "target_course_id": course_id,
        "target_forum_id": forum_id,
        "target_forum_name": forum_name,
        "target_post_id": target_post_id,
        "target_discussion_id": target_discussion_id,
        "target_discussion_url": target_discussion_url,
        "target_discussion_title": target_discussion_title,
        "target_module_id": env.get("MOODLE_TARGET_MODULE_ID", str(source.get("target_module_id") or "")),
        "section_name": env.get("MOODLE_SECTION_NAME", "General"),
        "target_object": env.get("MOODLE_TEST_OBJECT_NAME", str(source.get("target_object") or forum_name or "Announcements")),
        "object_type": (
            "Forum reply"
            if action in {"reply_forum_post", "reply_to_forum_post"}
            else "Forum announcement"
        ),
        "action": action,
        "subject": subject,
        "message": body,
        "approval_attachments": approval_attachments,
        "planned_at": _now(),
        "api_execution": {
            "api_url": env.get("MOODLE_API_URL", DEFAULT_API_URL),
            "key_env": "DASE7137_KEY",
            "wsfunction": wsfunction,
        },
        "guardrails": [
            "sandbox course only: migrationtest Moodle course id 105330",
            "use APIM endpoint only; do not use browser writes for Moodle changes",
            "do not use the legacy real DASE7137/IMSE7137 Moodle course",
            "subject must make test posts easy to identify, for example with [API TEST]",
            "subscription key must come from DASE7137_KEY and must not be written to files",
        ],
    }
    for key in (
        "target_post_id",
        "target_post_url",
        "target_discussion_id",
        "target_discussion_url",
        "target_discussion_title",
        "target_subject",
        "target_ref",
    ):
        if source.get(key):
            operation[key] = source.get(key)
    return operation


def _group_api_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    operation_id = env.get("MOODLE_OPERATION_ID", "SANDBOX-GROUP-API")
    course_id = env.get("MOODLE_COURSE_ID", DEFAULT_COURSE_ID)
    source = _manifest_operation_from_env(env)
    action = env.get("MOODLE_GROUP_ACTION", "").strip() or str(source.get("action") or "create_group")
    group_name = env.get(
        "MOODLE_GROUP_NAME",
        str(source.get("group_name") or f"[API TEST] Sandbox group {approval_run_id or 'RUN-TEMPLATE'}"),
    )
    group_id = env.get("MOODLE_GROUP_ID", "").strip() or str(source.get("group_id") or source.get("groupid") or "")
    approval_attachments = _approval_review_attachments(env)
    operation = {
        "batch_id": batch_id,
        "approval_run_id": approval_run_id,
        "operation_id": operation_id,
        "operation_kind": "group_api",
        "risk_level": "medium",
        "target_course_url": env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL),
        "target_course_id": course_id,
        "section_name": env.get("MOODLE_SECTION_NAME", "Participants / Groups"),
        "target_object": group_name,
        "object_type": "Moodle group",
        "action": action,
        "group_name": group_name,
        "description": env.get(
            "MOODLE_GROUP_DESCRIPTION",
            str(source.get("description") or "Sandbox group created through HKU APIM Moodle API."),
        ),
        "approval_attachments": approval_attachments,
        "planned_at": _now(),
        "api_execution": {
            "api_url": env.get("MOODLE_API_URL", DEFAULT_API_URL),
            "key_env": "DASE7137_KEY",
            "wsfunction": "core_group_delete_groups" if action == "delete_group" else "core_group_create_groups",
        },
        "guardrails": [
            "sandbox course only: migrationtest Moodle course id 105330",
            "use APIM endpoint only; do not use browser writes for Moodle changes",
            "do not use the legacy real DASE7137/IMSE7137 Moodle course",
            "subscription key must come from DASE7137_KEY and must not be written to files",
        ],
    }
    if group_id:
        operation["group_id"] = group_id
    if source.get("approval_scope"):
        operation["approval_scope"] = source.get("approval_scope")
    return operation


def _grouping_api_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    operation_id = env.get("MOODLE_OPERATION_ID", "SANDBOX-GROUPING-API")
    course_id = env.get("MOODLE_COURSE_ID", DEFAULT_COURSE_ID)
    source = _manifest_operation_from_env(env)
    action = env.get("MOODLE_GROUPING_ACTION", "").strip() or str(source.get("action") or "create_grouping")
    grouping_name = env.get(
        "MOODLE_GROUPING_NAME",
        str(source.get("grouping_name") or f"[API TEST] Sandbox grouping {approval_run_id or 'RUN-TEMPLATE'}"),
    )
    idnumber = env.get("MOODLE_GROUPING_IDNUMBER") or str(source.get("idnumber") or f"api-{approval_run_id or 'run-template'}")
    approval_attachments = _approval_review_attachments(env)
    operation = {
        "batch_id": batch_id,
        "approval_run_id": approval_run_id,
        "operation_id": operation_id,
        "operation_kind": "grouping_api",
        "risk_level": "medium",
        "target_course_url": env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL),
        "target_course_id": course_id,
        "section_name": env.get("MOODLE_SECTION_NAME", "Participants / Groups / Groupings"),
        "target_object": env.get(
            "MOODLE_TEST_OBJECT_NAME",
            str(source.get("target_object") or grouping_name or "[API TEST] Sandbox grouping"),
        ),
        "object_type": "Moodle grouping",
        "action": action,
        "grouping_name": grouping_name,
        "idnumber": idnumber,
        "description": env.get(
            "MOODLE_GROUPING_DESCRIPTION",
            str(source.get("description") or "Sandbox grouping created through HKU APIM Moodle API."),
        ),
        "approval_attachments": approval_attachments,
        "planned_at": _now(),
        "api_execution": {
            "api_url": env.get("MOODLE_API_URL", DEFAULT_API_URL),
            "key_env": "DASE7137_KEY",
            "wsfunction": {
                "delete_grouping": "core_group_delete_groupings",
                "assign_group_names_to_grouping": "core_group_assign_grouping",
                "unassign_group_names_from_grouping": "core_group_unassign_grouping",
            }.get(action, "core_group_create_groupings"),
        },
        "guardrails": [
            "sandbox course only: migrationtest Moodle course id 105330",
            "grouping idnumber is required; Moodle returns invalidresponse when it is omitted",
            "use APIM endpoint only; do not use browser writes for Moodle changes",
            "do not use the legacy real DASE7137/IMSE7137 Moodle course",
            "subscription key must come from DASE7137_KEY and must not be written to files",
        ],
    }
    grouping_id = env.get("MOODLE_GROUPING_ID", "").strip() or str(
        source.get("grouping_id") or source.get("groupingid") or ""
    )
    if grouping_id:
        operation["grouping_id"] = grouping_id
    if "group_names" in source:
        operation["group_names"] = source.get("group_names")
    if env.get("MOODLE_GROUP_NAMES", "").strip():
        operation["group_names"] = env["MOODLE_GROUP_NAMES"]
    if source.get("approval_scope"):
        operation["approval_scope"] = source.get("approval_scope")
    return operation


def _access_time_api_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    operation_id = env.get("MOODLE_OPERATION_ID", "SANDBOX-ACCESS-TIME-PLUGIN-REQUIRED")
    return {
        "batch_id": batch_id,
        "approval_run_id": approval_run_id,
        "operation_id": operation_id,
        "operation_kind": "access_time",
        "risk_level": "medium",
        "target_course_url": env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL),
        "target_course_id": env.get("MOODLE_COURSE_ID", DEFAULT_COURSE_ID),
        "target_module_id": env.get("MOODLE_TARGET_MODULE_ID", ""),
        "section_name": env.get("MOODLE_SECTION_NAME", "General"),
        "target_object": env.get("MOODLE_TEST_OBJECT_NAME", "Announcements"),
        "object_type": "Course module",
        "action": "set_access_restriction_time",
        "target_value": env.get("MOODLE_TARGET_ACCESS_TIME", "TBD_APPROVED_TEST_DATETIME_WITH_TIMEZONE"),
        "rollback_value": "remove added date restriction",
        "planned_at": _now(),
        "guardrails": [
            "sandbox course only: migrationtest Moodle course id 105330",
            "current APIM whitelist cannot update module availability/access restriction settings",
            "block this operation until a custom Moodle plugin function is installed",
            "do not use browser writes or the legacy real DASE7137/IMSE7137 Moodle course",
        ],
    }


def _hidden_marker_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    marker_text = f"[SANDBOX MOODLE API TEST] {batch_id} {approval_run_id} approval-gated Moodle marker"
    return {
        "batch_id": batch_id,
        "approval_run_id": approval_run_id,
        "risk_level": "low",
        "target_course_url": env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL),
        "target_course_id": env.get("MOODLE_COURSE_ID", DEFAULT_COURSE_ID),
        "section_name": env.get("MOODLE_SECTION_NAME", "00 Overview"),
        "target_object": env.get("MOODLE_TEST_OBJECT_NAME", "Sandbox Smoke Test Approval Marker"),
        "object_type": "Text and media area / Label",
        "action": "create_or_update_hidden_marker",
        "marker_text": marker_text,
        "planned_at": _now(),
        "guardrails": [
            "sandbox course only",
            "do not publish announcements",
            "do not touch grades",
            "do not delete existing Moodle objects",
            "make the marker hidden or keep it in a staff-only/test section",
        ],
    }


def _manifest_operation_from_env(env: dict[str, str]) -> dict:
    raw = env.get("MOODLE_MANIFEST_OPERATION_JSON", "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _browser_moodle_task_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    operation_id = env.get("MOODLE_OPERATION_ID", "SANDBOX-BROWSER-MOODLE-TASK")
    source = _manifest_operation_from_env(env)
    operation = dict(source)
    operation.update(
        {
            "batch_id": batch_id,
            "approval_run_id": approval_run_id,
            "operation_id": operation_id,
            "operation_kind": "browser_moodle_task",
            "risk_level": operation.get("risk_level", "medium"),
            "target_course_url": env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL),
            "target_course_id": env.get("MOODLE_COURSE_ID", DEFAULT_COURSE_ID),
            "action": operation.get("action", "browser_moodle_task"),
            "target_object": operation.get("target_object")
            or operation.get("approval_item")
            or operation.get("summary", "Moodle browser task"),
            "object_type": operation.get("object_type", "Moodle course item"),
            "planned_at": _now(),
            "browser_execution": {
                "execute_approval_phrase": (
                    f"Approved Moodle browser task {batch_id} RUN={approval_run_id} OP={operation_id}"
                ),
                "revert_approval_phrase": "",
                "default_mode": approved_browser_write_policy(env),
            },
        }
    )
    operation.setdefault(
        "guardrails",
        [
            "browser automation is allowed only for migrationtest sandbox course id 105330",
            "execute only the browser_steps generated for the approved staff task",
            "stop if any step navigates outside migrationtest Moodle, requests credentials, or targets another course",
            "stop before any write not described by the current-run WhatsApp approval",
            "run APIM/readback or browser verification after the task and report PASS only with evidence",
        ],
    )
    return operation


def _browser_section_rename_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    operation_id = env.get("MOODLE_OPERATION_ID", "SANDBOX-BROWSER-SECTION-RENAME")
    source = _manifest_operation_from_env(env)
    operation = dict(source)
    section_name = operation.get("section_name") or env.get("MOODLE_SECTION_NAME", "")
    sectionnum = str(operation.get("sectionnum") or env.get("MOODLE_SECTION_NUM", "")).strip()
    section_id = str(operation.get("section_id") or env.get("MOODLE_SECTION_ID", "")).strip()
    new_title = operation.get("new_title") if "new_title" in operation else env.get("MOODLE_TARGET_TITLE", "")
    operation.update(
        {
            "batch_id": batch_id,
            "approval_run_id": approval_run_id,
            "operation_id": operation_id,
            "operation_kind": "browser_section_rename",
            "risk_level": operation.get("risk_level", "medium"),
            "target_course_url": env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL),
            "target_course_id": env.get("MOODLE_COURSE_ID", DEFAULT_COURSE_ID),
            "section_name": section_name,
            "sectionnum": sectionnum,
            "section_id": section_id,
            "target_object": operation.get("target_object") or section_name or f"Section {sectionnum}",
            "object_type": "Moodle course section",
            "action": "rename_section",
            "new_title": new_title,
            "target_title": new_title,
            "planned_at": _now(),
            "browser_execution": {
                "execute_approval_phrase": (
                    f"Approved Moodle rename section {batch_id} RUN={approval_run_id} OP={operation_id}"
                ),
                "revert_approval_phrase": "",
                "default_mode": approved_browser_write_policy(env),
            },
        }
    )
    operation.setdefault(
        "guardrails",
        [
            "browser automation section rename is allowed only for migrationtest sandbox course id 105330",
            "rename exactly one section resolved by APIM readback before opening section settings",
            "do not change resources, activities, visibility, dates, groups, grades, completion, or notifications",
            "the current-run WhatsApp OK is the action-time approval; do not ask staff for a second approval phrase",
            "verify the section title through read-only APIM after save",
        ],
    )
    return operation


def _browser_module_visibility_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    operation_id = env.get("MOODLE_OPERATION_ID", "SANDBOX-BROWSER-MODULE-VISIBILITY")
    source = _manifest_operation_from_env(env)
    operation = dict(source)
    target_module_id = str(operation.get("target_module_id") or env.get("MOODLE_TARGET_MODULE_ID", "")).strip()
    target_title = operation.get("target_object") or env.get("MOODLE_TARGET_TITLE", env.get("MOODLE_TEST_OBJECT_NAME", ""))
    visible_value = operation.get("visible") if "visible" in operation else env.get("MOODLE_TARGET_VISIBLE", "0")
    visible = visible_value if isinstance(visible_value, bool) else str(visible_value).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "visible",
        "show",
    }
    operation.update(
        {
            "batch_id": batch_id,
            "approval_run_id": approval_run_id,
            "operation_id": operation_id,
            "operation_kind": "browser_module_visibility",
            "risk_level": operation.get("risk_level", "high" if visible else "medium"),
            "target_course_url": env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL),
            "target_course_id": env.get("MOODLE_COURSE_ID", DEFAULT_COURSE_ID),
            "target_module_id": target_module_id,
            "target_object": target_title,
            "object_type": "Moodle course module",
            "action": "set_visibility",
            "visible": visible,
            "expected_visibility": "Show on course page" if visible else "Hide on course page",
            "planned_at": _now(),
            "browser_execution": {
                "execute_approval_phrase": (
                    f"Approved Moodle set visibility {batch_id} RUN={approval_run_id} OP={operation_id}"
                ),
                "revert_approval_phrase": "",
                "default_mode": approved_browser_write_policy(env),
            },
        }
    )
    operation.setdefault(
        "guardrails",
        [
            "browser automation visibility change is allowed only for migrationtest sandbox course id 105330",
            "change exactly one module resolved by APIM readback before opening settings",
            "making content visible may affect students and must be explicitly approved in WhatsApp",
            "do not change title, files, dates, access restrictions, groups, grades, completion, or notifications",
            "verify final visibility through read-only APIM after save",
        ],
    )
    return operation


def _browser_group_signup_activity_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    operation_id = env.get("MOODLE_OPERATION_ID", "SANDBOX-GROUP-SIGNUP-ACTIVITY")
    source = _manifest_operation_from_env(env)
    operation = dict(source)
    operation.update(
        {
            "batch_id": batch_id,
            "approval_run_id": approval_run_id,
            "operation_id": operation_id,
            "operation_kind": "browser_group_signup_activity",
            "risk_level": operation.get("risk_level", "medium"),
            "target_course_url": env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL),
            "target_course_id": env.get("MOODLE_COURSE_ID", DEFAULT_COURSE_ID),
            "action": operation.get("action", "create_group_signup_activity"),
            "modname": "choicegroup",
            "sectionnum": operation.get("sectionnum") or env.get("MOODLE_SECTION_NUM", "0"),
            "section_name": operation.get("section_name") or env.get("MOODLE_SECTION_NAME", ""),
            "title": operation.get("title") or env.get("MOODLE_ACTIVITY_TITLE", ""),
            "target_object": operation.get("target_object") or operation.get("title") or env.get("MOODLE_ACTIVITY_TITLE", ""),
            "expected_visibility": operation.get("expected_visibility")
            or env.get("MOODLE_EXPECTED_VISIBILITY", "Show on course page"),
        }
    )
    if env.get("MOODLE_GROUP_NAMES", "").strip() and "group_names" not in operation:
        operation["group_names"] = env["MOODLE_GROUP_NAMES"]
    if env.get("MOODLE_GROUPING_NAME", "").strip() and "grouping_name" not in operation:
        operation["grouping_name"] = env["MOODLE_GROUPING_NAME"]
    operation.setdefault(
        "guardrails",
        [
            "sandbox course only: migrationtest Moodle course id 105330",
            "create only one Choicegroup activity in the approved section",
            "select only the approved group names",
            "do not alter grades, participants, or non-sandbox courses",
        ],
    )
    return operation


def _browser_group_membership_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    operation_id = env.get("MOODLE_OPERATION_ID", "SANDBOX-GROUP-MEMBERSHIP")
    source = _manifest_operation_from_env(env)
    operation = dict(source)
    membership_action = str(
        operation.get("membership_action") or env.get("MOODLE_MEMBERSHIP_ACTION", "")
    ).strip()
    operation.update(
        {
            "batch_id": batch_id,
            "approval_run_id": approval_run_id,
            "operation_id": operation_id,
            "operation_kind": "browser_group_membership",
            "risk_level": operation.get("risk_level", "high"),
            "target_course_url": env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL),
            "target_course_id": env.get("MOODLE_COURSE_ID", DEFAULT_COURSE_ID),
            "action": operation.get("action", f"{membership_action}_group_member"),
            "membership_action": membership_action,
            "user_id": str(operation.get("user_id") or env.get("MOODLE_USER_ID", "")),
            "user_name_query": str(
                operation.get("user_name_query") or env.get("MOODLE_USER_NAME_QUERY", "")
            ),
            "participant_lookup_mode": str(
                operation.get("participant_lookup_mode")
                or (
                    "user_id"
                    if operation.get("user_id") or env.get("MOODLE_USER_ID", "")
                    else "user_name"
                )
            ),
            "source_group_id": str(
                operation.get("source_group_id") or env.get("MOODLE_SOURCE_GROUP_ID", "")
            ),
            "source_group_name": str(
                operation.get("source_group_name") or env.get("MOODLE_SOURCE_GROUP_NAME", "")
            ),
            "target_group_id": str(
                operation.get("target_group_id") or env.get("MOODLE_TARGET_GROUP_ID", "")
            ),
            "target_group_name": str(
                operation.get("target_group_name") or env.get("MOODLE_TARGET_GROUP_NAME", "")
            ),
        }
    )
    operation.setdefault(
        "guardrails",
        [
            "sandbox course only: migrationtest Moodle course id 105330",
            "use exactly one approved participant reference: numeric user id or name query",
            "a name query must resolve to one numeric option value before any write",
            "do not return or persist candidate display names, emails, or member-list text",
            "change only the approved exact source and target Groups",
            "verify exact option-value membership transiently and persist count deltas only",
        ],
    )
    return operation


def _browser_assignment_activity_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    operation_id = env.get("MOODLE_OPERATION_ID", "SANDBOX-ASSIGNMENT-ACTIVITY")
    source = _manifest_operation_from_env(env)
    operation = dict(source)
    operation.update(
        {
            "batch_id": batch_id,
            "approval_run_id": approval_run_id,
            "operation_id": operation_id,
            "operation_kind": "browser_assignment_activity",
            "risk_level": operation.get("risk_level", "medium"),
            "target_course_url": env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL),
            "target_course_id": env.get("MOODLE_COURSE_ID", DEFAULT_COURSE_ID),
            "action": operation.get("action", "create_assignment_activity"),
            "modname": "assign",
            "sectionnum": operation.get("sectionnum") or env.get("MOODLE_SECTION_NUM", "0"),
            "section_name": operation.get("section_name") or env.get("MOODLE_SECTION_NAME", ""),
            "title": operation.get("title") or env.get("MOODLE_ACTIVITY_TITLE", ""),
            "intro": operation.get("intro") or env.get("MOODLE_ACTIVITY_INTRO", ""),
            "target_object": operation.get("target_object") or operation.get("title") or env.get("MOODLE_ACTIVITY_TITLE", ""),
            "expected_visibility": operation.get("expected_visibility")
            or env.get("MOODLE_EXPECTED_VISIBILITY", "Show on course page"),
        }
    )
    operation.setdefault(
        "guardrails",
        [
            "sandbox course only: migrationtest Moodle course id 105330",
            "create only one Assignment activity in the approved section",
            "leave dates disabled unless they were explicitly approved",
            "do not alter grades, participants, groups, or non-sandbox courses",
        ],
    )
    return operation


def _browser_forum_activity_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    operation_id = env.get("MOODLE_OPERATION_ID", "SANDBOX-FORUM-ACTIVITY")
    source = _manifest_operation_from_env(env)
    operation = dict(source)
    operation.update(
        {
            "batch_id": batch_id,
            "approval_run_id": approval_run_id,
            "operation_id": operation_id,
            "operation_kind": "browser_forum_activity",
            "risk_level": operation.get("risk_level", "medium"),
            "target_course_url": env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL),
            "target_course_id": env.get("MOODLE_COURSE_ID", DEFAULT_COURSE_ID),
            "action": operation.get("action", "create_forum_activity"),
            "modname": "forum",
            "sectionnum": operation.get("sectionnum") or env.get("MOODLE_SECTION_NUM", "0"),
            "section_name": operation.get("section_name") or env.get("MOODLE_SECTION_NAME", ""),
            "title": operation.get("title") or env.get("MOODLE_ACTIVITY_TITLE", ""),
            "intro": operation.get("intro") or env.get("MOODLE_ACTIVITY_INTRO", ""),
            "target_object": operation.get("target_object")
            or operation.get("title")
            or env.get("MOODLE_ACTIVITY_TITLE", ""),
            "expected_visibility": operation.get("expected_visibility")
            or env.get("MOODLE_EXPECTED_VISIBILITY", "Show on course page"),
        }
    )
    operation.setdefault(
        "guardrails",
        [
            "sandbox course only: migrationtest Moodle course id 105330",
            "create only one Forum activity with the approved exact name and section",
            "do not create a duplicate exact forum title",
            "do not alter grades, participants, groups, or non-sandbox courses",
        ],
    )
    return operation


def _browser_forum_post_delete_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    operation_id = env.get("MOODLE_OPERATION_ID", "SANDBOX-FORUM-POST-DELETE")
    source = _manifest_operation_from_env(env)
    operation = dict(source)
    operation.update(
        {
            "batch_id": batch_id,
            "approval_run_id": approval_run_id,
            "operation_id": operation_id,
            "operation_kind": "browser_forum_post_delete",
            "risk_level": operation.get("risk_level", "high"),
            "target_course_url": env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL),
            "target_course_id": env.get("MOODLE_COURSE_ID", DEFAULT_COURSE_ID),
            "action": operation.get("action", "delete_forum_post"),
            "target_post_id": operation.get("target_post_id") or env.get("MOODLE_FORUM_POST_ID", ""),
            "target_post_url": operation.get("target_post_url") or env.get("MOODLE_FORUM_POST_URL", ""),
            "target_discussion_id": operation.get("target_discussion_id") or env.get("MOODLE_FORUM_DISCUSSION_ID", ""),
            "target_discussion_url": operation.get("target_discussion_url") or env.get("MOODLE_FORUM_DISCUSSION_URL", ""),
            "target_subject": operation.get("target_subject") or env.get("MOODLE_FORUM_TARGET_SUBJECT", ""),
            "target_ref": operation.get("target_ref", ""),
            "target_forum_name": operation.get("target_forum_name") or env.get("MOODLE_TARGET_FORUM_NAME", ""),
            "target_forum_id": operation.get("target_forum_id") or env.get("MOODLE_TARGET_FORUM_ID", ""),
            "target_forum_url": operation.get("target_forum_url") or env.get("MOODLE_TARGET_FORUM_URL", ""),
            "target_forum_cmid": operation.get("target_forum_cmid") or env.get("MOODLE_TARGET_FORUM_CMID", ""),
        }
    )
    operation.setdefault("target_object", operation.get("target_subject") or operation.get("target_ref") or "Forum message")
    operation.setdefault(
        "guardrails",
        [
            "sandbox course only: migrationtest Moodle course id 105330",
            "delete only the exact approved forum post/discussion target",
            "confirmation page must show the approved subject/ref when one is provided",
            "do not delete a whole forum activity/module",
        ],
    )
    return operation


def _browser_forum_reply_delete_operation(env: dict[str, str]) -> dict:
    operation = _browser_forum_post_delete_operation(env)
    operation.update(
        {
            "operation_kind": "browser_forum_reply_delete",
            "action": "delete_forum_reply",
            "target_reply_post_id": (
                operation.get("target_reply_post_id")
                or env.get("MOODLE_FORUM_REPLY_POST_ID", "")
            ),
            "target_reply_post_url": (
                operation.get("target_reply_post_url")
                or env.get("MOODLE_FORUM_REPLY_POST_URL", "")
            ),
            "target_reply_text": (
                operation.get("target_reply_text")
                or env.get("MOODLE_FORUM_REPLY_TARGET_TEXT", "")
            ),
            "target_reply_subject": (
                operation.get("target_reply_subject")
                or env.get("MOODLE_FORUM_REPLY_TARGET_SUBJECT", "")
            ),
            "reply_only": True,
        }
    )
    operation.setdefault(
        "target_object",
        operation.get("target_reply_subject")
        or operation.get("target_reply_text")
        or operation.get("target_reply_post_id")
        or "Forum reply",
    )
    operation["guardrails"] = [
        "sandbox course only: migrationtest Moodle course id 105330",
        "delete exactly one non-root reply in the approved discussion",
        "never delete the discussion root post or a whole forum activity/module",
        "verify the resolved reply post id is absent after one delete",
    ]
    return operation


def _browser_forum_post_reply_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    operation_id = env.get("MOODLE_OPERATION_ID", "SANDBOX-FORUM-POST-REPLY")
    source = _manifest_operation_from_env(env)
    operation = dict(source)
    operation.update(
        {
            "batch_id": batch_id,
            "approval_run_id": approval_run_id,
            "operation_id": operation_id,
            "operation_kind": "browser_forum_post_reply",
            "risk_level": operation.get("risk_level", "medium"),
            "target_course_url": env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL),
            "target_course_id": env.get("MOODLE_COURSE_ID", DEFAULT_COURSE_ID),
            "action": "reply_forum_post",
            "target_post_id": operation.get("target_post_id")
            or env.get("MOODLE_FORUM_POST_ID", ""),
            "target_post_url": operation.get("target_post_url")
            or env.get("MOODLE_FORUM_POST_URL", ""),
            "target_discussion_id": operation.get("target_discussion_id")
            or env.get("MOODLE_FORUM_DISCUSSION_ID", ""),
            "target_discussion_url": operation.get("target_discussion_url")
            or env.get("MOODLE_FORUM_DISCUSSION_URL", ""),
            "target_discussion_title": operation.get("target_discussion_title")
            or env.get("MOODLE_FORUM_DISCUSSION_TITLE", ""),
            "target_forum_name": operation.get("target_forum_name")
            or env.get("MOODLE_TARGET_FORUM_NAME", ""),
            "target_forum_id": operation.get("target_forum_id")
            or env.get("MOODLE_TARGET_FORUM_ID", ""),
            "target_forum_url": operation.get("target_forum_url")
            or env.get("MOODLE_TARGET_FORUM_URL", ""),
            "target_forum_cmid": operation.get("target_forum_cmid")
            or env.get("MOODLE_TARGET_FORUM_CMID", ""),
            "subject": operation.get("subject")
            or env.get("MOODLE_FORUM_POST_SUBJECT", ""),
            "message": operation.get("message")
            or operation.get("body")
            or env.get("MOODLE_FORUM_POST_MESSAGE", ""),
        }
    )
    operation.setdefault(
        "target_object",
        operation.get("target_discussion_title") or "Forum discussion",
    )
    operation.setdefault(
        "guardrails",
        [
            "sandbox course only: migrationtest Moodle course id 105330",
            "open only the exact approved discussion URL",
            "resolve exactly one Reply control for the approved root/parent post before any write",
            "submit exactly one reply with the approved subject and body",
            "do not create a new discussion or modify any existing post",
            "verify exactly one new matching post in the same discussion after submit",
        ],
    )
    return operation


def _browser_module_access_time_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    operation_id = env.get("MOODLE_OPERATION_ID", "SANDBOX-BROWSER-MODULE-ACCESS-TIME")
    source = _manifest_operation_from_env(env)
    operation = dict(source)
    params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
    target_module_id = str(
        operation.get("target_module_id")
        or params.get("target_module_id")
        or params.get("cmid")
        or env.get("MOODLE_TARGET_MODULE_ID", "")
    ).strip()
    target_title = (
        operation.get("target_object")
        or operation.get("approval_item")
        or params.get("resource_name")
        or params.get("title")
        or env.get("MOODLE_TARGET_TITLE", env.get("MOODLE_TEST_OBJECT_NAME", ""))
    )
    target_value = (
        operation.get("target_value")
        or params.get("restriction_time")
        or params.get("target_value")
        or env.get("MOODLE_TARGET_ACCESS_TIME", "")
    )
    access_time_action = str(
        operation.get("access_time_action")
        or params.get("access_time_action")
        or env.get("MOODLE_ACCESS_TIME_ACTION", "")
        or ("clear" if str(target_value).strip().lower() in {"clear", "remove", "delete", "unset"} else "set")
    ).strip().lower()
    operation.update(
        {
            "batch_id": batch_id,
            "approval_run_id": approval_run_id,
            "operation_id": operation_id,
            "operation_kind": "browser_module_access_time",
            "risk_level": operation.get("risk_level", "medium"),
            "target_course_url": env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL),
            "target_course_id": env.get("MOODLE_COURSE_ID", DEFAULT_COURSE_ID),
            "target_module_id": target_module_id,
            "target_object": target_title,
            "object_type": "Moodle course module",
            "action": "set_access_restriction_time",
            "access_time_action": access_time_action,
            "target_value": target_value,
            "restriction_time": target_value,
            "planned_at": _now(),
            "browser_execution": {
                "execute_approval_phrase": (
                    f"Approved Moodle set access time {batch_id} RUN={approval_run_id} OP={operation_id}"
                ),
                "revert_approval_phrase": "",
                "default_mode": approved_browser_write_policy(env),
            },
        }
    )
    operation.setdefault(
        "guardrails",
        [
            "browser automation access-time change is allowed only for migrationtest sandbox course id 105330",
            "change exactly one module resolved by APIM readback before opening settings",
            "set only the approved date access restriction and do not change visibility, title, files, grades, groups, completion, or notifications",
            "the current-run WhatsApp OK is the action-time approval; do not ask staff for a second approval phrase",
            "verify the saved availability/access restriction through Moodle settings readback after save",
        ],
    )
    return operation


def _browser_module_delete_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    operation_id = env.get("MOODLE_OPERATION_ID", "SANDBOX-BROWSER-MODULE-DELETE")
    source = _manifest_operation_from_env(env)
    operation = dict(source)
    target_module_id = str(operation.get("target_module_id") or env.get("MOODLE_TARGET_MODULE_ID", "")).strip()
    target_title = operation.get("target_object") or env.get("MOODLE_TARGET_TITLE", env.get("MOODLE_TEST_OBJECT_NAME", ""))
    modname = operation.get("modname") or env.get("MOODLE_TARGET_MODNAME", "")
    operation.update(
        {
            "batch_id": batch_id,
            "approval_run_id": approval_run_id,
            "operation_id": operation_id,
            "operation_kind": "browser_module_delete",
            "risk_level": operation.get("risk_level", "high"),
            "target_course_url": env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL),
            "target_course_id": env.get("MOODLE_COURSE_ID", DEFAULT_COURSE_ID),
            "target_module_id": target_module_id,
            "target_object": target_title,
            "object_type": "Moodle course module",
            "modname": modname,
            "action": "delete_module",
            "planned_at": _now(),
            "browser_execution": {
                "execute_approval_phrase": (
                    f"Approved Moodle delete module {batch_id} RUN={approval_run_id} OP={operation_id}"
                ),
                "revert_approval_phrase": "",
                "default_mode": approved_browser_write_policy(env),
            },
        }
    )
    operation.setdefault(
        "guardrails",
        [
            "browser automation module delete is allowed only for migrationtest sandbox course id 105330",
            "delete exactly one module whose cmid/title/type match APIM readback",
            "use Moodle's delete confirmation page for that exact cmid; do not browse or infer another target",
            "the current-run WhatsApp OK is the action-time approval; do not ask staff for a second approval phrase",
            "verify through read-only APIM after deletion that the cmid is absent",
        ],
    )
    return operation


def _course_refresh_batch_upload_operation(env: dict[str, str]) -> dict:
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    approval_run_id = env.get("APPROVAL_RUN_ID", "")
    operation_id = env.get("MOODLE_OPERATION_ID", "SANDBOX-COURSE-REFRESH-BATCH-UPLOAD")
    source = _manifest_operation_from_env(env)
    operation = dict(source)
    manifest_path = (
        operation.get("prepared_upload_manifest_path")
        or operation.get("upload_manifest_path")
        or env.get("MOODLE_UPLOAD_MANIFEST_PATH", "")
    )
    operation.update(
        {
            "batch_id": batch_id,
            "approval_run_id": approval_run_id,
            "operation_id": operation_id,
            "operation_kind": "course_refresh_batch_upload",
            "risk_level": operation.get("risk_level", "medium"),
            "target_course_url": env.get("MOODLE_COURSE_URL", DEFAULT_COURSE_URL),
            "target_course_id": env.get("MOODLE_COURSE_ID", DEFAULT_COURSE_ID),
            "action": "course_refresh_batch_upload",
            "target_object": operation.get("approval_item")
            or operation.get("target_object")
            or "Course file batch",
            "object_type": "Moodle File resource batch",
            "planned_at": _now(),
            "prepared_upload_manifest_path": manifest_path,
            "browser_execution": {
                "execute_approval_phrase": (
                    operation.get("approval_phrase")
                    or f"Approved Moodle course file batch {batch_id} RUN={approval_run_id} OP={operation_id}"
                ),
                "revert_approval_phrase": "",
                "default_mode": approved_browser_write_policy(env),
            },
        }
    )
    operation.setdefault(
        "guardrails",
        [
            "execute only the prepared upload manifest approved in the current WhatsApp run",
            "write only to migrationtest sandbox course id 105330",
            "do not regenerate file scope after approval",
            "do not upload files that failed date/visual audit",
            "do not use the legacy live DASE7137/IMSE7137 Moodle course",
        ],
    )
    return operation


def build_operation(env: dict[str, str]) -> dict:
    template_operation = _operation_from_template(env)
    if template_operation is not None:
        return template_operation
    kind = _operation_kind(env)
    if kind in {"announcement_api", "publish_announcement", "announcement", "forum_announcement"}:
        return _announcement_api_operation(env)
    if kind in {"group_api", "create_group", "moodle_group"}:
        return _group_api_operation(env)
    if kind in {"grouping_api", "create_grouping", "moodle_grouping"}:
        return _grouping_api_operation(env)
    if kind in {
        "browser_module_access_time",
        "browser_access_time",
        "browser_access_restriction_time",
        "browser_set_access_restriction_time",
    }:
        return _browser_module_access_time_operation(env)
    if kind in {"access_time", "access_restriction_time", "set_access_restriction_time"}:
        return _access_time_api_operation(env)
    if kind in {"direct_title_edit", "imse7137_d1_title_edit", "title_edit", "d1_title_edit"}:
        return _direct_title_edit_operation(env)
    if kind in {"file_api_upload", "api_file_upload", "core_files_upload", "upload_file_api"}:
        return _file_api_upload_operation(env)
    if kind in {
        "course_file_resource_upload",
        "upload_course_file_resource",
        "create_course_file_resource",
        "replace_course_file_resource",
    }:
        return _course_file_resource_upload_operation(env)
    if kind in {
        "browser_course_file_resource_upload",
        "browser_file_resource_upload",
        "create_browser_file_resource",
        "create_file_resource",
    }:
        return _browser_course_file_resource_upload_operation(env)
    if kind in {
        "browser_course_file_resource_delete",
        "browser_file_resource_delete",
        "delete_browser_file_resource",
        "delete_file_resource",
    }:
        return _browser_course_file_resource_delete_operation(env)
    if kind in {"browser_module_delete", "module_delete", "delete_module"}:
        return _browser_module_delete_operation(env)
    if kind in {"browser_group_signup_activity", "group_signup_activity", "create_group_signup_activity"}:
        return _browser_group_signup_activity_operation(env)
    if kind in {"browser_group_membership", "group_membership", "update_group_membership"}:
        return _browser_group_membership_operation(env)
    if kind in {"browser_assignment_activity", "assignment_activity", "create_assignment_activity"}:
        return _browser_assignment_activity_operation(env)
    if kind in {"browser_forum_activity", "forum_activity", "create_forum_activity"}:
        return _browser_forum_activity_operation(env)
    if kind in {
        "browser_forum_post_reply",
        "forum_post_reply",
        "reply_forum_post",
    }:
        return _browser_forum_post_reply_operation(env)
    if kind in {"browser_forum_post_delete", "forum_post_delete", "delete_forum_post"}:
        return _browser_forum_post_delete_operation(env)
    if kind in {
        "browser_forum_reply_delete",
        "forum_reply_delete",
        "delete_forum_reply",
    }:
        return _browser_forum_reply_delete_operation(env)
    if kind in {"browser_moodle_task", "moodle_browser_task", "generic_browser_task"}:
        return _browser_moodle_task_operation(env)
    if kind in {"browser_module_visibility", "module_visibility", "set_visibility"}:
        return _browser_module_visibility_operation(env)
    if kind in {"browser_section_rename", "section_rename", "rename_section"}:
        return _browser_section_rename_operation(env)
    if kind in {"course_refresh_batch_upload", "course_file_refresh_batch_upload", "refresh_batch_upload"}:
        return _course_refresh_batch_upload_operation(env)
    if kind in {"file_upload_replace", "upload_or_replace_file", "file_upload", "upload_replace"}:
        return _file_upload_replace_operation(env)
    if kind in {"hidden_marker", "marker", "label_marker"}:
        return _hidden_marker_operation(env)
    raise ValueError(
        f"Unsupported MOODLE_OPERATION_KIND={kind!r}. Use announcement_api, group_api, "
        "grouping_api, file_api_upload, course_file_resource_upload, browser_course_file_resource_upload, "
        "browser_course_file_resource_delete, browser_module_delete, browser_module_visibility, "
        "browser_module_access_time, browser_section_rename, browser_group_membership, browser_group_signup_activity, "
        "browser_assignment_activity, browser_forum_activity, "
        "browser_forum_post_reply, browser_forum_post_delete, browser_forum_reply_delete, "
        "browser_moodle_task, course_refresh_batch_upload, "
        "access_time, hidden_marker, file_upload_replace, or set "
        "MOODLE_OPERATION_TEMPLATE."
    )


def write_operation_package(operation: dict) -> Path:
    REPORTS.mkdir(exist_ok=True)
    suffix = f"_{operation['approval_run_id']}" if operation.get("approval_run_id") else ""
    index = int(operation.get("course_agent_operation_index") or 0)
    count = int(operation.get("course_agent_operation_count") or 0)
    item_suffix = f"_{index:02d}" if count > 1 and index > 0 else ""
    path = REPORTS / f"{operation['batch_id']}{suffix}_operation{item_suffix}.json"
    _write_json(path, operation)
    return path


def write_moodle_api_result(operation: dict, api_result: dict) -> Path:
    REPORTS.mkdir(exist_ok=True)
    suffix = f"_{operation['approval_run_id']}" if operation.get("approval_run_id") else ""
    index = int(operation.get("course_agent_operation_index") or 0)
    count = int(operation.get("course_agent_operation_count") or 0)
    item_suffix = f"_{index:02d}" if count > 1 and index > 0 else ""
    path = REPORTS / f"{operation['batch_id']}{suffix}_moodle_api_result{item_suffix}.json"
    _write_json(path, api_result)
    return path


def write_moodle_agent_checklist(operation: dict) -> Path:
    REPORTS.mkdir(exist_ok=True)
    suffix = f"_{operation['approval_run_id']}" if operation.get("approval_run_id") else ""
    path = REPORTS / f"{operation['batch_id']}{suffix}_moodle_agent_checklist.md"
    course_url = operation.get("target_course_url") or "<paste sandbox course URL here>"
    if operation.get("api_execution"):
        operation_summary = render_operation_summary(operation)
        api = operation.get("api_execution", {})
        content = f"""# Moodle APIM Operation Checklist

{operation_summary}

Target course URL: {course_url}
Target course ID: `{operation.get('target_course_id', DEFAULT_COURSE_ID)}`
Target section: `{operation.get('section_name', '')}`

## API Execution

- APIM URL: `{api.get('api_url', DEFAULT_API_URL)}`
- Subscription key env var: `{api.get('key_env', 'DASE7137_KEY')}`
- Moodle web service function: `{api.get('wsfunction', '')}`
- Action: `{operation.get('action', '')}`

## Guardrails

{chr(10).join(f"- {item}" for item in operation.get('guardrails', []))}

## Required Flow

1. Confirm WhatsApp approval for `{operation['batch_id']}` / `{operation.get('approval_run_id', '')}` exists in `approval_ledger.jsonl`.
2. Load the subscription key from `{api.get('key_env', 'DASE7137_KEY')}` without printing or writing it.
3. Call only the listed APIM function for the sandbox course.
4. Write the API result report under `reports/`.
5. If Moodle returns an exception or the operation is unsupported, report BLOCKED/FAIL and do not fall back to browser editing.

## Pass Criteria

- Operation targets migrationtest course `105330`.
- API result status is `moodle_api_action_verified`.
- No legacy real-course URL, course id, or module id is used.
- The subscription key is not included in reports, logs, or generated operation JSON.
"""
        path.write_text(content, encoding="utf-8")
        return path

    toolkit = render_toolkit_for_agent(operation)
    operation_summary = render_operation_summary(operation)
    browser_rules = render_browser_rules_for_agent()
    execution_outline = render_execution_outline_for_agent(operation)
    if operation.get("action") == "create_or_update_hidden_marker":
        marker_section = f"""
## Marker Payload

Use this exact marker text:

```text
{operation['marker_text']}
```
"""
    else:
        marker_section = ""
    content = f"""# Moodle Agent Smoke Test Checklist

{operation_summary}

Target course URL: {course_url}
Target section: `{operation.get('section_name', '')}`

## Guardrails

{chr(10).join(f"- {item}" for item in operation.get('guardrails', []))}

{toolkit}

{browser_rules}

{execution_outline}

{marker_section}

## Agent-Owned Moodle Touch

The agent may execute this section only after WhatsApp approval is recorded in
`approval_ledger.jsonl`. The current-run WhatsApp `OK <approval-code>` is the
action-time approval for the Moodle save; do not request a second staff approve.
This checklist is generated for the agent; Moodle is not opened automatically by
the approval server.

1. Select the tools above based on the operation action.
2. Attach to the existing Moodle browser session when required.
3. Run read-only prechecks before any Moodle save.
4. Execute only the approved write step.
5. Verify the state from Moodle after each write.
6. Stop immediately if any stop condition is hit.
7. Report PASS/FAIL with evidence paths or browser evidence.

## Pass Criteria

- WhatsApp approval for `{operation['batch_id']}` / `{operation.get('approval_run_id', '')}` exists in `approval_ledger.jsonl`.
- The selected agent tools match the operation action.
- All write actions are covered by the current-run WhatsApp action-time approval.
- Moodle final state matches the approved target.
- Guardrail fields remain unchanged.
"""
    path.write_text(content, encoding="utf-8")
    return path


def write_manual_checklist(operation: dict) -> Path:
    return write_moodle_agent_checklist(operation)


def write_moodle_browser_action_plan(operation: dict) -> Path:
    REPORTS.mkdir(exist_ok=True)
    suffix = f"_{operation['approval_run_id']}" if operation.get("approval_run_id") else ""
    path = REPORTS / f"{operation['batch_id']}{suffix}_moodle_browser_action.md"
    browser = operation.get("browser_execution", {})
    browser_rules = render_browser_rules_for_agent()
    execution_outline = render_execution_outline_for_agent(operation)
    content = f"""# Moodle Browser Action Plan

Batch: `{operation['batch_id']}`
Run: `{operation.get('approval_run_id', '')}`
Operation: `{operation.get('operation_id', '')}`
Risk: {operation.get('risk_level', 'unknown')}
Default browser mode: `{browser.get('default_mode', 'precheck_only')}`

## Target

- Course URL: {operation.get('target_course_url', '')}
- Settings URL: {operation.get('target_settings_url', '')}
- Object: `{operation.get('target_object', '')}`
- Object type: `{operation.get('object_type', '')}`
- Action: `{operation.get('action', '')}`

## Planned Change

- Original title: `{operation.get('original_title', '')}`
- Target title: `{operation.get('target_title', '')}`
- Expected visibility before and after save: `{operation.get('expected_visibility', '')}`

## Guardrails

{chr(10).join(f"- {item}" for item in operation.get('guardrails', []))}

{browser_rules}

{execution_outline}

## Default Precheck

This command opens Moodle and records current settings without saving:

```bash
{runtime_python_hint()} run_moodle_browser_action.py --operation reports/{operation['batch_id']}{suffix}_operation.json --precheck-only
```

## Execute After WhatsApp Approval

The WhatsApp approval for this RUN is the staff action-time approval. The phrase
below is a legacy local script guard derived from the same approved RUN; do not
ask staff for a second approval before using it.

```text
{browser.get('execute_approval_phrase', '')}
```

Command:

```bash
{browser.get('execute_command', '')}
```

## Revert After WhatsApp Approval

This is available for manual recovery. The `execute_only` demo flow does not run
the revert command automatically. In `execute_and_revert` mode, the same
WhatsApp approval covers the approved execute plus rollback sequence; the phrase
below is only the legacy local script guard.

```text
{browser.get('revert_approval_phrase', '')}
```

Command:

```bash
{browser.get('revert_command', '')}
```
"""
    path.write_text(content, encoding="utf-8")
    return path


def _operation_has_unresolved_placeholders(operation: dict) -> bool:
    unresolved_markers = (
        "TBD",
        "replace_with_",
        "choose_a_random_string",
        "YOUR_",
        "your_",
        "XXXXXXXX",
    )
    html_tags = (
        "a",
        "b",
        "blockquote",
        "br",
        "code",
        "div",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "i",
        "li",
        "ol",
        "p",
        "pre",
        "span",
        "strong",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
    )
    bracket_placeholder = re.compile(rf"<(?!/?(?:{'|'.join(html_tags)})\b)[^>]+>")

    def has_placeholder(value: object) -> bool:
        if isinstance(value, str):
            if any(marker in value for marker in unresolved_markers):
                return True
            return bool(bracket_placeholder.search(value))
        if isinstance(value, dict):
            return any(has_placeholder(item) for item in value.values())
        if isinstance(value, list):
            return any(has_placeholder(item) for item in value)
        return False

    return has_placeholder(operation)


def _rollback_progress_callbacks(operation: dict, action: str) -> str:
    if action == "upload_or_replace_file":
        return f"""{{"event":"moodle_rollback_save_started","run_id":"{operation.get('approval_run_id', '')}","operation_id":"{operation.get('operation_id', '')}","saves_count":1}}
{{"event":"moodle_rollback_save_completed","run_id":"{operation.get('approval_run_id', '')}","operation_id":"{operation.get('operation_id', '')}","saves_count":2,"file_area":"post-rollback file area"}}
{{"event":"moodle_rollback_verified","run_id":"{operation.get('approval_run_id', '')}","operation_id":"{operation.get('operation_id', '')}","saves_count":2,"final_visibility":"verified visibility","final_file_area":"verified rollback file area"}}"""
    return f"""{{"event":"moodle_rollback_save_started","run_id":"{operation.get('approval_run_id', '')}","operation_id":"{operation.get('operation_id', '')}","saves_count":1}}
{{"event":"moodle_rollback_save_completed","run_id":"{operation.get('approval_run_id', '')}","operation_id":"{operation.get('operation_id', '')}","saves_count":2,"title":"post-rollback title"}}
{{"event":"moodle_rollback_verified","run_id":"{operation.get('approval_run_id', '')}","operation_id":"{operation.get('operation_id', '')}","saves_count":2,"final_title":"verified final title","final_visibility":"verified visibility","final_access_restrictions":"verified availability JSON"}}"""


def _title_execute_only_policy(operation: dict) -> tuple[str, str, str, str, str]:
    execution_outline = """## Action Execution Outline

Action: `edit_assignment_title`

1. Precheck title, visibility, access restriction JSON, course id, and module id.
2. Save only the approved title change.
3. Verify the display page and settings page after the save.
4. Leave the target title in place for the visible demo; do not rollback.
"""
    retry_note = (
        "If a retry starts after the execute save, inspect the live Moodle state first. Do not repeat "
        "the execute save when the target title/change is already live; verify display and settings "
        "state, then post the result with the observed save count."
    )
    action_sequence = f"""7. For `edit_assignment_title`, execute only this sequence:
   - if the current title is already `{operation.get('target_title', '')}`, do not save again; verify display/settings state and report PASS with `saves_count=0` and `note="already_applied"`;
   - otherwise require current title `{operation.get('original_title', '')}` before any save;
   - set title from `{operation.get('original_title', '')}` to `{operation.get('target_title', '')}`;
   - save once;
   - verify display page and settings page;
   - leave the target title in place for the visible demo.
8. For other actions, stop before saving and report BLOCKED with `saves_count=0`.
9. Record exact save count. Expected save count is `1` for approved execute-only; otherwise `0`."""
    save_policy_note = (
        "The WhatsApp approval for this RUN is the action-time approval for exactly one Moodle save: "
        "one approved execute save that changes only the assignment title and leaves the target title "
        "visible for the live demo. Do not perform rollback in this run."
    )
    pass_rule = (
        "Use `PASS` only when the approved one-save Moodle title edit completed, no rollback ran, "
        "and the target state was verified, or when the target state was already applied and verified "
        "without another save."
    )
    return execution_outline, save_policy_note, retry_note, action_sequence, pass_rule


def _upload_execute_only_policy(operation: dict) -> tuple[str, str, str, str, str]:
    target_file = operation.get("target_file_path", "")
    target_name = operation.get("expected_uploaded_filename", _filename_hint(target_file))
    source_name = operation.get("expected_existing_filename", "")
    delete_first = bool(operation.get("delete_existing_before_upload"))
    delete_name = operation.get("delete_before_upload_filename") or target_name
    allow_visible = bool(operation.get("allow_visible_existing_resource_reupload"))
    visibility_rule = (
        "verify the target resource is the approved existing File resource; if it is visible, proceed only because `allow_visible_existing_resource_reupload` is true and the resource title/file match exactly"
        if allow_visible
        else "verify the target resource is hidden or staff-only"
    )
    delete_outline = (
        f"3. In the filemanager draft area, delete `{delete_name}` first, verify it is absent, then upload `{target_name}`.\n"
        if delete_first
        else f"3. Save only the approved upload/replace of `{target_name}` in the Moodle file resource content area.\n"
    )
    delete_sequence = (
        f"   - delete `{delete_name}` from the filemanager draft area first; do not save yet;\n"
        f"   - verify the draft file area no longer shows `{delete_name}` and record evidence;\n"
        if delete_first
        else ""
    )
    execution_outline = f"""## Action Execution Outline

Action: `upload_or_replace_file`

1. Precheck settings URL, object identity, visibility, access restriction JSON if present, and current file area contents.
2. Verify the approved local target file exists: `{target_file}`.
{delete_outline.rstrip()}
4. Verify the display page and settings page after the save, including final file name and unchanged visibility.
5. Return the same Moodle tab to the approved course page after verification and before the final PASS result.
6. Leave `{target_name}` in place for the visible demo; do not rollback.
"""
    retry_note = (
        "If a retry starts after the upload save, inspect the live Moodle file area first. Do not upload "
        "again when the target file is already present; verify file area and visibility, then post the result."
    )
    action_sequence = f"""7. For `upload_or_replace_file`, execute only this sequence:
   - {visibility_rule} and matches `{operation.get('target_object', '')}`;
   - verify the local file exists at `{target_file}` and its filename is `{target_name}`;
   - read the current Moodle file area; expected existing filename hint is `{source_name}`;
   - if `{target_name}` is already present and visibility is still hidden, do not save again; verify display/settings state and report PASS with `saves_count=0` and `note="already_applied"`;
{delete_sequence.rstrip()}
   - upload only `{target_name}` into the file resource content area after the delete-first step when enabled;
   - do not change title, visibility, dates, access restrictions, grades, notifications, or any unrelated files;
   - save once;
   - verify display page and settings page show `{target_name}` and unchanged visibility;
   - navigate the same Moodle tab back to the approved course page and verify the course page is visible;
   - leave the uploaded target file in place for the visible demo.
8. For other actions, stop before saving and report BLOCKED with `saves_count=0`.
9. Record exact save count. Expected save count is `1` for approved execute-only; otherwise `0`."""
    save_policy_note = (
        "The WhatsApp approval for this RUN is the action-time approval for exactly one Moodle save: "
        f"one approved upload/replace save for harmless test file `{target_name}`. Do not perform rollback in this run."
    )
    pass_rule = (
        "Use `PASS` only when the approved one-save Moodle upload completed, no rollback ran, "
        "the target file is present, visibility/guardrail fields were verified unchanged, and the Moodle tab "
        "was returned to the approved course page; or when the target file was already applied, verified without "
        "another save, and the Moodle tab was returned to the approved course page."
    )
    return execution_outline, save_policy_note, retry_note, action_sequence, pass_rule


def _create_file_resource_execute_only_policy(operation: dict) -> tuple[str, str, str, str, str]:
    target_file = operation.get("target_file_path", "")
    target_name = operation.get("expected_uploaded_filename", _filename_hint(target_file))
    resource_name = operation.get("resource_name") or operation.get("target_object", "")
    section = operation.get("section_name", "")
    visible = bool(operation.get("visible"))
    visibility_rule = (
        "set visibility/availability exactly to `Show on course page` because this RUN explicitly approved a visible resource"
        if visible
        else "set visibility/availability to hidden, `Hide on course page`, or the equivalent non-student-visible option"
    )
    expected_visibility = operation.get(
        "expected_visibility",
        "Show on course page" if visible else "Hide on course page",
    )
    intro = str(operation.get("intro", "")).strip()
    intro_step = (
        f"   - set the description/intro only to the approved value `{intro}`;\n"
        if intro
        else "   - leave description/intro empty unless Moodle requires a harmless default;\n"
    )
    execution_outline = f"""## Action Execution Outline

Action: `create_file_resource`

1. Precheck the approved course URL, course section, and local file path.
2. Verify no duplicate resource with title `{resource_name}` needs to be created.
3. Create one Moodle File resource in section `{section}`.
4. Upload only `{target_name}` from `{target_file}` and set visibility to `{expected_visibility}`.
5. Verify the course page, resource display page, settings page, uploaded filename, and visibility after the save.
6. Return the same Moodle tab to the approved course page after verification and before the final PASS result.
"""
    retry_note = (
        "If a retry starts after the create save, inspect the live Moodle course first. Do not create a "
        "duplicate when the approved resource title and file are already present; verify file area and "
        "visibility, then post the result."
    )
    action_sequence = f"""7. For `create_file_resource`, execute only this sequence:
   - open the approved course page `{operation.get('target_course_url', '')}` and find section `{section}`;
   - verify the local file exists at `{target_file}` and its filename is `{target_name}`;
   - search the course DOM for exact resource title `{resource_name}`;
   - if `{resource_name}` already exists and shows `{target_name}` with visibility `{expected_visibility}`, do not save again; verify display/settings state and report PASS with `saves_count=0` and `note="already_applied"`;
   - if `{resource_name}` already exists but file or visibility cannot be verified as approved, stop before saving and report BLOCKED with `saves_count=0`;
   - enable edit mode only if needed to expose the add-activity controls;
   - in section `{section}`, choose Add activity/resource and select Moodle `File`;
   - set the resource name/title exactly to `{resource_name}`;
{intro_step.rstrip()}
   - upload only `{target_name}` into the File resource content area;
   - {visibility_rule};
   - do not change dates, grades, access restrictions, groups, completion, notifications, or unrelated files;
   - save once;
   - verify the course page/resource/settings show `{resource_name}`, `{target_name}`, and visibility `{expected_visibility}`;
   - navigate the same Moodle tab back to the approved course page and verify the course page is visible;
   - leave the created File resource in place for the visible demo.
8. For other actions, stop before saving and report BLOCKED with `saves_count=0`.
9. Record exact save count. Expected save count is `1` for approved execute-only; otherwise `0`."""
    save_policy_note = (
        "The WhatsApp approval for this RUN is the action-time approval for exactly one Moodle save: "
        f"one approved create-File-resource save for harmless test file `{target_name}`. Do not delete "
        "or rollback the created resource in this run."
    )
    pass_rule = (
        "Use `PASS` only when the approved one-save Moodle File resource creation completed, no deletion/rollback ran, "
        "the approved file is present, visibility/guardrail fields were verified unchanged, and the Moodle tab "
        "was returned to the approved course page; or when the target resource was already applied, verified without "
        "another save, and the Moodle tab was returned to the approved course page."
    )
    return execution_outline, save_policy_note, retry_note, action_sequence, pass_rule


def _delete_file_resource_execute_only_policy(operation: dict) -> tuple[str, str, str, str, str]:
    cmid = str(operation.get("target_module_id", "")).strip()
    resource_name = operation.get("resource_name") or operation.get("target_object", "")
    resource_url = operation.get("target_resource_url", "")
    execution_outline = f"""## Action Execution Outline

Action: `delete_file_resource`

1. Read current sandbox Moodle course contents through APIM.
2. Verify cmid `{cmid}` is exactly one Moodle `resource` module titled `{resource_name}`.
3. Open the sandbox course in the agent-scoped Moodle browser.
4. Open Moodle's delete confirmation for cmid `{cmid}` and confirm only that one File resource.
5. Verify through APIM readback that cmid `{cmid}` is absent from course `105330`.
6. Return the Moodle tab to the approved sandbox course page before reporting PASS.
"""
    retry_note = (
        "If a retry starts after a delete confirmation, run APIM readback first. "
        "If the target cmid is already absent, report PASS with `saves_count=0` and "
        "`note=\"already_deleted\"`; do not delete another resource."
    )
    action_sequence = f"""7. For `delete_file_resource`, execute only this sequence:
   - verify the approved target course is `{operation.get('target_course_url', '')}`;
   - verify target cmid `{cmid}` and title `{resource_name}` match APIM readback;
   - optional display/resource URL hint: `{resource_url}`;
   - stop before deletion if the cmid is missing, target is not a File resource, title mismatches, or the target is ambiguous;
   - open Moodle delete URL only for cmid `{cmid}`;
   - confirm the Moodle delete prompt only when it names `{resource_name}`;
   - verify APIM no longer lists cmid `{cmid}` in sandbox course `105330`;
   - do not delete sections, other modules, groups, files inside other resources, or any legacy real-course object.
8. For other actions, stop before saving/deleting and report BLOCKED with `saves_count=0`.
9. Record exact delete count. Expected delete count is `1`, represented as `saves_count=1` for compatibility."""
    save_policy_note = (
        "The WhatsApp approval for this RUN is the action-time approval for exactly one Moodle delete: "
        f"delete File resource cmid `{cmid}` titled `{resource_name}` from sandbox course `105330`."
    )
    pass_rule = (
        "Use `PASS` only when the approved one-resource delete completed or the same cmid was already absent, "
        "and APIM readback verifies the resource is no longer in sandbox course 105330."
    )
    return execution_outline, save_policy_note, retry_note, action_sequence, pass_rule


def _title_execute_and_revert_sequence(operation: dict) -> str:
    return f"""7. For `edit_assignment_title`, execute only this sequence:
   - set title from `{operation.get('original_title', '')}` to `{operation.get('target_title', '')}`;
   - save once;
   - verify display page and settings page;
   - set title back to `{operation.get('original_title', '')}`;
   - save once;
   - verify final title, visibility, and access restrictions.
8. For other actions, execute only if the action execution outline and operation package provide exact target and rollback values.
9. Record exact save count. Expected save count is `2` only for approved execute+rollback; otherwise `0`."""


def _upload_execute_and_revert_sequence(operation: dict) -> str:
    target_file = operation.get("target_file_path", "")
    rollback_file = operation.get("rollback_file_path", "")
    target_name = operation.get("expected_uploaded_filename", _filename_hint(target_file))
    rollback_name = operation.get("expected_rollback_filename", _filename_hint(rollback_file))
    delete_first = bool(operation.get("delete_existing_before_upload"))
    delete_name = operation.get("delete_before_upload_filename") or target_name
    allow_visible = bool(operation.get("allow_visible_existing_resource_reupload"))
    visibility_rule = (
        "verify the target resource is the approved existing File resource; if it is visible, proceed only because `allow_visible_existing_resource_reupload` is true and the resource title/file match exactly"
        if allow_visible
        else "verify the target resource is hidden or staff-only"
    )
    delete_sequence = (
        f"   - delete `{delete_name}` from the filemanager draft area first and verify it is absent before uploading;\n"
        if delete_first
        else ""
    )
    return f"""7. For `upload_or_replace_file`, execute only this sequence:
   - {visibility_rule} and matches `{operation.get('target_object', '')}`;
   - verify both local files exist: target `{target_file}` and rollback `{rollback_file}`;
{delete_sequence.rstrip()}
   - upload or replace only `{target_name}` in the file resource content area;
   - save once;
   - verify display page and settings page show `{target_name}` and unchanged hidden visibility;
   - restore the approved rollback file state using `{rollback_name}` from `{rollback_file}`;
   - save once;
   - verify the final file area is back to `{rollback_name}` and visibility/guardrail fields are unchanged.
8. For other actions, execute only if the action execution outline and operation package provide exact target and rollback values.
9. Record exact save count. Expected save count is `2` only for approved execute+rollback; otherwise `0`."""


def _browser_task_policy(operation: dict, write_policy: str, browser: dict) -> dict[str, str]:
    action = operation.get("action", "")
    browser_action_phrases = "\n".join(
        phrase
        for phrase in (
            browser.get("execute_approval_phrase", ""),
            browser.get("revert_approval_phrase", ""),
        )
        if phrase
    )
    if write_policy == "execute_only":
        if action == "upload_or_replace_file":
            execution_outline, save_policy_note, retry_note, action_sequence, pass_rule = _upload_execute_only_policy(
                operation
            )
        elif action == "create_file_resource":
            execution_outline, save_policy_note, retry_note, action_sequence, pass_rule = _create_file_resource_execute_only_policy(
                operation
            )
        elif action == "delete_file_resource":
            execution_outline, save_policy_note, retry_note, action_sequence, pass_rule = _delete_file_resource_execute_only_policy(
                operation
            )
        else:
            execution_outline, save_policy_note, retry_note, action_sequence, pass_rule = _title_execute_only_policy(
                operation
            )
        return {
            "execution_outline": execution_outline,
            "save_policy_note": save_policy_note,
            "retry_note": retry_note,
            "allowed_write_step": (
                "5. If `write_policy` is not `execute_only`, stop before saving and report BLOCKED with `saves_count=0`."
            ),
            "action_sequence": action_sequence,
            "pass_rule": pass_rule,
            "rollback_progress_callbacks": "",
            "browser_action_phrases": browser.get("execute_approval_phrase", ""),
        }
    if write_policy == "execute_and_revert":
        if action == "create_file_resource":
            return {
                "execution_outline": render_execution_outline_for_agent(operation),
                "save_policy_note": (
                    "Creating a Moodle File resource is not treated as safely reversible by this workflow. "
                    "Stop before saving; rerun with execute_only only when leaving the created sandbox resource "
                    "in place is explicitly approved."
                ),
                "retry_note": (
                    "If a retry starts after any partial browser navigation, inspect only. Do not save or delete "
                    "a Moodle File resource in execute-and-revert mode."
                ),
                "allowed_write_step": (
                    "5. This action does not support execute_and_revert. Stop before saving and report BLOCKED with `saves_count=0`."
                ),
                "action_sequence": """7. For `create_file_resource`, do not save in execute-and-revert mode; report BLOCKED with `saves_count=0`.
8. Do not delete Moodle resources as rollback.
9. Record exact save count. Expected save count is `0`.""",
                "pass_rule": "Use `BLOCKED`; File resource creation can only PASS in execute-only mode.",
                "rollback_progress_callbacks": "",
                "browser_action_phrases": browser_action_phrases,
            }
        if action == "upload_or_replace_file":
            action_sequence = _upload_execute_and_revert_sequence(operation)
            retry_note = (
                "If a retry starts after a partial upload, inspect the live Moodle file area first. Do not "
                "repeat the execute upload when the target file is already live; perform rollback-only "
                "recovery to the approved rollback file state, verify it, then post the result."
            )
        else:
            action_sequence = _title_execute_and_revert_sequence(operation)
            retry_note = (
                "If a retry starts after a partial save, inspect the live Moodle state first. Do not repeat "
                "an execute save when the target title/change is already live; perform rollback-only recovery, "
                "verify the final state, then post the result."
            )
        return {
            "execution_outline": render_execution_outline_for_agent(operation),
            "save_policy_note": (
                "The WhatsApp approval for this RUN is the action-time approval for exactly two Moodle saves: "
                "one approved execute save and one approved rollback save. Do not perform any extra save."
            ),
            "retry_note": retry_note,
            "allowed_write_step": (
                "5. If `write_policy` is not `execute_and_revert`, stop before saving and report BLOCKED with `saves_count=0`."
            ),
            "action_sequence": action_sequence,
            "pass_rule": (
                "Use `PASS` only when the approved Moodle write and rollback both completed and final state was verified."
            ),
            "rollback_progress_callbacks": _rollback_progress_callbacks(operation, action),
            "browser_action_phrases": browser_action_phrases,
        }
    return {
        "execution_outline": render_execution_outline_for_agent(operation),
        "save_policy_note": (
            "This run is precheck-only. Do not save Moodle; report BLOCKED if a write would be required."
        ),
        "retry_note": (
            "If a retry starts after a partial save, inspect the live Moodle state first. Do not repeat "
            "any save; verify the observed state, then post BLOCKED or FAIL with exact evidence."
        ),
        "allowed_write_step": (
            "5. If `write_policy` is not `execute_only` or `execute_and_revert`, stop before saving and report BLOCKED with `saves_count=0`."
        ),
        "action_sequence": f"""7. For `{action}`, do not save in precheck-only mode; report BLOCKED with `saves_count=0`.
8. Stop before upload, delete, rollback, publish, or any other Moodle write.
9. Record exact save count. Expected save count is `0`.""",
        "pass_rule": "Use `PASS` only for approved write policies; precheck-only runs should report BLOCKED before saving.",
        "rollback_progress_callbacks": "",
        "browser_action_phrases": browser_action_phrases,
    }


def write_moodle_agent_browser_task(operation: dict, operation_path: Path, env: dict[str, str]) -> Path:
    REPORTS.mkdir(exist_ok=True)
    suffix = f"_{operation['approval_run_id']}" if operation.get("approval_run_id") else ""
    path = REPORTS / f"{operation['batch_id']}{suffix}_moodle_agent_browser_task.md"
    browser = operation.get("browser_execution", {})
    write_policy = approved_browser_write_policy(env)
    result_endpoint = env.get("MOODLE_AGENT_RESULT_ENDPOINT", "http://localhost:8787/agent/moodle-result")
    progress_endpoint = env.get("MOODLE_AGENT_PROGRESS_ENDPOINT", "http://localhost:8787/agent/moodle-progress")
    browser_profile = env.get("MOODLE_BROWSER_PROFILE", "user").strip() or "user"
    toolkit = render_toolkit_for_agent(operation)
    operation_summary = render_operation_summary(operation)
    browser_rules = render_browser_rules_for_agent(browser_profile)
    execution_outline = render_execution_outline_for_agent(operation)
    unresolved = _operation_has_unresolved_placeholders(operation)
    policy = _browser_task_policy(operation, write_policy, browser)
    execution_outline = policy["execution_outline"]
    save_policy_note = policy["save_policy_note"]
    retry_note = policy["retry_note"]
    allowed_write_step = policy["allowed_write_step"]
    action_sequence = policy["action_sequence"]
    pass_rule = policy["pass_rule"]
    rollback_progress_callbacks = policy["rollback_progress_callbacks"]
    browser_action_phrases = policy["browser_action_phrases"]
    unresolved_note = (
        "This operation still contains unresolved placeholder values. The agent must not save Moodle; "
        "it may only run read-only checks and then report BLOCKED."
        if unresolved
        else "No unresolved placeholder values were detected in the operation package."
    )
    if operation.get("action") == "create_file_resource":
        target_open_step = f"""3. Open the approved course page and prepare the exact section:
   - open `{operation.get('target_course_url', '')}`;
   - after `browser tabs`, prefer compact `browser evaluate` DOM searches over broad visual snapshots;
   - find the course section matching `{operation.get('section_name', '')}` or section number `{operation.get('sectionnum', '')}`;
   - search `document.querySelectorAll('a,button,[role="button"]')` plus nearby section text for an existing exact title `{operation.get('resource_name') or operation.get('target_object', '')}`;
   - if an existing exact-title File resource already has `{operation.get('expected_uploaded_filename', '')}` and approved visibility, verify it instead of creating a duplicate;
   - if an existing exact-title resource is ambiguous or mismatched, stop and report BLOCKED with `saves_count=0`;
   - enable edit mode only if needed to expose the Add activity/resource controls;
   - open the Add activity/resource flow for the exact section and choose Moodle `File`;
   - if the activity chooser or section-specific add control is ambiguous, stop and report BLOCKED with `saves_count=0`."""
    elif operation.get("action") == "delete_file_resource":
        target_open_step = f"""3. Open the approved course page and prepare the exact delete:
   - open `{operation.get('target_course_url', '')}`;
   - run/read APIM course contents before delete and confirm cmid `{operation.get('target_module_id', '')}` is exactly one Moodle File resource titled `{operation.get('resource_name') or operation.get('target_object', '')}`;
   - if the target cmid is already absent, verify it is absent and report PASS with `saves_count=0` and `note="already_deleted"`;
   - open Moodle delete confirmation only for cmid `{operation.get('target_module_id', '')}`;
   - confirm only if Moodle's page names the same File resource title;
   - if title, cmid, module type, course, or confirmation text does not match, stop and report BLOCKED with `saves_count=0`."""
    elif operation.get("allow_agent_target_discovery") and not operation.get("target_settings_url"):
        target_open_step = f"""3. No settings URL is supplied. Discover the target yourself:
   - open `{operation.get('target_discovery_start_url') or operation.get('target_course_url', '')}`;
   - after `browser tabs`, prefer a compact `browser evaluate` DOM search over a full course snapshot: collect same-page anchors/buttons whose text, href, aria-label, or closest section text contains `{operation.get('target_discovery_query', '')}`, `{operation.get('expected_existing_filename', '')}`, or `{operation.get('target_discovery_section', '')}`;
   - if the first snapshot only shows the course index, use evaluate on `document.querySelectorAll('a,button,[role="button"]')` to find the actual main-course resource link and editing controls;
   - find the section matching `{operation.get('target_discovery_section', '')}` when present;
   - find exactly one File resource whose visible title or linked file matches `{operation.get('target_discovery_query', '')}`;
   - for this demo, the expected visible resource title is `{operation.get('target_object', '')}`;
   - open that resource, then use Moodle's edit/settings controls to reach its settings page;
   - if edit mode is required to expose the settings menu, turn it on only for navigation and do not edit any other item;
   - if no exact match or more than one plausible match is found, stop and report BLOCKED with `saves_count=0`."""
    else:
        target_open_step = "3. Open or focus the operation settings URL."
    content = f"""# Codex Moodle Browser Agent Task

Batch: `{operation['batch_id']}`
Run: `{operation.get('approval_run_id', '')}`
Operation: `{operation.get('operation_id', '')}`
Write policy: `{write_policy}`
Result endpoint: `{result_endpoint}`
Progress endpoint: `{progress_endpoint}`

## Safety Gate

{save_policy_note}

The current-run WhatsApp `OK <approval-code>` is the Moodle write approval.
Do not ask staff for another execute approval, approval phrase, or confirmation
before carrying out the approved operation within the operation guardrails.

{unresolved_note}

If Moodle asks for login, credentials, cookies, local storage, passwords, or a new profile, stop and report BLOCKED.
The Moodle browser window/session is agent-scoped. Reuse the configured profile
and existing Moodle tab across approval cycles when available, and do not close
the browser after a single operation; the launcher owns shutdown.

## Operation Package

Use this run-specific operation JSON as the source of truth:

```text
{operation_path}
```

{operation_summary}

## Guardrails

{chr(10).join(f"- {item}" for item in operation.get('guardrails', []))}

{toolkit}

{browser_rules}

{execution_outline}

## Progress Callbacks

Post JSON progress to `{progress_endpoint}` as soon as each step is true. These
callbacks are required; they let the listener retry safely without repeating an
already completed save.

When using `curl` or an exec tool to POST progress/result callbacks, do not print
the response into the conversation. Redirect it to `/dev/null` or otherwise keep
only a tiny acknowledgement; use `/status` only when you explicitly need local
state for recovery.

```http
POST {progress_endpoint}
Content-Type: application/json

{{"event":"moodle_browser_started","run_id":"{operation.get('approval_run_id', '')}","operation_id":"{operation.get('operation_id', '')}","detail":"starting Moodle browser task"}}
{{"event":"moodle_tabs_claimed","run_id":"{operation.get('approval_run_id', '')}","operation_id":"{operation.get('operation_id', '')}","target_id":"raw CDP targetId","current_url":"current Moodle URL"}}
{{"event":"moodle_target_opened","run_id":"{operation.get('approval_run_id', '')}","operation_id":"{operation.get('operation_id', '')}","current_url":"settings URL is open or discovered settings URL is open"}}
{{"event":"moodle_precheck_started","run_id":"{operation.get('approval_run_id', '')}","operation_id":"{operation.get('operation_id', '')}"}}
{{"event":"moodle_precheck_passed","run_id":"{operation.get('approval_run_id', '')}","operation_id":"{operation.get('operation_id', '')}","title":"observed title","visibility":"observed visibility","access_restrictions":"observed availability JSON","file_area":"observed file area when relevant"}}
{{"event":"moodle_precheck_blocked","run_id":"{operation.get('approval_run_id', '')}","operation_id":"{operation.get('operation_id', '')}","detail":"why saving is not safe"}}
{{"event":"moodle_execute_save_started","run_id":"{operation.get('approval_run_id', '')}","operation_id":"{operation.get('operation_id', '')}","saves_count":0}}
{{"event":"moodle_execute_save_completed","run_id":"{operation.get('approval_run_id', '')}","operation_id":"{operation.get('operation_id', '')}","saves_count":1,"title":"post-save title when relevant","uploaded_file":"post-save uploaded file when relevant"}}
{{"event":"moodle_execute_verified","run_id":"{operation.get('approval_run_id', '')}","operation_id":"{operation.get('operation_id', '')}","saves_count":1,"title":"verified test title when relevant","file_area":"verified file area when relevant"}}
{{"event":"moodle_returned_to_course","run_id":"{operation.get('approval_run_id', '')}","operation_id":"{operation.get('operation_id', '')}","current_url":"approved Moodle course page URL"}}
{rollback_progress_callbacks}
{{"event":"moodle_result_post_started","run_id":"{operation.get('approval_run_id', '')}","operation_id":"{operation.get('operation_id', '')}","detail":"posting PASS/BLOCKED/FAIL"}}
```

{retry_note}

## Required Flow

1. Attach to the configured Moodle browser profile with `profile="{browser_profile}"`.
2. Use `browser tabs`; choose the live Moodle target by raw `targetId`.
{target_open_step}
4. Run read-only precheck and verify title, visibility, and access restrictions.
{allowed_write_step}
6. If any operation field contains unresolved placeholders, stop before saving and report BLOCKED with `saves_count=0`.
{action_sequence}
10. For successful PASS results, return the same Moodle tab to the approved course page and post `moodle_returned_to_course`.
11. Submit the result to the local endpoint or run the local recorder command.

## Compatibility Script Guard Phrases

These phrases are available only for legacy script-based browser actions as
local guards derived from the same approved WhatsApp RUN. They are not a second
staff approval request and must not be sent back to WhatsApp for another OK.

```text
{browser_action_phrases}
```

## Result POST Schema

POST JSON to `{result_endpoint}`:

```json
{{
  "agent_name": "{env.get('COURSE_AGENT_NAME', 'codex')}",
  "run_id": "{operation.get('approval_run_id', '')}",
  "operation_id": "{operation.get('operation_id', '')}",
  "status": "PASS | BLOCKED | FAIL",
  "saves_count": 0,
  "final_title": "",
  "final_visibility": "",
  "final_access_restrictions": "",
  "final_moodle_state": {{
    "title": "",
    "visibility": "",
    "access_restrictions": "",
    "course_url": "{operation.get('target_course_url', '')}",
    "final_browser_url": "",
    "files": [],
    "file_area": ""
  }},
  "evidence_report": "",
  "log": "",
  "note": ""
}}
```

{pass_rule}
Use `BLOCKED` when browser/session/precondition/approval data prevents safe execution.
Use `FAIL` when an attempted approved action failed and final state is known or evidence is captured.

Local recorder fallback:

```text
{runtime_python_hint()} record_moodle_agent_result.py --operation-id {operation.get('operation_id', '')} --status BLOCKED --saves-count 0 --run-id {operation.get('approval_run_id', '')} --note "Agent browser task stopped before saving."
```
"""
    path.write_text(content, encoding="utf-8")
    return path


def execute_after_approval(env: dict[str, str]) -> dict:
    mode = env.get("MOODLE_EXECUTION_MODE", "manual").strip().lower()
    batch_id = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")

    if mode == "skip":
        result = {
            "status": "moodle_skipped",
            "moodle_execution_mode": mode,
            "batch_id": batch_id,
            "reason": "Moodle-side testing disabled for this run.",
        }
        _append_ledger({"event": "moodle_skipped", "result": result})
        return result

    if mode in {"course_planning_approval", "planning_approval", "planning_only"}:
        result = {
            "status": "course_planning_approved",
            "moodle_execution_mode": mode,
            "batch_id": batch_id,
            "planning_package_path": env.get("PLANNING_PACKAGE_PATH", ""),
            "approval_run_id": env.get("APPROVAL_RUN_ID", ""),
            "reason": "Course planning package approved; Moodle writes still require separate action-time approval.",
        }
        _append_ledger({"event": "course_planning_approved", "result": result})
        return result

    operation = build_operation(env)
    operation_index = str(env.get("COURSE_AGENT_OPERATION_INDEX") or "").strip()
    operation_count = str(env.get("COURSE_AGENT_OPERATION_COUNT") or "").strip()
    if operation_index.isdigit():
        operation["course_agent_operation_index"] = int(operation_index)
    if operation_count.isdigit():
        operation["course_agent_operation_count"] = int(operation_count)
    operation_path = write_operation_package(operation)

    result = {
        "status": "approved",
        "moodle_execution_mode": mode,
        "operation_path": str(operation_path),
        "batch_id": operation["batch_id"],
    }

    if mode == "dry_run":
        result["status"] = "dry_run_written"
        _append_ledger({"event": "moodle_dry_run_written", "result": result})
        return result

    if mode in API_ACTION_MODES:
        if _operation_has_unresolved_placeholders(operation):
            api_result = {
                "status": "moodle_api_action_blocked",
                "ok": False,
                "moodle_execution_mode": mode,
                "operation_id": operation.get("operation_id", ""),
                "action": operation.get("action", ""),
                "target_course_id": operation.get("target_course_id", ""),
                "reason": "Operation contains unresolved placeholders; refusing Moodle API write.",
            }
        else:
            api_result = execute_api_operation(operation, env)
        api_result_path = write_moodle_api_result(operation, api_result)
        result.update(
            {
                "status": api_result.get("status", "moodle_api_action_failed"),
                "moodle_execution_mode": mode,
                "operation_id": operation.get("operation_id", ""),
                "action": operation.get("action", ""),
                "target_object": operation.get("target_object", ""),
                "target_course_url": operation.get("target_course_url", ""),
                "target_course_id": operation.get("target_course_id", ""),
                "api_result_path": str(api_result_path),
                "api_result": api_result,
            }
        )
        failure_reason = str(api_result.get("reason") or api_result.get("error") or "").strip()
        if failure_reason:
            result["reason"] = failure_reason
        _append_ledger({"event": "moodle_api_action_finished", "result": result})
        return result

    if mode in {"browser_action", "moodle_browser_action", "direct_browser_action"}:
        if operation.get("action") != "edit_assignment_title":
            raise ValueError(
                "MOODLE_EXECUTION_MODE=browser_action currently supports only "
                "MOODLE_OPERATION_KIND=direct_title_edit."
            )
        action_plan_path = write_moodle_browser_action_plan(operation)
        result.update(
            {
                "status": "moodle_browser_action_approval_pending",
                "browser_action_plan_path": str(action_plan_path),
                "operation_id": operation.get("operation_id", ""),
                "action": operation.get("action", ""),
                "target_object": operation.get("target_object", ""),
                "execute_approval_phrase": operation.get("browser_execution", {}).get(
                    "execute_approval_phrase", ""
                ),
                "revert_approval_phrase": operation.get("browser_execution", {}).get(
                    "revert_approval_phrase", ""
                ),
            }
        )
        _append_ledger({"event": "moodle_browser_action_plan_written", "result": result})
        return result

    if mode in AGENT_BROWSER_MODES:
        task_path = write_moodle_agent_browser_task(operation, operation_path, env)
        result.update(
            {
                "status": "moodle_agent_browser_action_pending",
                "moodle_agent_task_path": str(task_path),
                "moodle_agent_browser_task_path": str(task_path),
                "moodle_agent_result_endpoint": env.get(
                    "MOODLE_AGENT_RESULT_ENDPOINT", "http://localhost:8787/agent/moodle-result"
                ),
                "moodle_agent_progress_endpoint": env.get(
                    "MOODLE_AGENT_PROGRESS_ENDPOINT", "http://localhost:8787/agent/moodle-progress"
                ),
                "operation_id": operation.get("operation_id", ""),
                "action": operation.get("action", ""),
                "target_object": operation.get("target_object", ""),
                "write_policy": operation.get("browser_execution", {}).get(
                    "default_mode",
                    approved_browser_write_policy(env),
                ),
                "has_unresolved_placeholders": _operation_has_unresolved_placeholders(operation),
            }
        )
        _append_ledger({"event": "moodle_agent_browser_task_written", "result": result})
        return result

    if mode == "manual":
        mode = "agent_checklist"

    if mode != "agent_checklist":
        raise ValueError(
            "Unsupported MOODLE_EXECUTION_MODE="
            f"{mode!r}. Use skip, agent_checklist, manual, dry_run, browser_action, "
            "agent_browser_action, codex_browser_action, or api_action."
        )

    checklist_path = write_moodle_agent_checklist(operation)
    result.update(
        {
            "status": "moodle_agent_checklist_pending",
            "agent_checklist_path": str(checklist_path),
            "manual_checklist_path": str(checklist_path),
            "course_url": operation.get("target_course_url", ""),
        }
    )
    _append_ledger({"event": "moodle_agent_checklist_written", "result": result})

    if env.get("OPEN_BROWSER", "1") == "1" and operation.get("target_course_url"):
        webbrowser.open(operation["target_course_url"])
        _append_ledger({"event": "moodle_course_url_opened", "url": operation["target_course_url"]})

    return result
