from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_moodle_course_clear_file_resources import call_apim_contents, load_local_env
from run_moodle_course_file_reset_reupload import goto_with_retry, open_course
from run_moodle_course_file_resource_upload import (
    DEFAULT_CHROME_PATH,
    SANDBOX_COURSE_ID,
    SANDBOX_HOST,
    import_playwright,
    load_operation,
    looks_like_login,
    page_summary,
    save_screenshot,
    visible_text,
    write_result,
)
from run_moodle_course_file_upload_batch import safe_name
from moodle_readback_consistency import wait_for_expected_readback


ROOT = Path(__file__).resolve().parent
REPORTS = ROOT.parent / "course_files" / "evidence" / "process" / "course_agent_app" / "reports"
SANDBOX_COURSE_URL = f"https://{SANDBOX_HOST}/course/view.php?id={SANDBOX_COURSE_ID}"


try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def operation_section(operation: dict[str, Any]) -> dict[str, str]:
    env_overrides = operation.get("env_overrides") if isinstance(operation.get("env_overrides"), dict) else {}
    raw_sectionnum = (
        operation.get("sectionnum")
        if operation.get("sectionnum") is not None
        else env_overrides.get("MOODLE_SECTION_NUM")
    )
    return {
        "section_id": str(operation.get("section_id") or env_overrides.get("MOODLE_SECTION_ID") or "").strip(),
        "sectionnum": "" if raw_sectionnum is None else str(raw_sectionnum).strip(),
        "section_name": str(operation.get("section_name") or env_overrides.get("MOODLE_SECTION_NAME") or "").strip(),
        "new_title": str(operation.get("new_title") if "new_title" in operation else operation.get("target_title", "")),
    }


def assert_section_rename_operation(operation: dict[str, Any]) -> None:
    if str(operation.get("operation_kind", "")) != "browser_section_rename":
        raise ValueError("Operation kind must be browser_section_rename.")
    if str(operation.get("action", "rename_section")) != "rename_section":
        raise ValueError("Operation action must be rename_section.")
    if str(operation.get("target_course_id", "")) != SANDBOX_COURSE_ID:
        raise ValueError(f"Refusing non-sandbox course id: {operation.get('target_course_id')!r}")
    course_url = str(operation.get("target_course_url", ""))
    if "migrationtest-moodle.hku.hk" not in course_url or f"id={SANDBOX_COURSE_ID}" not in course_url:
        raise ValueError(f"Refusing non-sandbox Moodle course URL: {course_url!r}")
    section = operation_section(operation)
    if not section["section_id"] and not section["sectionnum"] and not section["section_name"]:
        raise ValueError("Section rename requires section_id, sectionnum, or exact section_name.")
    if "new_title" not in operation and "target_title" not in operation:
        raise ValueError("Section rename requires new_title or target_title; use an empty string to clear a custom title.")


def call_apim_contents_with_retry(env: dict[str, str], *, attempts: int = 5, delay_seconds: float = 2.0) -> list[dict[str, Any]]:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return call_apim_contents(env)
        except Exception as exc:
            last_exc = exc
            if attempt == attempts:
                break
            time.sleep(delay_seconds)
    raise RuntimeError(f"Request failed for core_course_get_contents: {last_exc}") from last_exc


def normalize_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def find_live_section(contents: list[dict[str, Any]], target: dict[str, str]) -> dict[str, Any] | None:
    wanted_id = target.get("section_id", "")
    wanted_num = target.get("sectionnum", "")
    wanted_name = normalize_text(target.get("section_name", ""))
    for section in contents:
        section_id = str(section.get("id") or section.get("sectionid") or "")
        sectionnum = str(section.get("section") if section.get("section") is not None else section.get("sectionnum", ""))
        section_name = normalize_text(section.get("name", ""))
        if wanted_id and section_id == wanted_id:
            return section
        if wanted_num and sectionnum == wanted_num:
            return section
        if wanted_name and section_name == wanted_name:
            return section
    return None


def section_public_name(section: dict[str, Any]) -> str:
    return str(section.get("name") or f"Section {section.get('section', section.get('sectionnum', ''))}").strip()


def section_edit_url(section: dict[str, Any], target: dict[str, str]) -> str:
    section_id = str(section.get("id") or section.get("sectionid") or target.get("section_id") or "").strip()
    sectionnum = str(section.get("section") if section.get("section") is not None else target.get("sectionnum", "")).strip()
    if not section_id:
        return ""
    suffix = f"&sr={sectionnum}" if sectionnum else ""
    return f"https://{SANDBOX_HOST}/course/editsection.php?id={section_id}{suffix}"


def form_visible(page) -> bool:
    selectors = ["#id_name", "input[name='name']", "#id_submitbutton", "button:has-text('Save changes')"]
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible(timeout=1000):
                return True
        except Exception:
            continue
    return False


