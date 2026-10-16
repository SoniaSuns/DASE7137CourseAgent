from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from whatsapp_approval import approval_reply_command  # noqa: E402
from moodle_operations import normalize_write_policy  # noqa: E402
from course_agent import (  # noqa: E402
    build_codex_exec_command,
    codex_model,
    codex_no_run,
    unsupported_legacy_env_warnings,
)


ROOT = Path(__file__).resolve().parent
REPORTS = ROOT.parent / "course_files" / "evidence" / "process" / "course_agent_app" / "reports"


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
    env.setdefault("PORT", "8787")
    env.setdefault("WHATSAPP_MODE", "codex_agent_web")
    env.setdefault("CODEX_BIN", "/Applications/ChatGPT.app/Contents/Resources/codex")
    env.setdefault("CODEX_MODEL", "gpt-5.5")
    env.setdefault("CODEX_AGENT_TIMEOUT", "900")
    env.setdefault("CODEX_REPLY_POLL_SECONDS", "8")
    env.setdefault("CODEX_REPLY_MAX_POLLS", "18")
    env.setdefault("CODEX_BROWSER_PROFILE", "codex")
    env.setdefault("COURSE_AGENT_WHATSAPP_CHAT_NAME", "test approval")
    env.setdefault("COURSE_AGENT_NAME", "codex")
    env.setdefault("COURSE_AGENT_FREE_TASK_MODE", "1")
    env.setdefault("COURSE_AGENT_BROWSER_LIFECYCLE", "agent")
    env.setdefault("WHATSAPP_WEB_AGENT_NAME", "whatsapp-web-automation")
    env.setdefault("BATCH_ID", "SMOKE-MOODLE-WA-001")
    return env


def agent_model(env: dict[str, str], stage: str) -> str:
    return codex_model(env, stage)


def request_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_server(base_url: str, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            request_json(f"{base_url}/health")
            return True
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(0.3)
    return False


def start_server(env: dict[str, str], base_url: str) -> subprocess.Popen | None:
    if wait_for_server(base_url, timeout=1.0):
        print(f"Using existing approval server at {base_url}")
        return None

    REPORTS.mkdir(exist_ok=True)
    log_path = REPORTS / f"{env['BATCH_ID']}_approval_server.log"
    log_handle = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "approval_server.py"],
        cwd=ROOT,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if not wait_for_server(base_url):
        process.terminate()
        raise RuntimeError(f"Approval server did not become healthy. See {log_path}")
    print(f"Started approval server at {base_url}")
    print(f"Server log: {log_path}")
    return process


