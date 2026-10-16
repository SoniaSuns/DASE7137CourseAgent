from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from whatsapp_approval import approval_reply_command  # noqa: E402
from moodle_operations import normalize_write_policy  # noqa: E402
from course_agent import codex_no_run, unsupported_legacy_env_warnings  # noqa: E402
from run_agent_smoke import (
    ROOT,
    agent_model,
    build_codex_prompt,
    build_moodle_codex_command,
    build_moodle_codex_prompt,
    build_codex_command,
    load_env,
    post_json,
    request_json,
    start_server,
)
from run_whatsapp_web_smoke import (
    import_playwright,
    launch_whatsapp_context,
    run_whatsapp_web_approval,
    whatsapp_web_agent_name,
)


TERMINAL_STATUSES = {
    "approved",
    "moodle_agent_checklist_pending",
    "moodle_browser_action_approval_pending",
    "dry_run_written",
    "moodle_skipped",
    "course_planning_approved",
    "course_agent_manifest_blocked",
    "course_agent_operations_verified",
    "course_agent_operations_blocked",
    "course_agent_operations_failed",
    "course_agent_result_verified",
    "course_agent_result_blocked",
    "course_agent_result_failed",
    "rejected",
    "moodle_external_action_verified",
    "moodle_external_action_blocked",
    "moodle_external_action_failed",
    "moodle_api_action_verified",
    "moodle_api_action_blocked",
    "moodle_api_action_failed",
    "agent_whatsapp_send_failed",
    "agent_timeout_needs_human_check",
    "moodle_agent_timeout_needs_human_check",
}
MOODLE_AGENT_PENDING_STATUS = "moodle_agent_browser_action_pending"
MOODLE_AGENT_TERMINAL_STATUSES = {
    "moodle_external_action_verified",
    "moodle_external_action_blocked",
    "moodle_external_action_failed",
    "moodle_agent_timeout_needs_human_check",
}
WHATSAPP_SENT_STATUSES = {
    "agent_whatsapp_sent_waiting_for_reply",
    "agent_timeout_after_send_waiting_reply",
}
WHATSAPP_REPLY_OBSERVED_STATUSES = {
    "agent_whatsapp_reply_observed",
    "agent_timeout_after_reply_observed",
}
MOODLE_EXECUTE_DONE_EVENTS = {
    "moodle_execute_save_completed",
    "moodle_execute_verified",
}
MOODLE_ROLLBACK_DONE_EVENTS = {
    "moodle_rollback_save_completed",
    "moodle_rollback_verified",
    "moodle_result_post_started",
}
MOODLE_PRECHECK_DONE_EVENTS = {
    "moodle_precheck_passed",
    "moodle_precheck_blocked",
}


def env_float(env: dict[str, str], key: str, default: float) -> float:
    try:
        return float(env.get(key, str(default)))
    except ValueError:
        return default


