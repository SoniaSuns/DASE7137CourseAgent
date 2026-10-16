from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import tempfile
import uuid
import urllib.error
import urllib.request
from html import escape
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# The shared-Chrome entry uses this durable authority directly in one process.
# The legacy HTTP workflow below remains available to the original entry.
from chrome_approval import ChromeApproval

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moodle_operations import build_operation, execute_after_approval, normalize_write_policy  # noqa: E402
from course_agent import (  # noqa: E402
    ALLOWED_OPERATION_KINDS,
    SANDBOX_COURSE_ID,
    SANDBOX_COURSE_URL,
    apply_intent_defaults,
    apply_manifest_defaults,
    apply_result_defaults,
    capability_specs,
    unsupported_legacy_env_warnings,
    role_contract_markdown,
    summarize_execution_verification,
    validate_course_agent_result,
    validate_course_operation_manifest,
    validate_course_task_intent,
)
from whatsapp_approval import (  # noqa: E402
    approval_reply_commands,
    format_approval_request,
    parse_approval_command,
)
from operator_console import (  # noqa: E402
    ConsoleError,
    build_dashboard_state,
    dashboard_html,
    execute_approved_manifest,
    prepare_action_approval,
    prepare_staff_package,
    validate_manifest_approval,
)


ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "flow_state.json"
LEDGER_PATH = ROOT / "approval_ledger.jsonl"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    env_path = ROOT / ".env"
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env.setdefault(key.strip(), value.strip())
    env.setdefault("BATCH_ID", "SMOKE-MOODLE-WA-001")
    env.setdefault("APPROVAL_THRESHOLD", "1")
    env.setdefault("GRAPH_API_VERSION", "v23.0")
    env.setdefault("MOODLE_EXECUTION_MODE", "agent_checklist")
    env.setdefault("WHATSAPP_MODE", "codex_agent_web")
    env.setdefault("PORT", "8787")
    env.setdefault("COURSE_AGENT_WHATSAPP_CHAT_NAME", "CCHU9056 Test Approval")
    env.setdefault("COURSE_AGENT_APPROVER_NAME", "Codex WhatsApp Approver")
    env.setdefault("COURSE_AGENT_NAME", "codex")
    env.setdefault("CODEX_MODEL", "gpt-5.5")
    env.setdefault("CODEX_AGENT_TIMEOUT", "900")
    env.setdefault("CODEX_REPLY_POLL_SECONDS", "8")
    env.setdefault("CODEX_REPLY_MAX_POLLS", "18")
    env.setdefault("CODEX_BROWSER_PROFILE", "codex")
    env.setdefault("AGENT_REQUIRE_INCOMING_REPLY", "1")
    env.setdefault("AGENT_SINGLE_ACCOUNT_TEST_MODE", "0")
    env.setdefault("REQUIRE_RUN_ID_IN_APPROVAL", "1")
    env.setdefault("COURSE_AGENT_APPROVAL_MESSAGE_FORMAT", "boxed_ascii")
    env.setdefault("COURSE_AGENT_FREE_TASK_MODE", "1")
    return env


ENV = load_env()


def whatsapp_mode() -> str:
    mode = ENV.get("WHATSAPP_MODE", "codex_agent_web").strip().lower()
    if mode in {
        "codex_agent",
        "codex_web",
        "course_agent_web",
        "whatsapp_web",
        "whatsapp_web_automation",
    }:
        return "codex_agent_web"
    return mode