def build_codex_prompt(
    env: dict[str, str],
    task_path: Path,
    run_id: str,
    attempt: int = 1,
    recovery_hint: str = "",
) -> str:
    task = task_path.read_text(encoding="utf-8")
    single_account = env.get("AGENT_SINGLE_ACCOUNT_TEST_MODE") == "1"
    browser_profile = env.get("CODEX_BROWSER_PROFILE", "codex")
    poll_seconds = env.get("CODEX_REPLY_POLL_SECONDS", "8")
    max_polls = env.get("CODEX_REPLY_MAX_POLLS", "18")
    fast_rule = ""
    if env.get("CODEX_FAST_MODE", "0") == "1":
        fast_rule = (
            "Fast mode: minimize browser round trips while preserving evidence. "
            "If WhatsApp is already open on the target chat with the message textbox visible, "
            "skip search/navigation and send from the current pane. If an old draft is present, "
            "clear it before composing the current run. Paste the current-run approval request, "
            "press Enter once immediately, then report approval_request_sent only after the "
            "outgoing message is visible. Do not report approval_request_draft_ready as a separate "
            "stopping point. After reporting approval_request_sent, "
            "inspect the latest messages once before sleeping, then poll at the configured short interval. "
        )
    direction_rule = (
        "This run is in single-account smoke-test mode: a WhatsApp bubble marked "
        "'You:' or outgoing may be accepted only when it is sent after this task "
        "and exactly includes the current RUN id. Ignore all older messages."
        if single_account
        else "Production mode: report only a new incoming approval command, never an outgoing 'You:' message."
    )
    attempt_rule = (
        "This is the first watch attempt. Send the approval request exactly once, then watch for the reply."
        if attempt <= 1
        else (
            f"This is watch attempt {attempt} for the same RUN id. Do not create a new run. "
            "If the exact current-run approval request is already visible in the chat, do not resend it; "
            "continue watching for the matching current-run reply. If the request is not visible, send it once."
        )
    )
    if recovery_hint:
        attempt_rule = f"{attempt_rule} Recovery hint from the local flow: {recovery_hint}"
    prompt = (
        "Run the following CCHU9056 smoke-test task exactly. "
        "The agent is responsible for WhatsApp reply detection. "
        f"{direction_rule} {attempt_rule} "
        f"Use the Codex-managed browser profile `{browser_profile}` for WhatsApp Web. "
        "When calling the browser tool, omit `profile` or set it to `codex`; "
        "do not use `profile=\"user\"` or attach to the operator's default Chrome. "
        "For browser tab targeting, use the raw CDP `targetId` returned by `browser tabs` "
        "for both snapshot and act calls; do not use the tab label `wa` for act calls. "
        "If the right-hand WhatsApp pane is already the configured chat, do not click the "
        "left chat-list row again. If search results remain visible after clicking the chat, "
        "clear the search and retry by pressing Enter on the chat name or clicking the visible "
        "chat title text; send nothing until the right-hand chat header and bottom message textbox "
        "are visible. Once the target chat textbox is visible, clear any old draft text, paste the "
        "exact approval request for the current RUN id, press Enter once immediately, then verify "
        "and report approval_request_sent before watching for replies. Do not report "
        "approval_request_draft_ready as a separate stopping point. If a browser act call returns `targetId must match`, "
        "do not retry the same ref/action; take a fresh tabs+snapshot with the raw targetId, "
        "or stop and report the browser-tool blocker. "
        f"When waiting for replies, poll the chat about every {poll_seconds} seconds "
        f"for up to {max_polls} checks; do not run a single sleep longer than "
        f"{poll_seconds} seconds. If no matching current-run reply appears after "
        "those checks, finish with an honest no-reply report instead of timing out. "
        f"{fast_rule}\n\n"
        + task
    )
    return prompt


def build_codex_command(
    env: dict[str, str],
    task_path: Path,
    run_id: str,
    attempt: int = 1,
    recovery_hint: str = "",
) -> list[str]:
    prompt = build_codex_prompt(env, task_path, run_id, attempt=attempt, recovery_hint=recovery_hint)
    last_message = REPORTS / f"{env['BATCH_ID']}_{run_id}_codex_whatsapp_last_message.txt"
    return build_codex_exec_command(
        env,
        prompt,
        stage="whatsapp",
        cwd=PROJECT_ROOT,
        output_last_message=last_message,
        prompt_via_stdin=True,
    )


def _compact_operation_path(env: dict[str, str], task_path: Path, run_id: str) -> Path:
    sibling = Path(str(task_path).replace("_moodle_agent_browser_task.md", "_operation.json"))
    if sibling.exists():
        return sibling
    suffix = f"_{run_id}" if run_id else ""
    return REPORTS / f"{env['BATCH_ID']}{suffix}_operation.json"


