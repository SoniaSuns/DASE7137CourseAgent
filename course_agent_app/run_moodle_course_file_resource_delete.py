from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from run_moodle_course_clear_file_resources import call_apim_contents, flatten_modules, load_local_env
from run_moodle_course_file_reset_reupload import delete_one_resource, open_course
from run_moodle_course_file_resource_upload import (
    DEFAULT_CHROME_PATH,
    SANDBOX_COURSE_ID,
    SANDBOX_HOST,
    import_playwright,
    load_operation,
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


def cmid_from_resource_url(url: str) -> str:
    parsed = urlparse(str(url))
    if parsed.hostname and parsed.hostname != SANDBOX_HOST:
        return ""
    if "/mod/resource/view.php" not in parsed.path:
        return ""
    values = parse_qs(parsed.query).get("id") or []
    return values[0] if values and values[0].isdigit() else ""


def operation_target(operation: dict[str, Any]) -> dict[str, str]:
    env_overrides = operation.get("env_overrides") if isinstance(operation.get("env_overrides"), dict) else {}
    resource_url = str(
        operation.get("target_resource_url")
        or operation.get("resource_url")
        or operation.get("display_url")
        or env_overrides.get("MOODLE_RESOURCE_URL")
        or env_overrides.get("MOODLE_TARGET_RESOURCE_URL")
        or ""
    ).strip()
    cmid = str(
        operation.get("target_module_id")
        or operation.get("cmid")
        or operation.get("resource_cmid")
        or env_overrides.get("MOODLE_TARGET_MODULE_ID")
        or env_overrides.get("MOODLE_RESOURCE_CMID")
        or cmid_from_resource_url(resource_url)
        or ""
    ).strip()
    title = str(
        operation.get("resource_name")
        or operation.get("target_object")
        or operation.get("title")
        or operation.get("name")
        or env_overrides.get("MOODLE_RESOURCE_NAME")
        or env_overrides.get("MOODLE_TARGET_TITLE")
        or env_overrides.get("MOODLE_TEST_OBJECT_NAME")
        or ""
    ).strip()
    return {
        "cmid": cmid,
        "title": re.sub(r"\s+", " ", title).strip(),
        "resource_url": resource_url,
    }


def assert_delete_operation(operation: dict[str, Any], approval_phrase: str = "", *, require_phrase: bool = False) -> None:
    if str(operation.get("operation_kind", "")) != "browser_course_file_resource_delete":
        raise ValueError("Operation kind must be browser_course_file_resource_delete.")
    if str(operation.get("action", "delete_file_resource")) != "delete_file_resource":
        raise ValueError("Operation action must be delete_file_resource.")
    if str(operation.get("target_course_id", "")) != SANDBOX_COURSE_ID:
        raise ValueError(f"Refusing non-sandbox course id: {operation.get('target_course_id')!r}")
    course_url = str(operation.get("target_course_url", ""))
    if "migrationtest-moodle.hku.hk" not in course_url or f"id={SANDBOX_COURSE_ID}" not in course_url:
        raise ValueError(f"Refusing non-sandbox Moodle course URL: {course_url!r}")
    target = operation_target(operation)
    if not target["cmid"].isdigit() or not target["title"]:
        raise ValueError("Delete operation requires numeric target_module_id/cmid and exact resource title.")
    if require_phrase:
        expected = (operation.get("browser_execution") or {}).get("execute_approval_phrase", "")
        if not expected or approval_phrase != expected:
            raise ValueError("Approval phrase mismatch. Refusing Moodle delete.")


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


def find_live_resource(contents: list[dict[str, Any]], cmid: str) -> dict[str, Any] | None:
    for module in flatten_modules(contents):
        if str(module.get("cmid", "")) == str(cmid):
            return module
    return None


def execute_delete_operation(
    operation: dict[str, Any],
    env: dict[str, str],
    page,
    timeout_error_type,
    *,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    assert_delete_operation(operation)
    env = {**env, "MOODLE_AUTO_CLICK_HKU_LOGIN": env.get("MOODLE_AUTO_CLICK_HKU_LOGIN", "0")}
    target = operation_target(operation)
    operation_id = str(operation.get("operation_id", "SANDBOX-BROWSER-COURSE-FILE-RESOURCE-DELETE"))
    report_dir = report_dir or REPORTS / f"moodle_course_file_delete_{stamp()}_{safe_name(operation_id)}"
    report_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "ok": False,
        "status": "STARTED",
        "operation_id": operation_id,
        "approval_run_id": operation.get("approval_run_id", ""),
        "course_url": SANDBOX_COURSE_URL,
        "target_module_id": target["cmid"],
        "resource_name": target["title"],
        "target_resource_url": target["resource_url"],
        "report_dir": str(report_dir),
        "saves_count": 0,
        "deletes_count": 0,
        "write_action_taken": False,
    }

    try:
        before_contents = call_apim_contents_with_retry(env)
        live = find_live_resource(before_contents, target["cmid"])
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
        if live.get("modname") != "resource":
            raise RuntimeError(f"Target cmid {target['cmid']} is not a Moodle File resource: {live}")
        if str(live.get("name", "")).strip() != target["title"]:
            raise RuntimeError(f"Target title mismatch for cmid {target['cmid']}: expected {target['title']!r}, live {live.get('name')!r}")
        result["preflight"] = {"target": target, "live": live, "ok": True}
        write_result(report_dir, "01_apim_preflight.json", result["preflight"])

        open_course(page, env, report_dir, "02_course_before_delete")
        delete_result = delete_one_resource(
            page,
            timeout_error_type,
            {"cmid": target["cmid"], "title": target["title"], "href": str(live.get("url", target["resource_url"]))},
            report_dir,
        )
        result["delete_result"] = delete_result
        if not delete_result.get("ok"):
            raise RuntimeError(delete_result.get("blocker") or "Moodle delete action did not complete.")
        result["saves_count"] = 1
        result["deletes_count"] = 1
        result["write_action_taken"] = True

        after_contents = wait_for_expected_readback(
            lambda: call_apim_contents(env),
            lambda contents: find_live_resource(contents, target["cmid"]) is None,
            env,
            description=f"deletion of File resource {target['title']!r}",
        )
        remaining = find_live_resource(after_contents, target["cmid"])
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
    parser = argparse.ArgumentParser(description="Delete one exact Moodle sandbox File resource after approval.")
    parser.add_argument("--operation", required=True, help="Path to approved operation JSON.")
    parser.add_argument("--execute-approved-change", action="store_true")
    parser.add_argument("--approval-phrase", default="")
    parser.add_argument("--keep-open", action="store_true")
    args = parser.parse_args()

    if not args.execute_approved_change:
        raise SystemExit("This runner is execute-only and requires --execute-approved-change.")

    operation_path, operation = load_operation(args.operation)
    assert_delete_operation(operation, args.approval_phrase, require_phrase=True)
    env = load_local_env()
    env.update(os.environ)
    playwright = None
    context = None
    try:
        playwright, context, page, timeout_error_type = launch_page(env)
        result = execute_delete_operation(operation, env, page, timeout_error_type)
        result["operation_path"] = str(operation_path)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    finally:
        if context is not None and not args.keep_open:
            try:
                context.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
