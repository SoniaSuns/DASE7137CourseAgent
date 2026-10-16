from __future__ import annotations

from typing import Any


PIPELINE_ROLES = ("planner", "tools", "verifiers")
REQUIRED_VERIFIERS = (
    "sandbox_course_guard",
    "approval_boundary_guard",
    "moodle_readback_verifier",
)


ROLE_CONTRACTS: dict[str, dict[str, Any]] = {
    "planner": {
        "role": "planner",
        "owns": "Interpret the WhatsApp task, record assumptions, and produce a bounded intent and manifest.",
        "must_not": "Execute Moodle writes or bypass the project operation layer.",
    },
    "tools": {
        "role": "tools",
        "owns": "Execute only approved manifest operations through APIM or approved browser fallback adapters.",
        "must_not": "Invent unsupported Moodle write operations or target any non-sandbox course.",
    },
    "verifiers": {
        "role": "verifiers",
        "owns": "Read the current exact target once and compare it with the approved expected state.",
        "must_not": "Re-plan, infer a different target, repeat the write, or add a second verification workflow.",
    },
}

BROWSER_STEP_TYPES = (
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
)
TEXT_BROWSER_STEP_TYPES = {"wait_for_text", "assert_text", "assert_no_text"}
LOCATOR_BROWSER_STEP_TYPES = {"click", "fill", "select", "check", "uncheck"}


def role_contract_markdown(allowed_operation_kinds: list[str] | tuple[str, ...]) -> str:
    kinds = ", ".join(sorted(str(kind) for kind in allowed_operation_kinds))
    return f"""## Planner / Tools / Verifiers Contract

### Planner

- Read only the configured WhatsApp chat and Moodle/readback inputs.
- Produce `course_task_intent` first, then `course_operation_manifest`.
- Keep this as one bounded planner call; AutoAgent-style sub-roles are prompt organization only, not a separate runtime.
- Express intended Moodle writes as `capability` + typed `params` first; `operation_kind` is the resolved executor lane.
- Record assumptions instead of asking for low-risk clarifications.
- Describe high-risk impact plainly in the current WhatsApp approval; do not request a second reapproval.
- Treat operation kinds as execution lanes, not as a fixed user-intent list.

### Tools

- Tools are the project execution adapters, not free-form Moodle exploration.
- Do not invoke AutoAgent as a Moodle executor, browser controller, approval parser, or manifest runner.
- Available execution lanes for this run: {kinds}.
- Prefer APIM/API. Use stable Moodle tools/page objects for browser-backed capabilities.
- Use `browser_moodle_task` only as a strict fallback when no stable capability exists and the manifest visibly explains the fallback.
- Never write before WhatsApp approval is accepted by the local state machine.

### Verifiers

- Keep verification simple: one current target readback and one exact comparison
  with the approved expected state.
- Check the sandbox course guard, approval boundary, operation status, and exact
  readback fields only.
- Do not re-plan, fuzzy-match, switch targets, repeat a write, or start another
  approval/verification workflow.
- Report skipped, blocked, failed, or pending-browser items separately.
"""