def env_flag(env: dict[str, str], key: str, default: bool = False) -> bool:
    raw = env.get(key, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def env_int(env: dict[str, str], key: str, default: int) -> int:
    try:
        return int(env.get(key, str(default)))
    except ValueError:
        return default


def agent_browser_lifecycle(env: dict[str, str]) -> str:
    raw = env.get("COURSE_AGENT_BROWSER_LIFECYCLE", "agent").strip().lower().replace("-", "_")
    if raw in {"approval", "per_approval", "run", "per_run"}:
        return "per_approval"
    return "agent"


def course_agent_forever(env: dict[str, str]) -> bool:
    if "CODEX_LISTENER_FOREVER" in env:
        return env_flag(env, "CODEX_LISTENER_FOREVER")
    return env_flag(env, "COURSE_AGENT_FREE_TASK_MODE", True)


def start_new_run(env: dict[str, str], base_url: str) -> dict:
    start_result = request_json(f"{base_url}/start")
    run_id = start_result["run_id"]
    approval_code = start_result.get("approval_code", "")
    print("\n=== New approval run ===")
    print(f"Run id: {run_id}")
    if approval_code:
        print(f"Approval code: {approval_code}")
    print(f"Agent task: {start_result['agent_task_path']}")
    print(f"Reply command: {approval_reply_command('APPROVE', env['BATCH_ID'], run_id, approval_code=approval_code)}")
    for attachment in start_result.get("approval_attachments", []) or []:
        print(f"Approval attachment: {attachment.get('path', '')}")
    return start_result


def whatsapp_approval_driver(env: dict[str, str]) -> str:
    raw = env.get("CODEX_WHATSAPP_APPROVAL_DRIVER") or env.get("WHATSAPP_APPROVAL_DRIVER") or "codex_agent"
    return raw.strip().lower().replace("-", "_")


def terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def read_status(base_url: str, run_id: str, stage: str) -> dict | None:
    try:
        return request_json(f"{base_url}/status")
    except Exception as exc:
        print(f"Could not read approval server status during {stage}: {exc}")
        return {
            "run_id": run_id,
            "status": "status_unavailable",
            "error": str(exc),
            "stage": stage,
        }


def whatsapp_status(status: dict) -> str:
    return status.get("agent_whatsapp_status") or status.get("status", "")


def whatsapp_recovery_hint(status: dict) -> str:
    state_status = status.get("status", "")
    agent_status = whatsapp_status(status)
    if status.get("approval_reply_observed_at") or agent_status in WHATSAPP_REPLY_OBSERVED_STATUSES:
        return (
            "A matching current-run WhatsApp reply may already have been observed, but the local approval "
            "callback was not completed. Do not resend the approval request. Reopen the target chat, find "
            "the current RUN reply, then POST it to the local approval endpoint."
        )
    if status.get("approval_request_sent_at") or agent_status in WHATSAPP_SENT_STATUSES:
        return (
            "The local flow already has approval_request_sent for this RUN. Do not resend the request. "
            "Only watch the target chat for a matching current-run reply and report it."
        )
    if state_status == "agent_timeout_before_send" or agent_status == "agent_timeout_before_send":
        return (
            "The previous attempt timed out before a confirmed send. First inspect the target chat for an "
            "existing current-run request. If it is visible, POST approval_request_sent with recovered=true "
            "and do not resend; otherwise send the request once."
        )
    return ""


def choose_timeout_event(status: dict, final: bool = False) -> str:
    if final:
        return "agent_timeout_needs_human_check"
    agent_status = whatsapp_status(status)
    if status.get("approval_reply_observed_at") or agent_status in WHATSAPP_REPLY_OBSERVED_STATUSES:
        return "agent_timeout_after_reply_observed"
    if status.get("approval_request_sent_at") or agent_status in WHATSAPP_SENT_STATUSES:
        return "agent_timeout_after_send_waiting_reply"
    return "agent_timeout_before_send"


def mark_whatsapp_timeout(base_url: str, status: dict, attempt: int, final: bool = False) -> dict:
    run_id = status.get("run_id", "")
    if not run_id:
        return status
    event = choose_timeout_event(status, final=final)
    detail = (
        f"Codex WhatsApp attempt {attempt} ended before a terminal local callback. "
        f"Previous flow status: {status.get('status', '')}; "
        f"previous agent WhatsApp status: {status.get('agent_whatsapp_status', '')}."
    )
    try:
        return post_json(
            f"{base_url}/agent/progress",
            {
                "event": event,
                "run_id": run_id,
                "agent_name": "codex",
                "attempt": attempt,
                "detail": detail,
            },
        )["state"]
    except Exception as exc:
        print(f"Could not record WhatsApp timeout progress: {exc}")
        return status


def mark_whatsapp_web_timeout(base_url: str, env: dict[str, str], status: dict, detail: str) -> dict:
    run_id = status.get("run_id", "")
    if not run_id:
        return status
    try:
        return post_json(
            f"{base_url}/agent/progress",
            {
                "event": "agent_timeout_needs_human_check",
                "run_id": run_id,
                "agent_name": whatsapp_web_agent_name(env),
                "chat_name": env.get("COURSE_AGENT_WHATSAPP_CHAT_NAME", ""),
                "detail": detail,
            },
        )["state"]
    except Exception as exc:
        print(f"Could not record WhatsApp Web timeout progress: {exc}")
        return status


def moodle_status(status: dict) -> str:
    return status.get("moodle_agent_status") or status.get("status", "")


def moodle_write_policy(status: dict) -> str:
    result = status.get("moodle_result", {})
    try:
        return normalize_write_policy(result.get("write_policy", "precheck_only"))
    except ValueError:
        return "precheck_only"


def moodle_action(status: dict) -> str:
    return status.get("moodle_result", {}).get("action", "")


def moodle_recovery_hint(status: dict) -> str:
    last_event = status.get("moodle_agent_last_event", "")
    agent_status = moodle_status(status)
    write_policy = moodle_write_policy(status)
    action = moodle_action(status)
    state_label = (
        "file/resource state"
        if action in {"upload_or_replace_file", "create_file_resource"}
        else "title/state"
    )
    if (
        status.get("moodle_rollback_verified_at")
        or last_event in {"moodle_rollback_verified", "moodle_result_post_started"}
        or agent_status in {"moodle_agent_rollback_verified", "moodle_agent_result_post_started"}
    ):
        return (
            "Rollback appears to be verified already. Do not save again. Re-read final Moodle state if needed, "
            "then post PASS/BLOCKED/FAIL to the local Moodle result endpoint."
        )
    if (
        status.get("moodle_rollback_save_completed_at")
        or last_event == "moodle_rollback_save_completed"
        or agent_status == "moodle_agent_rollback_save_completed"
    ):
        return (
            "Rollback save appears completed but final verification/result callback is missing. Do not save again. "
            "Open settings, verify final title/visibility/access restrictions, then post the result."
        )
    if (
        status.get("moodle_execute_save_completed_at")
        or status.get("moodle_execute_verified_at")
        or last_event in MOODLE_EXECUTE_DONE_EVENTS
        or agent_status in {"moodle_agent_execute_save_completed", "moodle_agent_execute_verified"}
    ):
        if write_policy == "execute_only":
            return (
                "The execute-only save appears completed. Do not repeat the execute save and do not roll it back. "
                f"Inspect the live Moodle {state_label}, verify the target state/visibility/guardrails, then post PASS "
                "or the exact observed result."
            )
        return (
            f"The execute save appears completed. Do not repeat the execute save. Inspect the live Moodle {state_label}, "
            "perform rollback-only recovery to the approved original state, verify it, then post the result."
        )
    if last_event == "moodle_execute_save_started" or agent_status == "moodle_agent_execute_save_started":
        if write_policy == "execute_only":
            return (
                "An execute-only save may have been started but completion was not confirmed. Inspect the live Moodle "
                f"{state_label} first. If the target change is live, verify it and post the result without rollback; if no "
                "change is live and preconditions still match, continue safely."
            )
        return (
            f"A save may have been started but completion was not confirmed. Inspect the live Moodle {state_label} first. "
            "If the temporary change is live, rollback only; if no change is live and preconditions still match, continue safely."
        )
    if (
        status.get("moodle_precheck_passed_at")
        or last_event in MOODLE_PRECHECK_DONE_EVENTS
        or agent_status in {"moodle_agent_precheck_passed", "moodle_agent_precheck_blocked"}
    ):
        return (
            "Precheck already ran. Reconfirm the live state before any save. If placeholders or preconditions block the task, "
            "post BLOCKED with saves_count=0; otherwise continue from the approved write step."
        )
    return ""


def choose_moodle_timeout_event(status: dict, final: bool = False) -> str:
    if final:
        return "moodle_timeout_needs_human_check"
    last_event = status.get("moodle_agent_last_event", "")
    agent_status = moodle_status(status)
    if (
        status.get("moodle_rollback_verified_at")
        or last_event in {"moodle_rollback_verified", "moodle_result_post_started"}
        or agent_status in {"moodle_agent_rollback_verified", "moodle_agent_result_post_started"}
    ):
        return "moodle_timeout_after_rollback_before_result"
    if (
        status.get("moodle_rollback_save_completed_at")
        or last_event in MOODLE_ROLLBACK_DONE_EVENTS
        or agent_status in {"moodle_agent_rollback_save_completed"}
    ):
        return "moodle_timeout_after_rollback_before_result"
    if last_event == "moodle_rollback_save_started" or agent_status == "moodle_agent_rollback_save_started":
        return "moodle_timeout_during_rollback"
    if (
        status.get("moodle_execute_save_completed_at")
        or status.get("moodle_execute_verified_at")
        or last_event in MOODLE_EXECUTE_DONE_EVENTS
        or agent_status in {"moodle_agent_execute_save_completed", "moodle_agent_execute_verified"}
    ):
        return "moodle_timeout_after_execute_before_rollback"
    if last_event == "moodle_execute_save_started" or agent_status == "moodle_agent_execute_save_started":
        return "moodle_timeout_after_execute_before_rollback"
    if (
        status.get("moodle_precheck_passed_at")
        or last_event in MOODLE_PRECHECK_DONE_EVENTS
        or agent_status in {"moodle_agent_precheck_passed", "moodle_agent_precheck_blocked"}
    ):
        return "moodle_timeout_after_precheck_before_save"
    return "moodle_timeout_before_precheck"


def mark_moodle_timeout(base_url: str, status: dict, attempt: int, final: bool = False) -> dict:
    run_id = status.get("run_id", "")
    if not run_id:
        return status
    moodle_result = status.get("moodle_result", {})
    event = choose_moodle_timeout_event(status, final=final)
    detail = (
        f"Codex Moodle browser attempt {attempt} ended before a terminal local result. "
        f"Previous flow status: {status.get('status', '')}; "
        f"previous Moodle agent status: {status.get('moodle_agent_status', '')}; "
        f"last Moodle event: {status.get('moodle_agent_last_event', '')}."
    )
    try:
        return post_json(
            f"{base_url}/agent/moodle-progress",
            {
                "event": event,
                "run_id": run_id,
                "operation_id": moodle_result.get("operation_id", ""),
                "agent_name": "codex",
                "attempt": attempt,
                "detail": detail,
            },
        )["state"]
    except Exception as exc:
        print(f"Could not record Moodle timeout progress: {exc}")
        return status


def write_final_status(env: dict[str, str], status: dict) -> None:
    path_text = env.get("CODEX_FINAL_STATUS_PATH", "").strip()
    if not path_text:
        return
    path = Path(path_text).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"Could not write final status to {path}: {exc}")


