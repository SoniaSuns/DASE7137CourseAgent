from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .pipeline import (
    apply_planner_structure,
    apply_tools_structure,
    apply_verifier_structure,
    validate_pipeline_artifact,
)
from .capabilities import validate_capability_operation
from course_agent_app.course_refresh_job import (
    OPERATION_KIND as COURSE_REFRESH_BATCH_UPLOAD_KIND,
    validate_refresh_job_shape,
)
from course_agent_app.file_refresh_preview import (
    OPERATION_KIND as REFRESH_FILE_PREVIEW_KIND,
    validate_refresh_file_preview_shape,
)
from course_agent_app.file_refresh_batch_preview import (
    FINALIZE_OPERATION_KIND as FINALIZE_FILE_BATCH_PREVIEW_KIND,
    OPERATION_KIND as REFRESH_FILE_BATCH_PREVIEW_KIND,
    validate_finalize_file_batch_preview_shape,
    validate_refresh_file_batch_preview_shape,
)
from course_agent_app.reference_pack_rebuild import (
    OPERATION_KIND as REBUILD_REFERENCE_PACK_KIND,
    validate_rebuild_reference_pack_shape,
)
from course_agent_app.semester_schedule_update import (
    OPERATION_KIND as UPDATE_SEMESTER_SCHEDULE_KIND,
    validate_update_semester_schedule_shape,
)
from course_agent_app.timeline_draft_review import (
    COMMIT_OPERATION_KIND as COMMIT_TIMELINE_DRAFT_KIND,
    REVISE_OPERATION_KIND as REVISE_TIMELINE_DRAFT_KIND,
    validate_commit_timeline_draft_shape,
    validate_revise_timeline_draft_shape,
)


SANDBOX_COURSE_ID = "105330"
SANDBOX_COURSE_URL = "https://migrationtest-moodle.hku.hk/course/view.php?id=105330"
ALLOWED_OPERATION_KINDS = {
    "announcement_api",
    "group_api",
    "grouping_api",
    "group_structure_readback",
    "forum_structure_readback",
    "file_api_upload",
    "course_file_resource_upload",
    "browser_course_file_resource_upload",
    "browser_course_file_resource_delete",
    "browser_module_delete",
    "browser_module_visibility",
    "browser_module_access_time",
    "browser_section_rename",
    "browser_group_membership",
    "browser_group_signup_activity",
    "browser_assignment_activity",
    "browser_forum_activity",
    "browser_forum_post_reply",
    "browser_forum_post_delete",
    "browser_forum_reply_delete",
    "browser_moodle_task",
    "course_refresh_batch_upload",
    "refresh_file_preview",
    "refresh_file_batch_preview",
    "finalize_file_batch_preview",
    "rebuild_reference_pack",
    "update_semester_schedule",
    "revise_timeline_draft",
    "commit_timeline_draft",
    "file_upload_replace",
    "direct_title_edit",
    "access_time",
    "hidden_marker",
}
HIGH_RISK_ACTIONS = {
    "delete",
    "delete_resource",
    "delete_file",
    "delete_file_resource",
    "delete_module",
    "clear_course",
    "show_visible",
    "make_visible",
    "publish_to_students",
    "notify_students",
    "change_course",
    "change_source_file",
}
SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
DELETE_REQUEST_WORDS = (
    "delete",
    "remove",
    "删",
    "删除",
    "移除",
    "去掉",
)
VISIBILITY_REQUEST_WORDS = (
    "visible",
    "show",
    "publish",
    "public",
    "显示",
    "公开",
    "发布",
    "可见",
)
GENERIC_BROWSER_STEP_TYPES = {
    "goto",
    "click",
    "fill",
    "select",
    "check",
    "uncheck",
    "press",
    "wait",
    "wait_for_text",
    "assert_text",
    "assert_no_text",
    "screenshot",
    "readback_course_contents",
}