def apply_planner_structure(intent: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(intent)
    normalized.setdefault("pipeline_stage", "planner")
    normalized.setdefault("planner", ROLE_CONTRACTS["planner"])
    normalized.setdefault(
        "tool_boundary",
        {
            "execution_owner": "tools",
            "write_gate": "whatsapp_ok_code_required",
            "allowed_operation_kinds": normalized.get("allowed_operation_kinds", []),
        },
    )
    verification_requirements = (
        dict(normalized.get("verification_requirements"))
        if isinstance(normalized.get("verification_requirements"), dict)
        else {}
    )
    verification_requirements.setdefault("owner", "verifiers")
    verification_requirements.setdefault("required", list(REQUIRED_VERIFIERS))
    verification_requirements.setdefault("readback_required", True)
    verification_requirements["method"] = "one_current_snapshot_exact_comparison"
    normalized["verification_requirements"] = verification_requirements
    return normalized


def apply_tools_structure(manifest: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(manifest)
    normalized.setdefault("pipeline_stage", "tools")
    if not isinstance(normalized.get("tools"), dict):
        normalized["tools"] = {
            **ROLE_CONTRACTS["tools"],
            "executor": "moodle_operations.execute_after_approval",
            "api_first": True,
        }
    verification_plan = (
        dict(normalized.get("verification_plan"))
        if isinstance(normalized.get("verification_plan"), dict)
        else {}
    )
    verification_plan.setdefault("owner", "verifiers")
    verification_plan.setdefault("required", list(REQUIRED_VERIFIERS))
    verification_plan.setdefault("readback_required", True)
    verification_plan["method"] = "one_current_snapshot_exact_comparison"
    normalized["verification_plan"] = verification_plan
    normalized.setdefault("readback_required", True)
    operations = normalized.get("operations")
    if isinstance(operations, list):
        normalized["operations"] = [apply_tool_operation_structure(item) for item in operations]
    return normalized


def apply_tool_operation_structure(operation: Any) -> Any:
    if not isinstance(operation, dict):
        return operation
    normalized = dict(operation)
    normalized = _normalize_browser_fallback_operation(normalized)
    if isinstance(normalized.get("browser_steps"), list):
        normalized["browser_steps"] = normalize_browser_steps(normalized["browser_steps"])
    operation_kind = str(normalized.get("operation_kind", "")).strip()
    execution_mode = str(normalized.get("execution_mode", "")).strip()
    normalized.setdefault("tool_name", operation_kind or execution_mode or "moodle_operation")
    normalized.setdefault(
        "tool_contract",
        {
            "owner": "tools",
            "capability": str(normalized.get("capability", "")).strip(),
            "operation_kind": operation_kind,
            "execution_mode": execution_mode,
            "uses_project_adapter": True,
        },
    )
    return normalized


def _normalize_browser_fallback_operation(operation: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(operation)
    capability = str(normalized.get("capability", "")).strip()
    operation_kind = str(normalized.get("operation_kind", "")).strip()
    if capability != "browser_moodle_task" and operation_kind != "browser_moodle_task":
        return normalized
    params = normalized.get("params") if isinstance(normalized.get("params"), dict) else {}
    normalized.setdefault("operation_kind", "browser_moodle_task")
    normalized.setdefault("execution_mode", "agent_browser_action")
    normalized.setdefault("requires_browser_fallback", True)
    for key in (
        "approval_action",
        "approval_item",
        "fallback_reason",
        "browser_fallback_reason",
        "browser_goal",
        "browser_steps",
        "readback_expectations",
        "verification_steps",
    ):
        if key not in normalized and key in params:
            normalized[key] = params[key]
    return normalized


def normalize_browser_steps(steps: Any) -> Any:
    if not isinstance(steps, list):
        return steps
    return [normalize_browser_step(step) for step in steps]


def normalize_browser_step(step: Any) -> Any:
    if not isinstance(step, dict):
        return step
    normalized = dict(step)
    step_type = str(normalized.get("type") or normalized.get("action") or "").strip()
    if not step_type:
        shorthand_type = next((key for key in BROWSER_STEP_TYPES if key in normalized), "")
        if shorthand_type:
            value = normalized.pop(shorthand_type)
            normalized["type"] = shorthand_type
            normalized = _merge_browser_step_shorthand(normalized, shorthand_type, value)
            step_type = shorthand_type
    elif "type" not in normalized:
        normalized["type"] = step_type
    return _normalize_canonical_browser_step(normalized, step_type)


def _merge_browser_step_shorthand(step: dict[str, Any], step_type: str, value: Any) -> dict[str, Any]:
    normalized = dict(step)
    if isinstance(value, dict):
        normalized.update(value)
    elif step_type == "goto":
        normalized["url"] = str(value or "")
    elif step_type in TEXT_BROWSER_STEP_TYPES:
        normalized["text"] = str(value or "")
    elif step_type == "wait":
        normalized["timeout_ms"] = _duration_to_milliseconds(value)
    elif step_type == "screenshot":
        if value not in (None, False, True):
            normalized["label"] = str(value)
    elif step_type == "readback_course_contents":
        pass
    elif step_type == "press":
        normalized["key"] = str(value or "")
    elif step_type in LOCATOR_BROWSER_STEP_TYPES:
        normalized["text"] = str(value or "")
    return normalized


def _normalize_canonical_browser_step(step: dict[str, Any], step_type: str) -> dict[str, Any]:
    normalized = dict(step)
    if step_type and normalized.get("type") != step_type:
        normalized["type"] = step_type
    if "timeout_seconds" in normalized and "timeout_ms" not in normalized:
        normalized["timeout_ms"] = _duration_to_milliseconds(normalized["timeout_seconds"])
    if step_type == "wait":
        for key in ("seconds", "timeout"):
            if key in normalized and "timeout_ms" not in normalized:
                normalized["timeout_ms"] = _duration_to_milliseconds(normalized[key])
        if "timeout_ms" not in normalized:
            normalized["timeout_ms"] = 1000
    return normalized


def _duration_to_milliseconds(value: Any) -> int:
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value or "").strip().lower()
        if text.endswith("ms"):
            try:
                return max(0, int(float(text[:-2].strip())))
            except ValueError:
                return 1000
        if text.endswith("s"):
            text = text[:-1].strip()
        try:
            number = float(text)
        except ValueError:
            return 1000
    if number <= 300:
        number *= 1000
    return max(0, int(number))


def apply_verifier_structure(result: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(result)
    normalized.setdefault("pipeline_stage", "verifiers")
    normalized.setdefault("verifiers", ROLE_CONTRACTS["verifiers"])
    verification = (
        dict(normalized.get("verification"))
        if isinstance(normalized.get("verification"), dict)
        else {}
    )
    verification.setdefault("owner", "verifiers")
    verification.setdefault("required", list(REQUIRED_VERIFIERS))
    verification.setdefault("readback_required", True)
    verification.setdefault("status_basis", "course_agent_result")
    verification["method"] = "one_current_snapshot_exact_comparison"
    normalized["verification"] = verification
    return normalized


def validate_pipeline_artifact(kind: str, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if kind == "intent":
        if payload.get("pipeline_stage") != "planner":
            errors.append("course_task_intent.pipeline_stage must be planner")
        if not isinstance(payload.get("planner"), dict):
            errors.append("course_task_intent.planner must be an object")
        if not isinstance(payload.get("tool_boundary"), dict):
            errors.append("course_task_intent.tool_boundary must be an object")
        if not isinstance(payload.get("verification_requirements"), dict):
            errors.append("course_task_intent.verification_requirements must be an object")
    elif kind == "manifest":
        if payload.get("pipeline_stage") != "tools":
            errors.append("course_operation_manifest.pipeline_stage must be tools")
        if not isinstance(payload.get("tools"), dict):
            errors.append("course_operation_manifest.tools must be an object")
        if not isinstance(payload.get("verification_plan"), dict):
            errors.append("course_operation_manifest.verification_plan must be an object")
        for index, operation in enumerate(payload.get("operations", []) or []):
            if isinstance(operation, dict) and not str(operation.get("tool_name", "")).strip():
                errors.append(f"operations[{index}].tool_name must be set")
    elif kind == "result":
        if payload.get("pipeline_stage") != "verifiers":
            errors.append("course_agent_result.pipeline_stage must be verifiers")
        if not isinstance(payload.get("verifiers"), dict):
            errors.append("course_agent_result.verifiers must be an object")
        if not isinstance(payload.get("verification"), dict):
            errors.append("course_agent_result.verification must be an object")
    else:
        errors.append(f"Unknown pipeline artifact kind: {kind}")
    return errors


def summarize_execution_verification(results: list[dict[str, Any]], aggregate_status: str) -> dict[str, Any]:
    blocked_or_failed = [
        {
            "operation_id": result.get("operation_id", ""),
            "status": result.get("status", ""),
            "course_agent_operation_index": result.get("course_agent_operation_index", ""),
        }
        for result in results
        if str(result.get("status", "")).endswith("_blocked") or str(result.get("status", "")).endswith("_failed")
    ]
    failures = [
        str(result.get("reason") or result.get("error") or result.get("status") or "Operation failed")
        for result in results
        if str(result.get("status", "")).endswith("_blocked")
        or str(result.get("status", "")).endswith("_failed")
    ]
    skipped = [
        {
            "operation_id": result.get("operation_id", ""),
            "status": result.get("status", ""),
            "course_agent_operation_index": result.get("course_agent_operation_index", ""),
            "reason": result.get("reason", ""),
        }
        for result in results
        if str(result.get("status", "")).endswith("_skipped")
    ]
    return {
        "owner": "verifiers",
        "required": list(REQUIRED_VERIFIERS),
        "readback_required": True,
        "aggregate_status": aggregate_status,
        "passed": aggregate_status == "course_agent_operations_verified",
        "blocked_or_failed": blocked_or_failed,
        "failures": failures,
        "skipped": skipped,
    }