def watch_moodle_agent_stage(env: dict[str, str], base_url: str, status: dict) -> dict:
    run_id = status["run_id"]
    moodle_result = status.get("moodle_result", {})
    task_path_text = moodle_result.get("moodle_agent_task_path") or moodle_result.get("moodle_agent_browser_task_path")
    if not task_path_text:
        print("Moodle agent browser status is pending, but no task path was found.")
        return status

    task_path = Path(task_path_text)
    if codex_no_run(env, "moodle"):
        print("CODEX_MOODLE_AGENT_NO_RUN=1, Moodle browser task generated without launching Codex.")
        print(f"Moodle browser task: {task_path}")
        return status

    poll_seconds = env_float(env, "CODEX_MOODLE_LISTENER_POLL_SECONDS", 5.0)
    restart_delay = env_float(env, "CODEX_MOODLE_LISTENER_RESTART_DELAY", 3.0)
    max_attempts = env_int(env, "CODEX_MOODLE_LISTENER_MAX_ATTEMPTS", 2)
    attempt = 1

    while True:
        if max_attempts and attempt > max_attempts:
            latest = read_status(base_url, run_id, "moodle-max-attempts")
            if latest is None or latest.get("status") == "status_unavailable":
                return latest or {"run_id": run_id, "status": "status_unavailable"}
            if latest.get("run_id") == run_id and latest.get("status") not in MOODLE_AGENT_TERMINAL_STATUSES:
                latest = mark_moodle_timeout(base_url, latest, attempt - 1, final=True)
            print(f"Reached CODEX_MOODLE_LISTENER_MAX_ATTEMPTS={max_attempts}.")
            return latest

        latest_before_attempt = read_status(base_url, run_id, "moodle-before-attempt")
        if latest_before_attempt is None or latest_before_attempt.get("status") == "status_unavailable":
            return latest_before_attempt or {"run_id": run_id, "status": "status_unavailable"}
        if latest_before_attempt.get("run_id") == run_id and latest_before_attempt.get("status") in MOODLE_AGENT_TERMINAL_STATUSES:
            return latest_before_attempt
        recovery_hint = moodle_recovery_hint(latest_before_attempt)
        command = build_moodle_codex_command(env, task_path, run_id, attempt=attempt, recovery_hint=recovery_hint)
        session_suffix = env.get("CODEX_MOODLE_SESSION_SUFFIX", "moodle-browser").strip() or "moodle-browser"
        session_key = f"agent:main:{env['BATCH_ID'].lower()}-{run_id.lower()}-{session_suffix}"
        print(f"\nLaunching Codex Moodle browser attempt {attempt}.")
        print(f"Session key: {session_key}")
        print(f"Model: {agent_model(env, 'moodle')}")
        if recovery_hint:
            print(f"Moodle recovery hint: {recovery_hint}")
        prompt = build_moodle_codex_prompt(env, task_path, run_id, attempt=attempt, recovery_hint=recovery_hint)
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", newline="\n") as prompt_stream:
            prompt_stream.write(prompt)
            prompt_stream.seek(0)
            process = subprocess.Popen(command, cwd=ROOT, stdin=prompt_stream)

            try:
                while process.poll() is None:
                    latest = read_status(base_url, run_id, "moodle-poll")
                    if latest is None or latest.get("status") == "status_unavailable":
                        terminate_process(process)
                        return latest or {"run_id": run_id, "status": "status_unavailable"}
                    state_status = latest.get("status", "")
                    agent_status = latest.get("moodle_agent_status", "")
                    print(f"[{time.strftime('%H:%M:%S')}] run={run_id} moodle_status={state_status} agent={agent_status}")
                    if latest.get("run_id") == run_id and state_status in MOODLE_AGENT_TERMINAL_STATUSES:
                        print("Moodle browser agent reached a terminal status; stopping this attempt.")
                        terminate_process(process)
                        return latest
                    time.sleep(poll_seconds)
            finally:
                terminate_process(process)

        latest = read_status(base_url, run_id, "moodle-after-attempt")
        if latest is None or latest.get("status") == "status_unavailable":
            return latest or {"run_id": run_id, "status": "status_unavailable"}
        state_status = latest.get("status", "")
        if latest.get("run_id") == run_id and state_status in MOODLE_AGENT_TERMINAL_STATUSES:
            return latest
        if latest.get("run_id") == run_id:
            latest = mark_moodle_timeout(base_url, latest, attempt)
            state_status = latest.get("status", "")
            if state_status in MOODLE_AGENT_TERMINAL_STATUSES:
                return latest

        print(
            f"Codex Moodle browser attempt {attempt} ended while run {run_id} is still "
            f"{state_status!r}; restarting in {restart_delay:g}s."
        )
        attempt += 1
        time.sleep(restart_delay)