def build_compact_moodle_codex_prompt(
    env: dict[str, str],
    task_path: Path,
    run_id: str,
    attempt: int = 1,
    recovery_hint: str = "",
) -> str:
    operation_path = _compact_operation_path(env, task_path, run_id)
    operation: dict[str, object] = {}
    if operation_path.exists():
        operation = json.loads(operation_path.read_text(encoding="utf-8"))

    browser_profile = env.get("MOODLE_BROWSER_PROFILE", "user").strip() or "user"
    operation_id = str(operation.get("operation_id") or "SANDBOX-API-OPERATION")
    course_url = str(
        operation.get("target_discovery_start_url")
        or operation.get("target_course_url")
        or "https://migrationtest-moodle.hku.hk/course/view.php?id=105330"
    )
    section = str(operation.get("target_discovery_section") or operation.get("section_name") or "General")
    target_title = str(
        operation.get("target_discovery_query")
        or operation.get("target_object")
        or "Announcements"
    )
    expected_filename = str(
        operation.get("expected_uploaded_filename")
        or operation.get("expected_existing_filename")
        or "unsupported-file-upload.pdf"
    )
    local_file = str(
        operation.get("target_file_path")
        or operation.get("source_file_path")
        or "/tmp/unsupported-file-upload.pdf"
    )
    result_endpoint = env.get("LOCAL_BASE_URL", f"http://localhost:{env['PORT']}").rstrip("/") + "/agent/moodle-result"
    progress_endpoint = env.get("LOCAL_BASE_URL", f"http://localhost:{env['PORT']}").rstrip("/") + "/agent/moodle-progress"
    attempt_rule = "first attempt"
    if attempt > 1:
        attempt_rule = f"attempt {attempt}; inspect live state first and do not repeat an already-completed save"
    if recovery_hint:
        attempt_rule = f"{attempt_rule}; recovery hint: {recovery_hint}"

    action = str(operation.get("action") or "upload_or_replace_file")
    if action == "create_file_resource":
        expected_visibility = str(
            operation.get("expected_visibility")
            or ("Hide on course page" if operation.get("visible") is False else "Show on course page")
        )
        return f"""Codex Moodle browser-agent task ({attempt_rule}).

Use browser profile exactly `profile="{browser_profile}"` for every browser tool call. Treat the Moodle browser as agent-scoped: reuse the existing Moodle tab/session across approvals when available, and do not close the browser at the end of a single operation. Do not log in, enter credentials, inspect cookies/local storage/passwords, or create a new Moodle session.

Run id: `{run_id}`
Batch: `{env['BATCH_ID']}`
Operation id: `{operation_id}`
Write policy: execute_only
Operation JSON source of truth: `{operation_path}`
Full generated task file for reference only if needed: `{task_path}`

Goal: create exactly one Moodle File resource in the migrationtest sandbox course by browser automation. This browser fallback is approved only for this RUN after WhatsApp approval and must stop before saving if the target/course/section/file is uncertain.

Target:
- Course URL: {course_url}
- Section: {section}
- File resource title: {target_title}
- Uploaded filename: {expected_filename}
- Local upload path: {local_file}
- Expected visibility: {expected_visibility}

Hard browser rules:
- Start with `browser tabs`; use the raw CDP `targetId` for all snapshot/act/evaluate/navigate calls.
- After navigation or save, refresh `browser tabs` and continue with the current raw `targetId`.
- Prefer compact `browser evaluate` DOM searches over broad visual snapshots. Search `document.querySelectorAll('a,button,[role="button"]')` and include each element's text, href, aria-label, title, and closest section text. Match `{section}`, `{target_title}`, and `{expected_filename}`.
- If the course snapshot only shows the course index, use evaluate to find the real section and add-activity controls.
- If edit mode is needed to expose Add activity/resource, enable it only for this create flow. Do not edit unrelated items.

Allowed Moodle action:
1. Open the approved course page and find section `{section}`.
2. Precheck that local file exists at `{local_file}` and the filename is `{expected_filename}`.
3. Search for an existing exact resource title `{target_title}`. If it already exists with `{expected_filename}` and visibility `{expected_visibility}`, verify it and report PASS with `saves_count=0`; do not create a duplicate.
4. If an exact-title resource exists but the file or visibility is ambiguous/mismatched, report BLOCKED with `saves_count=0`.
5. In the approved section only, add a Moodle `File` resource.
6. Set the resource title exactly to `{target_title}`, upload only `{expected_filename}` from `{local_file}`, and set visibility to `{expected_visibility}`.
7. Do not change dates, grades, access restrictions, groups, completion, notifications, or unrelated files.
8. Click Save once. Expected `saves_count=1`.
9. Verify course page, resource display/settings, `{expected_filename}`, and unchanged guardrail fields.
10. After verification, navigate the same Moodle tab back to the course URL `{course_url}` and verify the course page is visible before posting PASS.

Progress endpoint: `{progress_endpoint}`
Result endpoint: `{result_endpoint}`
When posting callbacks with exec/curl, redirect output to `/dev/null` or print only a tiny acknowledgement. Do not print `/status` or callback response bodies into the conversation.

Post progress events as they become true: `moodle_browser_started`, `moodle_tabs_claimed`, `moodle_target_opened`, `moodle_precheck_started`, `moodle_precheck_passed` or `moodle_precheck_blocked`, `moodle_execute_save_started`, `moodle_execute_save_completed`, `moodle_execute_verified`, `moodle_returned_to_course`, `moodle_result_post_started`.

Final result: POST JSON to `{result_endpoint}` with fields `agent_name`, `run_id`, `operation_id`, `status` (`PASS`, `BLOCKED`, or `FAIL`), `saves_count`, `final_title`, `final_visibility`, `final_access_restrictions`, `final_moodle_state`, `evidence_report`, `log`, and `note`. Include the returned course URL/current URL in `final_moodle_state`.

Use PASS only after the approved one-save File resource creation completed, no delete/rollback ran, `{expected_filename}` is present, visibility/guardrail fields were verified unchanged, and the Moodle tab was returned to the course page. Use BLOCKED before saving when safety or target discovery is uncertain.
"""

    delete_first = bool(operation.get("delete_existing_before_upload"))
    delete_step = (
        f"4. Delete `{operation.get('delete_before_upload_filename') or expected_filename}` from the filemanager draft area first. Do not save yet. Verify it is absent.\n"
        if delete_first
        else "4. Do not delete existing Moodle files unless the operation JSON explicitly sets `delete_existing_before_upload=true`.\n"
    )
    upload_step_number = "5" if delete_first else "5"
    return f"""Codex Moodle browser-agent task ({attempt_rule}).

Use browser profile exactly `profile="{browser_profile}"` for every browser tool call. Treat the Moodle browser as agent-scoped: reuse the existing Moodle tab/session across approvals when available, and do not close the browser at the end of a single operation. Do not log in, enter credentials, inspect cookies/local storage/passwords, or create a new Moodle session.

Run id: `{run_id}`
Batch: `{env['BATCH_ID']}`
Operation id: `{operation_id}`
Write policy: execute_only
Operation JSON source of truth: `{operation_path}`
Full generated task file for reference only if needed: `{task_path}`

Goal: upload or replace the approved file in one sandbox Moodle File resource by browser automation. This browser fallback is approved only for this RUN after WhatsApp approval and must stop before saving if the target/resource/file is uncertain.

Target:
- Course URL: {course_url}
- Section: {section}
- File resource title: {target_title}
- Expected existing/uploaded filename: {expected_filename}
- Local upload path: {local_file}
- Visible same-file reupload is approved for this RUN only.

Hard browser rules:
- Start with `browser tabs`; use the raw CDP `targetId` for all snapshot/act/evaluate/navigate calls.
- After navigation or save, refresh `browser tabs` and continue with the current raw `targetId`.
- Prefer compact `browser evaluate` DOM searches over broad visual snapshots. Search `document.querySelectorAll('a,button,[role="button"]')` and include each element's text, href, aria-label, title, and closest section text. Match `{section}`, `{target_title}`, and `{expected_filename}`.
- If the course snapshot only shows the course index, use evaluate to find the real resource link/edit controls. If no exact match or more than one plausible match exists, report BLOCKED with `saves_count=0`.
- If edit mode is needed to expose settings, enable it only for navigation. Do not edit unrelated items.

Allowed Moodle action:
1. Open/discover the target File resource settings page.
2. Precheck target identity, visibility, access restrictions if present, and current filemanager contents.
3. Verify local file exists at `{local_file}` and the filename is `{expected_filename}`.
{delete_step.rstrip()}
{upload_step_number}. Upload only `{expected_filename}` from `{local_file}` into the File resource content area.
6. Do not change title, visibility, dates, grades, access restrictions, notifications, or unrelated files.
7. Click Save once. Expected `saves_count=1`.
8. Verify display page and settings page show `{expected_filename}` and unchanged visibility.
9. After verification, navigate the same Moodle tab back to the course URL `{course_url}` and verify the course page is visible before posting PASS.

Progress endpoint: `{progress_endpoint}`
Result endpoint: `{result_endpoint}`
When posting callbacks with exec/curl, redirect output to `/dev/null` or print only a tiny acknowledgement. Do not print `/status` or callback response bodies into the conversation.

Post progress events as they become true: `moodle_browser_started`, `moodle_tabs_claimed`, `moodle_target_opened`, `moodle_precheck_started`, `moodle_precheck_passed` or `moodle_precheck_blocked`, `moodle_execute_save_started`, `moodle_execute_save_completed`, `moodle_execute_verified`, `moodle_returned_to_course`, `moodle_result_post_started`.

Final result: POST JSON to `{result_endpoint}` with fields `agent_name`, `run_id`, `operation_id`, `status` (`PASS`, `BLOCKED`, or `FAIL`), `saves_count`, `final_title`, `final_visibility`, `final_access_restrictions`, `final_moodle_state`, `evidence_report`, `log`, and `note`. Include the returned course URL/current URL in `final_moodle_state`.

Use PASS only after the approved one-save upload/replace completed, no rollback ran, `{expected_filename}` is present, visibility/guardrail fields were verified unchanged, and the Moodle tab was returned to the course page. Use BLOCKED before saving when safety or target discovery is uncertain.
"""


