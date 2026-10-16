from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_moodle_course_clear_file_resources import call_apim_contents, flatten_modules, load_local_env
from run_moodle_course_file_reset_reupload import confirm_delete_current_page, goto_with_retry, open_course
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


def operation_target(operation: dict[str, Any]) -> dict[str, str]:
    env_overrides = operation.get("env_overrides") if isinstance(operation.get("env_overrides"), dict) else {}
    target_id = str(
        operation.get("target_module_id")
        or operation.get("cmid")
        or env_overrides.get("MOODLE_TARGET_MODULE_ID")
        or ""
    ).strip()
    title = str(
        operation.get("target_object")
        or operation.get("title")
        or operation.get("name")
        or env_overrides.get("MOODLE_TARGET_TITLE")
        or env_overrides.get("MOODLE_TEST_OBJECT_NAME")
        or ""
    ).strip()
    modname = str(operation.get("modname") or env_overrides.get("MOODLE_TARGET_MODNAME") or "").strip()
    return {"cmid": target_id, "title": re.sub(r"\s+", " ", title).strip(), "modname": modname}


def assert_module_delete_operation(operation: dict[str, Any]) -> None:
    if str(operation.get("operation_kind", "")) != "browser_module_delete":
        raise ValueError("Operation kind must be browser_module_delete.")
    if str(operation.get("action", "delete_module")) != "delete_module":
        raise ValueError("Operation action must be delete_module.")
    if str(operation.get("target_course_id", "")) != SANDBOX_COURSE_ID:
        raise ValueError(f"Refusing non-sandbox course id: {operation.get('target_course_id')!r}")
    course_url = str(operation.get("target_course_url", ""))
    if "migrationtest-moodle.hku.hk" not in course_url or f"id={SANDBOX_COURSE_ID}" not in course_url:
        raise ValueError(f"Refusing non-sandbox Moodle course URL: {course_url!r}")
    target = operation_target(operation)
    if not target["cmid"].isdigit() or not target["title"]:
        raise ValueError("Module delete requires numeric target_module_id/cmid and exact module title.")


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


def delete_one_module(page, timeout_error_type, module: dict[str, str], report_dir: Path) -> dict[str, Any]:
    cmid = module["cmid"]
    title = module["title"]
    item_dir = report_dir / f"delete_{safe_name(cmid + '-' + title)}"
    item_dir.mkdir(parents=True, exist_ok=True)
    delete_url = f"https://{SANDBOX_HOST}/course/mod.php?delete={cmid}"
    result: dict[str, Any] = {
        "cmid": cmid,
        "title": title,
        "delete_url": delete_url,
        "ok": False,
        "status": "STARTED",
    }
    try:
        goto_with_retry(page, delete_url, wait_until="domcontentloaded", timeout=60000)
        save_screenshot(page, item_dir, "01_delete_confirmation.png")
        write_result(item_dir, "01_delete_confirmation.json", page_summary(page))
        if looks_like_login(page):
            raise RuntimeError("Moodle requested login during delete confirmation.")
        page_text = visible_text(page, 8000)
        if title and title not in page_text:
            raise RuntimeError(f"Moodle delete confirmation did not show exact target title {title!r}.")
        clicked = confirm_delete_current_page(page)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=45000)
        except timeout_error_type:
            page.wait_for_timeout(2000)
        try:
            save_screenshot(page, item_dir, "02_after_delete.png")
            write_result(item_dir, "02_after_delete.json", page_summary(page))
        except Exception as exc:
            result["post_delete_evidence_warning"] = str(exc)
        result.update({"ok": True, "status": "PASS", "clicked": clicked, "after_url": page.url})
    except Exception as exc:
        result.update({"ok": False, "status": "BLOCKED", "blocker": str(exc), "current_url": getattr(page, "url", "")})
        try:
            save_screenshot(page, item_dir, "error_state.png")
        except Exception:
            pass
    write_result(item_dir, "result.json", result)
    return result