def watch_one_run(env: dict[str, str], base_url: str, start_result: dict) -> dict:
    run_id = start_result["run_id"]
    task_path = Path(start_result["agent_task_path"])
    poll_seconds = env_float(env, "CODEX_LISTENER_POLL_SECONDS", 5.0)
    restart_delay = env_float(env, "CODEX_LISTENER_RESTART_DELAY", 3.0)
    max_attempts = env_int(env, "CODEX_LISTENER_MAX_ATTEMPTS", 2)
    attempt = 1

    while True:
        if max_attempts and attempt > max_attempts:
            status = read_status(base_url, run_id, "whatsapp-max-attempts")
            if status is None or status.get("status") == "status_unavailable":
                return status or {"run_id": run_id, "status": "status_unavailable"}
            if status.get("run_id") == run_id and status.get("status") == MOODLE_AGENT_PENDING_STATUS:
                return watch_moodle_agent_stage(env, base_url, status)
            if status.get("run_id") == run_id and status.get("status") not in TERMINAL_STATUSES:
                status = mark_whatsapp_timeout(base_url, status, attempt - 1, final=True)
            print(f"Reached CODEX_LISTENER_MAX_ATTEMPTS={max_attempts}.")
            return status

        latest_before_attempt = read_status(base_url, run_id, "whatsapp-before-attempt")
        if latest_before_attempt is None or latest_before_attempt.get("status") == "status_unavailable":
            return latest_before_attempt or {"run_id": run_id, "status": "status_unavailable"}
        state_status = latest_before_attempt.get("status", "")
        if latest_before_attempt.get("run_id") == run_id and state_status == MOODLE_AGENT_PENDING_STATUS:
            return watch_moodle_agent_stage(env, base_url, latest_before_attempt)
        if latest_before_attempt.get("run_id") == run_id and state_status in TERMINAL_STATUSES:
            return latest_before_attempt
        recovery_hint = whatsapp_recovery_hint(latest_before_attempt)
        command = build_codex_command(env, task_path, run_id, attempt=attempt, recovery_hint=recovery_hint)
        session_key = f"agent:main:{env['BATCH_ID'].lower()}-{run_id.lower()}-whatsapp-approval"
        print(f"\nLaunching Codex listener attempt {attempt}.")
        print(f"Session key: {session_key}")
        print(f"Model: {agent_model(env, 'whatsapp')}")
        if recovery_hint:
            print(f"Recovery hint: {recovery_hint}")
        prompt = build_codex_prompt(env, task_path, run_id, attempt=attempt, recovery_hint=recovery_hint)
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", newline="\n") as prompt_stream:
            prompt_stream.write(prompt)
            prompt_stream.seek(0)
            process = subprocess.Popen(command, cwd=ROOT, stdin=prompt_stream)

            try:
                while process.poll() is None:
                    status = read_status(base_url, run_id, "whatsapp-poll")
                    if status is None or status.get("status") == "status_unavailable":
                        terminate_process(process)
                        return status or {"run_id": run_id, "status": "status_unavailable"}
                    state_status = status.get("status", "")
                    print(f"[{time.strftime('%H:%M:%S')}] run={run_id} status={state_status}")
                    if status.get("run_id") == run_id and state_status == MOODLE_AGENT_PENDING_STATUS:
                        print("WhatsApp approval complete; switching to Moodle browser agent stage.")
                        terminate_process(process)
                        return watch_moodle_agent_stage(env, base_url, status)
                    if status.get("run_id") == run_id and state_status in TERMINAL_STATUSES:
                        print("Current run reached a terminal status; stopping this Codex attempt.")
                        terminate_process(process)
                        return status
                    time.sleep(poll_seconds)
            finally:
                terminate_process(process)

        status = read_status(base_url, run_id, "whatsapp-after-attempt")
        if status is None or status.get("status") == "status_unavailable":
            return status or {"run_id": run_id, "status": "status_unavailable"}
        state_status = status.get("status", "")
        if status.get("run_id") == run_id and state_status == MOODLE_AGENT_PENDING_STATUS:
            return watch_moodle_agent_stage(env, base_url, status)
        if status.get("run_id") == run_id and state_status in TERMINAL_STATUSES:
            return status
        if status.get("run_id") == run_id:
            status = mark_whatsapp_timeout(base_url, status, attempt)
            state_status = status.get("status", "")
            if state_status in TERMINAL_STATUSES:
                return status

        print(
            f"Codex attempt {attempt} ended while run {run_id} is still {state_status!r}; "
            f"restarting in {restart_delay:g}s."
        )
        attempt += 1
        time.sleep(restart_delay)


