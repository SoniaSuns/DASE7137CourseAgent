from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moodle_operations.operation_loader import load_operation as load_operation_file  # noqa: E402


ROOT = Path(__file__).resolve().parent
REPORTS = ROOT.parent / "course_files" / "evidence" / "process" / "course_agent_app" / "reports"
DEFAULT_CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SANDBOX_HOST = "migrationtest-moodle.hku.hk"
SANDBOX_COURSE_ID = "105330"
LEGACY_REAL_COURSE_ID = "118635"
LEGACY_REAL_TITLE_DEMO_MODULE_ID = "3687497"


try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass


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
    env.setdefault("MOODLE_BROWSER_PROFILE_DIR", str(ROOT / ".moodle_browser_profile"))
    env.setdefault("MOODLE_BROWSER_CHROME_PATH", DEFAULT_CHROME_PATH)
    env.setdefault("MOODLE_BROWSER_WAIT_SECONDS", "900")
    env.setdefault("COURSE_AGENT_BROWSER_LIFECYCLE", "agent")
    return env


def env_flag(env: dict[str, str], key: str, default: bool = False) -> bool:
    raw = env.get(key, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def keep_moodle_browser_open(env: dict[str, str], cli_keep_open: bool) -> bool:
    lifecycle = env.get("COURSE_AGENT_BROWSER_LIFECYCLE", "agent").strip().lower().replace("-", "_")
    default_keep_open = lifecycle not in {"approval", "per_approval", "run", "per_run"}
    return cli_keep_open or env_flag(env, "MOODLE_BROWSER_KEEP_OPEN", default_keep_open)


def import_playwright():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        install_python = r".venv\Scripts\python.exe" if os.name == "nt" else ".venv/bin/python"
        raise RuntimeError(
            f"Python Playwright is not installed. Run: {install_python} -m pip install -r requirements.txt"
        ) from exc
    return sync_playwright, PlaywrightTimeoutError


def safe_name(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-")
    return cleaned[:80] or "moodle-course-file-upload"


def resolve_local_path(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def load_operation(path_text: str) -> tuple[Path, dict]:
    return load_operation_file(path_text)


def visible_text(page, limit: int = 12000) -> str:
    return page.evaluate(
        """
        (limit) => (document.body && document.body.innerText || '')
          .replace(/\\s+/g, ' ')
          .trim()
          .slice(0, limit)
        """,
        limit,
    )


def has_text(page, pattern: str) -> bool:
    return re.search(pattern, visible_text(page), re.IGNORECASE) is not None


def write_result(report_dir: Path, name: str, data: dict) -> Path:
    path = report_dir / name
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def save_screenshot(page, report_dir: Path, name: str) -> Path:
    path = report_dir / name
    page.screenshot(path=str(path), full_page=True)
    return path


def append_ledger(event: dict) -> None:
    with (ROOT / "approval_ledger.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": now(), **event}, ensure_ascii=False) + "\n")


def assert_not_legacy_real_course(operation: dict) -> None:
    checked = "\n".join(
        str(operation.get(key, ""))
        for key in (
            "target_course_url",
            "target_settings_url",
            "target_module_id",
            "target_discovery_start_url",
        )
    )
    if f"id={LEGACY_REAL_COURSE_ID}" in checked or f"update={LEGACY_REAL_TITLE_DEMO_MODULE_ID}" in checked:
        raise ValueError(
            "Refusing legacy real DASE7137/IMSE7137 Moodle target. "
            "All Moodle modification tests must use the migrationtest sandbox course 105330."
        )


def assert_create_file_resource(operation: dict) -> None:
    assert_not_legacy_real_course(operation)
    if operation.get("action") != "create_file_resource":
        raise ValueError(f"Unsupported Moodle course upload action: {operation.get('action')!r}")
    if str(operation.get("target_course_id", "")) != SANDBOX_COURSE_ID:
        raise ValueError(f"Refusing non-sandbox course id: {operation.get('target_course_id')!r}")
    course_url = str(operation.get("target_course_url", ""))
    parsed = urlparse(course_url)
    if parsed.hostname != SANDBOX_HOST or f"id={SANDBOX_COURSE_ID}" not in course_url:
        raise ValueError(f"Refusing non-sandbox Moodle course URL: {course_url!r}")
    if not operation.get("resource_name"):
        raise ValueError("Operation is missing resource_name.")
    if not operation.get("target_file_path") and not operation.get("upload_file_path"):
        raise ValueError("Operation is missing target_file_path/upload_file_path.")


def assert_phrase(operation: dict, phrase: str) -> None:
    expected = (operation.get("browser_execution") or {}).get("execute_approval_phrase", "")
    if not expected or phrase != expected:
        raise ValueError(
            "Approval phrase mismatch. Refusing Moodle write. "
            "Use the exact run-specific phrase from the approved operation JSON."
        )


def course_add_resource_url(operation: dict) -> str:
    section = str(operation.get("sectionnum") or "0")
    return (
        f"https://{SANDBOX_HOST}/course/modedit.php?"
        + urlencode(
            {
                "add": "resource",
                "type": "",
                "course": SANDBOX_COURSE_ID,
                "section": section,
                "return": "0",
                "sr": section,
            }
        )
    )


def looks_like_login(page) -> bool:
    url = page.url.lower()
    text = visible_text(page, 5000).lower()
    login_words = ["log in", "login", "sign in", "username", "password", "hku portal user login"]
    return "login" in url or any(word in text for word in login_words)


def maybe_click_hku_portal_login(page) -> bool:
    candidates = [
        page.get_by_text(re.compile(r"HKU\s+Portal\s+user\s+login", re.I)).first,
        page.get_by_role("button", name=re.compile(r"HKU\s+Portal", re.I)).first,
        page.get_by_role("link", name=re.compile(r"HKU\s+Portal", re.I)).first,
    ]
    for locator in candidates:
        try:
            if locator.count() and locator.is_visible(timeout=1000):
                locator.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


def wait_for_form_or_course(page, wait_seconds: float, target_url: str, *, auto_click_hku: bool = True) -> None:
    deadline = time.time() + wait_seconds
    login_notice_printed = False
    hku_clicked = False
    while time.time() < deadline:
        try:
            if page.locator("#id_name").count() == 1:
                return
            url = page.url.lower()
            text = visible_text(page, 8000).lower()
        except Exception:
            time.sleep(1)
            continue
        course_signal = (
            SANDBOX_HOST in url
            and (
                f"id={SANDBOX_COURSE_ID}" in url
                or "modedit.php" in url
                or "participants" in text
                or "edit mode" in text
                or "add an activity" in text
            )
        )
        if course_signal and not looks_like_login(page):
            return
        if looks_like_login(page):
            if auto_click_hku and not hku_clicked:
                hku_clicked = maybe_click_hku_portal_login(page)
            if not login_notice_printed:
                print("\nMoodle is asking for login.")
                if hku_clicked:
                    print("The script clicked the HKU Portal login entry.")
                elif not auto_click_hku:
                    print("HKU Portal auto-click is disabled; use the Guest User form in the Chrome window.")
                print("Please complete the Moodle login manually in the Chrome window.")
                print("Do not paste your password into this terminal.")
                print(f"After login, the script will continue toward: {target_url}\n")
                login_notice_printed = True
        time.sleep(2)
    raise RuntimeError("Timed out waiting for Moodle login/course/form page.")


def find_exact_resource_link(page, title: str):
    links = page.get_by_role("link", name=title, exact=True)
    try:
        if links.count():
            return links.first
    except Exception:
        return None
    return None


def select_hidden_visibility(page, expected_visibility: str) -> None:
    visible_select = page.locator("#id_visible")
    if visible_select.count() != 1:
        return
    fallback_value = "1" if expected_visibility.strip().lower().startswith("show") else "0"
    try:
        visible_select.select_option(label=expected_visibility)
        return
    except Exception:
        pass
    try:
        visible_select.select_option(value=fallback_value)
        return
    except Exception as exc:
        try:
            visible_select.evaluate(
                """
                (el, value) => {
                  el.value = value;
                  el.dispatchEvent(new Event('input', {bubbles: true}));
                  el.dispatchEvent(new Event('change', {bubbles: true}));
                }
                """,
                fallback_value,
            )
            return
        except Exception as js_exc:
            raise RuntimeError(
                f"Could not set resource visibility to {expected_visibility}: {exc}; JS fallback: {js_exc}"
            ) from js_exc


def set_intro(page, intro_html: str) -> None:
    if not intro_html:
        return
    editable = page.locator("#id_introeditoreditable")
    if editable.count():
        editable.evaluate(
            """
            (el, html) => {
              el.innerHTML = html;
              el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: ''}));
              el.dispatchEvent(new Event('change', {bubbles: true}));
            }
            """,
            intro_html,
        )
    textarea = page.locator("textarea[name='introeditor[text]']")
    if textarea.count():
        try:
            textarea.fill(intro_html)
        except Exception:
            textarea.evaluate(
                """
                (el, html) => {
                  el.value = html;
                  el.dispatchEvent(new Event('input', {bubbles: true}));
                  el.dispatchEvent(new Event('change', {bubbles: true}));
                }
                """,
                intro_html,
            )


def click_first_visible(page, selectors: list[str], timeout_ms: int = 5000) -> str | None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() and locator.is_visible(timeout=1000):
                locator.click(timeout=timeout_ms)
                return selector
        except Exception:
            continue
    return None


def file_input_scopes(page):
    yield ("page", page)
    for index, frame in enumerate(page.frames):
        yield (f"frame:{index}:{frame.url}", frame)


def active_filepicker_file_inputs(page):
    selectors = [
        ".moodle-dialogue-focused input[type='file'][name='repo_upload_file']",
        ".moodle-dialogue-focused .filepicker input[type='file'][name='repo_upload_file']",
        ".moodle-dialogue-focused input[type='file']",
        ".moodle-dialogue input[type='file'][name='repo_upload_file']",
        ".filepicker input[type='file'][name='repo_upload_file']",
    ]
    for selector in selectors:
        inputs = page.locator(selector)
        try:
            count = inputs.count()
        except Exception:
            count = 0
        for index in range(count):
            yield selector, inputs.nth(index)


def set_first_file_input(page, local_file: Path) -> bool:
    preferred_selectors = [
        ".moodle-dialogue-focused input[type='file'][name='repo_upload_file']",
        ".moodle-dialogue input[type='file'][name='repo_upload_file']",
        ".filepicker input[type='file'][name='repo_upload_file']",
        "input[type='file'][name='repo_upload_file']",
        ".filemanager input[type='file']",
        "input[type='file']",
    ]
    for _, scope in file_input_scopes(page):
        for selector in preferred_selectors:
            inputs = scope.locator(selector)
            try:
                count = inputs.count()
            except Exception:
                count = 0
            for index in range(count - 1, -1, -1):
                candidate = inputs.nth(index)
                try:
                    candidate.set_input_files(str(local_file), timeout=10000)
                    selected_names = candidate.evaluate(
                        "(el) => Array.from(el.files || []).map((file) => file.name)"
                    )
                    if local_file.name in selected_names:
                        candidate.evaluate(
                            """
                            (el) => {
                              el.dispatchEvent(new Event('input', {bubbles: true}));
                              el.dispatchEvent(new Event('change', {bubbles: true}));
                            }
                            """
                        )
                        return True
                except Exception:
                    continue
    return False


def set_active_filepicker_file_input(page, local_file: Path) -> dict:
    script = "(el) => Array.from(el.files || []).map((file) => file.name)"
    for selector, candidate in active_filepicker_file_inputs(page):
        try:
            candidate.set_input_files(str(local_file), timeout=10000)
            selected_names = candidate.evaluate(script)
            if local_file.name not in selected_names:
                continue
            candidate.evaluate(
                """
                (el) => {
                  el.dispatchEvent(new Event('input', {bubbles: true}));
                  el.dispatchEvent(new Event('change', {bubbles: true}));
                }
                """
            )
            return {"ok": True, "selector": selector, "selected_names": selected_names}
        except Exception as exc:
            last_error = str(exc)
            continue
    try:
        chooser_input = page.locator(
            ".moodle-dialogue-focused input[type='file'][name='repo_upload_file'], "
            ".moodle-dialogue-focused input[type='file']"
        ).first
        if chooser_input.count():
            with page.expect_file_chooser(timeout=5000) as chooser_info:
                chooser_input.click(force=True, timeout=5000)
            chooser_info.value.set_files(str(local_file), timeout=10000)
            return {"ok": True, "selector": "filechooser", "selected_names": [local_file.name]}
    except Exception as exc:
        last_error = str(exc)
    return {"ok": False, "selector": "", "selected_names": [], "error": locals().get("last_error", "")}


def active_filepicker_attached_file_names(page) -> list[str]:
    names: list[str] = []
    script = "(el) => Array.from(el.files || []).map((file) => file.name)"
    for _, candidate in active_filepicker_file_inputs(page):
        try:
            names.extend(candidate.evaluate(script))
        except Exception:
            continue
    return names


def wait_for_file_input(page, timeout_ms: int = 15000) -> bool:
    deadline = time.time() + (timeout_ms / 1000)
    while time.time() < deadline:
        for _, scope in file_input_scopes(page):
            try:
                if scope.locator("input[type='file'], input[name='repo_upload_file']").count():
                    return True
            except Exception:
                continue
        page.wait_for_timeout(250)
    return False


def file_input_debug(page) -> list[dict]:
    records = []
    script = """
    () => Array.from(document.querySelectorAll('input')).map((el, index) => ({
      index,
      type: el.getAttribute('type') || '',
      name: el.getAttribute('name') || '',
      id: el.id || '',
      className: el.className || '',
      ariaLabel: el.getAttribute('aria-label') || '',
      title: el.getAttribute('title') || '',
      visible: Boolean(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
    }))
    """
    for scope_name, scope in file_input_scopes(page):
        try:
            records.append({"scope": scope_name, "inputs": scope.evaluate(script)})
        except Exception as exc:
            records.append({"scope": scope_name, "error": str(exc)})
    return records


def attached_file_names(page) -> list[str]:
    names: list[str] = []
    script = "(el) => Array.from(el.files || []).map((file) => file.name)"
    for _, scope in file_input_scopes(page):
        try:
            count = scope.locator("input[type='file']").count()
        except Exception:
            count = 0
        for index in range(count):
            try:
                names.extend(scope.locator("input[type='file']").nth(index).evaluate(script))
            except Exception:
                continue
    return names


def dismiss_no_file_attached_error(page) -> bool:
    try:
        if not page.get_by_text("No files attached", exact=False).count():
            return False
        ok_button = page.locator(".moodle-dialogue-focused button:has-text('OK'), button:has-text('OK')").first
        if ok_button.count():
            ok_button.click(timeout=5000)
            page.wait_for_timeout(500)
        return True
    except Exception:
        return False


def upload_file_via_filepicker(page, report_dir: Path, local_file: Path, expected_filename: str) -> dict:
    add_selector = click_first_visible(
        page,
        [
            ".filemanager .fp-btn-add a",
            ".filemanager .fp-btn-add button",
            ".filemanager [title='Add...']",
            ".filemanager [aria-label='Add...']",
            "a[title='Add...']",
            "button[title='Add...']",
            "a:has-text('Add...')",
            "button:has-text('Add...')",
            "a:has-text('Add file')",
            "button:has-text('Add file')",
        ],
    )
    if not add_selector:
        if not set_first_file_input(page, local_file):
            raise RuntimeError("Could not find Moodle filemanager Add button or file input.")
    else:
        try:
            upload_repo = page.get_by_text(re.compile(r"^Upload a file$", re.I)).first
            if upload_repo.count() and upload_repo.is_visible(timeout=5000):
                upload_repo.click(timeout=5000)
        except Exception:
            pass
        wait_for_file_input(page, timeout_ms=15000)
        clicked_upload = None
        active_input_result = {}
        for attempt in range(1, 4):
            active_input_result = set_active_filepicker_file_input(page, local_file)
            if not active_input_result.get("ok"):
                write_result(
                    report_dir,
                    "03_file_input_debug.json",
                    {
                        "attempt": attempt,
                        "active_input_result": active_input_result,
                        "inputs": file_input_debug(page),
                    },
                )
                raise RuntimeError("Moodle file picker opened, but no usable upload-dialog file input was found.")
            try:
                title = page.locator("input[name='title']").first
                if title.count() and title.is_visible(timeout=1000):
                    title.fill(expected_filename)
            except Exception:
                pass
            if local_file.name not in active_filepicker_attached_file_names(page):
                page.wait_for_timeout(500)
                if local_file.name not in active_filepicker_attached_file_names(page):
                    save_screenshot(page, report_dir, f"03_filepicker_file_not_attached_retry_{attempt}.png")
                    continue
            clicked_upload = click_first_visible(
                page,
                [
                    "button.fp-upload-btn",
                    "input.fp-upload-btn",
                    "button:has-text('Upload this file')",
                    "input[value='Upload this file']",
                    "button:has-text('Upload')",
                    "input[value='Upload']",
                ],
                timeout_ms=10000,
            )
            if not clicked_upload:
                raise RuntimeError("Could not find Moodle file picker upload confirmation button.")
            page.wait_for_timeout(1000)
            if dismiss_no_file_attached_error(page):
                write_result(
                    report_dir,
                    f"03_filepicker_no_file_retry_{attempt}.json",
                    {
                        "active_input_result": active_input_result,
                        "active_attached_names": active_filepicker_attached_file_names(page),
                        "all_attached_names": attached_file_names(page),
                        "inputs": file_input_debug(page),
                    },
                )
                save_screenshot(page, report_dir, f"03_filepicker_no_file_retry_{attempt}.png")
                continue
            break
        if not clicked_upload:
            raise RuntimeError("Could not confirm Moodle file picker upload after retries.")

    size_bytes = local_file.stat().st_size
    upload_wait_ms = max(60000, min(600000, int(size_bytes / 400_000) * 1000))
    try:
        page.get_by_text(expected_filename, exact=False).first.wait_for(timeout=upload_wait_ms)
    except Exception:
        save_screenshot(page, report_dir, "03_after_file_upload_unverified.png")
        text = visible_text(page, 20000)
        if expected_filename not in text:
            raise RuntimeError(f"Uploaded filename {expected_filename!r} did not appear in Moodle filemanager.")
    return {
        "expected_filename": expected_filename,
        "local_file": str(local_file),
        "file_size": size_bytes,
        "upload_wait_ms": upload_wait_ms,
        "add_selector": add_selector,
    }


@contextmanager
def maybe_navigation(page, timeout_error_type, timeout: int = 45000):
    try:
        with page.expect_navigation(wait_until="domcontentloaded", timeout=timeout):
            yield
    except timeout_error_type:
        page.wait_for_load_state("domcontentloaded", timeout=timeout)
        page.wait_for_timeout(2000)


def click_save_and_wait(page, timeout_error_type) -> None:
    save_button = page.locator("#id_submitbutton")
    if save_button.count() != 1:
        save_button = page.locator("#id_submitbutton2")
    if save_button.count() != 1:
        raise RuntimeError("Could not find a unique Moodle Save button.")
    with maybe_navigation(page, timeout_error_type):
        save_button.click(timeout=10000)
    page.wait_for_load_state("domcontentloaded", timeout=45000)


def page_summary(page) -> dict:
    h1 = ""
    try:
        if page.locator("h1").count():
            h1 = page.locator("h1").first.inner_text(timeout=3000)
    except Exception:
        h1 = ""
    return {
        "captured_at": now(),
        "url": page.url,
        "title": page.title(),
        "h1": h1,
        "text_sample": visible_text(page, 2000),
    }


def execute_course_file_resource_upload_operation(
    operation: dict,
    env: dict[str, str],
    page,
    timeout_error_type,
    *,
    report_dir: Path | None = None,
) -> dict:
    assert_create_file_resource(operation)
    env = {**env, "MOODLE_AUTO_CLICK_HKU_LOGIN": env.get("MOODLE_AUTO_CLICK_HKU_LOGIN", "0")}
    auto_click_hku = env.get("MOODLE_AUTO_CLICK_HKU_LOGIN", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }

    upload_path = resolve_local_path(operation.get("target_file_path") or operation.get("upload_file_path"))
    if not upload_path.exists():
        raise FileNotFoundError(f"Upload file does not exist: {upload_path}")
    expected_filename = operation.get("expected_uploaded_filename") or upload_path.name
    if upload_path.name != expected_filename:
        raise ValueError(f"Upload file basename {upload_path.name!r} does not match {expected_filename!r}.")

    REPORTS.mkdir(exist_ok=True)
    operation_id = str(operation.get("operation_id", "SANDBOX-BROWSER-COURSE-FILE-RESOURCE-UPLOAD"))
    report_dir = report_dir or (
        REPORTS / f"moodle_course_file_upload_{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}_{safe_name(operation_id)}"
    )
    report_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "ok": False,
        "status": "STARTED",
        "mode": "execute_approved_change",
        "report_dir": str(report_dir),
        "operation_id": operation_id,
        "approval_run_id": operation.get("approval_run_id", ""),
        "course_url": operation.get("target_course_url", ""),
        "resource_name": operation.get("resource_name", ""),
        "target_object": operation.get("resource_name", ""),
        "expected_uploaded_filename": expected_filename,
        "saves_count": 0,
        "write_action_taken": False,
    }

    try:
        course_url = operation["target_course_url"]
        add_url = course_add_resource_url(operation)
        page.goto(course_url, wait_until="domcontentloaded", timeout=60000)
        wait_for_form_or_course(
            page,
            float(env.get("MOODLE_BROWSER_WAIT_SECONDS", "900")),
            course_url,
            auto_click_hku=auto_click_hku,
        )
        save_screenshot(page, report_dir, "01_course_or_login_after_wait.png")
        write_result(report_dir, "01_course_or_login_after_wait.json", page_summary(page))

        if looks_like_login(page):
            raise RuntimeError("Moodle is still on a login page after waiting.")

        existing = find_exact_resource_link(page, operation["resource_name"])
        if existing is not None:
            result.update(
                {
                    "ok": True,
                    "status": "ALREADY_PRESENT",
                    "write_action_taken": False,
                    "note": "Exact resource title already exists on the course page; no duplicate was created.",
                    "course_verify_url": course_url,
                }
            )
            write_result(report_dir, "result.json", result)
            append_ledger({"event": "moodle_course_file_upload_already_present", "result": result})
            return result

        page.goto(add_url, wait_until="domcontentloaded", timeout=60000)
        wait_for_form_or_course(
            page,
            float(env.get("MOODLE_BROWSER_WAIT_SECONDS", "900")),
            add_url,
            auto_click_hku=auto_click_hku,
        )
        if page.locator("#id_name").count() != 1:
            raise RuntimeError("Moodle add-resource form did not expose #id_name.")
        save_screenshot(page, report_dir, "02_add_resource_form_before_fill.png")
        write_result(report_dir, "02_add_resource_form_before_fill.json", page_summary(page))

        page.locator("#id_name").fill(operation["resource_name"])
        set_intro(page, operation.get("intro", ""))
        select_hidden_visibility(page, operation.get("expected_visibility", "Show on course page"))
        upload_details = upload_file_via_filepicker(page, report_dir, upload_path, expected_filename)
        save_screenshot(page, report_dir, "03_after_fill_and_file_upload_before_save.png")
        write_result(
            report_dir,
            "03_after_fill_and_file_upload_before_save.json",
            {"page": page_summary(page), "upload": upload_details},
        )

        click_save_and_wait(page, timeout_error_type)
        result["saves_count"] = 1
        result["write_action_taken"] = True
        save_screenshot(page, report_dir, "04_after_save_display.png")
        display_summary = page_summary(page)
        write_result(report_dir, "04_after_save_display.json", display_summary)
        display_text = visible_text(page, 30000)
        file_contents = upload_path.read_text(encoding="utf-8", errors="replace")
        file_signal = expected_filename in display_text or file_contents[:60].strip() in display_text
        title_signal = operation["resource_name"] in display_text or operation["resource_name"] in display_summary.get("h1", "")

        page.goto(course_url, wait_until="domcontentloaded", timeout=60000)
        wait_for_form_or_course(page, 30, course_url, auto_click_hku=auto_click_hku)
        save_screenshot(page, report_dir, "05_verify_course_after_save.png")
        course_summary = page_summary(page)
        write_result(report_dir, "05_verify_course_after_save.json", course_summary)
        course_text = visible_text(page, 30000)
        course_title_signal = operation["resource_name"] in course_text

        if not title_signal and not course_title_signal:
            raise RuntimeError("Saved resource title was not visible on display page or course page.")
        if not file_signal and expected_filename not in course_text:
            raise RuntimeError("Uploaded file signal was not visible after save.")

        result.update(
            {
                "ok": True,
                "status": "PASS",
                "display_url": display_summary.get("url", ""),
                "title_visible_after_save": bool(title_signal or course_title_signal),
                "file_visible_after_save": bool(file_signal or expected_filename in course_text),
                "course_verify_url": course_summary.get("url", ""),
                "screenshots": [
                    str(report_dir / "04_after_save_display.png"),
                    str(report_dir / "05_verify_course_after_save.png"),
                ],
                "final_moodle_state": {
                    "course_url": course_url,
                    "resource_name": operation.get("resource_name", ""),
                    "expected_uploaded_filename": expected_filename,
                    "display_url": display_summary.get("url", ""),
                },
            }
        )
        write_result(report_dir, "result.json", result)
        append_ledger({"event": "moodle_course_file_upload_executed", "result": result})
        return result
    except Exception as exc:
        result.update({"ok": False, "status": "BLOCKED", "blocker": str(exc), "current_url": page.url})
        try:
            save_screenshot(page, report_dir, "error_state.png")
            result["error_screenshot"] = str(report_dir / "error_state.png")
        except Exception:
            pass
        write_result(report_dir, "result.json", result)
        append_ledger({"event": "moodle_course_file_upload_blocked", "result": result})
        return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create one Moodle course File resource through Playwright browser automation."
    )
    parser.add_argument("--operation", required=True, help="Path to the approved operation JSON.")
    parser.add_argument("--approval-phrase", default="", help="Exact run-specific approval phrase required for save.")
    parser.add_argument("--execute-approved-change", action="store_true", help="Perform the approved upload/save.")
    parser.add_argument("--precheck-only", action="store_true", help="Open and inspect the add-resource form without saving.")
    parser.add_argument("--keep-open", action="store_true", help="Leave Chrome open after the action.")
    args = parser.parse_args()

    if args.execute_approved_change and args.precheck_only:
        raise SystemExit("Choose either --execute-approved-change or --precheck-only.")
    if not args.execute_approved_change and not args.precheck_only:
        args.precheck_only = True

    env = load_env()
    keep_open = keep_moodle_browser_open(env, args.keep_open)
    auto_click_hku = env.get("MOODLE_AUTO_CLICK_HKU_LOGIN", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    operation_path, operation = load_operation(args.operation)
    assert_create_file_resource(operation)
    if args.execute_approved_change:
        assert_phrase(operation, args.approval_phrase)

    upload_path = resolve_local_path(operation.get("target_file_path") or operation.get("upload_file_path"))
    if not upload_path.exists():
        raise FileNotFoundError(f"Upload file does not exist: {upload_path}")
    expected_filename = operation.get("expected_uploaded_filename") or upload_path.name
    if upload_path.name != expected_filename:
        raise ValueError(f"Upload file basename {upload_path.name!r} does not match {expected_filename!r}.")

    REPORTS.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    report_dir = REPORTS / f"moodle_course_file_upload_{stamp}_{safe_name(operation.get('operation_id', 'operation'))}"
    report_dir.mkdir(parents=True, exist_ok=True)

    sync_playwright, PlaywrightTimeoutError = import_playwright()
    result = {
        "ok": False,
        "status": "STARTED",
        "mode": "execute_approved_change" if args.execute_approved_change else "precheck_only",
        "operation_path": str(operation_path),
        "report_dir": str(report_dir),
        "operation_id": operation.get("operation_id", ""),
        "approval_run_id": operation.get("approval_run_id", ""),
        "course_url": operation.get("target_course_url", ""),
        "resource_name": operation.get("resource_name", ""),
        "expected_uploaded_filename": expected_filename,
        "saves_count": 0,
        "write_action_taken": False,
    }

    with sync_playwright() as playwright:
        profile_dir = Path(env["MOODLE_BROWSER_PROFILE_DIR"]).expanduser()
        if not profile_dir.is_absolute():
            profile_dir = ROOT / profile_dir
        profile_dir.mkdir(parents=True, exist_ok=True)
        chrome_path = env.get("MOODLE_BROWSER_CHROME_PATH", DEFAULT_CHROME_PATH)
        launch_kwargs = {
            "user_data_dir": str(profile_dir),
            "headless": False,
            "viewport": {"width": 1400, "height": 900},
        }
        if chrome_path and Path(chrome_path).exists():
            launch_kwargs["executable_path"] = chrome_path

        context = playwright.chromium.launch_persistent_context(**launch_kwargs)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            course_url = operation["target_course_url"]
            add_url = course_add_resource_url(operation)
            print(f"Opening Moodle course: {course_url}")
            page.goto(course_url, wait_until="domcontentloaded", timeout=60000)
            wait_for_form_or_course(
                page,
                float(env.get("MOODLE_BROWSER_WAIT_SECONDS", "900")),
                course_url,
                auto_click_hku=auto_click_hku,
            )
            save_screenshot(page, report_dir, "01_course_or_login_after_wait.png")
            write_result(report_dir, "01_course_or_login_after_wait.json", page_summary(page))

            if looks_like_login(page):
                raise RuntimeError("Moodle is still on a login page after waiting.")

            existing = find_exact_resource_link(page, operation["resource_name"])
            if existing is not None:
                result.update(
                    {
                        "ok": True,
                        "status": "ALREADY_PRESENT",
                        "write_action_taken": False,
                        "note": "Exact resource title already exists on the course page; no duplicate was created.",
                    }
                )
                write_result(report_dir, "result.json", result)
                append_ledger({"event": "moodle_course_file_upload_already_present", "result": result})
                print(json.dumps(result, indent=2, ensure_ascii=False))
                return

            print(f"Opening Moodle add-resource form: {add_url}")
            page.goto(add_url, wait_until="domcontentloaded", timeout=60000)
            wait_for_form_or_course(
                page,
                float(env.get("MOODLE_BROWSER_WAIT_SECONDS", "900")),
                add_url,
                auto_click_hku=auto_click_hku,
            )
            if page.locator("#id_name").count() != 1:
                raise RuntimeError("Moodle add-resource form did not expose #id_name.")
            save_screenshot(page, report_dir, "02_add_resource_form_before_fill.png")
            write_result(report_dir, "02_add_resource_form_before_fill.json", page_summary(page))

            if args.precheck_only:
                result.update({"ok": True, "status": "PRECHECK_ONLY", "write_action_taken": False})
                write_result(report_dir, "result.json", result)
                append_ledger({"event": "moodle_course_file_upload_precheck", "result": result})
                print(json.dumps(result, indent=2, ensure_ascii=False))
                return

            page.locator("#id_name").fill(operation["resource_name"])
            set_intro(page, operation.get("intro", ""))
            select_hidden_visibility(page, operation.get("expected_visibility", "Show on course page"))
            upload_details = upload_file_via_filepicker(page, report_dir, upload_path, expected_filename)
            save_screenshot(page, report_dir, "03_after_fill_and_file_upload_before_save.png")
            write_result(
                report_dir,
                "03_after_fill_and_file_upload_before_save.json",
                {"page": page_summary(page), "upload": upload_details},
            )

            click_save_and_wait(page, PlaywrightTimeoutError)
            result["saves_count"] = 1
            result["write_action_taken"] = True
            save_screenshot(page, report_dir, "04_after_save_display.png")
            display_summary = page_summary(page)
            write_result(report_dir, "04_after_save_display.json", display_summary)
            display_text = visible_text(page, 30000)
            file_contents = upload_path.read_text(encoding="utf-8", errors="replace")
            file_signal = expected_filename in display_text or file_contents[:60].strip() in display_text
            title_signal = operation["resource_name"] in display_text or operation["resource_name"] in display_summary.get("h1", "")

            page.goto(course_url, wait_until="domcontentloaded", timeout=60000)
            wait_for_form_or_course(page, 30, course_url, auto_click_hku=auto_click_hku)
            save_screenshot(page, report_dir, "05_verify_course_after_save.png")
            course_summary = page_summary(page)
            write_result(report_dir, "05_verify_course_after_save.json", course_summary)
            course_text = visible_text(page, 30000)
            course_title_signal = operation["resource_name"] in course_text

            if not title_signal and not course_title_signal:
                raise RuntimeError("Saved resource title was not visible on display page or course page.")
            if not file_signal and expected_filename not in course_text:
                raise RuntimeError("Uploaded file signal was not visible after save.")

            result.update(
                {
                    "ok": True,
                    "status": "PASS",
                    "display_url": display_summary.get("url", ""),
                    "title_visible_after_save": bool(title_signal or course_title_signal),
                    "file_visible_after_save": bool(file_signal or expected_filename in course_text),
                    "course_verify_url": course_summary.get("url", ""),
                    "screenshots": [
                        str(report_dir / "04_after_save_display.png"),
                        str(report_dir / "05_verify_course_after_save.png"),
                    ],
                }
            )
            write_result(report_dir, "result.json", result)
            append_ledger({"event": "moodle_course_file_upload_executed", "result": result})
            print(json.dumps(result, indent=2, ensure_ascii=False))
        except Exception as exc:
            result.update({"ok": False, "status": "BLOCKED", "blocker": str(exc), "current_url": page.url})
            try:
                save_screenshot(page, report_dir, "error_state.png")
                result["error_screenshot"] = str(report_dir / "error_state.png")
            except Exception:
                pass
            write_result(report_dir, "result.json", result)
            append_ledger({"event": "moodle_course_file_upload_blocked", "result": result})
            print(json.dumps(result, indent=2, ensure_ascii=False))
            raise SystemExit(1)
        finally:
            if not keep_open:
                try:
                    context.close()
                except Exception:
                    pass


if __name__ == "__main__":
    main()