def build_moodle_codex_prompt(
    env: dict[str, str],
    task_path: Path,
    run_id: str,
    attempt: int = 1,
    recovery_hint: str = "",
) -> str:
    if env.get("CODEX_MOODLE_COMPACT_PROMPT", "0") == "1":
        return build_compact_moodle_codex_prompt(
            env,
            task_path,
            run_id,
            attempt=attempt,
            recovery_hint=recovery_hint,
        )
    task = task_path.read_text(encoding="utf-8")
    browser_profile = env.get("MOODLE_BROWSER_PROFILE", "user").strip() or "user"
    if browser_profile == "user":
        profile_rule = (
            "Use the existing signed-in user/default Chrome profile for Moodle by setting "
            '`profile="user"` in browser tool calls. Do not use the WhatsApp/Codex-managed profile for Moodle. '
        )
    else:
        profile_rule = (
            f'Use the configured Moodle browser profile by setting `profile="{browser_profile}"` '
            "in browser tool calls. "
        )
    write_policy = normalize_write_policy(
        env.get("MOODLE_AGENT_BROWSER_WRITE_POLICY")
        or env.get("MOODLE_BROWSER_WRITE_POLICY")
        or "precheck_only"
    )
    if write_policy == "execute_only":
        retry_recovery = (
            "Start from the live Moodle state and the task file; if a prior execute save appears "
            "to have succeeded, do not repeat the write and do not roll it back. Verify the target state "
            "and report the exact final state."
        )
        final_state_rule = (
            "For execute-only tasks, leave the verified target Moodle change in place for the visible demo. "
            "Only rollback when the task explicitly says execute-and-revert."
        )
    else:
        retry_recovery = (
            "Start from the live Moodle state and the task file; if a prior write partially succeeded, "
            "prioritize rollback-only recovery and report the exact final state."
        )
        final_state_rule = (
            "Never leave a verified temporary Moodle change unrolled back unless the task explicitly "
            "uses execute-only/no-rollback wording; in that case verify and report the final observed state."
        )
    attempt_rule = (
        "This is the first Moodle browser-agent attempt for this RUN."
        if attempt <= 1
        else (
            f"This is Moodle browser-agent attempt {attempt} for the same RUN id. "
            f"{retry_recovery}"
        )
    )
    if recovery_hint:
        attempt_rule = f"{attempt_rule} Recovery hint from the local flow: {recovery_hint}"
    fast_rule = ""
    if env.get("CODEX_FAST_MODE", "0") == "1":
        fast_rule = (
            "Fast mode: prefer direct evaluate reads over full-page visual exploration when selectors are known. "
            "Combine Moodle settings reads for #id_name, [name=\"visible\"], and "
            "[name=\"availabilityconditionsjson\"] in one evaluate call when possible. "
            "For file upload tasks, still use the browser/file chooser flow for the approved local file; "
            "do not fake filemanager state from text alone. "
            "If the target settings URL is already open, reuse it. After a successful save, verify display and "
            "settings state promptly, return the Moodle tab to the approved course page, and post the result. "
        )
    prompt = (
        "Run the following CCHU9056 Moodle browser-agent task exactly. "
        f"{attempt_rule} "
        f"{profile_rule}"
        "Do not log in, enter credentials, inspect cookies/local storage/passwords, or create a new Moodle session. "
        "Begin with `browser tabs` and use the raw CDP `targetId` returned by that call for snapshots, acts, "
        "navigation, and evaluation; do not use suggested tab aliases. "
        "After navigation or any Moodle save, refresh `browser tabs` and continue with the current raw targetId. "
        "If `targetId must match request targetId` occurs, take one fresh tabs+snapshot and retry once; "
        "if it repeats, stop and report BLOCKED. "
        "Respect the task's write policy. If the task says precheck-only or contains unresolved placeholders, "
        "do not save Moodle; submit a BLOCKED result with saves_count=0. "
        "For agent-discovered file upload tasks, prefer compact DOM/evaluate searches for matching section, "
        "resource title, filename, edit menus, and settings links before taking broad visual snapshots. "
        "Post Moodle progress callbacks to the task's progress endpoint after every browser milestone, "
        "especially before and after each save; when using exec/curl for callbacks, keep the command output tiny "
        "or redirect it to /dev/null instead of printing local state into the conversation. "
        "For successful PASS results, return the same Moodle tab to the approved course page after verification "
        "and before posting the final result. "
        "After PASS, BLOCKED, or FAIL, submit the result to the task's local result endpoint. "
        f"{final_state_rule} {fast_rule}\n\n"
        + task
    )
    return prompt