def execute_module_delete_operation(
    operation: dict[str, Any],
    env: dict[str, str],
    page,
    timeout_error_type,
    *,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    assert_module_delete_operation(operation)
    env = {**env, "MOODLE_AUTO_CLICK_HKU_LOGIN": env.get("MOODLE_AUTO_CLICK_HKU_LOGIN", "0")}
    target = operation_target(operation)
    operation_id = str(operation.get("operation_id", "SANDBOX-BROWSER-MODULE-DELETE"))
    report_dir = report_dir or REPORTS / f"moodle_module_delete_{stamp()}_{safe_name(operation_id)}"
    report_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "ok": False,
        "status": "STARTED",
        "operation_id": operation_id,
        "approval_run_id": operation.get("approval_run_id", ""),
        "course_url": SANDBOX_COURSE_URL,
        "target_module_id": target["cmid"],
        "target_object": target["title"],
        "modname": target["modname"],
        "report_dir": str(report_dir),
        "saves_count": 0,
        "deletes_count": 0,
        "write_action_taken": False,
    }

    try:
        before_contents = call_apim_contents_with_retry(env)
        live = find_live_module(before_contents, target["cmid"])
        result["preflight"] = {"target": target, "live": live, "ok": False}
        write_result(report_dir, "01_apim_preflight.json", result["preflight"])
        if live is None:
            result.update(
                {
                    "ok": True,
                    "status": "PASS",
                    "note": "already_deleted",
                    "preflight": {"target": target, "live": None, "ok": True, "already_absent": True},
                }
            )
            write_result(report_dir, "result.json", result)
            return result
        if str(live.get("name", "")).strip() != target["title"]:
            raise RuntimeError(f"Target title mismatch for cmid {target['cmid']}: expected {target['title']!r}, live {live.get('name')!r}")
        if target["modname"] and str(live.get("modname", "")).strip() != target["modname"]:
            raise RuntimeError(f"Target module type mismatch for cmid {target['cmid']}: expected {target['modname']!r}, live {live.get('modname')!r}")
        result["preflight"] = {"target": target, "live": live, "ok": True}
        write_result(report_dir, "01_apim_preflight.json", result["preflight"])

        open_course(page, env, report_dir, "02_course_before_delete")
        delete_result = delete_one_module(page, timeout_error_type, target, report_dir)
        result["delete_result"] = delete_result
        if not delete_result.get("ok"):
            raise RuntimeError(delete_result.get("blocker") or "Moodle module delete action did not complete.")
        result["saves_count"] = 1
        result["deletes_count"] = 1
        result["write_action_taken"] = True

        after_contents = wait_for_expected_readback(
            lambda: call_apim_contents(env),
            lambda contents: find_live_module(contents, target["cmid"]) is None,
            env,
            description=f"deletion of module {target['title']!r}",
        )
        remaining = find_live_module(after_contents, target["cmid"])
        verify = {
            "ok": remaining is None,
            "target_module_id": target["cmid"],
            "remaining": remaining,
            "module_count": len(flatten_modules(after_contents)),
        }
        result["apim_verify"] = verify
        write_result(report_dir, "99_apim_verify.json", verify)
        open_course(page, env, report_dir, "99_course_after_delete")
        if not verify["ok"]:
            raise RuntimeError(f"APIM readback still shows cmid {target['cmid']} after delete.")
        result.update(
            {
                "ok": True,
                "status": "PASS",
                "course_verify_url": SANDBOX_COURSE_URL,
                "final_moodle_state": {
                    "deleted": True,
                    "target_module_id": target["cmid"],
                    "title": target["title"],
                    "modname": target["modname"],
                    "course_url": SANDBOX_COURSE_URL,
                },
            }
        )
    except Exception as exc:
        result.update({"ok": False, "status": "BLOCKED", "blocker": str(exc), "current_url": getattr(page, "url", "")})
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
    parser = argparse.ArgumentParser(description="Delete one exact Moodle sandbox module after approval.")
    parser.add_argument("--operation", required=True, help="Path to approved operation JSON.")
    parser.add_argument("--execute-approved-change", action="store_true")
    parser.add_argument("--approval-phrase", default="")
    parser.add_argument("--keep-open", action="store_true")
    args = parser.parse_args()

    if not args.execute_approved_change:
        raise SystemExit("This runner is execute-only and requires --execute-approved-change.")

    _operation_path, operation = load_operation(args.operation)
    assert_module_delete_operation(operation)
    expected = (operation.get("browser_execution") or {}).get("execute_approval_phrase", "")
    if expected and args.approval_phrase != expected:
        raise SystemExit("Approval phrase mismatch. Refusing Moodle module delete.")

    env = load_local_env()
    env.setdefault("MOODLE_BROWSER_PROFILE_DIR", ".moodle-browser-profile")
    playwright, context, page, timeout_error_type = launch_page(env)
    try:
        result = execute_module_delete_operation(operation, env, page, timeout_error_type)
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
