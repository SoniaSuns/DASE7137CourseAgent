from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_moodle_course_file_resource_upload import (
    SANDBOX_COURSE_ID,
    SANDBOX_HOST,
    click_save_and_wait,
    save_screenshot,
    wait_for_form_or_course,
    write_result,
)
from run_moodle_course_file_upload_batch import safe_name
from run_moodle_module_access_time import read_settings_state, settings_url_for_cmid
from run_moodle_module_visibility import call_apim_contents_with_retry, find_live_module
from moodle_readback_consistency import wait_for_expected_readback


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS = PROJECT_ROOT / "course_files" / "evidence" / "process" / "course_agent_app" / "reports"
SANDBOX_COURSE_URL = f"https://{SANDBOX_HOST}/course/view.php?id={SANDBOX_COURSE_ID}"


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def title_target(operation: dict[str, Any]) -> dict[str, str]:
    params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
    env = operation.get("env_overrides") if isinstance(operation.get("env_overrides"), dict) else {}
    return {
        "cmid": str(
            operation.get("target_module_id")
            or operation.get("cmid")
            or env.get("MOODLE_TARGET_MODULE_ID")
            or ""
        ).strip(),
        "before": str(
            operation.get("original_title")
            or operation.get("target_object")
            or params.get("target_title")
            or env.get("MOODLE_ORIGINAL_TITLE")
            or ""
        ).strip(),
        "after": str(
            operation.get("target_title")
            or operation.get("new_title")
            or params.get("new_title")
            or env.get("MOODLE_TARGET_TITLE")
            or ""
        ).strip(),
    }


def assert_title_operation(operation: dict[str, Any]) -> None:
    if str(operation.get("operation_kind") or "") != "direct_title_edit":
        raise ValueError("Operation kind must be direct_title_edit.")
    if str(operation.get("target_course_id") or "") != SANDBOX_COURSE_ID:
        raise ValueError(f"Refusing non-sandbox course id: {operation.get('target_course_id')!r}")
    course_url = str(operation.get("target_course_url") or "")
    if SANDBOX_HOST not in course_url or f"id={SANDBOX_COURSE_ID}" not in course_url:
        raise ValueError(f"Refusing non-sandbox Moodle course URL: {course_url!r}")
    target = title_target(operation)
    if not target["cmid"].isdigit():
        raise ValueError("Title operation requires numeric target_module_id/cmid.")
    if not target["after"]:
        raise ValueError("Title operation requires a non-empty target title.")


def execute_module_title_operation(
    operation: dict[str, Any],
    env: dict[str, str],
    page,
    timeout_error_type,
    *,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    assert_title_operation(operation)
    target = title_target(operation)
    operation_id = str(operation.get("operation_id") or f"MODULE-TITLE-{target['cmid']}")
    report_dir = report_dir or REPORTS / f"moodle_module_title_{stamp()}_{safe_name(operation_id)}"
    report_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "ok": False,
        "status": "STARTED",
        "operation_id": operation_id,
        "approval_run_id": operation.get("approval_run_id", ""),
        "course_url": SANDBOX_COURSE_URL,
        "target_module_id": target["cmid"],
        "target_object": target["before"],
        "original_title": target["before"],
        "target_title": target["after"],
        "report_dir": str(report_dir),
        "saves_count": 0,
        "write_action_taken": False,
    }
    try:
        contents = call_apim_contents_with_retry(env)
        live_before = find_live_module(contents, target["cmid"])
        preflight = {"target": target, "live": live_before, "ok": False}
        write_result(report_dir, "01_apim_preflight.json", preflight)
        if live_before is None:
            raise RuntimeError(f"Target cmid {target['cmid']} is absent from sandbox course readback.")
        live_title = str(live_before.get("name") or "").strip()
        if target["before"] and live_title not in {target["before"], target["after"]}:
            raise RuntimeError(
                f"Target title mismatch for cmid {target['cmid']}: expected {target['before']!r}, live {live_title!r}"
            )
        if live_title == target["after"]:
            result.update(
                {
                    "ok": True,
                    "status": "PASS",
                    "target_object": target["after"],
                    "final_moodle_state": {
                        "target_module_id": target["cmid"],
                        "title": target["after"],
                        "course_url": SANDBOX_COURSE_URL,
                    },
                    "note": "The approved title was already present; no save was needed.",
                }
            )
            write_result(report_dir, "result.json", result)
            return result

        settings_url = settings_url_for_cmid(
            str(operation.get("target_course_url") or SANDBOX_COURSE_URL), target["cmid"]
        )
        page.goto(settings_url, wait_until="domcontentloaded", timeout=60000)
        wait_for_form_or_course(
            page,
            30,
            settings_url,
            auto_click_hku=env.get("MOODLE_AUTO_CLICK_HKU_LOGIN", "0") == "1",
        )
        before = read_settings_state(page, report_dir, "02_settings_before")
        save_screenshot(page, report_dir, "02_settings_before.png")
        if str(before.get("title") or "").strip() != live_title:
            raise RuntimeError("The settings form title did not match action-time Moodle readback.")
        preserved = {
            "visibility_value": before.get("visibility_value"),
            "availabilityconditionsjson": before.get("availabilityconditionsjson"),
        }
        name = page.locator("#id_name")
        if name.count() != 1:
            raise RuntimeError("Moodle settings did not expose one unique module name field.")
        name.fill(target["after"])
        save_screenshot(page, report_dir, "03_before_save.png")
        click_save_and_wait(page, timeout_error_type)
        result["saves_count"] = 1
        result["write_action_taken"] = True

        page.goto(settings_url, wait_until="domcontentloaded", timeout=60000)
        wait_for_form_or_course(
            page,
            30,
            settings_url,
            auto_click_hku=env.get("MOODLE_AUTO_CLICK_HKU_LOGIN", "0") == "1",
        )
        after = read_settings_state(page, report_dir, "04_settings_after")
        save_screenshot(page, report_dir, "04_settings_after.png")
        if str(after.get("title") or "").strip() != target["after"]:
            raise RuntimeError("Saved Moodle settings did not contain the approved title.")
        for key, value in preserved.items():
            if after.get(key) != value:
                raise RuntimeError(f"Title edit changed out-of-scope module setting {key}.")

        after_contents = wait_for_expected_readback(
            lambda: call_apim_contents_with_retry(env),
            lambda contents: (
                (live := find_live_module(contents, target["cmid"])) is not None
                and str(live.get("name") or "").strip() == target["after"]
            ),
            env,
            description=f"title update for module {target['cmid']}",
        )
        live_after = find_live_module(after_contents, target["cmid"])
        if live_after is None or str(live_after.get("name") or "").strip() != target["after"]:
            raise RuntimeError("APIM readback did not verify the approved module title.")
        result.update(
            {
                "ok": True,
                "status": "PASS",
                "target_object": target["after"],
                "course_verify_url": SANDBOX_COURSE_URL,
                "final_moodle_state": {
                    "target_module_id": target["cmid"],
                    "title": target["after"],
                    "course_url": SANDBOX_COURSE_URL,
                    **preserved,
                },
            }
        )
    except Exception as exc:
        result.update(
            {
                "ok": False,
                "status": "BLOCKED",
                "blocker": str(exc),
                "current_url": getattr(page, "url", ""),
            }
        )
        try:
            save_screenshot(page, report_dir, "error_state.png")
        except Exception:
            pass
    write_result(report_dir, "result.json", result)
    return result
