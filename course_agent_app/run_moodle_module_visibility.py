from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_moodle_course_clear_file_resources import call_apim_contents, flatten_modules, load_local_env
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
    select_hidden_visibility,
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


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "visible", "show"}


def operation_target(operation: dict[str, Any]) -> dict[str, Any]:
    env_overrides = operation.get("env_overrides") if isinstance(operation.get("env_overrides"), dict) else {}
    target_id = str(
        operation.get("target_module_id")
        or operation.get("cmid")
        or env_overrides.get("MOODLE_TARGET_MODULE_ID")
        or ""
    ).strip()
    title = str(
        operation.get("target_object")
        or operation.get("resource_name")
        or operation.get("title")
        or env_overrides.get("MOODLE_TARGET_TITLE")
        or env_overrides.get("MOODLE_TEST_OBJECT_NAME")
        or ""
    ).strip()
    visible = operation.get("visible") if "visible" in operation else env_overrides.get("MOODLE_TARGET_VISIBLE", "")
    return {"cmid": target_id, "title": title, "visible": truthy(visible)}


def assert_visibility_operation(operation: dict[str, Any]) -> None:
    if str(operation.get("operation_kind", "")) != "browser_module_visibility":
        raise ValueError("Operation kind must be browser_module_visibility.")
    if str(operation.get("action", "set_visibility")) != "set_visibility":
        raise ValueError("Operation action must be set_visibility.")
    if str(operation.get("target_course_id", "")) != SANDBOX_COURSE_ID:
        raise ValueError(f"Refusing non-sandbox course id: {operation.get('target_course_id')!r}")
    course_url = str(operation.get("target_course_url", ""))
    if "migrationtest-moodle.hku.hk" not in course_url or f"id={SANDBOX_COURSE_ID}" not in course_url:
        raise ValueError(f"Refusing non-sandbox Moodle course URL: {course_url!r}")
    target = operation_target(operation)
    if not target["cmid"].isdigit():
        raise ValueError("Visibility operation requires numeric target_module_id/cmid.")
    if "visible" not in operation and not (operation.get("env_overrides") or {}).get("MOODLE_TARGET_VISIBLE"):
        raise ValueError("Visibility operation requires visible or MOODLE_TARGET_VISIBLE.")


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


def find_live_module(contents: list[dict[str, Any]], cmid: str) -> dict[str, Any] | None:
    for module in flatten_modules(contents):
        if str(module.get("cmid", "")) == str(cmid):
            return module
    return None


def open_module_settings(page, env: dict[str, str], report_dir: Path, cmid: str) -> str:
    settings_url = f"https://{SANDBOX_HOST}/course/modedit.php?update={cmid}&return=1"
    goto_with_retry(page, settings_url, wait_until="domcontentloaded", timeout=60000)
    save_screenshot(page, report_dir, "02_module_settings.png")
    write_result(report_dir, "02_module_settings.json", page_summary(page))
    if looks_like_login(page):
        raise RuntimeError("Moodle requested login while opening module settings.")
    if not page.locator("#id_visible").count():
        text = visible_text(page, 4000)
        raise RuntimeError(f"Moodle settings form did not expose #id_visible. Page text: {text[:1000]}")
    return settings_url