def build_moodle_codex_command(
    env: dict[str, str],
    task_path: Path,
    run_id: str,
    attempt: int = 1,
    recovery_hint: str = "",
) -> list[str]:
    prompt = build_moodle_codex_prompt(env, task_path, run_id, attempt=attempt, recovery_hint=recovery_hint)
    last_message = REPORTS / f"{env['BATCH_ID']}_{run_id}_codex_moodle_last_message.txt"
    return build_codex_exec_command(
        env,
        prompt,
        stage="moodle",
        cwd=PROJECT_ROOT,
        output_last_message=last_message,
        prompt_via_stdin=True,
    )


def run_codex_agent(env: dict[str, str], task_path: Path, run_id: str, attempt: int = 1) -> int:
    command = build_codex_command(env, task_path, run_id, attempt=attempt)
    prompt = build_codex_prompt(env, task_path, run_id, attempt=attempt)
    session_key = f"agent:main:{env['BATCH_ID'].lower()}-{run_id.lower()}-whatsapp-approval"
    print("Launching Codex agent.")
    print(f"Session key: {session_key}")
    print(f"Model: {agent_model(env, 'whatsapp')}")
    return subprocess.run(
        command,
        cwd=ROOT,
        input=prompt,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).returncode


def run_codex_moodle_agent(env: dict[str, str], task_path: Path, run_id: str, attempt: int = 1) -> int:
    command = build_moodle_codex_command(env, task_path, run_id, attempt=attempt)
    prompt = build_moodle_codex_prompt(env, task_path, run_id, attempt=attempt)
    session_suffix = env.get("CODEX_MOODLE_SESSION_SUFFIX", "moodle-browser").strip() or "moodle-browser"
    session_key = f"agent:main:{env['BATCH_ID'].lower()}-{run_id.lower()}-{session_suffix}"
    print("Launching Codex Moodle browser agent.")
    print(f"Session key: {session_key}")
    print(f"Model: {agent_model(env, 'moodle')}")
    return subprocess.run(
        command,
        cwd=ROOT,
        input=prompt,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).returncode