def base_url() -> str:
    configured = ENV.get("LOCAL_BASE_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    return f"http://localhost:{ENV.get('PORT', '8787')}"


def course_agent_name() -> str:
    return ENV.get("COURSE_AGENT_NAME", "codex")


def course_agent_chat_name() -> str:
    return ENV.get("COURSE_AGENT_WHATSAPP_CHAT_NAME", "")


def course_agent_approver_name() -> str:
    return ENV.get("COURSE_AGENT_APPROVER_NAME", "")


def append_ledger(event: dict) -> None:
    with LEDGER_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": now(), **event}, ensure_ascii=False) + "\n")


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%H%M%S")
    return f"RUN-{stamp}-{uuid.uuid4().hex[:4].upper()}"


def new_approval_code() -> str:
    return f"{int(uuid.uuid4().hex[:8], 16) % 1_000_000:06d}"


def approval_code_from_run_id(run_id: str) -> str:
    return f"{int(uuid.uuid5(uuid.NAMESPACE_URL, run_id).hex[:8], 16) % 1_000_000:06d}"


def initial_state(run_id: str | None = None) -> dict:
    generated_run_id = run_id or new_run_id()
    approval_code = ""
    if generated_run_id:
        approval_code = new_approval_code() if run_id is None else approval_code_from_run_id(generated_run_id)
    return {
        "batch_id": ENV["BATCH_ID"],
        "run_id": generated_run_id,
        "approval_code": approval_code,
        "status": "initialized",
        "approvals": [],
        "rejections": [],
    }


def read_state() -> dict:
    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        state.setdefault("run_id", "")
        state.setdefault("approval_code", approval_code_from_run_id(state["run_id"]) if state.get("run_id") else "")
        return state
    return initial_state("")


def write_state(state: dict) -> None:
    payload = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=STATE_PATH.parent,
        prefix=f".{STATE_PATH.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)
    temp_path.replace(STATE_PATH)


WHATSAPP_PROGRESS_EVENT_TO_STATUS = {
    "agent_app_starting": "course_agent_app_starting",
    "agent_task_created": "agent_whatsapp_task_created",
    "whatsapp_window_opening": "agent_whatsapp_window_opening",
    "whatsapp_ready_waiting": "agent_whatsapp_ready_waiting",
    "whatsapp_ready": "agent_whatsapp_ready",
    "whatsapp_identity_ready": "agent_whatsapp_identity_ready",
    "whatsapp_identity_degraded": "agent_whatsapp_identity_degraded",
    "moodle_window_opening": "agent_moodle_window_opening",
    "moodle_ready_waiting": "agent_moodle_ready_waiting",
    "moodle_login_required": "agent_moodle_login_required",
    "moodle_ready": "agent_moodle_ready",
    "moodle_ready_blocked": "agent_moodle_ready_blocked",
    "agent_runtime_ready": "agent_runtime_ready",
    "lifecycle_notice_send_started": "agent_lifecycle_notice_sending",
    "lifecycle_notice_sent": "agent_lifecycle_notice_sent",
    "lifecycle_notice_send_failed": "agent_lifecycle_notice_send_failed",
    "staff_prompt_wait_started": "agent_waiting_for_staff_prompt",
    "staff_prompt_received": "agent_staff_prompt_received",
    "reference_attachment_collection_requested": "agent_reference_attachment_collection_requested",
    "reference_attachment_collection_completed": "agent_reference_attachment_collection_completed",
    "group_structure_readback_started": "agent_group_structure_readback_started",
    "group_structure_readback_completed": "agent_group_structure_readback_completed",
    "forum_structure_readback_started": "agent_forum_structure_readback_started",
    "forum_structure_readback_completed": "agent_forum_structure_readback_completed",
    "course_agent_shutdown_requested": "agent_shutdown_requested",
    "approval_request_send_started": "agent_whatsapp_sending_request",
    "whatsapp_opened": "agent_whatsapp_opened",
    "approval_chat_opened": "agent_whatsapp_chat_opened",
    "approval_request_draft_ready": "agent_whatsapp_draft_ready",
    "approval_request_sent": "agent_whatsapp_sent_waiting_for_reply",
    "approval_request_send_failed": "agent_whatsapp_send_failed",
    "approval_report_send_failed": "agent_whatsapp_report_send_failed",
    "approval_reply_observed": "agent_whatsapp_reply_observed",
    "approval_report_posted": "agent_whatsapp_report_posted",
    "agent_timeout_before_send": "agent_timeout_before_send",
    "agent_timeout_after_send_waiting_reply": "agent_timeout_after_send_waiting_reply",
    "agent_timeout_after_reply_observed": "agent_timeout_after_reply_observed",
    "agent_timeout_needs_human_check": "agent_timeout_needs_human_check",
}
WHATSAPP_TOP_LEVEL_STATES = {
    "initialized",
    "course_task_agent_created",
    "course_task_intent_ready",
    "course_operation_manifest_ready",
    "agent_whatsapp_waiting_for_reply",  # legacy state kept so in-flight runs can still move forward.
    *WHATSAPP_PROGRESS_EVENT_TO_STATUS.values(),
}
WHATSAPP_NO_TOP_LEVEL_EVENTS = {"approval_report_posted"}
WHATSAPP_TERMINAL_PROGRESS_EVENTS = {
    "approval_request_send_failed",
    "approval_report_send_failed",
    "agent_timeout_needs_human_check",
}
NEGATIVE_DECISION_STATUSES = {"rejected"}
START_ENV_OVERRIDE_KEYS = {
    "BATCH_ID",
    "APPROVAL_THRESHOLD",
    "APPROVAL_MESSAGE_FILE",
    "COURSE_AGENT_APPROVAL_MESSAGE_FORMAT",
    "COURSE_AGENT_FREE_TASK_MODE",
    "COURSE_AGENT_WHATSAPP_CHAT_NAME",
    "PLANNING_PACKAGE_PATH",
    "OPERATOR_CONSOLE_APPROVAL_KIND",
    "OPERATOR_CONSOLE_APPROVAL_PHRASE",
    "OPERATOR_CONSOLE_MANIFEST_PATH",
    "OPERATOR_CONSOLE_STAFF_CATEGORY",
}
START_ENV_OVERRIDE_PREFIXES = ("MOODLE_",)


def apply_start_env_overrides(overrides: dict | None) -> dict[str, str]:
    if not overrides:
        return {}
    if not isinstance(overrides, dict):
        raise ValueError("env_overrides must be a JSON object.")
    applied: dict[str, str] = {}
    for raw_key, raw_value in overrides.items():
        key = str(raw_key).strip()
        if not re.match(r"^[A-Z][A-Z0-9_]*$", key):
            raise ValueError(f"Invalid env override key: {key!r}.")
        if key not in START_ENV_OVERRIDE_KEYS and not key.startswith(START_ENV_OVERRIDE_PREFIXES):
            raise ValueError(f"Env override {key!r} is not allowed for approval restarts.")
        value = "" if raw_value is None else str(raw_value)
        ENV[key] = value
        applied[key] = value
    return applied


def handle_agent_progress(payload: dict) -> dict:
    state = read_state()
    event = str(payload.get("event", "")).strip()
    if event not in WHATSAPP_PROGRESS_EVENT_TO_STATUS:
        allowed = ", ".join(sorted(WHATSAPP_PROGRESS_EVENT_TO_STATUS))
        raise ValueError(f"Agent progress event must be one of: {allowed}.")

    current_run_id = state.get("run_id", "")
    run_id = str(payload.get("run_id", "")).strip() or current_run_id
    if current_run_id and run_id and run_id != current_run_id:
        raise ValueError(f"Agent progress run_id {run_id!r} does not match current run {current_run_id!r}.")
    if run_id and not current_run_id:
        state["run_id"] = run_id

    status = WHATSAPP_PROGRESS_EVENT_TO_STATUS[event]
    entry = {
        "event": event,
        "status": status,
        "run_id": run_id,
        "agent_name": payload.get("agent_name", course_agent_name()),
        "chat_name": payload.get("chat_name", course_agent_chat_name()),
        "detail": payload.get("detail", ""),
        "evidence": payload.get("evidence", ""),
        "attempt": payload.get("attempt", ""),
        "recorded_at": now(),
    }
    for key in (
        "target_id",
        "message_id",
        "observed_direction",
        "recovered",
        "whatsapp_requester",
        "whatsapp_root_message_id",
        "whatsapp_last_inbound_message_id",
        "whatsapp_active_workflow",
        "whatsapp_chat_type",
        "whatsapp_agent_display_name",
        "whatsapp_mention_ready",
        "whatsapp_authenticated",
        "whatsapp_degraded_reason",
        "whatsapp_provenance_confidence",
    ):
        if key in payload:
            entry[key] = payload[key]
            if key.startswith("whatsapp_"):
                state[key] = payload[key]

    state.setdefault("agent_progress", []).append(entry)
    state["agent_whatsapp_status"] = status
    state["agent_whatsapp_last_event"] = event
    state["agent_whatsapp_last_progress_at"] = entry["recorded_at"]

    if event == "approval_request_sent":
        state["approval_request_sent_at"] = entry["recorded_at"]
        state["approval_request_sent_evidence"] = entry.get("evidence", "")
    elif event == "approval_reply_observed":
        state["approval_reply_observed_at"] = entry["recorded_at"]
        state["approval_reply_observed_evidence"] = entry.get("evidence", "")
    elif event == "approval_request_send_failed":
        state["approval_request_send_failed_at"] = entry["recorded_at"]
        state["approval_request_send_failed_reason"] = entry.get("detail", "")
    elif event.startswith("agent_timeout"):
        state["agent_timeout_at"] = entry["recorded_at"]
        state["agent_timeout_event"] = event

    current_status = state.get("status", "")
    may_update_flow = (
        current_status in WHATSAPP_TOP_LEVEL_STATES
        or event in WHATSAPP_TERMINAL_PROGRESS_EVENTS
        or not current_status
    )
    if may_update_flow and event not in WHATSAPP_NO_TOP_LEVEL_EVENTS:
        state["status"] = status

    write_state(state)
    append_ledger({"event": "agent_whatsapp_progress", "progress": entry, "state_status": state.get("status", "")})
    return {"handled": True, "progress": entry, "state": state}


def _payload_for_current_run(payload: dict, label: str) -> tuple[dict, str]:
    state = read_state()
    current_run_id = state.get("run_id", "")
    run_id = str(payload.get("run_id", "")).strip() or current_run_id
    if current_run_id and run_id and run_id != current_run_id:
        raise ValueError(f"{label} run_id {run_id!r} does not match current run {current_run_id!r}.")
    if not run_id:
        raise ValueError(f"{label} must include run_id.")
    return state, run_id


def handle_course_task_intent(payload: dict) -> dict:
    state, run_id = _payload_for_current_run(payload, "course_task_intent")
    intent = apply_intent_defaults(dict(payload.get("course_task_intent") or payload))
    intent["run_id"] = str(intent.get("run_id") or run_id)
    errors = validate_course_task_intent(intent)
    if errors:
        raise ValueError("; ".join(errors))
    state["course_task_intent"] = intent
    state["status"] = "course_task_intent_ready"
    state["course_task_intent_recorded_at"] = now()
    write_state(state)
    append_ledger({"event": "course_task_intent_recorded", "intent": intent})
    return {"handled": True, "intent": intent, "state": state}


def handle_course_operation_manifest(payload: dict) -> dict:
    state, run_id = _payload_for_current_run(payload, "course_operation_manifest")
    intent = state.get("course_task_intent") or {}
    manifest = apply_manifest_defaults(dict(payload.get("course_operation_manifest") or payload))
    manifest["run_id"] = str(manifest.get("run_id") or run_id)
    manifest.setdefault("target_course_id", SANDBOX_COURSE_ID)
    manifest.setdefault("target_course_url", SANDBOX_COURSE_URL)
    errors = validate_course_operation_manifest(manifest, intent if isinstance(intent, dict) else None)
    if errors:
        raise ValueError("; ".join(errors))
    state["course_operation_manifest"] = manifest
    state["course_operation_manifest_recorded_at"] = now()
    state["status"] = "course_operation_manifest_ready"
    write_state(state)
    append_ledger({"event": "course_operation_manifest_recorded", "manifest": manifest})
    return {"handled": True, "ok": True, "manifest": manifest, "state": state}


def _first_text(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return re.sub(r"\s+", " ", text)
    return ""


def _operation_target_label(operation: dict, default: str = "Moodle item") -> str:
    env_overrides = operation.get("env_overrides") if isinstance(operation.get("env_overrides"), dict) else {}
    return _first_text(
        operation.get("resource_name"),
        operation.get("target_object"),
        operation.get("approval_item"),
        operation.get("group_name"),
        operation.get("grouping_name"),
        operation.get("subject"),
        env_overrides.get("MOODLE_RESOURCE_NAME"),
        env_overrides.get("MOODLE_TARGET_TITLE"),
        env_overrides.get("MOODLE_GROUP_NAME"),
        env_overrides.get("MOODLE_GROUPING_NAME"),
        operation.get("summary"),
        default,
    )


def _manifest_primary_target_label(manifest: dict) -> str:
    operations = manifest.get("operations") if isinstance(manifest.get("operations"), list) else []
    for operation in reversed(operations):
        if isinstance(operation, dict):
            label = _operation_target_label(operation, "")
            if label:
                return label
    return _first_text(
        manifest.get("target_object"),
        manifest.get("approval_item"),
        manifest.get("approval_target"),
        manifest.get("approval_boundary"),
        manifest.get("summary"),
    )


def _aggregate_target_label(labels: list[str]) -> str:
    unique = list(dict.fromkeys(label for label in labels if label))
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0]
    shown = ", ".join(unique[:4])
    if len(unique) > 4:
        shown = f"{shown}, +{len(unique) - 4} more"
    return f"{len(unique)} Moodle changes: {shown}"


def _verified_write_count(results: list[dict]) -> int:
    count = 0
    for result in results:
        status = str(result.get("status", "")).casefold()
        if result.get("ok") is True or status.endswith("_verified") or status == "pass":
            count += 1
    return count


def _aggregate_execution_result_fields(manifest: dict, results: list[dict], pending_result: dict | None) -> dict:
    operations = manifest.get("operations") if isinstance(manifest.get("operations"), list) else []
    labels: list[str] = []
    for index, result in enumerate(results):
        operation = operations[index] if index < len(operations) and isinstance(operations[index], dict) else {}
        labels.append(_first_text(result.get("target_object"), _operation_target_label(operation)))
    target_object = _aggregate_target_label(labels)
    if not pending_result and len(results) < len(operations):
        target_object = _first_text(_manifest_primary_target_label(manifest), target_object)
    failed_result = next(
        (
            result
            for result in results
            if str(result.get("status") or "").endswith(("_failed", "_blocked"))
        ),
        {},
    )
    failed_api_result = (
        failed_result.get("api_result")
        if isinstance(failed_result.get("api_result"), dict)
        else {}
    )
    failure_reason = _first_text(
        failed_result.get("reason"),
        failed_result.get("error"),
        failed_result.get("blocker"),
        failed_api_result.get("reason"),
        failed_api_result.get("error"),
    )
    return {
        "operation_id": pending_result.get("operation_id", "") if pending_result else _first_text(results[0].get("operation_id") if results else ""),
        "action": pending_result.get("action", "") if pending_result else _first_text(results[0].get("action") if results else ""),
        "target_object": pending_result.get("target_object", "") if pending_result else target_object,
        "saves_count": _verified_write_count(results),
        "failure_reason": failure_reason,
        "final_moodle_state": {
            "course_url": SANDBOX_COURSE_URL,
            "operation_count": len(results),
            "verified_count": _verified_write_count(results),
            "targets": labels,
            "results": results,
        },
    }


def _write_manifest_execution_evidence(state: dict, manifest: dict, results: list[dict]) -> str:
    reports = STATE_PATH.parent / "reports"
    reports.mkdir(exist_ok=True)
    batch_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(state.get("batch_id") or ENV["BATCH_ID"]))
    run_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(state.get("run_id") or "RUN"))
    path = reports / f"{batch_id}_{run_id}_moodle_operation_batch.json"
    payload = {
        "run_id": state.get("run_id", ""),
        "target_course_id": manifest.get("target_course_id", ""),
        "operation_count": len(manifest.get("operations", []) or []),
        "result_count": len(results),
        "operations": manifest.get("operations", []) or [],
        "results": results,
        "recorded_at": now(),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return str(path)


def execute_course_operation_manifest(state: dict) -> dict:
    manifest = state.get("course_operation_manifest")
    intent = state.get("course_task_intent") or {}
    if not isinstance(manifest, dict):
        return {
            "status": "course_agent_manifest_blocked",
            "ok": False,
            "reason": "No course_operation_manifest was registered before approval.",
        }
    manifest = apply_manifest_defaults(manifest)
    errors = validate_course_operation_manifest(manifest, intent if isinstance(intent, dict) else None)
    if errors:
        return {
            "status": "course_agent_manifest_blocked",
            "ok": False,
            "reason": "; ".join(errors),
        }

    results: list[dict] = []
    pending_result: dict | None = None
    operations = manifest.get("operations", []) or []
    for index, operation in enumerate(operations, start=1):
        op_env = dict(ENV)
        env_overrides = operation.get("env_overrides") if isinstance(operation.get("env_overrides"), dict) else {}
        op_env.update({str(key): "" if value is None else str(value) for key, value in env_overrides.items()})
        op_env["APPROVAL_RUN_ID"] = state.get("run_id", "")
        op_env["MOODLE_COURSE_ID"] = SANDBOX_COURSE_ID
        op_env["MOODLE_COURSE_URL"] = SANDBOX_COURSE_URL
        op_env["MOODLE_OPERATION_KIND"] = str(operation.get("operation_kind", op_env.get("MOODLE_OPERATION_KIND", "")))
        op_env["MOODLE_EXECUTION_MODE"] = str(operation.get("execution_mode", op_env.get("MOODLE_EXECUTION_MODE", "api_action")))
        op_env["MOODLE_OPERATION_ID"] = str(
            operation.get("operation_id")
            or env_overrides.get("MOODLE_OPERATION_ID")
            or f"COURSE-AGENT-{state.get('run_id', 'RUN')}-{index:02d}"
        )
        op_env["MOODLE_MANIFEST_OPERATION_JSON"] = json.dumps(operation, ensure_ascii=False)
        op_env["COURSE_AGENT_OPERATION_INDEX"] = str(index)
        op_env["COURSE_AGENT_OPERATION_COUNT"] = str(len(operations))
        result = execute_after_approval(
            {
                **op_env,
                "MOODLE_AGENT_RESULT_ENDPOINT": f"{base_url()}/agent/moodle-result",
                "MOODLE_AGENT_PROGRESS_ENDPOINT": f"{base_url()}/agent/moodle-progress",
            }
        )
        target_label = _operation_target_label(operation)
        if target_label:
            result["target_object"] = target_label
        result["course_agent_operation_index"] = index
        results.append(result)
        if result.get("status") == "moodle_agent_browser_action_pending":
            pending_result = result
            break
        if result.get("status", "").endswith("_failed") or result.get("status", "").endswith("_blocked"):
            failure_id = str(result.get("operation_id") or f"operation {index}")
            for skipped_index, skipped_operation in enumerate(operations[index:], start=index + 1):
                results.append(
                    {
                        "status": "course_agent_operation_skipped",
                        "ok": False,
                        "operation_id": str(
                            skipped_operation.get("operation_id")
                            or f"COURSE-AGENT-{state.get('run_id', 'RUN')}-{skipped_index:02d}"
                        ),
                        "action": skipped_operation.get("action", ""),
                        "target_object": _operation_target_label(skipped_operation),
                        "course_agent_operation_index": skipped_index,
                        "reason": f"Not executed because approved operation {failure_id} did not verify.",
                    }
                )
            break

    aggregate_status = "course_agent_operations_verified"
    if pending_result:
        aggregate_status = pending_result.get("status", "moodle_agent_browser_action_pending")
    elif any(str(result.get("status", "")).endswith("_failed") for result in results):
        aggregate_status = "course_agent_operations_failed"
    elif any(str(result.get("status", "")).endswith("_blocked") for result in results):
        aggregate_status = "course_agent_operations_blocked"
    verification = summarize_execution_verification(results, aggregate_status)
    aggregate_fields = _aggregate_execution_result_fields(manifest, results, pending_result)
    execution_evidence_path = _write_manifest_execution_evidence(state, manifest, results)
    return {
        "status": aggregate_status,
        "ok": aggregate_status == "course_agent_operations_verified",
        "pipeline_stage": "tools",
        "tools": manifest.get("tools", {}),
        "verification": verification,
        "moodle_execution_mode": "course_agent_manifest",
        "batch_id": state.get("batch_id", ENV["BATCH_ID"]),
        "approval_run_id": state.get("run_id", ""),
        "operation_id": aggregate_fields["operation_id"],
        "action": aggregate_fields["action"],
        "target_object": aggregate_fields["target_object"],
        "saves_count": aggregate_fields["saves_count"],
        "reason": aggregate_fields["failure_reason"],
        "note": aggregate_fields["failure_reason"],
        "final_moodle_state": aggregate_fields["final_moodle_state"],
        "evidence_report": execution_evidence_path,
        "moodle_agent_task_path": pending_result.get("moodle_agent_task_path", "") if pending_result else "",
        "moodle_agent_browser_task_path": pending_result.get("moodle_agent_browser_task_path", "") if pending_result else "",
        "moodle_agent_result_endpoint": pending_result.get("moodle_agent_result_endpoint", "") if pending_result else "",
        "moodle_agent_progress_endpoint": pending_result.get("moodle_agent_progress_endpoint", "") if pending_result else "",
        "results": results,
    }


def handle_course_agent_result(payload: dict) -> dict:
    state, run_id = _payload_for_current_run(payload, "course_agent_result")
    result = apply_result_defaults(dict(payload.get("course_agent_result") or payload))
    result["run_id"] = str(result.get("run_id") or run_id)
    errors = validate_course_agent_result(result)
    if errors:
        raise ValueError("; ".join(errors))
    state["course_agent_result"] = result
    state["course_agent_result_recorded_at"] = now()
    if result.get("status") in {"PASS", "BLOCKED", "FAIL"}:
        state["course_agent_reported_status"] = result["status"]
        state["status"] = {
            "PASS": "course_agent_result_verified",
            "BLOCKED": "course_agent_result_blocked",
            "FAIL": "course_agent_result_failed",
        }[result["status"]]
    write_state(state)
    append_ledger({"event": "course_agent_result_recorded", "result": result})
    return {"handled": True, "result": result, "state": state}


PLACEHOLDER_MARKERS = (
    "replace_with_",
    "choose_a_random_string",
    "your_",
    "YOUR_",
    "你的",
    "XXXXXXXX",
)


def is_missing_config_value(value: str | None) -> bool:
    if value is None:
        return True
    stripped = value.strip()
    if not stripped:
        return True
    return any(marker in stripped for marker in PLACEHOLDER_MARKERS)


def required_env(keys: list[str]) -> list[str]:
    return [key for key in keys if is_missing_config_value(ENV.get(key))]


def resolve_local_path(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    if path.is_absolute():
        return path
    return ROOT / path


def normalize_approval_attachments_for_operation(operation: dict) -> list[dict]:
    raw_items: list[dict] = []
    highlighted_path = str(operation.get("highlighted_review_file_path", "")).strip()
    if highlighted_path:
        raw_items.append(
            {
                "kind": "highlighted_review_file",
                "label": "Highlighted changed-region review file",
                "path": highlighted_path,
            }
        )

    for item in operation.get("approval_attachments", []) or []:
        if isinstance(item, str):
            raw_items.append({"kind": "approval_attachment", "label": "Approval attachment", "path": item})
        elif isinstance(item, dict):
            raw_items.append(dict(item))

    attachments: list[dict] = []
    seen_paths: set[str] = set()
    for item in raw_items:
        path_text = str(item.get("path", "")).strip()
        if not path_text or is_missing_config_value(path_text) or "TBD" in path_text:
            continue
        path = resolve_local_path(path_text)
        resolved = str(path)
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        attachments.append(
            {
                "kind": str(item.get("kind", "approval_attachment") or "approval_attachment"),
                "label": str(item.get("label", "Approval attachment") or "Approval attachment"),
                "path": resolved,
                "filename": path.name,
                "mime_type": mime_type,
                "exists": path.exists(),
            }
        )
    return attachments


def approval_attachments_for_env(run_id: str, env: dict[str, str]) -> list[dict]:
    operation = build_operation({**env, "APPROVAL_RUN_ID": run_id})
    return normalize_approval_attachments_for_operation(operation)


def approval_attachments(run_id: str) -> list[dict]:
    return approval_attachments_for_env(run_id, ENV)


def attachment_summary_for_message(operation: dict) -> tuple[str, str]:
    attachments = [
        attachment
        for attachment in normalize_approval_attachments_for_operation(operation)
        if attachment.get("kind") in {"highlighted_review_file", "approval_attachment"}
    ]
    names = ", ".join(attachment["filename"] for attachment in attachments)
    if not names:
        return "", ""
    highlighted_pdf_only = all(
        attachment.get("kind") == "highlighted_review_file"
        and attachment.get("mime_type") == "application/pdf"
        for attachment in attachments
    )
    label = "review PDF" if highlighted_pdf_only else "review attachment"
    return label, names


def compact_message_preview(value: str, limit: int = 240) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def whatsapp_api_request(payload: dict) -> dict:
    url = (
        f"https://graph.facebook.com/{ENV['GRAPH_API_VERSION']}/"
        f"{ENV['WHATSAPP_PHONE_NUMBER_ID']}/messages"
    )
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {ENV['WHATSAPP_ACCESS_TOKEN']}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            return {"status_code": response.status, "body": json.loads(body)}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"WhatsApp API error {exc.code}: {body}") from exc


def upload_whatsapp_media(attachment: dict) -> str:
    path = Path(attachment["path"])
    if not path.exists():
        raise FileNotFoundError(f"Approval attachment does not exist: {path}")

    boundary = f"----CodexWhatsAppBoundary{uuid.uuid4().hex}"
    mime_type = attachment.get("mime_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = bytearray()

    def add_field(name: str, value: str) -> None:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")

    add_field("messaging_product", "whatsapp")
    add_field("type", mime_type)
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        (
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
    )
    body.extend(path.read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    url = (
        f"https://graph.facebook.com/{ENV['GRAPH_API_VERSION']}/"
        f"{ENV['WHATSAPP_PHONE_NUMBER_ID']}/media"
    )
    request = urllib.request.Request(
        url,
        data=bytes(body),
        method="POST",
        headers={
            "Authorization": f"Bearer {ENV['WHATSAPP_ACCESS_TOKEN']}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"WhatsApp media upload error {exc.code}: {body_text}") from exc

    media_id = str(payload.get("id", "")).strip()
    if not media_id:
        raise RuntimeError(f"WhatsApp media upload did not return an id: {payload}")
    return media_id


def send_whatsapp_message(text: str, attachments: list[dict] | None = None) -> dict:
    missing = required_env(["WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_TO"])
    if missing:
        raise RuntimeError(f"Missing WhatsApp config: {', '.join(missing)}")

    attachments = attachments or []
    if attachments:
        results: list[dict] = []
        text_sent_separately = False
        caption = text
        if len(text) > 1024:
            results.append(
                whatsapp_api_request(
                    {
                        "messaging_product": "whatsapp",
                        "recipient_type": "individual",
                        "to": ENV["WHATSAPP_TO"],
                        "type": "text",
                        "text": {"preview_url": False, "body": text},
                    }
                )
            )
            text_sent_separately = True
            caption = "Highlighted review file for the approval request above."

        for index, attachment in enumerate(attachments):
            media_id = upload_whatsapp_media(attachment)
            document = {
                "id": media_id,
                "filename": attachment.get("filename") or Path(attachment["path"]).name,
            }
            if index == 0:
                document["caption"] = caption
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": ENV["WHATSAPP_TO"],
                "type": "document",
                "document": document,
            }
            results.append(whatsapp_api_request(payload))

        return {
            "status_code": results[-1]["status_code"] if results else 200,
            "body": results[-1]["body"] if results else {},
            "messages": results,
            "attachments": attachments,
            "text_sent_separately": text_sent_separately,
        }

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": ENV["WHATSAPP_TO"],
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    return whatsapp_api_request(payload)


def planned_marker_text(run_id: str) -> str:
    return f"[SANDBOX MOODLE API TEST] {ENV['BATCH_ID']} {run_id} approval-gated Moodle marker"


def _approval_effect_summary(moodle_mode: str, operation: dict, env: dict[str, str] | None = None) -> str:
    env = env or ENV
    action = operation.get("action", "")
    write_policy = normalize_write_policy(
        env.get("MOODLE_AGENT_BROWSER_WRITE_POLICY")
        or env.get("MOODLE_BROWSER_WRITE_POLICY")
        or "execute_only"
    )
    if moodle_mode == "skip":
        return "record approval only; no Moodle change"
    if moodle_mode in {"api_action", "moodle_api_action", "apim_action"}:
        if action == "publish_announcement":
            return "publish one sandbox announcement via APIM"
        if action == "publish_forum_post":
            return "post one sandbox forum message via APIM"
        if action in {"reply_forum_post", "reply_to_forum_post"}:
            return "reply once to one exact sandbox forum discussion via APIM"
        if action == "delete_forum_post":
            return "check exact forum delete request and block unless a safe delete lane is available"
        if action == "delete_forum_reply":
            return "check exact forum reply delete request and block unless the stable browser lane is used"
        if action in {"create_group", "create_grouping"}:
            return "create listed sandbox group/grouping via APIM"
        if action in {"delete_group", "delete_grouping"}:
            return "delete the exact approved empty sandbox group/grouping via APIM"
        if action in {"assign_group_names_to_grouping", "unassign_group_names_from_grouping"}:
            return "update only the listed sandbox grouping relationships via APIM"
        if action == "upload_file_api":
            return "upload listed test file via APIM"
        if action == "upload_course_file_resource":
            return "create/update one sandbox File resource via APIM plugin"
        return "run listed sandbox APIM operation only"
    if write_policy == "execute_only":
        if action == "upload_or_replace_file":
            return "one save: replace/upload listed file"
        if action == "create_file_resource":
            return "one save: create one sandbox File resource"
        if action == "create_group_signup_activity":
            return "one save: create one sandbox group sign-up activity"
        if action in {"reply_forum_post", "reply_to_forum_post"}:
            return "one save: reply to one exact sandbox forum discussion through Moodle browser"
        if action == "delete_forum_post":
            return "one save: delete one exact sandbox forum message"
        if action == "delete_forum_reply":
            return "one save: delete one exact non-root sandbox forum reply"
        if action == "edit_assignment_title":
            return "one save: change listed title only"
        return "one save: listed operation only"
    if write_policy == "execute_and_revert":
        return "one test save plus one rollback save"
    return "prepare only; explicit precheck/dry-run mode prevents Moodle writes"


def _approval_visibility_text(operation: dict) -> str:
    expected = str(operation.get("expected_visibility", "")).strip()
    if expected:
        return expected
    visible = operation.get("visible", "")
    if isinstance(visible, bool):
        return "Show on course page" if visible else "Hide on course page"
    normalized = str(visible).strip().lower()
    if normalized in {"1", "true", "yes", "show", "visible"}:
        return "Show on course page"
    if normalized in {"0", "false", "no", "hide", "hidden"}:
        return "Hide on course page"
    return ""


def _approval_operation_summary(operation: dict) -> list[tuple[str, str]]:
    action = operation.get("action", "unknown")
    object_name = operation.get("target_object", "")
    if action in {"create_file_resource", "upload_course_file_resource"}:
        object_name = object_name or operation.get("resource_name", "")
    fields = [
        ("operation", action),
        ("object", object_name),
        ("section", operation.get("section_name", "")),
    ]
    if action == "create_file_resource":
        fields.extend(
            [
                ("course id", operation.get("target_course_id", "")),
                ("upload file", operation.get("expected_uploaded_filename", "")),
                ("expected visibility", _approval_visibility_text(operation)),
            ]
        )
    elif action == "upload_or_replace_file":
        existing_name = operation.get("expected_existing_filename") or Path(operation.get("source_file_path", "")).name
        target_name = operation.get("expected_uploaded_filename") or Path(operation.get("target_file_path", "")).name
        attachment_label, attachment_names = attachment_summary_for_message(operation)
        if operation.get("delete_existing_before_upload"):
            fields.append(("current file to remove", operation.get("delete_before_upload_filename") or existing_name))
        else:
            fields.append(("current file", existing_name))
        fields.extend(
            [
                ("proposed file", target_name),
                ("proposed file path", operation.get("target_file_path", "")),
            ]
        )
        if attachment_names:
            fields.append((attachment_label, attachment_names))
        rollback_name = operation.get("expected_rollback_filename") or Path(operation.get("rollback_file_path", "")).name
        if rollback_name:
            fields.append(("rollback file", rollback_name))
    elif action == "upload_file_api":
        attachment_label, attachment_names = attachment_summary_for_message(operation)
        fields.extend(
            [
                ("course id", operation.get("target_course_id", "")),
                ("wsfunction", operation.get("api_execution", {}).get("wsfunction", "")),
                ("upload file", operation.get("expected_uploaded_filename", "")),
                ("upload file path", operation.get("target_file_path", "")),
                (
                    "file area",
                    "/".join(
                        str(part).strip()
                        for part in [
                            operation.get("component", ""),
                            operation.get("filearea", ""),
                            operation.get("itemid", ""),
                        ]
                        if str(part).strip()
                    ),
                ),
            ]
        )
        context = operation.get("contextid") or " ".join(
            str(part).strip()
            for part in [operation.get("contextlevel", ""), operation.get("instanceid", "")]
            if str(part).strip()
        )
        if context:
            fields.append(("context", context))
        if attachment_names:
            fields.append((attachment_label, attachment_names))
    elif action == "upload_course_file_resource":
        fields.extend(
            [
                ("course id", operation.get("target_course_id", "")),
                ("upload file", operation.get("expected_uploaded_filename", "")),
                ("expected visibility", _approval_visibility_text(operation)),
            ]
        )
    elif action == "edit_assignment_title":
        fields.extend(
            [
                ("current title", operation.get("original_title", "")),
                ("proposed title", operation.get("target_title", "")),
            ]
        )
    elif action == "set_access_restriction_time":
        fields.extend(
            [
                ("restriction type", operation.get("target_condition_type", "")),
                ("proposed value", operation.get("target_value", "")),
                ("rollback value", operation.get("rollback_value", "")),
            ]
        )
    elif action in {
        "publish_announcement",
        "publish_forum_post",
        "reply_forum_post",
        "reply_to_forum_post",
    }:
        attachment_label, attachment_names = attachment_summary_for_message(operation)
        fields.extend(
            [
                ("course id", operation.get("target_course_id", "")),
                ("forum id", operation.get("target_forum_id", "")),
                ("forum name", operation.get("target_forum_name", "")),
                ("discussion id", operation.get("target_discussion_id", "")),
                ("discussion", operation.get("target_discussion_title", "")),
                ("subject", operation.get("subject", "")),
                ("body preview", compact_message_preview(operation.get("message", ""))),
            ]
        )
        if attachment_names:
            fields.append((attachment_label, attachment_names))
    elif action == "delete_forum_post":
        fields.extend(
            [
                ("course id", operation.get("target_course_id", "")),
                ("forum id", operation.get("target_forum_id", "")),
                ("forum name", operation.get("target_forum_name", "")),
                ("post id", operation.get("target_post_id", "")),
                ("discussion id", operation.get("target_discussion_id", "")),
                ("subject", operation.get("target_subject", "")),
                ("target", operation.get("target_ref", "")),
            ]
        )
    elif action == "delete_forum_reply":
        fields.extend(
            [
                ("course id", operation.get("target_course_id", "")),
                ("forum id", operation.get("target_forum_id", "")),
                ("forum name", operation.get("target_forum_name", "")),
                ("discussion id", operation.get("target_discussion_id", "")),
                ("discussion", operation.get("target_discussion_title", "")),
                ("reply post id", operation.get("target_reply_post_id", "")),
                ("reply subject", operation.get("target_reply_subject", "")),
                (
                    "reply preview",
                    compact_message_preview(
                        operation.get("target_reply_text", "")
                    ),
                ),
            ]
        )
    elif action in {"create_group", "delete_group"}:
        attachment_label, attachment_names = attachment_summary_for_message(operation)
        fields.extend(
            [
                ("course id", operation.get("target_course_id", "")),
                ("group name", operation.get("group_name", "")),
            ]
        )
        if attachment_names:
            fields.append((attachment_label, attachment_names))
    elif action in {"create_grouping", "delete_grouping"}:
        attachment_label, attachment_names = attachment_summary_for_message(operation)
        fields.extend(
            [
                ("course id", operation.get("target_course_id", "")),
                ("grouping name", operation.get("grouping_name", "")),
                ("idnumber", operation.get("idnumber", "")),
            ]
        )
        if attachment_names:
            fields.append((attachment_label, attachment_names))
    elif action in {"assign_group_names_to_grouping", "unassign_group_names_from_grouping"}:
        group_names = operation.get("group_names", []) or []
        group_names_text = (
            group_names
            if isinstance(group_names, str)
            else ", ".join(str(item) for item in group_names)
        )
        fields.extend(
            [
                ("course id", operation.get("target_course_id", "")),
                ("grouping name", operation.get("grouping_name", "")),
                (
                    "groups to add"
                    if action == "assign_group_names_to_grouping"
                    else "groups to remove",
                    group_names_text,
                ),
            ]
        )
    elif action == "create_group_signup_activity":
        group_names = operation.get("group_names", []) or operation.get("choicegroup_target_group_names", []) or []
        if isinstance(group_names, str):
            group_names_text = group_names
        else:
            group_names_text = ", ".join(str(item) for item in group_names)
        fields.extend(
            [
                ("course id", operation.get("target_course_id", "")),
                ("section", operation.get("section_name") or operation.get("sectionnum", "")),
                ("activity", operation.get("title", "")),
                ("groups", group_names_text),
                ("visibility", operation.get("expected_visibility", "")),
            ]
        )
    else:
        marker_text = operation.get("marker_text") or planned_marker_text(operation.get("approval_run_id", ""))
        fields.append(("details", marker_text))
    return [(label, str(value).strip()) for label, value in fields if str(value).strip()]


def approval_message_for_env(run_id: str, env: dict[str, str], approval_code: str | None = None) -> str:
    batch_id = env["BATCH_ID"]
    message_file = env.get("APPROVAL_MESSAGE_FILE", "").strip()
    if message_file:
        path = Path(message_file).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        message = path.read_text(encoding="utf-8")
        return (
            message.replace("{batch_id}", batch_id)
            .replace("{run_id}", run_id)
            .replace("{approval_code}", approval_code or run_id)
        )

    operation = build_operation({**env, "APPROVAL_RUN_ID": run_id})
    moodle_mode = env.get("MOODLE_EXECUTION_MODE", "agent_checklist").strip().lower()
    effect = _approval_effect_summary(moodle_mode, operation, env)
    fields = _approval_operation_summary(operation)
    message_format = env.get(
        "COURSE_AGENT_APPROVAL_MESSAGE_FORMAT",
        "boxed_ascii",
    ).strip().lower()
    return format_approval_request(
        batch_id=batch_id,
        run_id=run_id,
        fields=fields,
        effect=effect,
        message_format=message_format,
        approval_code=approval_code,
    )


def approval_message(run_id: str) -> str:
    state = read_state()
    code = state.get("approval_code", "") if state.get("run_id") == run_id else ""
    return approval_message_for_env(run_id, ENV, approval_code=code)


def write_course_task_agent_task(state: dict | None = None) -> Path:
    state = state or read_state()
    run_id = state.get("run_id") or new_run_id()
    approval_code = state.get("approval_code") or new_approval_code()
    state["approval_code"] = approval_code
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    path = reports / f"{ENV['BATCH_ID']}_{run_id}_codex_course_task.md"
    progress_url = f"{base_url()}/agent/progress"
    intent_url = f"{base_url()}/agent/course-task-intent"
    manifest_url = f"{base_url()}/agent/course-operation-manifest"
    result_url = f"{base_url()}/agent/course-agent-result"
    approval_url = f"{base_url()}/agent/approval"
    status_url = f"{base_url()}/status"
    reply_commands = approval_reply_commands(ENV["BATCH_ID"], run_id, approval_code=approval_code)
    allowed_kinds = ", ".join(sorted(ALLOWED_OPERATION_KINDS))
    capability_text = json.dumps(capability_specs(), ensure_ascii=False, indent=2)
    role_contract = role_contract_markdown(sorted(ALLOWED_OPERATION_KINDS))
    content = f"""# Codex Course Management Agent Task

Batch: `{ENV['BATCH_ID']}`
Run: `{run_id}`
Approval code: `{approval_code}`
Target chat: `{course_agent_chat_name()}`
Target Moodle course: `{SANDBOX_COURSE_ID}` ({SANDBOX_COURSE_URL})

## Goal

Listen in the configured WhatsApp Web chat for one new natural-language course-management task from staff. This is one cycle inside a longer-running course-agent lifecycle: the launcher may start another cycle after this one finishes, and browser windows belong to the agent lifecycle rather than this single approval. Convert the task into a bounded Moodle operation intent, request approval in WhatsApp, and after approval allow the local server to execute only the registered operation manifest.

{role_contract}

## Required Runtime Contract

1. Open only `https://web.whatsapp.com/` and only the chat named `{course_agent_chat_name()}`. Do not open, inspect, compose in, or answer any other chat.
2. Wait for the newest non-command staff task message after this task starts. Ignore old messages and messages that already look like `OK` or `NO`.
3. Planner: infer a `course_task_intent` for sandbox course `{SANDBOX_COURSE_ID}` only. Set `pipeline_stage=planner`, include a `planner` object, include `tool_boundary`, and include `verification_requirements`. Ordinary uncertainty may be handled by recording assumptions; high-risk changes must be made explicit in the current WhatsApp approval boundary.
4. POST the intent to `{intent_url}` before requesting approval.
5. Planner: read current Moodle state through project-supported readback/API routes when needed. Build a `course_operation_manifest` by composing supported capabilities with typed `params`; `operation_kind` is the resolved execution lane. Set `pipeline_stage=tools`, include a `tools` object, include `tool_name` for every operation, and include `verification_plan`.
   - If a bounded sandbox Moodle task is possible through the existing Moodle browser UI but no stable API/project capability covers it, use `capability=browser_moodle_task` with the strict browser fallback DSL instead of blocking solely because the stable capability is missing.
   - Do not use browser fallback for unbounded cleanup, credentials/secrets, live-course work, arbitrary code, server/admin/plugin changes, grade/student-record changes, or participant/student-data export.
6. POST the manifest to `{manifest_url}` before requesting approval. The current-run WhatsApp staff approval is the authority for the bounded manifest.
7. Planner: send one compact WhatsApp approval request. Include the interpreted intent, expected Moodle impact, operation kinds, browser-fallback possibility, approval code `{approval_code}`, and these exact reply commands:

```text
{reply_commands['approve']}
{reply_commands['reject']}
```

8. After the approval request is visible in WhatsApp, POST `approval_request_sent` to `{progress_url}`.
9. Watch only the configured chat for a new current-run reply. When a matching reply appears, POST it to `{approval_url}` with the exact body and evidence.
10. Tools: poll `{status_url}` after posting the reply. If Moodle browser action is pending, stop; the local runner will start the Codex Moodle browser task.
11. Verifiers: if API execution finished, perform one simple verification: read the current exact target once, compare only the approved expected fields, and return PASS or FAIL. Do not re-plan, fuzzy-match, switch targets, repeat a write, or start another verification workflow. Send a concise result summary to WhatsApp, and POST the same summary to `{result_url}` with `pipeline_stage=verifiers`, a `verifiers` object, and a `verification` object.
12. Leave WhatsApp and Moodle browser windows/sessions reusable for the next prompt in this agent lifecycle. Do not close them at the end of a single approval cycle unless the whole agent process is stopping.

## Safety Boundaries

- Never target any course except sandbox `{SANDBOX_COURSE_ID}`.
- Never modify the legacy live DASE7137/IMSE7137 Moodle course.
- If staff asks for any state-changing action on the legacy/live/old Moodle course or course id 118635, block the whole request. Do not execute only the sandbox portion of a mixed live-course plus sandbox write request unless staff sends a new sandbox-only prompt.
- Do not read or print secrets, Moodle tokens, browser cookies, local storage, or passwords.
- API/APIM is preferred. Browser writes are allowed only through generated Moodle agent tasks after approval.
- If the task needs deletion, student-visible publication, a different source file than staff implied, or unusual write scope, make that boundary explicit in the current WhatsApp approval. If it targets a different/non-sandbox course, block before approval.
- If you cannot produce a valid intent and manifest, POST a progress failure and report the blocker in WhatsApp.

## Supported Capabilities

Use `capability` plus typed `params` as canonical planner output. The capability resolver fills the executor lane from these specs.
One current-run WhatsApp approval covers one exact bounded manifest. If staff explicitly requires a separate approval cycle before each individual write, return no operations and explain that the writes must be submitted as separate prompts; never claim that one manifest will issue multiple approval codes.
If staff explicitly asks to hide/show/set visibility after another action, include a separate `set_visibility` operation even when current readback already appears to match.
For any "visible/released after/at a future time" request, include both `set_restriction_time` and `set_visibility` visible=true for the same target. A date restriction alone is not enough when the module itself is hidden.
Do not add `set_visibility` for a newly created `upload_file_resource` unless an existing `target_module_id`/`cmid` is already known. For new uploads, encode the requested initial visibility in `upload_file_resource.params.visible`; the upload tool applies it.
For section group sign-up requests, use `create_group_signup_activity` with explicit `section`, `title`, and `group_names`. If the request also creates groups/grouping, order `create_group`, then `create_grouping`, then `create_group_signup_activity`.
For Assignment activity creation requests, use `create_assignment_activity` with explicit `section` and `title`/`name`; do not use generic `browser_moodle_task` for ordinary assignment creation.
For a newly created Moodle activity that must become visible at a future time, do not leave it only `Hidden from students`. Use one bounded approval with ordered operations: create the activity hidden with `post_create_reference`, set_restriction_time using that `target_ref`, then set_visibility using the same `target_ref` with visible=true so the date restriction controls student access.
For forum messages/posts, use `create_forum_post` with an exact `forum_name` or `target_forum_id` grounded in the current course readback. If no unique current forum target can be established, return a blocker instead of inventing a default target.
For replies to existing Forum questions/discussions, use `reply_forum_post` with subject, body/message, an exact `forum_name` or `target_forum_id`, and one exact `discussion_id`, `discussion_url`, or `discussion_title`. Never use `create_forum_post` for a reply because it creates a new discussion. If detailed Forum structure is not yet supplied, keep `reply_forum_post`, set `require_detailed_forum_structure=true`, and let runtime collect the sanitized discussion inventory before replanning.
For deleting one reply inside an existing discussion, use `delete_forum_reply` with an exact forum, exact discussion, and `reply_post_id`/`reply_post_url` or exact `reply_text`/`target_ref`. Never use `delete_forum_post` for a reply. The browser lane must prove the target is a non-root reply, delete once, and verify that reply post id is absent.
When the current Forum structure does not have `detail_status=detailed` and exact current discussions/posts are needed, select `inspect_forum_structure` with `scope=forums` and exact readback-grounded `forum_names`. Runtime must complete the sanitized read-only pass and re-plan; do not ask staff to run the inventory.
After detailed Forum readback, omit `inspect_forum_structure` from a final write plan and output the exact ordered write operations only.
When sanitized detailed Forum discussion inventory is supplied, expand a bounded "delete all posts" request into one exact `delete_forum_post` operation per current discussion; do not ask staff to manually supply inventory already present in readback.
If staff explicitly asks to post to an exact named forum that is absent, `create_forum_activity` may create that exact prerequisite. It must precede all dependent posts, declare a unique top-level `params.post_create_reference`, and every later `create_announcement`/`create_forum_post` must use the same top-level `params.target_ref` rather than only nesting it in `forum_target`. Disclose the prerequisite in the single approval and never substitute another forum.
When a staff-provided tag/reference is the only requested content, the planner should generate concise distinct subject/body values retaining that exact tag rather than asking the staff to restate it.
For forum message/post deletion, use `delete_forum_post` with either an exact `post_url`, `discussion_url`, `post_id`, or `discussion_id`, or `forum_name`/`forum_url` plus `target_subject`/`target_ref`; the browser lane searches by visible text and must block if the match is not unique. Do not use it for deleting a whole forum module.

```json
{capability_text}
```

For local course-file refresh and batch upload, use `capability=course_refresh_batch_upload`; follow its `params_schema`, local preapproval steps, and safety rules. Do not invent scripts or arbitrary browser steps for the refresh pipeline.
For one WhatsApp-attached file that staff wants refreshed and returned for review, use `capability=refresh_file_preview`; this is local-only, must not be mixed with Moodle writes, and any later Moodle upload must be a separate approved operation.
For multiple current WhatsApp file attachments or a ZIP package that staff wants refreshed and reviewed individually, use `capability=refresh_file_batch_preview` with ordered `params.file_paths`; runtime returns each PDF separately and does not write Moodle.
Use `capability=finalize_file_batch_preview` only for an explicit request to confirm the exact active batch/version and generate one combined PDF. A generic acknowledgement is insufficient, and finalization never consumes a Moodle approval code.
For bounded Moodle browser-only work, use `capability=browser_moodle_task` only when no stable listed capability covers the request. It must include a fallback reason, explicit browser goal, constrained browser steps, readback expectations, and visible approval wording that says Moodle browser fallback.
"""
    path.write_text(content, encoding="utf-8")
    append_ledger({"event": "codex_course_task_written", "path": str(path), "run_id": run_id})
    return path


def write_agent_task(state: dict | None = None) -> Path:
    state = state or read_state()
    run_id = state.get("run_id") or new_run_id()
    approval_code = state.get("approval_code") or new_approval_code()
    state["approval_code"] = approval_code
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    path = reports / f"{ENV['BATCH_ID']}_{run_id}_agent_whatsapp_approval_task.md"
    message = approval_message_for_env(run_id, ENV, approval_code=approval_code)
    attachments = approval_attachments(run_id)
    report_url = f"{base_url()}/agent/report"
    approval_url = f"{base_url()}/agent/approval"
    progress_url = f"{base_url()}/agent/progress"
    single_account = ENV.get("AGENT_SINGLE_ACCOUNT_TEST_MODE", "0") == "1"
    direction_rule = (
        "This smoke test is in single-account mode. A WhatsApp bubble marked `You:` or outgoing may be treated as the test approver reply only when it is sent after the current request and exactly includes this run id. Report it honestly with `observed_direction=outgoing_self_test`, never as `incoming`."
        if single_account
        else "Only incoming messages from another WhatsApp participant are valid approval replies."
    )
    wait_for_reply = (
        "wait for a new current-run WhatsApp command in the configured chat"
        if single_account
        else "wait for a new incoming approver reply in the configured chat"
    )
    report_direction = "outgoing_self_test" if single_account else "incoming"
    sender_example = "You / operator self-test" if single_account else "approver id or phone if visible"
    profile_example = "You" if single_account else "Approver name shown in WhatsApp"
    poll_seconds = ENV.get("CODEX_REPLY_POLL_SECONDS", "8")
    max_polls = ENV.get("CODEX_REPLY_MAX_POLLS", "18")
    evidence_example = (
        "New outgoing You/self-test command observed after the approval request."
        if single_account
        else "New incoming message observed after the approval request."
    )
    guardrail_line = (
        "Do not fabricate approval evidence. In single-account smoke mode, it is valid to report an operator-authored `You:`/outgoing command only with `observed_direction=outgoing_self_test` and only when it exactly matches this run id."
        if single_account
        else "Do not approve on behalf of staff; only report an actual incoming reply."
    )
    reply_commands = approval_reply_commands(ENV["BATCH_ID"], run_id, approval_code=approval_code)
    approve_command = reply_commands["approve"]
    if attachments:
        attachment_lines = "\n".join(
            f"- {attachment['label']}: `{attachment['path']}`"
            + ("" if attachment.get("exists") else " (missing; report send failure instead of sending without it)")
            for attachment in attachments
        )
        attachment_section = f"""## Approval Attachments

Send these file attachment(s) with the approval request:

{attachment_lines}

Prefer one WhatsApp document message with the approval text as the caption. If the browser tool cannot attach with a caption, send the approval text first and then attach the file immediately. Do not report `approval_request_sent` until approval code `{approval_code}` and every listed attachment are visible in the target chat.
"""
        send_goal = "send the approval request and its approval attachment(s) in WhatsApp Web"
        send_step = (
            "10. Paste the exact approval request from \"Message To Send\", verify the draft contains "
            f"`{approval_code}`, attach every file from \"Approval Attachments\", then send once. Do not wait for "
            "human approval before this send; humans can only reply after the outgoing request and attachment are visible. "
            "If any attachment is missing or safe composition/sending is not possible, post `approval_request_send_failed` and stop."
        )
        sent_step = (
            "11. After sending, verify the current-run request and every approval attachment appear as sent/outgoing WhatsApp content, then post `approval_request_sent`. Do not continue to reply-watching until this callback succeeds."
        )
    else:
        attachment_section = ""
        send_goal = "send the approval request in WhatsApp Web"
        send_step = (
            f"10. Paste the exact approval request from \"Message To Send\", verify the draft contains `{approval_code}`, then immediately press Enter once in the same textbox. Do not wait for human approval before this send; humans can only reply after the outgoing request is visible. If safe composition or sending is not possible, post `approval_request_send_failed` and stop."
        )
        sent_step = (
            "11. After sending, verify the current-run request appears as a sent/outgoing WhatsApp message, then post `approval_request_sent`. Do not continue to reply-watching until this callback succeeds."
        )
    browser_profile = ENV.get("CODEX_BROWSER_PROFILE", "codex")
    content = f"""# Codex WhatsApp Approval Task

Batch: `{ENV['BATCH_ID']}`
Run: `{run_id}`
Approval code: `{approval_code}`
Target chat: `{course_agent_chat_name()}`
Browser profile: `{browser_profile}` (Codex-managed browser, not the operator's default Chrome)
Browser targeting: use the raw CDP `targetId` from `browser tabs` for both `snapshot` and `act`; do not use the tab label `wa` for `act`.
Chat isolation: only the target chat is in scope. Ignore unread badges, notifications, previews, and new messages from every other WhatsApp chat.
Agent report form: `{report_url}`
Agent approval endpoint: `{approval_url}`
Agent progress endpoint: `{progress_url}`

## Goal

The agent owns the WhatsApp loop end to end:

1. {send_goal};
2. {wait_for_reply};
3. classify the reply as OK or NO;
4. report the exact command and evidence back to the local approval server.

Humans only reply inside WhatsApp. They must not copy/paste approval text into
the local server during the normal workflow.

Current test direction rule: {direction_rule}

## Progress Callbacks

Post JSON progress to `{progress_url}` as soon as each step is true. These callbacks are how the local flow distinguishes "not sent yet" from "sent and waiting".

```http
POST {progress_url}
Content-Type: application/json

{{"event":"approval_request_send_started","run_id":"{run_id}","detail":"opening WhatsApp and target chat"}}
{{"event":"whatsapp_opened","run_id":"{run_id}","detail":"WhatsApp Web loaded"}}
{{"event":"approval_chat_opened","run_id":"{run_id}","detail":"target chat header and message box visible"}}
{{"event":"approval_request_sent","run_id":"{run_id}","evidence":"current-run request is visible as a sent/outgoing WhatsApp message"}}
{{"event":"approval_request_send_failed","run_id":"{run_id}","detail":"why it could not be sent"}}
{{"event":"approval_reply_observed","run_id":"{run_id}","evidence":"matching current-run reply observed"}}
{{"event":"approval_report_posted","run_id":"{run_id}","detail":"reply was posted to the local approval endpoint"}}
```

Only send `approval_request_sent` after pressing Enter and verifying that WhatsApp shows approval code `{approval_code}` as sent/outgoing. Do not report `approval_request_draft_ready` as a separate stopping point. If this is a retry and that exact request is already visible, post `approval_request_sent` with `"recovered": true` and do not resend.

## Message To Send

```text
{message}
```

{attachment_section}
## Expected Reply Commands

```text
{reply_commands["approve"]}
{reply_commands["reject"]}
```

## Agent Steps

1. Open `https://web.whatsapp.com/`.
2. Use the Codex-managed browser profile `{browser_profile}`. If using the browser tool, omit `profile` or set `profile="{browser_profile}"`; do not use `profile="user"`.
3. After `browser tabs`, use the raw CDP `targetId` for the WhatsApp page in all later `snapshot` and `act` calls. Do not use the tab label `wa` for `act`. If an `act` call returns `targetId must match`, do not repeat the same ref/action; take a fresh tabs+snapshot with the raw target id, or stop and report the blocker.
4. Confirm the operator is logged in. If QR login is needed, pause for human scan.
5. Open the chat named `{course_agent_chat_name()}`. If the right-hand WhatsApp pane already shows this chat and has a message textbox for it, do not click the left chat-list row again.
6. When opening the chat from search, prefer the chat result under the `Chats` heading, not `Messages` or an old message preview. If clicking the result leaves the left pane on `Search results.` and no right-hand chat header/textbox is visible, clear the search and retry by typing the chat name and pressing Enter, or click the visible chat title text itself. Do not send the approval request until the right-hand pane shows the target chat header and the bottom message textbox.
7. If any new unread message, notification, or preview appears outside `{course_agent_chat_name()}`, ignore it. Do not open, inspect, compose in, or reply to any other chat.
8. Before composing the message, post `approval_request_send_started`.
9. Click the bottom message textbox. If any old draft text is present, select all text in the textbox and delete it before composing this run.
{send_step}
{sent_step}
12. Wait until a new message, after the request was sent, matches one of the
   expected reply commands and includes `{approval_code}`.
13. Poll the chat about every {poll_seconds} seconds for up to {max_polls} checks.
   Do not use a single sleep longer than {poll_seconds} seconds. After each wait,
   inspect the latest messages again and immediately report a matching command.
14. Ignore old approvals from previous runs, even if they contain the same batch id.
15. In normal mode, ignore bubbles marked `You`. In single-account test mode,
   `You:` is acceptable only if it is the exact current-run command.
16. Record only the command message, not unrelated chat text.
17. When a matching reply is visible, post `approval_reply_observed`, then open `{report_url}` or POST `{approval_url}` and submit the exact command plus evidence.
18. After the local approval endpoint succeeds, post `approval_report_posted`.
19. If no matching current-run reply appears after all checks, finish with an
    honest no-reply report instead of using an old message or fabricating approval.

If the agent has a direct HTTP POST tool, it may alternatively POST JSON to:

```http
POST {approval_url}
Content-Type: application/json

{{
  "agent_name": "{course_agent_name()}",
  "source_channel": "whatsapp_web",
  "chat_name": "{course_agent_chat_name()}",
  "observed_direction": "{report_direction}",
  "run_id": "{run_id}",
  "whatsapp_sender": "{sender_example}",
  "profile_name": "{profile_example}",
  "body": "{approve_command}",
  "evidence": "{evidence_example}"
}}
```

## Guardrails

- Use the configured approval chat only.
- Ignore and never answer messages from any other WhatsApp chat during the test.
- Match only this approval code: `{approval_code}`.
- Do not send student-facing Moodle content.
- {guardrail_line}
- Never use a previous run's approval message.
- If the reply is ambiguous, do not convert it into a command; keep waiting for a valid `OK {approval_code}` or `NO {approval_code}` reply, then report no matching reply if the polling window expires.
"""
    path.write_text(content, encoding="utf-8")
    append_ledger({"event": "agent_whatsapp_task_written", "path": str(path), "run_id": run_id})
    return path


def start_approval_run(env_overrides: dict | None = None) -> dict:
    applied_overrides = apply_start_env_overrides(env_overrides)
    mode = whatsapp_mode()
    if mode == "codex_agent_web":
        previous_state = read_state()
        state = initial_state()
        for key in (
            "whatsapp_authenticated",
            "whatsapp_agent_display_name",
            "whatsapp_chat_type",
            "whatsapp_mention_ready",
            "whatsapp_degraded_reason",
        ):
            if key in previous_state:
                state[key] = previous_state[key]
        free_task = mode == "codex_agent_web" and ENV.get("COURSE_AGENT_FREE_TASK_MODE", "1") == "1"
        task_path = write_course_task_agent_task(state) if free_task else write_agent_task(state)
        attachments = approval_attachments(state["run_id"])
        state["status"] = "course_task_agent_created" if free_task else "agent_whatsapp_task_created"
        state["agent_whatsapp_status"] = state["status"]
        state["agent_progress"] = []
        state["approval_attachments"] = attachments
        state["agent_task_path"] = str(task_path)
        state["agent_report_endpoint"] = f"{base_url()}/agent/approval"
        state["agent_progress_endpoint"] = f"{base_url()}/agent/progress"
        state["codex_task_path"] = str(task_path)
        if free_task:
            state["course_task_intent_endpoint"] = f"{base_url()}/agent/course-task-intent"
            state["course_operation_manifest_endpoint"] = f"{base_url()}/agent/course-operation-manifest"
        if applied_overrides:
            state["start_env_overrides"] = applied_overrides
        write_state(state)
        progress_result = handle_agent_progress(
            {
                "event": "agent_task_created",
                "run_id": state["run_id"],
                "agent_name": course_agent_name(),
                "chat_name": course_agent_chat_name(),
                "detail": f"agent task written to {task_path}",
            }
        )
        state = progress_result["state"]
        return {
            "ok": True,
            "mode": mode,
            "course_task_mode": free_task,
            "run_id": state["run_id"],
            "approval_code": state.get("approval_code", ""),
            "approval_message": approval_message(state["run_id"]),
            "approval_attachments": attachments,
            "agent_task_path": str(task_path),
            "agent_report_endpoint": f"{base_url()}/agent/approval",
            "agent_progress_endpoint": f"{base_url()}/agent/progress",
            "course_task_intent_endpoint": f"{base_url()}/agent/course-task-intent",
            "course_operation_manifest_endpoint": f"{base_url()}/agent/course-operation-manifest",
            "course_agent_result_endpoint": f"{base_url()}/agent/course-agent-result",
            "env_overrides_applied": applied_overrides,
            "state": state,
        }

    state = initial_state()
    attachments = approval_attachments(state["run_id"])
    state["approval_attachments"] = attachments
    if applied_overrides:
        state["start_env_overrides"] = applied_overrides
    write_state(state)
    result = send_whatsapp_message(approval_message(state["run_id"]), attachments)
    state["status"] = "whatsapp_sent_waiting_for_approval"
    write_state(state)
    append_ledger({"event": "whatsapp_approval_request_sent", "result": result})
    return {
        "ok": True,
        "mode": whatsapp_mode(),
        "run_id": state["run_id"],
        "approval_code": state.get("approval_code", ""),
        "approval_message": approval_message(state["run_id"]),
        "approval_attachments": approval_attachments(state["run_id"]),
        "webhook_endpoint": f"{base_url()}/webhook",
        "result": result,
        "env_overrides_applied": applied_overrides,
        "state": state,
    }


def operator_console_approval_overrides(state: dict) -> dict:
    overrides = state.get("start_env_overrides") or {}
    if not isinstance(overrides, dict):
        return {}
    return overrides


def record_operator_console_approval(state: dict) -> None:
    overrides = operator_console_approval_overrides(state)
    phrase = str(overrides.get("OPERATOR_CONSOLE_APPROVAL_PHRASE") or "").strip()
    kind = str(overrides.get("OPERATOR_CONSOLE_APPROVAL_KIND") or "").strip()
    if kind != "moodle_action" or not phrase:
        return
    append_ledger(
        {
            "event": "operator_console_approval_recorded",
            "run_id": state.get("run_id", ""),
            "approval_code": state.get("approval_code", ""),
            "approval_kind": kind,
            "manifest_path": overrides.get("OPERATOR_CONSOLE_MANIFEST_PATH", ""),
            "approval_phrase": phrase,
            "recorded_at": now(),
        }
    )


def start_operator_console_approval(payload: dict) -> dict:
    manifest_path = str(payload.get("manifest_path") or "").strip()
    category = str(payload.get("category") or "").strip()
    if manifest_path:
        prepared = prepare_action_approval(manifest_path)
    elif category:
        prepared = prepare_staff_package(category, env=ENV)
    else:
        raise ConsoleError("Provide manifest_path or category.")
    result = start_approval_run(prepared.get("env_overrides") or {})
    append_ledger(
        {
            "event": "operator_console_approval_started",
            "run_id": result.get("run_id", ""),
            "approval_code": result.get("approval_code", ""),
            "approval_kind": prepared.get("kind", ""),
            "manifest_path": prepared.get("manifest_path", ""),
            "package_path": prepared.get("package_path", ""),
        }
    )
    result["operator_console"] = prepared
    return result


def parse_whatsapp_payload(payload: dict) -> list[dict]:
    messages = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            contacts = {c.get("wa_id"): c for c in value.get("contacts", [])}
            for message in value.get("messages", []):
                sender = message.get("from", "")
                body = message.get("text", {}).get("body", "")
                messages.append(
                    {
                        "sender": sender,
                        "profile_name": contacts.get(sender, {}).get("profile", {}).get("name", ""),
                        "message_id": message.get("id", ""),
                        "body": body.strip(),
                    }
                )
    return messages


def parse_command(body: str, batch_id: str) -> dict | None:
    return parse_approval_command(body, batch_id)


def sender_allowed(sender: str) -> bool:
    allowed = [s.strip() for s in ENV.get("ALLOWED_WHATSAPP_NUMBERS", "").split(",") if s.strip()]
    return not allowed or sender in allowed


def handle_command(message: dict) -> dict:
    state = read_state()
    batch_id = state["batch_id"]
    current_run_id = state.get("run_id", "")
    current_approval_code = str(state.get("approval_code", "")).strip()
    command = parse_command(message["body"], batch_id)
    if not command:
        append_ledger({"event": "ignored_message", "message": message})
        return {"handled": False, "reason": "message did not match command format"}

    if ENV.get("REQUIRE_RUN_ID_IN_APPROVAL", "1") == "1":
        command_run_id = command.get("run_id", "")
        current_match = command_run_id == current_run_id or (
            bool(current_approval_code) and command_run_id == current_approval_code
        )
        if not current_run_id or not current_match:
            append_ledger(
                {
                    "event": "ignored_wrong_or_missing_run_id",
                    "message": message,
                    "command": command,
                    "current_run_id": current_run_id,
                    "current_approval_code": current_approval_code,
                }
            )
            return {"handled": False, "reason": "wrong or missing run_id"}
        if command_run_id == current_approval_code:
            command["approval_code"] = current_approval_code
            command["run_id"] = current_run_id

    if not sender_allowed(message["sender"]):
        append_ledger({"event": "blocked_sender", "message": message, "command": command})
        return {"handled": False, "reason": "sender not allowed"}
    bound_requester = str(state.get("whatsapp_requester") or "").strip()
    observed_requester = str(message.get("profile_name") or message.get("sender") or "").strip()
    if bound_requester and observed_requester.casefold() != bound_requester.casefold():
        append_ledger(
            {
                "event": "blocked_workflow_requester_mismatch",
                "expected_requester": bound_requester,
                "observed_requester": observed_requester,
                "message_id": message.get("message_id", ""),
            }
        )
        return {"handled": False, "reason": "reply did not come from the current workflow requester"}

    current_status = state.get("status", "")
    if current_status in NEGATIVE_DECISION_STATUSES and command["command"] == "APPROVE":
        append_ledger(
            {
                "event": "ignored_approval_after_terminal_negative_decision",
                "message": message,
                "command": command,
                "current_status": current_status,
            }
        )
        return {
            "handled": False,
            "reason": f"run already has terminal negative decision: {current_status}",
            "state": state,
        }

    event = {
        "sender": message["sender"],
        "profile_name": message["profile_name"],
        "note": command["note"],
        "message_id": message.get("message_id", ""),
        "recorded_at": now(),
    }
    if command.get("approval_code"):
        event["approval_code"] = command["approval_code"]
    if command["command"] == "APPROVE":
        if all(existing["sender"] != message["sender"] for existing in state["approvals"]):
            state["approvals"].append(event)
        threshold = int(ENV.get("APPROVAL_THRESHOLD", "1"))
        if len(state["approvals"]) >= threshold:
            state["status"] = "approved"
            write_state(state)
            if state.get("course_operation_manifest"):
                moodle_result = execute_course_operation_manifest(state)
            else:
                moodle_result = execute_after_approval(
                    {
                        **ENV,
                        "APPROVAL_RUN_ID": state.get("run_id", ""),
                        "MOODLE_AGENT_RESULT_ENDPOINT": f"{base_url()}/agent/moodle-result",
                        "MOODLE_AGENT_PROGRESS_ENDPOINT": f"{base_url()}/agent/moodle-progress",
                    }
                )
            state = read_state()
            state["status"] = moodle_result["status"]
            state["moodle_result"] = moodle_result
            write_state(state)
            if moodle_result.get("status") == "moodle_agent_browser_action_pending":
                handle_moodle_agent_progress(
                    {
                        "event": "moodle_task_created",
                        "run_id": state.get("run_id", ""),
                        "operation_id": moodle_result.get("operation_id", ""),
                        "agent_name": course_agent_name(),
                        "detail": f"Moodle browser-agent task written to {moodle_result.get('moodle_agent_task_path', '')}",
                    }
                )
        else:
            state["status"] = "waiting_for_more_approvals"
            write_state(state)
    elif command["command"] == "REJECT":
        state["status"] = "rejected"
        state["decision"] = "REJECT"
        state["decision_sender"] = event["sender"]
        state["decision_recorded_at"] = event["recorded_at"]
        state["rejections"].append(event)
        write_state(state)
    append_ledger({"event": "command_handled", "message": message, "command": command, "state": read_state()})
    return {"handled": True, "command": command["command"], "state": read_state()}


def handle_agent_approval(payload: dict) -> dict:
    state = read_state()
    observed_direction = payload.get("observed_direction", "").strip().lower()
    require_incoming = ENV.get("AGENT_REQUIRE_INCOMING_REPLY", "1") == "1"
    incoming_aliases = {"incoming", "inbound", "message-in", "received"}
    single_account = ENV.get("AGENT_SINGLE_ACCOUNT_TEST_MODE", "0") == "1"
    self_test_aliases = {"outgoing", "message-out", "you", "self", "outgoing_self_test"}
    direction_ok = observed_direction in incoming_aliases or (
        single_account and observed_direction in self_test_aliases
    )
    if require_incoming and not direction_ok:
        append_ledger(
            {
                "event": "agent_report_rejected_not_incoming",
                "payload": {
                    key: payload.get(key)
                    for key in [
                        "agent_name",
                        "source_channel",
                        "chat_name",
                        "observed_direction",
                        "whatsapp_sender",
                        "profile_name",
                        "body",
                        "evidence",
                    ]
                },
            }
        )
        raise ValueError(
            "Agent report must identify a valid new WhatsApp reply: incoming in production, "
            "or current-run You/outgoing in single-account smoke mode."
        )
    message_id = str(payload.get("message_id") or "").strip()
    if not message_id and not single_account:
        raise ValueError("WhatsApp approval reply must include a stable current message_id.")
    bound_requester = str(state.get("whatsapp_requester") or "").strip()
    observed_requester = str(
        payload.get("profile_name")
        or payload.get("whatsapp_sender")
        or payload.get("sender")
        or ""
    ).strip()
    if bound_requester and observed_requester.casefold() != bound_requester.casefold():
        raise ValueError("WhatsApp approval reply did not come from the current workflow requester.")
    bound_chat_type = str(state.get("whatsapp_chat_type") or payload.get("chat_type") or "").strip().lower()
    if bound_chat_type == "group" and not bool(payload.get("agent_mentioned")) and not bound_requester:
        raise ValueError(
            "WhatsApp group approval without a mention must be bound to the current workflow requester."
        )
    if bound_chat_type == "group" and not bool(payload.get("agent_mentioned")):
        append_ledger(
            {
                "event": "bound_group_approval_without_mention_accepted",
                "run_id": payload.get("run_id", ""),
                "reason": "exact command, stable message_id, and current workflow requester matched",
            }
        )

    if single_account and observed_direction in self_test_aliases:
        append_ledger(
            {
                "event": "single_account_self_reply_accepted",
                "run_id": payload.get("run_id", ""),
                "observed_direction": observed_direction,
                "reason": "single-account smoke test treats You/outgoing as approver reply",
            }
        )

    message = {
        "sender": payload.get("whatsapp_sender")
        or payload.get("sender")
        or f"agent:{payload.get('agent_name', course_agent_name())}",
        "profile_name": payload.get("profile_name", course_agent_approver_name()),
        "message_id": message_id or f"agent-{now()}",
        "body": payload.get("body", "").strip(),
        "agent_report": {
            "agent_name": payload.get("agent_name", course_agent_name()),
            "source_channel": payload.get("source_channel", "whatsapp_web"),
            "chat_name": payload.get("chat_name", course_agent_chat_name()),
            "observed_direction": observed_direction,
            "run_id": payload.get("run_id", ""),
            "chat_type": bound_chat_type,
            "agent_mentioned": bool(payload.get("agent_mentioned")),
            "single_account_test_mode": single_account,
            "evidence": payload.get("evidence", ""),
        },
    }
    handle_agent_progress(
        {
            "event": "approval_reply_observed",
            "run_id": payload.get("run_id", ""),
            "agent_name": payload.get("agent_name", course_agent_name()),
            "chat_name": payload.get("chat_name", course_agent_chat_name()),
            "observed_direction": observed_direction,
            "evidence": payload.get("evidence", ""),
        }
    )
    append_ledger({"event": "agent_whatsapp_reply_detected", "message": message})
    result = handle_command(message)
    handle_agent_progress(
        {
            "event": "approval_report_posted",
            "run_id": payload.get("run_id", ""),
            "agent_name": payload.get("agent_name", course_agent_name()),
            "chat_name": payload.get("chat_name", course_agent_chat_name()),
            "detail": "agent approval payload accepted by local endpoint",
        }
    )
    return result


MOODLE_AGENT_STATUS_TO_FLOW_STATE = {
    "PASS": "moodle_external_action_verified",
    "BLOCKED": "moodle_external_action_blocked",
    "FAIL": "moodle_external_action_failed",
}

MOODLE_PROGRESS_EVENT_TO_STATUS = {
    "moodle_task_created": "moodle_agent_task_created",
    "moodle_browser_started": "moodle_agent_browser_started",
    "moodle_tabs_claimed": "moodle_agent_tabs_claimed",
    "moodle_target_opened": "moodle_agent_target_opened",
    "moodle_precheck_started": "moodle_agent_precheck_started",
    "moodle_precheck_passed": "moodle_agent_precheck_passed",
    "moodle_precheck_blocked": "moodle_agent_precheck_blocked",
    "moodle_execute_save_started": "moodle_agent_execute_save_started",
    "moodle_execute_save_completed": "moodle_agent_execute_save_completed",
    "moodle_execute_verified": "moodle_agent_execute_verified",
    "moodle_returned_to_course": "moodle_agent_returned_to_course",
    "moodle_rollback_save_started": "moodle_agent_rollback_save_started",
    "moodle_rollback_save_completed": "moodle_agent_rollback_save_completed",
    "moodle_rollback_verified": "moodle_agent_rollback_verified",
    "moodle_result_post_started": "moodle_agent_result_post_started",
    "moodle_result_posted": "moodle_agent_result_posted",
    "moodle_agent_blocked": "moodle_agent_blocked",
    "moodle_agent_failed": "moodle_agent_failed",
    "moodle_timeout_before_precheck": "moodle_agent_timeout_before_precheck",
    "moodle_timeout_after_precheck_before_save": "moodle_agent_timeout_after_precheck_before_save",
    "moodle_timeout_after_execute_before_rollback": "moodle_agent_timeout_after_execute_before_rollback",
    "moodle_timeout_during_rollback": "moodle_agent_timeout_during_rollback",
    "moodle_timeout_after_rollback_before_result": "moodle_agent_timeout_after_rollback_before_result",
    "moodle_timeout_needs_human_check": "moodle_agent_timeout_needs_human_check",
}
MOODLE_TERMINAL_PROGRESS_EVENTS = {"moodle_timeout_needs_human_check"}
MOODLE_PROGRESS_STATE_FIELDS = (
    "title",
    "visibility",
    "access_restrictions",
    "url",
    "current_url",
    "target_id",
    "saves_count",
    "final_title",
    "final_visibility",
    "final_access_restrictions",
    "file_area",
    "files",
    "uploaded_file",
    "final_file_area",
    "final_files",
    "recovered",
)


def handle_moodle_agent_progress(payload: dict) -> dict:
    state = read_state()
    event = str(payload.get("event", "")).strip()
    if event not in MOODLE_PROGRESS_EVENT_TO_STATUS:
        allowed = ", ".join(sorted(MOODLE_PROGRESS_EVENT_TO_STATUS))
        raise ValueError(f"Moodle agent progress event must be one of: {allowed}.")

    current_run_id = state.get("run_id", "")
    run_id = str(payload.get("run_id", "")).strip() or current_run_id
    if current_run_id and run_id and run_id != current_run_id:
        raise ValueError(f"Moodle progress run_id {run_id!r} does not match current run {current_run_id!r}.")

    operation_id = str(payload.get("operation_id", "")).strip()
    current_operation_id = state.get("moodle_result", {}).get("operation_id", "")
    if current_operation_id and operation_id and operation_id != current_operation_id:
        raise ValueError(
            f"Moodle progress operation_id {operation_id!r} does not match current operation "
            f"{current_operation_id!r}."
        )

    status = MOODLE_PROGRESS_EVENT_TO_STATUS[event]
    entry = {
        "event": event,
        "status": status,
        "run_id": run_id,
        "operation_id": operation_id or current_operation_id,
        "agent_name": payload.get("agent_name", course_agent_name()),
        "detail": payload.get("detail", ""),
        "evidence": payload.get("evidence", ""),
        "attempt": payload.get("attempt", ""),
        "recorded_at": now(),
    }
    for key in MOODLE_PROGRESS_STATE_FIELDS:
        if key in payload:
            entry[key] = payload[key]

    state.setdefault("moodle_agent_progress", []).append(entry)
    state["moodle_agent_status"] = status
    state["moodle_agent_last_event"] = event
    state["moodle_agent_last_progress_at"] = entry["recorded_at"]

    for field, timestamp_key in (
        ("moodle_precheck_passed", "moodle_precheck_passed_at"),
        ("moodle_execute_save_completed", "moodle_execute_save_completed_at"),
        ("moodle_execute_verified", "moodle_execute_verified_at"),
        ("moodle_returned_to_course", "moodle_returned_to_course_at"),
        ("moodle_rollback_save_completed", "moodle_rollback_save_completed_at"),
        ("moodle_rollback_verified", "moodle_rollback_verified_at"),
        ("moodle_result_posted", "moodle_result_posted_at"),
    ):
        if event == field:
            state[timestamp_key] = entry["recorded_at"]

    observed_state = {
        key: entry[key]
        for key in (
            "title",
            "visibility",
            "access_restrictions",
            "final_title",
            "final_visibility",
            "final_access_restrictions",
            "file_area",
            "files",
            "uploaded_file",
            "final_file_area",
            "final_files",
            "current_url",
            "url",
        )
        if key in entry and entry[key] not in ("", None)
    }
    if observed_state:
        state["moodle_agent_last_observed_state"] = observed_state

    if event.startswith("moodle_timeout"):
        state["moodle_agent_timeout_at"] = entry["recorded_at"]
        state["moodle_agent_timeout_event"] = event

    if event in MOODLE_TERMINAL_PROGRESS_EVENTS:
        state["status"] = status
        moodle_result = dict(state.get("moodle_result", {}))
        moodle_result.setdefault("operation_id", operation_id or current_operation_id)
        moodle_result.update(
            {
                "status": status,
                "agent_status": "TIMEOUT",
                "note": entry.get("detail", ""),
                "recorded_at": entry["recorded_at"],
            }
        )
        state["moodle_result"] = moodle_result

    write_state(state)
    append_ledger({"event": "moodle_agent_progress", "progress": entry, "state_status": state.get("status", "")})
    return {"handled": True, "progress": entry, "state": state}


def normalized_local_path(value: str) -> str:
    if not value:
        return ""
    if any(char.isspace() for char in value) and not Path(value).exists():
        return value
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return str(path)


def handle_moodle_agent_result(payload: dict) -> dict:
    state = read_state()
    status = str(payload.get("status", "")).strip().upper()
    if status not in MOODLE_AGENT_STATUS_TO_FLOW_STATE:
        raise ValueError("Moodle agent result status must be PASS, BLOCKED, or FAIL.")

    operation_id = str(payload.get("operation_id", "")).strip()
    if not operation_id:
        raise ValueError("Moodle agent result must include operation_id.")
    current_operation_id = state.get("moodle_result", {}).get("operation_id", "")
    if current_operation_id and operation_id != current_operation_id:
        raise ValueError(
            f"Moodle agent result operation_id {operation_id!r} does not match current "
            f"operation {current_operation_id!r}."
        )

    run_id = str(payload.get("run_id", "")).strip()
    current_run_id = state.get("run_id", "")
    if run_id and current_run_id and run_id != current_run_id:
        raise ValueError(f"Moodle agent result run_id {run_id!r} does not match current run {current_run_id!r}.")

    try:
        saves_count = int(payload.get("saves_count", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("Moodle agent result saves_count must be an integer.") from exc

    final_state = dict(payload.get("final_moodle_state") or {})
    if payload.get("final_title"):
        final_state["title"] = payload.get("final_title")
    if payload.get("final_visibility"):
        final_state["visibility"] = payload.get("final_visibility")
    if payload.get("final_access_restrictions"):
        final_state["access_restrictions"] = payload.get("final_access_restrictions")
    if payload.get("final_file_area"):
        final_state["file_area"] = payload.get("final_file_area")
    if payload.get("final_files"):
        final_state["files"] = payload.get("final_files")

    result = {
        "status": MOODLE_AGENT_STATUS_TO_FLOW_STATE[status],
        "agent_status": status,
        "agent_name": payload.get("agent_name", course_agent_name()),
        "batch_id": state.get("batch_id", ENV["BATCH_ID"]),
        "run_id": run_id or current_run_id,
        "operation_id": operation_id,
        "saves_count": saves_count,
        "final_moodle_state": final_state,
        "evidence_report": normalized_local_path(str(payload.get("evidence_report", ""))),
        "log": normalized_local_path(str(payload.get("log", ""))),
        "note": payload.get("note", ""),
        "recorded_at": now(),
    }
    if isinstance(payload.get("results"), list):
        result["results"] = [item for item in payload["results"] if isinstance(item, dict)]

    previous_state_status = state.get("status", "")
    previous_moodle_result = dict(state.get("moodle_result", {}))
    previous_moodle_status = previous_moodle_result.get("status", "")
    state["status"] = result["status"]
    previous_moodle_result.update(result)
    previous_moodle_result["previous_status"] = previous_moodle_status
    state["moodle_result"] = previous_moodle_result
    write_state(state)
    append_ledger(
        {
            "event": "moodle_agent_browser_result_recorded",
            "result": result,
            "previous_flow_state_status": previous_state_status,
            "previous_operation_id": current_operation_id,
        }
    )
    handle_moodle_agent_progress(
        {
            "event": "moodle_result_posted",
            "run_id": result["run_id"],
            "operation_id": operation_id,
            "agent_name": result["agent_name"],
            "detail": f"Moodle agent result accepted with status {status}.",
            "saves_count": saves_count,
            "final_title": final_state.get("title", ""),
            "final_visibility": final_state.get("visibility", ""),
            "final_access_restrictions": final_state.get("access_restrictions", ""),
            "final_file_area": final_state.get("file_area", ""),
            "final_files": final_state.get("files", ""),
        }
    )
    return {"handled": True, "result": result, "state": read_state()}


def report_form(result: dict | None = None, error: str | None = None) -> bytes:
    state = read_state()
    batch_id = state.get("batch_id", ENV["BATCH_ID"])
    run_id = state.get("run_id", "")
    approval_code = state.get("approval_code", "")
    commands = approval_reply_commands(batch_id, run_id, approval_code=approval_code)
    default_body = ""
    status_html = ""
    if result is not None:
        status_html = (
            "<section class='ok'><h2>Submitted</h2><pre>"
            + escape(json.dumps(result, indent=2, ensure_ascii=False))
            + "</pre></section>"
        )
    if error:
        status_html = "<section class='error'><h2>Error</h2><pre>" + escape(error) + "</pre></section>"

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent Approval Report</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; max-width: 860px; color: #17212b; }}
    label {{ display: block; font-weight: 700; margin: 18px 0 6px; }}
    input, textarea {{ width: 100%; box-sizing: border-box; font: inherit; padding: 10px; border: 1px solid #b8c2cc; border-radius: 6px; }}
    textarea {{ min-height: 96px; }}
    button {{ margin-top: 16px; padding: 10px 16px; font: inherit; font-weight: 700; border: 0; border-radius: 6px; background: #0b5fff; color: white; }}
    code, pre {{ background: #f4f6f8; border-radius: 6px; padding: 2px 5px; }}
    pre {{ overflow: auto; padding: 12px; }}
    .ok {{ border-left: 4px solid #16833b; padding-left: 12px; }}
    .error {{ border-left: 4px solid #c62828; padding-left: 12px; }}
  </style>
</head>
<body>
  <h1>Agent Approval Report</h1>
  <p>This form is for the agent to report the exact new incoming WhatsApp command it detected in the configured approval chat.</p>
  <p>Current run: <code>{escape(run_id)}</code></p>
  <p>Expected examples: <code>{escape(commands['approve'])}</code>, <code>{escape(commands['reject'])}</code>.</p>
  {status_html}
  <form method="post" action="/agent/report">
    <label for="agent_name">Agent name</label>
    <input id="agent_name" name="agent_name" value="{escape(course_agent_name())}">
    <label for="chat_name">WhatsApp chat</label>
    <input id="chat_name" name="chat_name" value="{escape(course_agent_chat_name())}">
    <label for="run_id">Run id</label>
    <input id="run_id" name="run_id" value="{escape(run_id)}">
    <label for="observed_direction">Observed direction</label>
    <input id="observed_direction" name="observed_direction" value="incoming">
    <label for="whatsapp_sender">WhatsApp sender</label>
    <input id="whatsapp_sender" name="whatsapp_sender" value="">
    <label for="profile_name">Profile name</label>
    <input id="profile_name" name="profile_name" value="{escape(course_agent_approver_name())}">
    <label for="body">Exact WhatsApp reply</label>
    <textarea id="body" name="body">{escape(default_body)}</textarea>
    <label for="evidence">Agent evidence note</label>
    <textarea id="evidence" name="evidence">New incoming message observed after the approval request in the configured WhatsApp chat.</textarea>
    <button type="submit">Submit Approval Reply</button>
  </form>
</body>
</html>
"""
    return html.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, data: dict) -> None:
        body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def _read_form(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        data = parse_qs(raw)
        return {key: values[0] if values else "" for key, values in data.items()}

    def _send_html(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[%s] %s\n" % (now(), fmt % args))

    def _send_agent_progress_ack(self, result: dict) -> None:
        progress = result.get("progress", {})
        self._send_json(
            200,
            {
                "ok": True,
                "handled": bool(result.get("handled")),
                "event": progress.get("event", ""),
                "status": progress.get("status", ""),
                "run_id": progress.get("run_id", ""),
                "operation_id": progress.get("operation_id", ""),
            },
        )

    def _send_agent_approval_ack(self, result: dict) -> None:
        state = result.get("state", {})
        self._send_json(
            200,
            {
                "ok": True,
                "handled": bool(result.get("handled")),
                "command": result.get("command", ""),
                "run_id": state.get("run_id", ""),
                "status": state.get("status", ""),
            },
        )

    def _send_moodle_result_ack(self, result: dict) -> None:
        recorded = result.get("result", {})
        self._send_json(
            200,
            {
                "ok": True,
                "handled": bool(result.get("handled")),
                "agent_status": recorded.get("agent_status", ""),
                "status": recorded.get("status", ""),
                "run_id": recorded.get("run_id", ""),
                "operation_id": recorded.get("operation_id", ""),
                "saves_count": recorded.get("saves_count", 0),
            },
        )

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(200, {"ok": True, "mode": whatsapp_mode(), "state": read_state()})
            return
        if parsed.path == "/status":
            self._send_json(200, read_state())
            return
        if parsed.path == "/start":
            try:
                result = start_approval_run()
            except Exception as exc:
                append_ledger({"event": "approval_start_failed", "error": str(exc)})
                self._send_json(500, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)
            return
        if parsed.path == "/webhook":
            query = parse_qs(parsed.query)
            verify_token = query.get("hub.verify_token", [""])[0]
            challenge = query.get("hub.challenge", [""])[0]
            mode = query.get("hub.mode", [""])[0]
            if mode == "subscribe" and verify_token == ENV.get("WHATSAPP_VERIFY_TOKEN"):
                body = challenge.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                append_ledger({"event": "webhook_verified"})
                return
            self._send_json(403, {"ok": False, "error": "webhook verification failed"})
            return
        if parsed.path == "/agent/task":
            state = read_state()
            if not state.get("run_id"):
                state = initial_state()
                write_state(state)
            task_path = write_agent_task(state)
            self._send_json(
                200,
                {
                    "ok": True,
                    "run_id": state.get("run_id", ""),
                    "chat_name": course_agent_chat_name(),
                    "approval_message": approval_message(state.get("run_id", "")),
                    "approval_attachments": approval_attachments(state.get("run_id", "")),
                    "agent_task_path": str(task_path),
                    "agent_report_endpoint": f"{base_url()}/agent/approval",
                    "agent_progress_endpoint": f"{base_url()}/agent/progress",
                    "course_task_intent_endpoint": f"{base_url()}/agent/course-task-intent",
                    "course_operation_manifest_endpoint": f"{base_url()}/agent/course-operation-manifest",
                    "course_agent_result_endpoint": f"{base_url()}/agent/course-agent-result",
                },
            )
            return
        if parsed.path == "/agent/report":
            self._send_html(200, report_form())
            return
        if parsed.path == "/agent/moodle-task":
            state = read_state()
            task_path_text = state.get("moodle_result", {}).get("moodle_agent_task_path", "")
            task_path = Path(task_path_text) if task_path_text else None
            task_text = ""
            if task_path and task_path.exists():
                task_text = task_path.read_text(encoding="utf-8")
            self._send_json(
                200,
                {
                    "ok": bool(task_path_text),
                    "run_id": state.get("run_id", ""),
                    "status": state.get("status", ""),
                    "moodle_agent_task_path": task_path_text,
                    "moodle_agent_result_endpoint": f"{base_url()}/agent/moodle-result",
                    "moodle_agent_progress_endpoint": f"{base_url()}/agent/moodle-progress",
                    "task_text": task_text,
                },
            )
            return
        if parsed.path == "/agent/course-task-intent":
            state = read_state()
            self._send_json(
                200,
                {
                    "ok": bool(state.get("course_task_intent")),
                    "run_id": state.get("run_id", ""),
                    "course_task_intent": state.get("course_task_intent", {}),
                },
            )
            return
        if parsed.path == "/agent/course-operation-manifest":
            state = read_state()
            self._send_json(
                200,
                {
                    "ok": bool(state.get("course_operation_manifest")),
                    "run_id": state.get("run_id", ""),
                    "course_operation_manifest": state.get("course_operation_manifest", {}),
                },
            )
            return
        if parsed.path == "/agent/course-agent-result":
            state = read_state()
            self._send_json(
                200,
                {
                    "ok": bool(state.get("course_agent_result")),
                    "run_id": state.get("run_id", ""),
                    "course_agent_result": state.get("course_agent_result", {}),
                },
            )
            return
        self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/start":
            try:
                payload = self._read_json()
                result = start_approval_run(payload.get("env_overrides") or {})
            except ValueError as exc:
                append_ledger({"event": "approval_start_rejected", "error": str(exc)})
                self._send_json(422, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:
                append_ledger({"event": "approval_start_failed", "error": str(exc)})
                self._send_json(500, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)
            return
        if parsed.path == "/agent/course-task-intent":
            try:
                result = handle_course_task_intent(self._read_json())
            except ValueError as exc:
                append_ledger({"event": "course_task_intent_rejected", "error": str(exc)})
                self._send_json(422, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:
                append_ledger({"event": "course_task_intent_failed", "error": str(exc)})
                self._send_json(500, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, {"ok": True, "handled": True, "intent": result.get("intent", {}), "status": result.get("state", {}).get("status", "")})
            return
        if parsed.path == "/agent/course-operation-manifest":
            try:
                result = handle_course_operation_manifest(self._read_json())
            except ValueError as exc:
                append_ledger({"event": "course_operation_manifest_rejected", "error": str(exc)})
                self._send_json(422, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:
                append_ledger({"event": "course_operation_manifest_failed", "error": str(exc)})
                self._send_json(500, {"ok": False, "error": str(exc)})
                return
            self._send_json(
                200,
                {
                    "ok": bool(result.get("ok", True)),
                    "handled": True,
                    "status": result.get("state", {}).get("status", result.get("status", "")),
                },
            )
            return
        if parsed.path == "/agent/course-agent-result":
            try:
                result = handle_course_agent_result(self._read_json())
            except ValueError as exc:
                append_ledger({"event": "course_agent_result_rejected", "error": str(exc)})
                self._send_json(422, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:
                append_ledger({"event": "course_agent_result_failed", "error": str(exc)})
                self._send_json(500, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, {"ok": True, "handled": True, "result": result.get("result", {})})
            return
        if parsed.path == "/agent/progress":
            try:
                result = handle_agent_progress(self._read_json())
            except ValueError as exc:
                append_ledger({"event": "agent_progress_rejected", "error": str(exc)})
                self._send_json(422, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:
                append_ledger({"event": "agent_progress_failed", "error": str(exc)})
                self._send_json(500, {"ok": False, "error": str(exc)})
                return
            self._send_agent_progress_ack(result)
            return
        if parsed.path == "/agent/report":
            try:
                form = self._read_form()
                result = handle_agent_approval(
                    {
                        "agent_name": form.get("agent_name", course_agent_name()),
                        "source_channel": "whatsapp_web",
                        "chat_name": form.get("chat_name", course_agent_chat_name()),
                        "observed_direction": form.get("observed_direction", ""),
                        "run_id": form.get("run_id", ""),
                        "whatsapp_sender": form.get("whatsapp_sender", ""),
                        "profile_name": form.get("profile_name", course_agent_approver_name()),
                        "body": form.get("body", ""),
                        "evidence": form.get("evidence", ""),
                    }
                )
            except ValueError as exc:
                append_ledger({"event": "agent_report_rejected", "error": str(exc)})
                self._send_html(422, report_form(error=str(exc)))
                return
            except Exception as exc:
                append_ledger({"event": "agent_report_failed", "error": str(exc)})
                self._send_html(500, report_form(error=str(exc)))
                return
            self._send_html(200, report_form(result=result))
            return
        if parsed.path == "/agent/approval":
            try:
                result = handle_agent_approval(self._read_json())
            except ValueError as exc:
                append_ledger({"event": "agent_approval_rejected", "error": str(exc)})
                self._send_json(422, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:
                append_ledger({"event": "agent_approval_failed", "error": str(exc)})
                self._send_json(500, {"ok": False, "error": str(exc)})
                return
            self._send_agent_approval_ack(result)
            return
        if parsed.path == "/agent/moodle-progress":
            try:
                result = handle_moodle_agent_progress(self._read_json())
            except ValueError as exc:
                append_ledger({"event": "moodle_agent_progress_rejected", "error": str(exc)})
                self._send_json(422, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:
                append_ledger({"event": "moodle_agent_progress_failed", "error": str(exc)})
                self._send_json(500, {"ok": False, "error": str(exc)})
                return
            self._send_agent_progress_ack(result)
            return
        if parsed.path == "/agent/moodle-result":
            try:
                result = handle_moodle_agent_result(self._read_json())
            except ValueError as exc:
                append_ledger({"event": "moodle_agent_result_rejected", "error": str(exc)})
                self._send_json(422, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:
                append_ledger({"event": "moodle_agent_result_failed", "error": str(exc)})
                self._send_json(500, {"ok": False, "error": str(exc)})
                return
            self._send_moodle_result_ack(result)
            return
        if parsed.path != "/webhook":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        try:
            payload = self._read_json()
            messages = parse_whatsapp_payload(payload)
            results = [handle_command(message) for message in messages]
        except Exception as exc:
            append_ledger({"event": "webhook_post_failed", "error": str(exc)})
            self._send_json(500, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, {"ok": True, "messages": messages, "results": results, "state": read_state()})


def main() -> None:
    mode = whatsapp_mode()
    for warning in unsupported_legacy_env_warnings(ENV):
        print(f"Warning: {warning}")
    missing = [] if mode == "codex_agent_web" else required_env(["WHATSAPP_VERIFY_TOKEN"])
    if missing:
        print(f"Missing required config for server: {', '.join(missing)}", file=sys.stderr)
        sys.exit(2)

    state = read_state()
    write_state(state)
    port = int(ENV.get("PORT", "8787"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Approval server running on http://localhost:{port}")
    print(f"WhatsApp mode: {mode}")
    print(f"Health: http://localhost:{port}/health")
    print(f"Start WhatsApp approval: http://localhost:{port}/start")
    print(f"Agent task: http://localhost:{port}/agent/task")
    print(f"Agent approval endpoint: http://localhost:{port}/agent/approval")
    print(f"Agent progress endpoint: http://localhost:{port}/agent/progress")
    print(f"Moodle agent progress endpoint: http://localhost:{port}/agent/moodle-progress")
    print(f"Webhook path for Meta/ngrok: /webhook")
    server.serve_forever()


if __name__ == "__main__":
    main()