def load_schema(name: str) -> dict[str, Any]:
    path = SCHEMA_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object.")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list.")
    return value


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def apply_intent_defaults(intent: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(intent)
    normalized.setdefault("target_course_id", SANDBOX_COURSE_ID)
    normalized.setdefault("target_course_url", SANDBOX_COURSE_URL)
    normalized.setdefault("allowed_operation_kinds", sorted(ALLOWED_OPERATION_KINDS))
    normalized.setdefault("risk_boundaries", [])
    normalized.setdefault("assumptions", [])
    normalized.setdefault("requires_reapproval_if", [])
    return apply_planner_structure(normalized)


def apply_manifest_defaults(manifest: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(manifest)
    normalized.setdefault("target_course_id", SANDBOX_COURSE_ID)
    normalized.setdefault("target_course_url", SANDBOX_COURSE_URL)
    return apply_tools_structure(normalized)


def apply_result_defaults(result: dict[str, Any]) -> dict[str, Any]:
    return apply_verifier_structure(dict(result))


def validate_course_task_intent(intent: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    intent = _require_object(intent, "course_task_intent")
    intent = apply_intent_defaults(intent)
    for field in ["run_id", "raw_task", "interpreted_intent", "target_course_id", "allowed_operation_kinds"]:
        if not _non_empty_string(intent.get(field)) and field != "allowed_operation_kinds":
            errors.append(f"course_task_intent.{field} must be a non-empty string")
    if intent.get("target_course_id") != SANDBOX_COURSE_ID:
        errors.append(f"course_task_intent.target_course_id must be sandbox course {SANDBOX_COURSE_ID}")
    course_url = str(intent.get("target_course_url", "")).strip()
    if course_url and ("migrationtest-moodle.hku.hk" not in course_url or f"id={SANDBOX_COURSE_ID}" not in course_url):
        errors.append("course_task_intent.target_course_url must point to sandbox course 105330")
    kinds = intent.get("allowed_operation_kinds", [])
    if not isinstance(kinds, list) or not kinds:
        errors.append("course_task_intent.allowed_operation_kinds must be a non-empty list")
    else:
        invalid = sorted(str(kind) for kind in kinds if str(kind) not in ALLOWED_OPERATION_KINDS)
        if invalid:
            errors.append(f"course_task_intent.allowed_operation_kinds has unsupported values: {invalid}")
    for list_field in ["risk_boundaries", "assumptions", "requires_reapproval_if"]:
        if list_field in intent and not isinstance(intent[list_field], list):
            errors.append(f"course_task_intent.{list_field} must be a list")
    errors.extend(validate_pipeline_artifact("intent", intent))
    return errors


def validate_course_operation_manifest(manifest: dict[str, Any], intent: dict[str, Any] | None = None) -> list[str]:
    errors: list[str] = []
    manifest = _require_object(manifest, "course_operation_manifest")
    manifest = apply_manifest_defaults(manifest)
    for field in ["run_id", "target_course_id", "operations"]:
        if field != "operations" and not _non_empty_string(manifest.get(field)):
            errors.append(f"course_operation_manifest.{field} must be a non-empty string")
    if manifest.get("target_course_id") != SANDBOX_COURSE_ID:
        errors.append(f"course_operation_manifest.target_course_id must be sandbox course {SANDBOX_COURSE_ID}")
    approval_policy = str(
        manifest.get("approval_policy")
        or (intent or {}).get("approval_policy")
        or ""
    ).strip().casefold()
    if approval_policy in {"per_write", "per_operation", "separate_each_write"}:
        errors.append(
            "course_operation_manifest requests unsupported per-write approval; "
            "one current-run WhatsApp approval must cover one exact bounded manifest"
        )
    operations = manifest.get("operations", [])
    if not isinstance(operations, list) or not operations:
        errors.append("course_operation_manifest.operations must be a non-empty list")
        return errors
    allowed = set(intent.get("allowed_operation_kinds", [])) if intent else ALLOWED_OPERATION_KINDS
    declared_runtime_refs: set[str] = set()
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            errors.append(f"operations[{index}] must be an object")
            continue
        kind = str(operation.get("operation_kind", "")).strip()
        if kind not in ALLOWED_OPERATION_KINDS:
            errors.append(f"operations[{index}].operation_kind is unsupported: {kind}")
        if kind and kind not in allowed:
            errors.append(f"operations[{index}].operation_kind {kind!r} is outside approved intent")
        course_id = str(operation.get("target_course_id") or manifest.get("target_course_id") or "")
        if course_id != SANDBOX_COURSE_ID:
            errors.append(f"operations[{index}].target_course_id must be sandbox course {SANDBOX_COURSE_ID}")
        mode = str(operation.get("execution_mode", "")).strip()
        if not mode:
            errors.append(f"operations[{index}].execution_mode must be set")
        errors.extend(validate_capability_operation(operation, index))
        if "env_overrides" in operation and not isinstance(operation["env_overrides"], dict):
            errors.append(f"operations[{index}].env_overrides must be an object")
        operation_refs = _declared_runtime_target_refs(operation)
        duplicate_refs = sorted(operation_refs & declared_runtime_refs)
        if duplicate_refs:
            errors.append(
                f"operations[{index}] declares runtime reference(s) already produced earlier: {duplicate_refs}"
            )
        target_ref = _runtime_target_ref(operation)
        runtime_target_id = str(operation.get("target_module_id") or operation.get("cmid") or "").strip()
        if (
            target_ref
            and runtime_target_id
            and kind in {
                "announcement_api",
                "direct_title_edit",
                "browser_module_access_time",
                "browser_module_visibility",
            }
        ):
            errors.append(
                f"operations[{index}] cannot combine target_ref with target_module_id/cmid"
            )
        if (
            target_ref
            and kind in {
                "announcement_api",
                "direct_title_edit",
                "browser_module_access_time",
                "browser_module_visibility",
            }
            and target_ref not in declared_runtime_refs
        ):
            errors.append(
                f"operations[{index}].target_ref {target_ref!r} does not reference exactly one prior producer"
            )
        if kind == "browser_course_file_resource_delete" and not _delete_operation_has_exact_target(operation):
            errors.append(
                f"operations[{index}].browser_course_file_resource_delete requires numeric target_module_id/cmid and exact resource title"
            )
        if kind == "browser_module_delete" and not _module_delete_operation_has_exact_target(operation):
            errors.append(f"operations[{index}].browser_module_delete requires numeric target_module_id/cmid and exact module title")
        if kind == "browser_module_visibility":
            errors.extend(_validate_module_visibility_operation(operation, index, declared_runtime_refs))
        if kind == "browser_module_access_time":
            errors.extend(_validate_runtime_target_operation(operation, index, declared_runtime_refs, kind))
        if kind == "direct_title_edit":
            errors.extend(_validate_runtime_target_operation(operation, index, declared_runtime_refs, kind))
            params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
            if not str(operation.get("target_title") or operation.get("new_title") or params.get("new_title") or "").strip():
                errors.append(f"operations[{index}].direct_title_edit requires new_title or target_title")
        if kind == "browser_section_rename":
            errors.extend(_validate_section_rename_operation(operation, index))
        if kind == "browser_moodle_task":
            errors.extend(_validate_generic_browser_task(operation, index))
        if kind == COURSE_REFRESH_BATCH_UPLOAD_KIND:
            errors.extend(f"operations[{index}].{error}" for error in validate_refresh_job_shape(operation))
        if kind == REFRESH_FILE_PREVIEW_KIND:
            errors.extend(f"operations[{index}].{error}" for error in validate_refresh_file_preview_shape(operation))
        if kind == REFRESH_FILE_BATCH_PREVIEW_KIND:
            errors.extend(
                f"operations[{index}].{error}"
                for error in validate_refresh_file_batch_preview_shape(operation)
            )
        if kind == FINALIZE_FILE_BATCH_PREVIEW_KIND:
            errors.extend(
                f"operations[{index}].{error}"
                for error in validate_finalize_file_batch_preview_shape(operation)
            )
        if kind == REBUILD_REFERENCE_PACK_KIND:
            errors.extend(f"operations[{index}].{error}" for error in validate_rebuild_reference_pack_shape(operation))
        if kind == UPDATE_SEMESTER_SCHEDULE_KIND:
            errors.extend(f"operations[{index}].{error}" for error in validate_update_semester_schedule_shape(operation))
        if kind == REVISE_TIMELINE_DRAFT_KIND:
            errors.extend(f"operations[{index}].{error}" for error in validate_revise_timeline_draft_shape(operation))
        if kind == COMMIT_TIMELINE_DRAFT_KIND:
            errors.extend(f"operations[{index}].{error}" for error in validate_commit_timeline_draft_shape(operation))
        declared_runtime_refs.update(operation_refs)
    errors.extend(validate_pipeline_artifact("manifest", manifest))
    return errors


def high_risk_reapproval_reasons(manifest: dict[str, Any], intent: dict[str, Any] | None = None) -> list[str]:
    """Compatibility hook.

    Current-run WhatsApp approval is the authority for the bounded manifest. Safety
    is enforced through manifest validation, sandbox checks, exact-target checks,
    and action-time OK-code approval, not by a second reapproval gate.
    """
    return []


def _delete_action_is_inside_approved_intent(operation: dict[str, Any], raw_task: str) -> bool:
    kind = str(operation.get("operation_kind", "")).strip()
    action = str(operation.get("action", "")).strip().lower()
    if not any(word in raw_task for word in DELETE_REQUEST_WORDS):
        return False
    if kind == "browser_course_file_resource_delete" and action in {
        "delete",
        "delete_resource",
        "delete_file",
        "delete_file_resource",
    }:
        return _delete_operation_has_exact_target(operation)
    if kind == "browser_module_delete" and action == "delete_module":
        return _module_delete_operation_has_exact_target(operation)
    return False


def _delete_operation_has_exact_target(operation: dict[str, Any]) -> bool:
    env_overrides = operation.get("env_overrides") if isinstance(operation.get("env_overrides"), dict) else {}
    target_id = str(
        operation.get("target_module_id")
        or operation.get("cmid")
        or operation.get("resource_cmid")
        or env_overrides.get("MOODLE_TARGET_MODULE_ID")
        or env_overrides.get("MOODLE_RESOURCE_CMID")
        or ""
    ).strip()
    target_title = str(
        operation.get("target_object")
        or operation.get("resource_name")
        or operation.get("title")
        or operation.get("name")
        or env_overrides.get("MOODLE_RESOURCE_NAME")
        or env_overrides.get("MOODLE_TARGET_TITLE")
        or env_overrides.get("MOODLE_TEST_OBJECT_NAME")
        or ""
    ).strip()
    return target_id.isdigit() and bool(target_title)


def _module_delete_operation_has_exact_target(operation: dict[str, Any]) -> bool:
    env_overrides = operation.get("env_overrides") if isinstance(operation.get("env_overrides"), dict) else {}
    target_id = str(
        operation.get("target_module_id")
        or operation.get("cmid")
        or env_overrides.get("MOODLE_TARGET_MODULE_ID")
        or ""
    ).strip()
    target_title = str(
        operation.get("target_object")
        or operation.get("title")
        or operation.get("name")
        or env_overrides.get("MOODLE_TARGET_TITLE")
        or env_overrides.get("MOODLE_TEST_OBJECT_NAME")
        or ""
    ).strip()
    return target_id.isdigit() and bool(target_title)


def _validate_generic_browser_task(operation: dict[str, Any], index: int) -> list[str]:
    errors: list[str] = []
    if not _generic_browser_fallback_is_explicit(operation):
        errors.append(
            f"operations[{index}].browser_moodle_task is a strict fallback and requires "
            "requires_browser_fallback/fallback_allowed plus fallback_reason"
        )
    goal = str(operation.get("browser_goal") or operation.get("goal") or operation.get("summary") or "").strip()
    if not goal:
        errors.append(f"operations[{index}].browser_moodle_task requires browser_goal or summary")
    steps = operation.get("browser_steps")
    if not isinstance(steps, list) or not steps:
        errors.append(f"operations[{index}].browser_moodle_task requires non-empty browser_steps")
    elif len(steps) > 80:
        errors.append(f"operations[{index}].browser_moodle_task has too many browser_steps")
    else:
        for step_index, step in enumerate(steps):
            if not isinstance(step, dict):
                errors.append(f"operations[{index}].browser_steps[{step_index}] must be an object")
                continue
            step_type = str(step.get("type") or step.get("action") or "").strip()
            if step_type not in GENERIC_BROWSER_STEP_TYPES:
                errors.append(f"operations[{index}].browser_steps[{step_index}] has unsupported type {step_type!r}")
            if step_type == "click":
                label = " ".join(
                    str(step.get(key) or "")
                    for key in ("name", "text", "label")
                ).casefold()
                if (
                    "add an activity or resource" in label
                    and not any(
                        step.get(key) not in (None, "")
                        for key in ("section", "section_name", "sectionnum", "section_id")
                    )
                ):
                    errors.append(
                        f"operations[{index}].browser_steps[{step_index}] Add activity click "
                        "requires section or sectionnum"
                    )
            if step_type == "goto":
                url = str(step.get("url", "")).strip()
                if not _is_sandbox_moodle_url(url):
                    errors.append(f"operations[{index}].browser_steps[{step_index}].url must stay on sandbox Moodle")
            for url in _collect_url_values(step):
                if not _is_sandbox_moodle_url(url):
                    errors.append(f"operations[{index}].browser_steps[{step_index}] contains non-sandbox URL {url!r}")
    verifications = operation.get("readback_expectations") or operation.get("verification_steps")
    if not isinstance(verifications, list) or not verifications:
        errors.append(f"operations[{index}].browser_moodle_task requires readback_expectations or verification_steps")
    return errors


def _validate_section_rename_operation(operation: dict[str, Any], index: int) -> list[str]:
    errors: list[str] = []
    section_name = str(operation.get("section_name") or "").strip()
    raw_sectionnum = (
        operation.get("sectionnum")
        if operation.get("sectionnum") is not None
        else operation.get("section_number")
    )
    sectionnum = "" if raw_sectionnum is None else str(raw_sectionnum).strip()
    if not section_name and not sectionnum:
        errors.append(f"operations[{index}].browser_section_rename requires section_name or sectionnum")
    if "new_title" not in operation and "target_title" not in operation:
        errors.append(f"operations[{index}].browser_section_rename requires new_title or target_title")
    return errors


def _validate_module_visibility_operation(
    operation: dict[str, Any],
    index: int,
    declared_runtime_refs: set[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    target_id = str(operation.get("target_module_id") or operation.get("cmid") or "").strip()
    if not target_id.isdigit() and not _operation_has_deferred_runtime_ref(operation, declared_runtime_refs):
        errors.append(f"operations[{index}].browser_module_visibility requires numeric target_module_id/cmid")
    if "visible" not in operation and "target_visible" not in operation:
        errors.append(f"operations[{index}].browser_module_visibility requires visible or target_visible")
    return errors


def _operation_has_deferred_runtime_ref(
    operation: dict[str, Any],
    declared_runtime_refs: set[str] | None = None,
) -> bool:
    ref = _runtime_target_ref(operation)
    return bool(ref and ref in (declared_runtime_refs or set()))


def _runtime_target_ref(operation: dict[str, Any]) -> str:
    params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
    return str(
        operation.get("target_ref")
        or operation.get("ref")
        or params.get("target_ref")
        or params.get("ref")
        or ""
    ).strip()


def _validate_runtime_target_operation(
    operation: dict[str, Any],
    index: int,
    declared_runtime_refs: set[str],
    kind: str,
) -> list[str]:
    target_id = str(operation.get("target_module_id") or operation.get("cmid") or "").strip()
    if target_id.isdigit() or _operation_has_deferred_runtime_ref(operation, declared_runtime_refs):
        return []
    return [f"operations[{index}].{kind} requires numeric target_module_id/cmid or a prior runtime target_ref"]


def _declared_runtime_target_refs(operation: dict[str, Any]) -> set[str]:
    capability = str(operation.get("capability") or "").strip()
    kind = str(operation.get("operation_kind") or "").strip()
    if capability not in {
        "create_assignment_activity",
        "create_forum_activity",
        "create_group_signup_activity",
        "upload_file_resource",
    } and kind not in {
        "browser_assignment_activity",
        "browser_forum_activity",
        "browser_group_signup_activity",
        "browser_course_file_resource_upload",
    }:
        return set()
    params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
    refs: set[str] = set()
    for key in ("post_create_reference", "result_reference", "created_reference", "reference", "ref"):
        value = str(operation.get(key) or params.get(key) or "").strip()
        if value:
            refs.add(value)
    return refs


def _generic_browser_fallback_is_explicit(operation: dict[str, Any]) -> bool:
    if operation.get("fallback_allowed") is True or operation.get("requires_browser_fallback") is True:
        reason = str(operation.get("fallback_reason") or operation.get("browser_fallback_reason") or "").strip()
        return bool(reason)
    return False


def _collect_url_values(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("http://") or text.startswith("https://"):
            urls.append(text)
    elif isinstance(value, dict):
        for item in value.values():
            urls.extend(_collect_url_values(item))
    elif isinstance(value, list):
        for item in value:
            urls.extend(_collect_url_values(item))
    return urls


def _is_sandbox_moodle_url(url: str) -> bool:
    if not url:
        return False
    if "migrationtest-moodle.hku.hk" not in url:
        return False
    if "moodle.hku.hk/course/view.php?id=118635" in url or "id=118635" in url:
        return False
    return True


def validate_course_agent_result(result: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    result = _require_object(result, "course_agent_result")
    result = apply_result_defaults(result)
    for field in ["run_id", "status", "summary"]:
        if not _non_empty_string(result.get(field)):
            errors.append(f"course_agent_result.{field} must be a non-empty string")
    if result.get("status") not in {"PASS", "BLOCKED", "FAIL", "PENDING_MOODLE_AGENT"}:
        errors.append("course_agent_result.status must be PASS, BLOCKED, FAIL, or PENDING_MOODLE_AGENT")
    if "items" in result:
        _require_list(result["items"], "course_agent_result.items")
    errors.extend(validate_pipeline_artifact("result", result))
    return errors