def watch_whatsapp_web_approval_run(
    env: dict[str, str],
    base_url: str,
    start_result: dict,
    *,
    context=None,
) -> dict:
    run_id = start_result["run_id"]
    keep_open = env.get("WHATSAPP_WEB_KEEP_OPEN", "0") == "1"
    try:
        status = run_whatsapp_web_approval(env, base_url, start_result, keep_open=keep_open, context=context)
    except TimeoutError as exc:
        latest = read_status(base_url, run_id, "whatsapp-web-timeout")
        if latest is None or latest.get("status") == "status_unavailable":
            return latest or {"run_id": run_id, "status": "status_unavailable", "error": str(exc)}
        print(f"WhatsApp Web approval timed out: {exc}")
        return mark_whatsapp_web_timeout(base_url, env, latest, str(exc))
    except Exception as exc:
        latest = read_status(base_url, run_id, "whatsapp-web-error")
        if latest and latest.get("run_id") == run_id and not latest.get("approval_request_sent_at"):
            try:
                latest = post_json(
                    f"{base_url}/agent/progress",
                    {
                        "event": "approval_request_send_failed",
                        "run_id": run_id,
                        "agent_name": whatsapp_web_agent_name(env),
                        "chat_name": env.get("COURSE_AGENT_WHATSAPP_CHAT_NAME", ""),
                        "detail": str(exc),
                        "evidence": "Deterministic WhatsApp Web approval stage failed before a confirmed send.",
                    },
                )["state"]
            except Exception as progress_exc:
                print(f"Could not record WhatsApp Web send failure: {progress_exc}")
        if latest is not None:
            latest["whatsapp_web_error"] = str(exc)
            return latest
        return {"run_id": run_id, "status": "agent_whatsapp_send_failed", "whatsapp_web_error": str(exc)}

    if status.get("run_id") == run_id and status.get("status") == MOODLE_AGENT_PENDING_STATUS:
        print("WhatsApp approval complete; switching to Moodle browser agent stage.")
        return watch_moodle_agent_stage(env, base_url, status)
    return status