def main() -> None:
    env = load_env()
    env["WHATSAPP_MODE"] = "codex_agent_web"
    env.setdefault("LOCAL_BASE_URL", f"http://localhost:{env['PORT']}")
    for warning in unsupported_legacy_env_warnings(env):
        print(f"Warning: {warning}")
    base_url = env["LOCAL_BASE_URL"].rstrip("/")

    server = start_server(env, base_url)
    try:
        start_result = request_json(f"{base_url}/start")
        task_path = Path(start_result["agent_task_path"])
        approval_code = start_result.get("approval_code", "")
        print(f"Agent task: {task_path}")
        print(f"Run id: {start_result['run_id']}")
        if approval_code:
            print(f"Approval code: {approval_code}")
        print(
            "Reply command: "
            + approval_reply_command(
                "APPROVE",
                env["BATCH_ID"],
                start_result["run_id"],
                approval_code=approval_code,
            )
        )
        for attachment in start_result.get("approval_attachments", []) or []:
            print(f"Approval attachment: {attachment.get('path', '')}")
        if codex_no_run(env):
            print("CODEX_AGENT_NO_RUN=1, task generated without launching Codex.")
            return
        exit_code = run_codex_agent(env, task_path, start_result["run_id"])
        status = request_json(f"{base_url}/status")
        if status.get("status") == "moodle_agent_browser_action_pending":
            moodle_result = status.get("moodle_result", {})
            moodle_task_path = moodle_result.get("moodle_agent_task_path") or moodle_result.get(
                "moodle_agent_browser_task_path"
            )
            if codex_no_run(env, "moodle"):
                print("CODEX_MOODLE_AGENT_NO_RUN=1, Moodle browser task generated without launching Codex.")
                print(f"Moodle browser task: {moodle_task_path}")
            elif moodle_task_path:
                exit_code = run_codex_moodle_agent(env, Path(moodle_task_path), start_result["run_id"])
                status = request_json(f"{base_url}/status")
        print(json.dumps(status, indent=2, ensure_ascii=False))
        raise SystemExit(exit_code)
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    main()