def open_section_settings(page, env: dict[str, str], report_dir: Path, section: dict[str, Any], target: dict[str, str]) -> str:
    edit_url = section_edit_url(section, target)
    if edit_url:
        goto_with_retry(page, edit_url, wait_until="domcontentloaded", timeout=60000)
        save_screenshot(page, report_dir, "02_section_settings_direct.png")
        write_result(report_dir, "02_section_settings_direct.json", page_summary(page))
        if looks_like_login(page):
            raise RuntimeError("Moodle requested login while opening section settings.")
        if form_visible(page):
            return edit_url

    open_course(page, env, report_dir, "02_course_before_section_settings")
    section_name = section_public_name(section)
    page.evaluate(
        """
        (sectionName) => {
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
          };
          const sections = Array.from(document.querySelectorAll('li.section, section, [data-sectionid], [data-section]'))
            .filter(visible);
          const section = sections.find((candidate) => (candidate.innerText || candidate.textContent || '').includes(sectionName));
          if (!section) {
            throw new Error(`Could not find visible section ${sectionName}`);
          }
          const candidates = Array.from(section.querySelectorAll('a, button, [role="button"]')).filter(visible);
          const edit = candidates.find((el) => {
            const text = [
              el.innerText || '',
              el.textContent || '',
              el.getAttribute('aria-label') || '',
              el.getAttribute('title') || '',
            ].join(' ').toLowerCase();
            return text.includes('edit settings') || text.includes('settings') || text.includes('edit section');
          });
          if (!edit) {
            throw new Error(`Could not find section settings control for ${sectionName}`);
          }
          edit.click();
        }
        """,
        section_name,
    )
    try:
        page.wait_for_load_state("domcontentloaded", timeout=45000)
    except Exception:
        page.wait_for_timeout(2000)
    save_screenshot(page, report_dir, "03_section_settings_ui.png")
    write_result(report_dir, "03_section_settings_ui.json", page_summary(page))
    if not form_visible(page):
        text = visible_text(page, 4000)
        raise RuntimeError(f"Section settings form did not appear. Page text: {text[:1000]}")
    return page.url


def set_checkbox_if_present(page, selector: str, checked: bool) -> bool:
    try:
        locator = page.locator(selector).first
        if not locator.count():
            return False
        current = locator.is_checked(timeout=1000)
        if current != checked:
            if checked:
                locator.check(timeout=10000)
            else:
                locator.uncheck(timeout=10000)
        return True
    except Exception:
        return False


def fill_section_title(page, new_title: str) -> None:
    if new_title:
        set_checkbox_if_present(page, "#id_name_customize", True)
        set_checkbox_if_present(page, "input[name='name_customize']", True)
    else:
        if set_checkbox_if_present(page, "#id_name_customize", False) or set_checkbox_if_present(
            page,
            "input[name='name_customize']",
            False,
        ):
            return

    filled = False
    for selector in ("#id_name", "input[name='name']"):
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible(timeout=1000):
                locator.fill(new_title, timeout=10000)
                filled = True
                break
        except Exception:
            continue
    if not filled and new_title:
        try:
            page.get_by_label("Section name", exact=False).fill(new_title, timeout=10000)
            filled = True
        except Exception:
            pass
    if not filled and new_title:
        raise RuntimeError("Could not fill Moodle section name field.")


def click_save_changes(page, timeout_error_type) -> str:
    selectors = [
        "#id_submitbutton",
        "input[type='submit'][value='Save changes']",
        "button:has-text('Save changes')",
        "input[type='submit'][value='Save and return to course']",
        "button:has-text('Save and return to course')",
    ]
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() and locator.is_visible(timeout=1000):
                locator.click(timeout=10000, no_wait_after=True)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=45000)
                except timeout_error_type:
                    page.wait_for_timeout(2000)
                return selector
        except Exception:
            continue
    text = visible_text(page, 4000)
    raise RuntimeError(f"Could not find Moodle section save button. Page text: {text[:1000]}")


def verify_section_title(
    contents: list[dict[str, Any]],
    live_before: dict[str, Any],
    target: dict[str, str],
    new_title: str,
) -> dict[str, Any]:
    live_after = find_live_section(contents, target)
    old_name = section_public_name(live_before)
    new_name = section_public_name(live_after or {})
    if new_title:
        ok = normalize_text(new_name) == normalize_text(new_title)
    else:
        ok = normalize_text(new_name) != normalize_text(old_name)
    return {
        "ok": ok,
        "old_name": old_name,
        "new_name": new_name,
        "expected_title": new_title,
        "section_id": str((live_after or live_before).get("id") or ""),
        "sectionnum": (
            ""
            if (live_after or live_before).get("section") is None
            else str((live_after or live_before).get("section"))
        ),
    }