def main() -> None:
    env = load_env()
    env["WHATSAPP_MODE"] = "codex_agent_web"
    env.setdefault("LOCAL_BASE_URL", f"http://localhost:{env['PORT']}")
    base_url = env["LOCAL_BASE_URL"].rstrip("/")
    for warning in unsupported_legacy_env_warnings(env):
        print(f"Warning: {warning}")
    forever = course_agent_forever(env)
    next_run_delay = env_float(env, "CODEX_LISTENER_NEXT_RUN_DELAY", 10.0)
    driver = whatsapp_approval_driver(env)
    browser_lifecycle = agent_browser_lifecycle(env)

    server = start_server(env, base_url)
    shared_playwright = None
    shared_whatsapp_context = None
    try:
        if driver in {"web_automation", "whatsapp_web", "browser_script", "playwright"} and browser_lifecycle == "agent":
            sync_playwright, _ = import_playwright()
            shared_playwright = sync_playwright().start()
            shared_whatsapp_context = launch_whatsapp_context(shared_playwright, env)
            print("Using agent-scoped WhatsApp Web browser window for all approval cycles.")
        while True:
            start_result = start_new_run(env, base_url)
            if codex_no_run(env):
                print("CODEX_AGENT_NO_RUN=1, listener generated a task without launching Codex.")
                write_final_status(env, request_json(f"{base_url}/status"))
                return

            if driver in {"web_automation", "whatsapp_web", "browser_script", "playwright"}:
                print("Using deterministic WhatsApp Web approval driver.")
                final_status = watch_whatsapp_web_approval_run(
                    env,
                    base_url,
                    start_result,
                    context=shared_whatsapp_context,
                )
            else:
                print("Using Codex WhatsApp approval driver.")
                final_status = watch_one_run(env, base_url, start_result)
            write_final_status(env, final_status)
            print("\n=== Run finished ===")
            print(json.dumps(final_status, indent=2, ensure_ascii=False))

            if not forever:
                return
            print(f"CODEX_LISTENER_FOREVER=1, starting the next run in {next_run_delay:g}s.")
            time.sleep(next_run_delay)
    finally:
        if shared_whatsapp_context is not None:
            try:
                shared_whatsapp_context.close()
            except Exception as exc:
                print(f"WhatsApp browser was already closed: {exc}")
        if shared_playwright is not None:
            try:
                shared_playwright.stop()
            except Exception as exc:
                print(f"Playwright was already stopped: {exc}")
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    main()
