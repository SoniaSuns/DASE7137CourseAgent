from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import html
import zipfile
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo


def configure_utf8_diagnostic_streams(*streams) -> None:
    """Keep Windows diagnostic output from crashing on valid Unicode prompts."""

    targets = streams or (sys.stdout, sys.stderr)
    for stream in targets:
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, OSError, ValueError):
            # Embedded/test streams may not support reconfiguration. Logging
            # must never prevent the long-running Agent from starting.
            continue


configure_utf8_diagnostic_streams()

APP_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = APP_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from course_agent import (  # noqa: E402
    ALLOWED_OPERATION_KINDS,
    SANDBOX_COURSE_ID,
    SANDBOX_COURSE_URL,
    apply_intent_defaults,
    apply_manifest_defaults,
    build_codex_exec_command,
    capability_specs,
    resolve_capability_manifest,
    validate_course_operation_manifest,
    validate_course_task_intent,
)
from moodle_operations import execute_after_approval  # noqa: E402
from run_agent_listener import watch_moodle_agent_stage  # noqa: E402
from run_agent_smoke import load_env, request_json, start_server  # noqa: E402
from run_moodle_course_clear_file_resources import call_apim_contents, flatten_modules  # noqa: E402
from run_moodle_course_file_resource_delete import (  # noqa: E402
    cmid_from_resource_url,
    execute_delete_operation,
)
from run_moodle_browser_task_executor import execute_browser_moodle_task  # noqa: E402
from run_moodle_module_delete import execute_module_delete_operation  # noqa: E402
from run_moodle_module_visibility import execute_module_visibility_operation  # noqa: E402
from run_moodle_module_access_time import execute_module_access_time_operation  # noqa: E402
from run_moodle_module_title import execute_module_title_operation  # noqa: E402
from run_moodle_section_rename import execute_section_rename_operation  # noqa: E402
from course_refresh_job import (  # noqa: E402
    OPERATION_KIND as COURSE_REFRESH_BATCH_UPLOAD_KIND,
    execute_course_refresh_batch_upload,
    is_course_refresh_operation,
    prepare_course_refresh_operation,
    resolve_local_path as resolve_refresh_local_path,
)
from file_refresh_preview import (  # noqa: E402
    OPERATION_KIND as REFRESH_FILE_PREVIEW_KIND,
    REFERENCE_PACK_REQUIRED_FILENAMES,
    execute_refresh_file_preview_operation,
    is_refresh_file_preview_operation,
    reference_pack_path as refresh_reference_pack_path,
    resolve_local_path as resolve_preview_local_path,
    visible_text as refresh_visible_text,
)
from file_refresh_batch_preview import (  # noqa: E402
    FINALIZE_OPERATION_KIND as FINALIZE_FILE_BATCH_PREVIEW_KIND,
    OPERATION_KIND as REFRESH_FILE_BATCH_PREVIEW_KIND,
    active_batch_editable_path_keys,
    active_batch_upload_path_keys,
    batch_file_paths,
    execute_finalize_file_batch_preview_operation,
    execute_refresh_file_batch_preview_operation,
    is_finalize_file_batch_preview_operation,
    is_refresh_file_batch_preview_operation,
    load_batch_state,
    mark_batch_item_delivery_failed,
    mark_batch_item_delivered,
    mark_final_batch_pdf_delivered,
    planner_safe_active_batch_context,
    planner_safe_recent_finalized_batch_context,
    verify_batch_item_preview,
    verify_final_batch_pdf,
)
from reference_pack_rebuild import (  # noqa: E402
    OPERATION_KIND as REBUILD_REFERENCE_PACK_KIND,
    REFERENCE_SOURCE_ROLES,
    execute_rebuild_reference_pack_operation,
    is_rebuild_reference_pack_operation,
    resolve_local_path as resolve_reference_local_path,
)
from semester_schedule_update import (  # noqa: E402
    OPERATION_KIND as UPDATE_SEMESTER_SCHEDULE_KIND,
    execute_update_semester_schedule_operation,
    is_update_semester_schedule_operation,
)
from timeline_draft_review import (  # noqa: E402
    COMMIT_OPERATION_KIND as COMMIT_TIMELINE_DRAFT_KIND,
    REVISE_OPERATION_KIND as REVISE_TIMELINE_DRAFT_KIND,
    execute_commit_timeline_draft_operation,
    execute_revise_timeline_draft_operation,
    is_commit_timeline_draft_operation,
    is_revise_timeline_draft_operation,
    planner_safe_active_timeline_draft_context,
)
from run_moodle_course_file_resource_upload import (  # noqa: E402
    DEFAULT_CHROME_PATH,
    execute_course_file_resource_upload_operation,
    load_env as load_moodle_browser_env,
    looks_like_login,
    page_summary,
    save_screenshot,
    visible_text,
    write_result,
)
from run_moodle_course_activity_shell_batch import execute_one_in_context  # noqa: E402
from moodle_operations.api_client import call_moodle_api  # noqa: E402
from whatsapp_approval import parse_approval_command  # noqa: E402
from moodle_group_structure import (  # noqa: E402
    GROUP_STRUCTURE_READBACK_KIND,
    base_group_structure,
    group_action_preflight_hash,
    group_structure_hash,
    manifest_has_group_writes,
    manifest_needs_detailed_group_structure,
    read_detailed_group_structure,
)
from moodle_group_membership import execute_group_membership_operation  # noqa: E402
from moodle_backup_evidence import configured_backup_evidence  # noqa: E402
from document_change_model import sanitized_group_snapshot_hash  # noqa: E402
from run_moodle_course_file_upload_batch import safe_name  # noqa: E402
from run_moodle_course_file_reset_reupload import confirm_delete_current_page, goto_with_retry  # noqa: E402
from run_whatsapp_web_smoke import (  # noqa: E402
    approval_command_from_record,
    approval_reply_command,
    close_whatsapp_document_viewer,
    course_agent_chat_name,
    current_whatsapp_chat_context,
    discover_whatsapp_account_name,
    document_attachment_counts,
    ensure_chat_compose_ready,
    exact_mention_suggestion_count,
    expand_collapsed_approval_requests,
    import_playwright,
    launch_whatsapp_context,
    message_records,
    multiline_message_matches,
    normalize_message_text,
    open_chat,
    outgoing_multiline_message_count,
    post_json,
    record_contains_approval_request,
    record_contains_collapsed_approval_request,
    record_direction,
    record_has_real_mention,
    record_key,
    record_message_id,
    record_sender_display_name,
    scroll_chat_to_bottom,
    send_message,
    send_message_with_attachments,
    strip_record_mentions,
    wait_for_approval_reply,
    wait_for_attachment_counts,
    wait_for_sent_request,
    wait_for_whatsapp_ready,
    WhatsAppSendUnconfirmedError,
    whatsapp_safe_compose_text,
    whatsapp_session_health,
)


MOODLE_KEYCHAIN_ACCOUNT = "sandbox-105330"
MOODLE_USERNAME_KEYCHAIN_SERVICE = "course-agent-moodle-username"
MOODLE_PASSWORD_KEYCHAIN_SERVICE = "course-agent-moodle-password"


ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
PROMPT_ATTACHMENT_ROOT = PROJECT_ROOT / "course_files" / "evidence" / "process" / "course_agent_app" / "whatsapp_prompt_attachments"
CONSUMED_PROMPT_KEYS_PATH = PROJECT_ROOT / "course_files" / "evidence" / "process" / "course_agent_app" / "course_agent_consumed_prompt_keys.json"
LATEST_REFRESH_PREVIEW_CONTEXT_PATH = PROJECT_ROOT / "course_files" / "evidence" / "process" / "course_agent_app" / "latest_file_refresh_preview_context.json"
PENDING_REFERENCE_COLLECTION_PATH = PROJECT_ROOT / "course_files" / "evidence" / "process" / "course_agent_app" / "pending_reference_collection.json"
UPLOAD_REPORT_ROOTS = (
    PROJECT_ROOT / "course_files" / "evidence" / "process" / "course_agent_app" / "reports",
    ROOT / "reports",
)
HK_TZ = ZoneInfo("Asia/Hong_Kong")
REFERENCE_COLLECTION_ATTACHMENTS_HEADING = "Reference attachments collected for rebuild:"
REFERENCE_COLLECTION_STATUS_HEADING = "Reference collection status:"
OUTBOUND_PURPOSES = {"lifecycle", "acknowledgement", "progress", "action_required", "terminal"}


@dataclass
class StaffPromptContext:
    raw_task: str
    baseline_keys: set[str]
    observed_at: datetime
    requester: str = ""
    message_id: str = ""
    chat_type: str = "unknown"
    direction: str = "incoming"
    mentions: list[dict] = field(default_factory=list)
    provenance_confidence: str = "low"
    collection_started_at: datetime | None = None
    allowed_attachment_message_ids: set[str] = field(default_factory=set)

    def __iter__(self):
        yield self.raw_task
        yield self.baseline_keys
        yield self.observed_at


@dataclass(frozen=True)
class OutboundMessage:
    body: str
    purpose: str = "terminal"
    action_kind: str = ""
    requester: str = ""
    chat_type: str = ""
    run_id: str = ""
    attachments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.purpose not in OUTBOUND_PURPOSES:
            raise ValueError(f"Unsupported WhatsApp message purpose: {self.purpose}")


ACTIVE_STAFF_PROMPT_CONTEXT: StaffPromptContext | None = None


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def contains_agent_result_block(normalized: str) -> bool:
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    agent_headings = (
        "please approve this moodle change",
        "done. i",
        "i could not complete this moodle request",
        "i tried the moodle request",
        "i am still waiting to finish this moodle request",
        "this moodle request was not approved",
        "moodle request status",
        "moodle approval",
        "moodle result",
        "course agent blocked",
        "course agent lifecycle",
        "course agent is running",
        "course agent running",
        "course agent ready",
        "course agent stopped",
        "course agent has stopped",
        "course agent is stopping",
        "file refresh preview",
        "file refresh review pdf",
        "file refresh batch",
        "combined course documents pdf",
        "reference pack rebuild",
        "reference is ready.",
        "reference pack rebuild needs one more step.",
        "timeline review needed.",
        "timeline draft is ready for confirmation.",
        "timeline draft still needs review.",
        "timeline reference is ready.",
        "timeline draft action was blocked.",
        "semester schedule update is ready.",
        "semester schedule update did not finish.",
        "file received.",
        "reference file upload is still waiting.",
        "reference file upload timed out.",
    )
    if any(line.lower().startswith(agent_headings) for line in lines):
        return True
    for index, line in enumerate(lines):
        if not re.match(r"^Status:\s*\S+", line, flags=re.IGNORECASE):
            continue
        tail = "\n".join(lines[index : index + 12])
        if not re.search(r"^Task:\s+", tail, flags=re.MULTILINE):
            continue
        if re.search(r"^(Change|Verify|Reason|Course|Run):\s+", tail, flags=re.MULTILINE):
            return True
    return False


def is_command_or_agent_message(text: str) -> bool:
    normalized = normalize_message_text(text)
    if not normalized:
        return True
    if re.match(r"^(OK|NO)\b", normalized, flags=re.IGNORECASE):
        return True
    if contains_agent_result_block(normalized):
        return True
    lowered = normalized.lower()
    return lowered.startswith(
        (
            "moodle approval",
            "moodle result",
            "course agent blocked",
            "course agent lifecycle",
            "course agent is running",
            "course agent running",
            "course agent ready",
            "course agent stopped",
            "course agent has stopped",
            "course agent is stopping",
            "file refresh preview",
            "file refresh review pdf",
            "reference pack rebuild",
            "file received.",
            "reference file upload is still waiting.",
            "reference file upload timed out.",
            "请上传 reference 文件",
            "请上传reference文件",
            "please upload reference file",
            "please upload the reference file",
        )
    )


def is_agent_stop_command(text: str) -> bool:
    normalized = normalize_message_text(user_visible_task_text(text) or text).strip()
    compact = re.sub(r"\s+", " ", normalized).strip().lower()
    chinese_compact = re.sub(r"\s+", "", normalized).lower()
    exact_commands = {
        "stop",
        "quit",
        "exit",
        "shutdown",
        "stop agent",
        "quit agent",
        "exit agent",
        "shutdown agent",
        "停止",
        "退出",
        "关闭",
        "停止运行",
        "结束运行",
    }
    chinese_agent_commands = {
        "停止agent",
        "退出agent",
        "关闭agent",
        "结束agent",
        "停止代理",
        "退出代理",
        "关闭代理",
    }
    return compact in exact_commands or chinese_compact in chinese_agent_commands


def lifecycle_whatsapp_messages_enabled(env: dict[str, str]) -> bool:
    return env.get("COURSE_AGENT_LIFECYCLE_WHATSAPP_MESSAGES", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def lifecycle_start_text(*, moodle_ready: bool = True) -> str:
    if not moodle_ready:
        return (
            "Course agent is running.\n"
            "Moodle status: Login required in the visible agent window.\n"
            "Moodle structure work will remain blocked until login is complete.\n"
            f"Course: {SANDBOX_COURSE_ID} sandbox"
        )
    return (
        "Course agent is running.\n"
        "Send a Moodle request here when you are ready.\n"
        f"Course: {SANDBOX_COURSE_ID} sandbox"
    )


def lifecycle_stop_text(reason: str = "agent process stopped") -> str:
    return (
        "Course agent stopped.\n"
        f"Reason: {reason}\n"
        f"Course: {SANDBOX_COURSE_ID} sandbox"
    )


def post_app_progress(
    base_url: str,
    event: str,
    chat: str,
    detail: str = "",
    evidence: str = "",
    **extra,
) -> None:
    payload = {
        "event": event,
        "agent_name": "course-agent-app",
        "chat_name": chat,
        "detail": detail,
        "evidence": evidence,
    }
    payload.update(extra)
    post_json(f"{base_url}/agent/progress", payload)


def load_consumed_prompt_keys() -> set[str]:
    try:
        payload = json.loads(CONSUMED_PROMPT_KEYS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(payload, list):
        return set()
    return {str(item) for item in payload}


def remember_consumed_prompt_key(key: str) -> None:
    remember_consumed_prompt_keys({key})


def remember_consumed_prompt_keys(keys_to_add: set[str]) -> None:
    if not keys_to_add:
        return
    keys = load_consumed_prompt_keys()
    keys.update(str(key) for key in keys_to_add if str(key).strip())
    CONSUMED_PROMPT_KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONSUMED_PROMPT_KEYS_PATH.write_text(json.dumps(sorted(keys), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def stable_record_signature(record: dict) -> str:
    source = "document" if record.get("documents") else "message"
    meta = normalize_message_text(record.get("meta", ""))
    text = normalize_message_text(record.get("text", ""))
    documents = "\n".join(normalize_message_text(item) for item in record.get("documents", []) or [])
    signature = "\n".join([source, meta, text, documents]).strip()
    return f"stable_record:{signature}" if signature else ""


def record_identity_keys(record: dict) -> set[str]:
    keys = {record_key(record)}
    signature = stable_record_signature(record)
    if signature:
        keys.add(signature)
    return {key for key in keys if str(key).strip()}


def record_is_known(record: dict, known_keys: set[str]) -> bool:
    return bool(record_identity_keys(record) & known_keys)


def remember_consumed_record(record: dict) -> None:
    remember_consumed_prompt_keys(record_identity_keys(record))


def add_record_to_key_set(keys: set[str], record: dict) -> None:
    keys.update(record_identity_keys(record))


def reveal_latest_incoming_record_for_recovery(page) -> bool:
    """Bring the newest structurally incoming WhatsApp row into the viewport.

    WhatsApp virtualizes records outside the viewport, while attachment cards
    do not always expose a full ``data-pre-plain-text`` timestamp.  Recovery
    therefore reveals only the newest incoming message container and leaves
    all provenance, mention, time-boundary, and consumed-key decisions to the
    ordinary intake path.
    """

    for _attempt in range(16):
        try:
            candidates = page.locator(".message-in")
            count = candidates.count()
        except Exception:
            count = 0
        for index in range(count - 1, max(-1, count - 8), -1):
            try:
                candidates.nth(index).scroll_into_view_if_needed(timeout=5000)
                page.wait_for_timeout(250)
                if any(
                    record_direction(record) == "incoming" and is_whatsapp_message_record(record)
                    for record in message_records(page)
                ):
                    return True
            except Exception:
                continue
        try:
            moved = bool(
                page.evaluate(
                    """
                    () => {
                      const outgoing = Array.from(document.querySelectorAll('.message-out')).pop();
                      if (!outgoing) return false;
                      for (let current = outgoing.parentElement; current; current = current.parentElement) {
                        if (current.scrollHeight <= current.clientHeight + 100) continue;
                        const before = current.scrollTop;
                        current.scrollTop = Math.max(0, before - Math.max(300, current.clientHeight * 0.8));
                        return current.scrollTop !== before;
                      }
                      return false;
                    }
                    """
                )
            )
            if not moved:
                viewport = page.viewport_size or {"width": 1280, "height": 720}
                page.mouse.move(viewport["width"] * 0.76, viewport["height"] * 0.45)
                page.mouse.wheel(0, -300)
            page.wait_for_timeout(900)
            if any(
                record_direction(record) == "incoming" and is_whatsapp_message_record(record)
                for record in message_records(page)
            ):
                return True
        except Exception:
            break
    return False


def current_prompt_baseline_keys(
    page,
    *,
    preserve_incoming_since: datetime | None = None,
) -> set[str]:
    keys = load_consumed_prompt_keys()
    recovery_counts = {
        "visible_records": 0,
        "incoming_records": 0,
        "whatsapp_records": 0,
        "after_boundary": 0,
        "already_consumed": 0,
        "preserved_for_intake": 0,
    }
    recovery_records: list[dict[str, Any]] = []
    try:
        records = message_records(page)
    except Exception:
        return keys
    for record in records:
        if preserve_incoming_since is not None:
            recovery_counts["visible_records"] += 1
            incoming = record_direction(record) == "incoming"
            whatsapp_record = is_whatsapp_message_record(record)
            after_boundary = not record_is_before_staff_wait_window(record, preserve_incoming_since)
            consumed = record_is_known(record, keys)
            parsed_time = whatsapp_record_time_hk(record)
            recovery_counts["incoming_records"] += int(incoming)
            recovery_counts["whatsapp_records"] += int(whatsapp_record)
            recovery_counts["after_boundary"] += int(after_boundary)
            recovery_counts["already_consumed"] += int(consumed)
            recovery_records.append(
                {
                    "record_hash": hashlib.sha256(record_key(record).encode("utf-8")).hexdigest()[:12],
                    "source": str(record.get("source") or ""),
                    "direction": record_direction(record),
                    "timestamp": str(record.get("timestamp") or ""),
                    "parsed_at": parsed_time.isoformat() if parsed_time else "",
                    "is_whatsapp": whatsapp_record,
                    "after_boundary": after_boundary,
                    "already_consumed": consumed,
                    "document_count": len(record.get("documents") or []),
                    "mention_count": len(record.get("mentions") or []),
                }
            )
            if incoming and whatsapp_record and after_boundary and not consumed:
                recovery_counts["preserved_for_intake"] += 1
                continue
        add_record_to_key_set(keys, record)
    if preserve_incoming_since is not None:
        print(
            "WhatsApp recovery baseline: "
            + json.dumps(recovery_counts, ensure_ascii=False, sort_keys=True)
        )
        print(
            "WhatsApp recovery records: "
            + json.dumps(recovery_records, ensure_ascii=False, sort_keys=True)
        )
    return keys


def is_whatsapp_message_record(record: dict) -> bool:
    meta = str(record.get("meta") or "").strip()
    if re.match(r"^\[[^\]]+\]\s+[^:]+:", meta):
        return True
    source = str(record.get("source") or "").strip()
    return source == "document_fallback" and bool(record.get("text") or record.get("documents"))


def is_outgoing_whatsapp_record(record: dict) -> bool:
    return record_direction(record) == "outgoing"


def css_attr_escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def prompt_message_locator(page, record: dict):
    source = str(record.get("source") or "").strip()
    if source == "data_pre":
        return page.locator("[data-pre-plain-text]").nth(int(record.get("index", 0)))
    data_id = str(record.get("data_id") or "").strip()
    if data_id:
        locator = page.locator(f'[data-id="{css_attr_escape(data_id)}"]').first
        try:
            if locator.count() > 0:
                return locator
        except Exception:
            pass
    if str(record.get("meta") or "").strip():
        return page.locator("[data-pre-plain-text]").nth(int(record.get("index", 0)))
    return None


def prompt_attachment_root(env: dict[str, str] | None = None) -> Path:
    root_text = ""
    if isinstance(env, dict):
        root_text = str(env.get("COURSE_AGENT_PROMPT_ATTACHMENT_ROOT") or "").strip()
    root_text = root_text or os.environ.get("COURSE_AGENT_PROMPT_ATTACHMENT_ROOT", "").strip()
    root = Path(root_text).expanduser() if root_text else PROMPT_ATTACHMENT_ROOT
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    return root.resolve(strict=False)


def reference_pack_collection_settings(env: dict[str, str]) -> tuple[float, int, float]:
    seconds = float(
        env.get(
            "COURSE_AGENT_REFERENCE_ATTACHMENT_COLLECTION_SECONDS",
            env.get("COURSE_AGENT_REFERENCE_COLLECTION_SECONDS", "180"),
        )
    )
    expected_count = int(env.get("COURSE_AGENT_REFERENCE_ATTACHMENT_COUNT", "3"))
    quiet_seconds = float(env.get("COURSE_AGENT_REFERENCE_ATTACHMENT_QUIET_SECONDS", "3"))
    return max(0.0, seconds), max(1, expected_count), max(0.0, quiet_seconds)


def reference_pack_upload_request_text(expected_count: int) -> str:
    return (
        "Please upload the reference documents for this rebuild.\n\n"
        f"I am waiting for {expected_count} document attachment(s). Together, they should provide:\n"
        "1. Course timeline or schedule source: the original weekly dates and the Lecture, Workshop/Tutorial, "
        "and Submission content.\n"
        "2. Source-year academic calendar: used to resolve each old date to its original calendar week, "
        "teaching week, weekday, holidays, Reading Weeks, and teaching suspensions.\n"
        "3. Target-year academic calendar or semester dates: the teaching period, holidays or suspensions, "
        "Reading Week, revision period, and assessment period for the new schedule.\n\n"
        "File names and formats may vary. You do not need to label the files; the planner will inspect their "
        "contents and assign the roles.\n"
        "Send each file here as a WhatsApp document attachment."
    )


def reference_collection_verified_attachment_count(raw_task: str) -> int:
    keys: set[str] = set()
    for raw_line in reference_collection_attachment_text(raw_task).splitlines():
        line = raw_line.strip()
        if not line.startswith("- ") or "local_path=" not in line:
            continue
        filename = line[2:].split(":", 1)[0].strip()
        key = reference_attachment_key({"filename": filename, "document_text": filename})
        if key:
            keys.add(key)
    if keys:
        return len(keys)
    if REFERENCE_COLLECTION_STATUS_HEADING in str(raw_task or ""):
        return 0
    return len(current_prompt_local_path_keys(raw_task))


def reference_attachment_key(attachment: dict) -> str:
    document_name = logical_prompt_attachment_name(str(attachment.get("document_text") or attachment.get("filename") or ""))
    if document_name and not document_name.startswith("whatsapp_attachment_"):
        return f"name:{document_name}"
    local_path = resolved_path_key(str(attachment.get("local_path") or ""))
    if local_path:
        return f"path:{local_path}"
    filename = str(attachment.get("filename") or "").strip().lower()
    return f"filename:{filename}" if filename else ""


def logical_prompt_attachment_name(value: str) -> str:
    document_name = filename_from_document_text(str(value or ""), 0)
    document_name = re.sub(r"-(?:[2-9])(?=\.[^.]+$)", "", document_name).strip().lower()
    return document_name


def prompt_attachment_key(attachment: dict) -> str:
    document_name = logical_prompt_attachment_name(str(attachment.get("document_text") or attachment.get("filename") or ""))
    try:
        document_global_index = int(attachment.get("document_global_index", -1))
    except (TypeError, ValueError):
        document_global_index = -1
    if document_global_index >= 0:
        return f"document-control:{document_global_index}:name:{document_name}"
    if document_name and not document_name.startswith("whatsapp_attachment_"):
        return f"name:{document_name}"
    local_path = resolved_path_key(str(attachment.get("local_path") or ""))
    if local_path:
        return f"path:{local_path}"
    filename = str(attachment.get("filename") or "").strip().lower()
    return f"filename:{filename}" if filename else ""


def dedupe_prompt_attachments(attachments: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    index_by_key: dict[str, int] = {}
    for attachment in attachments or []:
        if not isinstance(attachment, dict):
            continue
        copy = dict(attachment)
        key = prompt_attachment_key(copy)
        if not key:
            deduped.append(copy)
            continue
        previous_index = index_by_key.get(key)
        if previous_index is None:
            index_by_key[key] = len(deduped)
            deduped.append(copy)
            continue
        previous = deduped[previous_index]
        if copy.get("local_path") and not previous.get("local_path"):
            deduped[previous_index] = copy
    return deduped


def merge_reference_attachments(existing: list[dict], incoming: list[dict]) -> list[dict]:
    merged: list[dict] = []
    index_by_key: dict[str, int] = {}
    for attachment in [*(existing or []), *(incoming or [])]:
        if not isinstance(attachment, dict):
            continue
        copy = dict(attachment)
        key = reference_attachment_key(copy)
        if not key:
            merged.append(copy)
            continue
        previous_index = index_by_key.get(key)
        if previous_index is None:
            index_by_key[key] = len(merged)
            merged.append(copy)
            continue
        previous = merged[previous_index]
        if copy.get("local_path") and not previous.get("local_path"):
            merged[previous_index] = copy
    return merged


def verified_reference_attachment_count_from_list(attachments: list[dict]) -> int:
    keys: set[str] = set()
    for attachment in attachments or []:
        if not isinstance(attachment, dict) or not attachment.get("local_path"):
            continue
        key = reference_attachment_key(attachment) or resolved_path_key(str(attachment.get("local_path") or ""))
        if key:
            keys.add(key)
    return len(keys)


def verified_unique_reference_attachments(attachments: list[dict]) -> list[dict]:
    unique: list[dict] = []
    seen: set[str] = set()
    for attachment in attachments or []:
        if not isinstance(attachment, dict) or not attachment.get("local_path"):
            continue
        key = reference_attachment_key(attachment) or resolved_path_key(str(attachment.get("local_path") or ""))
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(dict(attachment))
    return unique


def file_content_sha256(path_text: object) -> str:
    path_value = str(path_text or "").strip()
    if not path_value:
        return ""
    try:
        path = Path(path_value).expanduser().resolve(strict=True)
        if not path.is_file():
            return ""
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def attachment_matches_excluded_source(
    attachment: dict,
    *,
    excluded_path_keys: set[str],
    excluded_content_hashes: set[str],
) -> bool:
    path_text = str(attachment.get("local_path") or "").strip()
    if not path_text:
        return False
    path_key = resolved_path_key(path_text)
    if path_key and path_key in excluded_path_keys:
        return True
    content_hash = file_content_sha256(path_text)
    return bool(content_hash and content_hash in excluded_content_hashes)


def reference_collection_prompt_from_attachments(original_task: str, attachments: list[dict]) -> str:
    attachments = verified_unique_reference_attachments(attachments)
    prompt = normalize_message_text(original_task)
    downloaded_count = verified_reference_attachment_count_from_list(attachments)
    if attachments:
        reference_lines = []
        for attachment in attachments:
            filename = str(attachment.get("filename") or attachment.get("document_text") or "reference attachment").strip()
            local_path = str(attachment.get("local_path") or "").strip()
            if local_path:
                reference_lines.append(f"- {filename}: local_path={local_path}")
        prompt = (
            prompt
            + f"\n\n{REFERENCE_COLLECTION_ATTACHMENTS_HEADING}\n"
            + "\n".join(reference_lines)
            + "\n\nThese reference local_path values are verified from the active collection window only."
            + f"\n\n{REFERENCE_COLLECTION_STATUS_HEADING} collected {downloaded_count} verified reference attachment(s). "
            "The planner must inspect these local_path values and emit params.reference_files as objects with content role labels "
            "for reference pack rebuild before any file refresh; do not infer roles from filenames alone."
        ).strip()
    else:
        prompt = (
            prompt
            + f"\n\n{REFERENCE_COLLECTION_STATUS_HEADING} no reference attachments were received in the collection window."
        ).strip()
    return prompt


def load_pending_reference_collection() -> dict:
    try:
        payload = json.loads(PENDING_REFERENCE_COLLECTION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or not payload.get("active"):
        return {}
    created_at = str(payload.get("created_at") or "").strip()
    if created_at:
        try:
            created = datetime.fromisoformat(created_at)
            if created.tzinfo is None:
                created = created.replace(tzinfo=HK_TZ)
            if datetime.now(HK_TZ) - created.astimezone(HK_TZ) > timedelta(hours=6):
                clear_pending_reference_collection()
                return {}
        except ValueError:
            pass
    if not parse_hk_datetime(payload.get("prompt_observed_at")):
        clear_pending_reference_collection()
        return {}
    payload["attachments"] = [item for item in payload.get("attachments", []) if isinstance(item, dict)]
    return payload


def parse_hk_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=HK_TZ)
    return parsed.astimezone(HK_TZ)


def prompt_observed_at_from_record(record: dict) -> datetime:
    return whatsapp_record_time_hk(record) or datetime.now(HK_TZ)


def prompt_boundary_keys(records: list[dict], baseline_keys: set[str], prompt_key: str) -> set[str]:
    keys = {key for key in baseline_keys if not str(key).startswith("stable_record:")}
    for record in records:
        key = record_key(record)
        keys.add(key)
        if key == prompt_key:
            break
    return keys


def save_pending_reference_collection(session: dict) -> None:
    payload = dict(session or {})
    payload["active"] = True
    payload["attachments"] = merge_reference_attachments([], payload.get("attachments", []))
    payload["updated_at"] = datetime.now(HK_TZ).isoformat()
    payload.setdefault("created_at", payload["updated_at"])
    PENDING_REFERENCE_COLLECTION_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_REFERENCE_COLLECTION_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def clear_pending_reference_collection() -> None:
    try:
        PENDING_REFERENCE_COLLECTION_PATH.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def pending_reference_collection_from_task(
    *,
    original_task: str,
    run_id: str,
    expected_count: int,
    attachments: list[dict],
    prompt_observed_at: datetime | None = None,
    requester: str = "",
    root_message_id: str = "",
    chat_type: str = "",
) -> dict:
    now = datetime.now(HK_TZ).isoformat()
    prompt_at = (prompt_observed_at or datetime.now(HK_TZ)).astimezone(HK_TZ).isoformat()
    return {
        "active": True,
        "original_task": normalize_message_text(original_task),
        "created_run_id": run_id,
        "expected_count": expected_count,
        "prompt_observed_at": prompt_at,
        "requester": str(requester or "").strip(),
        "root_message_id": str(root_message_id or "").strip(),
        "chat_type": str(chat_type or "").strip(),
        "created_at": now,
        "updated_at": now,
        "attachments": merge_reference_attachments([], attachments),
    }


def pending_reference_collection_count(session: dict) -> int:
    return verified_reference_attachment_count_from_list(session.get("attachments", []) if isinstance(session, dict) else [])


def pending_reference_collection_prompt(session: dict) -> str:
    return reference_collection_prompt_from_attachments(
        str(session.get("original_task") or "Prepare new academic-year Sem 1 refresh reference files"),
        session.get("attachments", []) if isinstance(session.get("attachments"), list) else [],
    )


def reference_collection_waiting_text(
    *,
    run_id: str,
    task: object,
    expected_count: int,
    downloaded_count: int,
    wait_seconds: float | None = None,
) -> str:
    noun = "file" if downloaded_count == 1 else "files"
    lines = [
        "Reference file upload is still waiting.",
        "",
    ]
    task_text = user_visible_task_text(task)
    if task_text:
        lines.append(f"Your request: {task_text}")
    if wait_seconds is not None:
        lines.append(f"I waited {wait_seconds:g} seconds for the reference files.")
    lines.extend(
        [
            f"I received {downloaded_count} verified reference {noun} out of {expected_count} and kept them.",
            "Please send the missing reference file here as a WhatsApp document attachment.",
            "I will rebuild the reference pack after all required files are verified.",
            f"Course: {SANDBOX_COURSE_ID} sandbox",
        ]
    )
    if run_id:
        lines.append(f"Run: {run_id}")
    return "\n".join(lines)


def record_has_meaningful_user_caption(record: dict) -> bool:
    text = normalize_message_text(record.get("text", ""))
    if not text:
        return False
    metadata_values = {
        normalize_message_text(record.get("sender_display_name", "")).casefold(),
        normalize_message_text(record.get("sender_id", "")).casefold(),
    }
    for document_text in record.get("documents") or []:
        metadata_values.update(
            normalize_message_text(part).casefold()
            for part in re.split(r"[\r\n]+", str(document_text or ""))
            if normalize_message_text(part)
        )
    metadata_values.discard("")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        normalized = normalize_message_text(line)
        if normalized.casefold() in metadata_values:
            continue
        if re.fullmatch(r"\+?\d[\d\s().-]{6,}\d", normalized):
            continue
        if looks_like_whatsapp_document_metadata_line(normalized):
            continue
        return True
    return False


def standalone_document_without_active_reference_text(run_id: str) -> str:
    lines = [
        "File received.",
        "",
        "I did not treat this attachment alone as a Moodle request because it has no instruction caption.",
        "Please send a Moodle request with the file, or start the reference preparation request and upload the reference files after I ask for them.",
        f"Course: {SANDBOX_COURSE_ID} sandbox",
    ]
    if run_id:
        lines.append(f"Run: {run_id}")
    return "\n".join(lines)


def whatsapp_record_time_hk(record: dict, now: datetime | None = None) -> datetime | None:
    meta = str(record.get("meta") or "")
    fallback_text = "\n".join(
        [
            meta,
            str(record.get("text") or ""),
            "\n".join(str(item) for item in record.get("documents", []) or []),
        ]
    )
    time_source = meta if re.search(r"\b\d{1,2}:\d{2}\s*[AP]M\b", meta, flags=re.IGNORECASE) else fallback_text
    matches = re.findall(r"\b(1[0-2]|0?[1-9]):([0-5]\d)\s*([AP]M)\b", time_source, flags=re.IGNORECASE)
    if not matches:
        return None
    hour_text, minute_text, meridiem = matches[-1]
    hour = int(hour_text)
    minute = int(minute_text)
    meridiem = meridiem.upper()
    if meridiem == "PM" and hour != 12:
        hour += 12
    if meridiem == "AM" and hour == 12:
        hour = 0
    current = now.astimezone(HK_TZ) if now else datetime.now(HK_TZ)
    date_matches = re.findall(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", meta)
    if date_matches:
        first, second, year = (int(value) for value in date_matches[-1])
        candidates: list[datetime] = []
        for month, day in ((first, second), (second, first)):
            try:
                candidate = datetime(year, month, day, hour, minute, tzinfo=HK_TZ)
            except ValueError:
                continue
            if candidate not in candidates:
                candidates.append(candidate)
        if candidates:
            return min(candidates, key=lambda candidate: abs(candidate - current))
    candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate > current + timedelta(hours=1):
        candidate -= timedelta(days=1)
    elif candidate < current - timedelta(hours=23):
        candidate += timedelta(days=1)
    return candidate


def record_is_in_reference_collection_window(
    record: dict,
    collection_started_at: datetime,
    *,
    observed_at: datetime | None = None,
) -> bool:
    record_time = whatsapp_record_time_hk(record, observed_at or datetime.now(HK_TZ))
    if record_time is None:
        return False
    cutoff = collection_started_at.astimezone(HK_TZ).replace(second=0, microsecond=0)
    return record_time >= cutoff


def record_is_before_staff_wait_window(
    record: dict,
    wait_started_at: datetime,
    *,
    observed_at: datetime | None = None,
) -> bool:
    record_time = whatsapp_record_time_hk(record, observed_at or datetime.now(HK_TZ))
    if record_time is None:
        return False
    cutoff = wait_started_at.astimezone(HK_TZ).replace(second=0, microsecond=0)
    return record_time < cutoff


def reference_collection_timeout_text(
    *,
    run_id: str,
    task: object,
    expected_count: int,
    downloaded_count: int,
    wait_seconds: float,
) -> str:
    noun = "file" if downloaded_count == 1 else "files"
    lines = [
        "Reference file upload timed out.",
        "",
        f"I waited {wait_seconds:g} seconds for the reference files.",
        f"I received {downloaded_count} verified reference {noun} out of {expected_count}.",
        "I did not rebuild the reference pack or change Moodle.",
        "Please send the request again with the reference files attached when you are ready.",
        f"Course: {SANDBOX_COURSE_ID} sandbox",
    ]
    task_text = user_visible_task_text(task)
    if task_text:
        lines.insert(2, f"Your request: {task_text}")
    if run_id:
        lines.append(f"Run: {run_id}")
    return "\n".join(lines)


def safe_attachment_filename(raw: str, fallback: str) -> str:
    name = Path(str(raw or "").strip()).name
    name = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", name).strip(" .")
    if not name:
        name = fallback
    suffix = Path(name).suffix[:13]
    stem = name[: -len(suffix)] if suffix else name
    if stem.upper() in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }:
        stem += "_"
    limit = 96 if os.name == "nt" else 180
    candidate = stem + suffix
    if len(candidate) <= limit:
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:8]
    stem_limit = max(1, limit - len(suffix) - len(digest) - 1)
    return f"{stem[:stem_limit]}-{digest}{suffix}"


def filename_from_document_text(text: str, index: int) -> str:
    normalized = str(text or "").replace("\u200e", "").strip()
    quoted_match = re.search(
        r'["“”]([^"“”\r\n]{1,180}?\.[A-Za-z0-9]{1,12})["“”]',
        normalized,
    )
    if quoted_match:
        return safe_attachment_filename(quoted_match.group(1), f"whatsapp_attachment_{index + 1}")
    labelled_match = re.search(
        r"\b(?:document|file)\s+name\s*:\s*([^\r\n•]{1,180}?\.[A-Za-z0-9]{1,12})",
        normalized,
        flags=re.IGNORECASE,
    )
    if labelled_match:
        return safe_attachment_filename(labelled_match.group(1), f"whatsapp_attachment_{index + 1}")
    parts = [part.strip() for part in re.split(r"[\r\n]+", normalized) if part.strip()]
    for part in parts:
        if "." in part and "•" not in part:
            return safe_attachment_filename(part, f"whatsapp_attachment_{index + 1}")
    match = re.search(r"([^\r\n•]+?\.[A-Za-z0-9]{1,12})", normalized)
    if match:
        return safe_attachment_filename(match.group(1), f"whatsapp_attachment_{index + 1}")
    return safe_attachment_filename(normalized, f"whatsapp_attachment_{index + 1}")


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not allocate a unique attachment path for {path}")


def click_for_download(page, locator, timeout_ms: int):
    try:
        locator.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        pass
    with page.expect_download(timeout=timeout_ms) as download_info:
        locator.click(timeout=3000, force=True)
    return download_info.value


def mark_whatsapp_viewer_download_button(page) -> bool:
    try:
        return bool(
            page.evaluate(
                """
                () => {
                  document
                    .querySelectorAll('[data-course-agent-viewer-download-button]')
                    .forEach((node) => node.removeAttribute('data-course-agent-viewer-download-button'));
                  const visible = (el) => {
                    if (!el || el.closest('[data-pre-plain-text]')) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0
                      && rect.height > 0
                      && rect.right > 0
                      && rect.bottom > 0
                      && rect.x < window.innerWidth
                      && rect.y < window.innerHeight
                      && style.visibility !== 'hidden'
                      && style.display !== 'none';
                  };
                  const labelFor = (el) => [
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('title') || '',
                    el.getAttribute('data-testid') || '',
                    el.getAttribute('data-icon') || '',
                    el.innerText || el.textContent || '',
                  ].join(' ').replace(/\\s+/g, ' ').trim();
                  const pageText = Array.from(document.querySelectorAll('header, div, span'))
                    .filter(visible)
                    .map((el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim())
                    .filter(Boolean)
                    .slice(0, 120)
                    .join('\\n');
                  const viewerLikelyOpen = /Edit PDF|Powered by Adobe Acrobat|\\.pdf\\b/i.test(pageText);
                  const candidates = Array.from(
                    document.querySelectorAll('button, [role="button"], [aria-label], [title], [data-icon], [data-testid]')
                  )
                    .filter(visible)
                    .map((el) => {
                      const rect = el.getBoundingClientRect();
                      const label = labelFor(el);
                      const lowered = label.toLowerCase();
                      const isDownload = /\\bdownload\\b/.test(lowered)
                        || /download/.test(lowered)
                        || /down-?load/.test(lowered);
                      const inTopToolbar = rect.y < Math.min(260, window.innerHeight * 0.32);
                      const onRightHalf = rect.x > window.innerWidth * 0.45;
                      const score = (isDownload ? 100 : 0)
                        + (inTopToolbar ? 30 : 0)
                        + (onRightHalf ? 10 : 0)
                        - Math.round(rect.y / 10);
                      return {el, isDownload, inTopToolbar, score};
                    })
                    .filter((item) => item.isDownload && item.inTopToolbar)
                    .sort((a, b) => b.score - a.score);
                  if (!viewerLikelyOpen && !candidates.length) return false;
                  const target = candidates[0] && candidates[0].el;
                  if (!target) return false;
                  target.setAttribute('data-course-agent-viewer-download-button', '1');
                  return true;
                }
                """
            )
        )
    except Exception:
        return False


def click_viewer_download(page, timeout_ms: int):
    deadline = time.time() + max(1.0, timeout_ms / 1000)
    last_error = ""
    while time.time() < deadline:
        if not mark_whatsapp_viewer_download_button(page):
            page.wait_for_timeout(250)
            continue
        locator = page.locator('[data-course-agent-viewer-download-button="1"]').first
        try:
            if locator.count() <= 0:
                page.wait_for_timeout(250)
                continue
            remaining_ms = max(1000, int((deadline - time.time()) * 1000))
            with page.expect_download(timeout=remaining_ms) as download_info:
                locator.click(timeout=3000, force=True)
            return download_info.value
        except Exception as exc:
            last_error = str(exc)[:300]
            page.wait_for_timeout(250)
    selectors = [
        '[role="dialog"] [aria-label*="Download"], [role="dialog"] [title*="Download"], [role="dialog"] [data-icon*="download"], [role="dialog"] [data-testid*="download"]',
        'header [aria-label*="Download"], header [title*="Download"], header [data-icon*="download"], header [data-testid*="download"]',
    ]
    for selector in selectors:
        locator = page.locator(selector).last
        try:
            if locator.count() <= 0:
                continue
            with page.expect_download(timeout=1000) as download_info:
                locator.click(timeout=3000, force=True)
            return download_info.value
        except Exception as exc:
            last_error = str(exc)[:300]
            continue
    if last_error:
        raise RuntimeError(last_error)
    return None


def download_prompt_document(page, record: dict, document_text: str, document_index: int, run_id: str, env: dict[str, str]) -> dict:
    filename = filename_from_document_text(document_text, document_index)
    item = {
        "filename": filename,
        "document_text": document_text,
        "source": "whatsapp_document",
    }
    if env.get("COURSE_AGENT_DOWNLOAD_WHATSAPP_ATTACHMENTS", "1").strip().lower() in {"0", "false", "no", "off"}:
        item["download_error"] = "download disabled by COURSE_AGENT_DOWNLOAD_WHATSAPP_ATTACHMENTS=0"
        return item

    target_dir = prompt_attachment_root(env) / run_id
    target_dir.mkdir(parents=True, exist_ok=True)
    message = prompt_message_locator(page, record)
    timeout_ms = int(float(env.get("COURSE_AGENT_ATTACHMENT_DOWNLOAD_SECONDS", "20")) * 1000)
    errors: list[str] = []
    candidates = []
    global_indices = record.get("document_global_indices") if isinstance(record.get("document_global_indices"), list) else []
    if document_index < len(global_indices):
        try:
            global_index = int(global_indices[document_index])
        except (TypeError, ValueError):
            global_index = -1
        if global_index >= 0:
            item["document_global_index"] = global_index
            candidates.append(page.locator(f'[data-course-agent-document-global-index="{global_index}"]').first)
    if message is not None:
        candidates.extend(
            [
                message.get_by_text(filename, exact=False).first,
                message.locator('[data-testid="document-thumb"], [data-icon="document"]').nth(document_index),
                message,
            ]
        )
    candidates.extend(
        [
            page.get_by_text(filename, exact=False).last,
        ]
    )
    try:
        for candidate in candidates:
            try:
                if candidate.count() <= 0:
                    continue
                try:
                    download = click_for_download(page, candidate, timeout_ms)
                except Exception as exc:
                    errors.append(str(exc)[:300])
                    download = click_viewer_download(page, timeout_ms)
                    if download is None:
                        continue
                suggested = safe_attachment_filename(download.suggested_filename or filename, filename)
                target = unique_path(target_dir / suggested)
                download.save_as(str(target))
                item["local_path"] = str(target)
                item["filename"] = target.name
                return item
            except Exception as exc:
                errors.append(str(exc)[:300])
        item["download_error"] = "; ".join(errors) or "no downloadable WhatsApp document control was found"
        return item
    finally:
        close_whatsapp_document_viewer(page)


def prompt_document_download_entries(record: dict) -> list[tuple[int, str]]:
    documents = record.get("documents") or []
    global_indices = record.get("document_global_indices") if isinstance(record.get("document_global_indices"), list) else []
    selected: list[dict] = []
    selected_positions_by_name: dict[str, list[int]] = {}
    seen_global_indices: set[int] = set()

    for index, value in enumerate(documents):
        document_text = str(value)
        document_name = logical_prompt_attachment_name(document_text)
        try:
            global_index = int(global_indices[index]) if index < len(global_indices) else -1
        except (TypeError, ValueError):
            global_index = -1

        if global_index >= 0 and global_index in seen_global_indices:
            continue

        same_name_positions = selected_positions_by_name.get(document_name, []) if document_name else []
        indistinguishable_position = next(
            (
                position
                for position in same_name_positions
                if global_index < 0 or int(selected[position]["global_index"]) < 0
            ),
            None,
        )
        entry = {
            "index": index,
            "text": document_text,
            "global_index": global_index,
        }
        if indistinguishable_position is not None:
            previous = selected[indistinguishable_position]
            if int(previous["global_index"]) < 0 <= global_index:
                selected[indistinguishable_position] = entry
                seen_global_indices.add(global_index)
            continue

        position = len(selected)
        selected.append(entry)
        if document_name:
            selected_positions_by_name.setdefault(document_name, []).append(position)
        if global_index >= 0:
            seen_global_indices.add(global_index)

    return [(int(entry["index"]), str(entry["text"])) for entry in selected]


def download_prompt_attachments(page, record: dict, run_id: str, env: dict[str, str]) -> list[dict]:
    return [
        download_prompt_document(page, record, document_text, index, run_id, env)
        for index, document_text in prompt_document_download_entries(record)
    ]


def staff_prompt_text(record: dict, attachments: list[dict]) -> str:
    text = normalize_message_text(record.get("text", ""))
    attachments = dedupe_prompt_attachments(attachments)
    if not attachments:
        return text
    lines = [text] if text else ["Staff sent a WhatsApp document attachment."]
    lines.append("")
    lines.append("WhatsApp attached file(s):")
    for attachment in attachments:
        filename = attachment.get("filename", "")
        local_path = attachment.get("local_path", "")
        if local_path:
            lines.append(f"- {filename}: local_path={local_path}")
        else:
            reason = attachment.get("download_error", "not downloaded")
            lines.append(f"- {filename}: download_failed={reason}")
    lines.append("")
    lines.append("These local_path values are verified inputs from the current WhatsApp prompt only.")
    return "\n".join(lines).strip()


def is_agent_generated_reference_attachment_record(record: dict) -> bool:
    text = normalize_message_text(record.get("text", ""))
    if text and is_command_or_agent_message(text):
        return True
    documents = [filename_from_document_text(str(item), index).lower() for index, item in enumerate(record.get("documents") or [])]
    if not documents:
        return False
    generated_names = {
        "source_manifest.json",
        "timeline.json",
        "calendar.json",
        "syllabus_rules.json",
    }
    return all(name in generated_names or re.fullmatch(r"source_manifest-\d+\.json", name) for name in documents)


def collect_reference_pack_attachments_for_task(
    page,
    env: dict[str, str],
    raw_task: str,
    baseline_after_task: set[str],
    run_id: str,
    base_url: str = "",
    prompt_observed_at: datetime | None = None,
    excluded_source_paths: set[str] | None = None,
    prompt_context: StaffPromptContext | None = None,
) -> tuple[str, set[str]]:
    collection_seconds, expected_count, _quiet_seconds = reference_pack_collection_settings(env)
    ignored_keys = set(baseline_after_task) | load_consumed_prompt_keys()
    next_prompt_boundary = set(baseline_after_task)
    collection_started_at = (prompt_observed_at or datetime.now(HK_TZ)).astimezone(HK_TZ)
    send_course_agent_message(
        page,
        course_agent_chat_name(env),
        reference_pack_upload_request_text(expected_count),
        purpose="action_required",
        action_kind="upload",
    )
    if base_url:
        post_app_progress(
            base_url,
            "reference_attachment_collection_requested",
            course_agent_chat_name(env),
            f"Asked staff to upload {expected_count} reference file(s).",
            "Waiting for WhatsApp document attachments before rebuilding the local reference pack.",
        )

    deadline = time.time() + collection_seconds
    collected_records: list[dict] = []
    collected_keys: set[str] = set()
    attachments: list[dict] = []
    excluded_path_keys = {
        resolved_path_key(path)
        for path in (excluded_source_paths or set())
        if resolved_path_key(path)
    }
    excluded_content_hashes = {
        digest
        for digest in (file_content_sha256(path) for path in (excluded_source_paths or set()))
        if digest
    }
    latest_records: list[dict] = message_records_with_navigation_recovery(page, env)

    while True:
        latest_records = message_records_with_navigation_recovery(page, env)
        for record in latest_records:
            key = record_key(record)
            if record_is_known(record, ignored_keys) or record_is_known(record, collected_keys):
                continue
            if not is_whatsapp_message_record(record):
                continue
            if is_outgoing_whatsapp_record(record):
                add_record_to_key_set(ignored_keys, record)
                next_prompt_boundary.add(record_key(record))
                continue
            sender = record_sender_display_name(record)
            if prompt_context and sender.casefold() != prompt_context.requester.casefold():
                add_record_to_key_set(ignored_keys, record)
                next_prompt_boundary.add(record_key(record))
                continue
            if not record_is_in_reference_collection_window(record, collection_started_at):
                add_record_to_key_set(ignored_keys, record)
                next_prompt_boundary.add(record_key(record))
                continue
            documents = record.get("documents") or []
            if not documents:
                continue
            if (
                prompt_context
                and prompt_context.chat_type == "group"
                and record_has_meaningful_user_caption(record)
                and not record_has_real_mention(record, str(env.get("_WHATSAPP_AGENT_DISPLAY_NAME") or ""))
            ):
                add_record_to_key_set(ignored_keys, record)
                next_prompt_boundary.add(record_key(record))
                continue
            if is_agent_generated_reference_attachment_record(record):
                add_record_to_key_set(ignored_keys, record)
                next_prompt_boundary.add(record_key(record))
                continue
            add_record_to_key_set(collected_keys, record)
            next_prompt_boundary.add(record_key(record))
            collected_records.append(record)
            downloaded = download_prompt_attachments(page, record, run_id, env)
            attachments.extend(
                attachment
                for attachment in downloaded
                if not attachment_matches_excluded_source(
                    attachment,
                    excluded_path_keys=excluded_path_keys,
                    excluded_content_hashes=excluded_content_hashes,
                )
            )
        downloaded_count = sum(1 for attachment in attachments if attachment.get("local_path"))
        if downloaded_count >= expected_count:
            break
        if collection_seconds <= 0:
            break
        now = time.time()
        if now >= deadline:
            break
        page.wait_for_timeout(min(1500, max(250, int((deadline - now) * 1000))))

    verified_attachments = verified_unique_reference_attachments(attachments)
    downloaded_count = len(verified_attachments)
    if downloaded_count >= expected_count:
        clear_pending_reference_collection()
    elif downloaded_count > 0:
        save_pending_reference_collection(
            pending_reference_collection_from_task(
                original_task=raw_task,
                run_id=run_id,
                expected_count=expected_count,
                attachments=verified_attachments,
                prompt_observed_at=collection_started_at,
                requester=prompt_context.requester if prompt_context else "",
                root_message_id=prompt_context.message_id if prompt_context else "",
                chat_type=prompt_context.chat_type if prompt_context else "",
            )
        )
    prompt = reference_collection_prompt_from_attachments(raw_task, verified_attachments)
    remember_consumed_prompt_keys(collected_keys)
    return prompt, next_prompt_boundary


def ensure_staff_chat_poll_position(page, env: dict[str, str]) -> None:
    chat = course_agent_chat_name(env)
    base_url = str(env.get("_COURSE_AGENT_BASE_URL") or "").strip()
    health = whatsapp_session_health(page) if base_url else {"authenticated": True}
    if not health.get("authenticated"):
        post_app_progress(
            base_url,
            "whatsapp_identity_degraded",
            chat,
            str(health.get("degraded_reason") or "WhatsApp Web is not authenticated."),
            "",
            whatsapp_authenticated=False,
            whatsapp_chat_type=str(env.get("_WHATSAPP_CHAT_TYPE") or "unknown"),
            whatsapp_mention_ready=False,
            whatsapp_degraded_reason=str(health.get("degraded_reason") or ""),
        )
        wait_for_whatsapp_ready(
            page,
            float(env.get("COURSE_AGENT_WHATSAPP_RECOVERY_SECONDS", env.get("WHATSAPP_WEB_WAIT_SECONDS", "420"))),
        )
        agent_display_name = discover_whatsapp_account_name(page)
        open_chat(page, chat)
        chat_context = current_whatsapp_chat_context(page, agent_display_name)
        env["_WHATSAPP_AGENT_DISPLAY_NAME"] = agent_display_name
        env["_WHATSAPP_CHAT_TYPE"] = str(chat_context.get("chat_type") or "unknown")
        env["_WHATSAPP_ENFORCE_UNIQUE_DISPLAY_NAME"] = (
            "1" if env["_WHATSAPP_CHAT_TYPE"] == "group" else "0"
        )
        if env["_WHATSAPP_CHAT_TYPE"] == "group" and not agent_display_name:
            raise RuntimeError(
                "WhatsApp group intake is paused because the dedicated agent account name could not be read."
            )
        post_app_progress(
            base_url,
            "whatsapp_identity_ready",
            chat,
            "WhatsApp Web recovered and the dedicated account identity is ready.",
            "",
            whatsapp_authenticated=True,
            whatsapp_agent_display_name=agent_display_name,
            whatsapp_chat_type=env["_WHATSAPP_CHAT_TYPE"],
            whatsapp_mention_ready=bool(chat_context.get("mention_ready")),
            whatsapp_degraded_reason="",
            recovered=True,
        )
    try:
        open_chat(page, chat)
    except Exception:
        try:
            ensure_chat_compose_ready(page, chat)
        except Exception:
            return
    try:
        scroll_chat_to_bottom(page)
    except Exception:
        try:
            page.keyboard.press("End")
            page.wait_for_timeout(300)
        except Exception:
            pass


def is_transient_whatsapp_navigation_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return any(
        marker in text
        for marker in (
            "execution context was destroyed",
            "most likely because of a navigation",
            "cannot find context with specified id",
        )
    )


def message_records_with_navigation_recovery(page, env: dict[str, str]) -> list[dict]:
    try:
        return message_records(page)
    except Exception as exc:
        if not is_transient_whatsapp_navigation_error(exc):
            raise

    recovery_seconds = float(
        env.get("COURSE_AGENT_WHATSAPP_RECOVERY_SECONDS", env.get("WHATSAPP_WEB_WAIT_SECONDS", "420"))
    )
    try:
        page.wait_for_load_state("domcontentloaded", timeout=min(int(recovery_seconds * 1000), 60000))
    except Exception:
        pass
    wait_for_whatsapp_ready(page, recovery_seconds)
    ensure_staff_chat_poll_position(page, env)
    return message_records(page)


def wait_for_staff_task_context(
    page,
    env: dict[str, str],
    baseline_keys: set[str],
    run_id: str,
    wait_started_at: datetime | None = None,
) -> StaffPromptContext:
    started_at = wait_started_at.astimezone(HK_TZ) if wait_started_at else None
    wait_seconds = float(env.get("COURSE_AGENT_TASK_WAIT_SECONDS", "0"))
    deadline = time.time() + wait_seconds if wait_seconds > 0 else None
    if deadline is None:
        print(f"Waiting indefinitely for a staff prompt in WhatsApp chat `{course_agent_chat_name(env)}`...")
    else:
        print(f"Waiting up to {wait_seconds:g}s for a staff prompt in WhatsApp chat `{course_agent_chat_name(env)}`...")
    next_position_check = 0.0
    first_scan = True
    while deadline is None or time.time() < deadline:
        # Scan the currently visible records once before scrolling.  This is
        # required for bounded recovery because WhatsApp virtualizes older
        # messages out of the DOM as soon as the chat is moved to the bottom.
        if not first_scan and time.time() >= next_position_check:
            ensure_staff_chat_poll_position(page, env)
            next_position_check = time.time() + float(env.get("COURSE_AGENT_CHAT_REOPEN_SECONDS", "8"))
        records = message_records_with_navigation_recovery(page, env)
        consumed_keys = load_consumed_prompt_keys()
        pending_reference = load_pending_reference_collection()
        for record in records:
            key = record_key(record)
            if record_is_known(record, baseline_keys) or record_is_known(record, consumed_keys):
                continue
            if is_outgoing_whatsapp_record(record):
                add_record_to_key_set(baseline_keys, record)
                continue
            if started_at and record_is_before_staff_wait_window(record, started_at):
                add_record_to_key_set(baseline_keys, record)
                continue
            if not is_whatsapp_message_record(record):
                add_record_to_key_set(baseline_keys, record)
                continue
            direction = record_direction(record)
            requester = record_sender_display_name(record)
            message_id = record_message_id(record)
            record_chat_type = str(record.get("chat_type") or "").strip().lower()
            chat_type = str(env.get("_WHATSAPP_CHAT_TYPE") or record_chat_type or "unknown").strip().lower()
            if chat_type not in {"group", "direct"}:
                chat_type = "direct"
            agent_display_name = str(env.get("_WHATSAPP_AGENT_DISPLAY_NAME") or "").strip()
            if direction != "incoming":
                add_record_to_key_set(baseline_keys, record)
                continue
            text = strip_record_mentions(record.get("text", ""), record)
            documents = record.get("documents") or []
            strong_provenance = (
                str(record.get("provenance_confidence") or "").lower() == "high"
                or bool(record.get("direction"))
            )
            if not strong_provenance and not documents and is_command_or_agent_message(text):
                add_record_to_key_set(baseline_keys, record)
                continue
            has_meaningful_caption = record_has_meaningful_user_caption(record)
            if pending_reference and documents and not has_meaningful_caption:
                if is_agent_generated_reference_attachment_record(record):
                    add_record_to_key_set(baseline_keys, record)
                    continue
                pending_prompt_at = parse_hk_datetime(pending_reference.get("prompt_observed_at"))
                if not pending_prompt_at or not record_is_in_reference_collection_window(record, pending_prompt_at):
                    add_record_to_key_set(baseline_keys, record)
                    continue
                expected_requester = str(pending_reference.get("requester") or "").strip()
                if expected_requester and requester.casefold() != expected_requester.casefold():
                    add_record_to_key_set(baseline_keys, record)
                    continue
                attachments = download_prompt_attachments(page, record, run_id, env)
                pending_reference["attachments"] = merge_reference_attachments(
                    pending_reference.get("attachments", []),
                    attachments,
                )
                expected_count = int(pending_reference.get("expected_count") or reference_pack_collection_settings(env)[1])
                downloaded_count = pending_reference_collection_count(pending_reference)
                remember_consumed_record(record)
                if downloaded_count >= expected_count:
                    clear_pending_reference_collection()
                    return StaffPromptContext(
                        raw_task=pending_reference_collection_prompt(pending_reference),
                        baseline_keys=prompt_boundary_keys(records, baseline_keys, key),
                        observed_at=pending_prompt_at,
                        requester=expected_requester or requester,
                        message_id=str(pending_reference.get("root_message_id") or message_id),
                        chat_type=str(pending_reference.get("chat_type") or chat_type),
                        direction="incoming",
                        mentions=list(record.get("mentions") or []),
                        provenance_confidence=str(record.get("provenance_confidence") or "low"),
                        collection_started_at=pending_prompt_at,
                        allowed_attachment_message_ids={
                            message_id
                        } if message_id else set(),
                    )
                save_pending_reference_collection(pending_reference)
                send_course_agent_message(
                    page,
                    course_agent_chat_name(env),
                    reference_collection_waiting_text(
                        run_id=run_id,
                        task=pending_reference.get("original_task", ""),
                        expected_count=expected_count,
                        downloaded_count=downloaded_count,
                    ),
                    purpose="action_required",
                    action_kind="upload",
                    requester=expected_requester or requester,
                    chat_type=str(pending_reference.get("chat_type") or chat_type),
                )
                add_record_to_key_set(baseline_keys, record)
                continue
            if pending_reference:
                clear_pending_reference_collection()
                pending_reference = {}
            if documents and not has_meaningful_caption:
                remember_consumed_record(record)
                add_record_to_key_set(baseline_keys, record)
                continue
            if not requester or requester.casefold() in {"you", "me", "unknown"}:
                add_record_to_key_set(baseline_keys, record)
                continue
            if chat_type == "group":
                if not agent_display_name:
                    raise RuntimeError(
                        "WhatsApp group intake is paused because the dedicated agent account name could not be read."
                    )
                if not record_has_real_mention(record, agent_display_name):
                    add_record_to_key_set(baseline_keys, record)
                    continue
                if str(env.get("_WHATSAPP_ENFORCE_UNIQUE_DISPLAY_NAME") or "") == "1":
                    try:
                        requester_matches = exact_mention_suggestion_count(page, requester)
                    except Exception:
                        requester_matches = 0
                    if requester_matches != 1:
                        remember_consumed_record(record)
                        add_record_to_key_set(baseline_keys, record)
                        send_course_agent_message(
                            page,
                            course_agent_chat_name(env),
                            (
                                "Course agent blocked\n\n"
                                "I could not uniquely identify the Staff member who sent the request. "
                                "Please use a unique WhatsApp display name and mention the agent again.\n"
                                f"Run: {run_id}"
                            ),
                        )
                        continue
            attachments = download_prompt_attachments(page, record, run_id, env) if documents else []
            prompt_record = dict(record)
            prompt_record["text"] = text
            prompt = staff_prompt_text(prompt_record, attachments)
            if not strong_provenance and is_command_or_agent_message(prompt):
                add_record_to_key_set(baseline_keys, record)
                continue
            prompt_observed_at = prompt_observed_at_from_record(record)
            remember_consumed_record(record)
            return StaffPromptContext(
                raw_task=prompt,
                baseline_keys=prompt_boundary_keys(records, baseline_keys, key),
                observed_at=prompt_observed_at,
                requester=requester,
                message_id=message_id or f"legacy:{hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]}",
                chat_type=chat_type,
                direction=direction,
                mentions=list(record.get("mentions") or []),
                provenance_confidence=str(record.get("provenance_confidence") or "low"),
                collection_started_at=prompt_observed_at if attachments else None,
                allowed_attachment_message_ids={
                    message_id
                } if attachments and message_id else set(),
            )
        first_scan = False
        page.wait_for_timeout(1500)
    raise TimeoutError("No staff prompt appeared before COURSE_AGENT_TASK_WAIT_SECONDS.")


def wait_for_staff_task(page, env: dict[str, str], baseline_keys: set[str], run_id: str) -> tuple[str, set[str]]:
    context = wait_for_staff_task_context(page, env, baseline_keys, run_id)
    return context.raw_task, context.baseline_keys


def extract_json_object(text: str) -> dict:
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    candidates = fenced + [text[text.find("{") : text.rfind("}") + 1]]
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("Codex planner did not return a JSON object.")


def verified_prompt_attachment_path(path_text: str) -> Path | None:
    if not path_text:
        return None
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        resolved = path.resolve()
        resolved.relative_to(prompt_attachment_root().resolve(strict=False))
    except (OSError, ValueError):
        return None
    if not resolved.exists() or not resolved.is_file():
        return None
    return resolved


def operation_reports_for_upload_result(result: dict) -> list[dict]:
    approval_run_id = str(result.get("approval_run_id") or "").strip()
    operation_id = str(result.get("operation_id") or "").strip()
    patterns: list[str] = []
    if approval_run_id:
        patterns.append(f"*_{approval_run_id}_operation.json")
    if operation_id:
        patterns.append(f"*{operation_id}*.json")
    reports: list[dict] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for path in REPORTS.glob(pattern):
            if path in seen:
                continue
            seen.add(path)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if operation_id and payload.get("operation_id") not in {"", None, operation_id}:
                continue
            reports.append(payload)
    return reports


def verified_source_file_for_upload_result(result: dict) -> Path | None:
    path_texts: list[str] = []
    for key in ("target_file_path", "upload_file_path", "file_path"):
        value = result.get(key)
        if value:
            path_texts.append(str(value))
    for operation in operation_reports_for_upload_result(result):
        for key in ("target_file_path", "upload_file_path", "file_path"):
            value = operation.get(key)
            if value:
                path_texts.append(str(value))
        params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
        env_overrides = operation.get("env_overrides") if isinstance(operation.get("env_overrides"), dict) else {}
        for value in (
            params.get("file_path"),
            params.get("target_file_path"),
            params.get("upload_file_path"),
            env_overrides.get("MOODLE_UPLOAD_FILE_PATH"),
        ):
            if value:
                path_texts.append(str(value))
    for path_text in path_texts:
        verified = verified_prompt_attachment_path(path_text)
        if verified:
            return verified
    return None


def latest_file_resource_context() -> dict:
    paths: list[Path] = []
    for root in UPLOAD_REPORT_ROOTS:
        if root.exists():
            paths.extend(root.glob("moodle_course_file_upload_*/result.json"))
    for path in sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if result.get("status") not in {"PASS", "ALREADY_PRESENT"}:
            continue
        course_url = str(result.get("course_url") or result.get("course_verify_url") or "")
        if "migrationtest-moodle.hku.hk" not in course_url or f"id={SANDBOX_COURSE_ID}" not in course_url:
            continue
        display_url = str(result.get("display_url") or result.get("target_resource_url") or "")
        cmid = cmid_from_resource_url(display_url)
        title = str(result.get("resource_name") or result.get("target_object") or "").strip()
        if not cmid or not title:
            continue
        context = {
            "source": str(path),
            "approval_run_id": result.get("approval_run_id", ""),
            "operation_id": result.get("operation_id", ""),
            "target_course_id": SANDBOX_COURSE_ID,
            "course_url": SANDBOX_COURSE_URL,
            "resource_name": title,
            "target_module_id": cmid,
            "display_url": display_url,
            "expected_uploaded_filename": result.get("expected_uploaded_filename", ""),
            "status": result.get("status", ""),
        }
        verified_source = verified_source_file_for_upload_result(result)
        if verified_source:
            context.update(
                {
                    "verified_source_file_path": str(verified_source),
                    "verified_source_filename": verified_source.name,
                    "source_verified_by": "previous_whatsapp_attachment_upload",
                }
            )
        return context
    return {}


def latest_file_refresh_preview_context() -> dict:
    try:
        context = json.loads(LATEST_REFRESH_PREVIEW_CONTEXT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    edit_path = str(context.get("refreshed_file_path") or "").strip()
    upload_path = str(context.get("upload_ready_path") or context.get("preview_pdf_path") or "").strip()
    if not (edit_path or upload_path):
        return {}
    resolved_edit = resolve_preview_local_path(edit_path) if edit_path else None
    resolved_upload = resolve_preview_local_path(upload_path) if upload_path else None
    if resolved_edit is not None and resolved_edit.exists():
        context["verified_refreshed_file_path"] = str(resolved_edit)
        context["verified_refreshed_filename"] = resolved_edit.name
        excerpt = re.sub(r"\s+", " ", review_source_body_text(resolved_edit)).strip()
        if excerpt:
            context["review_source_text_excerpt"] = excerpt[:2000]
    if resolved_upload is not None and resolved_upload.exists():
        context["verified_upload_ready_path"] = str(resolved_upload)
        context["verified_upload_ready_filename"] = resolved_upload.name
    if not (context.get("verified_refreshed_file_path") or context.get("verified_upload_ready_path")):
        return {}
    context["source_verified_by"] = "previous_file_refresh_preview"
    context["review_session_active"] = True
    return context


def review_source_body_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".docx", ".pptx", ".xlsx"}:
        try:
            with zipfile.ZipFile(path, "r") as archive:
                names = ["word/document.xml"] if suffix == ".docx" else []
                parts = []
                for name in names:
                    if name not in archive.namelist():
                        continue
                    payload = archive.read(name).decode("utf-8", errors="replace")
                    parts.append(html.unescape(re.sub(r"<[^>]+>", " ", payload)))
                if parts:
                    return "\n".join(parts)
        except zipfile.BadZipFile:
            return ""
    return refresh_visible_text(path)


def write_latest_file_refresh_preview_context(result: dict) -> None:
    if not result.get("ok"):
        return
    artifact_path = first_text(result.get("refreshed_file_path"), result.get("upload_ready_path"))
    if not artifact_path:
        return
    context = {
        "source": result.get("result_path", ""),
        "run_id": result.get("run_id", ""),
        "operation_kind": REFRESH_FILE_PREVIEW_KIND,
        "source_file_name": result.get("source_file_name", ""),
        "refreshed_file_path": result.get("refreshed_file_path", ""),
        "upload_ready_path": result.get("upload_ready_path", ""),
        "preview_pdf_path": result.get("preview_pdf_path", ""),
        "status": result.get("status", ""),
        "audit": result.get("audit", {}),
        "selected_specialists": result.get("selected_specialists", []),
        "composition_plan": result.get("composition_plan", {}),
        "group_plan": result.get("group_plan", {}),
        "composition_summary": (result.get("composition_plan") or {}).get("summary", ""),
        "group_plan_summary": (result.get("group_plan") or {}).get("summary", ""),
        "input_document_fingerprint": result.get("input_document_fingerprint", ""),
        "group_snapshot_hash": result.get("group_snapshot_hash", ""),
        "whatsapp_requester": (
            ACTIVE_STAFF_PROMPT_CONTEXT.requester
            if ACTIVE_STAFF_PROMPT_CONTEXT
            else ""
        ),
        "whatsapp_root_message_id": (
            ACTIVE_STAFF_PROMPT_CONTEXT.message_id
            if ACTIVE_STAFF_PROMPT_CONTEXT
            else ""
        ),
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    LATEST_REFRESH_PREVIEW_CONTEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
    LATEST_REFRESH_PREVIEW_CONTEXT_PATH.write_text(json.dumps(context, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def planner_safe_latest_file_resource_context() -> dict:
    context = dict(latest_file_resource_context())
    for key in ("verified_source_file_path", "verified_source_filename", "source_verified_by"):
        context.pop(key, None)
    return context


def planner_safe_latest_file_refresh_review_context() -> dict:
    requester = ACTIVE_STAFF_PROMPT_CONTEXT.requester if ACTIVE_STAFF_PROMPT_CONTEXT else ""
    context = dict(latest_file_refresh_preview_context())
    batch_state = load_batch_state()
    batch_requester = str(batch_state.get("whatsapp_requester") or "").strip()
    requester_matches_batch = not batch_requester or (
        bool(requester) and batch_requester.casefold() == requester.casefold()
    )
    if (
        batch_state
        and requester_matches_batch
        and str(batch_state.get("updated_at") or "")
        >= str(context.get("updated_at") or "")
    ):
        return {}
    if not context or not active_review_requester_matches(context):
        return {}
    return {
        key: context.get(key)
        for key in (
            "run_id",
            "source_file_name",
            "verified_refreshed_file_path",
            "verified_refreshed_filename",
            "verified_upload_ready_path",
            "verified_upload_ready_filename",
            "preview_pdf_path",
            "review_source_text_excerpt",
            "status",
            "review_session_active",
            "source_verified_by",
            "selected_specialists",
            "composition_summary",
            "group_plan_summary",
            "input_document_fingerprint",
            "group_snapshot_hash",
        )
        if context.get(key) not in ("", None)
    }


def active_review_requester_matches(context: dict | None = None) -> bool:
    context = context or latest_file_refresh_preview_context()
    expected = str(context.get("whatsapp_requester") or "").strip()
    observed = ACTIVE_STAFF_PROMPT_CONTEXT.requester if ACTIVE_STAFF_PROMPT_CONTEXT else ""
    return not expected or (bool(observed) and observed.casefold() == expected.casefold())


def readback_retry_settings(env: dict[str, str]) -> tuple[int, float]:
    try:
        attempts = int(float(env.get("COURSE_AGENT_READBACK_ATTEMPTS", "3")))
    except ValueError:
        attempts = 3
    try:
        delay_seconds = float(env.get("COURSE_AGENT_READBACK_RETRY_SECONDS", "1.5"))
    except ValueError:
        delay_seconds = 1.5
    return max(1, attempts), max(0.0, delay_seconds)


def relative_restriction_time_from_prompt(raw_task: str, now: datetime | None = None) -> str:
    text = str(raw_task or "").strip()
    if not text:
        return ""
    current = now.astimezone(HK_TZ) if now else datetime.now(HK_TZ)
    match = re.search(r"(\d+(?:\.\d+)?)\s*(小时|小時|hour|hours|hr|hrs|h)\s*(后|後|later|from now)?", text, re.IGNORECASE)
    if match:
        return (current + timedelta(hours=float(match.group(1)))).replace(microsecond=0).isoformat()
    match = re.search(r"(\d+(?:\.\d+)?)\s*(分钟|分鐘|minute|minutes|min|mins|m)\s*(后|後|later|from now)?", text, re.IGNORECASE)
    if match:
        return (current + timedelta(minutes=float(match.group(1)))).replace(microsecond=0).isoformat()
    match = re.search(r"(明天|tomorrow)\s*(?:at\s*)?(\d{1,2})(?::(\d{2}))?", text, re.IGNORECASE)
    if match:
        hour = int(match.group(2))
        minute = int(match.group(3) or 0)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            target = (current + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            return target.isoformat()
    return ""


def normalize_access_time_operations(manifest: dict, raw_task: str, now: datetime | None = None) -> dict:
    normalized = dict(manifest or {})
    relative_time = relative_restriction_time_from_prompt(raw_task, now)
    if not relative_time:
        return normalized
    operations = []
    changed = False
    for operation in normalized.get("operations", []) or []:
        if not isinstance(operation, dict):
            operations.append(operation)
            continue
        operation_copy = dict(operation)
        if operation_copy.get("operation_kind") in {"access_time", "browser_module_access_time"} or operation_copy.get(
            "action"
        ) == "set_access_restriction_time":
            operation_copy["target_value"] = relative_time
            params = dict(operation_copy.get("params") or {})
            params["restriction_time"] = relative_time
            operation_copy["params"] = params
            env_overrides = dict(operation_copy.get("env_overrides") or {})
            env_overrides["MOODLE_TARGET_ACCESS_TIME"] = relative_time
            operation_copy["env_overrides"] = env_overrides
            changed = True
        operations.append(operation_copy)
    if changed:
        normalized["operations"] = operations
    return normalized


def call_apim_contents_for_agent(env: dict[str, str]) -> list[dict]:
    attempts, delay_seconds = readback_retry_settings(env)
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return call_apim_contents(env)
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            time.sleep(delay_seconds)
    raise RuntimeError(f"core_course_get_contents failed after {attempts} attempts: {last_exc}") from last_exc


def current_course_readback_context(
    env: dict[str, str],
    *,
    detailed_group_structure: dict | None = None,
    detailed_forum_structure: dict | None = None,
) -> dict:
    try:
        contents = call_apim_contents_for_agent(env)
    except Exception as exc:
        return {
            "status": "unavailable",
            "reason": str(exc)[:500],
            "course_id": SANDBOX_COURSE_ID,
            "course_url": SANDBOX_COURSE_URL,
        }
    sections: list[dict] = []
    for section in contents:
        modules: list[dict] = []
        for module in section.get("modules") or []:
            modules.append(
                {
                    "cmid": module.get("id"),
                    "instance": module.get("instance"),
                    "modname": module.get("modname"),
                    "name": module.get("name"),
                    "visible": module.get("visible"),
                    "url": module.get("url"),
                }
            )
        sections.append(
            {
                "sectionnum": section.get("section"),
                "section_id": section.get("id"),
                "name": section.get("name"),
                "module_count": len(modules),
                "modules": modules,
            }
        )
    modules = flatten_modules(contents)
    result = {
        "status": "ok",
        "course_id": SANDBOX_COURSE_ID,
        "course_url": SANDBOX_COURSE_URL,
        "section_count": len(sections),
        "module_count": len(modules),
        "sections": sections,
    }
    try:
        forums = call_moodle_api(
            "mod_forum_get_forums_by_courses",
            {"courseids[0]": SANDBOX_COURSE_ID},
            env=env,
        )
        forum_items = forums if isinstance(forums, list) else forums.get("forums", []) if isinstance(forums, dict) else []
        result["forum_structure"] = {
            "status": "ok",
            "forums": [
                {
                    "id": item.get("id"),
                    "course": item.get("course"),
                    "name": item.get("name"),
                    "type": item.get("type"),
                }
                for item in forum_items
                if isinstance(item, dict)
            ],
        }
    except Exception as exc:
        result["forum_structure"] = {
            "status": "unavailable",
            "reason": str(exc)[:500],
            "forums": [],
        }
    if isinstance(detailed_forum_structure, dict):
        result["forum_structure"] = dict(detailed_forum_structure)
    if isinstance(detailed_group_structure, dict):
        result["group_structure"] = dict(detailed_group_structure)
        return result
    try:
        groups = call_moodle_api(
            "core_group_get_course_groups",
            {"courseid": SANDBOX_COURSE_ID},
            env=env,
        )
        groupings = call_moodle_api(
            "core_group_get_course_groupings",
            {"courseid": SANDBOX_COURSE_ID},
            env=env,
        )
        result["group_structure"] = base_group_structure(groups, groupings, sections)
    except Exception as exc:
        result["group_structure"] = {
            "status": "unavailable",
            "course_id": SANDBOX_COURSE_ID,
            "reason": str(exc)[:500],
            "groups": [],
            "groupings": [],
            "choicegroup_activities": [
                {
                    "cmid": str(module.get("cmid") or ""),
                    "title": str(module.get("name") or ""),
                    "section": str(section.get("name") or ""),
                }
                for section in sections
                for module in section.get("modules") or []
                if str(module.get("modname") or "") == "choicegroup"
            ],
        }
    return result


def resolved_path_key(path_text: str) -> str:
    text = str(path_text or "").strip()
    if not text:
        return ""
    try:
        path = resolve_preview_local_path(text)
    except Exception:
        path = Path(text).expanduser()
    return str(path.resolve(strict=False))


def current_prompt_local_path_keys(raw_task: str) -> set[str]:
    keys: set[str] = set()
    for line in str(raw_task or "").splitlines():
        if "local_path=" not in line:
            continue
        value = line.split("local_path=", 1)[1].strip()
        key = resolved_path_key(value)
        if key:
            keys.add(key)
    return keys


def current_prompt_local_paths(raw_task: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for line in str(raw_task or "").splitlines():
        if "local_path=" not in line:
            continue
        value = line.split("local_path=", 1)[1].strip()
        key = resolved_path_key(value)
        if key and key not in seen:
            seen.add(key)
            paths.append(value)
    return paths


def reference_collection_attachment_text(raw_task: str) -> str:
    text = str(raw_task or "")
    if REFERENCE_COLLECTION_STATUS_HEADING not in text:
        return text
    if REFERENCE_COLLECTION_ATTACHMENTS_HEADING not in text:
        return ""
    segment = text.split(REFERENCE_COLLECTION_ATTACHMENTS_HEADING, 1)[1]
    return segment.split(REFERENCE_COLLECTION_STATUS_HEADING, 1)[0]


def reference_input_local_path_keys(raw_task: str) -> set[str]:
    if REFERENCE_COLLECTION_STATUS_HEADING in str(raw_task or ""):
        return current_prompt_local_path_keys(reference_collection_attachment_text(raw_task))
    return current_prompt_local_path_keys(raw_task)


def refresh_source_local_path_keys(raw_task: str) -> set[str]:
    text = str(raw_task or "")
    if REFERENCE_COLLECTION_ATTACHMENTS_HEADING in text:
        text = text.split(REFERENCE_COLLECTION_ATTACHMENTS_HEADING, 1)[0]
    elif REFERENCE_COLLECTION_STATUS_HEADING in text:
        text = text.split(REFERENCE_COLLECTION_STATUS_HEADING, 1)[0]
    return current_prompt_local_path_keys(text)


def active_review_editable_path_keys() -> set[str]:
    context = latest_file_refresh_preview_context()
    keys: set[str] = set()
    if context.get("review_session_active") and active_review_requester_matches(context):
        for value in (
            context.get("verified_refreshed_file_path"),
            context.get("refreshed_file_path"),
        ):
            key = resolved_path_key(str(value or ""))
            if key:
                keys.add(key)
    requester = ACTIVE_STAFF_PROMPT_CONTEXT.requester if ACTIVE_STAFF_PROMPT_CONTEXT else ""
    keys.update(active_batch_editable_path_keys(requester))
    return keys


def active_review_upload_path_keys() -> set[str]:
    context = latest_file_refresh_preview_context()
    keys: set[str] = set()
    if context.get("review_session_active") and active_review_requester_matches(context):
        for value in (
            context.get("verified_upload_ready_path"),
            context.get("upload_ready_path"),
            context.get("preview_pdf_path"),
        ):
            key = resolved_path_key(str(value or ""))
            if key:
                keys.add(key)
    requester = ACTIVE_STAFF_PROMPT_CONTEXT.requester if ACTIVE_STAFF_PROMPT_CONTEXT else ""
    keys.update(active_batch_upload_path_keys(requester))
    return keys


def file_source_provenance(source: object, raw_task: str, *, allow_active_review_upload: bool = True) -> str:
    source_key = resolved_path_key(str(source or ""))
    if not source_key:
        return ""
    current_source_keys = refresh_source_local_path_keys(raw_task)
    if source_key in current_source_keys:
        return "current_whatsapp_attachment"
    if current_source_keys:
        return ""
    if source_key in active_review_editable_path_keys():
        return "active_file_refresh_review_editable"
    if allow_active_review_upload and source_key in active_review_upload_path_keys():
        return "active_file_refresh_review_upload_ready"
    return ""


def _current_prompt_path_value(value: object, current_keys: set[str]) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text if resolved_path_key(text) in current_keys else ""


def sanitize_reference_files_value(value: object, current_keys: set[str]) -> object:
    if isinstance(value, list):
        cleaned: list[object] = []
        for item in value:
            if isinstance(item, dict):
                path_text = first_text(item.get("local_path"), item.get("path"), item.get("file_path"))
                if not _current_prompt_path_value(path_text, current_keys):
                    continue
                cleaned.append(item)
            else:
                path_text = _current_prompt_path_value(item, current_keys)
                if path_text:
                    cleaned.append(path_text)
        return cleaned
    if isinstance(value, tuple):
        return sanitize_reference_files_value(list(value), current_keys)
    if isinstance(value, dict):
        cleaned_dict: dict[str, object] = {}
        for key, item in value.items():
            if isinstance(item, dict):
                path_text = first_text(item.get("local_path"), item.get("path"), item.get("file_path"))
                if _current_prompt_path_value(path_text, current_keys):
                    cleaned_dict[key] = item
                continue
            path_text = _current_prompt_path_value(item, current_keys)
            if path_text:
                cleaned_dict[key] = path_text
        return cleaned_dict
    return _current_prompt_path_value(value, current_keys)


def sanitize_reference_pack_operation_inputs(operation: dict, raw_task: str) -> dict:
    current_keys = reference_input_local_path_keys(raw_task)
    if not current_keys:
        current_keys = set()
    cleaned = dict(operation)
    params = dict(cleaned.get("params") or {}) if isinstance(cleaned.get("params"), dict) else {}
    for container in (cleaned, params):
        for key in REFERENCE_PACK_INPUT_KEYS:
            if key not in container:
                continue
            value = container.get(key)
            sanitized = sanitize_reference_files_value(value, current_keys)
            if sanitized:
                container[key] = sanitized
            else:
                container.pop(key, None)
    if params:
        cleaned["params"] = params
    elif "params" in cleaned:
        cleaned["params"] = params
    return cleaned


def normalize_reference_pack_inputs(env: dict[str, str], raw_task: str, manifest: dict) -> dict:
    operations = manifest.get("operations") or []
    if not operations:
        return manifest
    normalized_operations: list[dict] = []
    changed = False
    for operation in operations:
        if isinstance(operation, dict) and is_rebuild_reference_pack_operation(operation):
            cleaned = sanitize_reference_pack_operation_inputs(operation, raw_task)
            changed = changed or cleaned != operation
            normalized_operations.append(cleaned)
        else:
            normalized_operations.append(operation)
    if local_refresh_preview_operations({"operations": normalized_operations}):
        preview_ready = True
        for _index, operation in local_refresh_preview_operations({"operations": normalized_operations}):
            if not bool(refresh_preview_reference_pack_status(env, operation).get("ready")):
                preview_ready = False
                break
        if preview_ready:
            kept_operations = [
                operation
                for operation in normalized_operations
                if not (
                    isinstance(operation, dict)
                    and is_rebuild_reference_pack_operation(operation)
                    and not reference_pack_operation_has_inputs(operation)
                )
            ]
            changed = changed or len(kept_operations) != len(normalized_operations)
            normalized_operations = kept_operations
    if not changed:
        return manifest
    normalized = dict(manifest)
    normalized["operations"] = normalized_operations
    return apply_manifest_defaults(normalized)


def reference_pack_status(env: dict[str, str], params: dict | None = None) -> dict:
    root = refresh_reference_pack_path(params or {}, env)
    missing: list[str] = []
    present: list[str] = []
    for filename in REFERENCE_PACK_REQUIRED_FILENAMES:
        exists = root.is_dir() and (root / filename).is_file()
        (present if exists else missing).append(filename)
    return {
        "root": str(root),
        "exists": root.exists(),
        "ready": not missing,
        "required_files": list(REFERENCE_PACK_REQUIRED_FILENAMES),
        "present_files": present,
        "missing_files": missing,
    }


def refresh_preview_reference_pack_status(env: dict[str, str], operation: dict) -> dict:
    params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
    return reference_pack_status(env, params)


def refresh_preview_source_provenance(operation: dict, raw_task: str) -> str:
    params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
    source = first_text(
        operation.get("source_file_path"),
        operation.get("file_path"),
        params.get("file_path"),
        params.get("source_file_path"),
    )
    return file_source_provenance(source, raw_task, allow_active_review_upload=False)


def operation_file_source_path(operation: dict) -> str:
    params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
    env_overrides = operation.get("env_overrides") if isinstance(operation.get("env_overrides"), dict) else {}
    return first_text(
        operation.get("source_file_path"),
        operation.get("target_file_path"),
        operation.get("upload_file_path"),
        operation.get("file_path"),
        params.get("source_file_path"),
        params.get("file_path"),
        params.get("path"),
        params.get("upload_path"),
        env_overrides.get("MOODLE_UPLOAD_FILE_PATH"),
        env_overrides.get("MOODLE_TARGET_FILE_PATH"),
    )


def operation_uses_moodle_file_source(operation: dict) -> bool:
    kind = str(operation.get("operation_kind") or "").strip()
    action = str(operation.get("action") or "").strip()
    capability = str(operation.get("capability") or "").strip()
    return (
        kind in {"browser_course_file_resource_upload", "course_file_resource_upload", "file_upload_replace"}
        or action in {"create_file_resource", "upload_or_replace_file", "replace_file_resource"}
        or capability in {"upload_file_resource", "replace_file_resource"}
    )


def moodle_file_source_blocker(manifest: dict, raw_task: str) -> str:
    blocked: list[str] = []
    for operation in manifest.get("operations") or []:
        if not isinstance(operation, dict) or not operation_uses_moodle_file_source(operation):
            continue
        source = operation_file_source_path(operation)
        if not file_source_provenance(source, raw_task):
            blocked.append(Path(source).name if source else "the file")
    if not blocked:
        return ""
    return (
        "I need the file for this request as a current WhatsApp document attachment or the active reviewed file from this refresh session. "
        "I will not reuse old local files or files from earlier runs."
    )


def reviewed_group_snapshot_drift_blocker(manifest: dict, env: dict[str, str]) -> str:
    context = latest_file_refresh_preview_context()
    if "group_document" not in {
        str(value) for value in context.get("selected_specialists") or []
    }:
        return ""
    expected_hash = str(context.get("group_snapshot_hash") or "").strip()
    reviewed_keys = {
        resolved_path_key(str(value))
        for value in (
            context.get("verified_refreshed_file_path"),
            context.get("refreshed_file_path"),
            context.get("verified_upload_ready_path"),
            context.get("upload_ready_path"),
            context.get("preview_pdf_path"),
        )
        if str(value or "").strip()
    }
    reviewed_keys.discard("")
    if not expected_hash or not reviewed_keys:
        return ""
    uses_reviewed_file = any(
        isinstance(operation, dict)
        and operation_uses_moodle_file_source(operation)
        and resolved_path_key(operation_file_source_path(operation)) in reviewed_keys
        for operation in manifest.get("operations") or []
    )
    if not uses_reviewed_file:
        return ""
    readback = current_course_readback_context(env)
    snapshot = readback.get("group_structure") if isinstance(readback.get("group_structure"), dict) else {}
    if sanitized_group_snapshot_hash(snapshot) == expected_hash:
        return ""
    return (
        "The sandbox Group structure changed after this reviewed file was generated. "
        "I stopped before approval; refresh the editable file again so the planner can evaluate the new snapshot."
    )


def normalize_course_id_fields(payload: dict) -> dict:
    normalized = dict(payload)
    if "target_course_id" in normalized:
        normalized["target_course_id"] = str(normalized["target_course_id"])
    operations = normalized.get("operations")
    if isinstance(operations, list):
        normalized["operations"] = [
            {
                **operation,
                "target_course_id": str(operation.get("target_course_id"))
                if isinstance(operation, dict) and "target_course_id" in operation
                else operation.get("target_course_id", SANDBOX_COURSE_ID),
            }
            if isinstance(operation, dict)
            else operation
            for operation in operations
        ]
    return normalized


def planner_prompt(
    env: dict[str, str],
    run_id: str,
    raw_task: str,
    *,
    readback_context: dict | None = None,
) -> str:
    allowed = ", ".join(sorted(ALLOWED_OPERATION_KINDS))
    capability_text = json.dumps(capability_specs(), ensure_ascii=False, indent=2)
    latest_resource = planner_safe_latest_file_resource_context()
    latest_resource_text = json.dumps(latest_resource, ensure_ascii=False, indent=2) if latest_resource else "none"
    latest_preview = planner_safe_latest_file_refresh_review_context()
    latest_preview_text = (
        json.dumps(latest_preview, ensure_ascii=False, indent=2)
        if latest_preview
        else "none (no active file-refresh review session)"
    )
    requester = ACTIVE_STAFF_PROMPT_CONTEXT.requester if ACTIVE_STAFF_PROMPT_CONTEXT else ""
    active_file_batch = planner_safe_active_batch_context(requester)
    active_file_batch_text = (
        json.dumps(active_file_batch, ensure_ascii=False, indent=2)
        if active_file_batch
        else "none (no active multi-file refresh review)"
    )
    finalized_file_batch = planner_safe_recent_finalized_batch_context(requester)
    finalized_file_batch_text = (
        json.dumps(finalized_file_batch, ensure_ascii=False, indent=2)
        if finalized_file_batch
        else "none (no recent finalized multi-file PDF)"
    )
    active_timeline_draft = planner_safe_active_timeline_draft_context(
        requester
    )
    active_timeline_draft_text = (
        json.dumps(active_timeline_draft, ensure_ascii=False, indent=2)
        if active_timeline_draft
        else "none (no active timeline-draft review)"
    )
    refresh_pack = reference_pack_status(env)
    refresh_pack_text = json.dumps(refresh_pack, ensure_ascii=False, indent=2)
    readback_context = readback_context or current_course_readback_context(env)
    readback_text = json.dumps(readback_context, ensure_ascii=False, indent=2)
    reference_roles = ", ".join(sorted(REFERENCE_SOURCE_ROLES))
    return f"""You are the planner for a sandbox-only Moodle course-management agent.

Staff prompt:
{raw_task}

Recent verified Moodle File resource context:
{latest_resource_text}

Recent refreshed file review context:
{latest_preview_text}

Active multi-file refresh review:
{active_file_batch_text}

Recent finalized multi-file PDF:
{finalized_file_batch_text}

Active timeline draft review:
{active_timeline_draft_text}

Default refresh reference pack status:
{refresh_pack_text}

Current read-only Moodle course contents:
{readback_text}

Return one JSON object with exactly these top-level keys:
- course_task_intent
- course_operation_manifest
- approval_message

Hard constraints:
- Target only sandbox Moodle course {SANDBOX_COURSE_ID}: {SANDBOX_COURSE_URL}
- If staff asks for any state-changing action on the legacy/live/old Moodle course or course id 118635, return an empty operations list. Do not execute only the sandbox portion of a mixed live-course plus sandbox write request unless staff sends a new sandbox-only prompt.
- Staff prompts are free-form. Plan by composing supported Moodle capabilities, not by matching a fixed user-action list.
- Capability + params is canonical planner output. operation_kind is only the executor lane selected by the resolver.
- Supported capabilities:
{capability_text}
- Available executor lanes after resolution: {allowed}
- Use APIM/API lanes when they directly cover the capability.
- Use stable project tools/page objects for browser-backed capabilities such as exact File resource delete and section rename.
- For ordinary staff requests to upload a file as course content, prefer capability=upload_file_resource; it uses the current stable sandbox browser File-resource tool.
- For staff requests to refresh/update/revise/翻新 one WhatsApp-attached file and send a PDF back for staff checking, use capability=refresh_file_preview. This is a local-only PDF review-copy operation: no Moodle write, no approval OK code, and no browser fallback.
- For refresh_file_preview, do not decide or encode change domains, target structures, table meanings, Group scope, ordering policy, durations, venues, or replacement text in the manifest. The bounded File Change Composition Planner receives the current document model and readback, decides what needs semantic review, and delegates to specialist planners. Runtime does not route these decisions from staff phrases, filenames, event names, headers, Group naming formats, or historical examples.
- For a staff request to refresh multiple current WhatsApp document attachments, any current .zip attachment as a file package, or one or more exact items in Active multi-file refresh review, use capability=refresh_file_batch_preview. Put the ordered verified source paths in params.file_paths. For an active batch revision also bind its exact batch_id and batch_version and use only the selected items' verified_refreshed_file_path values. Runtime safely expands ZIP files and sends every generated preview PDF as a separate WhatsApp attachment.
- When a semantic file refresh needs current Group membership counts, Groups inside a Grouping, or Grouping relationships and the supplied Group structure status is api_only, keep the appropriate refresh_file_preview or refresh_file_batch_preview capability and set params.require_detailed_group_structure=true. Runtime will perform one bounded, sanitized, read-only detail pass and re-plan. When the supplied Group structure is already detailed, omit this flag. Do not add an inspect_group_structure operation solely to obtain context for a file refresh.
- Use capability=finalize_file_batch_preview only when the current prompt explicitly confirms the Active multi-file refresh review and asks to generate the combined/final/total PDF. Bind the exact active batch_id and batch_version. Generic acknowledgements such as OK, received, looks fine, or approval of only one item are insufficient. Do not invent include_item_ids; provide them only when staff explicitly names a successful subset.
- refresh_file_batch_preview and finalize_file_batch_preview are local-only operations. They do not write Moodle, consume an OK approval code, or combine with Moodle upload/replace in the same manifest.
- Recent finalized multi-file PDF may be used only when the same requester immediately asks to upload that exact combined artifact and supplies a clear Moodle target. The later upload is a separate capability and still requires current-run WhatsApp approval.
- For staff requests to upload, replace, or describe weekly class meeting arrangements, weekday class schedules, 周内上课安排, 每周几上课, class arrangement, or which weekdays have class, use capability=update_semester_schedule. When all source content fits, this atomically reflows local semester_schedule.json and timeline.json and refreshes source_manifest.json checksums. When content exceeds valid teaching weeks, runtime leaves the canonical pack unchanged and sends a versioned timeline draft for review. It does not write Moodle, consume an approval OK code, or use browser fallback. Put current WhatsApp attachment local_path values in params.schedule_files, and put the current prompt's natural-language arrangement in params.schedule_text when useful. Use merge_mode=replace_all.
- When Active timeline draft review is present and the current prompt asks to delete course content, move a whole source Teaching Week or one content kind, or reflow later content to fill a released Teaching Week, use capability=revise_timeline_draft with the exact active draft_id and draft_version. Do not turn the instruction into direct item IDs yourself; the bounded Timeline Reconciliation Agent selects stable IDs, expands an intended sequence reflow into explicit move_items, and runtime validates only move_items/delete_items. Calendar holidays, Reading Weeks, and suspensions are already excluded from the Teaching Week sequence and must not be shifted as content. A prompt that both revises and says to confirm still selects revise_timeline_draft only.
- Use capability=commit_timeline_draft only when the active exact version is READY_FOR_CONFIRMATION, the staff has already received that latest preview, and this current prompt is a separate, explicit natural-language confirmation to make that reviewed version official. Bind exact draft_id and draft_version. Ambiguous acknowledgement, stale version, historical draft, or revision plus confirmation in one prompt must not commit.
- Timeline draft revision and commit are local reference operations. They never use a Moodle OK approval code and never claim Moodle changed.
- Do not use rebuild_reference_pack for a staff-provided weekly class arrangement unless they are also rebuilding timeline/calendar/syllabus references. update_semester_schedule is the dedicated capability for reflowing schedule and timeline outside a source-reference rebuild.
- If the same staff prompt includes a new weekly class arrangement and a specific old file to refresh, order update_semester_schedule first and refresh_file_preview second so the Date Planner can use the updated semester_schedule.json.
- Carefully distinguish old-file date refresh from reference-pack rebuild. If the staff supplied an old course file and asks to update/refresh dates/content, plan refresh_file_preview for that file. If the staff asks to prepare/rebuild timeline/calendar/rules/reference JSON without a specific old file to revise, plan rebuild_reference_pack only. If both reference sources and a specific old file are present, order rebuild_reference_pack first and refresh_file_preview second; both are local-only.
- For upload_file_resource, replace_file_resource, and refresh_file_preview, any source file path must come from either the current Staff prompt's WhatsApp attachment local_path values or the active Recent refreshed file review context. For refresh_file_preview review-loop revisions, use the active context's verified_refreshed_file_path editable file, not the preview PDF/upload-ready file. For later Moodle upload/replace, use verified_upload_ready_path when the reviewed PDF is the intended upload. Do not use historical local files, older review outputs, Downloads, guessed paths, previous Moodle uploads, or Recent verified Moodle File resource context as source files.
- For refresh_file_batch_preview, every top-level path must come from the current prompt's ordered WhatsApp attachment local_path values or the same requester's Active multi-file refresh review editable paths. A single unauthorized source blocks the whole batch. ZIP member paths are created only by runtime and must never be invented by the planner.
- If Default refresh reference pack status is not ready and staff is asking to refresh a specific old file, choose refresh_file_preview for the old file with no invented reference paths; the runtime may collect/rebuild missing reference attachments and then automatically continue to the PDF review copy. If staff is only preparing references and no old file is present, choose rebuild_reference_pack.
- Do not combine refresh_file_preview with Moodle upload/replace in the same operation manifest. After a local PDF review copy is returned, a later upload/replace request may use the active Recent refreshed file review context as its file source, but it must still be a separate Moodle approval step.
- If staff asks to start or prepare a new academic-year Sem 1 file/date refresh but no specific old file local_path is attached yet, choose capability=rebuild_reference_pack as the first step so runtime can collect reference files. Do not choose refresh_file_preview until a specific source file is available.
- For staff requests to prepare/reproduce/update reference inputs for a new academic-year Sem 1 file/date refresh, use capability=rebuild_reference_pack. The same staff prompt must state which weekdays the class meets. Convert that instruction semantically into params.class_weekdays using canonical English weekday names, for example ["Tuesday"]; never infer it from an old schedule, filename, or historical prompt. If the weekday arrangement is missing, return no operation and a natural Status: Waiting message asking the staff to resend the rebuild request with the class weekday(s).
- For rebuild_reference_pack, when WhatsApp attachment local_path values are already present, inspect their content and put all relevant inputs in params.reference_files as objects: {{"local_path": "<verified path>", "role": "<planner-selected content role>"}}. Allowed roles: {reference_roles}. Do not emit bare paths, and do not infer a role from the filename/caption alone. When the staff intent clearly needs reference inputs but no verified attachment local_path is present yet, still use capability=rebuild_reference_pack with params.class_weekdays and no invented paths; the runtime will ask staff to upload reference files and will re-plan after collection. Keep the stable local refresh_rules.md with the resulting pack; do not treat it as a file to regenerate from the attachments. This is local-only: no Moodle write, no approval OK code, and no browser fallback.
- Reference roles describe semantic content, not file extensions or document containers. Select timeline_reference only when the inspected content actually contains a Week/Date/Lecture structure. Select source_calendar_reference for an old/source academic-year calendar used to resolve old dates to source calendar week, teaching week, and weekday. Select target_calendar_reference for the new/target academic-year calendar used to generate the refreshed schedule. The legacy calendar_reference role is only for calendar facts whose source/target purpose genuinely cannot yet be distinguished. Do not force one attachment into each role, and never relabel a calendar as timeline_reference merely to keep it in the input list. Runtime content-detects available parsers, validates the academic year inside each calendar, and accepts one only after the required structured facts are extracted.
- For rebuild_reference_pack, do not invent dates or calendar facts from the prompt. The local runner uses the source calendar to resolve old visible dates into source academic year, semester, calendar week, teaching week, and weekday, and uses the target HKU calendar plus params.class_weekdays to regenerate target timeline.json and semester_schedule.json. For the old course, the Timeline is authoritative: when it explicitly schedules teaching on a source-calendar suspension, preserve the Timeline row and record `timeline_overrides_source_calendar` evidence instead of blocking or dropping the week. Timeline rows explicitly marked Holiday/Reading Week remain non-teaching. The target schedule still skips target-calendar holidays, reading weeks, and class suspensions without consuming teaching-week numbers. If all content does not fit before last_teaching, it leaves canonical JSON unchanged and sends a versioned overflow draft instead of blocking. Every teaching row records typed source coordinates plus target `teaching_week` and `calendar_week`. Date/content mappings use `teaching_week`; during file refresh the Date Planner must explicitly select which coordinate a visible Week column represents.
- For revision prompts after a PDF review copy has been sent, if there is an active Recent refreshed file review context and the staff asks for changes without uploading a new file, use its verified_refreshed_file_path as refresh_file_preview.params.file_path. Do not encode requested edits as params.replacements, text_replacements, change-domain flags, target IDs, Group schedule rules, or Date Planner `date_edits` in the course manifest. Composition and specialist planners must inspect the current artifact and decide the grounded changes; the Date Planner Agent may produce its own structurally anchored date_edits only after Composition ownership is known. Return another PDF review copy only after safe local refresh; do not write Moodle.
- If staff confirms the PDF review copy and asks to upload/replace it, use the active Recent refreshed file review context's verified_upload_ready_path as the upload/replace file source only when the Moodle target section or exact target resource is clear or uniquely resolved. This still requires a normal Moodle approval with OK/NO before writing.
- If no stable capability can express a bounded sandbox Moodle task, use capability=browser_moodle_task as the strict browser fallback instead of blocking solely because a stable tool is missing.
- browser_moodle_task is still constrained: set requires_browser_fallback=true, include fallback_reason, generate only approved DSL steps, and make the approval visibly say Moodle browser fallback may be used.
- New File resources must default visible unless the staff prompt explicitly asks to hide them.
- The current Moodle readback is already provided. For bounded broad prompts such as "delete everything in General section", "hide everything in Workshop II", or "rename all items matching X", use the readback to enumerate exact cmid/title/module targets yourself. Do not ask staff to provide inventory when readback status is ok.
- For "everything in a section", target the modules/items inside that section, not the section object itself, unless the staff explicitly asks to delete the section.
- If a target range includes default/core items such as Announcements, do not silently exclude them; include the item count and scope in approval fields, and encode exact cmid/title/module targets in the manifest so staff can approve or reject the bounded operation with OK/NO.
- Use recent Moodle File resource context only when staff explicitly refers to the existing recent Moodle resource as the target of a Moodle action. Do not treat that context as a current file attachment.
- Use recent local refresh review context only for the active review loop or its immediately requested upload/replace step. It is not a general source pool for unrelated new prompts.
- If the staff prompt includes WhatsApp attached file local_path values and staff asks to upload or refresh an attached/current file, use the listed local_path. Do not invent another file path. Use the attachment filename stem as resource_name unless staff gives a clearer Moodle item name.
- If a referenced WhatsApp attachment has no local_path or says download_failed, return an empty operations list with a clear blocker; Moodle upload requires a verified local file path.
- If staff asks to recreate or restore a week/section and upload an attached file there, first resolve the target section from readback. If that Moodle section already exists, upload into it. If the section exists but needs its title restored, compose rename_section before upload_file_resource. If no stable section target exists, return no operations with a human blocker instead of using generic browser fallback.
- For "delete/remove everything in section X", prefer capability=delete_modules_in_section with params.section=X. The resolver will expand exact supported targets from readback and block unsupported module types rather than letting you invent UI clicks.
- If staff asks to delete one named activity/module without giving a section, use capability=delete_module when current readback has a unique exact match. If the same target appears in multiple sections, block for a clearer boundary. Use delete_modules_in_section for deleting all items in a section or multiple resolved targets.
- For "rename/clear/delete section title", prefer capability=rename_section with params.section and params.new_title. Use an empty string for clearing a custom section title.
- For compound prompts, output ordered operations that share one bounded approval when they fit one staff intent, for example upload_file_resource followed by set_restriction_time.
- The state machine supports one current-run WhatsApp approval for one exact bounded manifest. If staff explicitly requires a separate approval cycle before each individual write, return no operations and explain that each write must be sent as a separate staff prompt. Never emit approval_policy=per_write or claim that one manifest will request multiple approval codes.
- For a newly created Moodle activity that must become visible at a future time, do not leave it only `Hidden from students`. Use one bounded approval with ordered operations: create the activity hidden with `post_create_reference`, set_restriction_time using that `target_ref`, then set_visibility using the same `target_ref` with visible=true so the date restriction controls student access.
- For "group sign-up", "grouping sign up", "让学生选组", or similar in a section, use capability=create_group_signup_activity with params.section, params.title, and explicit params.group_names. If the prompt also asks to create the groups/grouping, order create_group operations first, then create_grouping with group_names, then create_group_signup_activity. This browser activity operation must come after the API group/grouping operations.
- Group structure readback has status api_only or detailed. API-only data contains Group and Grouping identities but not member counts, Grouping membership, or Choicegroup dependencies.
- For a read-only request about Group membership counts, Groups inside a Grouping, or Choicegroup dependencies when Group structure is api_only, select capability=inspect_group_structure with a bounded scope. Runtime performs one read-only browser inspection and replans once; this capability never needs an OK code and must not be combined with Moodle writes in the final detailed plan.
- For create_group, create_grouping, update_grouping_membership, delete_group, delete_grouping, and create_group_signup_activity, select the intended typed capability even when detailed Group structure is not yet present. Runtime will collect detailed readback and replan before approval. Never replace these with browser_moodle_task.
- Use update_grouping_membership with grouping_name plus explicit add_group_names and/or remove_group_names. Unmentioned Groups remain unchanged. For "only keep" requests, use detailed readback to enumerate every explicit removal.
- Use delete_group only for one exact Group name. The resolver blocks Groups with members, Choicegroup references, or remaining Grouping links. When unlinking and deleting in one prompt, order update_grouping_membership removals before delete_group.
- Use delete_grouping only for one exact Grouping name. If it contains Groups, order explicit update_grouping_membership removals before delete_grouping.
- When Group structure status is detailed and the staff request is read-only, keep one capability=inspect_group_structure operation so runtime can return the sanitized typed result.
- When Group structure status is detailed and the staff request includes Group writes, omit inspect_group_structure from the final operation list. The detailed snapshot has already satisfied the pre-approval inspection; output only the explicit ordered write operations.
- For "assignment", "assignment submission", "作业", "功课", or similar activity creation in a section, use capability=create_assignment_activity with params.section and params.title/name. Do not use generic browser_moodle_task for ordinary Assignment activity creation.
- For forum messages/posts, use capability=create_forum_post with subject, body/message, and an exact forum_name or target_forum_id grounded in current course readback. If no unique target is available, block instead of inventing a default forum.
- For replying to an existing Forum question/discussion, use capability=reply_forum_post with subject, body/message, an exact forum_name or target_forum_id, and exactly one discussion target expressed by discussion_id, discussion_url, or discussion_title. Do not use create_forum_post, because that creates a new discussion. When detailed Forum structure is not yet supplied, keep reply_forum_post and set params.require_detailed_forum_structure=true; runtime collects the sanitized discussion inventory and replans before approval.
- For deleting one reply inside an existing discussion, use capability=delete_forum_reply with an exact forum, exact discussion, and reply_post_id/reply_post_url or exact reply_text/target_ref. Never use delete_forum_post for a reply. The browser lane must prove the target is a non-root reply, delete once, and verify that reply post id is absent.
- Forum structure without detail_status=detailed contains Forum identities but not a complete discussion inventory. When the requested operation semantically needs exact current discussions/posts, select capability=inspect_forum_structure with scope=forums and the exact readback-grounded forum_names. Runtime performs the bounded sanitized read-only pass and replans once; do not block or ask staff to run the inventory.
- When Forum structure is detailed and the request includes Forum writes, omit inspect_forum_structure from the final operation list and output only the explicit ordered writes. For a read-only Forum request, the detailed snapshot may be summarized directly without approval.
- Current Forum structure may include a sanitized detailed discussion inventory. For a bounded broad request such as deleting every post/discussion in one exact forum, enumerate every current discussion into one delete_forum_post operation per discussion URL/id. Do not ask staff to copy subjects or URLs that the supplied readback already contains. If the exact inventory is empty, treat the deletion portion as already satisfied.
- If staff explicitly requests posting to an exact named forum that is absent, the planner may add create_forum_activity for that exact name as a clearly disclosed prerequisite. That create_forum_activity MUST appear before every dependent post, MUST declare a unique top-level params.post_create_reference, and every dependent create_announcement/create_forum_post MUST use the exact same top-level params.target_ref. Do not put the runtime reference only inside forum_target, do not place a dependent announcement before its producer, and never substitute a different existing forum. Keep the dependency and all posts in the same bounded approval.
- When staff supplies a tag/reference but not prose, that tag is sufficient grounding for a minimal test post. The planner owns content composition: create concise, distinct subject/body values that retain the exact tag. Do not ask staff to repeat whether the tag should be the full body unless the prompt explicitly makes content ambiguous in a materially different way.
- For deleting a forum message/post/discussion, use capability=delete_forum_post with either an exact post_url/discussion_url/post_id/discussion_id, or forum_name/forum_url plus target_subject/target_ref. The browser tool may search that forum by visible text, but it must block if the match is not unique. Do not use it for deleting an entire forum module.
- If staff explicitly asks to hide/show/set visibility after another action, include a separate set_visibility operation even when current readback already appears to match.
- For any "visible/released after/at a future time" request, include both set_restriction_time and set_visibility visible=true for the same target. A date restriction alone is not enough when the module itself is hidden.
- Do not add set_visibility for a newly created upload_file_resource unless an existing target_module_id/cmid is already known. For new uploads, encode the requested initial visibility in upload_file_resource.params.visible; the upload tool applies it.
- If staff asks to refresh/update/roll over course files from a local backup/timeline/source-originals package and then batch upload, use capability=course_refresh_batch_upload with params.refresh_job or params.prepared_upload_manifest_path. The resolver selects operation_kind=course_refresh_batch_upload.
- For course_refresh_batch_upload params, follow the capability params_schema above: use workflow=dase7137_stale_cleanup_rebuild for the canonical DASE7137 source-first refresh pipeline, or prepared_upload_manifest when a validated upload manifest already exists.
- For course_refresh_batch_upload, do not invent arbitrary scripts or code. The local app prepares/materializes/audits files before approval and executes only the prepared upload manifest after OK.
- For single-file PDF review-copy refresh, prefer capability=refresh_file_preview instead of course_refresh_batch_upload. course_refresh_batch_upload is for local package/batch migration plus approved Moodle upload; refresh_file_preview is for returning one PDF review copy to staff.
- Delete-file operations must include capability=delete_file_resource and params with target_module_id/cmid plus target title, or target_ref=latest_upload when recent File resource context is present.
- If strict fallback browser_moodle_task is unavoidable, generate browser_steps using only this DSL:
  - goto: url
  - click: selector OR text OR role/name; an "Add an activity or resource" click must also include the exact section or sectionnum
  - fill: selector/label/placeholder + value
  - select: selector + value OR label_value
  - check/uncheck: selector/label/text
  - press: selector optional + key
  - wait/wait_for_text/assert_text/assert_no_text/screenshot/readback_course_contents
- Locator steps may include zero-based nth when the same label or text occurs more than once. Use it for repeated Moodle controls such as per-choice Grade selectors; never rely on an unscoped first match.
- For select steps, label_value means visible option text and value means the HTML option value. For Moodle percentage grades such as 100% or None, use label_value unless a read-only form inspection supplied the exact HTML value.
- In the HKU activity/resource chooser, clicking the exact activity card (for example Quiz) enters that activity's add form directly. Do not add a second generic click text="Add" between the activity card and the first form fill. This does not apply to the later question-type chooser, whose explicit Add button is required.
- browser_moodle_task must include approval_action, approval_item, browser_goal, browser_steps, and readback_expectations.
- readback_expectations must include at least one verifiable expectation such as module_present, module_absent, text_present, text_absent, or url_contains.
- Browser steps may navigate only within migrationtest Moodle sandbox URLs. Never use the legacy live Moodle course.
- Do not use browser_moodle_task for unbounded cleanup, credentials/secrets, live-course work, arbitrary code, server/admin/plugin changes, grade/student-record changes, or participant/student-data export.
- If the task is unsafe or unclear in a high-risk way, return an empty operations list and an approval_message explaining the blocker.

For operations, include:
- capability
- params
- operation_kind
- execution_mode
- target_course_id
- summary
- env_overrides
- approval_action
- approval_item
- approval_scope when the operation covers multiple exact targets

For a sandbox announcement, use operation_kind=announcement_api and execution_mode=api_action.
For a sandbox browser file-resource upload, use operation_kind=browser_course_file_resource_upload and execution_mode=agent_browser_action.
For a sandbox browser file-resource delete, use operation_kind=browser_course_file_resource_delete and execution_mode=agent_browser_action.
For a section title change, use capability=rename_section and operation_kind=browser_section_rename.
For a section group sign-up activity, use capability=create_group_signup_activity and operation_kind=browser_group_signup_activity. Moodle Choicegroup exposes one shared per-Group quota control: encode a request such as "quota 30 for each selected group" as the scalar params.maxanswers=30, never as a name-to-quota object.
For read-only detailed Group inspection, use capability=inspect_group_structure and operation_kind=group_structure_readback.
For explicit Grouping membership changes, use capability=update_grouping_membership and operation_kind=grouping_api.
For adding, removing, or moving one participant between Groups, use capability=update_group_membership and operation_kind=browser_group_membership. Require params.action=add|remove|move plus exactly one staff-supplied participant reference: a positive numeric params.user_id or a non-empty params.user_name. Use params.target_group_name for add, params.source_group_name for remove, and both for move. Name lookup is a bounded action-time Moodle search: normalized case-insensitive containment must resolve to exactly one numeric option value in every relevant Group page, otherwise block before write. Do not use email lookup, return candidate identities, or place participant identity in visible approval/result text.
For exact Group deletion, use capability=delete_group and operation_kind=group_api.
For exact Grouping deletion, use capability=delete_grouping and operation_kind=grouping_api.
For an Assignment activity, use capability=create_assignment_activity and operation_kind=browser_assignment_activity.
For a Forum activity prerequisite, use capability=create_forum_activity and operation_kind=browser_forum_activity.
For a forum message/post, use capability=create_forum_post and operation_kind=announcement_api.
For a reply to an existing Forum discussion, use capability=reply_forum_post and operation_kind=browser_forum_post_reply with execution_mode=agent_browser_action.
For a forum message/post delete, use capability=delete_forum_post and operation_kind=browser_forum_post_delete.
For one Forum reply delete, use capability=delete_forum_reply and operation_kind=browser_forum_reply_delete.
For a restriction/access time change, use capability=set_restriction_time and operation_kind=browser_module_access_time.
For clearing a restriction/access time, use set_restriction_time params with restriction_action="clear"; do not invent a timestamp.
For a strict browser fallback task, use capability=browser_moodle_task, operation_kind=browser_moodle_task, and execution_mode=agent_browser_action with requires_browser_fallback=true and fallback_reason.
For a local file refresh plus batch upload task, use capability=course_refresh_batch_upload with typed params; the resolver fills operation_kind and execution_mode.
For a local single-file refresh that sends a PDF for staff checking, use capability=refresh_file_preview with params.file_path from the current WhatsApp attachment local_path or the active Recent refreshed file review context's verified_refreshed_file_path editable file; the resolver fills operation_kind=refresh_file_preview and execution_mode=local_file_action.
For multiple local file refresh previews or a ZIP package, use capability=refresh_file_batch_preview with ordered params.file_paths; the resolver fills operation_kind=refresh_file_batch_preview and execution_mode=local_file_action.
For explicit final confirmation of the exact active multi-file review batch, use capability=finalize_file_batch_preview with batch_id and batch_version; the resolver fills operation_kind=finalize_file_batch_preview and execution_mode=local_file_action.
For a local reference pack rebuild, use capability=rebuild_reference_pack with params.reference_files objects whose local_path values come from WhatsApp attachments and whose role values are chosen by the planner from file content; include params.class_weekdays from the current staff instruction. The resolver fills operation_kind=rebuild_reference_pack and execution_mode=local_file_action.
For a local semester schedule update, use capability=update_semester_schedule with params.schedule_files from WhatsApp attachment local_path values and/or params.schedule_text from the current staff prompt; the resolver fills operation_kind=update_semester_schedule and execution_mode=local_file_action. The arrangement must cover the complete actual Teaching Week 1-N sequence needed by timeline.json; partial replacement blocks.
For an active timeline draft revision, use capability=revise_timeline_draft with the exact active draft_id and draft_version; the resolver fills operation_kind=revise_timeline_draft and execution_mode=local_file_action.
For a separate final confirmation of the latest READY_FOR_CONFIRMATION draft, use capability=commit_timeline_draft with the exact active draft_id and draft_version; the resolver fills operation_kind=commit_timeline_draft and execution_mode=local_file_action.

Set run_id to {run_id}. Output JSON only, no markdown."""


def run_codex_planner(
    env: dict[str, str],
    run_id: str,
    raw_task: str,
    *,
    detailed_group_structure: dict | None = None,
    detailed_forum_structure: dict | None = None,
    _repair_attempt: int = 0,
    _repair_feedback: str = "",
) -> tuple[dict, dict, str]:
    REPORTS.mkdir(exist_ok=True)
    last_message = REPORTS / f"{env['BATCH_ID']}_{run_id}_course_planner_last_message.txt"
    readback_context = current_course_readback_context(
        env,
        detailed_group_structure=detailed_group_structure,
        detailed_forum_structure=detailed_forum_structure,
    )
    prompt_text = planner_prompt(env, run_id, raw_task, readback_context=readback_context)
    if _repair_feedback:
        prompt_text += (
            "\n\nPlanner contract repair required:\n"
            f"{_repair_feedback}\n"
            "Return a complete replacement JSON object. Re-evaluate the full dependency graph: "
            "every runtime-reference producer must precede every consumer, every consumer must "
            "declare the exact producer reference, and every required target must be grounded in "
            "the supplied readback or an earlier explicit producer. Do not merely delete the "
            "operation that exposed the validation error."
        )
    command = build_codex_exec_command(
        env,
        prompt_text,
        stage="planner",
        cwd=PROJECT_ROOT,
        output_last_message=last_message,
        prompt_via_stdin=True,
    )
    completed = subprocess.run(
        command,
        cwd=ROOT,
        input=prompt_text,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Codex planner failed with exit code {completed.returncode}: {completed.stdout[-2000:]}")
    text = last_message.read_text(encoding="utf-8") if last_message.exists() else completed.stdout
    payload = extract_json_object(text)
    intent_payload = dict(payload.get("course_task_intent") or {})
    intent_payload.setdefault("raw_task", intent_payload.get("staff_prompt") or raw_task)
    intent_payload.setdefault("interpreted_intent", intent_payload.get("status") or raw_task)
    intent = apply_intent_defaults(normalize_course_id_fields(intent_payload))
    manifest = normalize_course_id_fields(dict(payload.get("course_operation_manifest") or {}))
    intent["run_id"] = run_id
    manifest["run_id"] = run_id
    needs_group_detail = (
        bool(manifest.get("operations"))
        and manifest_needs_detailed_group_structure(manifest)
        and str((readback_context.get("group_structure") or {}).get("status") or "") != "detailed"
    )
    forum_structure = (
        readback_context.get("forum_structure")
        if isinstance(readback_context.get("forum_structure"), dict)
        else {}
    )
    needs_forum_detail = (
        bool(manifest.get("operations"))
        and manifest_needs_detailed_forum_structure(manifest)
        and str(forum_structure.get("detail_status") or "") != "detailed"
    )
    if manifest.get("operations") and not needs_group_detail and not needs_forum_detail:
        try:
            manifest = resolve_capability_manifest(
                manifest,
                readback_context=readback_context,
                latest_resource=latest_file_resource_context(),
            )
        except ValueError as exc:
            if _repair_attempt >= 1:
                raise
            prior_manifest = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
            return run_codex_planner(
                env,
                run_id,
                raw_task,
                detailed_group_structure=detailed_group_structure,
                detailed_forum_structure=detailed_forum_structure,
                _repair_attempt=_repair_attempt + 1,
                _repair_feedback=(
                    f"The prior manifest failed deterministic capability resolution: {exc}. "
                    f"Prior manifest: {prior_manifest}"
                ),
            )
        manifest = remove_completed_group_preflight_operation(
            manifest,
            detailed_group_structure,
        )
        manifest = remove_completed_forum_preflight_operation(
            manifest,
            detailed_forum_structure,
        )
    elif needs_group_detail:
        manifest["needs_group_structure_readback"] = True
    if needs_forum_detail:
        manifest["needs_forum_structure_readback"] = True
    manifest = normalize_access_time_operations(manifest, raw_task)
    manifest = apply_manifest_defaults(normalize_course_id_fields(manifest))
    approval_policy = str(
        manifest.get("approval_policy")
        or intent.get("approval_policy")
        or ""
    ).strip().casefold()
    if approval_policy in {"per_write", "per_operation", "separate_each_write"}:
        manifest["operations"] = []
        manifest["requires_approval"] = False
        return (
            intent,
            manifest,
            "\n".join(
                [
                    "Status: Blocked",
                    f"Task: {user_visible_task_text(raw_task)}",
                    "Target: requested Moodle changes",
                    (
                        "Reason: This agent supports one current-run WhatsApp approval for one "
                        "exact bounded manifest. A request for a separate approval before every "
                        "individual write must be split into separate staff prompts."
                    ),
                    f"Course: {SANDBOX_COURSE_ID} sandbox",
                    f"Run: {run_id}",
                ]
            ),
        )
    intent_errors = validate_course_task_intent(intent)
    operations = manifest.get("operations") or []
    deferred_reference_inputs = manifest_needs_reference_pack_attachments(manifest)
    manifest_errors = (
        []
        if not operations or deferred_reference_inputs or needs_group_detail or needs_forum_detail
        else validate_course_operation_manifest(manifest, intent)
    )
    if intent_errors or manifest_errors:
        validation_error = "; ".join(intent_errors + manifest_errors)
        if _repair_attempt >= 1:
            raise ValueError(validation_error)
        prior_manifest = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
        return run_codex_planner(
            env,
            run_id,
            raw_task,
            detailed_group_structure=detailed_group_structure,
            detailed_forum_structure=detailed_forum_structure,
            _repair_attempt=_repair_attempt + 1,
            _repair_feedback=(
                f"The prior planner output failed deterministic schema/safety validation: "
                f"{validation_error}. Prior manifest: {prior_manifest}"
            ),
        )
    approval_message = str(payload.get("approval_message") or "").strip()
    return intent, manifest, approval_message


def detailed_group_structure_for_manifest(
    env: dict[str, str],
    manifest: dict,
    moodle_page,
    *,
    run_id: str,
) -> dict:
    if moodle_page is None:
        raise RuntimeError("Detailed Group structure readback needs the Moodle browser session.")
    moodle_env = load_moodle_browser_env()
    moodle_env.update(env)
    ensure_moodle_authenticated(moodle_page, moodle_env)
    base_readback = current_course_readback_context(env)
    base_snapshot = base_readback.get("group_structure")
    if not isinstance(base_snapshot, dict) or base_snapshot.get("status") != "api_only":
        reason = (
            base_snapshot.get("reason")
            if isinstance(base_snapshot, dict)
            else "APIM Group readback did not return a usable snapshot."
        )
        raise RuntimeError(f"Could not prepare Group structure readback: {reason}")
    report_dir = (
        REPORTS
        / f"group_structure_{stamp()}_{safe_name(run_id or 'run')}"
    )
    try:
        return read_detailed_group_structure(
            moodle_page,
            base_snapshot,
            report_dir=report_dir,
            include_choicegroup_dependencies=True,
        )
    except RuntimeError as exc:
        if "requested login" not in str(exc).casefold():
            raise
        ensure_moodle_authenticated(
            moodle_page,
            moodle_env,
            target_url=SANDBOX_COURSE_URL,
        )
        return read_detailed_group_structure(
            moodle_page,
            base_snapshot,
            report_dir=report_dir,
            include_choicegroup_dependencies=True,
        )


def manifest_group_snapshot_hash(manifest: dict) -> str:
    hashes = {
        str(operation.get("group_snapshot_hash") or "").strip()
        for operation in manifest.get("operations") or []
        if isinstance(operation, dict)
        and str(operation.get("group_snapshot_hash") or "").strip()
    }
    if len(hashes) > 1:
        raise RuntimeError("Approved Group operations do not share one bounded snapshot.")
    return next(iter(hashes), "")


def group_preflight_blocker(manifest: dict, fresh_snapshot: dict) -> str:
    expected_hash = manifest_group_snapshot_hash(manifest)
    if not expected_hash:
        return "The approved Group manifest is missing its detailed preflight snapshot."
    actual_hash = group_action_preflight_hash(fresh_snapshot)
    if actual_hash != expected_hash:
        return (
            "The sandbox Group structure changed while approval was pending. "
            "No Moodle write was attempted; send a new request so the scope can be planned again."
        )
    return ""


def _snapshot_named_items(snapshot: dict, key: str, name: str) -> list[dict]:
    return [
        item
        for item in snapshot.get(key) or []
        if isinstance(item, dict) and str(item.get("name") or "").strip() == name
    ]


GROUP_OPERATION_CAPABILITIES = {
    "create_group",
    "create_grouping",
    "update_grouping_membership",
    "update_group_membership",
    "delete_group",
    "delete_grouping",
    "create_group_signup_activity",
}
GROUP_OPERATION_KINDS = {
    "group_api",
    "grouping_api",
    "browser_group_membership",
    "browser_group_signup_activity",
}


def _is_group_manifest_operation(operation: dict) -> bool:
    return (
        str(operation.get("capability") or "").strip()
        in GROUP_OPERATION_CAPABILITIES
        or str(operation.get("operation_kind") or "").strip()
        in GROUP_OPERATION_KINDS
    )


def _execution_item_failure_reason(item: dict) -> str:
    api_result = item.get("api_result") if isinstance(item.get("api_result"), dict) else {}
    return first_text(
        item.get("blocker"),
        item.get("reason"),
        item.get("error"),
        api_result.get("reason"),
        api_result.get("error"),
        item.get("note"),
    )


def verify_group_manifest_snapshot(
    run_id: str,
    manifest: dict,
    snapshot: dict,
    execution_state: dict,
    *,
    initial_snapshot: dict | None = None,
) -> dict:
    checks: list[dict] = []
    failures: list[str] = []
    groupings_by_name = {
        str(item.get("name") or ""): item
        for item in snapshot.get("groupings") or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    groups_by_name = {
        str(item.get("name") or "").strip(): item
        for item in snapshot.get("groups") or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    initial_group_counts = {
        str(item.get("name") or "").strip(): item.get("member_count")
        for item in (initial_snapshot or {}).get("groups") or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    execution_result = execution_state.get("moodle_result") or {}
    nested_results = [
        item
        for item in (
            (execution_result.get("final_moodle_state") or {}).get("results")
            or execution_result.get("results")
            or []
        )
        if isinstance(item, dict)
    ]
    group_operation_indexes = {
        index
        for index, operation in enumerate(manifest.get("operations") or [], start=1)
        if isinstance(operation, dict) and _is_group_manifest_operation(operation)
    }
    group_operation_actions = {
        str(operation.get("action") or "").strip()
        for operation in manifest.get("operations") or []
        if isinstance(operation, dict) and _is_group_manifest_operation(operation)
    }
    failed_group_execution_items = [
        item
        for item in nested_results
        if (
            (
                str(item.get("course_agent_operation_index") or "").isdigit()
                and int(item.get("course_agent_operation_index")) in group_operation_indexes
            )
            or str(item.get("action") or "").strip() in group_operation_actions
        )
        and str(item.get("status") or "").endswith(("_failed", "_blocked"))
    ]
    group_execution_reasons = list(
        dict.fromkeys(
            reason
            for reason in (
                _execution_item_failure_reason(item)
                for item in failed_group_execution_items
            )
            if reason
        )
    )
    manifest_operations = [
        operation
        for operation in manifest.get("operations") or []
        if isinstance(operation, dict)
    ]
    execution_status_text = " ".join(
        [
            str(execution_state.get("status") or ""),
            str(execution_result.get("status") or ""),
        ]
    ).casefold()
    if (
        not group_execution_reasons
        and manifest_operations
        and all(_is_group_manifest_operation(operation) for operation in manifest_operations)
        and ("failed" in execution_status_text or "blocked" in execution_status_text)
    ):
        aggregate_group_reason = first_text(
            execution_result.get("note"),
            execution_result.get("reason"),
            execution_result.get("blocker"),
            execution_result.get("error"),
            *(
                _execution_item_failure_reason(item)
                for item in nested_results
            ),
            *(
                str(item).strip()
                for item in (execution_result.get("verification") or {}).get("failures") or []
                if str(item).strip()
            ),
        )
        if aggregate_group_reason:
            group_execution_reasons.append(aggregate_group_reason)
    membership_execution_results = [
        item
        for item in nested_results
        if isinstance(item, dict) and item.get("membership_action")
    ]
    expected_group_names: set[str] | None = None
    expected_grouping_members: dict[str, set[str]] | None = None
    if isinstance(initial_snapshot, dict):
        expected_group_names = {
            str(item.get("name") or "").strip()
            for item in initial_snapshot.get("groups") or []
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }
        expected_grouping_members = {
            str(item.get("name") or "").strip(): {
                str(value).strip()
                for value in item.get("group_names") or []
                if str(value or "").strip()
            }
            for item in initial_snapshot.get("groupings") or []
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }
        for planned in manifest.get("operations") or []:
            if not isinstance(planned, dict):
                continue
            planned_action = str(planned.get("action") or "").strip()
            if planned_action == "create_group":
                planned_name = str(
                    planned.get("group_name") or planned.get("target_object") or ""
                ).strip()
                if planned_name:
                    expected_group_names.add(planned_name)
            elif planned_action == "delete_group":
                planned_name = str(
                    planned.get("group_name") or planned.get("target_object") or ""
                ).strip()
                expected_group_names.discard(planned_name)
                for members in expected_grouping_members.values():
                    members.discard(planned_name)
            elif planned_action == "create_grouping":
                planned_name = str(
                    planned.get("grouping_name") or planned.get("target_object") or ""
                ).strip()
                if planned_name:
                    expected_grouping_members.setdefault(planned_name, set())
            elif planned_action == "delete_grouping":
                planned_name = str(
                    planned.get("grouping_name") or planned.get("target_object") or ""
                ).strip()
                expected_grouping_members.pop(planned_name, None)
            elif planned_action in {
                "assign_group_names_to_grouping",
                "unassign_group_names_from_grouping",
            }:
                planned_grouping = str(planned.get("grouping_name") or "").strip()
                planned_groups = {
                    str(value).strip()
                    for value in planned.get("group_names") or []
                    if str(value or "").strip()
                }
                if planned_grouping in expected_grouping_members:
                    if planned_action == "assign_group_names_to_grouping":
                        expected_grouping_members[planned_grouping].update(planned_groups)
                    else:
                        expected_grouping_members[planned_grouping].difference_update(
                            planned_groups
                        )
    for operation in manifest.get("operations") or []:
        if not isinstance(operation, dict):
            continue
        action = str(operation.get("action") or "").strip()
        capability = str(operation.get("capability") or "").strip()
        if not _is_group_manifest_operation(operation):
            continue
        passed = True
        detail = ""
        if action == "create_group":
            name = str(operation.get("group_name") or operation.get("target_object") or "").strip()
            count = len(_snapshot_named_items(snapshot, "groups", name))
            expected_present = (
                name in expected_group_names
                if expected_group_names is not None
                else True
            )
            passed = count == (1 if expected_present else 0)
            detail = (
                f"Group {name!r} final exact match count is {count}; "
                f"expected {'present' if expected_present else 'absent'}"
            )
        elif action == "delete_group":
            name = str(operation.get("group_name") or operation.get("target_object") or "").strip()
            count = len(_snapshot_named_items(snapshot, "groups", name))
            expected_present = (
                name in expected_group_names
                if expected_group_names is not None
                else False
            )
            passed = count == (1 if expected_present else 0)
            detail = (
                f"Group {name!r} final exact match count is {count}; "
                f"expected {'present' if expected_present else 'absent'}"
            )
        elif action == "create_grouping":
            name = str(operation.get("grouping_name") or operation.get("target_object") or "").strip()
            count = len(_snapshot_named_items(snapshot, "groupings", name))
            expected_present = (
                name in expected_grouping_members
                if expected_grouping_members is not None
                else True
            )
            passed = count == (1 if expected_present else 0)
            detail = (
                f"Grouping {name!r} final exact match count is {count}; "
                f"expected {'present' if expected_present else 'absent'}"
            )
        elif action == "delete_grouping":
            name = str(operation.get("grouping_name") or operation.get("target_object") or "").strip()
            count = len(_snapshot_named_items(snapshot, "groupings", name))
            expected_present = (
                name in expected_grouping_members
                if expected_grouping_members is not None
                else False
            )
            passed = count == (1 if expected_present else 0)
            detail = (
                f"Grouping {name!r} final exact match count is {count}; "
                f"expected {'present' if expected_present else 'absent'}"
            )
        elif action in {"assign_group_names_to_grouping", "unassign_group_names_from_grouping"}:
            grouping_name = str(operation.get("grouping_name") or "").strip()
            group_names = {
                str(value).strip()
                for value in operation.get("group_names") or []
                if str(value or "").strip()
            }
            grouping = groupings_by_name.get(grouping_name)
            actual = {
                str(value).strip()
                for value in (grouping or {}).get("group_names") or []
                if str(value or "").strip()
            }
            if expected_grouping_members is not None:
                expected_members = expected_grouping_members.get(grouping_name)
                if expected_members is None:
                    passed = grouping is None
                    detail = (
                        f"Grouping {grouping_name!r} is absent after the approved "
                        "ordered relationship changes and deletion"
                    )
                else:
                    passed = grouping is not None and actual == expected_members
                    detail = (
                        f"Grouping {grouping_name!r} final Groups are {sorted(actual)}; "
                        f"expected {sorted(expected_members)}"
                    )
            elif action == "assign_group_names_to_grouping":
                passed = grouping is not None and group_names <= actual
                detail = f"Grouping {grouping_name!r} contains approved additions {sorted(group_names)}"
            else:
                passed = grouping is not None and not (group_names & actual)
                detail = f"Grouping {grouping_name!r} excludes approved removals {sorted(group_names)}"
        elif action in {"add_group_member", "remove_group_member", "move_group_member"}:
            operation_id = str(operation.get("operation_id") or "").strip()
            source_name = str(operation.get("source_group_name") or "").strip()
            target_name = str(operation.get("target_group_name") or "").strip()
            membership_action = action.removesuffix("_group_member")
            matching_execution = [
                item
                for item in membership_execution_results
                if (
                    (not operation_id or str(item.get("operation_id") or "") == operation_id)
                    and str(item.get("membership_action") or "") == membership_action
                    and str(item.get("source_group_name") or "") == source_name
                    and str(item.get("target_group_name") or "") == target_name
                )
            ]
            execution = matching_execution[0] if len(matching_execution) == 1 else {}
            expected_source_delta = int(execution.get("source_count_delta") or 0)
            expected_target_delta = int(execution.get("target_count_delta") or 0)
            count_checks: list[str] = []
            passed = bool(execution.get("membership_verified"))
            if source_name:
                initial_count = initial_group_counts.get(source_name)
                final_count = (groups_by_name.get(source_name) or {}).get("member_count")
                source_passed = (
                    isinstance(initial_count, int)
                    and isinstance(final_count, int)
                    and final_count == initial_count + expected_source_delta
                )
                passed = passed and source_passed
                count_checks.append(
                    f"source Group {source_name!r} count {initial_count}->{final_count} "
                    f"(expected delta {expected_source_delta})"
                )
            if target_name:
                initial_count = initial_group_counts.get(target_name)
                final_count = (groups_by_name.get(target_name) or {}).get("member_count")
                target_passed = (
                    isinstance(initial_count, int)
                    and isinstance(final_count, int)
                    and final_count == initial_count + expected_target_delta
                )
                passed = passed and target_passed
                count_checks.append(
                    f"target Group {target_name!r} count {initial_count}->{final_count} "
                    f"(expected delta {expected_target_delta})"
                )
            detail = (
                f"Exact participant membership was transiently verified; {'; '.join(count_checks)}"
                if execution.get("membership_verified")
                else "Exact participant membership was not verified by the approved browser operation"
            )
        elif action == "create_group_signup_activity":
            title = str(operation.get("title") or operation.get("target_object") or "").strip()
            section = str(operation.get("section_name") or "").strip()
            candidates = [
                item
                for item in snapshot.get("choicegroup_activities") or []
                if isinstance(item, dict)
                and str(item.get("title") or "").strip() == title
                and (not section or str(item.get("section") or "").strip() == section)
            ]
            expected_groups = {
                str(value).strip()
                for value in operation.get("group_names")
                or operation.get("choicegroup_target_group_names")
                or []
                if str(value or "").strip()
            }
            if len(candidates) != 1:
                passed = False
                detail = f"Choicegroup {title!r} exact match count is {len(candidates)}"
            else:
                activity = candidates[0]
                actual_groups = {
                    str(value).strip()
                    for value in activity.get("selected_group_names") or []
                    if str(value or "").strip()
                }
                expected_visible = bool(operation.get("visible", True))
                actual_visible = bool(activity.get("visible"))
                actual_maxanswers = activity.get("maxanswers")
                quota_consistent = activity.get("quota_consistent")
                selected_quota_count = int(activity.get("selected_quota_count") or 0)
                passed = (
                    actual_groups == expected_groups
                    and actual_visible == expected_visible
                    and isinstance(actual_maxanswers, int)
                )
                if operation.get("maxanswers") not in ("", None):
                    passed = (
                        passed
                        and actual_maxanswers == int(operation.get("maxanswers"))
                        and quota_consistent is True
                        and selected_quota_count == len(expected_groups)
                    )
                detail = (
                    f"Choicegroup {title!r} groups={sorted(actual_groups)}, "
                    f"visible={actual_visible}, maxanswers={actual_maxanswers}, "
                    f"quota_consistent={quota_consistent}, "
                    f"selected_quota_count={selected_quota_count}"
                )
        checks.append(
            {
                "action": action or capability,
                "target": str(operation.get("target_object") or operation.get("approval_item") or ""),
                "passed": passed,
                "detail": detail,
            }
        )
        if not passed:
            failures.append(detail or f"{action or capability} readback did not match")
    passed = bool(checks) and not failures
    saves_count = int(execution_result.get("saves_count", 0) or 0)
    execution_reason = first_text(*group_execution_reasons)
    if failures and execution_reason:
        if saves_count == 0:
            failures = [execution_reason]
        elif execution_reason not in failures:
            failures.append(execution_reason)
    result_status = "PASS" if passed else ("BLOCKED" if saves_count == 0 else "FAIL")
    reported_checks = (
        []
        if result_status == "BLOCKED" and execution_reason
        else checks
    )
    summary = (
        "Group structure readback matched the approved manifest."
        if passed
        else first_text(
            execution_reason,
            "Group execution or final structure readback did not match the approved manifest.",
        )
    )
    return {
        "run_id": run_id,
        "pipeline_stage": "verifiers",
        "status": result_status,
        "summary": summary,
        "blocker": summary if result_status == "BLOCKED" else "",
        "target_object": str(execution_result.get("target_object") or "Sandbox Group structure"),
        "saves_count": saves_count,
        "moodle_urls": [SANDBOX_COURSE_URL],
        "items": reported_checks,
        "evidence": [
            str(value)
            for value in snapshot.get("evidence") or []
            if str(value or "").strip()
        ],
        "verification": {
            "passed": passed,
            "checks": reported_checks,
            "failures": failures,
            "execution_reconciled_by_readback": bool(
                passed and failed_group_execution_items
            ),
            "snapshot_hash": str(snapshot.get("snapshot_hash") or ""),
        },
        "final_moodle_state": {
            "course_url": SANDBOX_COURSE_URL,
            "group_count": len(snapshot.get("groups") or []),
            "grouping_count": len(snapshot.get("groupings") or []),
            "choicegroup_count": len(snapshot.get("choicegroup_activities") or []),
        },
    }


def blocked_group_preflight_result(run_id: str, blocker: str) -> dict:
    return {
        "run_id": run_id,
        "pipeline_stage": "verifiers",
        "status": "BLOCKED",
        "summary": blocker,
        "target_object": "Sandbox Group structure",
        "saves_count": 0,
        "moodle_urls": [SANDBOX_COURSE_URL],
        "items": [],
        "evidence": [],
        "verification": {
            "passed": False,
            "preflight_blocked": True,
            "failures": [blocker],
        },
        "final_moodle_state": {"course_url": SANDBOX_COURSE_URL},
    }


def group_structure_readback_blocker(exc: Exception) -> tuple[str, str]:
    text = str(exc or "")
    lowered = text.casefold()
    if "login" in lowered or "signed out" in lowered:
        return (
            "The Moodle browser session is signed out. Sign in using the visible "
            "agent Moodle window, then resend the Group structure request.",
            "moodle_login_required",
        )
    if (
        "did not expose the member list" in lowered
        or "did not expose assigned groups" in lowered
        or "did not expose selected groups" in lowered
        or "exposed unknown group labels" in lowered
    ):
        return (
            "The sandbox Moodle page did not expose one required Group structure "
            "field uniquely. No approval was requested and no Moodle write was attempted.",
            "group_structure_dom_unavailable",
        )
    if (
        "ambiguous" in lowered
        or "could not find exact moodle group" in lowered
        or "could not find exact moodle grouping" in lowered
    ):
        return (
            "A requested Group or Grouping name was missing or not unique in the "
            "detailed sandbox readback. No approval was requested and no Moodle "
            "write was attempted.",
            "group_structure_name_not_unique",
        )
    return (
        "I could not obtain and uniquely resolve the detailed sandbox Group "
        "structure. No approval was requested and no Moodle write was attempted.",
        "group_structure_readback_unavailable",
    )


def failed_group_verification_result(
    run_id: str,
    execution_state: dict,
    reason: str,
) -> dict:
    execution_result = execution_state.get("moodle_result") or {}
    return {
        "run_id": run_id,
        "pipeline_stage": "verifiers",
        "status": "FAIL",
        "summary": reason,
        "target_object": str(
            execution_result.get("target_object") or "Sandbox Group structure"
        ),
        "saves_count": int(execution_result.get("saves_count", 0) or 0),
        "moodle_urls": [SANDBOX_COURSE_URL],
        "items": [],
        "evidence": [],
        "verification": {
            "passed": False,
            "readback_failed": True,
            "failures": [reason],
        },
        "final_moodle_state": {"course_url": SANDBOX_COURSE_URL},
    }


def first_text(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return re.sub(r"\s+", " ", text)
    return ""


def cmid_from_moodle_module_url(url: str) -> str:
    parsed = urlparse(str(url or ""))
    if parsed.hostname and parsed.hostname != "migrationtest-moodle.hku.hk":
        return ""
    if not parsed.path.startswith("/mod/"):
        return ""
    values = parse_qs(parsed.query).get("id") or []
    return values[0] if values and values[0].isdigit() else ""


def strip_internal_attachment_context(value: object) -> str:
    text = str(value or "").replace("\u200e", "").strip()
    if not text:
        return ""
    text = re.split(
        r"\n\s*(?:WhatsApp attached file\(s\):|Reference attachments collected for rebuild:|Reference collection status:)",
        text,
        maxsplit=1,
    )[0].strip()
    visible_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        if "local_path=" in lowered or "download_failed=" in lowered:
            continue
        if lowered.startswith("when the staff says"):
            continue
        visible_lines.append(line)
    return "\n".join(visible_lines).strip()


def looks_like_whatsapp_document_metadata_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if re.fullmatch(r"(PDF|DOCX?|PPTX?|XLSX?|TXT|CSV|ZIP|RAR)", stripped, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"[A-Z]", stripped):
        return True
    if re.fullmatch(
        r"(?:\d+\s+pages?\s*[•·]\s*)?[A-Z0-9]+\s*[•·]\s*[\d.]+\s*(?:kB|MB|GB)",
        stripped,
        flags=re.IGNORECASE,
    ):
        return True
    if re.fullmatch(r"\d{1,2}:\d{2}\s*(?:AM|PM)?", stripped, flags=re.IGNORECASE):
        return True
    if re.search(r"\.(?:pdf|docx?|pptx?|xlsx?|txt|csv|zip|rar)\b", stripped, flags=re.IGNORECASE):
        command_cues = (
            "上传",
            "删除",
            "改",
            "设置",
            "可见",
            "隐藏",
            "upload",
            "delete",
            "remove",
            "set",
            "make",
            "rename",
        )
        if not any(cue in stripped.lower() for cue in command_cues):
            return True
    return False


def user_visible_task_text(*values: object) -> str:
    for value in values:
        cleaned = strip_internal_attachment_context(value)
        if not cleaned:
            continue
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        if len(lines) > 1:
            request_lines = [line for line in lines if not looks_like_whatsapp_document_metadata_line(line)]
            if request_lines:
                return re.sub(r"\s+", " ", request_lines[-1]).strip()
        return re.sub(r"\s+", " ", cleaned).strip()
    return ""


def compact_approval_value(value: object) -> str:
    if isinstance(value, dict):
        for key in ("display", "label", "summary", "scope", "scope_limit", "description", "intended_change"):
            text = first_text(value.get(key))
            if text:
                return shorten_approval_value(text)
        targets = value.get("targets") or value.get("items") or value.get("operations")
        count = value.get("count") or value.get("item_count") or value.get("target_count")
        if not count and isinstance(targets, list):
            count = len(targets)
        if count:
            noun = first_text(value.get("noun"), value.get("kind"), "Moodle items")
            return f"{count} {noun}"
        return ""
    if isinstance(value, list):
        if not value:
            return ""
        if len(value) == 1:
            return compact_approval_value(value[0])
        return f"{len(value)} Moodle items"
    return shorten_approval_value(first_text(value))


def shorten_approval_value(text: str, limit: int = 160) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _join_reason_parts(value: object) -> str:
    if isinstance(value, (list, tuple, set)):
        return "; ".join(first_text(item) for item in value if first_text(item))
    return first_text(value)


def human_block_reason(value: object, *, default: str = "I could not safely prepare a Moodle change for this request.") -> str:
    if isinstance(value, (list, tuple, set)):
        raw_reason = "\n".join(str(item or "") for item in value if str(item or "").strip())
    else:
        raw_reason = str(value or "")
    reason = _join_reason_parts(value)
    if not reason:
        return default
    if re.search(r"(?im)^Status:\s*(Done|Blocked|Failed|Waiting)\b", raw_reason):
        match = re.search(r"(?im)^Reason:\s*(.+)$", raw_reason)
        if match:
            return shorten_approval_value(match.group(1).strip(), 260)
        return default
    lowered = reason.lower()
    if "get_by_role(\"button\", name=\"edit mode\")" in reason or "edit mode" in lowered:
        return "Moodle page did not expose an Edit mode button; the browser step was too brittle."
    if "could not focus the whatsapp message compose box" in lowered:
        return "WhatsApp Web compose box could not be focused."
    if "download_failed" in lowered or ("attached file" in lowered and "local_path" in lowered):
        return "The WhatsApp attachment could not be downloaded to a verified local file, so I stopped before writing."
    if "moodle_upload_file_path" in lowered or "target_file_path" in lowered:
        return "I could not verify the file to upload, so I stopped before writing."
    if "upload file does not exist" in lowered or "file does not exist" in lowered:
        return "The file to upload was not available at the verified local path, so I stopped before writing."
    if "requires a filename" in lowered:
        return "I could not determine the filename for the Moodle upload."
    if "requires a resource_name" in lowered or "requires a resource name" in lowered:
        return "I could not determine the Moodle file resource name."
    if "unresolved placeholders" in lowered:
        return "Some required Moodle details were still unresolved, so I stopped before writing."
    if "target course changed" in lowered or "legacy live moodle" in lowered or "old course" in lowered:
        return f"The request targets a course outside the sandbox. I can only write to course {SANDBOX_COURSE_ID}."
    if "student visibility" in lowered or "increase student visibility" in lowered:
        return "The planned change may affect student visibility, so I stopped before writing."
    if "outside the approved intent" in lowered or "outside approved wording" in lowered:
        return "The planned change does not match the approved task boundary, so I stopped before writing."
    if "unsupported moodle api action" in lowered or "apim whitelist" in lowered or "unsupported by the current moodle" in lowered:
        return "This Moodle change is not covered by the current supported tools, so I stopped before writing."
    if "no supported moodle operation" in lowered or "no supported operation" in lowered:
        return "I could not map this request to a supported Moodle change, so I stopped before writing."
    if "dase7137_key" in lowered or "subscription-key" in lowered or "api key" in lowered:
        return "The Moodle service credential is not configured, so I stopped before writing."
    internal_tokens = (
        "operation_kind",
        "browser_steps",
        "browser_moodle_task",
        "cmid",
        "moodle_",
        "core_",
        "local_courseops_api",
        "{",
        "}",
        "[{",
    )
    if any(token in lowered for token in internal_tokens):
        return default
    sanitized = re.sub(r"/Users/[^ \n\r\t,;:]+", "the local file", reason)
    sanitized = re.sub(r"\bcourse id\b", "course", sanitized, flags=re.IGNORECASE)
    return shorten_approval_value(sanitized, 260)


def blocked_agent_text(
    *,
    run_id: str,
    task: object = "",
    reason: object = "",
    target: object = "",
) -> str:
    lines = [
        "Course agent blocked",
        "",
        "Status: Blocked",
    ]
    task_text = user_visible_task_text(task)
    if task_text:
        lines.append(f"Task: {task_text}")
    target_text = first_text(target)
    if target_text:
        lines.append(f"Target: {target_text}")
    lines.extend(
        [
            f"Reason: {human_block_reason(reason)}",
            f"Course: {SANDBOX_COURSE_ID} sandbox",
        ]
    )
    if run_id:
        lines.append(f"Run: {run_id}")
    return "\n".join(lines)


def is_read_only_noop_result(intent: dict, manifest: dict, planner_message: str) -> bool:
    if manifest.get("operations") or []:
        return False
    if not (
        manifest.get("read_only") is True
        or manifest.get("requires_approval") is False
        or intent.get("is_state_changing") is False
    ):
        return False
    return bool(re.search(r"(?im)^Status:\s*Done\b", planner_message or ""))


def is_user_facing_noop_message(manifest: dict, planner_message: str) -> bool:
    if manifest.get("operations") or []:
        return False
    return bool(re.search(r"(?im)^Status:\s*(Done|Blocked|Failed|Waiting)\b", planner_message or ""))


def natural_status_intro(status_label: str) -> str:
    normalized = status_label.strip().lower()
    if normalized == "done":
        return "Done. I checked Moodle and found the requested information."
    if normalized == "blocked":
        return "I could not complete this Moodle request."
    if normalized == "failed":
        return "I tried the Moodle request, but it did not finish correctly."
    if normalized == "waiting":
        return "I am still waiting to finish this Moodle request."
    return f"Moodle request status: {status_label.strip() or 'Unknown'}."


def naturalize_status_message(message: str, run_id: str = "") -> str:
    lines = [line.strip() for line in str(message or "").replace("\u200e", "").splitlines() if line.strip()]
    fields: dict[str, str] = {}
    details: list[str] = []
    detail_mode = False
    for line in lines:
        match = re.match(r"^(Status|Task|Target|Change|Verify|Reason|Course|Run|URL):\s*(.*)$", line, flags=re.IGNORECASE)
        if match:
            fields[match.group(1).lower()] = match.group(2).strip()
            continue
        if line.lower() == "contents:":
            detail_mode = True
        details.append(line)
    status_label = fields.get("status", "")
    if not status_label:
        return str(message or "").strip()
    natural = [natural_status_intro(status_label), ""]
    if fields.get("task"):
        natural.append(f"Your request: {user_visible_task_text(fields['task'])}")
    if fields.get("target"):
        natural.append(f"Moodle item: {fields['target']}")
    if fields.get("change"):
        natural.append(f"What happened: {fields['change']}")
    if fields.get("verify"):
        natural.append(f"Check: {fields['verify']}")
    if fields.get("reason"):
        natural.append(f"Why: {fields['reason']}")
    if details:
        natural.extend(["", *details])
    if fields.get("course"):
        natural.append(f"Course: {fields['course']}")
    elif SANDBOX_COURSE_ID:
        natural.append(f"Course: {SANDBOX_COURSE_ID} sandbox")
    final_run_id = fields.get("run") or run_id
    if final_run_id:
        natural.append(f"Run: {final_run_id}")
    if fields.get("url"):
        natural.append(f"Link: {fields['url']}")
    return "\n".join(natural)


def user_facing_noop_message_text(run_id: str, planner_message: str) -> str:
    message = str(planner_message or "").strip()
    return naturalize_status_message(message, run_id)


def read_only_noop_result_text(run_id: str, raw_task: str, manifest: dict, planner_message: str) -> str:
    message = str(planner_message or "").strip()
    if message:
        return user_facing_noop_message_text(run_id, message)
    result = manifest.get("result") if isinstance(manifest, dict) else {}
    count = result.get("section_count") if isinstance(result, dict) else ""
    verify = f"Current readback shows {count} sections" if count else "Read-only Moodle state was returned"
    return "\n".join(
        [
            "Done. I checked Moodle and found the requested information.",
            "",
            f"Your request: {user_visible_task_text(raw_task)}",
            "Moodle item: Moodle course",
            "What happened: No Moodle changes were requested.",
            f"Check: {verify}.",
            f"Course: {SANDBOX_COURSE_ID} sandbox",
            f"Run: {run_id}",
        ]
    )


def manifest_is_only_group_structure_readback(manifest: dict) -> bool:
    operations = manifest.get("operations") or []
    return bool(operations) and all(
        isinstance(operation, dict)
        and operation.get("operation_kind") == GROUP_STRUCTURE_READBACK_KIND
        for operation in operations
    )


def remove_completed_group_preflight_operation(
    manifest: dict,
    detailed_group_structure: dict | None,
) -> dict:
    """Remove a satisfied read-only inspection from a final Group write plan."""
    if not isinstance(detailed_group_structure, dict):
        return manifest
    structure = detailed_group_structure.get("group_structure")
    if not isinstance(structure, dict) or structure.get("status") != "detailed":
        return manifest
    if not manifest_has_group_writes(manifest):
        return manifest
    operations = manifest.get("operations") or []
    write_operations = [
        operation
        for operation in operations
        if not (
            isinstance(operation, dict)
            and (
                operation.get("capability") == "inspect_group_structure"
                or operation.get("operation_kind") == GROUP_STRUCTURE_READBACK_KIND
            )
        )
    ]
    if len(write_operations) == len(operations):
        return manifest
    normalized = dict(manifest)
    normalized["operations"] = write_operations
    return normalized


def remove_completed_forum_preflight_operation(
    manifest: dict,
    detailed_forum_structure: dict | None,
) -> dict:
    """Remove a satisfied read-only Forum inspection from a final write plan."""
    if (
        not isinstance(detailed_forum_structure, dict)
        or detailed_forum_structure.get("detail_status") != "detailed"
    ):
        return manifest
    operations = manifest.get("operations") or []
    has_non_readback_operation = any(
        isinstance(operation, dict)
        and operation.get("capability") != "inspect_forum_structure"
        and operation.get("operation_kind") != FORUM_STRUCTURE_READBACK_KIND
        for operation in operations
    )
    if not has_non_readback_operation:
        return manifest
    write_operations = [
        operation
        for operation in operations
        if not (
            isinstance(operation, dict)
            and (
                operation.get("capability") == "inspect_forum_structure"
                or operation.get("operation_kind") == FORUM_STRUCTURE_READBACK_KIND
            )
        )
    ]
    if len(write_operations) == len(operations):
        return manifest
    normalized = dict(manifest)
    normalized["operations"] = write_operations
    return normalized


def group_structure_result_text(
    run_id: str,
    raw_task: str,
    snapshot: dict,
    manifest: dict | None = None,
) -> str:
    operation = (
        (manifest or {}).get("operations", [])[0]
        if (manifest or {}).get("operations")
        else {}
    )
    requested_groups = set(operation.get("group_names") or []) if isinstance(operation, dict) else set()
    requested_groupings = (
        set(operation.get("grouping_names") or []) if isinstance(operation, dict) else set()
    )
    scope = str(operation.get("scope") or "course") if isinstance(operation, dict) else "course"
    include_choicegroups = bool(
        operation.get("include_choicegroup_dependencies")
        or scope == "choicegroup_dependencies"
    ) if isinstance(operation, dict) else True
    show_groups = scope in {"course", "groups", "choicegroup_dependencies"} or bool(
        requested_groups
    )
    show_groupings = scope in {
        "course",
        "groupings",
        "choicegroup_dependencies",
    } or bool(requested_groupings)
    groups = [
        group
        for group in snapshot.get("groups") or []
        if isinstance(group, dict)
        and show_groups
        and (not requested_groups or str(group.get("name") or "") in requested_groups)
    ]
    groupings = [
        grouping
        for grouping in snapshot.get("groupings") or []
        if isinstance(grouping, dict)
        and show_groupings
        and (not requested_groupings or str(grouping.get("name") or "") in requested_groupings)
    ]
    activities = [
        activity
        for activity in snapshot.get("choicegroup_activities") or []
        if isinstance(activity, dict)
        and include_choicegroups
    ]
    lines = [
        "Status: Done",
        f"Task: {user_visible_task_text(raw_task)}",
        "Target: Sandbox Group structure",
        "Change: No Moodle changes were made.",
        (
            f"Verify: Read {len(groups)} Group(s), {len(groupings)} Grouping(s), "
            f"and {len(activities)} Choicegroup activity setting(s)."
        ),
    ]
    if groups:
        lines.append("Groups:")
        for group in groups:
            count = group.get("member_count")
            count_text = str(count) if isinstance(count, int) else "not inspected"
            grouping_text = ", ".join(str(value) for value in group.get("grouping_names") or []) or "none"
            group_line = (
                f"- {group.get('name')}: members {count_text}; Groupings {grouping_text}"
            )
            if include_choicegroups:
                reference_text = ", ".join(
                    str(ref.get("title") or "")
                    for ref in group.get("choicegroup_refs") or []
                    if isinstance(ref, dict) and str(ref.get("title") or "").strip()
                ) or "none"
                group_line += f"; Choicegroup references {reference_text}"
            lines.append(group_line)
    if groupings:
        lines.append("Groupings:")
        for grouping in groupings:
            members = ", ".join(str(value) for value in grouping.get("group_names") or []) or "empty"
            lines.append(f"- {grouping.get('name')}: {members}")
    if activities:
        lines.append("Choicegroup activities:")
        for activity in activities:
            selected = ", ".join(
                str(value) for value in activity.get("selected_group_names") or []
            ) or "none"
            lines.append(f"- {activity.get('title')}: {selected}")
    lines.extend([f"Course: {SANDBOX_COURSE_ID} sandbox", f"Run: {run_id}"])
    return "\n".join(lines)


def no_group_changes_result_text(run_id: str, raw_task: str, manifest: dict) -> str:
    notes = [
        str(value).strip()
        for value in manifest.get("resolution_notes") or []
        if str(value or "").strip()
    ]
    verify = "; ".join(notes) or "The requested Group state already matches Moodle."
    return "\n".join(
        [
            "Status: Done",
            f"Task: {user_visible_task_text(raw_task)}",
            "Target: Sandbox Group structure",
            "Change: No Moodle changes were needed.",
            f"Verify: {verify}",
            f"Course: {SANDBOX_COURSE_ID} sandbox",
            f"Run: {run_id}",
        ]
    )


def read_only_group_course_agent_result(
    run_id: str,
    snapshot: dict,
    summary: str,
) -> dict:
    return {
        "run_id": run_id,
        "pipeline_stage": "verifiers",
        "status": "PASS",
        "summary": summary,
        "target_object": "Sandbox Group structure",
        "saves_count": 0,
        "moodle_urls": [SANDBOX_COURSE_URL],
        "items": [],
        "evidence": [
            str(value)
            for value in snapshot.get("evidence") or []
            if str(value or "").strip()
        ],
        "verification": {
            "passed": True,
            "read_only": True,
            "snapshot_hash": str(snapshot.get("snapshot_hash") or ""),
            "failures": [],
        },
        "final_moodle_state": {
            "course_url": SANDBOX_COURSE_URL,
            "group_count": len(snapshot.get("groups") or []),
            "grouping_count": len(snapshot.get("groupings") or []),
            "choicegroup_count": len(snapshot.get("choicegroup_activities") or []),
        },
    }


def is_forum_readonly_query(raw_task: str, intent: dict | None = None, manifest: dict | None = None, planner_message: str = "") -> bool:
    del raw_task, planner_message
    intent = intent if isinstance(intent, dict) else {}
    manifest = manifest if isinstance(manifest, dict) else {}
    if manifest.get("operations") or []:
        return False
    if not (manifest.get("read_only") is True or intent.get("is_state_changing") is False):
        return False
    read_only_response = manifest.get("read_only_response")
    if not isinstance(read_only_response, dict):
        return False
    return isinstance(read_only_response.get("forum_modules_found"), list)


def forum_readonly_target_from_manifest(manifest: dict, intent: dict, env: dict[str, str]) -> dict[str, str]:
    candidates: list[dict] = []
    read_only_response = manifest.get("read_only_response") if isinstance(manifest, dict) else {}
    if isinstance(read_only_response, dict):
        found = read_only_response.get("forum_modules_found")
        if isinstance(found, list):
            if not found:
                return {}
            candidates.extend(item for item in found if isinstance(item, dict))
    target = intent.get("target") if isinstance(intent, dict) else {}
    if isinstance(target, dict):
        candidates.append(target)
    for candidate in candidates:
        url = first_text(candidate.get("url"), candidate.get("forum_url"), candidate.get("target_forum_url"))
        if url:
            return {
                "forum_name": first_text(candidate.get("forum_name"), candidate.get("name"), "Course Forum"),
                "section": first_text(candidate.get("section"), candidate.get("section_name"), "General"),
                "url": url,
            }
    readback = current_course_readback_context(env)
    forums: list[dict] = []
    for section in readback.get("sections", []) if isinstance(readback, dict) else []:
        if not isinstance(section, dict):
            continue
        for module in section.get("modules", []) or []:
            if not isinstance(module, dict):
                continue
            if str(module.get("modname", "")).strip() == "forum" or "/mod/forum/" in str(module.get("url", "")):
                forums.append(
                    {
                        "forum_name": first_text(module.get("name"), "Course Forum"),
                        "section": first_text(section.get("name"), "General"),
                        "url": first_text(module.get("url")),
                    }
                )
    forums = [forum for forum in forums if forum.get("url")]
    if len(forums) == 1:
        return {key: str(value) for key, value in forums[0].items()}
    return {}


def read_forum_discussions_from_page(
    page,
    forum_url: str,
    report_dir: Path | None = None,
    *,
    moodle_env: dict[str, str] | None = None,
) -> dict:
    if not is_sandbox_moodle_page_url(forum_url):
        raise RuntimeError(f"Refusing non-sandbox forum readback URL: {forum_url}")
    goto_with_retry(page, forum_url, wait_until="domcontentloaded", timeout=60000)
    recover_moodle_login_for_target(
        page,
        forum_url,
        moodle_env,
        context="forum readback",
    )
    if report_dir is not None:
        report_dir.mkdir(parents=True, exist_ok=True)
        save_screenshot(page, report_dir, "forum_readback.png")
        write_result(report_dir, "forum_readback_page.json", page_summary(page))
    if looks_like_login(page):
        raise RuntimeError("Moodle requested login during forum readback.")
    discussions = page.evaluate(
        """
        () => {
          const normalize = (text) => String(text || '').replace(/\\s+/g, ' ').trim();
          const visible = (element) => {
            if (!element) return false;
            const style = window.getComputedStyle(element);
            return style && style.visibility !== 'hidden' && style.display !== 'none' && element.getClientRects().length > 0;
          };
          const seen = new Set();
          return Array.from(document.querySelectorAll('a[href*="/mod/forum/discuss.php?d="]'))
            .filter((anchor) => visible(anchor))
            .map((anchor) => {
              const href = anchor.href || anchor.getAttribute('href') || '';
              const title = normalize(anchor.innerText || anchor.textContent || anchor.getAttribute('title') || '');
              const discussionId = new URL(href, window.location.href).searchParams.get('d') || '';
              return {discussion_id: discussionId, title, url: href};
            })
            .filter((item) => item.discussion_id && item.title)
            .filter((item) => {
              if (seen.has(item.discussion_id)) return false;
              seen.add(item.discussion_id);
              return true;
            });
        }
        """
    )
    if not isinstance(discussions, list):
        discussions = []
    page_text = visible_text(page, 12000)
    return {
        "ok": True,
        "url": page.url,
        "discussion_count": len(discussions),
        "discussions": discussions,
        "text_sample": page_text[:1200],
    }


def forum_discussion_operation_url(operation: dict) -> str:
    url = first_text(
        operation.get("target_discussion_url"),
        operation.get("discussion_url"),
    )
    if url:
        if not is_sandbox_moodle_page_url(url):
            raise RuntimeError(
                f"Refusing non-sandbox Forum discussion readback URL: {url}"
            )
        return url
    discussion_id = first_text(
        operation.get("target_discussion_id"),
        operation.get("discussion_id"),
    )
    if discussion_id.isdigit():
        return (
            "https://migrationtest-moodle.hku.hk/mod/forum/discuss.php"
            f"?d={discussion_id}"
        )
    return ""


def read_forum_posts_from_page(
    page,
    discussion_url: str,
    report_dir: Path | None = None,
    *,
    moodle_env: dict[str, str] | None = None,
) -> dict:
    if not is_sandbox_moodle_page_url(discussion_url):
        raise RuntimeError(
            f"Refusing non-sandbox Forum discussion readback URL: {discussion_url}"
        )
    goto_with_retry(page, discussion_url, wait_until="domcontentloaded", timeout=60000)
    recover_moodle_login_for_target(
        page,
        discussion_url,
        moodle_env,
        context="Forum reply readback",
    )
    if report_dir is not None:
        report_dir.mkdir(parents=True, exist_ok=True)
        save_screenshot(page, report_dir, "forum_reply_readback.png")
        write_result(
            report_dir,
            "forum_reply_readback_page.json",
            page_summary(page),
        )
    if looks_like_login(page):
        raise RuntimeError("Moodle requested login during Forum reply readback.")
    posts = page.evaluate(
        """
        () => {
          const normalize = (text) => String(text || '').replace(/\\s+/g, ' ').trim();
          const postId = (element) => {
            const explicit = element.getAttribute('data-post-id') || element.dataset?.postId || '';
            if (/^\\d+$/.test(explicit)) return explicit;
            const ownId = element.id || '';
            const ownMatch = ownId.match(/^p(\\d+)$/);
            if (ownMatch) return ownMatch[1];
            const anchor = element.querySelector('a[href*="#p"], a[id^="p"]');
            const token = anchor?.getAttribute('href') || anchor?.id || '';
            const match = token.match(/(?:#|^)p(\\d+)/);
            return match ? match[1] : '';
          };
          const candidates = Array.from(document.querySelectorAll(
            '[data-post-id], article[id^="p"], div.forumpost[id^="p"], [id^="p"].forumpost'
          ));
          const seen = new Set();
          return candidates.map((element) => {
            const id = postId(element);
            const subjectElement = element.querySelector(
              '.forum-post-core-subject, [data-region="post-subject"], '
              + '.subject h3, .subject h4, h3.subject, h4.subject, '
              + '.subject, h3, h4'
            );
            const subject = normalize(
              subjectElement?.innerText || subjectElement?.textContent || ''
            );
            return {
              post_id: id,
              subject,
              url: id ? `${window.location.href.split('#')[0]}#p${id}` : ''
            };
          }).filter((item) => item.post_id).filter((item) => {
            if (seen.has(item.post_id)) return false;
            seen.add(item.post_id);
            return true;
          });
        }
        """
    )
    if not isinstance(posts, list):
        posts = []
    return {
        "ok": True,
        "url": page.url,
        "post_count": len(posts),
        "posts": posts,
    }


FORUM_STRUCTURE_READBACK_KIND = "forum_structure_readback"


def manifest_needs_detailed_forum_structure(manifest: dict) -> bool:
    """Honor only an explicit planner-selected Forum detail request."""
    for operation in manifest.get("operations") or []:
        if not isinstance(operation, dict):
            continue
        params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
        if params.get("require_detailed_forum_structure") is True:
            return True
        if (
            operation.get("capability") == "inspect_forum_structure"
            or operation.get("operation_kind") == FORUM_STRUCTURE_READBACK_KIND
            or operation.get("capability") == "reply_forum_post"
            or operation.get("capability") == "delete_forum_reply"
        ):
            return True
    return False


def requested_forum_names_for_readback(manifest: dict | None) -> set[str]:
    names: set[str] = set()
    for operation in (manifest or {}).get("operations") or []:
        if not isinstance(operation, dict):
            continue
        params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
        is_inspection = (
            operation.get("capability") == "inspect_forum_structure"
            or operation.get("operation_kind") == FORUM_STRUCTURE_READBACK_KIND
        )
        is_discussion_action = operation.get("capability") in {
            "reply_forum_post",
            "delete_forum_reply",
        }
        if not is_inspection and not is_discussion_action:
            continue
        values = params.get("forum_names") if is_inspection else None
        if not isinstance(values, list) and is_inspection:
            values = operation.get("forum_names")
        for value in values if isinstance(values, list) else []:
            name = str(value or "").strip()
            if name:
                names.add(name.casefold())
        if is_discussion_action:
            name = first_text(
                params.get("forum_name"),
                params.get("target_forum_name"),
                operation.get("target_forum_name"),
            )
            if name:
                names.add(name.casefold())
    return names


def detailed_forum_structure_for_planning(
    env: dict[str, str],
    moodle_page,
    *,
    run_id: str,
    manifest: dict | None = None,
) -> dict:
    if moodle_page is None:
        raise RuntimeError("Detailed Forum structure readback needs the Moodle browser session.")
    moodle_env = load_moodle_browser_env()
    moodle_env.update(env)
    ensure_moodle_authenticated(moodle_page, moodle_env)
    base = current_course_readback_context(env)
    forum_structure = base.get("forum_structure") if isinstance(base.get("forum_structure"), dict) else {}
    if forum_structure.get("status") != "ok":
        raise RuntimeError(
            f"Could not prepare Forum structure readback: {forum_structure.get('reason') or 'forum API readback unavailable'}"
        )
    requested_names = requested_forum_names_for_readback(manifest)
    modules_by_instance: dict[str, list[dict]] = {}
    for section in base.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for module in section.get("modules") or []:
            if not isinstance(module, dict) or str(module.get("modname") or "") != "forum":
                continue
            item = dict(module)
            item["section_name"] = str(section.get("name") or "")
            item["sectionnum"] = section.get("sectionnum")
            modules_by_instance.setdefault(str(module.get("instance") or ""), []).append(item)

    detailed_forums: list[dict] = []
    for forum in forum_structure.get("forums") or []:
        if not isinstance(forum, dict):
            continue
        if requested_names and str(forum.get("name") or "").strip().casefold() not in requested_names:
            continue
        instance_id = str(forum.get("id") or "")
        module_matches = modules_by_instance.get(instance_id, [])
        if len(module_matches) != 1:
            raise RuntimeError(
                f"Forum instance {instance_id or '<missing>'} did not resolve to one exact course module."
            )
        module = module_matches[0]
        forum_url = str(module.get("url") or "").strip()
        detail = read_forum_discussions_from_page(
            moodle_page,
            forum_url,
            moodle_env=moodle_env,
        )
        discussions = [
            {
                "discussion_id": str(item.get("discussion_id") or ""),
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
            }
            for item in detail.get("discussions") or []
            if isinstance(item, dict)
            and str(item.get("discussion_id") or "").isdigit()
            and str(item.get("title") or "").strip()
        ]
        detailed_forums.append(
            {
                "id": forum.get("id"),
                "course": forum.get("course"),
                "name": forum.get("name"),
                "type": forum.get("type"),
                "cmid": module.get("cmid"),
                "url": forum_url,
                "section_name": module.get("section_name"),
                "sectionnum": module.get("sectionnum"),
                "discussion_count": len(discussions),
                "discussions": discussions,
            }
        )
    canonical = json.dumps(detailed_forums, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "status": "ok",
        "detail_status": "detailed",
        "course_id": SANDBOX_COURSE_ID,
        "forums": detailed_forums,
        "snapshot_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "evidence": f"sanitized forum discussion inventory for {run_id}",
    }


FORUM_WRITE_ACTIONS = {
    "publish_announcement",
    "post_announcement",
    "publish_forum_post",
    "post_forum_message",
    "create_forum_post",
    "reply_forum_post",
    "reply_to_forum_post",
}


def manifest_has_forum_writes(manifest: dict) -> bool:
    return any(
        isinstance(operation, dict)
        and str(operation.get("action") or "").strip() in FORUM_WRITE_ACTIONS
        for operation in manifest.get("operations") or []
    )


def forum_operation_url(operation: dict) -> str:
    url = first_text(operation.get("target_forum_url"), operation.get("forum_url"))
    if url:
        if not is_sandbox_moodle_page_url(url):
            raise RuntimeError(f"Refusing non-sandbox forum readback URL: {url}")
        return url
    cmid = first_text(
        operation.get("target_forum_cmid"),
        operation.get("target_module_id"),
    )
    if cmid.isdigit():
        return f"https://migrationtest-moodle.hku.hk/mod/forum/view.php?id={cmid}"
    return ""


def _normalized_exact_subject(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def verify_forum_manifest_readback(
    run_id: str,
    manifest: dict,
    execution_state: dict,
    moodle_page,
) -> dict:
    execution_result = execution_state.get("moodle_result") or {}
    execution_results = [
        dict(item)
        for item in execution_result.get("results") or []
        if isinstance(item, dict)
    ]
    results_by_index = {
        int(item.get("course_agent_operation_index") or 0): item
        for item in execution_results
        if str(item.get("course_agent_operation_index") or "").isdigit()
    }
    checks: list[dict] = []
    failures: list[str] = []
    evidence: list[str] = []
    readback_cache: dict[str, dict] = {}
    verified_result_count = 0

    for index, operation in enumerate(manifest.get("operations") or [], start=1):
        if not isinstance(operation, dict) or str(operation.get("action") or "").strip() not in FORUM_WRITE_ACTIONS:
            continue
        action = str(operation.get("action") or "").strip()
        execution_item = results_by_index.get(index, {})
        execution_status = str(execution_item.get("status") or "")
        subject = str(operation.get("subject") or "").strip()
        target = first_text(
            execution_item.get("target_forum_name"),
            operation.get("target_forum_name"),
            operation.get("target_object"),
            "Moodle forum",
        )
        passed = False
        detail = ""

        if not (execution_item.get("ok") is True or execution_status.endswith("_verified")):
            detail = first_text(
                execution_item.get("reason"),
                execution_item.get("error"),
                execution_status,
                "Forum write was not executed or did not verify",
            )
        elif action in {"reply_forum_post", "reply_to_forum_post"}:
            discussion_url = forum_discussion_operation_url(
                {**operation, **execution_item}
            )
            if not discussion_url:
                detail = (
                    "Approved Forum reply has no exact discussion URL or "
                    "numeric discussion id"
                )
            else:
                if discussion_url not in readback_cache:
                    report_dir = (
                        REPORTS
                        / f"forum_reply_readback_{stamp()}_{safe_name(run_id)}_{index:02d}"
                    )
                    readback_cache[discussion_url] = read_forum_posts_from_page(
                        moodle_page,
                        discussion_url,
                        report_dir=report_dir,
                    )
                    evidence.append(str(report_dir))
                posts = readback_cache[discussion_url].get("posts") or []
                api_result = (
                    execution_item.get("api_result")
                    if isinstance(execution_item.get("api_result"), dict)
                    else {}
                )
                payload = (
                    api_result.get("payload")
                    if isinstance(api_result.get("payload"), dict)
                    else {}
                )
                expected_post_id = first_text(
                    execution_item.get("new_post_id"),
                    (
                        execution_item.get("details", {}).get("new_post_id")
                        if isinstance(execution_item.get("details"), dict)
                        else ""
                    ),
                    payload.get("postid"),
                )
                exact_matches = [
                    item
                    for item in posts
                    if isinstance(item, dict)
                    and str(item.get("post_id") or "").strip()
                    == expected_post_id
                ]
                subject_matches = (
                    len(exact_matches) == 1
                    and _normalized_exact_subject(
                        exact_matches[0].get("subject")
                    )
                    == _normalized_exact_subject(subject)
                )
                passed = bool(expected_post_id) and subject_matches
                detail = (
                    f"Forum reply post id {expected_post_id!r} match count is "
                    f"{len(exact_matches)} in the approved discussion; "
                    f"exact subject match is {subject_matches}"
                )
        else:
            forum_url = forum_operation_url({**operation, **execution_item})
            if not forum_url:
                detail = "Approved forum operation has no readback URL or current course-module id"
            else:
                if forum_url not in readback_cache:
                    report_dir = REPORTS / f"forum_write_readback_{stamp()}_{safe_name(run_id)}_{index:02d}"
                    readback_cache[forum_url] = read_forum_discussions_from_page(
                        moodle_page,
                        forum_url,
                        report_dir=report_dir,
                    )
                    evidence.append(str(report_dir))
                discussions = readback_cache[forum_url].get("discussions") or []
                exact_matches = [
                    item
                    for item in discussions
                    if isinstance(item, dict)
                    and _normalized_exact_subject(item.get("title")) == _normalized_exact_subject(subject)
                ]
                api_result = execution_item.get("api_result") if isinstance(execution_item.get("api_result"), dict) else {}
                payload = api_result.get("payload") if isinstance(api_result.get("payload"), dict) else {}
                expected_discussion_id = str(payload.get("discussionid") or payload.get("discussion_id") or "").strip()
                id_matches = (
                    not expected_discussion_id
                    or (
                        len(exact_matches) == 1
                        and str(exact_matches[0].get("discussion_id") or "") == expected_discussion_id
                    )
                )
                passed = len(exact_matches) == 1 and id_matches
                detail = (
                    f"Forum {target!r} exact subject {subject!r} match count is {len(exact_matches)}"
                    + (
                        f"; expected discussion id {expected_discussion_id!r}"
                        if expected_discussion_id
                        else ""
                    )
                )
        checks.append(
            {
                "operation_index": index,
                "action": operation.get("action", ""),
                "target": target,
                "subject": subject,
                "passed": passed,
                "detail": detail,
            }
        )
        if passed:
            verified_result_count += 1
            execution_item["browser_readback_verified"] = True
        else:
            failures.append(detail)
            execution_item["browser_readback_verified"] = False

    passed = bool(checks) and not failures
    saves_count = int(execution_result.get("saves_count", 0) or 0)
    return {
        "run_id": run_id,
        "pipeline_stage": "verifiers",
        "status": "PASS" if passed else "FAIL",
        "summary": (
            "Forum discussion readback matched every approved forum write."
            if passed
            else "Forum execution or exact discussion readback did not match every approved forum write."
        ),
        "target_object": str(execution_result.get("target_object") or "Moodle forum"),
        "saves_count": saves_count,
        "moodle_urls": [SANDBOX_COURSE_URL],
        "items": checks,
        "results": execution_results,
        "evidence": evidence,
        "evidence_report": execution_result.get("evidence_report", ""),
        "verification": {
            "passed": passed,
            "checks": checks,
            "failures": failures,
            "forum_operation_count": len(checks),
            "browser_verified_count": verified_result_count,
        },
        "final_moodle_state": {
            "course_url": SANDBOX_COURSE_URL,
            "forum_operation_count": len(checks),
            "browser_verified_count": verified_result_count,
        },
    }


def merge_specialist_verifier_results(results: list[dict]) -> dict:
    if len(results) == 1:
        return results[0]
    failures = [
        str(failure)
        for result in results
        for failure in (result.get("verification") or {}).get("failures") or []
        if str(failure).strip()
    ]
    statuses = {str(result.get("status") or "") for result in results}
    passed = statuses == {"PASS"}
    status = "PASS" if passed else ("FAIL" if "FAIL" in statuses else "BLOCKED")
    execution_results = next(
        (result.get("results") for result in reversed(results) if isinstance(result.get("results"), list)),
        [],
    )
    return {
        "run_id": first_text(*(result.get("run_id") for result in results)),
        "pipeline_stage": "verifiers",
        "status": status,
        "summary": (
            "All specialist Moodle readbacks matched the approved manifest."
            if passed
            else "One or more specialist Moodle readbacks did not match the approved manifest."
        ),
        "target_object": first_text(*(result.get("target_object") for result in reversed(results))),
        "saves_count": max(int(result.get("saves_count", 0) or 0) for result in results),
        "moodle_urls": list(
            dict.fromkeys(
                str(url)
                for result in results
                for url in result.get("moodle_urls") or []
                if str(url).strip()
            )
        ),
        "items": [item for result in results for item in result.get("items") or []],
        "results": execution_results,
        "evidence": list(
            dict.fromkeys(
                str(item)
                for result in results
                for item in result.get("evidence") or []
                if str(item).strip()
            )
        ),
        "evidence_report": first_text(*(result.get("evidence_report") for result in results)),
        "verification": {
            "passed": passed,
            "failures": failures,
            "specialist_results": [
                {
                    "status": result.get("status"),
                    "summary": result.get("summary"),
                    "verification": result.get("verification"),
                }
                for result in results
            ],
        },
        "final_moodle_state": {"course_url": SANDBOX_COURSE_URL},
    }


def execution_has_verified_forum_write(manifest: dict, execution_state: dict) -> bool:
    operations = manifest.get("operations") or []
    execution_result = execution_state.get("moodle_result") or {}
    for result in execution_result.get("results") or []:
        if not isinstance(result, dict):
            continue
        index = int(result.get("course_agent_operation_index") or 0)
        operation = operations[index - 1] if 0 < index <= len(operations) and isinstance(operations[index - 1], dict) else {}
        if (
            str(operation.get("action") or "").strip() in FORUM_WRITE_ACTIONS
            and (result.get("ok") is True or str(result.get("status") or "").endswith("_verified"))
        ):
            return True
    return False


def failed_forum_verification_result(run_id: str, execution_state: dict, reason: str) -> dict:
    execution_result = execution_state.get("moodle_result") or {}
    return {
        "run_id": run_id,
        "pipeline_stage": "verifiers",
        "status": "FAIL",
        "summary": reason,
        "target_object": str(execution_result.get("target_object") or "Moodle forum"),
        "saves_count": int(execution_result.get("saves_count", 0) or 0),
        "moodle_urls": [SANDBOX_COURSE_URL],
        "items": [],
        "results": execution_result.get("results") or [],
        "evidence": [],
        "evidence_report": execution_result.get("evidence_report", ""),
        "verification": {
            "passed": False,
            "readback_failed": True,
            "failures": [reason],
        },
        "final_moodle_state": {"course_url": SANDBOX_COURSE_URL},
    }


def forum_readonly_result_text(run_id: str, raw_task: str, target: dict[str, str], readback: dict) -> str:
    forum_name = first_text(target.get("forum_name"), "Course Forum")
    section = first_text(target.get("section"), "General")
    discussions = readback.get("discussions") if isinstance(readback.get("discussions"), list) else []
    count = int(readback.get("discussion_count", len(discussions)) or 0)
    if count:
        verify = f"{forum_name} currently has {count} discussion(s)."
        content_lines = ["Contents:"]
        for index, discussion in enumerate(discussions, start=1):
            if not isinstance(discussion, dict):
                continue
            title = first_text(discussion.get("title"), f"Discussion {index}")
            discussion_id = first_text(discussion.get("discussion_id"))
            suffix = f" (discussion id {discussion_id})" if discussion_id else ""
            content_lines.append(f"{index}. {title}{suffix}")
    else:
        verify = f"{forum_name} currently has 0 discussions."
        content_lines = ["Contents: No discussions found."]
    return "\n".join(
        [
            "Done. I checked Moodle and found the requested information.",
            "",
            f"Your request: {user_visible_task_text(raw_task)}",
            f"Moodle item: {forum_name} in {section}",
            "What happened: No Moodle changes were requested.",
            f"Check: {verify}",
            "",
            *content_lines,
            f"Course: {SANDBOX_COURSE_ID} sandbox",
            f"Run: {run_id}",
            f"Link: {first_text(target.get('url'), readback.get('url'), SANDBOX_COURSE_URL)}",
        ]
    )


def try_forum_readonly_result_text(
    *,
    run_id: str,
    raw_task: str,
    intent: dict,
    manifest: dict,
    planner_message: str,
    env: dict[str, str],
    moodle_page,
    moodle_available: bool,
) -> str:
    if not is_forum_readonly_query(raw_task, intent, manifest, planner_message):
        return ""
    target = forum_readonly_target_from_manifest(manifest, intent, env)
    if not target.get("url"):
        return ""
    if not moodle_available or moodle_page is None:
        return "\n".join(
            [
                "I could not complete this Moodle request.",
                "",
                f"Your request: {user_visible_task_text(raw_task)}",
                f"Moodle item: {first_text(target.get('forum_name'), 'Course Forum')}",
                "What happened: I did not make a Moodle change.",
                "Check: Forum discussion readback needs the Moodle browser session, but it is not available.",
                f"Course: {SANDBOX_COURSE_ID} sandbox",
                f"Run: {run_id}",
            ]
        )
    moodle_env = load_moodle_browser_env()
    moodle_env.update(env)
    ensure_moodle_authenticated(moodle_page, moodle_env)
    report_dir = REPORTS / f"forum_readback_{stamp()}_{safe_name(run_id)}"
    readback = read_forum_discussions_from_page(moodle_page, target["url"], report_dir=report_dir)
    write_result(report_dir, "result.json", {"target": target, "readback": readback})
    return forum_readonly_result_text(run_id, raw_task, target, readback)


def path_basename(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return Path(text).name


def approval_action_label(operation: dict) -> str:
    kind = str(operation.get("operation_kind", "")).strip()
    action = str(operation.get("action", "")).strip()
    if kind == "browser_course_file_resource_delete" or action == "delete_file_resource":
        return "Delete file"
    if kind == "browser_module_delete" or action == "delete_module":
        return "Delete item"
    if action == "delete_group":
        return "Delete group"
    if action == "delete_grouping":
        return "Delete grouping"
    if action in {"assign_group_names_to_grouping", "unassign_group_names_from_grouping"}:
        return "Update grouping"
    if kind == "browser_group_membership" or action in {
        "add_group_member",
        "remove_group_member",
        "move_group_member",
    }:
        return "Update group member"
    if kind == "browser_group_signup_activity" or action == "create_group_signup_activity":
        return "Create group sign-up"
    if kind == "browser_assignment_activity" or action == "create_assignment_activity":
        return "Create assignment"
    if action == "delete_forum_post":
        return "Delete forum message"
    if action == "delete_forum_reply":
        return "Delete forum reply"
    if action in {"reply_forum_post", "reply_to_forum_post"}:
        return "Reply to forum question"
    if action in {"publish_forum_post", "post_forum_message", "create_forum_post"}:
        return "Post forum message"
    if kind in {"browser_course_file_resource_upload", "course_file_resource_upload"} or action == "create_file_resource":
        return "Create file"
    if kind == "file_api_upload" or action == "upload_file_api":
        return "Upload file"
    if kind == "file_upload_replace" or action == "upload_or_replace_file":
        return "Replace file"
    if kind == "announcement_api":
        return "Post announcement"
    if kind == "group_api":
        return "Create group"
    if kind == "grouping_api":
        return "Create grouping"
    if kind == "direct_title_edit":
        return "Rename item"
    if kind == "browser_module_visibility" or action == "set_visibility":
        return "Set visibility"
    if kind == "browser_section_rename" or action == "rename_section":
        return "Rename section"
    if kind in {"access_time", "browser_module_access_time"}:
        if str(operation.get("access_time_action", "")).strip().lower() == "clear" or str(
            operation.get("target_value", "")
        ).strip().lower() in {"clear", "remove", "delete", "unset"}:
            return "Clear access time"
        return "Set access time"
    if kind == "hidden_marker":
        return "Create marker"
    if kind == COURSE_REFRESH_BATCH_UPLOAD_KIND:
        return "Update course files"
    return first_text(operation.get("approval_action"), "Update Moodle")


APPROVAL_ACTION_PHRASES = {
    "Delete file": "delete the file",
    "Delete item": "delete the Moodle item",
    "Create file": "create the file resource",
    "Upload file": "upload the file",
    "Replace file": "replace the file",
    "Post announcement": "post the announcement",
    "Create group": "create the group",
    "Create grouping": "create the grouping",
    "Delete group": "delete the group",
    "Delete grouping": "delete the grouping",
    "Update grouping": "update the grouping membership",
    "Create group sign-up": "create the group sign-up activity",
    "Post forum message": "post the forum message",
    "Reply to forum question": "reply to the forum question",
    "Delete forum message": "delete the forum message",
    "Rename item": "rename the Moodle item",
    "Set visibility": "set the visibility",
    "Rename section": "rename the section",
    "Set access time": "set the access time",
    "Clear access time": "clear the access time",
    "Create marker": "create the marker",
    "Update course files": "update the course files",
    "Update Moodle": "update Moodle",
}


def approval_action_phrase(action_label: str) -> str:
    return APPROVAL_ACTION_PHRASES.get(action_label, action_label[:1].lower() + action_label[1:])


def approval_item_label(operation: dict) -> str:
    env_overrides = operation.get("env_overrides") if isinstance(operation.get("env_overrides"), dict) else {}
    return first_text(
        operation.get("resource_name"),
        operation.get("target_object"),
        operation.get("approval_item"),
        operation.get("group_name"),
        operation.get("grouping_name"),
        operation.get("target_subject"),
        operation.get("subject"),
        operation.get("title"),
        operation.get("summary"),
        env_overrides.get("MOODLE_RESOURCE_NAME"),
        env_overrides.get("MOODLE_TARGET_TITLE"),
        env_overrides.get("MOODLE_TEST_OBJECT_NAME"),
        env_overrides.get("MOODLE_GROUP_NAME"),
        env_overrides.get("MOODLE_GROUPING_NAME"),
        env_overrides.get("MOODLE_ANNOUNCEMENT_SUBJECT"),
        "Moodle item",
    )


def approval_file_label(operation: dict) -> str:
    env_overrides = operation.get("env_overrides") if isinstance(operation.get("env_overrides"), dict) else {}
    explicit = first_text(
        operation.get("expected_uploaded_filename"),
        operation.get("filename"),
        env_overrides.get("MOODLE_EXPECTED_UPLOADED_FILENAME"),
    )
    if explicit:
        return explicit
    path_name = path_basename(
        operation.get("target_file_path")
        or operation.get("upload_file_path")
        or env_overrides.get("MOODLE_UPLOAD_FILE_PATH")
        or env_overrides.get("MOODLE_TARGET_FILE_PATH")
    )
    if path_name:
        return path_name
    if operation.get("operation_kind") == "browser_course_file_resource_delete":
        latest = latest_file_resource_context()
        target_id = first_text(
            operation.get("target_module_id"),
            operation.get("cmid"),
            env_overrides.get("MOODLE_TARGET_MODULE_ID"),
        )
        if latest and first_text(latest.get("target_module_id")) == target_id:
            return first_text(latest.get("expected_uploaded_filename"))
    return ""


def approval_visibility_label(operation: dict) -> str:
    env_overrides = operation.get("env_overrides") if isinstance(operation.get("env_overrides"), dict) else {}
    if operation.get("operation_kind") == "browser_course_file_resource_delete":
        return "Existing sandbox File resource"
    if operation.get("operation_kind") == COURSE_REFRESH_BATCH_UPLOAD_KIND:
        return first_text(operation.get("expected_visibility"), "Prepared manifest settings")
    visible = first_text(operation.get("visible"), operation.get("resource_visible"), env_overrides.get("MOODLE_RESOURCE_VISIBLE"))
    expected = first_text(operation.get("expected_visibility"), env_overrides.get("MOODLE_EXPECTED_VISIBILITY"))
    if expected:
        return expected.replace("Hide on course page", "Hidden").replace("Show on course page", "Visible")
    if visible.lower() in {"1", "true", "yes", "on", "visible", "show"}:
        return "Visible"
    if visible.lower() in {"0", "false", "no", "off", "hidden", "hide"}:
        return "Hidden"
    return ""


def operation_method_label(operation: dict) -> str:
    kind = str(operation.get("operation_kind", "")).strip()
    mode = str(operation.get("execution_mode", "")).strip()
    if kind.endswith("_api") or mode == "api_action":
        return "API"
    if kind == "browser_moodle_task":
        return "Moodle browser fallback"
    if kind.startswith("browser_") or "browser" in mode:
        return "Moodle browser"
    return first_text(mode, kind, "Project tool")


def approval_safety_label(operation: dict) -> str:
    kind = str(operation.get("operation_kind", "")).strip()
    action = str(operation.get("action", "")).strip().lower()
    if "delete" in kind or "delete" in action:
        return "Delete only the target shown here after OK"
    if approval_visibility_label(operation).lower() == "visible":
        return "May affect student visibility"
    return "Sandbox only; write only after OK"


def result_status_label(state_status: str, result_status: str) -> str:
    text = " ".join([state_status, result_status]).lower()
    if "verified" in text or result_status == "PASS":
        return "Done"
    if "blocked" in text or result_status == "BLOCKED":
        return "Blocked"
    if "failed" in text or result_status == "FAIL":
        return "Failed"
    if "pending" in text:
        return "Waiting"
    return first_text(result_status, state_status, "Unknown")


def result_reason_label(result: dict) -> str:
    verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
    failures = verification.get("failures") if isinstance(verification, dict) else []
    first_failure = failures[0] if isinstance(failures, list) and failures else ""
    reason = first_text(
        result.get("note"),
        result.get("blocker"),
        result.get("reason"),
        result.get("error"),
        first_failure,
        result.get("summary") if result.get("status") in {"BLOCKED", "FAIL"} else "",
    )
    if not reason:
        return ""
    return human_block_reason(reason)


def result_change_label(result: dict, status_label: str) -> str:
    saves_count = int(result.get("saves_count", 0) or 0)
    if status_label == "Done":
        if saves_count:
            noun = "change" if saves_count == 1 else "changes"
            return f"I made {saves_count} Moodle {noun} and verified the result"
        return "I checked Moodle and verified the current state"
    if saves_count:
        noun = "change" if saves_count == 1 else "changes"
        return f"I tried {saves_count} Moodle {noun}, but the final check did not pass"
    return "I did not make a verified Moodle change"


def result_verify_label(status_label: str) -> str:
    if status_label == "Done":
        return "Moodle readback passed"
    if status_label == "Blocked":
        return "I stopped before a Moodle change was verified"
    if status_label == "Failed":
        return "The final Moodle check failed"
    if "reject" in status_label.lower():
        return "No Moodle check was needed because the request was not approved"
    return "Waiting for next step"


def result_intro_label(status_label: str) -> str:
    lowered = status_label.lower()
    if status_label == "Done":
        return "Done. I finished the Moodle request and checked it."
    if status_label == "Blocked":
        return "I could not complete this Moodle request."
    if status_label == "Failed":
        return "I tried the Moodle request, but the final check failed."
    if status_label == "Waiting":
        return "I am still waiting to finish this Moodle request."
    if "reject" in lowered:
        return "This Moodle request was not approved, so I did not change Moodle."
    return f"Moodle request status: {status_label}."


def natural_blocked_result_text(run_id: str, raw_task: str, target: str, reason: str) -> str:
    lines = [
        "I could not complete this Moodle request.",
        "",
        f"Your request: {user_visible_task_text(raw_task) or 'Moodle request'}",
        f"Moodle item: {first_text(target, 'Moodle item')}",
        "What happened: I did not make a Moodle change.",
        f"Why: {human_block_reason(reason)}",
        f"Course: {SANDBOX_COURSE_ID} sandbox",
    ]
    if run_id:
        lines.append(f"Run: {run_id}")
    return "\n".join(lines)


def access_time_writes_enabled(env: dict[str, str]) -> bool:
    return env.get("COURSE_AGENT_ENABLE_ACCESS_TIME_WRITES", "0").strip().lower() in {"1", "true", "yes", "on"}


def unsupported_access_time_blocker(manifest: dict, env: dict[str, str]) -> tuple[str, str]:
    if access_time_writes_enabled(env):
        return "", ""
    for operation in manifest.get("operations", []) or []:
        if not isinstance(operation, dict):
            continue
        if operation.get("operation_kind") == "access_time" and operation.get("execution_mode") != "agent_browser_action":
            return (
                approval_item_label(operation),
                "The current HKU APIM whitelist does not expose a supported Moodle function for changing module "
                "availability or access restriction time. This needs a custom Moodle plugin or a stable approved "
                "browser tool before I can write it safely.",
            )
    return "", ""


def evidence_label(result: dict) -> str:
    evidence = first_text(result.get("evidence_report"), result.get("report_dir"))
    if not evidence and isinstance(result.get("evidence"), list) and result["evidence"]:
        evidence = first_text(result["evidence"][0])
    if evidence:
        return Path(evidence).name
    return ""


def approval_text(run_id: str, approval_code: str, raw_task: str, intent: dict, manifest: dict, planner_message: str) -> str:
    operations = manifest.get("operations") or []
    operation = operations[0] if operations and isinstance(operations[0], dict) else {}
    compact_group_batch = len(operations) > 1 and manifest_has_group_writes(manifest)
    action = approval_action_label(operation)
    target = approval_item_label(operation)
    task = user_visible_task_text(
        intent.get("original_staff_prompt"),
        intent.get("staff_prompt"),
        raw_task,
        intent.get("raw_task"),
        intent.get("interpreted_intent"),
    )
    lines = [
        "Please approve this Moodle change.",
        "",
        f"Your request: {task}",
        (
            f"What I will do: I will apply {len(operations)} ordered Group changes "
            f"in course {SANDBOX_COURSE_ID} sandbox."
            if compact_group_batch
            else f"What I will do: I will {approval_action_phrase(action)} in course {SANDBOX_COURSE_ID} sandbox."
        ),
        (
            "Moodle item: All Groups and Groupings in the current readback"
            if compact_group_batch
            else f"Moodle item: {target}"
        ),
    ]
    section = first_text(operation.get("section_name"))
    if section:
        lines.append(f"Section: {section}.")
    file_label = approval_file_label(operation)
    if file_label:
        lines.append(f"File: {file_label}.")
    visibility = approval_visibility_label(operation)
    if visibility:
        if operation.get("operation_kind") == "browser_course_file_resource_delete":
            lines.append(f"Item type: {visibility}.")
        else:
            lines.append(f"Visibility after the change: {visibility}.")
    approval_scope = compact_approval_value(operation.get("approval_scope"))
    if compact_group_batch:
        membership_updates = 0
        membership_links = 0
        deleted_groupings = 0
        deleted_groups = 0
        other_group_changes = 0
        for item in operations:
            if not isinstance(item, dict):
                continue
            item_action = str(item.get("action") or "").strip()
            if item_action in {
                "assign_group_names_to_grouping",
                "unassign_group_names_from_grouping",
            }:
                membership_updates += 1
                group_names = item.get("group_names") or []
                membership_links += len(group_names) if isinstance(group_names, list) else 1
            elif item_action == "delete_grouping":
                deleted_groupings += 1
            elif item_action == "delete_group":
                deleted_groups += 1
            else:
                other_group_changes += 1
        lines.append(f"This approval covers: {len(operations)} ordered Moodle Group changes.")
        if membership_updates:
            lines.append(
                f"- Update {membership_updates} Grouping memberships "
                f"({membership_links} explicit Group links)."
            )
        if deleted_groupings:
            lines.append(f"- Delete all {deleted_groupings} current Groupings.")
        if deleted_groups:
            lines.append(f"- Delete all {deleted_groups} current empty Groups.")
        if other_group_changes:
            lines.append(f"- Apply {other_group_changes} other explicit Group changes.")
    elif approval_scope:
        lines.append(f"This approval covers: {approval_scope}.")
    elif len(operations) > 1:
        lines.append(f"This approval covers: {len(operations)} Moodle changes.")
    if len(operations) > 1 and not compact_group_batch:
        lines.append("Ordered items:")
        for index, item in enumerate(operations, start=1):
            if not isinstance(item, dict):
                continue
            item_action = approval_action_phrase(approval_action_label(item))
            item_target = approval_item_label(item)
            lines.append(f"- {index}. {item_action}: {item_target}.")
        methods = list(
            dict.fromkeys(
                operation_method_label(item)
                for item in operations
                if isinstance(item, dict)
            )
        )
        method_label = " + ".join(methods)
    else:
        method_label = operation_method_label(operation)
    lines.extend(
        [
            f"How I will do it: {method_label}.",
            "How I will check it: I will read Moodle again after the change.",
            (
                "Safety note: Exact targets come from the current readback; stop if "
                "members, links, dependencies, or target inventory changed."
                if compact_group_batch
                else f"Safety note: {approval_safety_label(operation)}."
            ),
            "",
            "Reply with one of these:",
            f"OK {approval_code}",
            f"NO {approval_code}",
        ]
    )
    return "\n".join(lines)


def result_text(status: dict) -> str:
    result = status.get("course_agent_result") or status.get("moodle_result") or {}
    state_status = status.get("status", "")
    result_status = str(result.get("status", state_status))
    status_label = result_status_label(state_status, result_status)
    intent = status.get("course_task_intent") or {}
    manifest = status.get("course_operation_manifest") or {}
    operations = manifest.get("operations") or []
    operation = operations[0] if operations and isinstance(operations[0], dict) else {}
    url = (
        result.get("display_url")
        or result.get("course_verify_url")
        or result.get("target_course_url")
        or (
            result.get("moodle_urls", [""])[0]
            if isinstance(result.get("moodle_urls"), list) and result.get("moodle_urls")
            else ""
        )
        or SANDBOX_COURSE_URL
    )
    lines = [
        result_intro_label(status_label),
        "",
        f"Your request: {user_visible_task_text(intent.get('original_staff_prompt'), intent.get('staff_prompt'), intent.get('raw_task'), intent.get('interpreted_intent'), operation.get('summary'))}",
        f"Moodle item: {first_text(result.get('target_object'), approval_item_label(operation))}",
        f"What happened: {result_change_label(result, status_label)}.",
        f"Check: {result_verify_label(status_label)}.",
        f"Course: {SANDBOX_COURSE_ID} sandbox",
    ]
    reason = result_reason_label(result)
    if reason:
        lines.append(f"Why: {reason}")
    item_results = result.get("results") if isinstance(result.get("results"), list) else []
    if len(item_results) > 1:
        lines.append("Items:")
        for index, item_result in enumerate(item_results, start=1):
            if not isinstance(item_result, dict):
                continue
            item_status = str(item_result.get("status") or "").casefold()
            if item_result.get("ok") is True or item_status.endswith("_verified"):
                item_label = "Done"
            elif item_status.endswith("_skipped"):
                item_label = "Not run"
            elif item_status.endswith("_blocked"):
                item_label = "Blocked"
            else:
                item_label = "Failed"
            item_target = first_text(item_result.get("target_object"), f"Moodle item {index}")
            lines.append(f"- {index}. {item_label}: {item_target}")
    evidence = evidence_label(result)
    if evidence:
        lines.append(f"Evidence: {evidence}")
    if status.get("run_id", ""):
        lines.append(f"Run: {status.get('run_id', '')}")
    lines.append(f"Link: {url}")
    return "\n".join(lines)


def moodle_browser_proxy_bypass_list(env: dict[str, str]) -> str:
    raw = str(
        env.get("MOODLE_BROWSER_PROXY_BYPASS")
        or "migrationtest-moodle.hku.hk;apidev.hku.hk"
    )
    hosts: list[str] = []
    for value in re.split(r"[,;]", raw):
        host = value.strip()
        if host and re.fullmatch(r"[A-Za-z0-9.-]+", host) and host not in hosts:
            hosts.append(host)
    return ";".join(hosts)


def launch_moodle_context(playwright, env: dict[str, str]):
    profile_dir = Path(env["MOODLE_BROWSER_PROFILE_DIR"]).expanduser()
    if not profile_dir.is_absolute():
        profile_dir = ROOT / profile_dir
    profile_dir.mkdir(parents=True, exist_ok=True)
    chrome_path = env.get("MOODLE_BROWSER_CHROME_PATH", DEFAULT_CHROME_PATH)
    launch_kwargs: dict = {
        "user_data_dir": str(profile_dir),
        "headless": False,
        "viewport": {"width": 1400, "height": 900},
    }
    proxy_bypass = moodle_browser_proxy_bypass_list(env)
    if proxy_bypass:
        launch_kwargs["args"] = [f"--proxy-bypass-list={proxy_bypass}"]
    if chrome_path and Path(chrome_path).exists():
        launch_kwargs["executable_path"] = chrome_path
    context = playwright.chromium.launch_persistent_context(**launch_kwargs)
    page = context.pages[0] if context.pages else context.new_page()
    return context, page


def read_macos_keychain_secret(service: str, account: str) -> str:
    if not service or not account:
        return ""
    try:
        completed = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-w",
                "-s",
                service,
                "-a",
                account,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.rstrip("\r\n")


def moodle_login_credentials(env: dict[str, str]) -> tuple[str, str]:
    account = str(env.get("MOODLE_LOGIN_KEYCHAIN_ACCOUNT") or MOODLE_KEYCHAIN_ACCOUNT).strip()
    username = str(env.get("MOODLE_LOGIN_USERNAME") or "").strip()
    password = str(env.get("MOODLE_LOGIN_PASSWORD") or "")
    if not username:
        username = read_macos_keychain_secret(
            str(
                env.get("MOODLE_LOGIN_USERNAME_KEYCHAIN_SERVICE")
                or MOODLE_USERNAME_KEYCHAIN_SERVICE
            ).strip(),
            account,
        ).strip()
    if not password:
        password = read_macos_keychain_secret(
            str(
                env.get("MOODLE_LOGIN_PASSWORD_KEYCHAIN_SERVICE")
                or MOODLE_PASSWORD_KEYCHAIN_SERVICE
            ).strip(),
            account,
        )
    return username, password


def ensure_moodle_authenticated(
    page,
    env: dict[str, str],
    *,
    target_url: str = SANDBOX_COURSE_URL,
) -> bool:
    """Log in to the sandbox when needed, without exposing stored credentials."""
    if not is_sandbox_moodle_page_url(target_url):
        raise RuntimeError("Refusing automatic Moodle login for a non-sandbox target.")
    if str(getattr(page, "url", "") or "") != target_url:
        goto_with_retry(page, target_url, wait_until="domcontentloaded", timeout=60000)
    if not looks_like_login(page):
        return False
    current = urlparse(str(getattr(page, "url", "") or ""))
    if current.hostname != "migrationtest-moodle.hku.hk":
        raise RuntimeError("Refusing automatic Moodle login outside the sandbox host.")

    username, password = moodle_login_credentials(env)
    if not username or not password:
        raise RuntimeError("Moodle is signed out and automatic login credentials are unavailable.")

    username_input = page.locator("#username")
    password_input = page.locator("#password")
    submit_button = page.locator("#loginbtn")
    if username_input.count() != 1 or password_input.count() != 1 or submit_button.count() != 1:
        raise RuntimeError("The sandbox Moodle login form is unavailable for automatic login.")

    username_input.fill(username)
    password_input.fill(password)
    submit_button.click()
    try:
        page.wait_for_load_state("domcontentloaded", timeout=30000)
    except Exception:
        pass
    if looks_like_login(page):
        raise RuntimeError("Automatic Moodle login failed; the sandbox is still signed out.")

    if str(getattr(page, "url", "") or "") != target_url:
        goto_with_retry(page, target_url, wait_until="domcontentloaded", timeout=60000)
    if looks_like_login(page):
        raise RuntimeError("Automatic Moodle login did not persist on the sandbox target.")
    return True


def recover_moodle_login_for_target(
    page,
    target_url: str,
    env: dict[str, str] | None = None,
    *,
    context: str = "Moodle page",
) -> bool:
    """Recover one expired sandbox session and return to the exact target."""
    if not looks_like_login(page):
        return False
    if not is_sandbox_moodle_page_url(target_url):
        raise RuntimeError(
            f"Refusing automatic Moodle login recovery for non-sandbox {context}."
        )
    login_env = load_moodle_browser_env()
    if isinstance(env, dict):
        login_env.update(env)
    ensure_moodle_authenticated(page, login_env, target_url=target_url)
    if looks_like_login(page):
        raise RuntimeError(
            f"Automatic Moodle login recovery did not restore the sandbox {context}."
        )
    return True


def prepare_manifest_for_local_jobs(env: dict[str, str], run_id: str, raw_task: str, manifest: dict) -> dict:
    operations = manifest.get("operations") or []
    prepared_operations = []
    changed = False
    for operation in operations:
        if isinstance(operation, dict) and is_course_refresh_operation(operation):
            prepared_operations.append(prepare_course_refresh_operation(operation, env, run_id, raw_task))
            changed = True
        elif isinstance(operation, dict) and is_refresh_file_batch_preview_operation(operation):
            selected = {
                resolved_path_key(path): path
                for path in batch_file_paths(operation)
                if resolved_path_key(path)
            }
            ordered_current = [
                selected[resolved_path_key(path)]
                for path in current_prompt_local_paths(raw_task)
                if resolved_path_key(path) in selected
            ]
            if ordered_current:
                prepared = dict(operation)
                params = dict(prepared.get("params") or {})
                params["file_paths"] = ordered_current
                prepared["params"] = params
                prepared["file_paths"] = ordered_current
                prepared_operations.append(prepared)
                changed = changed or prepared != operation
            else:
                prepared_operations.append(operation)
        else:
            prepared_operations.append(operation)
    if not changed:
        return manifest
    prepared = dict(manifest)
    prepared["operations"] = prepared_operations
    return apply_manifest_defaults(prepared)


def operation_id_for_manifest_operation(operation: dict, run_id: str, index: int) -> str:
    env_overrides = operation.get("env_overrides") if isinstance(operation.get("env_overrides"), dict) else {}
    return str(
        operation.get("operation_id")
        or env_overrides.get("MOODLE_OPERATION_ID")
        or f"COURSE-AGENT-{run_id}-{index:02d}"
    )


def section_fields_from_operation(operation: dict) -> dict:
    params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
    candidates = [
        operation,
        params.get("target_section") if isinstance(params.get("target_section"), dict) else None,
        params.get("section") if isinstance(params.get("section"), dict) else None,
    ]
    fields: dict = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        name = first_text(candidate.get("section_name"), candidate.get("name"), candidate.get("title"), candidate.get("current_title"))
        sectionnum = first_text(candidate.get("sectionnum"), candidate.get("section_number"), candidate.get("section"))
        section_id = first_text(candidate.get("section_id"), candidate.get("id"), candidate.get("sectionid"))
        if name and not fields.get("section_name"):
            fields["section_name"] = name
        if sectionnum and not fields.get("sectionnum"):
            fields["sectionnum"] = str(sectionnum)
        if section_id and not fields.get("section_id"):
            fields["section_id"] = section_id
    section_ref = first_text(params.get("target_section"), params.get("section"), params.get("section_name"))
    if section_ref and not isinstance(section_ref, dict) and not fields.get("section_name") and not str(section_ref).isdigit():
        fields["section_name"] = str(section_ref)
    if section_ref and not isinstance(section_ref, dict) and not fields.get("sectionnum") and str(section_ref).isdigit():
        fields["sectionnum"] = str(section_ref)
    return fields


def local_result_module_context(materialized: dict, result: dict) -> dict:
    display_url = first_text(result.get("display_url"), result.get("target_resource_url"), result.get("target_url"))
    cmid = first_text(result.get("target_module_id"), result.get("cmid"), cmid_from_moodle_module_url(display_url))
    title = first_text(
        result.get("target_object"),
        result.get("resource_name"),
        materialized.get("target_object"),
        materialized.get("title"),
        materialized.get("name"),
    )
    if not cmid or not title:
        return {}
    context = {
        "target_module_id": cmid,
        "cmid": cmid,
        "target_object": title,
        "title": title,
        "resource_name": title,
        "display_url": display_url,
        "section_name": first_text(materialized.get("section_name"), result.get("section_name")),
        "sectionnum": first_text(materialized.get("sectionnum"), result.get("sectionnum")),
        "modname": first_text(materialized.get("modname"), result.get("modname")),
        "target_forum_id": first_text(result.get("target_forum_id"), result.get("forum_id")),
        "target_forum_url": first_text(result.get("target_forum_url"), result.get("display_url")),
    }
    return {key: value for key, value in context.items() if value not in ("", None)}


def runtime_reference_keys(materialized: dict, context: dict) -> list[str]:
    del context
    params = materialized.get("params") if isinstance(materialized.get("params"), dict) else {}
    keys: list[str] = []
    for key in (
        "post_create_reference",
        "result_reference",
        "created_reference",
        "reference",
        "ref",
    ):
        value = first_text(materialized.get(key), params.get(key))
        if value:
            keys.append(value)
    seen: set[str] = set()
    result: list[str] = []
    for key in keys:
        normalized = str(key or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def remember_runtime_reference(runtime_refs: dict[str, dict], materialized: dict, result: dict) -> None:
    if not result.get("ok"):
        return
    context = local_result_module_context(materialized, result)
    if not context:
        return
    resolved_ref = first_text(materialized.get("resolved_runtime_reference"))
    if resolved_ref:
        runtime_refs[resolved_ref] = dict(context)
    for key in runtime_reference_keys(materialized, context):
        runtime_refs[key] = dict(context)


RUNTIME_REFERENCE_CONSUMER_KINDS = {
    "announcement_api",
    "browser_module_access_time",
    "browser_module_visibility",
    "direct_title_edit",
}


def operation_target_ref(materialized: dict) -> str:
    if str(materialized.get("operation_kind") or "") not in RUNTIME_REFERENCE_CONSUMER_KINDS:
        return ""
    params = materialized.get("params") if isinstance(materialized.get("params"), dict) else {}
    return first_text(
        materialized.get("target_ref"),
        materialized.get("ref"),
        params.get("target_ref"),
        params.get("ref"),
    )


def apply_runtime_reference(materialized: dict, runtime_refs: dict[str, dict]) -> dict:
    target_ref = operation_target_ref(materialized)
    if not target_ref:
        return materialized
    context = runtime_refs.get(target_ref)
    if not context:
        return materialized
    cmid = first_text(context.get("target_module_id"), context.get("cmid"))
    if not cmid:
        return materialized
    resolved = dict(materialized)
    params = dict(resolved.get("params") or {})
    env_overrides = dict(resolved.get("env_overrides") or {})
    resolved["target_module_id"] = cmid
    resolved.setdefault("cmid", cmid)
    params["target_module_id"] = cmid
    env_overrides["MOODLE_TARGET_MODULE_ID"] = cmid
    current_title = first_text(
        context.get("target_object"),
        context.get("title"),
        resolved.get("target_object"),
    )
    if current_title:
        resolved["target_object"] = current_title
        resolved["approval_item"] = current_title
        env_overrides["MOODLE_TEST_OBJECT_NAME"] = current_title
        if resolved.get("operation_kind") == "announcement_api":
            forum_id = first_text(context.get("target_forum_id"))
            if forum_id:
                resolved["target_forum_id"] = forum_id
                resolved["target_forum_name"] = current_title
                params["target_forum_id"] = forum_id
                params["forum_name"] = current_title
                env_overrides["MOODLE_TARGET_FORUM_ID"] = forum_id
                env_overrides["MOODLE_TARGET_FORUM_NAME"] = current_title
                if context.get("target_forum_url"):
                    resolved["target_forum_url"] = context["target_forum_url"]
        elif resolved.get("operation_kind") == "direct_title_edit":
            desired_title = first_text(
                resolved.get("target_title"),
                resolved.get("new_title"),
                params.get("new_title"),
            )
            resolved["original_title"] = current_title
            params["target_title"] = current_title
            env_overrides["MOODLE_ORIGINAL_TITLE"] = current_title
            env_overrides["MOODLE_TARGET_TITLE"] = desired_title
        else:
            params["target_title"] = current_title
            env_overrides["MOODLE_TARGET_TITLE"] = current_title
    resolved["params"] = params
    resolved["env_overrides"] = env_overrides
    resolved["resolved_runtime_reference"] = target_ref
    return resolved


def materialize_manifest_operation(operation: dict, env: dict[str, str], run_id: str, index: int) -> dict:
    env_overrides = operation.get("env_overrides") if isinstance(operation.get("env_overrides"), dict) else {}
    materialized = dict(operation)
    materialized["batch_id"] = env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")
    materialized["approval_run_id"] = run_id
    materialized["operation_id"] = operation_id_for_manifest_operation(operation, run_id, index)
    materialized["target_course_id"] = SANDBOX_COURSE_ID
    materialized["target_course_url"] = SANDBOX_COURSE_URL
    materialized.setdefault("action", env_overrides.get("MOODLE_ACTION", ""))
    materialized.setdefault("target_module_id", env_overrides.get("MOODLE_TARGET_MODULE_ID", ""))
    materialized.setdefault("resource_name", env_overrides.get("MOODLE_RESOURCE_NAME", ""))
    materialized.setdefault("target_object", materialized.get("resource_name", ""))
    materialized.setdefault("target_resource_url", env_overrides.get("MOODLE_RESOURCE_URL", ""))
    section_fields = section_fields_from_operation(operation)
    if section_fields.get("section_name"):
        materialized["section_name"] = section_fields["section_name"]
        env_overrides["MOODLE_SECTION_NAME"] = str(section_fields["section_name"])
    if section_fields.get("sectionnum"):
        materialized["sectionnum"] = str(section_fields["sectionnum"])
        env_overrides["MOODLE_SECTION_NUM"] = str(section_fields["sectionnum"])
    if section_fields.get("section_id"):
        materialized["section_id"] = section_fields["section_id"]
        env_overrides["MOODLE_SECTION_ID"] = str(section_fields["section_id"])
    if env_overrides:
        materialized["env_overrides"] = env_overrides
    if materialized.get("operation_kind") == "browser_course_file_resource_delete":
        materialized["action"] = "delete_file_resource"
    if materialized.get("operation_kind") == "browser_course_file_resource_upload":
        materialized.setdefault("action", "create_file_resource")
    if materialized.get("operation_kind") == "browser_module_delete":
        materialized.setdefault("action", "delete_module")
    if materialized.get("operation_kind") == "browser_group_signup_activity":
        materialized.setdefault("action", "create_group_signup_activity")
    if materialized.get("operation_kind") == "browser_group_membership":
        materialized.setdefault(
            "action",
            f"{str(materialized.get('membership_action') or '').strip()}_group_member",
        )
    if materialized.get("operation_kind") == "browser_assignment_activity":
        materialized.setdefault("action", "create_assignment_activity")
    if materialized.get("operation_kind") == "browser_forum_activity":
        materialized.setdefault("action", "create_forum_activity")
    if materialized.get("operation_kind") == "browser_forum_post_reply":
        materialized.setdefault("action", "reply_forum_post")
    if materialized.get("operation_kind") == "browser_forum_post_delete":
        materialized.setdefault("action", "delete_forum_post")
    if materialized.get("operation_kind") == "browser_forum_reply_delete":
        materialized.setdefault("action", "delete_forum_reply")
    if materialized.get("operation_kind") == "browser_moodle_task":
        materialized.setdefault("action", "browser_moodle_task")
    if materialized.get("operation_kind") == "browser_module_visibility":
        materialized.setdefault("action", "set_visibility")
    if materialized.get("operation_kind") == "browser_module_access_time":
        materialized.setdefault("action", "set_access_restriction_time")
    if materialized.get("operation_kind") == "browser_section_rename":
        materialized.setdefault("action", "rename_section")
    if materialized.get("operation_kind") == COURSE_REFRESH_BATCH_UPLOAD_KIND:
        materialized.setdefault("action", "course_refresh_batch_upload")
    return materialized


LOCAL_BROWSER_OPERATION_KINDS = {
    "browser_course_file_resource_upload",
    "browser_course_file_resource_delete",
    "browser_module_delete",
    "browser_module_visibility",
    "browser_module_access_time",
    "direct_title_edit",
    "browser_section_rename",
    "browser_group_membership",
    "browser_group_signup_activity",
    "browser_assignment_activity",
    "browser_forum_activity",
    "browser_forum_post_reply",
    "browser_forum_post_delete",
    "browser_forum_reply_delete",
    "browser_moodle_task",
    COURSE_REFRESH_BATCH_UPLOAD_KIND,
}


def local_browser_operations(manifest: dict) -> list[tuple[int, dict]]:
    operations: list[tuple[int, dict]] = []
    for index, operation in enumerate(manifest.get("operations", []) or [], start=1):
        if isinstance(operation, dict) and operation.get("operation_kind") in LOCAL_BROWSER_OPERATION_KINDS:
            operations.append((index, operation))
    return operations


def approved_continuation_operations(manifest: dict, state: dict) -> list[tuple[int, dict]]:
    prior = state.get("moodle_result") if isinstance(state.get("moodle_result"), dict) else {}
    pending_index = 0
    for item in prior.get("results") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "") != "moodle_agent_browser_action_pending":
            continue
        value = str(item.get("course_agent_operation_index") or "").strip()
        if value.isdigit():
            pending_index = int(value)
            break
    if pending_index <= 0:
        return []
    return [
        (index, operation)
        for index, operation in enumerate(manifest.get("operations") or [], start=1)
        if index >= pending_index and isinstance(operation, dict)
    ]


def local_refresh_preview_operations(manifest: dict) -> list[tuple[int, dict]]:
    operations: list[tuple[int, dict]] = []
    for index, operation in enumerate(manifest.get("operations", []) or [], start=1):
        if isinstance(operation, dict) and is_refresh_file_preview_operation(operation):
            operations.append((index, operation))
    return operations


def local_refresh_batch_preview_operations(manifest: dict) -> list[tuple[int, dict]]:
    operations: list[tuple[int, dict]] = []
    for index, operation in enumerate(manifest.get("operations", []) or [], start=1):
        if isinstance(operation, dict) and is_refresh_file_batch_preview_operation(operation):
            operations.append((index, operation))
    return operations


def local_finalize_file_batch_preview_operations(manifest: dict) -> list[tuple[int, dict]]:
    operations: list[tuple[int, dict]] = []
    for index, operation in enumerate(manifest.get("operations", []) or [], start=1):
        if isinstance(operation, dict) and is_finalize_file_batch_preview_operation(operation):
            operations.append((index, operation))
    return operations


def refresh_preview_source_paths_for_reference_collection(manifest: dict, raw_task: str) -> set[str]:
    paths: set[str] = set()
    for _index, operation in local_refresh_preview_operations(manifest):
        params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
        source = first_text(
            operation.get("source_file_path"),
            operation.get("file_path"),
            params.get("file_path"),
        )
        if source and file_source_provenance(source, raw_task, allow_active_review_upload=False):
            paths.add(source)
    for _index, operation in local_refresh_batch_preview_operations(manifest):
        for source in batch_file_paths(operation):
            if file_source_provenance(source, raw_task, allow_active_review_upload=False):
                paths.add(source)
    return paths


def local_reference_pack_rebuild_operations(manifest: dict) -> list[tuple[int, dict]]:
    operations: list[tuple[int, dict]] = []
    for index, operation in enumerate(manifest.get("operations", []) or [], start=1):
        if isinstance(operation, dict) and is_rebuild_reference_pack_operation(operation):
            operations.append((index, operation))
    return operations


def local_semester_schedule_update_operations(manifest: dict) -> list[tuple[int, dict]]:
    operations: list[tuple[int, dict]] = []
    for index, operation in enumerate(manifest.get("operations", []) or [], start=1):
        if isinstance(operation, dict) and is_update_semester_schedule_operation(operation):
            operations.append((index, operation))
    return operations


def local_timeline_draft_revision_operations(manifest: dict) -> list[tuple[int, dict]]:
    operations: list[tuple[int, dict]] = []
    for index, operation in enumerate(manifest.get("operations", []) or [], start=1):
        if isinstance(operation, dict) and is_revise_timeline_draft_operation(operation):
            operations.append((index, operation))
    return operations


def local_timeline_draft_commit_operations(manifest: dict) -> list[tuple[int, dict]]:
    operations: list[tuple[int, dict]] = []
    for index, operation in enumerate(manifest.get("operations", []) or [], start=1):
        if isinstance(operation, dict) and is_commit_timeline_draft_operation(operation):
            operations.append((index, operation))
    return operations


def manifest_is_only_refresh_preview(manifest: dict) -> bool:
    operations = manifest.get("operations") or []
    return bool(operations) and all(isinstance(operation, dict) and is_refresh_file_preview_operation(operation) for operation in operations)


def manifest_is_only_refresh_batch_preview(manifest: dict) -> bool:
    operations = manifest.get("operations") or []
    return bool(operations) and all(
        isinstance(operation, dict) and is_refresh_file_batch_preview_operation(operation)
        for operation in operations
    )


def manifest_is_only_finalize_file_batch_preview(manifest: dict) -> bool:
    operations = manifest.get("operations") or []
    return bool(operations) and all(
        isinstance(operation, dict) and is_finalize_file_batch_preview_operation(operation)
        for operation in operations
    )


def manifest_has_refresh_preview(manifest: dict) -> bool:
    return bool(local_refresh_preview_operations(manifest))


def manifest_has_refresh_batch_preview(manifest: dict) -> bool:
    return bool(local_refresh_batch_preview_operations(manifest))


def manifest_has_finalize_file_batch_preview(manifest: dict) -> bool:
    return bool(local_finalize_file_batch_preview_operations(manifest))


def manifest_is_only_reference_pack_rebuild(manifest: dict) -> bool:
    operations = manifest.get("operations") or []
    return bool(operations) and all(isinstance(operation, dict) and is_rebuild_reference_pack_operation(operation) for operation in operations)


def manifest_has_reference_pack_rebuild(manifest: dict) -> bool:
    return bool(local_reference_pack_rebuild_operations(manifest))


def manifest_is_only_semester_schedule_update(manifest: dict) -> bool:
    operations = manifest.get("operations") or []
    return bool(operations) and all(isinstance(operation, dict) and is_update_semester_schedule_operation(operation) for operation in operations)


def manifest_has_semester_schedule_update(manifest: dict) -> bool:
    return bool(local_semester_schedule_update_operations(manifest))


def manifest_is_only_timeline_draft_revision(manifest: dict) -> bool:
    operations = manifest.get("operations") or []
    return bool(operations) and all(
        isinstance(operation, dict) and is_revise_timeline_draft_operation(operation)
        for operation in operations
    )


def manifest_is_only_timeline_draft_commit(manifest: dict) -> bool:
    operations = manifest.get("operations") or []
    return bool(operations) and all(
        isinstance(operation, dict) and is_commit_timeline_draft_operation(operation)
        for operation in operations
    )


def manifest_has_timeline_draft_action(manifest: dict) -> bool:
    return bool(
        local_timeline_draft_revision_operations(manifest)
        or local_timeline_draft_commit_operations(manifest)
    )


def manifest_is_only_local_file_preparation(manifest: dict) -> bool:
    operations = manifest.get("operations") or []
    if not operations:
        return False
    return all(
        isinstance(operation, dict)
        and (
            is_refresh_file_preview_operation(operation)
            or is_refresh_file_batch_preview_operation(operation)
            or is_finalize_file_batch_preview_operation(operation)
            or is_rebuild_reference_pack_operation(operation)
            or is_update_semester_schedule_operation(operation)
            or is_revise_timeline_draft_operation(operation)
            or is_commit_timeline_draft_operation(operation)
        )
        for operation in operations
    )


def manifest_is_reference_rebuild_then_refresh_preview(manifest: dict) -> bool:
    operations = manifest.get("operations") or []
    return (
        len(operations) == 2
        and isinstance(operations[0], dict)
        and isinstance(operations[1], dict)
        and is_rebuild_reference_pack_operation(operations[0])
        and is_refresh_file_preview_operation(operations[1])
    )


def manifest_is_schedule_update_then_refresh_preview(manifest: dict) -> bool:
    operations = manifest.get("operations") or []
    return (
        len(operations) == 2
        and isinstance(operations[0], dict)
        and isinstance(operations[1], dict)
        and is_update_semester_schedule_operation(operations[0])
        and is_refresh_file_preview_operation(operations[1])
    )


REFERENCE_PACK_INPUT_KEYS = (
    "reference_files",
    "reference_file_paths",
    "build_report_path",
    "timeline_report_path",
    "calendar_report_path",
    "calendar_grounding_report_path",
    "timeline_reference_path",
    "calendar_reference_path",
    "syllabus_reference_path",
    "instructions_path",
)


def reference_pack_operation_has_inputs(operation: dict) -> bool:
    params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
    for key in REFERENCE_PACK_INPUT_KEYS:
        for container in (operation, params):
            value = container.get(key)
            if isinstance(value, (list, tuple)) and any(str(item or "").strip() for item in value):
                return True
            if isinstance(value, dict) and any(str(item or "").strip() for item in value.values()):
                return True
            if isinstance(value, str) and value.strip():
                return True
    return False


def manifest_needs_reference_pack_attachments(manifest: dict, env: dict[str, str] | None = None, raw_task: str = "") -> bool:
    rebuild_operations = local_reference_pack_rebuild_operations(manifest)
    if len(rebuild_operations) == 1:
        _index, operation = rebuild_operations[0]
        return not reference_pack_operation_has_inputs(operation)
    if env is not None and manifest_is_only_refresh_preview(manifest):
        preview_operations = local_refresh_preview_operations(manifest)
        if len(preview_operations) != 1:
            return False
        _index, operation = preview_operations[0]
        status = refresh_preview_reference_pack_status(env, operation)
        if bool(status.get("ready")):
            return False
        return not reference_pack_operation_has_inputs(operation)
    if env is not None and manifest_is_only_refresh_batch_preview(manifest):
        preview_operations = local_refresh_batch_preview_operations(manifest)
        if len(preview_operations) != 1:
            return False
        _index, operation = preview_operations[0]
        status = refresh_preview_reference_pack_status(env, operation)
        if bool(status.get("ready")):
            return False
        return not reference_pack_operation_has_inputs(operation)
    return False


def refresh_preview_source_blocker(manifest: dict, raw_task: str) -> str:
    if not manifest_has_refresh_preview(manifest):
        return ""
    blocked: list[str] = []
    for _index, operation in local_refresh_preview_operations(manifest):
        provenance = refresh_preview_source_provenance(operation, raw_task)
        if provenance:
            continue
        source_name = first_text(
            operation.get("source_file_path"),
            operation.get("file_path"),
            (operation.get("params") or {}).get("file_path") if isinstance(operation.get("params"), dict) else "",
        )
        blocked.append(Path(source_name).name if source_name else "the source file")
    if not blocked:
        return ""
    return (
        "I need the file to refresh as a current WhatsApp document attachment or the active editable reviewed file from this refresh session. "
        "I will not use the preview PDF, old local files, older review outputs, or old Moodle uploads as the refresh source."
    )


def refresh_batch_preview_source_blocker(manifest: dict, raw_task: str) -> str:
    if not manifest_has_refresh_batch_preview(manifest):
        return ""
    blocked: list[str] = []
    for _index, operation in local_refresh_batch_preview_operations(manifest):
        for source in batch_file_paths(operation):
            if file_source_provenance(source, raw_task, allow_active_review_upload=False):
                continue
            blocked.append(Path(source).name if source else "the source file")
    if not blocked:
        return ""
    return (
        "I need every batch source to be a current WhatsApp document attachment or an editable item "
        "from your active multi-file review. I stopped the whole batch before reading any unverified path."
    )


def semester_schedule_file_paths(operation: dict) -> list[str]:
    params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
    raw = params.get("schedule_files") or params.get("schedule_file_paths") or operation.get("schedule_files") or []
    paths: list[str] = []
    if isinstance(raw, (str, Path)):
        raw = [str(raw)]
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                path_text = first_text(item.get("local_path"), item.get("path"), item.get("file_path"))
            else:
                path_text = str(item or "").strip()
            if path_text:
                paths.append(path_text)
    for key in ("schedule_file_path", "file_path", "path"):
        path_text = first_text(params.get(key), operation.get(key))
        if path_text:
            paths.append(path_text)
    return [path for index, path in enumerate(paths) if path and path not in paths[:index]]


def semester_schedule_source_blocker(manifest: dict, raw_task: str) -> str:
    if not manifest_has_semester_schedule_update(manifest):
        return ""
    current_keys = current_prompt_local_path_keys(raw_task)
    blocked: list[str] = []
    for _index, operation in local_semester_schedule_update_operations(manifest):
        for path_text in semester_schedule_file_paths(operation):
            if resolved_path_key(path_text) not in current_keys:
                blocked.append(Path(path_text).name if path_text else "the schedule file")
    if not blocked:
        return ""
    return (
        "I need schedule files to come from the current WhatsApp prompt's document attachments. "
        "I will not use historical attachments, old local files, or guessed paths to update semester_schedule.json."
    )


def execute_refresh_preview_manifest(
    env: dict[str, str],
    run_id: str,
    raw_task: str,
    manifest: dict,
    *,
    detailed_group_structure: dict | None = None,
) -> dict:
    preview_operations = local_refresh_preview_operations(manifest)
    if len(preview_operations) != 1:
        return {
            "ok": False,
            "status": "BLOCKED",
            "operation_kind": REFRESH_FILE_PREVIEW_KIND,
            "run_id": run_id,
            "blocker": "A single-file PDF review refresh requires exactly one source file per prompt.",
        }
    _index, operation = preview_operations[0]
    operation = dict(operation)
    operation["planner_context"] = {
        "course_readback": current_course_readback_context(
            env,
            detailed_group_structure=detailed_group_structure,
        ),
        "historical_evidence": configured_backup_evidence(env),
    }
    result = execute_refresh_file_preview_operation(operation, env, run_id, raw_task)
    write_latest_file_refresh_preview_context(result)
    return result


def execute_refresh_batch_preview_manifest(
    env: dict[str, str],
    run_id: str,
    raw_task: str,
    manifest: dict,
    *,
    detailed_group_structure: dict | None = None,
) -> dict:
    operations = local_refresh_batch_preview_operations(manifest)
    if len(operations) != 1:
        return {
            "ok": False,
            "status": "BLOCKED",
            "operation_kind": REFRESH_FILE_BATCH_PREVIEW_KIND,
            "run_id": run_id,
            "blocker": "A multi-file refresh requires exactly one batch operation per prompt.",
            "attachments": [],
        }
    operation = dict(operations[0][1])
    params = dict(operation.get("params") or {})
    params["planner_context"] = {
        "course_readback": current_course_readback_context(
            env,
            detailed_group_structure=detailed_group_structure,
        ),
        "historical_evidence": configured_backup_evidence(env),
    }
    operation["params"] = params
    return execute_refresh_file_batch_preview_operation(
        operation,
        env,
        run_id,
        raw_task,
    )


def execute_finalize_file_batch_preview_manifest(
    env: dict[str, str],
    run_id: str,
    raw_task: str,
    manifest: dict,
) -> dict:
    operations = local_finalize_file_batch_preview_operations(manifest)
    if len(operations) != 1:
        return {
            "ok": False,
            "status": "BLOCKED",
            "operation_kind": FINALIZE_FILE_BATCH_PREVIEW_KIND,
            "run_id": run_id,
            "blocker": "Final PDF generation requires exactly one active batch operation per prompt.",
            "attachments": [],
        }
    return execute_finalize_file_batch_preview_operation(
        operations[0][1],
        env,
        run_id,
        raw_task,
    )


def execute_reference_pack_rebuild_manifest(env: dict[str, str], run_id: str, raw_task: str, manifest: dict) -> dict:
    rebuild_operations = local_reference_pack_rebuild_operations(manifest)
    if len(rebuild_operations) != 1:
        return {
            "ok": False,
            "status": "BLOCKED",
            "operation_kind": REBUILD_REFERENCE_PACK_KIND,
            "run_id": run_id,
            "blocker": "Reference pack rebuild requires exactly one local reference operation per prompt.",
            "attachments": [],
        }
    _index, operation = rebuild_operations[0]
    return execute_rebuild_reference_pack_operation(operation, env, run_id, raw_task)


def execute_semester_schedule_update_manifest(env: dict[str, str], run_id: str, raw_task: str, manifest: dict) -> dict:
    schedule_operations = local_semester_schedule_update_operations(manifest)
    if len(schedule_operations) != 1:
        return {
            "ok": False,
            "status": "BLOCKED",
            "operation_kind": UPDATE_SEMESTER_SCHEDULE_KIND,
            "run_id": run_id,
            "blocker": "Semester schedule update requires exactly one local schedule operation per prompt.",
            "attachments": [],
        }
    _index, operation = schedule_operations[0]
    return execute_update_semester_schedule_operation(operation, env, run_id, raw_task)


def execute_timeline_draft_revision_manifest(
    env: dict[str, str],
    run_id: str,
    raw_task: str,
    manifest: dict,
) -> dict:
    operations = local_timeline_draft_revision_operations(manifest)
    if len(operations) != 1:
        return {
            "ok": False,
            "status": "BLOCKED",
            "operation_kind": REVISE_TIMELINE_DRAFT_KIND,
            "run_id": run_id,
            "blocker": "Timeline draft revision requires exactly one active draft operation per prompt.",
            "attachments": [],
        }
    return execute_revise_timeline_draft_operation(operations[0][1], env, run_id, raw_task)


def execute_timeline_draft_commit_manifest(
    env: dict[str, str],
    run_id: str,
    raw_task: str,
    manifest: dict,
) -> dict:
    operations = local_timeline_draft_commit_operations(manifest)
    if len(operations) != 1:
        return {
            "ok": False,
            "status": "BLOCKED",
            "operation_kind": COMMIT_TIMELINE_DRAFT_KIND,
            "run_id": run_id,
            "blocker": "Timeline draft commit requires exactly one active draft operation per prompt.",
            "attachments": [],
        }
    return execute_commit_timeline_draft_operation(operations[0][1], env, run_id, raw_task)


def execute_reference_rebuild_then_refresh_preview_manifest(
    env: dict[str, str],
    run_id: str,
    raw_task: str,
    manifest: dict,
) -> tuple[dict, dict | None]:
    rebuild_result = execute_reference_pack_rebuild_manifest(
        env,
        run_id,
        raw_task,
        {"operations": [manifest["operations"][0]]},
    )
    if (
        not bool(rebuild_result.get("ok"))
        or str(rebuild_result.get("status") or "") == "NEEDS_TIMELINE_REVIEW"
    ):
        return rebuild_result, None
    refresh_env = dict(env)
    if rebuild_result.get("reference_pack_path"):
        refresh_env["COURSE_REFERENCE_PACK_PATH"] = str(rebuild_result["reference_pack_path"])
    refresh_result = execute_refresh_preview_manifest(
        refresh_env,
        run_id,
        raw_task,
        {"operations": [manifest["operations"][1]]},
    )
    return rebuild_result, refresh_result


def execute_local_file_preparation_manifest(env: dict[str, str], run_id: str, raw_task: str, manifest: dict) -> dict:
    if manifest_is_schedule_update_then_refresh_preview(manifest):
        schedule_result = execute_semester_schedule_update_manifest(
            env,
            run_id,
            raw_task,
            {"operations": [manifest["operations"][0]]},
        )
        if (
            not bool(schedule_result.get("ok"))
            or str(schedule_result.get("status") or "") == "NEEDS_TIMELINE_REVIEW"
        ):
            return schedule_result
        refresh_env = dict(env)
        if schedule_result.get("reference_pack_path"):
            refresh_env["COURSE_REFERENCE_PACK_PATH"] = str(schedule_result["reference_pack_path"])
        return execute_refresh_preview_manifest(
            refresh_env,
            run_id,
            raw_task,
            {"operations": [manifest["operations"][1]]},
        )
    if manifest_is_reference_rebuild_then_refresh_preview(manifest):
        rebuild_result, refresh_result = execute_reference_rebuild_then_refresh_preview_manifest(
            env,
            run_id,
            raw_task,
            manifest,
        )
        return refresh_result or rebuild_result
    if manifest_is_only_reference_pack_rebuild(manifest):
        return execute_reference_pack_rebuild_manifest(env, run_id, raw_task, manifest)
    if manifest_is_only_semester_schedule_update(manifest):
        return execute_semester_schedule_update_manifest(env, run_id, raw_task, manifest)
    if manifest_is_only_timeline_draft_revision(manifest):
        return execute_timeline_draft_revision_manifest(env, run_id, raw_task, manifest)
    if manifest_is_only_timeline_draft_commit(manifest):
        return execute_timeline_draft_commit_manifest(env, run_id, raw_task, manifest)
    if manifest_is_only_refresh_batch_preview(manifest):
        return execute_refresh_batch_preview_manifest(env, run_id, raw_task, manifest)
    if manifest_is_only_finalize_file_batch_preview(manifest):
        return execute_finalize_file_batch_preview_manifest(env, run_id, raw_task, manifest)
    if manifest_is_only_refresh_preview(manifest):
        return execute_refresh_preview_manifest(env, run_id, raw_task, manifest)
    return {
        "ok": False,
        "status": "BLOCKED",
        "run_id": run_id,
        "blocker": "Local file preparation must run separately from Moodle changes.",
    }


def group_names_from_operation(operation: dict) -> list[str]:
    env_overrides = operation.get("env_overrides") if isinstance(operation.get("env_overrides"), dict) else {}
    raw = (
        operation.get("choicegroup_target_group_names")
        or operation.get("group_names")
        or operation.get("groups")
        or env_overrides.get("MOODLE_GROUP_NAMES")
    )
    if isinstance(raw, (list, tuple)):
        return [str(item).strip() for item in raw if str(item or "").strip()]
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, (list, tuple)):
        return [str(item).strip() for item in parsed if str(item or "").strip()]
    return [part.strip() for part in text.split(",") if part.strip()]


def post_write_readback_settings(env: dict[str, str]) -> tuple[int, float]:
    try:
        attempts = int(float(env.get("COURSE_AGENT_POST_WRITE_READBACK_ATTEMPTS", "12")))
    except ValueError:
        attempts = 12
    try:
        delay_seconds = float(env.get("COURSE_AGENT_POST_WRITE_READBACK_RETRY_SECONDS", "2"))
    except ValueError:
        delay_seconds = 2.0
    return max(1, attempts), max(0.0, delay_seconds)


def created_module_cmid_from_result(result: dict) -> str:
    return first_text(
        result.get("target_module_id"),
        result.get("cmid"),
        cmid_from_moodle_module_url(
            first_text(
                result.get("display_url"),
                result.get("target_resource_url"),
                result.get("target_url"),
            )
        ),
    )


def module_records_from_contents(contents: list[dict]) -> list[dict]:
    records: list[dict] = []
    for section in contents:
        if not isinstance(section, dict):
            continue
        for module in section.get("modules") or []:
            if not isinstance(module, dict):
                continue
            records.append(
                {
                    "sectionnum": section.get("section"),
                    "section_name": section.get("name"),
                    "cmid": module.get("id"),
                    "instance": module.get("instance"),
                    "modname": module.get("modname"),
                    "name": module.get("name"),
                    "visible": module.get("visible"),
                    "url": module.get("url"),
                }
            )
    return records


def structure_scalar_text(value: object) -> str:
    return "" if value is None else str(value).strip()


def resolve_created_module_readback(
    result: dict,
    materialized: dict,
    moodle_env: dict[str, str],
    *,
    modname: str,
    title: str,
) -> dict:
    observed_cmid = created_module_cmid_from_result(result)
    if not result.get("ok"):
        raise RuntimeError(
            first_text(
                result.get("blocker"),
                result.get("error"),
                f"Browser creation for {title!r} did not pass its immediate page verification.",
            )
        )
    sectionnum = structure_scalar_text(materialized.get("sectionnum"))
    attempts, delay_seconds = post_write_readback_settings(moodle_env)
    last_reason = "no APIM readback attempt completed"
    for attempt in range(1, attempts + 1):
        try:
            modules = module_records_from_contents(call_apim_contents(moodle_env))
            matches = [
                module
                for module in modules
                if str(module.get("modname") or "").strip() == modname
                and str(module.get("name") or "").strip() == title
                and (
                    not sectionnum
                    or structure_scalar_text(module.get("sectionnum")) == sectionnum
                )
                and (
                    not observed_cmid
                    or str(module.get("cmid") or "").strip() == observed_cmid
                )
            ]
            if len(matches) == 1:
                resolved = str(matches[0].get("cmid") or "").strip()
                if not resolved.isdigit():
                    raise RuntimeError(
                        f"Created module readback for {title!r} did not return a numeric cmid."
                    )
                return matches[0]
            if len(matches) > 1:
                raise RuntimeError(
                    f"Created module readback for {title!r} is ambiguous: found {len(matches)} exact matches."
                )
            last_reason = (
                f"attempt {attempt}/{attempts} found 0 exact APIM matches"
                + (f" for browser-observed cmid {observed_cmid}" if observed_cmid else "")
            )
        except RuntimeError as exc:
            if "ambiguous" in str(exc) or "numeric cmid" in str(exc):
                raise
            last_reason = f"attempt {attempt}/{attempts} failed: {exc}"
        except Exception as exc:
            last_reason = f"attempt {attempt}/{attempts} failed: {type(exc).__name__}: {exc}"
        if attempt < attempts:
            time.sleep(delay_seconds)
    observed = f" Browser save returned cmid {observed_cmid}." if observed_cmid else ""
    raise RuntimeError(
        f"Created module {title!r} did not become uniquely visible in bounded APIM readback "
        f"after {attempts} attempts.{observed} Last check: {last_reason}."
    )


def resolve_created_module_id(
    result: dict,
    materialized: dict,
    moodle_env: dict[str, str],
    *,
    modname: str,
    title: str,
) -> str:
    record = resolve_created_module_readback(
        result,
        materialized,
        moodle_env,
        modname=modname,
        title=title,
    )
    return str(record.get("cmid") or "").strip()


def execute_group_signup_activity_operation(materialized: dict, moodle_env: dict[str, str], moodle_page, timeout_error_type) -> dict:
    title = first_text(materialized.get("title"), materialized.get("activity_name"), materialized.get("target_object"))
    group_names = group_names_from_operation(materialized)
    if not title:
        return {
            "ok": False,
            "status": "BLOCKED",
            "operation_id": materialized.get("operation_id", ""),
            "blocker": "Group sign-up activity operation is missing a title.",
        }
    if not group_names:
        return {
            "ok": False,
            "status": "BLOCKED",
            "operation_id": materialized.get("operation_id", ""),
            "target_object": title,
            "blocker": "Group sign-up activity operation is missing approved group names.",
        }
    expected_visibility = first_text(materialized.get("expected_visibility"), "Show on course page")
    item = {
        "target_course_id": SANDBOX_COURSE_ID,
        "modname": "choicegroup",
        "title": title,
        "name": title,
        "intro": first_text(materialized.get("intro"), materialized.get("description")),
        "sectionnum": str(materialized.get("sectionnum") or "0"),
        "section_name": materialized.get("section_name", ""),
        "visibility": expected_visibility,
        "choicegroup_target_group_names": group_names,
    }
    if materialized.get("maxanswers") not in ("", None):
        item["maxanswers"] = materialized.get("maxanswers")
    manifest = {
        "batch_id": materialized.get("batch_id", moodle_env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")),
        "run_id": materialized.get("approval_run_id", ""),
        "approval_phrase": "",
    }
    report_dir = (
        REPORTS
        / f"group_signup_activity_{stamp()}_{safe_name(str(materialized.get('approval_run_id') or 'run'))}"
        / f"01_{safe_name(title)}"
    )
    result = execute_one_in_context(moodle_page, timeout_error_type, moodle_env, item, manifest, report_dir)
    try:
        cmid = resolve_created_module_id(
            result,
            materialized,
            moodle_env,
            modname="choicegroup",
            title=title,
        )
    except Exception as exc:
        result.update({"ok": False, "status": "BLOCKED", "blocker": str(exc)})
        cmid = created_module_cmid_from_result(result)
    result.update(
        {
            "operation_id": materialized.get("operation_id", ""),
            "target_object": title,
            "resource_name": title,
            "target_module_id": cmid,
            "report_dir": str(report_dir),
            "group_names": group_names,
        }
    )
    write_result(report_dir, "result.json", result)
    return result


def execute_assignment_activity_operation(materialized: dict, moodle_env: dict[str, str], moodle_page, timeout_error_type) -> dict:
    title = first_text(materialized.get("title"), materialized.get("activity_name"), materialized.get("target_object"))
    if not title:
        return {
            "ok": False,
            "status": "BLOCKED",
            "operation_id": materialized.get("operation_id", ""),
            "blocker": "Assignment activity operation is missing a title.",
        }
    expected_visibility = first_text(materialized.get("expected_visibility"), "Show on course page")
    item = {
        "target_course_id": SANDBOX_COURSE_ID,
        "modname": "assign",
        "title": title,
        "name": title,
        "intro": first_text(materialized.get("intro"), materialized.get("description")),
        "sectionnum": str(materialized.get("sectionnum") or "0"),
        "section_name": materialized.get("section_name", ""),
        "visibility": expected_visibility,
    }
    manifest = {
        "batch_id": materialized.get("batch_id", moodle_env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")),
        "run_id": materialized.get("approval_run_id", ""),
        "approval_phrase": "",
    }
    report_dir = (
        REPORTS
        / f"assignment_activity_{stamp()}_{safe_name(str(materialized.get('approval_run_id') or 'run'))}"
        / f"01_{safe_name(title)}"
    )
    result = execute_one_in_context(moodle_page, timeout_error_type, moodle_env, item, manifest, report_dir)
    try:
        cmid = resolve_created_module_id(
            result,
            materialized,
            moodle_env,
            modname="assign",
            title=title,
        )
    except Exception as exc:
        result.update({"ok": False, "status": "BLOCKED", "blocker": str(exc)})
        cmid = created_module_cmid_from_result(result)
    result.update(
        {
            "operation_id": materialized.get("operation_id", ""),
            "target_object": title,
            "resource_name": title,
            "target_module_id": cmid,
            "report_dir": str(report_dir),
        }
    )
    write_result(report_dir, "result.json", result)
    return result


def execute_forum_activity_operation(materialized: dict, moodle_env: dict[str, str], moodle_page, timeout_error_type) -> dict:
    title = first_text(materialized.get("title"), materialized.get("activity_name"), materialized.get("target_object"))
    if not title:
        return {
            "ok": False,
            "status": "BLOCKED",
            "operation_id": materialized.get("operation_id", ""),
            "blocker": "Forum activity operation is missing a title.",
        }
    expected_visibility = first_text(materialized.get("expected_visibility"), "Show on course page")
    item = {
        "target_course_id": SANDBOX_COURSE_ID,
        "modname": "forum",
        "title": title,
        "name": title,
        "intro": first_text(materialized.get("intro"), materialized.get("description")),
        "sectionnum": str(materialized.get("sectionnum") or "0"),
        "section_name": materialized.get("section_name", ""),
        "visibility": expected_visibility,
    }
    manifest = {
        "batch_id": materialized.get("batch_id", moodle_env.get("BATCH_ID", "SMOKE-MOODLE-WA-001")),
        "run_id": materialized.get("approval_run_id", ""),
        "approval_phrase": "",
    }
    report_dir = (
        REPORTS
        / f"forum_activity_{stamp()}_{safe_name(str(materialized.get('approval_run_id') or 'run'))}"
        / f"01_{safe_name(title)}"
    )
    before = current_course_readback_context(moodle_env)
    existing = [
        module
        for section in before.get("sections") or []
        if isinstance(section, dict)
        for module in section.get("modules") or []
        if isinstance(module, dict)
        and str(module.get("modname") or "") == "forum"
        and str(module.get("name") or "").strip().casefold() == title.casefold()
    ]
    if existing:
        return {
            "ok": False,
            "status": "BLOCKED",
            "operation_id": materialized.get("operation_id", ""),
            "target_object": title,
            "saves_count": 0,
            "blocker": (
                f"Forum create preflight found {len(existing)} existing exact title match(es) for {title!r}; re-plan from current readback."
            ),
        }
    result = execute_one_in_context(moodle_page, timeout_error_type, moodle_env, item, manifest, report_dir)
    try:
        module_record = resolve_created_module_readback(
            result,
            materialized,
            moodle_env,
            modname="forum",
            title=title,
        )
        cmid = str(module_record.get("cmid") or "").strip()
        forum_id = str(module_record.get("instance") or "").strip()
        if not forum_id.isdigit():
            raise RuntimeError(f"Created Forum readback for {title!r} did not return a numeric forum id.")
    except Exception as exc:
        result.update({"ok": False, "status": "BLOCKED", "blocker": str(exc)})
        cmid = created_module_cmid_from_result(result)
        forum_id = ""
    result.update(
        {
            "operation_id": materialized.get("operation_id", ""),
            "target_object": title,
            "resource_name": title,
            "target_module_id": cmid,
            "target_forum_id": forum_id,
            "target_forum_url": (
                f"https://migrationtest-moodle.hku.hk/mod/forum/view.php?id={cmid}" if cmid else ""
            ),
            "modname": "forum",
            "report_dir": str(report_dir),
        }
    )
    write_result(report_dir, "result.json", result)
    return result


def is_sandbox_moodle_page_url(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    return parsed.scheme in {"http", "https"} and parsed.hostname == "migrationtest-moodle.hku.hk" and "id=118635" not in parsed.query


class SandboxMoodleSessionPage:
    """Proxy one long-lived Moodle page with sandbox-wide login recovery."""

    def __init__(self, page, env: dict[str, str]):
        self._page = page
        self._env = dict(env)

    @property
    def raw_page(self):
        return self._page

    def __getattr__(self, name: str):
        return getattr(self._page, name)

    def goto(self, url: str, *args, **kwargs):
        target_url = str(url or "").strip()
        if not is_sandbox_moodle_page_url(target_url):
            raise RuntimeError(
                f"Refusing non-sandbox navigation in the reusable Moodle session: {target_url}"
            )
        response = self._page.goto(target_url, *args, **kwargs)
        if looks_like_login(self._page):
            ensure_moodle_authenticated(
                self._page,
                self._env,
                target_url=target_url,
            )
            if looks_like_login(self._page):
                raise RuntimeError(
                    "Automatic Moodle login recovery did not restore the exact sandbox target."
                )
        return response


def forum_post_delete_target_url(operation: dict) -> str:
    for key in ("target_post_url", "post_url", "target_url", "url", "target_discussion_url", "discussion_url"):
        url = first_text(operation.get(key))
        if url:
            if not is_sandbox_moodle_page_url(url):
                raise RuntimeError(f"Refusing non-sandbox forum delete URL: {url}")
            return url
    post_id = first_text(operation.get("target_post_id"), operation.get("post_id"))
    if post_id:
        if not post_id.isdigit():
            raise RuntimeError(f"Forum post id must be numeric: {post_id!r}")
        return f"https://migrationtest-moodle.hku.hk/mod/forum/post.php?delete={post_id}"
    discussion_id = first_text(operation.get("target_discussion_id"), operation.get("discussion_id"))
    if discussion_id:
        if not discussion_id.isdigit():
            raise RuntimeError(f"Forum discussion id must be numeric: {discussion_id!r}")
        return f"https://migrationtest-moodle.hku.hk/mod/forum/discuss.php?d={discussion_id}"
    return ""


def forum_post_delete_forum_url(operation: dict) -> str:
    for key in ("target_forum_url", "forum_url", "target_forum_link", "forum_link"):
        url = first_text(operation.get(key))
        if url:
            if not is_sandbox_moodle_page_url(url):
                raise RuntimeError(f"Refusing non-sandbox forum URL: {url}")
            return url
    forum_cmid = first_text(
        operation.get("target_forum_cmid"),
        operation.get("forum_cmid"),
        operation.get("target_forum_module_id"),
        operation.get("forum_module_id"),
    )
    if forum_cmid:
        if not forum_cmid.isdigit():
            raise RuntimeError(f"Forum module id must be numeric: {forum_cmid!r}")
        return f"https://migrationtest-moodle.hku.hk/mod/forum/view.php?id={forum_cmid}"
    return ""


def forum_target_text(operation: dict) -> str:
    return first_text(operation.get("target_subject"), operation.get("target_ref"), operation.get("subject"), operation.get("target_object"))


def forum_text_contains_target(page_text: str, target_text: str) -> bool:
    target = re.sub(r"\s+", " ", str(target_text or "")).strip()
    if not target:
        return False
    text = re.sub(r"\s+", " ", str(page_text or ""))
    if re.fullmatch(r"[A-Za-z0-9_]+", target):
        return re.search(rf"(?<![A-Za-z0-9_]){re.escape(target)}(?![A-Za-z0-9_])", text, re.IGNORECASE) is not None
    return target.lower() in text.lower()


def forum_discussion_missing_text(page_text: str) -> bool:
    text = re.sub(r"\s+", " ", str(page_text or "")).strip().lower()
    return any(
        marker in text
        for marker in (
            "the discussion was not found",
            "discussion was not found",
            "discussion not found",
            "errordiscussionnotfound",
        )
    )


def find_forum_discussion_href(page, target_text: str) -> str:
    return str(
        page.evaluate(
            """
            ({targetText}) => {
              const normalize = (text) => String(text || '').replace(/\\s+/g, ' ').trim();
              const target = normalize(targetText).toLowerCase();
              if (!target) return '';
              const visible = (element) => {
                if (!element) return false;
                const style = window.getComputedStyle(element);
                return style && style.visibility !== 'hidden' && style.display !== 'none' && element.getClientRects().length > 0;
              };
              const anchors = Array.from(document.querySelectorAll('a[href*="/mod/forum/discuss.php?d="]'))
                .filter((anchor) => visible(anchor))
                .map((anchor) => {
                  const href = anchor.href || anchor.getAttribute('href') || '';
                  const linkText = normalize(anchor.innerText || anchor.textContent || anchor.getAttribute('title') || '');
                  const row = anchor.closest('tr') || anchor.closest('li') || anchor.closest('article') || anchor.closest('.discussion') || anchor.closest('div');
                  const context = normalize((row && (row.innerText || row.textContent)) || linkText);
                  return {href, linkText, context};
                })
                .filter((item) => item.href);
              const exact = anchors.filter((item) => item.linkText.toLowerCase() === target);
              if (exact.length === 1) return exact[0].href;
              const contextMatches = anchors.filter((item) => item.context.toLowerCase().includes(target));
              if (contextMatches.length === 1) return contextMatches[0].href;
              return '';
            }
            """,
            {"targetText": target_text},
        )
        or ""
    )


def find_forum_delete_href(page, *, post_id: str, target_text: str) -> str:
    return str(
        page.evaluate(
            """
            ({postId, targetText}) => {
              const normalize = (text) => String(text || '').replace(/\\s+/g, ' ').trim();
              const target = normalize(targetText).toLowerCase();
              const anchors = Array.from(document.querySelectorAll('a[href*="/mod/forum/post.php"][href*="delete="]'));
              const matches = anchors.map((anchor) => {
                const href = anchor.href || anchor.getAttribute('href') || '';
                const containers = [
                  anchor.closest('[data-region]'),
                  anchor.closest('article'),
                  anchor.closest('.forumpost'),
                  anchor.closest('li'),
                  anchor.closest('div'),
                  document.body,
                ].filter(Boolean);
                const context = normalize(containers.map((node) => node.innerText || node.textContent || '').join(' '));
                return {href, context};
              }).filter((item) => item.href);
              if (postId) {
                const byPost = matches.filter((item) => item.href.includes(`delete=${postId}`));
                if (byPost.length === 1) return byPost[0].href;
              }
              if (target) {
                const byTarget = matches.filter((item) => item.context.toLowerCase().includes(target));
                if (byTarget.length === 1) return byTarget[0].href;
              }
              if (!target && matches.length === 1) return matches[0].href;
              return '';
            }
            """,
            {"postId": post_id, "targetText": target_text},
        )
        or ""
    )


def execute_forum_post_delete_operation(materialized: dict, moodle_env: dict[str, str], moodle_page, timeout_error_type) -> dict:
    operation_id = first_text(materialized.get("operation_id"), "SANDBOX-FORUM-POST-DELETE")
    target_text = forum_target_text(materialized)
    post_id = first_text(materialized.get("target_post_id"), materialized.get("post_id"))
    target_url = forum_post_delete_target_url(materialized)
    forum_url = forum_post_delete_forum_url(materialized)
    report_dir = REPORTS / f"forum_post_delete_{stamp()}_{safe_name(operation_id)}"
    report_dir.mkdir(parents=True, exist_ok=True)
    result: dict = {
        "ok": False,
        "status": "STARTED",
        "operation_id": operation_id,
        "target_object": first_text(target_text, post_id, materialized.get("target_discussion_id"), "Forum message"),
        "target_url": target_url,
        "saves_count": 0,
        "report_dir": str(report_dir),
    }
    try:
        searched_forum_by_text = False
        if not target_url:
            if not target_text:
                raise RuntimeError("Forum delete by text requires an approved subject or target text.")
            if not forum_url:
                raise RuntimeError("Forum delete by text requires a resolved sandbox forum URL or module id.")
            searched_forum_by_text = True
            goto_with_retry(moodle_page, forum_url, wait_until="domcontentloaded", timeout=60000)
            recover_moodle_login_for_target(
                moodle_page,
                forum_url,
                moodle_env,
                context="forum discussion lookup",
            )
            save_screenshot(moodle_page, report_dir, "01_forum_search_page.png")
            write_result(report_dir, "01_forum_search_page.json", page_summary(moodle_page))
            if looks_like_login(moodle_page):
                raise RuntimeError("Moodle requested login during forum discussion lookup.")
            discussion_href = find_forum_discussion_href(moodle_page, target_text)
            if not discussion_href:
                raise RuntimeError("Could not find one exact Moodle forum discussion matching the approved target text.")
            if not is_sandbox_moodle_page_url(discussion_href):
                raise RuntimeError(f"Refusing non-sandbox forum discussion link: {discussion_href}")
            target_url = discussion_href
            result["target_url"] = target_url
        goto_with_retry(moodle_page, target_url, wait_until="domcontentloaded", timeout=60000)
        recover_moodle_login_for_target(
            moodle_page,
            target_url,
            moodle_env,
            context="forum post delete",
        )
        target_page_stem = "02_target_or_delete_page" if searched_forum_by_text else "01_target_or_delete_page"
        save_screenshot(moodle_page, report_dir, f"{target_page_stem}.png")
        write_result(report_dir, f"{target_page_stem}.json", page_summary(moodle_page))
        if looks_like_login(moodle_page):
            raise RuntimeError("Moodle requested login during forum post delete.")
        page_text = visible_text(moodle_page, 12000)
        if target_text and not forum_text_contains_target(page_text, target_text):
            raise RuntimeError(f"Forum delete target page did not show approved target text {target_text!r}.")

        parsed = urlparse(moodle_page.url)
        is_delete_page = "/mod/forum/post.php" in parsed.path and "delete" in parse_qs(parsed.query)
        if not is_delete_page:
            delete_href = find_forum_delete_href(moodle_page, post_id=post_id, target_text=target_text)
            if not delete_href:
                raise RuntimeError("Could not find one exact Moodle forum Delete link for the approved target.")
            if not is_sandbox_moodle_page_url(delete_href):
                raise RuntimeError(f"Refusing non-sandbox forum delete link: {delete_href}")
            goto_with_retry(moodle_page, delete_href, wait_until="domcontentloaded", timeout=60000)
            recover_moodle_login_for_target(
                moodle_page,
                delete_href,
                moodle_env,
                context="forum delete confirmation",
            )
            save_screenshot(moodle_page, report_dir, "02_delete_confirmation.png")
            write_result(report_dir, "02_delete_confirmation.json", page_summary(moodle_page))
            page_text = visible_text(moodle_page, 12000)
        if target_text and not forum_text_contains_target(page_text, target_text):
            raise RuntimeError(f"Moodle delete confirmation did not show approved target text {target_text!r}.")

        clicked = confirm_delete_current_page(moodle_page)
        result["saves_count"] = 1
        try:
            moodle_page.wait_for_load_state("domcontentloaded", timeout=45000)
        except timeout_error_type:
            moodle_page.wait_for_timeout(2000)
        save_screenshot(moodle_page, report_dir, "03_after_delete.png")
        write_result(report_dir, "03_after_delete.json", page_summary(moodle_page))
        if target_text and target_url and is_sandbox_moodle_page_url(target_url):
            try:
                goto_with_retry(moodle_page, target_url, wait_until="domcontentloaded", timeout=60000)
                recover_moodle_login_for_target(
                    moodle_page,
                    target_url,
                    moodle_env,
                    context="forum delete readback",
                )
                after_text = visible_text(moodle_page, 12000)
                if forum_discussion_missing_text(after_text):
                    result["verification"] = "Moodle reports the target discussion no longer exists."
                elif forum_text_contains_target(after_text, target_text):
                    raise RuntimeError(f"Forum delete verification still found target text {target_text!r}.")
            except Exception as verify_exc:
                if "still found target text" in str(verify_exc):
                    raise
                result["verification_warning"] = str(verify_exc)
        result.update(
            {
                "ok": True,
                "status": "PASS",
                "clicked": clicked,
                "write_action_taken": True,
                "course_verify_url": SANDBOX_COURSE_URL,
                "final_moodle_state": {"course_url": SANDBOX_COURSE_URL, "final_browser_url": getattr(moodle_page, "url", "")},
            }
        )
    except Exception as exc:
        result.update({"ok": False, "status": "BLOCKED", "blocker": str(exc), "current_url": getattr(moodle_page, "url", "")})
        try:
            save_screenshot(moodle_page, report_dir, "error_state.png")
        except Exception:
            pass
    write_result(report_dir, "result.json", result)
    return result


def forum_reply_post_id_from_url(url: str) -> str:
    text = first_text(url)
    if not text:
        return ""
    if not is_sandbox_moodle_page_url(text):
        raise RuntimeError(f"Refusing non-sandbox Forum reply URL: {text}")
    parsed = urlparse(text)
    delete_values = parse_qs(parsed.query).get("delete") or []
    if delete_values and str(delete_values[0]).isdigit():
        return str(delete_values[0])
    match = re.fullmatch(r"p(\d+)", str(parsed.fragment or ""))
    return match.group(1) if match else ""


def find_forum_reply_delete_target(
    page,
    *,
    reply_post_id: str,
    reply_subject: str,
    reply_text: str,
) -> dict:
    value = page.evaluate(
        """
        ({replyPostId, replySubject, replyText}) => {
          const normalize = (text) => String(text || '').replace(/\\s+/g, ' ').trim();
          const fold = (text) => normalize(text).toLocaleLowerCase();
          const postId = (element) => {
            const explicit = element.getAttribute('data-post-id') || element.dataset?.postId || '';
            if (/^\\d+$/.test(explicit)) return explicit;
            const ownMatch = String(element.id || '').match(/^p(\\d+)$/);
            return ownMatch ? ownMatch[1] : '';
          };
          const containers = Array.from(document.querySelectorAll(
            'article.forum-post-container[data-post-id], '
            + '[data-region="post"][data-post-id], '
            + 'article[id^="p"], div.forumpost[id^="p"]'
          ));
          const seen = new Set();
          const posts = containers.map((element) => {
            const id = postId(element);
            if (!id || seen.has(id)) return null;
            seen.add(id);
            const subjectElement = element.querySelector(
              '.forum-post-core-subject, [data-region="post-subject"], .subject'
            );
            const bodyElement = element.querySelector(
              '.post-content-container, [data-region="post-content"], .posting.fullpost, .content'
            );
            const deleteLinks = Array.from(element.querySelectorAll('a[href*="delete="]'))
              .map((anchor) => {
                try {
                  const url = new URL(
                    anchor.href || anchor.getAttribute('href') || '',
                    window.location.href
                  );
                  if (
                    url.pathname === '/mod/forum/post.php'
                    && /^\\d+$/.test(url.searchParams.get('delete') || '')
                  ) {
                    return url.href;
                  }
                } catch (_error) {
                  // Malformed links fail closed below.
                }
                return '';
              })
              .filter(Boolean);
            return {
              post_id: id,
              subject: normalize(
                subjectElement?.innerText || subjectElement?.textContent || ''
              ),
              body: normalize(
                bodyElement?.innerText || bodyElement?.textContent || ''
              ),
              delete_href: deleteLinks.length === 1 ? deleteLinks[0] : '',
              delete_link_count: deleteLinks.length
            };
          }).filter(Boolean);
          const rootPostId = posts.length ? posts[0].post_id : '';
          const replies = posts.filter((item) => item.post_id !== rootPostId);
          const wantedId = normalize(replyPostId);
          const wantedSubject = fold(replySubject);
          const wantedText = fold(replyText);
          const matches = replies.filter((item) => {
            if (wantedId) return item.post_id === wantedId;
            const subjectMatches = wantedSubject && fold(item.subject) === wantedSubject;
            const textMatches = wantedText
              && (fold(item.body) === wantedText || fold(item.subject) === wantedText);
            if (wantedSubject && wantedText) return subjectMatches && textMatches;
            return Boolean(subjectMatches || textMatches);
          });
          const selected = matches.length === 1 ? matches[0] : null;
          return {
            root_post_id: rootPostId,
            reply_post_ids: replies.map((item) => item.post_id),
            match_count: matches.length,
            post_id: selected?.post_id || '',
            href: selected?.delete_href || '',
            delete_link_count: selected?.delete_link_count || 0
          };
        }
        """,
        {
            "replyPostId": reply_post_id,
            "replySubject": reply_subject,
            "replyText": reply_text,
        },
    )
    return dict(value) if isinstance(value, dict) else {
        "root_post_id": "",
        "reply_post_ids": [],
        "match_count": 0,
        "post_id": "",
        "href": "",
        "delete_link_count": 0,
    }


def execute_forum_reply_delete_operation(
    materialized: dict,
    moodle_env: dict[str, str],
    moodle_page,
    timeout_error_type,
) -> dict:
    operation_id = first_text(
        materialized.get("operation_id"),
        "SANDBOX-FORUM-REPLY-DELETE",
    )
    discussion_url = forum_post_reply_target_url(materialized)
    discussion_id = first_text(
        materialized.get("target_discussion_id"),
        materialized.get("discussion_id"),
    )
    forum_cmid = first_text(
        materialized.get("target_forum_cmid"),
        materialized.get("target_module_id"),
    )
    reply_post_url = first_text(
        materialized.get("target_reply_post_url"),
        materialized.get("reply_post_url"),
    )
    reply_post_id = first_text(
        materialized.get("target_reply_post_id"),
        materialized.get("reply_post_id"),
        forum_reply_post_id_from_url(reply_post_url) if reply_post_url else "",
    )
    reply_subject = first_text(
        materialized.get("target_reply_subject"),
        materialized.get("reply_subject"),
    )
    reply_text = first_text(
        materialized.get("target_reply_text"),
        materialized.get("reply_text"),
        materialized.get("target_ref"),
    )
    discussion_title = first_text(
        materialized.get("target_discussion_title"),
        materialized.get("discussion_title"),
    )
    report_dir = (
        REPORTS / f"forum_reply_delete_{stamp()}_{safe_name(operation_id)}"
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    result: dict = {
        "ok": False,
        "status": "STARTED",
        "operation_id": operation_id,
        "target_object": first_text(
            materialized.get("target_object"),
            reply_subject,
            reply_text,
            reply_post_id,
            "Forum reply",
        ),
        "target_discussion_id": discussion_id,
        "target_discussion_url": discussion_url,
        "target_discussion_title": discussion_title,
        "target_reply_post_id": reply_post_id,
        "saves_count": 0,
        "report_dir": str(report_dir),
    }
    try:
        if not discussion_id.isdigit():
            raise RuntimeError(
                "Forum reply delete requires an exact numeric discussion id."
            )
        if not forum_cmid.isdigit():
            raise RuntimeError(
                "Forum reply delete requires an exact Forum course-module id."
            )
        if not reply_post_id and not reply_subject and not reply_text:
            raise RuntimeError(
                "Forum reply delete requires a reply post id, subject, or exact text."
            )
        goto_with_retry(
            moodle_page,
            discussion_url,
            wait_until="domcontentloaded",
            timeout=60000,
        )
        recover_moodle_login_for_target(
            moodle_page,
            discussion_url,
            moodle_env,
            context="Forum reply delete preflight",
        )
        if looks_like_login(moodle_page):
            raise RuntimeError(
                "Moodle requested login during Forum reply delete preflight."
            )
        assert_forum_reply_discussion_identity(
            moodle_page,
            discussion_id=discussion_id,
            forum_cmid=forum_cmid,
        )
        if discussion_title and not forum_text_contains_target(
            visible_text(moodle_page, 12000),
            discussion_title,
        ):
            raise RuntimeError(
                "Forum reply delete page did not show the approved discussion title."
            )
        save_screenshot(moodle_page, report_dir, "01_reply_delete_preflight.png")
        write_result(
            report_dir,
            "01_reply_delete_preflight.json",
            page_summary(moodle_page),
        )
        target = find_forum_reply_delete_target(
            moodle_page,
            reply_post_id=reply_post_id,
            reply_subject=reply_subject,
            reply_text=reply_text,
        )
        root_post_id = first_text(target.get("root_post_id"))
        if reply_post_id and reply_post_id == root_post_id:
            raise RuntimeError(
                "Refusing to delete the Forum discussion root post as a reply."
            )
        if (
            reply_post_id
            and int(target.get("match_count", 0) or 0) == 0
            and reply_post_id
            not in {
                str(value)
                for value in target.get("reply_post_ids", [])
                if str(value).isdigit()
            }
        ):
            result.update(
                {
                    "ok": True,
                    "status": "PASS",
                    "already_absent": True,
                    "resolved_reply_post_id": reply_post_id,
                    "verification": (
                        f"Reply post id {reply_post_id} is already absent "
                        "from the approved discussion."
                    ),
                    "course_verify_url": discussion_url,
                    "final_moodle_state": {
                        "course_url": SANDBOX_COURSE_URL,
                        "discussion_url": discussion_url,
                        "reply_post_id": reply_post_id,
                        "reply_absent": True,
                    },
                }
            )
            write_result(report_dir, "result.json", result)
            return result
        if int(target.get("match_count", 0) or 0) != 1:
            raise RuntimeError(
                "Forum reply delete preflight requires exactly one non-root "
                f"reply match; found {int(target.get('match_count', 0) or 0)}."
            )
        resolved_post_id = first_text(target.get("post_id"))
        delete_href = first_text(target.get("href"))
        if not resolved_post_id.isdigit():
            raise RuntimeError(
                "Forum reply delete preflight did not resolve a numeric reply post id."
            )
        if int(target.get("delete_link_count", 0) or 0) != 1 or not delete_href:
            raise RuntimeError(
                "Forum reply delete preflight did not find one exact Delete control."
            )
        if forum_reply_post_id_from_url(delete_href) != resolved_post_id:
            raise RuntimeError(
                "Forum reply Delete control did not match the resolved reply post id."
            )
        result["resolved_reply_post_id"] = resolved_post_id

        goto_with_retry(
            moodle_page,
            delete_href,
            wait_until="domcontentloaded",
            timeout=60000,
        )
        recover_moodle_login_for_target(
            moodle_page,
            delete_href,
            moodle_env,
            context="Forum reply delete confirmation",
        )
        if looks_like_login(moodle_page):
            raise RuntimeError(
                "Moodle requested login during Forum reply delete confirmation."
            )
        if forum_reply_post_id_from_url(moodle_page.url) != resolved_post_id:
            raise RuntimeError(
                "Forum reply delete confirmation URL did not match the resolved reply."
            )
        confirmation_text = visible_text(moodle_page, 12000)
        for approved_text in (reply_subject, reply_text):
            if approved_text and not forum_text_contains_target(
                confirmation_text,
                approved_text,
            ):
                raise RuntimeError(
                    "Forum reply delete confirmation did not show the approved reply."
                )
        save_screenshot(moodle_page, report_dir, "02_reply_delete_confirmation.png")
        write_result(
            report_dir,
            "02_reply_delete_confirmation.json",
            page_summary(moodle_page),
        )

        clicked = confirm_delete_current_page(moodle_page)
        result["saves_count"] = 1
        try:
            moodle_page.wait_for_load_state("domcontentloaded", timeout=45000)
        except timeout_error_type:
            moodle_page.wait_for_timeout(2000)
        readback = read_forum_posts_from_page(
            moodle_page,
            discussion_url,
            report_dir=report_dir / "readback",
            moodle_env=moodle_env,
        )
        remaining_ids = {
            str(item.get("post_id") or "")
            for item in readback.get("posts") or []
            if isinstance(item, dict)
        }
        if resolved_post_id in remaining_ids:
            raise RuntimeError(
                f"Forum reply delete readback still found reply post id {resolved_post_id}."
            )
        result.update(
            {
                "ok": True,
                "status": "PASS",
                "clicked": clicked,
                "write_action_taken": True,
                "verification": (
                    f"Reply post id {resolved_post_id} is absent from the "
                    "approved discussion."
                ),
                "course_verify_url": discussion_url,
                "final_moodle_state": {
                    "course_url": SANDBOX_COURSE_URL,
                    "discussion_url": discussion_url,
                    "reply_post_id": resolved_post_id,
                    "reply_absent": True,
                },
            }
        )
    except Exception as exc:
        result.update(
            {
                "ok": False,
                "status": "BLOCKED",
                "blocker": str(exc),
                "current_url": getattr(moodle_page, "url", ""),
            }
        )
        try:
            save_screenshot(moodle_page, report_dir, "error_state.png")
        except Exception:
            pass
    write_result(report_dir, "result.json", result)
    return result


def forum_post_reply_target_url(operation: dict) -> str:
    url = first_text(
        operation.get("target_discussion_url"),
        operation.get("discussion_url"),
    )
    if url:
        if not is_sandbox_moodle_page_url(url):
            raise RuntimeError(f"Refusing non-sandbox Forum reply URL: {url}")
        return url
    discussion_id = first_text(
        operation.get("target_discussion_id"),
        operation.get("discussion_id"),
    )
    if not discussion_id.isdigit():
        raise RuntimeError(
            "Forum reply requires an exact numeric discussion id or sandbox discussion URL."
        )
    return (
        "https://migrationtest-moodle.hku.hk/mod/forum/discuss.php"
        f"?d={discussion_id}"
    )


def find_forum_reply_target(
    page,
    *,
    parent_post_id: str,
    discussion_title: str,
) -> dict:
    values = page.evaluate(
        """
        () => {
          const normalize = (text) => String(text || '').replace(/\\s+/g, ' ').trim().toLowerCase();
          const visible = (element) => {
            if (!element) return false;
            const style = window.getComputedStyle(element);
            return style && style.visibility !== 'hidden' && style.display !== 'none' && element.getClientRects().length > 0;
          };
          const subjectText = (container) => {
            if (!container) return '';
            const selectors = [
              '[data-region="post-subject"]',
              '.forum-post-core-subject',
              '.subject h3',
              '.subject h4',
              'h3.subject',
              'h4.subject',
              '.subject a[href*="discuss.php"]',
              'h3',
              'h4',
              '.subject'
            ];
            for (const selector of selectors) {
              const element = container.querySelector(selector);
              const text = normalize(
                element && (element.innerText || element.textContent)
              );
              if (text) return text;
            }
            return '';
          };
          return Array.from(
            document.querySelectorAll('a[href*="reply="]')
          ).filter((anchor) => visible(anchor)).map((anchor) => {
            const href = anchor.href || anchor.getAttribute('href') || '';
            let postId = '';
            try {
              const url = new URL(href, window.location.href);
              if (url.pathname !== '/mod/forum/post.php') return null;
              postId = url.searchParams.get('reply') || '';
            } catch (_error) {
              postId = '';
            }
            if (!/^\\d+$/.test(postId)) return null;
            const container =
              anchor.closest('[data-content="forum-post"]') ||
              anchor.closest('[data-region="forum-post"]') ||
              anchor.closest('article[id^="p"]') ||
              anchor.closest('.forumpost') ||
              anchor.closest('[data-post-id]') ||
              anchor.closest('[data-region="post"]') ||
              anchor.closest('[id^="p"]') ||
              anchor.closest('.forum-post-container') ||
              anchor.closest('li');
            return {
              href: new URL(href, window.location.href).href,
              post_id: postId,
              subject: subjectText(container)
            };
          }).filter(Boolean);
        }
        """
    )
    candidates: list[dict[str, str]] = []
    seen_post_ids: set[str] = set()
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, dict):
            continue
        href = first_text(value.get("href"))
        post_id = first_text(value.get("post_id"))
        subject = first_text(value.get("subject"))
        if not href or not post_id.isdigit():
            continue
        if post_id in seen_post_ids:
            continue
        seen_post_ids.add(post_id)
        candidates.append(
            {"href": href, "post_id": post_id, "subject": subject}
        )

    match_basis = ""
    if parent_post_id:
        matches = [
            item
            for item in candidates
            if item["post_id"] == parent_post_id
        ]
        match_basis = "approved_parent_post_id"
    else:
        expected_title = re.sub(
            r"\s+",
            " ",
            str(discussion_title or "").strip(),
        ).casefold()
        matches = [
            item
            for item in candidates
            if re.sub(r"\s+", " ", item["subject"].strip()).casefold()
            == expected_title
        ]
        match_basis = "exact_post_subject"
        if not matches and len(candidates) == 1:
            matches = list(candidates)
            match_basis = "only_reply_control_on_verified_discussion"

    if len(matches) == 1:
        return {
            "match_count": 1,
            "href": matches[0]["href"],
            "post_id": matches[0]["post_id"],
            "match_basis": match_basis,
        }
    return {
        "match_count": len(matches),
        "candidate_count": len(candidates),
        "href": "",
        "post_id": "",
        "match_basis": match_basis,
    }


def forum_reply_matching_post_ids(page, *, subject: str, message: str) -> list[str]:
    values = page.evaluate(
        """
        ({expectedSubject, expectedMessage}) => {
          const normalize = (text) => String(text || '').replace(/\\s+/g, ' ').trim().toLowerCase();
          const htmlToText = (html) => {
            const holder = document.createElement('div');
            holder.innerHTML = String(html || '');
            return normalize(holder.innerText || holder.textContent || '');
          };
          const subjectTarget = normalize(expectedSubject);
          const messageTarget = htmlToText(expectedMessage);
          const postId = (element) => {
            const explicit = element.getAttribute('data-post-id') || element.dataset?.postId || '';
            if (/^\\d+$/.test(explicit)) return explicit;
            const own = String(element.id || '').match(/^p(\\d+)$/);
            if (own) return own[1];
            const anchor = element.querySelector('a[href*="#p"], a[id^="p"]');
            const token = (anchor && (anchor.getAttribute('href') || anchor.id)) || '';
            const match = token.match(/(?:#|^)p(\\d+)/);
            return match ? match[1] : '';
          };
          const candidates = Array.from(document.querySelectorAll(
            'article[id^="p"], div.forumpost[id^="p"], [data-post-id], [data-region="post"]'
          ));
          const seen = new Set();
          return candidates.map((element) => {
            const id = postId(element);
            const subjectElement = element.querySelector(
              '.forum-post-core-subject, [data-region="post-subject"], '
              + '.subject h3, .subject h4, h3.subject, h4.subject, '
              + '.subject, h3, h4'
            );
            const bodyElement = element.querySelector(
              '.post-content-container, [data-region="post-content"], '
              + '.post-content, .posting, .content .posting'
            );
            return {
              id,
              subject: normalize(
                (subjectElement && (subjectElement.innerText || subjectElement.textContent)) || ''
              ),
              message: normalize(
                (bodyElement && (bodyElement.innerText || bodyElement.textContent)) || ''
              )
            };
          }).filter((item) => {
            if (!item.id || seen.has(item.id)) return false;
            seen.add(item.id);
            return item.subject === subjectTarget && item.message === messageTarget;
          }).map((item) => item.id);
        }
        """,
        {"expectedSubject": subject, "expectedMessage": message},
    )
    return [
        str(value)
        for value in values
        if str(value or "").isdigit()
    ] if isinstance(values, list) else []


def forum_reply_discussion_identity(page) -> dict:
    value = page.evaluate(
        """
        () => {
          const idsFromLinks = (selector, path, key) => {
            const values = new Set();
            for (const anchor of document.querySelectorAll(selector)) {
              try {
                const url = new URL(
                  anchor.href || anchor.getAttribute('href') || '',
                  window.location.href
                );
                if (url.pathname !== path) continue;
                const value = url.searchParams.get(key) || '';
                if (/^\\d+$/.test(value)) values.add(value);
              } catch (_error) {
                // Ignore malformed links; exact identity checks below fail closed.
              }
            }
            return Array.from(values);
          };
          let discussionId = '';
          try {
            const current = new URL(window.location.href);
            if (current.pathname === '/mod/forum/discuss.php') {
              discussionId = current.searchParams.get('d') || '';
            }
          } catch (_error) {
            discussionId = '';
          }
          return {
            discussion_id: discussionId,
            forum_cmids: idsFromLinks(
              'a[href*="/mod/forum/view.php"][href*="id="]',
              '/mod/forum/view.php',
              'id'
            ),
            course_ids: idsFromLinks(
              'a[href*="/course/view.php"][href*="id="]',
              '/course/view.php',
              'id'
            )
          };
        }
        """
    )
    return dict(value) if isinstance(value, dict) else {
        "discussion_id": "",
        "forum_cmids": [],
        "course_ids": [],
    }


def assert_forum_reply_discussion_identity(
    page,
    *,
    discussion_id: str,
    forum_cmid: str,
) -> None:
    identity = forum_reply_discussion_identity(page)
    current_discussion_id = first_text(identity.get("discussion_id"))
    forum_cmids = {
        str(value)
        for value in identity.get("forum_cmids", [])
        if str(value or "").isdigit()
    }
    course_ids = {
        str(value)
        for value in identity.get("course_ids", [])
        if str(value or "").isdigit()
    }
    if current_discussion_id != discussion_id:
        raise RuntimeError(
            "Forum reply page identity does not match the approved discussion id."
        )
    if not forum_cmid.isdigit() or forum_cmid not in forum_cmids:
        raise RuntimeError(
            "Forum reply page does not link back to the exact approved Forum activity."
        )
    if SANDBOX_COURSE_ID not in course_ids:
        raise RuntimeError(
            f"Forum reply page does not link back to sandbox course {SANDBOX_COURSE_ID}."
        )


def fill_forum_reply_form(page, *, subject: str, message: str) -> None:
    subject_input = page.locator("#id_subject, input[name='subject']")
    if subject_input.count() != 1:
        raise RuntimeError(
            "Forum reply form did not expose one unique Subject input."
        )
    subject_input.fill(subject)

    editor = page.locator(
        "#id_messageeditable, "
        "[contenteditable='true'][data-fieldtype='editor']"
    )
    textarea = page.locator(
        "textarea[name='message[text]'], textarea#id_message"
    )
    if editor.count() > 1 or textarea.count() > 1:
        raise RuntimeError("Forum reply form exposed ambiguous message editors.")
    if editor.count() == 0 and textarea.count() == 0:
        raise RuntimeError("Forum reply form did not expose a message editor.")
    if editor.count() == 1:
        editor.evaluate(
            """
            (element, html) => {
              element.innerHTML = String(html || '');
              element.dispatchEvent(new InputEvent('input', {
                bubbles: true,
                inputType: 'insertText',
                data: ''
              }));
              element.dispatchEvent(new Event('change', {bubbles: true}));
            }
            """,
            message,
        )
    if textarea.count() == 1:
        try:
            textarea.fill(message)
        except Exception:
            textarea.evaluate(
                """
                (element, value) => {
                  element.value = String(value || '');
                  element.dispatchEvent(new Event('input', {bubbles: true}));
                  element.dispatchEvent(new Event('change', {bubbles: true}));
                }
                """,
                message,
            )
    if str(subject_input.input_value() or "").strip() != subject:
        raise RuntimeError("Forum reply Subject field did not retain the approved value.")


def submit_forum_reply_form(page) -> str:
    submit = page.locator(
        "#id_submitbutton, "
        "button[type='submit'][name='submitbutton'], "
        "input[type='submit'][name='submitbutton']"
    )
    if submit.count() != 1:
        raise RuntimeError(
            f"Forum reply form expected one submit control, found {submit.count()}."
        )
    label = str(
        submit.get_attribute("value")
        or submit.inner_text()
        or "Post to forum"
    ).strip()
    submit.click()
    return label


def execute_forum_post_reply_operation(
    materialized: dict,
    moodle_env: dict[str, str],
    moodle_page,
    timeout_error_type,
) -> dict:
    operation_id = first_text(
        materialized.get("operation_id"),
        "SANDBOX-FORUM-POST-REPLY",
    )
    discussion_id = first_text(
        materialized.get("target_discussion_id"),
        materialized.get("discussion_id"),
    )
    discussion_title = first_text(
        materialized.get("target_discussion_title"),
        materialized.get("discussion_title"),
    )
    forum_cmid = first_text(
        materialized.get("target_forum_cmid"),
        materialized.get("target_module_id"),
    )
    parent_post_id = first_text(
        materialized.get("target_post_id"),
        materialized.get("parent_post_id"),
    )
    subject = first_text(materialized.get("subject"))
    message = first_text(materialized.get("message"), materialized.get("body"))
    report_dir = REPORTS / f"forum_post_reply_{stamp()}_{safe_name(operation_id)}"
    report_dir.mkdir(parents=True, exist_ok=True)
    result: dict = {
        "ok": False,
        "status": "STARTED",
        "operation_id": operation_id,
        "target_object": first_text(
            materialized.get("target_object"),
            discussion_title,
            "Forum discussion",
        ),
        "target_forum_name": first_text(
            materialized.get("target_forum_name")
        ),
        "target_discussion_id": discussion_id,
        "target_discussion_title": discussion_title,
        "saves_count": 0,
        "report_dir": str(report_dir),
    }
    try:
        if str(materialized.get("target_course_id") or "") != SANDBOX_COURSE_ID:
            raise RuntimeError(
                f"Forum reply is restricted to sandbox course {SANDBOX_COURSE_ID}."
            )
        if not discussion_id.isdigit():
            raise RuntimeError("Forum reply requires a numeric discussion id.")
        if not discussion_title:
            raise RuntimeError("Forum reply requires an exact discussion title.")
        if not forum_cmid.isdigit():
            raise RuntimeError(
                "Forum reply requires the exact current-course Forum activity id."
            )
        if not subject or not message:
            raise RuntimeError(
                "Forum reply requires the approved subject and message body."
            )
        target_url = forum_post_reply_target_url(materialized)
        parsed_target = urlparse(target_url)
        if parsed_target.path != "/mod/forum/discuss.php":
            raise RuntimeError(
                "Forum reply target URL must be an exact Moodle discussion URL."
            )
        target_query_id = first_text(
            *(parse_qs(parsed_target.query).get("d") or [])
        )
        if target_query_id != discussion_id:
            raise RuntimeError(
                "Approved Forum discussion id and URL do not identify the same target."
            )

        goto_with_retry(
            moodle_page,
            target_url,
            wait_until="domcontentloaded",
            timeout=60000,
        )
        recover_moodle_login_for_target(
            moodle_page,
            target_url,
            moodle_env,
            context="Forum reply preflight",
        )
        save_screenshot(moodle_page, report_dir, "01_discussion_preflight.png")
        write_result(
            report_dir,
            "01_discussion_preflight.json",
            page_summary(moodle_page),
        )
        if looks_like_login(moodle_page):
            raise RuntimeError("Moodle requested login during Forum reply preflight.")
        assert_forum_reply_discussion_identity(
            moodle_page,
            discussion_id=discussion_id,
            forum_cmid=forum_cmid,
        )
        page_text = visible_text(moodle_page, 16000)
        if not forum_text_contains_target(page_text, discussion_title):
            raise RuntimeError(
                f"Forum discussion page did not show approved title {discussion_title!r}."
            )

        before_ids = forum_reply_matching_post_ids(
            moodle_page,
            subject=subject,
            message=message,
        )
        if len(before_ids) == 1 and not parent_post_id:
            existing_post_id = before_ids[0]
            result.update(
                {
                    "ok": True,
                    "status": "PASS",
                    "already_present": True,
                    "new_post_id": existing_post_id,
                    "target_discussion_url": target_url,
                    "verification": (
                        "One exact matching reply was already present in the "
                        "approved discussion; no duplicate was submitted."
                    ),
                    "course_verify_url": SANDBOX_COURSE_URL,
                    "final_moodle_state": {
                        "course_url": SANDBOX_COURSE_URL,
                        "discussion_url": target_url,
                        "new_post_id": existing_post_id,
                        "already_present": True,
                    },
                }
            )
            write_result(report_dir, "result.json", result)
            return result
        if before_ids:
            raise RuntimeError(
                "The approved discussion already contains exact matching "
                "reply content, but it did not resolve to one reusable "
                "discussion-level result; refusing to create a duplicate."
            )
        reply_target = find_forum_reply_target(
            moodle_page,
            parent_post_id=parent_post_id,
            discussion_title=discussion_title,
        )
        match_count = int(reply_target.get("match_count") or 0)
        if match_count != 1:
            raise RuntimeError(
                "Forum reply preflight requires one exact Reply control for the "
                f"approved root/parent post; found {match_count}."
            )
        reply_href = first_text(reply_target.get("href"))
        resolved_parent_post_id = first_text(reply_target.get("post_id"))
        if not is_sandbox_moodle_page_url(reply_href):
            raise RuntimeError(f"Refusing non-sandbox Forum Reply link: {reply_href}")
        parsed_reply = urlparse(reply_href)
        reply_query_id = first_text(
            *(parse_qs(parsed_reply.query).get("reply") or [])
        )
        if (
            parsed_reply.path != "/mod/forum/post.php"
            or not reply_query_id.isdigit()
            or reply_query_id != resolved_parent_post_id
        ):
            raise RuntimeError(
                "Resolved Forum Reply control did not contain one numeric parent post id."
            )
        if parent_post_id and resolved_parent_post_id != parent_post_id:
            raise RuntimeError(
                "Resolved Forum Reply control does not match the approved parent post id."
            )

        goto_with_retry(
            moodle_page,
            reply_href,
            wait_until="domcontentloaded",
            timeout=60000,
        )
        recover_moodle_login_for_target(
            moodle_page,
            reply_href,
            moodle_env,
            context="Forum reply form",
        )
        if looks_like_login(moodle_page):
            raise RuntimeError("Moodle requested login on the Forum reply form.")
        reply_page_text = visible_text(moodle_page, 16000)
        if not forum_text_contains_target(reply_page_text, discussion_title):
            raise RuntimeError(
                "Forum reply form did not show the approved discussion title."
            )
        fill_forum_reply_form(
            moodle_page,
            subject=subject,
            message=message,
        )
        save_screenshot(moodle_page, report_dir, "02_reply_filled.png")
        write_result(
            report_dir,
            "02_reply_filled.json",
            page_summary(moodle_page),
        )

        submit_label = submit_forum_reply_form(moodle_page)
        result["saves_count"] = 1
        result["write_action_taken"] = True
        result["submit_label"] = submit_label
        try:
            moodle_page.wait_for_load_state("domcontentloaded", timeout=45000)
        except timeout_error_type:
            moodle_page.wait_for_timeout(2000)

        goto_with_retry(
            moodle_page,
            target_url,
            wait_until="domcontentloaded",
            timeout=60000,
        )
        recover_moodle_login_for_target(
            moodle_page,
            target_url,
            moodle_env,
            context="Forum reply readback",
        )
        if looks_like_login(moodle_page):
            raise RuntimeError("Moodle requested login during Forum reply readback.")
        assert_forum_reply_discussion_identity(
            moodle_page,
            discussion_id=discussion_id,
            forum_cmid=forum_cmid,
        )
        save_screenshot(moodle_page, report_dir, "03_reply_readback.png")
        write_result(
            report_dir,
            "03_reply_readback.json",
            page_summary(moodle_page),
        )
        after_ids = forum_reply_matching_post_ids(
            moodle_page,
            subject=subject,
            message=message,
        )
        new_ids = [post_id for post_id in after_ids if post_id not in before_ids]
        if len(new_ids) != 1:
            raise RuntimeError(
                "Forum reply readback expected exactly one new matching post in "
                f"the approved discussion; found {len(new_ids)}."
            )
        result.update(
            {
                "ok": True,
                "status": "PASS",
                "new_post_id": new_ids[0],
                "parent_post_id": resolved_parent_post_id,
                "target_discussion_url": target_url,
                "verification": (
                    "Exactly one new matching reply was read back in the "
                    "approved discussion."
                ),
                "course_verify_url": SANDBOX_COURSE_URL,
                "final_moodle_state": {
                    "course_url": SANDBOX_COURSE_URL,
                    "discussion_url": target_url,
                    "new_post_id": new_ids[0],
                },
            }
        )
    except Exception as exc:
        result.update(
            {
                "ok": False,
                "status": "FAIL" if result.get("saves_count") else "BLOCKED",
                "blocker": str(exc),
                "current_url": getattr(moodle_page, "url", ""),
            }
        )
        try:
            save_screenshot(moodle_page, report_dir, "error_state.png")
        except Exception:
            pass
    write_result(report_dir, "result.json", result)
    return result


def execute_local_browser_operation(materialized: dict, moodle_env: dict[str, str], moodle_page, timeout_error_type) -> dict:
    ensure_moodle_authenticated(moodle_page, moodle_env)
    kind = materialized.get("operation_kind")
    if kind == "browser_course_file_resource_upload":
        return execute_course_file_resource_upload_operation(
            materialized,
            moodle_env,
            moodle_page,
            timeout_error_type,
        )
    if kind == "browser_course_file_resource_delete":
        return execute_delete_operation(
            materialized,
            moodle_env,
            moodle_page,
            timeout_error_type,
        )
    if kind == "browser_module_delete":
        return execute_module_delete_operation(
            materialized,
            moodle_env,
            moodle_page,
            timeout_error_type,
        )
    if kind == "browser_group_signup_activity":
        return execute_group_signup_activity_operation(
            materialized,
            moodle_env,
            moodle_page,
            timeout_error_type,
        )
    if kind == "browser_group_membership":
        report_dir = (
            REPORTS
            / f"group_membership_{stamp()}_{safe_name(str(materialized.get('approval_run_id') or 'run'))}"
            / safe_name(str(materialized.get("operation_id") or "membership"))
        )
        result = execute_group_membership_operation(
            moodle_page,
            materialized,
            report_dir=report_dir,
        )
        result["report_dir"] = str(report_dir)
        return result
    if kind == "browser_assignment_activity":
        return execute_assignment_activity_operation(
            materialized,
            moodle_env,
            moodle_page,
            timeout_error_type,
        )
    if kind == "browser_forum_activity":
        return execute_forum_activity_operation(
            materialized,
            moodle_env,
            moodle_page,
            timeout_error_type,
        )
    if kind == "browser_forum_post_reply":
        return execute_forum_post_reply_operation(
            materialized,
            moodle_env,
            moodle_page,
            timeout_error_type,
        )
    if kind == "browser_forum_post_delete":
        return execute_forum_post_delete_operation(
            materialized,
            moodle_env,
            moodle_page,
            timeout_error_type,
        )
    if kind == "browser_forum_reply_delete":
        return execute_forum_reply_delete_operation(
            materialized,
            moodle_env,
            moodle_page,
            timeout_error_type,
        )
    if kind == "browser_module_visibility":
        return execute_module_visibility_operation(
            materialized,
            moodle_env,
            moodle_page,
            timeout_error_type,
        )
    if kind == "browser_module_access_time":
        return execute_module_access_time_operation(
            materialized,
            moodle_env,
            moodle_page,
            timeout_error_type,
        )
    if kind == "direct_title_edit":
        return execute_module_title_operation(
            materialized,
            moodle_env,
            moodle_page,
            timeout_error_type,
        )
    if kind == "browser_section_rename":
        return execute_section_rename_operation(
            materialized,
            moodle_env,
            moodle_page,
            timeout_error_type,
        )
    if kind == "browser_moodle_task":
        return execute_browser_moodle_task(
            materialized,
            moodle_env,
            moodle_page,
            timeout_error_type,
        )
    if kind == COURSE_REFRESH_BATCH_UPLOAD_KIND:
        return execute_course_refresh_batch_upload(
            materialized,
            moodle_env,
            moodle_page,
            timeout_error_type,
        )
    raise RuntimeError(f"Unsupported local browser operation: {kind}")


def execute_approved_api_continuation(
    materialized: dict,
    env: dict[str, str],
    *,
    operation_index: int,
    operation_count: int,
) -> dict:
    op_env = dict(env)
    env_overrides = materialized.get("env_overrides") if isinstance(materialized.get("env_overrides"), dict) else {}
    op_env.update({str(key): "" if value is None else str(value) for key, value in env_overrides.items()})
    op_env.update(
        {
            "APPROVAL_RUN_ID": str(materialized.get("approval_run_id") or ""),
            "MOODLE_COURSE_ID": SANDBOX_COURSE_ID,
            "MOODLE_COURSE_URL": SANDBOX_COURSE_URL,
            "MOODLE_OPERATION_KIND": str(materialized.get("operation_kind") or ""),
            "MOODLE_EXECUTION_MODE": str(materialized.get("execution_mode") or "api_action"),
            "MOODLE_OPERATION_ID": str(materialized.get("operation_id") or ""),
            "MOODLE_MANIFEST_OPERATION_JSON": json.dumps(materialized, ensure_ascii=False),
            "COURSE_AGENT_OPERATION_INDEX": str(operation_index),
            "COURSE_AGENT_OPERATION_COUNT": str(operation_count),
        }
    )
    result = execute_after_approval(op_env)
    if str(result.get("status") or "") == "moodle_agent_browser_action_pending":
        return {
            "ok": False,
            "status": "BLOCKED",
            "operation_id": materialized.get("operation_id", ""),
            "target_object": first_text(materialized.get("target_object"), materialized.get("approval_item")),
            "saves_count": 0,
            "blocker": "An approved continuation unexpectedly requested a second browser handoff.",
        }
    result["ok"] = bool(
        result.get("ok") is True
        or str(result.get("status") or "").casefold().endswith("_verified")
    )
    if not result["ok"]:
        api_result = result.get("api_result") if isinstance(result.get("api_result"), dict) else {}
        result.setdefault(
            "blocker",
            first_text(
                result.get("reason"),
                result.get("error"),
                api_result.get("reason"),
                api_result.get("error"),
                "The approved Moodle API operation failed.",
            ),
        )
    result.setdefault("saves_count", 1 if result["ok"] else 0)
    result.setdefault(
        "target_object",
        first_text(materialized.get("target_object"), materialized.get("approval_item")),
    )
    for key in (
        "target_module_id",
        "target_forum_id",
        "target_forum_name",
        "target_forum_url",
        "subject",
    ):
        if materialized.get(key) not in (None, ""):
            result.setdefault(key, materialized[key])
    return result


def aggregate_local_browser_results(results: list[dict], prior_result: dict | None = None) -> dict:
    if not results:
        return {
            "ok": False,
            "operation_id": "",
            "saves_count": 0,
            "blocker": "No local Moodle browser operation was executed.",
        }
    prior_result = prior_result if isinstance(prior_result, dict) else {}
    prior_items = [
        item
        for item in prior_result.get("results", []) or []
        if isinstance(item, dict)
        and str(item.get("status") or "") != "moodle_agent_browser_action_pending"
    ]
    ok = all(bool(result.get("ok")) for result in results)
    saves_count = int(prior_result.get("saves_count", 0) or 0) + sum(
        int(result.get("saves_count", 0) or 0) for result in results
    )
    failed = next((result for result in results if not result.get("ok")), {})
    local_target_labels = [
        first_text(result.get("target_object"), result.get("resource_name"), result.get("operation_id"))
        for result in results
    ]
    prior_target_labels = [
        first_text(item.get("target_object"), item.get("resource_name"), item.get("operation_id"))
        for item in prior_items
    ]
    target_labels = prior_target_labels + local_target_labels
    evidence_results = [
        {
            "operation_id": item.get("operation_id", ""),
            "course_agent_operation_index": item.get("course_agent_operation_index", ""),
            "status": item.get("status", ""),
            "ok": bool(
                item.get("ok") is True
                or str(item.get("status") or "").casefold().endswith("_verified")
            ),
            "target_object": first_text(item.get("target_object"), item.get("resource_name")),
            "target_module_id": item.get("target_module_id", ""),
            "target_forum_id": item.get("target_forum_id", ""),
            "target_forum_name": item.get("target_forum_name", ""),
            "target_forum_url": item.get("target_forum_url", ""),
            "target_discussion_id": item.get("target_discussion_id", ""),
            "target_discussion_url": item.get("target_discussion_url", ""),
            "target_discussion_title": item.get("target_discussion_title", ""),
            "target_post_id": item.get("target_post_id", ""),
            "target_post_url": item.get("target_post_url", ""),
            "new_post_id": item.get("new_post_id", ""),
            "already_present": bool(item.get("already_present")),
            "subject": item.get("subject", ""),
            "api_result": (
                item.get("api_result")
                if isinstance(item.get("api_result"), dict)
                else {}
            ),
            "blocker": first_text(item.get("blocker"), item.get("error"), item.get("reason"), item.get("note")),
        }
        for item in prior_items
    ]
    evidence_results.extend(
        {
            "operation_id": result.get("operation_id", ""),
            "course_agent_operation_index": result.get("course_agent_operation_index", ""),
            "status": result.get("status", ""),
            "ok": bool(result.get("ok")),
            "target_object": first_text(result.get("target_object"), result.get("resource_name")),
            "target_module_id": result.get("target_module_id", ""),
            "target_forum_id": result.get("target_forum_id", ""),
            "target_forum_name": result.get("target_forum_name", ""),
            "target_forum_url": result.get("target_forum_url", ""),
            "target_discussion_id": result.get("target_discussion_id", ""),
            "target_discussion_url": result.get("target_discussion_url", ""),
            "target_discussion_title": result.get("target_discussion_title", ""),
            "target_post_id": result.get("target_post_id", ""),
            "target_post_url": result.get("target_post_url", ""),
            "new_post_id": result.get("new_post_id", ""),
            "already_present": bool(result.get("already_present")),
            "subject": result.get("subject", ""),
            "api_result": (
                result.get("api_result")
                if isinstance(result.get("api_result"), dict)
                else {}
            ),
            "report_dir": result.get("report_dir", ""),
            "membership_verified": bool(result.get("membership_verified")),
            "membership_action": result.get("membership_action", ""),
            "source_group_name": result.get("source_group_name", ""),
            "target_group_name": result.get("target_group_name", ""),
            "source_count_delta": int(result.get("source_count_delta", 0) or 0),
            "target_count_delta": int(result.get("target_count_delta", 0) or 0),
            "rollback_performed": bool(result.get("rollback_performed")),
            "blocker": first_text(
                result.get("blocker"),
                result.get("error"),
                result.get("reason"),
                (
                    result.get("api_result", {}).get("reason")
                    if isinstance(result.get("api_result"), dict)
                    else ""
                ),
                (
                    result.get("api_result", {}).get("error")
                    if isinstance(result.get("api_result"), dict)
                    else ""
                ),
                result.get("note"),
            ),
        }
        for result in results
    )
    return {
        "ok": ok,
        "operation_id": results[0].get("operation_id", ""),
        "saves_count": saves_count,
        "target_object": f"{len(target_labels)} Moodle item(s)" if len(target_labels) > 1 else target_labels[0],
        "resource_name": f"{len(target_labels)} Moodle item(s)" if len(target_labels) > 1 else target_labels[0],
        "final_moodle_state": {
            "course_url": SANDBOX_COURSE_URL,
            "operation_count": len(evidence_results),
            "verified_count": sum(1 for item in evidence_results if item.get("ok")),
            "targets": target_labels,
            "results": evidence_results,
        },
        "note": (
            f"Executed and verified {len(evidence_results)} approved Moodle operation(s)."
            if ok
            else first_text(
                failed.get("blocker"),
                failed.get("error"),
                failed.get("reason"),
                (
                    failed.get("api_result", {}).get("reason")
                    if isinstance(failed.get("api_result"), dict)
                    else ""
                ),
                (
                    failed.get("api_result", {}).get("error")
                    if isinstance(failed.get("api_result"), dict)
                    else ""
                ),
                failed.get("note"),
                "An approved Moodle operation failed.",
            )
        ),
    }


def post_local_moodle_result(base_url: str, env: dict[str, str], run_id: str, result: dict) -> dict:
    status = "PASS" if result.get("ok") else "BLOCKED"
    payload = {
        "agent_name": env.get("COURSE_AGENT_NAME", "codex"),
        "run_id": run_id,
        "operation_id": result.get("operation_id", ""),
        "status": status,
        "saves_count": int(result.get("saves_count", 0)),
        "final_title": first_text(result.get("resource_name"), result.get("target_object")),
        "final_moodle_state": result.get(
            "final_moodle_state",
            {
                "course_url": SANDBOX_COURSE_URL,
                "target_module_id": result.get("target_module_id", ""),
                "deleted": bool(result.get("ok")),
            },
        ),
        "results": (result.get("final_moodle_state") or {}).get("results", []),
        "evidence_report": str(Path(result.get("report_dir", "")) / "result.json") if result.get("report_dir") else "",
        "note": result.get("note", result.get("blocker", "")),
    }
    return post_json(f"{base_url}/agent/moodle-result", payload)


def state_has_pending_unsent_approval(state: dict) -> bool:
    if state.get("approval_request_sent_at"):
        return False
    if state.get("status") not in {
        "course_operation_manifest_ready",
        "agent_whatsapp_send_failed",
        "agent_whatsapp_sending_request",
        "agent_whatsapp_draft_ready",
    }:
        return False
    manifest = state.get("course_operation_manifest")
    return isinstance(manifest, dict) and bool(manifest.get("operations"))


def state_has_sent_waiting_approval(state: dict) -> bool:
    if not state.get("approval_request_sent_at") or state.get("approval_reply_observed_at"):
        return False
    if state.get("moodle_result") or state.get("course_agent_result_recorded_at"):
        return False
    manifest = state.get("course_operation_manifest")
    return isinstance(manifest, dict) and bool(manifest.get("operations"))


def outbound_message(
    message: str | OutboundMessage,
    *,
    purpose: str = "terminal",
    action_kind: str = "",
    requester: str = "",
    chat_type: str = "",
    run_id: str = "",
) -> OutboundMessage:
    if isinstance(message, OutboundMessage):
        return message
    context = ACTIVE_STAFF_PROMPT_CONTEXT
    return OutboundMessage(
        body=str(message),
        purpose=purpose,
        action_kind=action_kind,
        requester=requester or (context.requester if context else ""),
        chat_type=chat_type or (context.chat_type if context else ""),
        run_id=run_id,
    )


def send_course_agent_message(
    page,
    chat: str,
    message: str | OutboundMessage,
    *,
    purpose: str = "terminal",
    action_kind: str = "",
    requester: str = "",
    chat_type: str = "",
    run_id: str = "",
) -> tuple[bool, str]:
    envelope = outbound_message(
        message,
        purpose=purpose,
        action_kind=action_kind,
        requester=requester,
        chat_type=chat_type,
        run_id=run_id,
    )
    try:
        send_message_to_chat(page, chat, envelope)
    except Exception as exc:
        return False, str(exc)
    return True, ""


def send_read_only_result_and_record(
    page,
    chat: str,
    message: str,
    *,
    base_url: str,
    run_id: str,
    detail: str,
    evidence: str,
) -> bool:
    ok, send_error = send_course_agent_message(page, chat, message, run_id=run_id)
    post_json(
        f"{base_url}/agent/progress",
        {
            "event": "approval_report_posted" if ok else "approval_report_send_failed",
            "run_id": run_id,
            "agent_name": "course-agent-app",
            "chat_name": chat,
            "detail": detail if ok else f"Read-only result was not sent: {send_error}",
            "evidence": evidence,
        },
    )
    return ok


def whatsapp_outgoing_message_visible(
    page,
    message: str,
    *,
    baseline_keys: set[str] | None = None,
) -> bool:
    expected = normalize_message_text(message)
    if not expected:
        return False
    baseline = baseline_keys or set()
    try:
        records = message_records(page)
    except Exception:
        return False
    for record in records:
        if record_key(record) in baseline or record_direction(record) != "outgoing":
            continue
        observed = normalize_message_text(record.get("text", ""))
        if observed == expected or expected in observed:
            return True
    return False


def expand_collapsed_outgoing_messages(page) -> int:
    try:
        expanded = int(
            page.evaluate(
                """
                () => {
                  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                  let clicked = 0;
                  for (const root of Array.from(document.querySelectorAll('.message-out'))) {
                    const controls = Array.from(root.querySelectorAll('button, [role="button"], span'));
                    const readMore = controls.find((control) =>
                      /^Read more$/i.test(normalize(control.innerText || control.textContent || ''))
                    );
                    if (!readMore) continue;
                    readMore.click();
                    clicked += 1;
                  }
                  return clicked;
                }
                """
            )
        )
    except Exception:
        return 0
    if expanded:
        page.wait_for_timeout(300)
    return expanded


def outgoing_message_delivery_visible(
    page,
    envelope: OutboundMessage,
    baseline_keys: set[str],
) -> bool:
    try:
        scroll_chat_to_bottom(page)
        expand_collapsed_outgoing_messages(page)
        records = message_records(page)
    except Exception:
        return False

    prepared_body = whatsapp_safe_compose_text(envelope.body)
    run_token_match = re.search(
        r"\bRUN-[0-9]{6}-[A-Z0-9]{4,}\b",
        envelope.run_id or envelope.body,
    )
    run_token = run_token_match.group(0) if run_token_match else ""
    for record in records:
        if record_direction(record) != "outgoing" or record_key(record) in baseline_keys:
            continue
        observed = str(record.get("text") or "")
        if run_token and run_token in observed:
            return True
        if multiline_message_matches(observed, prepared_body):
            return True
    return False


def send_message_to_chat(page, chat: str, message: str | OutboundMessage) -> dict:
    envelope = message if isinstance(message, OutboundMessage) else OutboundMessage(str(message))
    last_error: Exception | None = None
    baseline_keys: set[str] | None = None
    for attempt in range(2):
        try:
            print(f"WhatsApp send attempt {attempt + 1}: ensuring chat `{chat}`.")
            ensure_chat_compose_ready(page, chat)
            if baseline_keys is None:
                baseline_keys = {record_key(record) for record in message_records(page)}
            print(f"WhatsApp send attempt {attempt + 1}: composing message.")
            mention_name = envelope.requester if envelope.purpose == "action_required" else ""
            delivery = send_message(
                page,
                envelope.body,
                mention_name=mention_name,
                chat_type=envelope.chat_type,
            )
            print(f"WhatsApp send attempt {attempt + 1}: sent.")
            return delivery
        except Exception as exc:
            last_error = exc
            print(f"WhatsApp send attempt {attempt + 1} failed: {exc}")
            delivery_uncertain = isinstance(exc, WhatsAppSendUnconfirmedError)
            if outgoing_message_delivery_visible(page, envelope, baseline_keys or set()):
                print("WhatsApp message was found after the send verifier timed out; suppressing retry.")
                return {"delivery_verification": "recovered_after_timeout"}
            if attempt:
                break
            try:
                print("Reloading WhatsApp before retrying send.")
                page.reload(wait_until="domcontentloaded", timeout=60000)
                wait_for_whatsapp_ready(page, 60)
                open_chat(page, chat)
                if outgoing_message_delivery_visible(page, envelope, baseline_keys or set()):
                    print("WhatsApp message was found after reload; suppressing retry.")
                    return {"delivery_verification": "recovered_after_reload"}
            except Exception:
                pass
            if delivery_uncertain:
                raise RuntimeError(
                    "WhatsApp delivery remained unconfirmed after the send control was used; "
                    "automatic retry was suppressed to prevent a duplicate message."
                ) from exc
    raise RuntimeError(f"Could not send WhatsApp message to {chat!r}: {last_error}")


def refresh_preview_attachment_paths(result: dict) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for path_text in result.get("attachments") or []:
        text = str(path_text or "").strip()
        if not text:
            continue
        path = resolve_preview_local_path(text)
        key = str(path.resolve(strict=False))
        if key in seen or not path.exists():
            continue
        seen.add(key)
        paths.append(path)
    return paths


def human_refresh_preview_reason(result: dict) -> str:
    status = str(result.get("status") or "BLOCKED")
    explicit = first_text(result.get("user_reason"), result.get("human_reason"), result.get("staff_reason"))
    if explicit:
        sanitized_explicit = strip_internal_attachment_context(explicit)
        if any(token in sanitized_explicit for token in ("{", "}", "[{", "operation_kind", "env_overrides")):
            structured_reason = date_plan_structured_staff_reason(result)
            if structured_reason:
                return shorten_approval_value(structured_reason, 260)
            return "I could not safely refresh this file from the current attachment and reference pack."
        return shorten_approval_value(sanitized_explicit, 260)
    raw_reason = first_text(result.get("blocker"), result.get("note"), result.get("error"))
    audit = result.get("audit") if isinstance(result.get("audit"), dict) else {}
    stale_hits = audit.get("stale_date_hits") if isinstance(audit.get("stale_date_hits"), list) else []
    trace_hits = audit.get("student_visible_trace_hits") if isinstance(audit.get("student_visible_trace_hits"), list) else []
    if status == "NEEDS_REVIEW":
        details = []
        if stale_hits:
            details.append("dates to check: " + ", ".join(str(item) for item in stale_hits[:4]))
        if trace_hits:
            details.append("text to check: " + ", ".join(str(item) for item in trace_hits[:4]))
        suffix = " " + "; ".join(details) if details else ""
        return shorten_approval_value("I made a refreshed draft, but it needs human review before upload." + suffix, 260)
    if not raw_reason:
        return "I could not safely refresh this file from the current attachment and reference pack."
    sanitized = raw_reason
    sanitized = re.sub(r"refresh_file_preview(?:\.params)?\.[A-Za-z0-9_]+", "the file", sanitized)
    sanitized = re.sub(r"\bparams\.[A-Za-z0-9_]+", "the required file detail", sanitized)
    sanitized = re.sub(r"\b[A-Za-z_]+_path\b", "file path", sanitized)
    sanitized = sanitized.replace("refresh_file_preview", "the file refresh")
    sanitized = re.sub(r"/Users/[^ \n\r\t,;:]+", "the local file", sanitized)
    if any(token in sanitized.lower() for token in ("operation_kind", "env_overrides", "{", "}", "[{")):
        return "I could not safely refresh this file from the current attachment and reference pack."
    return shorten_approval_value(sanitized, 260)


def date_plan_structured_staff_reason(result: dict) -> str:
    date_plan = result.get("date_plan") if isinstance(result.get("date_plan"), dict) else {}
    blockers = date_plan.get("blockers") if isinstance(date_plan.get("blockers"), list) else []
    reasons: list[str] = []
    for item in blockers:
        if not isinstance(item, dict):
            continue
        visible = str(item.get("visible_text") or "").strip()
        reason = str(item.get("reason") or item.get("summary") or "").strip()
        needed = str(item.get("needed_to_unblock") or item.get("next_step") or "").strip()
        pieces: list[str] = []
        if visible:
            pieces.append(f"{visible}:")
        if reason:
            pieces.append(reason)
        if needed:
            pieces.append(f"Needed: {needed}")
        if pieces:
            reasons.append(" ".join(pieces))
    return " ".join(reasons)


def refresh_preview_result_text(run_id: str, raw_task: str, result: dict) -> str:
    status = str(result.get("status") or "BLOCKED")
    source_name = first_text(result.get("source_file_name"), "attached file")
    audit = result.get("audit") if isinstance(result.get("audit"), dict) else {}
    if status == "READY_FOR_REVIEW":
        status_label = "Done"
        change = "I made a refreshed PDF review copy. No Moodle change was made."
        verify = "Local date/trace check passed."
    elif status == "NEEDS_REVIEW":
        status_label = "Waiting"
        change = "I made a refreshed PDF review copy, but it needs human checking before upload."
        verify = "The local check found items for human review."
    else:
        status_label = "Blocked"
        change = "I did not generate a refreshed PDF review copy."
        verify = "No review PDF was generated."
    lines = [
        "File refresh review PDF",
        "",
        f"Status: {status_label}",
        f"Task: {user_visible_task_text(raw_task)}",
        f"File: {source_name}",
        f"Change: {change}",
        f"Verify: {verify}",
    ]
    if int(result.get("replacement_count", 0) or 0):
        lines.append(f"Changes applied: {int(result.get('replacement_count', 0) or 0)} date edit(s).")
    reason = human_refresh_preview_reason(result)
    if reason and status_label != "Done":
        lines.append(f"Reason: {reason}")
    stale_hits = audit.get("stale_date_hits") if isinstance(audit.get("stale_date_hits"), list) else []
    trace_hits = audit.get("student_visible_trace_hits") if isinstance(audit.get("student_visible_trace_hits"), list) else []
    if stale_hits:
        lines.append("Review needed: dates to check: " + ", ".join(str(item) for item in stale_hits[:6]))
    if trace_hits:
        lines.append("Review needed: text to check: " + ", ".join(str(item) for item in trace_hits[:6]))
    if result.get("result_path"):
        lines.append(f"Evidence: {Path(str(result['result_path'])).name}")
    lines.append(f"Run: {run_id}")
    lines.append(f"Next: {refresh_preview_next_step(status_label, result)}")
    return "\n".join(lines)


def refresh_preview_next_step(status_label: str, result: dict) -> str:
    if status_label == "Done":
        return "check the PDF, then reply with revision instructions or ask me to upload the refreshed file."
    if status_label == "Waiting":
        return "check the PDF and reply with revision instructions, or upload an editable source file."
    date_plan = result.get("date_plan") if isinstance(result.get("date_plan"), dict) else {}
    blockers = date_plan.get("blockers") if isinstance(date_plan.get("blockers"), list) else []
    needed_items: list[str] = []
    for item in blockers:
        if not isinstance(item, dict):
            continue
        needed = str(item.get("needed_to_unblock") or item.get("next_step") or "").strip()
        if needed and needed not in needed_items:
            needed_items.append(needed)
    if needed_items:
        return shorten_approval_value("; ".join(needed_items), 220)
    source_name = str(result.get("source_file_name") or "").casefold()
    reason = human_refresh_preview_reason(result).casefold()
    if source_name.endswith(".pdf") or "pdf" in reason:
        return "upload an editable source file, or explicitly allow a PDF-only copy if that is acceptable."
    return "upload an editable source file from the current prompt, or provide the missing date context, then ask me to refresh again."


def send_refresh_preview_result_to_chat(page, chat: str, message: str, result: dict, run_id: str) -> None:
    attachment_paths = refresh_preview_attachment_paths(result)
    envelope = outbound_message(
        message,
        purpose="action_required",
        action_kind="review",
        run_id=run_id,
    )
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            print(f"WhatsApp preview send attempt {attempt + 1}: ensuring chat `{chat}`.")
            ensure_chat_compose_ready(page, chat)
            baseline_counts = document_attachment_counts(page, attachment_paths)
            print(f"WhatsApp preview send attempt {attempt + 1}: composing result.")
            send_message_with_attachments(
                page,
                envelope.body,
                attachment_paths,
                run_id=run_id,
                mention_name=envelope.requester,
                chat_type=envelope.chat_type,
            )
            if attachment_paths:
                wait_for_attachment_counts(page, attachment_paths, baseline_counts)
            print(f"WhatsApp preview send attempt {attempt + 1}: sent.")
            return
        except Exception as exc:
            last_error = exc
            print(f"WhatsApp preview send attempt {attempt + 1} failed: {exc}")
            if attempt:
                break
            try:
                print("Reloading WhatsApp before retrying preview send.")
                page.reload(wait_until="domcontentloaded", timeout=60000)
                wait_for_whatsapp_ready(page, 60)
                open_chat(page, chat)
            except Exception:
                pass
    raise RuntimeError(f"Could not send WhatsApp file refresh review PDF to {chat!r}: {last_error}")


def file_batch_item_result_text(result: dict, item: dict, total: int) -> str:
    status = str(item.get("status") or "BLOCKED")
    if status == "READY_FOR_REVIEW":
        status_label = "Ready for review"
        verify = "The local date and trace audit passed."
    elif status == "NEEDS_REVIEW":
        status_label = "Needs review"
        verify = "The local audit found items that need checking before final merge."
    else:
        status_label = "Blocked"
        verify = "No review PDF was generated for this item."
    lines = [
        "File refresh review PDF",
        "",
        f"File {int(item.get('input_index') or 0)}/{total}",
        f"File: {item.get('source_file_name') or 'attached file'}",
        f"Status: {status_label}",
        f"Item: {item.get('item_id') or ''}",
        f"Version: {int(item.get('item_version') or 1)}",
        f"Verify: {verify}",
    ]
    if int(item.get("replacement_count") or 0):
        lines.append(f"Changes applied: {int(item['replacement_count'])} date edit(s).")
    if item.get("archive_member"):
        lines.append(f"Package item: {item['archive_member']}")
    if status != "READY_FOR_REVIEW":
        reason = human_refresh_preview_reason(item)
        if reason:
            lines.append(f"Reason: {reason}")
    lines.extend(
        [
            f"Batch: {result.get('batch_id')}",
            f"Batch version: {result.get('batch_version')}",
            "Next: name this file or item ID when requesting changes.",
        ]
    )
    if result.get("run_id"):
        lines.append(f"Run: {result['run_id']}")
    return "\n".join(lines)


def file_batch_summary_text(run_id: str, raw_task: str, result: dict) -> str:
    items = [item for item in result.get("items") or [] if isinstance(item, dict)]
    ready = [str(item.get("source_file_name") or item.get("item_id")) for item in items if item.get("status") == "READY_FOR_REVIEW"]
    needs_review = [str(item.get("source_file_name") or item.get("item_id")) for item in items if item.get("status") == "NEEDS_REVIEW"]
    failed = [
        str(item.get("source_file_name") or item.get("item_id"))
        for item in items
        if item.get("status") not in {"READY_FOR_REVIEW", "NEEDS_REVIEW"}
    ]
    delivery_failed = [
        str(item.get("source_file_name") or item.get("item_id"))
        for item in items
        if str(item.get("preview_delivery_error") or "").strip()
    ]
    delivery_complete = bool(result.get("preview_delivery_complete"))
    lines = [
        "File refresh batch",
        "",
        f"Status: {'Waiting' if ready or needs_review else 'Blocked'}",
        f"Task: {user_visible_task_text(raw_task)}",
        f"Batch: {result.get('batch_id')}",
        f"Version: {result.get('batch_version')}",
        f"Ready: {len(ready)}",
        f"Needs review: {len(needs_review)}",
        f"Failed: {len(failed)}",
        f"Skipped: {int(result.get('skipped_count') or 0)}",
        (
            "Verify: Each available review PDF was sent and checked as outgoing WhatsApp content."
            if delivery_complete
            else "Verify: One or more review PDFs could not be verified as sent."
            if ready or needs_review
            else "Verify: No review PDF was generated or sent."
        ),
    ]
    if int(result.get("deduplicated_count") or 0):
        lines.append(
            f"Duplicate attachments ignored: {int(result['deduplicated_count'])}"
        )
    if needs_review:
        lines.append("Needs review: " + ", ".join(needs_review[:10]))
    if failed:
        lines.append("Not generated: " + ", ".join(failed[:10]))
    if delivery_failed:
        lines.append("Not delivered: " + ", ".join(delivery_failed[:10]))
    if ready:
        lines.append(
            "Next: request changes by file/item, or explicitly confirm this batch and ask me to generate the combined PDF."
        )
    else:
        lines.append("Next: resolve the listed file issues before requesting a combined PDF.")
    lines.extend(["No Moodle changes were made.", f"Run: {run_id}"])
    return "\n".join(lines)


def batch_item_attachment_count(
    page,
    batch_id: str,
    item_id: str,
    item_version: int,
) -> int:
    markers = [
        f"Batch: {batch_id}",
        f"Item: {item_id}",
        f"Version: {item_version}",
    ]
    return int(
        page.evaluate(
            """
            ({markers}) => {
              const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0
                  && rect.x > 484
                  && style.visibility !== 'hidden'
                  && style.display !== 'none';
              };
              return Array.from(document.querySelectorAll('[data-testid="document-thumb"]'))
                .filter(visible)
                .filter((thumb) => {
                  const message = thumb.closest('[data-testid^="conv-msg-"]');
                  return Boolean(message && message.closest('[role="row"]'));
                })
                .filter((thumb) => {
                  let current = thumb;
                  for (let depth = 0; current && depth < 14; depth++, current = current.parentElement) {
                    const text = (current.innerText || current.textContent || '')
                      .replace(/\\u200e/g, '')
                      .replace(/\\s+/g, ' ')
                      .trim();
                    if (markers.every((marker) => text.includes(marker))) return true;
                  }
                  return false;
                }).length;
            }
            """,
            {"markers": markers},
        )
    )


def wait_for_batch_item_attachment(
    page,
    batch_id: str,
    item_id: str,
    item_version: int,
    baseline_count: int,
    timeout_ms: int = 45000,
) -> None:
    deadline = time.time() + timeout_ms / 1000
    latest = baseline_count
    stable_polls = 0
    while time.time() < deadline:
        scroll_chat_to_bottom(page)
        latest = batch_item_attachment_count(
            page,
            batch_id,
            item_id,
            item_version,
        )
        if latest > baseline_count:
            stable_polls += 1
            if stable_polls >= 3:
                return
        else:
            stable_polls = 0
        page.wait_for_timeout(1500)
    raise RuntimeError(
        "WhatsApp batch review attachment could not be verified by its batch/item caption; "
        f"before={baseline_count}, after={latest}"
    )


def send_file_batch_preview_result_to_chat(
    page,
    chat: str,
    result: dict,
    run_id: str,
    raw_task: str,
) -> dict:
    items = [item for item in result.get("items") or [] if isinstance(item, dict)]
    deliverable = [
        item
        for item in items
        if str(item.get("preview_pdf_path") or "").strip()
        and resolve_preview_local_path(str(item.get("preview_pdf_path"))).exists()
    ]
    total = len(items)
    for item in deliverable:
        if str(item.get("preview_delivered_at") or "").strip():
            continue
        path = verify_batch_item_preview(item)
        caption = file_batch_item_result_text(result, item, total)
        envelope = outbound_message(
            caption,
            purpose="action_required",
            action_kind="review",
            run_id=run_id,
        )
        batch_id = str(result.get("batch_id") or "")
        batch_version = int(result.get("batch_version") or 0)
        item_id = str(item.get("item_id") or "")
        item_version = int(item.get("item_version") or 1)
        baseline_count = batch_item_attachment_count(
            page,
            batch_id,
            item_id,
            item_version,
        )
        delivered = False
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                ensure_chat_compose_ready(page, chat)
                send_message_with_attachments(
                    page,
                    envelope.body,
                    [path],
                    run_id=run_id,
                    mention_name=envelope.requester,
                    chat_type=envelope.chat_type,
                )
                wait_for_batch_item_attachment(
                    page,
                    batch_id,
                    item_id,
                    item_version,
                    baseline_count,
                )
                mark_batch_item_delivered(
                    batch_id,
                    batch_version,
                    item_id,
                )
                delivered = True
                break
            except Exception as exc:
                last_error = exc
                try:
                    if (
                        batch_item_attachment_count(
                            page,
                            batch_id,
                            item_id,
                            item_version,
                        )
                        > baseline_count
                    ):
                        mark_batch_item_delivered(
                            batch_id,
                            batch_version,
                            item_id,
                        )
                        delivered = True
                        break
                except Exception:
                    pass
                if attempt == 0:
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=60000)
                        wait_for_whatsapp_ready(page, 60)
                        open_chat(page, chat)
                    except Exception:
                        pass
        if not delivered:
            mark_batch_item_delivery_failed(
                batch_id,
                batch_version,
                item_id,
                str(last_error or "WhatsApp delivery could not be verified."),
            )
    delivered_state = (
        load_batch_state(str(result.get("batch_id") or ""))
        if result.get("items")
        else {}
    )
    summary = file_batch_summary_text(run_id, raw_task, delivered_state or result)
    send_message_to_chat(
        page,
        chat,
        outbound_message(
            summary,
            purpose="action_required",
            action_kind="review",
            run_id=run_id,
        ),
    )
    return delivered_state or result


def final_file_batch_result_text(run_id: str, raw_task: str, result: dict) -> str:
    if not result.get("ok"):
        return "\n".join(
            [
                "Combined course documents PDF",
                "",
                "Status: Blocked",
                f"Task: {user_visible_task_text(raw_task)}",
                "Change: I did not release a combined PDF.",
                f"Reason: {human_refresh_preview_reason(result)}",
                "Verify: No Moodle changes were made.",
                f"Run: {run_id}",
            ]
        )
    excluded = [
        str(item.get("source_file_name") or item.get("item_id") or "")
        for item in result.get("excluded_items") or []
        if isinstance(item, dict)
    ]
    lines = [
        "Combined course documents PDF",
        "",
        "Status: Done",
        f"Task: {user_visible_task_text(raw_task)}",
        f"File: {result.get('final_pdf_name')}",
        f"Included: {len(result.get('included_item_ids') or [])} reviewed file(s)",
        f"Pages: {int(result.get('page_count') or 0)}",
        "Verify: Page count and the local date/trace audit passed.",
    ]
    if excluded:
        lines.append("Not included: " + ", ".join(excluded[:12]))
    lines.extend(
        [
            "No Moodle changes were made.",
            f"Batch: {result.get('batch_id')}",
            f"Version: {result.get('batch_version')}",
            f"Run: {run_id}",
        ]
    )
    return "\n".join(lines)


def send_final_file_batch_result_to_chat(
    page,
    chat: str,
    result: dict,
    run_id: str,
    raw_task: str,
) -> None:
    message = final_file_batch_result_text(run_id, raw_task, result)
    if not result.get("ok"):
        send_message_to_chat(page, chat, message)
        return
    paths = refresh_preview_attachment_paths(result)
    if len(paths) != 1:
        raise RuntimeError("Final file-refresh batch result must contain exactly one combined PDF.")
    path = verify_final_batch_pdf(result)
    envelope = outbound_message(
        message,
        purpose="terminal",
        action_kind="review",
        run_id=run_id,
    )
    ensure_chat_compose_ready(page, chat)
    baseline_counts = document_attachment_counts(page, [path])
    send_message_with_attachments(
        page,
        envelope.body,
        [path],
        run_id=run_id,
        mention_name=envelope.requester,
        chat_type=envelope.chat_type,
    )
    wait_for_attachment_counts(page, [path], baseline_counts)
    mark_final_batch_pdf_delivered(
        str(result.get("batch_id") or ""),
        int(result.get("batch_version") or 0),
    )


def reference_pack_rebuild_attachment_paths(result: dict) -> list[Path]:
    return refresh_preview_attachment_paths(result)


def reference_pack_rebuild_result_text(
    run_id: str,
    raw_task: str,
    result: dict,
    *,
    continuing_refresh: bool = False,
) -> str:
    status = str(result.get("status") or "BLOCKED")
    generated = result.get("generated_files") if isinstance(result.get("generated_files"), list) else []
    stable = result.get("stable_reference_files") if isinstance(result.get("stable_reference_files"), list) else []
    missing = result.get("missing_inputs") if isinstance(result.get("missing_inputs"), list) else []
    input_warnings = (
        result.get("input_warnings")
        if isinstance(result.get("input_warnings"), list)
        else []
    )
    files = [Path(str(path)).name for path in generated + stable]
    files = [name for index, name in enumerate(files) if name and name not in files[:index]]
    request_text = user_visible_task_text(raw_task)
    if status == "READY":
        lines = [
            "Reference is ready.",
            "",
            "The Sem 1 reference pack rebuild completed successfully.",
            "I made the local JSON pack and kept the standing refresh rules with it.",
        ]
        if request_text:
            lines.extend(["", f"This is for: {request_text}"])
        if files:
            lines.extend(["", "Ready now:"])
            lines.extend(f"- {name}" for name in files[:8])
        if input_warnings:
            lines.extend(
                [
                    "",
                    f"I ignored {len(input_warnings)} unusable or semantically mismatched attachment candidate(s) because other verified inputs supplied the required content.",
                ]
            )
        lines.append("")
        if continuing_refresh:
            lines.append("I am continuing with the current file refresh now.")
        else:
            lines.append("Next, send one old course file when you want me to refresh it.")
        lines.append("No Moodle changes were made.")
    elif status == "NEEDS_TIMELINE_REVIEW":
        lines = [
            "Timeline review needed.",
            "",
            "The source content does not fit into every valid teaching week for this class arrangement.",
            (
                f"I prepared timeline draft version {int(result.get('draft_version') or 1)} "
                f"with {int(result.get('overflow_count') or 0)} unresolved overflow item(s)."
            ),
            "The current reference JSON files are unchanged.",
        ]
        if request_text:
            lines.extend(["", f"This is for: {request_text}"])
        if input_warnings:
            lines.extend(
                [
                    "",
                    f"I ignored {len(input_warnings)} unusable or semantically mismatched attachment candidate(s); the valid timeline and calendar sources were still usable.",
                ]
            )
        lines.extend(
            [
                "",
                "Review the attached timeline, then tell me which content to move to another Teaching Week or delete.",
                "After all overflow is resolved, I will send a final draft for a separate confirmation.",
                "No Moodle changes were made.",
            ]
        )
    elif status == "NEEDS_STRUCTURED_EXTRACTION":
        lines = [
            "Reference pack rebuild needs one more step.",
            "",
            "I saved the reference files you uploaded, but I could not read enough timeline/calendar data to rebuild the full JSON pack yet.",
        ]
        if request_text:
            lines.extend(["", f"This is for: {request_text}"])
        if missing:
            lines.extend(["", "I still need:"])
            lines.extend(f"- {str(item)}" for item in missing[:6])
        lines.extend(
            [
                "",
                "Please send the missing reference file or a parseable JSON version.",
                "No Moodle changes were made.",
            ]
        )
    else:
        lines = [
            "Reference pack rebuild did not finish.",
            "",
            "I could not rebuild the reference JSON files from the available inputs.",
        ]
        if request_text:
            lines.extend(["", f"This is for: {request_text}"])
        reason = first_text(result.get("blocker"), result.get("note"))
        if reason:
            lines.extend(["", f"Why: {human_block_reason(reason)}"])
        if missing:
            lines.extend(["", "I still need:"])
            lines.extend(f"- {str(item)}" for item in missing[:6])
        lines.extend(["", "No Moodle changes were made."])
    lines.append(f"Run: {run_id}")
    return "\n".join(lines)


def send_reference_pack_rebuild_result_to_chat(page, chat: str, message: str, result: dict, run_id: str) -> None:
    attachment_paths = reference_pack_rebuild_attachment_paths(result)
    status = str(result.get("status") or "BLOCKED")
    requires_action = status != "READY"
    envelope = outbound_message(
        message,
        purpose="action_required" if requires_action else "terminal",
        action_kind="review" if status == "NEEDS_TIMELINE_REVIEW" else "missing_input",
        run_id=run_id,
    )
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            print(f"WhatsApp reference rebuild send attempt {attempt + 1}: ensuring chat `{chat}`.")
            ensure_chat_compose_ready(page, chat)
            baseline_counts = document_attachment_counts(page, attachment_paths)
            print(f"WhatsApp reference rebuild send attempt {attempt + 1}: composing result.")
            if attachment_paths:
                send_message_with_attachments(
                    page,
                    envelope.body,
                    attachment_paths,
                    run_id=run_id,
                    mention_name=envelope.requester if requires_action else "",
                    chat_type=envelope.chat_type,
                )
                wait_for_attachment_counts(page, attachment_paths, baseline_counts)
            else:
                send_message(
                    page,
                    envelope.body,
                    mention_name=envelope.requester if requires_action else "",
                    chat_type=envelope.chat_type,
                )
            print(f"WhatsApp reference rebuild send attempt {attempt + 1}: sent.")
            return
        except Exception as exc:
            last_error = exc
            print(f"WhatsApp reference rebuild send attempt {attempt + 1} failed: {exc}")
            if attempt:
                break
            try:
                print("Reloading WhatsApp before retrying reference rebuild send.")
                page.reload(wait_until="domcontentloaded", timeout=60000)
                wait_for_whatsapp_ready(page, 60)
                open_chat(page, chat)
            except Exception:
                pass
    raise RuntimeError(f"Could not send WhatsApp reference rebuild result to {chat!r}: {last_error}")


def semester_schedule_update_result_text(run_id: str, raw_task: str, result: dict) -> str:
    status = str(result.get("status") or "BLOCKED")
    request_text = user_visible_task_text(raw_task)
    if status == "READY":
        week_count = int(result.get("week_count") or 0)
        lines = [
            "Semester schedule update is ready.",
            "",
            f"I reflowed the local Sem 1 schedule and timeline for {week_count} actual teaching week(s).",
            "I only updated teaching dates/weekdays; existing timeline content placement and moved/deleted content mappings were preserved.",
            "Future file refreshes will use the same reflowed teaching-week sequence for class/session dates and week-based deadline mapping.",
        ]
        if request_text:
            lines.extend(["", f"This is for: {request_text}"])
        lines.extend(["", "No Moodle changes were made."])
    elif status == "NEEDS_TIMELINE_REVIEW":
        lines = [
            "Timeline review needed.",
            "",
            "The requested weekly class arrangement has fewer valid teaching weeks than the current course content.",
            (
                f"I prepared timeline draft version {int(result.get('draft_version') or 1)} "
                f"with {int(result.get('overflow_count') or 0)} unresolved overflow item(s)."
            ),
            "The current semester schedule, timeline, and manifest are unchanged.",
        ]
        if request_text:
            lines.extend(["", f"This is for: {request_text}"])
        lines.extend(
            [
                "",
                "Review the attached timeline, then tell me which content to move to another Teaching Week or delete.",
                "No Moodle changes were made.",
            ]
        )
    else:
        lines = [
            "Semester schedule update did not finish.",
            "",
            "I could not safely reflow the local semester schedule and timeline from the available input.",
        ]
        if request_text:
            lines.extend(["", f"This is for: {request_text}"])
        reason = first_text(result.get("blocker"), result.get("note"))
        if reason:
            lines.extend(
                [
                    "",
                    f"Why: {human_block_reason(reason, default='The schedule could not be grounded safely from the supplied class arrangement and calendar.')}",
                ]
            )
        lines.extend(["", "No Moodle changes were made."])
    lines.append(f"Run: {run_id}")
    return "\n".join(lines)


def send_semester_schedule_update_result_to_chat(page, chat: str, message: str, result: dict, run_id: str) -> None:
    attachment_paths = refresh_preview_attachment_paths(result)
    status = str(result.get("status") or "BLOCKED")
    requires_action = status != "READY"
    envelope = outbound_message(
        message,
        purpose="action_required" if requires_action else "terminal",
        action_kind="review" if status == "NEEDS_TIMELINE_REVIEW" else "missing_input",
        run_id=run_id,
    )
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            print(f"WhatsApp semester schedule send attempt {attempt + 1}: ensuring chat `{chat}`.")
            ensure_chat_compose_ready(page, chat)
            baseline_counts = document_attachment_counts(page, attachment_paths)
            print(f"WhatsApp semester schedule send attempt {attempt + 1}: composing result.")
            if attachment_paths:
                send_message_with_attachments(
                    page,
                    envelope.body,
                    attachment_paths,
                    run_id=run_id,
                    mention_name=envelope.requester if requires_action else "",
                    chat_type=envelope.chat_type,
                )
                wait_for_attachment_counts(page, attachment_paths, baseline_counts)
            else:
                send_message(
                    page,
                    envelope.body,
                    mention_name=envelope.requester if requires_action else "",
                    chat_type=envelope.chat_type,
                )
            print(f"WhatsApp semester schedule send attempt {attempt + 1}: sent.")
            return
        except Exception as exc:
            last_error = exc
            print(f"WhatsApp semester schedule send attempt {attempt + 1} failed: {exc}")
            if attempt:
                break
            try:
                print("Reloading WhatsApp before retrying semester schedule send.")
                page.reload(wait_until="domcontentloaded", timeout=60000)
                wait_for_whatsapp_ready(page, 60)
                open_chat(page, chat)
            except Exception:
                pass
    raise RuntimeError(f"Could not send WhatsApp semester schedule result to {chat!r}: {last_error}")


def timeline_draft_action_result_text(run_id: str, raw_task: str, result: dict) -> str:
    status = str(result.get("status") or "BLOCKED")
    request_text = user_visible_task_text(raw_task)
    version = int(result.get("draft_version") or 0)
    if status == "READY_FOR_CONFIRMATION":
        lines = [
            "Timeline draft is ready for confirmation.",
            "",
            f"I applied the requested revision and prepared version {version}.",
            "All remaining content now fits valid teaching weeks.",
            "The formal reference JSON is still unchanged.",
            "",
            "Review the attached latest draft. To make it official, send a separate confirmation message.",
            "No Moodle changes were made.",
        ]
    elif status == "NEEDS_TIMELINE_REVIEW":
        lines = [
            "Timeline draft still needs review.",
            "",
            f"I applied the requested revision and prepared version {version}.",
            f"{int(result.get('overflow_count') or 0)} overflow item(s) still need to be moved or deleted.",
            "The formal reference JSON is unchanged.",
            "",
            "Review the attached latest draft and send another move or delete instruction.",
            "No Moodle changes were made.",
        ]
    elif status == "COMMITTED":
        lines = [
            "Timeline reference is ready.",
            "",
            f"I committed the reviewed timeline draft version {version} to the local reference pack.",
            "The schedule, timeline, manifest, and applicable rebuild references were updated together with backups and checksums.",
            "No Moodle changes were made.",
        ]
    else:
        lines = [
            "Timeline draft action was blocked.",
            "",
            "I did not revise or commit the local reference pack.",
        ]
        reason = first_text(result.get("blocker"), result.get("note"))
        if reason:
            lines.extend(["", f"Why: {human_block_reason(reason)}"])
        lines.extend(["", "No Moodle changes were made."])
    if request_text:
        lines.extend(["", f"This is for: {request_text}"])
    lines.append(f"Run: {run_id}")
    return "\n".join(lines)


def send_timeline_draft_action_result_to_chat(
    page,
    chat: str,
    message: str,
    result: dict,
    run_id: str,
) -> None:
    attachment_paths = refresh_preview_attachment_paths(result)
    status = str(result.get("status") or "BLOCKED")
    requires_action = status in {"READY_FOR_CONFIRMATION", "NEEDS_TIMELINE_REVIEW", "BLOCKED"}
    envelope = outbound_message(
        message,
        purpose="action_required" if requires_action else "terminal",
        action_kind="confirmation" if status == "READY_FOR_CONFIRMATION" else "review",
        run_id=run_id,
    )
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            ensure_chat_compose_ready(page, chat)
            baseline_counts = document_attachment_counts(page, attachment_paths)
            if attachment_paths:
                send_message_with_attachments(
                    page,
                    envelope.body,
                    attachment_paths,
                    run_id=run_id,
                    mention_name=envelope.requester if requires_action else "",
                    chat_type=envelope.chat_type,
                )
                wait_for_attachment_counts(page, attachment_paths, baseline_counts)
            else:
                send_message(
                    page,
                    envelope.body,
                    mention_name=envelope.requester if requires_action else "",
                    chat_type=envelope.chat_type,
                )
            return
        except Exception as exc:
            last_error = exc
            if attempt:
                break
            try:
                page.reload(wait_until="domcontentloaded", timeout=60000)
                wait_for_whatsapp_ready(page, 60)
                open_chat(page, chat)
            except Exception:
                pass
    raise RuntimeError(f"Could not send WhatsApp timeline draft result to {chat!r}: {last_error}")


def approval_attachment_paths_for_manifest(manifest: dict) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for operation in manifest.get("operations", []) or []:
        if not isinstance(operation, dict):
            continue
        for attachment in operation.get("approval_attachments") or []:
            if isinstance(attachment, str):
                path_text = attachment
            elif isinstance(attachment, dict):
                path_text = str(attachment.get("path") or "")
            else:
                continue
            if not path_text.strip():
                continue
            path = resolve_refresh_local_path(path_text)
            key = str(path.resolve(strict=False))
            if key in seen:
                continue
            if not path.exists():
                raise FileNotFoundError(f"Approval attachment does not exist: {path}")
            if path.suffix.lower() != ".pdf":
                raise ValueError(f"Approval attachment must be a PDF: {path}")
            seen.add(key)
            paths.append(path)
    return paths


def send_approval_request_to_chat(page, chat: str, message: str, manifest: dict, run_id: str, approval_code: str) -> None:
    attachment_paths = approval_attachment_paths_for_manifest(manifest)
    envelope = outbound_message(
        message,
        purpose="action_required",
        action_kind="approval",
        run_id=run_id,
    )
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            print(f"WhatsApp approval send attempt {attempt + 1}: ensuring chat `{chat}`.")
            ensure_chat_compose_ready(page, chat)
            baseline_keys = {record_key(record) for record in message_records(page)}
            baseline_attachment_counts = document_attachment_counts(page, attachment_paths)
            print(f"WhatsApp approval send attempt {attempt + 1}: composing request.")
            send_message_with_attachments(
                page,
                envelope.body,
                attachment_paths,
                run_id=run_id,
                approval_code=approval_code,
                mention_name=envelope.requester,
                chat_type=envelope.chat_type,
            )
            wait_for_sent_request(
                page,
                run_id,
                attachment_paths,
                baseline_keys,
                baseline_attachment_counts,
                approval_code=approval_code,
                require_attachment_verification=bool(attachment_paths),
            )
            print(f"WhatsApp approval send attempt {attempt + 1}: sent.")
            return
        except Exception as exc:
            last_error = exc
            print(f"WhatsApp approval send attempt {attempt + 1} failed: {exc}")
            try:
                if approval_request_visible_in_chat(page, approval_code, run_id):
                    print("WhatsApp approval request was found after verification timed out; suppressing retry.")
                    return
            except Exception:
                pass
            if attempt:
                break
            try:
                print("Reloading WhatsApp before retrying approval send.")
                page.reload(wait_until="domcontentloaded", timeout=60000)
                wait_for_whatsapp_ready(page, 60)
                open_chat(page, chat)
                if approval_request_visible_in_chat(page, approval_code, run_id):
                    print("WhatsApp approval request was found after reload; suppressing retry.")
                    return
            except Exception:
                pass
    raise RuntimeError(f"Could not send WhatsApp approval request to {chat!r}: {last_error}")


def approval_request_visible_in_chat(page, approval_code: str, run_id: str) -> bool:
    scroll_chat_to_bottom(page)
    expand_collapsed_approval_requests(page)
    for record in message_records(page):
        if record_direction(record) == "outgoing" and record_contains_approval_request(record, approval_code, run_id):
            return True
    return False


def sent_approval_recovery_baseline_keys(
    page,
    approval_code: str,
    run_id: str,
    batch_id: str = "",
) -> set[str]:
    """Leave an exact reply observable even when its request was later retried."""
    records = message_records(page)
    full_request_indexes = [
        index
        for index, record in enumerate(records)
        if record_contains_approval_request(record, approval_code, run_id)
    ]
    reply_indexes = [
        index
        for index, record in enumerate(records)
        if approval_command_from_record(
            record,
            batch_id,
            run_id,
            approval_code,
        )
    ]
    if reply_indexes:
        first_reply_index = reply_indexes[0]
        preceding_request_indexes = [
            index
            for index, record in enumerate(records[:first_reply_index])
            if (
                record_contains_approval_request(record, approval_code, run_id)
                or record_contains_collapsed_approval_request(record)
            )
        ]
        if preceding_request_indexes:
            request_index = preceding_request_indexes[-1]
            return {
                record_key(record)
                for record in records[: request_index + 1]
            }
    if not full_request_indexes:
        return {record_key(record) for record in records}
    latest_request_index = full_request_indexes[-1]
    return {
        record_key(record)
        for record in records[: latest_request_index + 1]
    }


def run_approval_execution_cycle(
    *,
    page,
    moodle_page,
    moodle_available: bool,
    env: dict[str, str],
    moodle_env: dict[str, str],
    base_url: str,
    chat: str,
    run_id: str,
    approval_code: str,
    raw_task: str,
    intent: dict,
    manifest: dict,
    planner_message: str,
    baseline_after_task: set[str],
    timeout_error_type,
) -> dict:
    post_json(
        f"{base_url}/agent/progress",
        {
            "event": "approval_request_send_started",
            "run_id": run_id,
            "agent_name": "course-agent-app",
            "chat_name": chat,
            "evidence": "Playwright course-agent app is composing the approval request.",
        },
    )
    send_approval_request_to_chat(
        page,
        chat,
        approval_text(run_id, approval_code, raw_task, intent, manifest, planner_message),
        manifest,
        run_id,
        approval_code,
    )
    post_json(
        f"{base_url}/agent/progress",
        {
            "event": "approval_request_sent",
            "run_id": run_id,
            "agent_name": "course-agent-app",
            "chat_name": chat,
            "evidence": "Playwright course-agent app sent the approval request.",
        },
    )
    return wait_for_approval_and_execute(
        page=page,
        moodle_page=moodle_page,
        moodle_available=moodle_available,
        env=env,
        moodle_env=moodle_env,
        base_url=base_url,
        chat=chat,
        run_id=run_id,
        approval_code=approval_code,
        manifest=manifest,
        baseline_after_task=baseline_after_task,
        timeout_error_type=timeout_error_type,
    )


def wait_for_approval_and_execute(
    *,
    page,
    moodle_page,
    moodle_available: bool,
    env: dict[str, str],
    moodle_env: dict[str, str],
    base_url: str,
    chat: str,
    run_id: str,
    approval_code: str,
    manifest: dict,
    baseline_after_task: set[str],
    timeout_error_type,
) -> dict:
    workflow_state = request_json(f"{base_url}/status")
    context = ACTIVE_STAFF_PROMPT_CONTEXT
    expected_requester = (
        context.requester
        if context
        else str(workflow_state.get("whatsapp_requester") or "")
    )
    bound_chat_type = (
        context.chat_type
        if context
        else str(workflow_state.get("whatsapp_chat_type") or env.get("_WHATSAPP_CHAT_TYPE") or "")
    )
    payload = wait_for_approval_reply(
        page,
        env,
        run_id,
        baseline_after_task,
        approval_code,
        expected_requester=expected_requester,
        chat_type=bound_chat_type,
        agent_display_name=str(env.get("_WHATSAPP_AGENT_DISPLAY_NAME") or ""),
    )
    parsed_approval = parse_approval_command(
        str(payload.get("body") or ""),
        str(env.get("BATCH_ID") or ""),
    )
    approval_is_approve = (
        str((parsed_approval or {}).get("command") or "").upper() == "APPROVE"
        or str(payload.get("decision") or "").casefold() == "approve"
    )
    approved_group_snapshot: dict | None = None
    if manifest_has_group_writes(manifest) and approval_is_approve:
        if not moodle_available or moodle_page is None:
            blocker = (
                "The approved Group change needs an action-time sandbox readback, "
                "but the Moodle browser session is unavailable."
            )
            post_json(
                f"{base_url}/agent/course-agent-result",
                {
                    "run_id": run_id,
                    "course_agent_result": blocked_group_preflight_result(run_id, blocker),
                },
            )
            status = request_json(f"{base_url}/status")
            send_message_to_chat(page, chat, result_text(status))
            return status
        try:
            fresh_preflight = detailed_group_structure_for_manifest(
                env,
                manifest,
                moodle_page,
                run_id=run_id,
            )
        except Exception as exc:
            print(f"Group approval preflight readback failed for run {run_id}: {exc}")
            blocker, blocker_category = group_structure_readback_blocker(exc)
            post_json(
                f"{base_url}/agent/course-agent-result",
                {
                    "run_id": run_id,
                    "course_agent_result": {
                        **blocked_group_preflight_result(run_id, blocker),
                        "blocker_category": blocker_category,
                    },
                },
            )
            status = request_json(f"{base_url}/status")
            send_message_to_chat(page, chat, result_text(status))
            return status
        blocker = group_preflight_blocker(manifest, fresh_preflight)
        if blocker:
            post_json(
                f"{base_url}/agent/course-agent-result",
                {
                    "run_id": run_id,
                    "course_agent_result": blocked_group_preflight_result(run_id, blocker),
                },
            )
            status = request_json(f"{base_url}/status")
            send_message_to_chat(page, chat, result_text(status))
            return status
        approved_group_snapshot = fresh_preflight
    result = post_json(f"{base_url}/agent/approval", payload)
    status = result.get("state") or request_json(f"{base_url}/status")
    if status.get("status") == "moodle_agent_browser_action_pending":
        continuation_operations = approved_continuation_operations(manifest, status)
        if continuation_operations and moodle_available:
            local_results: list[dict] = []
            runtime_refs: dict[str, dict] = {}
            failed_position: int | None = None
            for local_position, (operation_index, operation) in enumerate(continuation_operations):
                materialized = materialize_manifest_operation(operation, env, run_id, operation_index)
                materialized = apply_runtime_reference(materialized, runtime_refs)
                print(
                    "Executing local Moodle browser operation "
                    f"{materialized.get('operation_kind')} for run {run_id} "
                    f"({operation_index}/{len(manifest.get('operations', []) or [])})."
                )
                target_ref = operation_target_ref(materialized)
                if target_ref and not materialized.get("resolved_runtime_reference"):
                    local_result = {
                        "ok": False,
                        "status": "BLOCKED",
                        "operation_id": materialized.get("operation_id", ""),
                        "target_object": first_text(materialized.get("target_object"), target_ref),
                        "saves_count": 0,
                        "blocker": (
                            f"Runtime target reference {target_ref!r} was not produced successfully by an earlier operation."
                        ),
                    }
                elif materialized.get("operation_kind") in LOCAL_BROWSER_OPERATION_KINDS:
                    local_result = execute_local_browser_operation(
                        materialized,
                        moodle_env,
                        moodle_page,
                        timeout_error_type,
                    )
                else:
                    local_result = execute_approved_api_continuation(
                        materialized,
                        env,
                        operation_index=operation_index,
                        operation_count=len(manifest.get("operations", []) or []),
                    )
                local_result["course_agent_operation_index"] = operation_index
                local_results.append(local_result)
                remember_runtime_reference(runtime_refs, materialized, local_result)
                if not local_result.get("ok"):
                    failed_position = local_position
                    break
            if failed_position is not None:
                for operation_index, operation in continuation_operations[failed_position + 1 :]:
                    local_results.append(
                        {
                            "ok": False,
                            "status": "NOT_RUN",
                            "operation_id": operation_id_for_manifest_operation(operation, run_id, operation_index),
                            "course_agent_operation_index": operation_index,
                            "target_object": first_text(
                                operation.get("target_object"),
                                operation.get("approval_item"),
                                operation.get("target_ref"),
                            ),
                            "saves_count": 0,
                            "note": "Not run because an earlier approved operation failed verification.",
                        }
                    )
            post_local_moodle_result(
                base_url,
                env,
                run_id,
                aggregate_local_browser_results(local_results, status.get("moodle_result")),
            )
            status = request_json(f"{base_url}/status")
        else:
            status = watch_moodle_agent_stage(env, base_url, status)
    specialist_verifier_results: list[dict] = []
    if manifest_has_group_writes(manifest) and approval_is_approve:
        try:
            execution_status_text = " ".join(
                [
                    str(status.get("status") or ""),
                    str((status.get("moodle_result") or {}).get("status") or ""),
                ]
            ).casefold()
            execution_failed = (
                "failed" in execution_status_text or "blocked" in execution_status_text
            )

            def group_readback_attempt() -> dict:
                snapshot = detailed_group_structure_for_manifest(
                    env,
                    manifest,
                    moodle_page,
                    run_id=run_id,
                )
                verification = verify_group_manifest_snapshot(
                    run_id,
                    manifest,
                    snapshot,
                    status,
                    initial_snapshot=approved_group_snapshot,
                )
                return {"snapshot": snapshot, "verification": verification}

            group_observation = group_readback_attempt()
            group_verifier_result = group_observation["verification"]
        except Exception as exc:
            print(f"Group final readback failed for run {run_id}: {exc}")
            group_verifier_result = failed_group_verification_result(
                run_id,
                status,
                (
                    "The approved Moodle operation finished, but I could not obtain "
                    "a unique detailed Group structure readback to verify the final state."
                ),
            )
        specialist_verifier_results.append(group_verifier_result)
    if (
        manifest_has_forum_writes(manifest)
        and approval_is_approve
        and execution_has_verified_forum_write(manifest, status)
    ):
        try:
            if not moodle_available or moodle_page is None:
                raise RuntimeError("Moodle browser session is unavailable for forum readback.")
            forum_verifier_result = verify_forum_manifest_readback(
                run_id,
                manifest,
                status,
                moodle_page,
            )
        except Exception as exc:
            print(f"Forum final readback failed for run {run_id}: {exc}")
            forum_verifier_result = failed_forum_verification_result(
                run_id,
                status,
                (
                    "The approved forum operation finished, but I could not obtain "
                    "an exact Moodle discussion readback to verify the final state."
                ),
            )
        specialist_verifier_results.append(forum_verifier_result)
    if specialist_verifier_results:
        verifier_result = merge_specialist_verifier_results(specialist_verifier_results)
        post_json(
            f"{base_url}/agent/course-agent-result",
            {"run_id": run_id, "course_agent_result": verifier_result},
        )
        status = request_json(f"{base_url}/status")
    send_message_to_chat(page, chat, result_text(status))
    return status


def main() -> None:
    global ACTIVE_STAFF_PROMPT_CONTEXT
    env = load_env()
    env["WHATSAPP_MODE"] = "codex_agent_web"
    env["COURSE_AGENT_FREE_TASK_MODE"] = "1"
    env.setdefault("COURSE_AGENT_BROWSER_LIFECYCLE", "agent")
    env.setdefault("CODEX_LISTENER_FOREVER", "1")
    # Zero means unbounded: this is a long-running agent and exits only through
    # an explicit lifecycle stop command, Ctrl-C, or an unrecoverable failure.
    env.setdefault("COURSE_AGENT_TASK_WAIT_SECONDS", "0")
    env.setdefault("WHATSAPP_WEB_WAIT_SECONDS", "86400")
    env.setdefault("COURSE_AGENT_MOODLE_READY_SECONDS", "30")
    env.setdefault("COURSE_AGENT_PROMPT_ATTACHMENT_ROOT", str(PROMPT_ATTACHMENT_ROOT))
    os.environ["COURSE_AGENT_PROMPT_ATTACHMENT_ROOT"] = env["COURSE_AGENT_PROMPT_ATTACHMENT_ROOT"]
    moodle_env = load_moodle_browser_env()
    moodle_env.update(env)
    moodle_env.setdefault("MOODLE_AUTO_CLICK_HKU_LOGIN", "0")
    base_url = env.get("LOCAL_BASE_URL", f"http://localhost:{env['PORT']}").rstrip("/")
    env["_COURSE_AGENT_BASE_URL"] = base_url
    chat = course_agent_chat_name(env)
    server = start_server(env, base_url)
    post_app_progress(base_url, "agent_app_starting", chat, "Starting the long-running course agent app.")
    sync_playwright, PlaywrightTimeoutError = import_playwright()
    playwright = sync_playwright().start()
    post_app_progress(base_url, "whatsapp_window_opening", chat, "Opening or attaching the WhatsApp Web browser session.")
    context = launch_whatsapp_context(playwright, env)
    moodle_context = None
    moodle_startup_ready = False
    page = None
    shutdown_notice_sent = False
    try:
        post_app_progress(base_url, "moodle_window_opening", chat, "Opening or attaching the Moodle browser session.")
        moodle_context, raw_moodle_page = launch_moodle_context(
            playwright,
            moodle_env,
        )
        moodle_page = SandboxMoodleSessionPage(raw_moodle_page, moodle_env)
        lifecycle_dir = REPORTS / f"course_agent_moodle_lifecycle_{stamp()}"
        lifecycle_dir.mkdir(parents=True, exist_ok=True)
        try:
            post_app_progress(base_url, "moodle_ready_waiting", chat, "Checking the sandbox Moodle course page.")
            timeout_ms = int(float(env["COURSE_AGENT_MOODLE_READY_SECONDS"]) * 1000)
            goto_with_retry(
                moodle_page,
                SANDBOX_COURSE_URL,
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
            moodle_page.screenshot(path=str(lifecycle_dir / "00_moodle_startup.png"), full_page=True)
            if looks_like_login(moodle_page):
                post_app_progress(
                    base_url,
                    "moodle_login_required",
                    chat,
                    "The Moodle browser session is signed out; attempting automatic sandbox login.",
                )
                ensure_moodle_authenticated(moodle_page, moodle_env)
            moodle_startup_ready = True
            post_app_progress(base_url, "moodle_ready", chat, "Sandbox Moodle course page opened for the agent lifecycle.")
        except Exception as exc:
            post_app_progress(
                base_url,
                "moodle_ready_blocked",
                chat,
                "Moodle browser opened, but the readiness check did not complete.",
                str(exc),
            )
            print(f"Moodle window opened, but readiness check is pending/blocked: {exc}")

        page = context.pages[0] if context.pages else context.new_page()
        if "web.whatsapp.com" not in page.url:
            page.goto("https://web.whatsapp.com/", wait_until="domcontentloaded", timeout=60000)
        post_app_progress(base_url, "whatsapp_ready_waiting", chat, "Waiting for WhatsApp Web to be ready in the configured chat.")
        wait_for_whatsapp_ready(page, float(env["WHATSAPP_WEB_WAIT_SECONDS"]))
        open_chat(page, chat)
        agent_display_name = discover_whatsapp_account_name(page)
        open_chat(page, chat)
        whatsapp_context = current_whatsapp_chat_context(page, agent_display_name)
        env["_WHATSAPP_AGENT_DISPLAY_NAME"] = agent_display_name
        env["_WHATSAPP_CHAT_TYPE"] = str(whatsapp_context.get("chat_type") or "unknown")
        env["_WHATSAPP_ENFORCE_UNIQUE_DISPLAY_NAME"] = (
            "1" if env["_WHATSAPP_CHAT_TYPE"] == "group" else "0"
        )
        identity_event = "whatsapp_identity_ready"
        identity_detail = "Dedicated WhatsApp account identity and chat context are ready."
        if env["_WHATSAPP_CHAT_TYPE"] == "group" and not agent_display_name:
            identity_event = "whatsapp_identity_degraded"
            identity_detail = (
                "WhatsApp group intake is paused because the dedicated agent account name "
                "could not be read from WhatsApp Web."
            )
        post_app_progress(
            base_url,
            identity_event,
            chat,
            identity_detail,
            "",
            whatsapp_authenticated=True,
            whatsapp_agent_display_name=agent_display_name,
            whatsapp_chat_type=env["_WHATSAPP_CHAT_TYPE"],
            whatsapp_mention_ready=bool(whatsapp_context.get("mention_ready")),
            whatsapp_degraded_reason="" if identity_event == "whatsapp_identity_ready" else identity_detail,
        )
        if identity_event == "whatsapp_identity_degraded":
            raise RuntimeError(identity_detail)
        post_app_progress(base_url, "whatsapp_ready", chat, "WhatsApp Web is ready and the configured chat is open.")
        print(f"Course agent app is running. Waiting for prompts in WhatsApp chat `{chat}`.")
        post_app_progress(base_url, "agent_runtime_ready", chat, "Course agent is ready to receive staff prompts.")
        recovery_wait_boundary = parse_hk_datetime(
            env.get("COURSE_AGENT_RECOVER_UNCONSUMED_AFTER")
        )
        wait_boundary_at = recovery_wait_boundary or datetime.now(HK_TZ)
        if recovery_wait_boundary is not None:
            print(
                "WhatsApp recovery reveal: "
                + ("incoming record visible" if reveal_latest_incoming_record_for_recovery(page) else "no incoming record revealed")
            )
        baseline = current_prompt_baseline_keys(
            page,
            preserve_incoming_since=recovery_wait_boundary,
        )
        # A recovery launch must not send (and therefore scroll for) a
        # lifecycle notice before the preserved visible prompt is inspected.
        if lifecycle_whatsapp_messages_enabled(env) and recovery_wait_boundary is None:
            startup_text = lifecycle_start_text(moodle_ready=moodle_startup_ready)
            post_app_progress(
                base_url,
                "lifecycle_notice_send_started",
                chat,
                "Sending the WhatsApp startup status notice.",
            )
            ok, send_error = send_course_agent_message(page, chat, startup_text)
            if ok and not whatsapp_outgoing_message_visible(
                page,
                startup_text,
                baseline_keys=baseline,
            ):
                ok = False
                send_error = (
                    "The startup notice send returned without error, but the outgoing "
                    "message was not visible in the current WhatsApp chat."
                )
            if not ok:
                post_app_progress(
                    base_url,
                    "lifecycle_notice_send_failed",
                    chat,
                    "The WhatsApp startup status notice could not be sent.",
                    send_error,
                )
                print(f"Could not send WhatsApp startup notice: {send_error}")
            else:
                post_app_progress(
                    base_url,
                    "lifecycle_notice_sent",
                    chat,
                    "The WhatsApp startup status notice was sent.",
                )

        pending_state = request_json(f"{base_url}/status")
        if pending_state.get("whatsapp_requester"):
            ACTIVE_STAFF_PROMPT_CONTEXT = StaffPromptContext(
                raw_task="",
                baseline_keys=set(),
                observed_at=datetime.now(HK_TZ),
                requester=str(pending_state.get("whatsapp_requester") or ""),
                message_id=str(pending_state.get("whatsapp_root_message_id") or ""),
                chat_type=str(pending_state.get("whatsapp_chat_type") or env["_WHATSAPP_CHAT_TYPE"]),
                provenance_confidence="high",
            )
        if state_has_pending_unsent_approval(pending_state):
            pending_run_id = str(pending_state.get("run_id", ""))
            pending_intent = apply_intent_defaults(dict(pending_state.get("course_task_intent") or {}))
            pending_manifest = apply_manifest_defaults(dict(pending_state.get("course_operation_manifest") or {}))
            pending_task = first_text(pending_intent.get("raw_task"), pending_intent.get("staff_prompt"), pending_intent.get("interpreted_intent"))
            print(f"Recovering unsent approval for run {pending_run_id}.")
            try:
                run_approval_execution_cycle(
                    page=page,
                    moodle_page=moodle_page,
                    moodle_available=moodle_context is not None,
                    env=env,
                    moodle_env=moodle_env,
                    base_url=base_url,
                    chat=chat,
                    run_id=pending_run_id,
                    approval_code=str(pending_state.get("approval_code", "")),
                    raw_task=pending_task,
                    intent=pending_intent,
                    manifest=pending_manifest,
                    planner_message="",
                    baseline_after_task={record_key(record) for record in message_records(page)},
                    timeout_error_type=PlaywrightTimeoutError,
                )
            except Exception as exc:
                ok, send_error = send_course_agent_message(
                    page,
                    chat,
                    blocked_agent_text(
                        run_id=pending_run_id,
                        task=pending_task,
                        reason=exc,
                    ),
                )
                post_json(
                    f"{base_url}/agent/progress",
                    {
                        "event": "approval_request_send_failed",
                        "run_id": pending_run_id,
                        "agent_name": "course-agent-app",
                        "chat_name": chat,
                        "detail": str(exc) if ok else f"{exc}; additionally failed to send WhatsApp blocker: {send_error}",
                        "evidence": "Course-agent app failed while recovering a pending approval cycle.",
                    },
                )
                print(f"Pending approval recovery failed for run {pending_run_id}; preserving state for retry.")
                return
        sent_state = request_json(f"{base_url}/status")
        if state_has_sent_waiting_approval(sent_state):
            sent_run_id = str(sent_state.get("run_id", ""))
            sent_manifest = apply_manifest_defaults(dict(sent_state.get("course_operation_manifest") or {}))
            sent_intent = sent_state.get("course_task_intent") if isinstance(sent_state.get("course_task_intent"), dict) else {}
            print(f"Recovering sent approval wait for run {sent_run_id}.")
            try:
                sent_approval_code = str(sent_state.get("approval_code", ""))
                if approval_request_visible_in_chat(page, sent_approval_code, sent_run_id):
                    wait_for_approval_and_execute(
                        page=page,
                        moodle_page=moodle_page,
                        moodle_available=moodle_context is not None,
                        env=env,
                        moodle_env=moodle_env,
                        base_url=base_url,
                        chat=chat,
                        run_id=sent_run_id,
                        approval_code=sent_approval_code,
                        manifest=sent_manifest,
                        baseline_after_task=sent_approval_recovery_baseline_keys(
                            page,
                            sent_approval_code,
                            sent_run_id,
                            str(env.get("BATCH_ID") or ""),
                        ),
                        timeout_error_type=PlaywrightTimeoutError,
                    )
                else:
                    pending_task = first_text(
                        sent_intent.get("raw_task"),
                        sent_intent.get("staff_prompt"),
                        sent_intent.get("interpreted_intent"),
                    )
                    print(f"Approval request for run {sent_run_id} is not visible; resending it.")
                    run_approval_execution_cycle(
                        page=page,
                        moodle_page=moodle_page,
                        moodle_available=moodle_context is not None,
                        env=env,
                        moodle_env=moodle_env,
                        base_url=base_url,
                        chat=chat,
                        run_id=sent_run_id,
                        approval_code=sent_approval_code,
                        raw_task=pending_task,
                        intent=apply_intent_defaults(dict(sent_intent)),
                        manifest=sent_manifest,
                        planner_message="",
                        baseline_after_task={record_key(record) for record in message_records(page)},
                        timeout_error_type=PlaywrightTimeoutError,
                    )
            except Exception as exc:
                ok, send_error = send_course_agent_message(
                    page,
                    chat,
                    blocked_agent_text(
                        run_id=sent_run_id,
                        task=first_text(sent_intent.get("raw_task"), sent_intent.get("interpreted_intent")),
                        reason=exc,
                    ),
                )
                post_json(
                    f"{base_url}/agent/progress",
                    {
                        "event": "approval_request_send_failed",
                        "run_id": sent_run_id,
                        "agent_name": "course-agent-app",
                        "chat_name": chat,
                        "detail": str(exc) if ok else f"{exc}; additionally failed to send WhatsApp blocker: {send_error}",
                        "evidence": "Course-agent app failed while recovering a sent approval wait.",
                    },
                )
                return

        while True:
            ACTIVE_STAFF_PROMPT_CONTEXT = None
            start_result = request_json(f"{base_url}/start")
            run_id = start_result["run_id"]
            approval_code = str(start_result.get("approval_code", "")).strip()
            post_json(
                f"{base_url}/agent/progress",
                {
                    "event": "staff_prompt_wait_started",
                    "run_id": run_id,
                    "agent_name": "course-agent-app",
                    "chat_name": chat,
                    "detail": "Waiting for a new staff prompt in the configured WhatsApp chat.",
                },
            )
            prompt_context = wait_for_staff_task_context(
                page,
                env,
                baseline,
                run_id,
                wait_started_at=wait_boundary_at,
            )
            raw_task, baseline_after_task, prompt_observed_at = prompt_context
            ACTIVE_STAFF_PROMPT_CONTEXT = prompt_context
            env["_WHATSAPP_REQUESTER"] = prompt_context.requester
            env["_WHATSAPP_ROOT_MESSAGE_ID"] = prompt_context.message_id
            baseline = set(baseline_after_task)
            wait_boundary_at = prompt_observed_at
            print(f"Received staff prompt for run {run_id}: {raw_task}")
            if is_agent_stop_command(raw_task):
                ok, send_error = send_course_agent_message(page, chat, lifecycle_stop_text("staff requested shutdown"))
                shutdown_notice_sent = ok
                if not ok:
                    print(f"Could not send WhatsApp shutdown notice: {send_error}")
                post_json(
                    f"{base_url}/agent/progress",
                    {
                        "event": "course_agent_shutdown_requested",
                        "run_id": run_id,
                        "agent_name": "course-agent-app",
                        "chat_name": chat,
                        "detail": raw_task,
                        "evidence": "Staff sent an explicit lifecycle stop command in the configured WhatsApp chat.",
                    },
                )
                print(f"Staff requested shutdown for run {run_id}.")
                break
            post_json(
                f"{base_url}/agent/progress",
                {
                    "event": "staff_prompt_received",
                    "run_id": run_id,
                    "agent_name": "course-agent-app",
                    "chat_name": chat,
                    "detail": raw_task,
                    "evidence": "New non-command message observed after the listener baseline.",
                    "message_id": prompt_context.message_id,
                    "whatsapp_requester": prompt_context.requester,
                    "whatsapp_root_message_id": prompt_context.message_id,
                    "whatsapp_last_inbound_message_id": prompt_context.message_id,
                    "whatsapp_active_workflow": run_id,
                    "whatsapp_chat_type": prompt_context.chat_type,
                    "whatsapp_agent_display_name": agent_display_name,
                    "whatsapp_mention_ready": bool(whatsapp_context.get("mention_ready")),
                    "whatsapp_provenance_confidence": prompt_context.provenance_confidence,
                },
            )
            send_course_agent_message(
                page,
                chat,
                f"Request received.\n\nI am checking it now.\nRun: {run_id}",
                purpose="acknowledgement",
                run_id=run_id,
            )
            try:
                detailed_group_structure: dict | None = None
                detailed_forum_structure: dict | None = None
                intent, manifest, planner_message = run_codex_planner(
                    env,
                    run_id,
                    raw_task,
                    detailed_forum_structure=detailed_forum_structure,
                )
                if manifest.get("needs_group_structure_readback"):
                    group_readback_completed = False
                    try:
                        if moodle_context is None or moodle_page is None:
                            raise RuntimeError(
                                "This Group request needs a detailed Moodle browser readback, "
                                "but the sandbox browser session is unavailable."
                            )
                        post_json(
                            f"{base_url}/agent/progress",
                            {
                                "event": "group_structure_readback_started",
                                "run_id": run_id,
                                "agent_name": "course-agent-app",
                                "chat_name": chat,
                                "detail": "Planner selected a Group capability; collecting bounded read-only Group structure.",
                                "evidence": "No Moodle save or submit action is permitted during this step.",
                            },
                        )
                        detailed_group_structure = detailed_group_structure_for_manifest(
                            env,
                            manifest,
                            moodle_page,
                            run_id=run_id,
                        )
                        group_readback_completed = True
                        intent, manifest, planner_message = run_codex_planner(
                            env,
                            run_id,
                            raw_task,
                            detailed_group_structure=detailed_group_structure,
                            detailed_forum_structure=detailed_forum_structure,
                        )
                        if manifest.get("needs_group_structure_readback"):
                            raise RuntimeError(
                                "Group planning still requested another structure readback "
                                "after the bounded detailed pass."
                            )
                        post_json(
                            f"{base_url}/agent/progress",
                            {
                                "event": "group_structure_readback_completed",
                                "run_id": run_id,
                                "agent_name": "course-agent-app",
                                "chat_name": chat,
                                "detail": "Detailed Group structure was collected and the request was replanned once.",
                                "evidence": "Sanitized member counts, Grouping relationships, and Choicegroup settings.",
                            },
                        )
                    except Exception as exc:
                        if group_readback_completed:
                            raise
                        print(
                            f"Group structure planning readback blocked for run {run_id}: {exc}"
                        )
                        blocker, blocker_category = group_structure_readback_blocker(exc)
                        send_course_agent_message(
                            page,
                            chat,
                            blocked_agent_text(
                                run_id=run_id,
                                task=raw_task,
                                reason=blocker,
                            ),
                        )
                        post_json(
                            f"{base_url}/agent/course-task-intent",
                            {"run_id": run_id, "course_task_intent": intent},
                        )
                        post_json(
                            f"{base_url}/agent/course-agent-result",
                            {
                                "run_id": run_id,
                                "course_agent_result": {
                                    **blocked_group_preflight_result(
                                        run_id,
                                        blocker,
                                    ),
                                    "blocker_category": blocker_category,
                                },
                            },
                        )
                        continue
                if manifest.get("needs_forum_structure_readback"):
                    forum_readback_completed = False
                    try:
                        if moodle_context is None or moodle_page is None:
                            raise RuntimeError(
                                "This Forum request needs a detailed Moodle browser readback, "
                                "but the sandbox browser session is unavailable."
                            )
                        post_json(
                            f"{base_url}/agent/progress",
                            {
                                "event": "forum_structure_readback_started",
                                "run_id": run_id,
                                "agent_name": "course-agent-app",
                                "chat_name": chat,
                                "detail": "Planner selected a Forum inspection capability; collecting a bounded read-only discussion inventory.",
                                "evidence": "No Moodle save, submit, edit, or delete action is permitted during this step.",
                            },
                        )
                        detailed_forum_structure = detailed_forum_structure_for_planning(
                            env,
                            moodle_page,
                            run_id=run_id,
                            manifest=manifest,
                        )
                        forum_readback_completed = True
                        intent, manifest, planner_message = run_codex_planner(
                            env,
                            run_id,
                            raw_task,
                            detailed_group_structure=detailed_group_structure,
                            detailed_forum_structure=detailed_forum_structure,
                        )
                        if manifest.get("needs_forum_structure_readback"):
                            raise RuntimeError(
                                "Forum planning still requested another structure readback "
                                "after the bounded detailed pass."
                            )
                        if manifest.get("needs_group_structure_readback"):
                            raise RuntimeError(
                                "Forum replanning introduced an unresolved Group structure readback."
                            )
                        post_json(
                            f"{base_url}/agent/progress",
                            {
                                "event": "forum_structure_readback_completed",
                                "run_id": run_id,
                                "agent_name": "course-agent-app",
                                "chat_name": chat,
                                "detail": "Detailed Forum discussion inventory was collected and the request was replanned once.",
                                "evidence": "Sanitized discussion ids, titles, and URLs only; no author identity or message body.",
                            },
                        )
                    except Exception as exc:
                        if forum_readback_completed:
                            raise
                        blocker = (
                            "The agent could not complete the required automatic read-only Forum "
                            f"inventory after retrying the sandbox Moodle page: {str(exc)[:500]}"
                        )
                        print(
                            f"Forum structure planning readback blocked for run {run_id}: {exc}"
                        )
                        send_course_agent_message(
                            page,
                            chat,
                            blocked_agent_text(
                                run_id=run_id,
                                task=raw_task,
                                reason=blocker,
                            ),
                        )
                        post_json(
                            f"{base_url}/agent/course-task-intent",
                            {"run_id": run_id, "course_task_intent": intent},
                        )
                        post_json(
                            f"{base_url}/agent/course-agent-result",
                            {
                                "run_id": run_id,
                                "course_agent_result": {
                                    "run_id": run_id,
                                    "status": "BLOCKED",
                                    "summary": blocker,
                                    "verification": {
                                        "status": "BLOCKED",
                                        "reason": "required Forum discussion inventory unavailable",
                                    },
                                },
                            },
                        )
                        continue
                manifest = prepare_manifest_for_local_jobs(env, run_id, raw_task, manifest)
                manifest = normalize_reference_pack_inputs(env, raw_task, manifest)
                post_json(f"{base_url}/agent/course-task-intent", {"run_id": run_id, "course_task_intent": intent})
                if manifest_needs_reference_pack_attachments(manifest, env, raw_task):
                    reference_collection_task = raw_task
                    raw_task, baseline_after_task = collect_reference_pack_attachments_for_task(
                        page,
                        env,
                        raw_task,
                        baseline_after_task,
                        run_id,
                        base_url,
                        prompt_observed_at=prompt_observed_at,
                        excluded_source_paths=refresh_preview_source_paths_for_reference_collection(
                            manifest,
                            reference_collection_task,
                        ),
                        prompt_context=prompt_context,
                    )
                    baseline = set(baseline_after_task)
                    collection_seconds, expected_count, _quiet_seconds = reference_pack_collection_settings(env)
                    downloaded_count = reference_collection_verified_attachment_count(raw_task)
                    if downloaded_count < expected_count:
                        timeout_detail = (
                            f"Reference attachment collection timed out after {collection_seconds:g}s "
                            f"with {downloaded_count}/{expected_count} verified attachment(s)."
                        )
                        timeout_message = (
                            reference_collection_waiting_text(
                                run_id=run_id,
                                task=reference_collection_task,
                                expected_count=expected_count,
                                downloaded_count=downloaded_count,
                                wait_seconds=collection_seconds,
                            )
                            if downloaded_count > 0
                            else reference_collection_timeout_text(
                                run_id=run_id,
                                task=reference_collection_task,
                                expected_count=expected_count,
                                downloaded_count=downloaded_count,
                                wait_seconds=collection_seconds,
                            )
                        )
                        send_course_agent_message(
                            page,
                            chat,
                            timeout_message,
                            purpose="action_required",
                            action_kind="upload",
                        )
                        post_json(
                            f"{base_url}/agent/progress",
                            {
                                "event": "agent_timeout_needs_human_check",
                                "run_id": run_id,
                                "agent_name": "course-agent-app",
                                "chat_name": chat,
                                "detail": timeout_detail,
                                "evidence": "Reference attachment collection ended before all required verified files were available; no Moodle write was attempted.",
                            },
                        )
                        continue
                    post_json(
                        f"{base_url}/agent/progress",
                        {
                            "event": "reference_attachment_collection_completed",
                            "run_id": run_id,
                            "agent_name": "course-agent-app",
                            "chat_name": chat,
                            "detail": "Planner selected reference pack rebuild; runtime collected follow-up reference attachments.",
                            "evidence": "Reference attachment collection is a local pre-planner-input repair, not a Moodle write.",
                        },
                    )
                    intent, manifest, planner_message = run_codex_planner(
                        env,
                        run_id,
                        raw_task,
                        detailed_group_structure=detailed_group_structure,
                        detailed_forum_structure=detailed_forum_structure,
                    )
                    manifest = prepare_manifest_for_local_jobs(env, run_id, raw_task, manifest)
                    manifest = normalize_reference_pack_inputs(env, raw_task, manifest)
                    post_json(f"{base_url}/agent/course-task-intent", {"run_id": run_id, "course_task_intent": intent})
                    if manifest_needs_reference_pack_attachments(manifest, env, raw_task):
                        blocker = "No verified reference files were uploaded during the collection window."
                        send_course_agent_message(
                            page,
                            chat,
                            blocked_agent_text(run_id=run_id, task=raw_task, reason=blocker),
                            purpose="action_required",
                            action_kind="missing_input",
                        )
                        post_json(
                            f"{base_url}/agent/progress",
                            {
                                "event": "approval_request_send_failed",
                                "run_id": run_id,
                                "agent_name": "course-agent-app",
                                "chat_name": chat,
                                "detail": blocker,
                                "evidence": "Reference pack rebuild stayed missing verified attachment paths after collection.",
                            },
                        )
                        continue
                source_blocker = (
                    moodle_file_source_blocker(manifest, raw_task)
                    or reviewed_group_snapshot_drift_blocker(manifest, env)
                    or refresh_preview_source_blocker(manifest, raw_task)
                    or refresh_batch_preview_source_blocker(manifest, raw_task)
                    or semester_schedule_source_blocker(manifest, raw_task)
                )
                if source_blocker:
                    send_course_agent_message(
                        page,
                        chat,
                        blocked_agent_text(run_id=run_id, task=raw_task, reason=source_blocker),
                        purpose="action_required",
                        action_kind="missing_input",
                    )
                    post_json(
                        f"{base_url}/agent/progress",
                        {
                            "event": "approval_request_send_failed",
                            "run_id": run_id,
                            "agent_name": "course-agent-app",
                            "chat_name": chat,
                            "detail": source_blocker,
                            "evidence": "File source did not come from a current prompt WhatsApp attachment local_path.",
                        },
                    )
                    continue
                sensitive_whatsapp_action = (
                    manifest_has_timeline_draft_action(manifest)
                    or manifest_has_finalize_file_batch_preview(manifest)
                    or any(
                    str(operation.get("execution_mode") or "") != "local_file_action"
                    for operation in manifest.get("operations", [])
                    if isinstance(operation, dict)
                    )
                )
                if (
                    sensitive_whatsapp_action
                    and prompt_context.provenance_confidence != "high"
                ):
                    blocker = (
                        "This approval or timeline action needs a stable current WhatsApp message ID. "
                        "Please mention the agent and send the request again."
                    )
                    send_course_agent_message(
                        page,
                        chat,
                        blocked_agent_text(run_id=run_id, task=raw_task, reason=blocker),
                        purpose="action_required",
                        action_kind="missing_input",
                    )
                    post_json(
                        f"{base_url}/agent/progress",
                        {
                            "event": "approval_request_send_failed",
                            "run_id": run_id,
                            "agent_name": "course-agent-app",
                            "chat_name": chat,
                            "detail": blocker,
                            "evidence": "Sensitive WhatsApp action was not bound to a high-confidence message ID.",
                        },
                    )
                    continue
                unsupported_target, unsupported_reason = unsupported_access_time_blocker(manifest, env)
                if unsupported_reason:
                    send_course_agent_message(
                        page,
                        chat,
                        natural_blocked_result_text(run_id, raw_task, unsupported_target, unsupported_reason),
                    )
                    post_json(
                        f"{base_url}/agent/progress",
                        {
                            "event": "approval_request_send_failed",
                            "run_id": run_id,
                            "agent_name": "course-agent-app",
                            "chat_name": chat,
                            "detail": unsupported_reason,
                            "evidence": "Access restriction time writes are not enabled for the current APIM/tooling.",
                        },
                    )
                    continue
                if manifest_has_timeline_draft_action(manifest) and not (
                    manifest_is_only_timeline_draft_revision(manifest)
                    or manifest_is_only_timeline_draft_commit(manifest)
                ):
                    blocker = (
                        "Timeline draft revision or confirmation must be the only local action in the current prompt."
                    )
                    send_course_agent_message(
                        page,
                        chat,
                        blocked_agent_text(run_id=run_id, task=raw_task, reason=blocker),
                        purpose="action_required",
                        action_kind="review",
                    )
                    post_json(
                        f"{base_url}/agent/progress",
                        {
                            "event": "approval_request_send_failed",
                            "run_id": run_id,
                            "agent_name": "course-agent-app",
                            "chat_name": chat,
                            "detail": blocker,
                            "evidence": "Timeline draft review actions cannot be combined with another local or Moodle operation.",
                        },
                    )
                    continue
                if manifest_is_only_timeline_draft_revision(manifest):
                    result = execute_timeline_draft_revision_manifest(env, run_id, raw_task, manifest)
                    send_timeline_draft_action_result_to_chat(
                        page,
                        chat,
                        timeline_draft_action_result_text(run_id, raw_task, result),
                        result,
                        run_id,
                    )
                    post_json(
                        f"{base_url}/agent/progress",
                        {
                            "event": "approval_report_posted",
                            "run_id": run_id,
                            "agent_name": "course-agent-app",
                            "chat_name": chat,
                            "detail": "Timeline draft revision result sent; canonical references and Moodle were unchanged.",
                            "evidence": result.get("result_path", ""),
                        },
                    )
                    continue
                if manifest_is_only_timeline_draft_commit(manifest):
                    result = execute_timeline_draft_commit_manifest(env, run_id, raw_task, manifest)
                    send_timeline_draft_action_result_to_chat(
                        page,
                        chat,
                        timeline_draft_action_result_text(run_id, raw_task, result),
                        result,
                        run_id,
                    )
                    post_json(
                        f"{base_url}/agent/progress",
                        {
                            "event": "approval_report_posted",
                            "run_id": run_id,
                            "agent_name": "course-agent-app",
                            "chat_name": chat,
                            "detail": "Timeline draft commit result sent; this was a local reference operation with no Moodle write.",
                            "evidence": result.get("result_path", ""),
                        },
                    )
                    continue
                if manifest_has_refresh_batch_preview(manifest) and not manifest_is_only_refresh_batch_preview(manifest):
                    blocker = "A multi-file refresh review must run separately from every other local or Moodle operation."
                    send_course_agent_message(
                        page,
                        chat,
                        blocked_agent_text(run_id=run_id, task=raw_task, reason=blocker),
                        purpose="action_required",
                        action_kind="review",
                    )
                    post_json(
                        f"{base_url}/agent/progress",
                        {
                            "event": "approval_request_send_failed",
                            "run_id": run_id,
                            "agent_name": "course-agent-app",
                            "chat_name": chat,
                            "detail": blocker,
                            "evidence": "Planner mixed a multi-file local review with another operation.",
                        },
                    )
                    continue
                if manifest_has_finalize_file_batch_preview(manifest) and not manifest_is_only_finalize_file_batch_preview(manifest):
                    blocker = "Final combined PDF generation must be the only operation in the current prompt."
                    send_course_agent_message(
                        page,
                        chat,
                        blocked_agent_text(run_id=run_id, task=raw_task, reason=blocker),
                        purpose="action_required",
                        action_kind="review",
                    )
                    post_json(
                        f"{base_url}/agent/progress",
                        {
                            "event": "approval_request_send_failed",
                            "run_id": run_id,
                            "agent_name": "course-agent-app",
                            "chat_name": chat,
                            "detail": blocker,
                            "evidence": "Planner mixed final batch PDF generation with another operation.",
                        },
                    )
                    continue
                if manifest_is_only_refresh_batch_preview(manifest):
                    result = execute_refresh_batch_preview_manifest(
                        env,
                        run_id,
                        raw_task,
                        manifest,
                        detailed_group_structure=detailed_group_structure,
                    )
                    delivered_result = send_file_batch_preview_result_to_chat(
                        page,
                        chat,
                        result,
                        run_id,
                        raw_task,
                    )
                    post_json(
                        f"{base_url}/agent/progress",
                        {
                            "event": "approval_report_posted",
                            "run_id": run_id,
                            "agent_name": "course-agent-app",
                            "chat_name": chat,
                            "detail": "Multi-file review PDFs were sent individually; no Moodle approval or write was required.",
                            "evidence": delivered_result.get("result_path", ""),
                        },
                    )
                    continue
                if manifest_is_only_finalize_file_batch_preview(manifest):
                    result = execute_finalize_file_batch_preview_manifest(env, run_id, raw_task, manifest)
                    send_final_file_batch_result_to_chat(
                        page,
                        chat,
                        result,
                        run_id,
                        raw_task,
                    )
                    post_json(
                        f"{base_url}/agent/progress",
                        {
                            "event": "approval_report_posted",
                            "run_id": run_id,
                            "agent_name": "course-agent-app",
                            "chat_name": chat,
                            "detail": "Final combined PDF result sent; no Moodle approval or write was required.",
                            "evidence": result.get("result_path", ""),
                        },
                    )
                    continue
                if manifest_is_schedule_update_then_refresh_preview(manifest):
                    result = execute_local_file_preparation_manifest(env, run_id, raw_task, manifest)
                    if str(result.get("status") or "") == "NEEDS_TIMELINE_REVIEW":
                        send_semester_schedule_update_result_to_chat(
                            page,
                            chat,
                            semester_schedule_update_result_text(run_id, raw_task, result),
                            result,
                            run_id,
                        )
                    else:
                        send_refresh_preview_result_to_chat(
                            page,
                            chat,
                            refresh_preview_result_text(run_id, raw_task, result),
                            result,
                            run_id,
                        )
                    post_json(
                        f"{base_url}/agent/progress",
                        {
                            "event": "approval_report_posted",
                            "run_id": run_id,
                            "agent_name": "course-agent-app",
                            "chat_name": chat,
                            "detail": "Local semester schedule update and refreshed PDF review copy completed; no Moodle approval or write was required.",
                            "evidence": result.get("result_path", ""),
                        },
                    )
                    continue
                if manifest_is_reference_rebuild_then_refresh_preview(manifest):
                    rebuild_result, result = execute_reference_rebuild_then_refresh_preview_manifest(
                        env,
                        run_id,
                        raw_task,
                        manifest,
                    )
                    send_reference_pack_rebuild_result_to_chat(
                        page,
                        chat,
                        reference_pack_rebuild_result_text(
                            run_id,
                            raw_task,
                            rebuild_result,
                            continuing_refresh=str(rebuild_result.get("status") or "") == "READY",
                        ),
                        rebuild_result,
                        run_id,
                    )
                    post_json(
                        f"{base_url}/agent/progress",
                        {
                            "event": "approval_report_posted",
                            "run_id": run_id,
                            "agent_name": "course-agent-app",
                            "chat_name": chat,
                            "detail": "Reference pack rebuild result sent before the current file refresh continued; no Moodle write was required.",
                            "evidence": rebuild_result.get("result_path", ""),
                        },
                    )
                    if result is None:
                        continue
                    send_refresh_preview_result_to_chat(
                        page,
                        chat,
                        refresh_preview_result_text(run_id, raw_task, result),
                        result,
                        run_id,
                    )
                    post_json(
                        f"{base_url}/agent/progress",
                        {
                            "event": "approval_report_posted",
                            "run_id": run_id,
                            "agent_name": "course-agent-app",
                            "chat_name": chat,
                            "detail": "Local reference preparation and refreshed PDF review copy completed; no Moodle approval or write was required.",
                            "evidence": result.get("result_path", ""),
                        },
                    )
                    continue
                if manifest_has_semester_schedule_update(manifest) and not manifest_is_only_semester_schedule_update(manifest):
                    blocker = "Semester schedule updates must run locally before any Moodle course operation."
                    send_course_agent_message(
                        page,
                        chat,
                        blocked_agent_text(run_id=run_id, task=raw_task, reason=blocker),
                        purpose="action_required",
                        action_kind="review",
                    )
                    post_json(
                        f"{base_url}/agent/progress",
                        {
                            "event": "approval_request_send_failed",
                            "run_id": run_id,
                            "agent_name": "course-agent-app",
                            "chat_name": chat,
                            "detail": blocker,
                            "evidence": "Planner mixed a local semester schedule update with another non-refresh operation.",
                        },
                    )
                    continue
                if manifest_has_refresh_preview(manifest) and not manifest_is_only_refresh_preview(manifest):
                    blocker = "The refreshed PDF review copy must be prepared separately before any Moodle upload or replacement."
                    send_course_agent_message(
                        page,
                        chat,
                        blocked_agent_text(run_id=run_id, task=raw_task, reason=blocker),
                        purpose="action_required",
                        action_kind="review",
                    )
                    post_json(
                        f"{base_url}/agent/progress",
                        {
                            "event": "approval_request_send_failed",
                            "run_id": run_id,
                            "agent_name": "course-agent-app",
                            "chat_name": chat,
                            "detail": blocker,
                            "evidence": "Planner mixed a local PDF review-copy operation with Moodle write operations.",
                        },
                    )
                    continue
                if manifest_has_reference_pack_rebuild(manifest) and not manifest_is_only_reference_pack_rebuild(manifest):
                    blocker = "Reference preparation must run separately before any Moodle course operation."
                    send_course_agent_message(
                        page,
                        chat,
                        blocked_agent_text(run_id=run_id, task=raw_task, reason=blocker),
                        purpose="action_required",
                        action_kind="review",
                    )
                    post_json(
                        f"{base_url}/agent/progress",
                        {
                            "event": "approval_request_send_failed",
                            "run_id": run_id,
                            "agent_name": "course-agent-app",
                            "chat_name": chat,
                            "detail": blocker,
                            "evidence": "Planner mixed a local reference pack rebuild with another operation.",
                        },
                    )
                    continue
                if manifest_is_only_reference_pack_rebuild(manifest):
                    result = execute_reference_pack_rebuild_manifest(env, run_id, raw_task, manifest)
                    send_reference_pack_rebuild_result_to_chat(
                        page,
                        chat,
                        reference_pack_rebuild_result_text(run_id, raw_task, result),
                        result,
                        run_id,
                    )
                    post_json(
                        f"{base_url}/agent/progress",
                        {
                            "event": "approval_report_posted",
                            "run_id": run_id,
                            "agent_name": "course-agent-app",
                            "chat_name": chat,
                            "detail": "Reference pack rebuild result sent; no Moodle approval or write was required.",
                            "evidence": result.get("result_path", ""),
                        },
                    )
                    continue
                if manifest_is_only_semester_schedule_update(manifest):
                    result = execute_semester_schedule_update_manifest(env, run_id, raw_task, manifest)
                    send_semester_schedule_update_result_to_chat(
                        page,
                        chat,
                        semester_schedule_update_result_text(run_id, raw_task, result),
                        result,
                        run_id,
                    )
                    post_json(
                        f"{base_url}/agent/progress",
                        {
                            "event": "approval_report_posted",
                            "run_id": run_id,
                            "agent_name": "course-agent-app",
                            "chat_name": chat,
                            "detail": "Semester schedule update result sent; no Moodle approval or write was required.",
                            "evidence": result.get("result_path", ""),
                        },
                    )
                    continue
                if manifest_is_only_refresh_preview(manifest):
                    result = execute_refresh_preview_manifest(
                        env,
                        run_id,
                        raw_task,
                        manifest,
                        detailed_group_structure=detailed_group_structure,
                    )
                    send_refresh_preview_result_to_chat(
                        page,
                        chat,
                        refresh_preview_result_text(run_id, raw_task, result),
                        result,
                        run_id,
                    )
                    post_json(
                        f"{base_url}/agent/progress",
                        {
                            "event": "approval_report_posted",
                            "run_id": run_id,
                            "agent_name": "course-agent-app",
                            "chat_name": chat,
                            "detail": "Single-file refreshed PDF review copy sent; no Moodle approval or write was required.",
                            "evidence": result.get("result_path", ""),
                        },
                    )
                    continue
                group_readback_operations = [
                    operation
                    for operation in manifest.get("operations") or []
                    if isinstance(operation, dict)
                    and operation.get("operation_kind") == GROUP_STRUCTURE_READBACK_KIND
                ]
                if group_readback_operations and not manifest_is_only_group_structure_readback(manifest):
                    blocker = (
                        "Read-only Group inspection must finish before a Moodle write is approved; "
                        "it cannot remain mixed with write operations."
                    )
                    send_course_agent_message(
                        page,
                        chat,
                        blocked_agent_text(run_id=run_id, task=raw_task, reason=blocker),
                    )
                    post_json(
                        f"{base_url}/agent/course-operation-manifest",
                        {"run_id": run_id, "course_operation_manifest": manifest},
                    )
                    post_json(
                        f"{base_url}/agent/course-agent-result",
                        {
                            "run_id": run_id,
                            "course_agent_result": blocked_group_preflight_result(
                                run_id,
                                blocker,
                            ),
                        },
                    )
                    continue
                if manifest_is_only_group_structure_readback(manifest):
                    if detailed_group_structure is None:
                        detailed_group_structure = detailed_group_structure_for_manifest(
                            env,
                            manifest,
                            moodle_page,
                            run_id=run_id,
                        )
                    post_json(
                        f"{base_url}/agent/course-operation-manifest",
                        {"run_id": run_id, "course_operation_manifest": manifest},
                    )
                    post_json(
                        f"{base_url}/agent/course-agent-result",
                        {
                            "run_id": run_id,
                            "course_agent_result": read_only_group_course_agent_result(
                                run_id,
                                detailed_group_structure,
                                "Detailed sandbox Group structure readback completed.",
                            ),
                        },
                    )
                    send_course_agent_message(
                        page,
                        chat,
                        group_structure_result_text(
                            run_id,
                            raw_task,
                            detailed_group_structure,
                            manifest,
                        ),
                    )
                    post_json(
                        f"{base_url}/agent/progress",
                        {
                            "event": "approval_report_posted",
                            "run_id": run_id,
                            "agent_name": "course-agent-app",
                            "chat_name": chat,
                            "detail": "Detailed Group structure result sent; no approval or Moodle write was used.",
                            "evidence": "Sanitized Group counts and relationships only.",
                        },
                    )
                    continue
                if (
                    not (manifest.get("operations") or [])
                    and manifest.get("resolution_status") == "no_changes"
                ):
                    if detailed_group_structure is not None:
                        post_json(
                            f"{base_url}/agent/course-operation-manifest",
                            {"run_id": run_id, "course_operation_manifest": manifest},
                        )
                        post_json(
                            f"{base_url}/agent/course-agent-result",
                            {
                                "run_id": run_id,
                                "course_agent_result": read_only_group_course_agent_result(
                                    run_id,
                                    detailed_group_structure,
                                    "The requested Group state already matched Moodle.",
                                ),
                            },
                        )
                    send_course_agent_message(
                        page,
                        chat,
                        no_group_changes_result_text(run_id, raw_task, manifest),
                    )
                    continue
                if not (manifest.get("operations") or []):
                    forum_result = try_forum_readonly_result_text(
                        run_id=run_id,
                        raw_task=raw_task,
                        intent=intent,
                        manifest=manifest,
                        planner_message=planner_message,
                        env=env,
                        moodle_page=moodle_page,
                        moodle_available=moodle_context is not None,
                    )
                    if forum_result:
                        send_read_only_result_and_record(
                            page,
                            chat,
                            forum_result,
                            base_url=base_url,
                            run_id=run_id,
                            detail="Read-only forum discussion result sent; no approval or write was required.",
                            evidence="Moodle browser readback of the forum discussion list.",
                        )
                        continue
                    if is_user_facing_noop_message(manifest, planner_message):
                        send_read_only_result_and_record(
                            page,
                            chat,
                            user_facing_noop_message_text(run_id, planner_message),
                            base_url=base_url,
                            run_id=run_id,
                            detail="Read-only Moodle result sent; no approval or write was required.",
                            evidence="Planner returned a read-only no-op result.",
                        )
                        continue
                    if is_read_only_noop_result(intent, manifest, planner_message):
                        send_read_only_result_and_record(
                            page,
                            chat,
                            read_only_noop_result_text(run_id, raw_task, manifest, planner_message),
                            base_url=base_url,
                            run_id=run_id,
                            detail="Read-only Moodle result sent; no approval or write was required.",
                            evidence="Planner returned a read-only no-op result.",
                        )
                        continue
                    blocker = planner_message or "No supported Moodle operation was planned."
                    send_course_agent_message(
                        page,
                        chat,
                        blocked_agent_text(run_id=run_id, task=raw_task, reason=blocker),
                        purpose="action_required",
                        action_kind="missing_input",
                    )
                    post_json(
                        f"{base_url}/agent/progress",
                        {
                            "event": "approval_request_send_failed",
                            "run_id": run_id,
                            "agent_name": "course-agent-app",
                            "chat_name": chat,
                            "detail": blocker,
                            "evidence": "Planner returned no supported operation; no approval request was sent.",
                        },
                    )
                    continue
                post_json(
                    f"{base_url}/agent/course-operation-manifest",
                    {"run_id": run_id, "course_operation_manifest": manifest},
                )
                run_approval_execution_cycle(
                    page=page,
                    moodle_page=moodle_page,
                    moodle_available=moodle_context is not None,
                    env=env,
                    moodle_env=moodle_env,
                    base_url=base_url,
                    chat=chat,
                    run_id=run_id,
                    approval_code=approval_code,
                    raw_task=raw_task,
                    intent=intent,
                    manifest=manifest,
                    planner_message=planner_message,
                    baseline_after_task=baseline_after_task,
                    timeout_error_type=PlaywrightTimeoutError,
                )
            except Exception as exc:
                ok, send_error = send_course_agent_message(
                    page,
                    chat,
                    blocked_agent_text(run_id=run_id, task=raw_task, reason=exc),
                )
                post_json(
                    f"{base_url}/agent/progress",
                    {
                        "event": "approval_request_send_failed",
                        "run_id": run_id,
                        "agent_name": "course-agent-app",
                        "chat_name": chat,
                        "detail": str(exc) if ok else f"{exc}; additionally failed to send WhatsApp blocker: {send_error}",
                        "evidence": "Course-agent app failed before completing the approval cycle.",
                    },
                )
            if env.get("CODEX_LISTENER_FOREVER", "1").strip().lower() not in {"1", "true", "yes", "on"}:
                break
            time.sleep(float(env.get("CODEX_LISTENER_NEXT_RUN_DELAY", "1")))
    finally:
        if (
            lifecycle_whatsapp_messages_enabled(env)
            and page is not None
            and not shutdown_notice_sent
        ):
            ok, send_error = send_course_agent_message(page, chat, lifecycle_stop_text("agent process is stopping"))
            shutdown_notice_sent = ok
            if not ok:
                print(f"Could not send WhatsApp shutdown notice: {send_error}")
        if env.get("COURSE_AGENT_BROWSER_LIFECYCLE", "agent").strip().lower() not in {"agent", "daemon"}:
            context.close()
            if moodle_context is not None:
                moodle_context.close()
        playwright.stop()
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    main()