def execute_section_rename_operation(
    operation: dict[str, Any],
    env: dict[str, str],
    page,
    timeout_error_type,
    *,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    assert_section_rename_operation(operation)
    env = {**env, "MOODLE_AUTO_CLICK_HKU_LOGIN": env.get("MOODLE_AUTO_CLICK_HKU_LOGIN", "0")}
    target = operation_section(operation)
    operation_id = str(operation.get("operation_id", "SANDBOX-BROWSER-SECTION-RENAME"))
    report_dir = report_dir or REPORTS / f"moodle_section_rename_{stamp()}_{safe_name(operation_id)}"
    report_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "ok": False,
        "status": "STARTED",
        "operation_id": operation_id,
        "approval_run_id": operation.get("approval_run_id", ""),
        "course_url": SANDBOX_COURSE_URL,
        "target_object": target["section_name"] or target["sectionnum"] or target["section_id"],
        "target_title": target["new_title"],
        "report_dir": str(report_dir),
        "saves_count": 0,
        "write_action_taken": False,
    }

    try:
        before_contents = call_apim_contents_with_retry(env)
        live_before = find_live_section(before_contents, target)
        if live_before is None:
            raise RuntimeError(f"Could not resolve section from Moodle readback: {target}")
        result["preflight"] = {"target": target, "live": live_before, "ok": True}
        write_result(report_dir, "01_apim_preflight.json", result["preflight"])

        settings_url = open_section_settings(page, env, report_dir, live_before, target)
        result["settings_url"] = settings_url
        fill_section_title(page, target["new_title"])
        save_screenshot(page, report_dir, "04_before_save.png")
        clicked = click_save_changes(page, timeout_error_type)
        result["saves_count"] = 1
        result["write_action_taken"] = True
        result["clicked"] = clicked
        save_screenshot(page, report_dir, "05_after_save.png")
        write_result(report_dir, "05_after_save.json", page_summary(page))

        after_contents = wait_for_expected_readback(
            lambda: call_apim_contents(env),
            lambda contents: verify_section_title(
                contents,
                live_before,
                target,
                target["new_title"],
            )["ok"],
            env,
            description=f"section title update to {target['new_title']!r}",
        )
        verify = verify_section_title(after_contents, live_before, target, target["new_title"])
        result["apim_verify"] = verify
        write_result(report_dir, "99_apim_verify.json", verify)
        open_course(page, env, report_dir, "99_course_after_section_rename")
        if not verify["ok"]:
            raise RuntimeError(f"APIM readback did not verify section title change: {verify}")
        result.update(
            {
                "ok": True,
                "status": "PASS",
                "target_object": verify["old_name"],
                "final_title": verify["new_name"],
                "course_verify_url": SANDBOX_COURSE_URL,
                "final_moodle_state": {
                    "section_id": verify["section_id"],
                    "sectionnum": verify["sectionnum"],
                    "old_name": verify["old_name"],
                    "new_name": verify["new_name"],
                    "course_url": SANDBOX_COURSE_URL,
                },
            }
        )
    except Exception as exc:
        result.update({"ok": False, "status": "BLOCKED", "blocker": str(exc), "current_url": getattr(page, "url", "")})
        try:
            save_screenshot(page, report_dir, "error_state.png")
        except Exception:
            pass
    write_result(report_dir, "result.json", result)
    return result


def launch_page(env: dict[str, str]):
    sync_playwright, timeout_error_type = import_playwright()
    playwright = sync_playwright().start()
    profile_dir = Path(env["MOODLE_BROWSER_PROFILE_DIR"]).expanduser()
    if not profile_dir.is_absolute():
        profile_dir = ROOT / profile_dir
    profile_dir.mkdir(parents=True, exist_ok=True)
    chrome_path = env.get("MOODLE_BROWSER_CHROME_PATH", DEFAULT_CHROME_PATH)
    launch_kwargs: dict[str, Any] = {
        "user_data_dir": str(profile_dir),
        "headless": False,
        "viewport": {"width": 1400, "height": 900},
    }
    if chrome_path and Path(chrome_path).exists():
        launch_kwargs["executable_path"] = chrome_path
    context = playwright.chromium.launch_persistent_context(**launch_kwargs)
    page = context.pages[0] if context.pages else context.new_page()
    return playwright, context, page, timeout_error_type


def main() -> None:
    parser = argparse.ArgumentParser(description="Rename one Moodle sandbox section after approval.")
    parser.add_argument("--operation", required=True, help="Path to approved operation JSON.")
    parser.add_argument("--execute-approved-change", action="store_true")
    parser.add_argument("--approval-phrase", default="")
    parser.add_argument("--keep-open", action="store_true")
    args = parser.parse_args()

    if not args.execute_approved_change:
        raise SystemExit("This runner is execute-only and requires --execute-approved-change.")

    _operation_path, operation = load_operation(args.operation)
    assert_section_rename_operation(operation)
    expected = (operation.get("browser_execution") or {}).get("execute_approval_phrase", "")
    if expected and args.approval_phrase != expected:
        raise SystemExit("Approval phrase mismatch. Refusing Moodle section rename.")

    env = load_local_env()
    env.setdefault("MOODLE_BROWSER_PROFILE_DIR", ".moodle-browser-profile")
    playwright, context, page, timeout_error_type = launch_page(env)
    try:
        result = execute_section_rename_operation(operation, env, page, timeout_error_type)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("ok"):
            raise SystemExit(1)
        if args.keep_open:
            print("Keeping browser open. Press Ctrl-C to exit.")
            while True:
                time.sleep(3600)
    finally:
        if not args.keep_open:
            context.close()
            playwright.stop()


if __name__ == "__main__":
    main()