def click_save_changes(page, timeout_error_type) -> str:
    selectors = [
        "#id_submitbutton",
        "input[type='submit'][value='Save and return to course']",
        "button:has-text('Save and return to course')",
        "input[type='submit'][value='Save changes']",
        "button:has-text('Save changes')",
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
    raise RuntimeError(f"Could not find Moodle module save button. Page text: {text[:1000]}")


def execute_module_visibility_operation(
    operation: dict[str, Any],
    env: dict[str, str],
    page,
    timeout_error_type,
    *,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    assert_visibility_operation(operation)
    env = {**env, "MOODLE_AUTO_CLICK_HKU_LOGIN": env.get("MOODLE_AUTO_CLICK_HKU_LOGIN", "0")}
    target = operation_target(operation)
    operation_id = str(operation.get("operation_id", "SANDBOX-BROWSER-MODULE-VISIBILITY"))
    report_dir = report_dir or REPORTS / f"moodle_module_visibility_{stamp()}_{safe_name(operation_id)}"
    report_dir.mkdir(parents=True, exist_ok=True)
    expected_visibility = "Show on course page" if target["visible"] else "Hide on course page"

    result: dict[str, Any] = {
        "ok": False,
        "status": "STARTED",
        "operation_id": operation_id,
        "approval_run_id": operation.get("approval_run_id", ""),
        "course_url": SANDBOX_COURSE_URL,
        "target_module_id": target["cmid"],
        "target_object": target["title"],
        "target_visible": target["visible"],
        "report_dir": str(report_dir),
        "saves_count": 0,
        "write_action_taken": False,
    }

    try:
        before_contents = call_apim_contents_with_retry(env)
        live_before = find_live_module(before_contents, target["cmid"])
        result["preflight"] = {"target": target, "live": live_before, "ok": False}
        write_result(report_dir, "01_apim_preflight.json", result["preflight"])
        if live_before is None:
            raise RuntimeError(f"Target cmid {target['cmid']} is absent from sandbox course readback.")
        if target["title"] and str(live_before.get("name", "")).strip() != target["title"]:
            raise RuntimeError(
                f"Target title mismatch for cmid {target['cmid']}: "
                f"expected {target['title']!r}, live {live_before.get('name')!r}"
            )
        result["preflight"] = {"target": target, "live": live_before, "ok": True}
        write_result(report_dir, "01_apim_preflight.json", result["preflight"])

        settings_url = open_module_settings(page, env, report_dir, target["cmid"])
        result["settings_url"] = settings_url
        select_hidden_visibility(page, expected_visibility)
        save_screenshot(page, report_dir, "03_before_save.png")
        clicked = click_save_changes(page, timeout_error_type)
        result["saves_count"] = 1
        result["write_action_taken"] = True
        result["clicked"] = clicked
        save_screenshot(page, report_dir, "04_after_save.png")
        write_result(report_dir, "04_after_save.json", page_summary(page))

        after_contents = wait_for_expected_readback(
            lambda: call_apim_contents(env),
            lambda contents: (
                (live := find_live_module(contents, target["cmid"])) is not None
                and truthy(live.get("visible")) == target["visible"]
            ),
            env,
            description=f"visibility update for module {target['cmid']}",
        )
        live_after = find_live_module(after_contents, target["cmid"])
        verify = {
            "ok": live_after is not None and truthy(live_after.get("visible")) == target["visible"],
            "target_module_id": target["cmid"],
            "expected_visible": target["visible"],
            "live_after": live_after,
        }
        result["apim_verify"] = verify
        write_result(report_dir, "99_apim_verify.json", verify)
        open_course(page, env, report_dir, "99_course_after_visibility")
        if not verify["ok"]:
            raise RuntimeError(f"APIM readback did not verify module visibility change: {verify}")
        result.update(
            {
                "ok": True,
                "status": "PASS",
                "target_object": str(live_after.get("name", target["title"])),
                "final_visibility": expected_visibility,
                "course_verify_url": SANDBOX_COURSE_URL,
                "final_moodle_state": {
                    "target_module_id": target["cmid"],
                    "title": str(live_after.get("name", target["title"])),
                    "visible": target["visible"],
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
    parser = argparse.ArgumentParser(description="Set one Moodle sandbox module visibility after approval.")
    parser.add_argument("--operation", required=True, help="Path to approved operation JSON.")
    parser.add_argument("--execute-approved-change", action="store_true")
    parser.add_argument("--approval-phrase", default="")
    parser.add_argument("--keep-open", action="store_true")
    args = parser.parse_args()

    if not args.execute_approved_change:
        raise SystemExit("This runner is execute-only and requires --execute-approved-change.")

    _operation_path, operation = load_operation(args.operation)
    assert_visibility_operation(operation)
    expected = (operation.get("browser_execution") or {}).get("execute_approval_phrase", "")
    if expected and args.approval_phrase != expected:
        raise SystemExit("Approval phrase mismatch. Refusing Moodle visibility change.")

    env = load_local_env()
    env.setdefault("MOODLE_BROWSER_PROFILE_DIR", ".moodle-browser-profile")
    playwright, context, page, timeout_error_type = launch_page(env)
    try:
        result = execute_module_visibility_operation(operation, env, page, timeout_error_type)
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
