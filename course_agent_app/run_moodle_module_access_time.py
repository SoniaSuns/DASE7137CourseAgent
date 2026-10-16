from __future__ import annotations

import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from run_moodle_course_clear_file_resources import call_apim_contents, flatten_modules  # noqa: E402
from run_moodle_course_file_resource_upload import (  # noqa: E402
    SANDBOX_COURSE_ID,
    SANDBOX_HOST,
    click_save_and_wait,
    page_summary,
    save_screenshot,
    wait_for_form_or_course,
    write_result,
)
from run_moodle_course_file_upload_batch import (  # noqa: E402
    release_availability_json,
    release_timestamp,
    safe_name,
    set_availability_restriction,
)


REPORTS = PROJECT_ROOT / "course_files" / "evidence" / "process" / "course_agent_app" / "reports"
SANDBOX_COURSE_URL = f"https://{SANDBOX_HOST}/course/view.php?id={SANDBOX_COURSE_ID}"


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def settings_url_for_cmid(course_url: str, cmid: str) -> str:
    parsed = urlparse(course_url or SANDBOX_COURSE_URL)
    root = f"{parsed.scheme}://{parsed.netloc}"
    return f"{root}/course/modedit.php?update={cmid}&return=1"


def read_settings_state(page, report_dir: Path, name: str) -> dict[str, Any]:
    state = page.evaluate(
        """
        () => {
          const visible = document.querySelector('#id_visible');
          const availability = document.querySelector('[name="availabilityconditionsjson"], #id_availabilityconditionsjson');
          const title = document.querySelector('#id_name, [name="name"]');
          return {
            url: location.href,
            title: title ? title.value : '',
            visibility_value: visible ? visible.value : '',
            visibility_label: visible && visible.selectedOptions && visible.selectedOptions.length
              ? visible.selectedOptions[0].textContent.trim()
              : '',
            availabilityconditionsjson: availability ? availability.value : ''
          };
        }
        """
    )
    write_result(report_dir, f"{name}.json", state)
    return state


def parse_availability(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def availability_has_timestamp(availability: dict[str, Any], timestamp: int) -> bool:
    for condition in availability.get("c") or []:
        if not isinstance(condition, dict):
            continue
        if condition.get("type") == "date" and str(condition.get("d")) == ">=" and int(condition.get("t") or 0) == timestamp:
            return True
    return False


def is_clear_access_time_request(value: Any) -> bool:
    if isinstance(value, dict):
        action = str(
            value.get("action")
            or value.get("mode")
            or value.get("operation")
            or value.get("value")
            or ""
        ).strip().lower()
        return action in {"clear", "remove", "delete", "unset", "none", "清除", "删除", "移除", "去掉"} or bool(
            value.get("clear")
        )
    text = str(value or "").strip()
    lowered = text.lower()
    if lowered.startswith("{") and lowered.endswith("}"):
        try:
            return is_clear_access_time_request(ast.literal_eval(text))
        except (SyntaxError, ValueError):
            pass
    return lowered in {"clear", "remove", "delete", "unset", "none", "null", "清除", "删除", "移除", "去掉"}


def access_time_action(operation: dict[str, Any]) -> str:
    action = str(
        operation.get("access_time_action")
        or operation.get("restriction_time_action")
        or operation.get("target_action")
        or ""
    ).strip().lower()
    if action in {"clear", "remove", "delete", "unset"}:
        return "clear"
    for key in ("target_value", "restriction_time"):
        if is_clear_access_time_request(operation.get(key)):
            return "clear"
    params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
    for key in ("restriction_time", "open_at", "available_from", "release_at"):
        if is_clear_access_time_request(params.get(key)):
            return "clear"
    return "set"


def access_time_target_value(operation: dict[str, Any]) -> str:
    if operation.get("target_value") not in ("", None):
        return str(operation.get("target_value")).strip()
    if operation.get("restriction_time") not in ("", None):
        return str(operation.get("restriction_time")).strip()
    params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
    for key in ("restriction_time", "open_at", "available_from", "release_at"):
        if params.get(key) not in ("", None):
            return str(params.get(key)).strip()
    return ""


def clear_date_restrictions(availability: dict[str, Any]) -> tuple[dict[str, Any], int]:
    original_conditions = availability.get("c") if isinstance(availability.get("c"), list) else []
    original_showc = availability.get("showc") if isinstance(availability.get("showc"), list) else []
    kept_conditions: list[Any] = []
    kept_showc: list[Any] = []
    removed = 0
    for index, condition in enumerate(original_conditions):
        if isinstance(condition, dict) and condition.get("type") == "date":
            removed += 1
            continue
        kept_conditions.append(condition)
        if index < len(original_showc):
            kept_showc.append(original_showc[index])
    cleaned = dict(availability)
    cleaned["op"] = cleaned.get("op") or "&"
    cleaned["c"] = kept_conditions
    cleaned["showc"] = kept_showc
    return cleaned, removed


def availability_has_date_restriction(availability: dict[str, Any]) -> bool:
    return any(isinstance(condition, dict) and condition.get("type") == "date" for condition in availability.get("c") or [])


def module_from_readback(env: dict[str, str], cmid: str) -> dict[str, Any]:
    contents = call_apim_contents(env)
    modules = flatten_modules(contents)
    for module in modules:
        if str(module.get("cmid", "")) == str(cmid):
            return module
    raise RuntimeError(f"Current Moodle readback did not find target module id {cmid}.")


def execute_module_access_time_operation(operation: dict[str, Any], env: dict[str, str], page, timeout_error_type) -> dict[str, Any]:
    cmid = str(operation.get("target_module_id") or operation.get("cmid") or "").strip()
    target_title = str(operation.get("target_object") or operation.get("approval_item") or "").strip()
    mode = access_time_action(operation)
    target_value = "clear" if mode == "clear" else access_time_target_value(operation)
    operation_id = str(operation.get("operation_id") or f"MODULE-ACCESS-TIME-{cmid}")
    report_dir = REPORTS / f"moodle_module_access_time_{stamp()}_{safe_name(operation_id)}"
    report_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "ok": False,
        "status": "STARTED",
        "operation_id": operation_id,
        "approval_run_id": operation.get("approval_run_id", ""),
        "course_url": SANDBOX_COURSE_URL,
        "target_object": target_title,
        "target_module_id": cmid,
        "target_value": target_value,
        "access_time_action": mode,
        "report_dir": str(report_dir),
        "saves_count": 0,
        "write_action_taken": False,
    }
    try:
        if not cmid.isdigit():
            raise RuntimeError("Access-time operation requires a numeric target_module_id.")
        target_timestamp = 0
        if mode == "set":
            target_timestamp = release_timestamp(target_value)
            if target_timestamp <= int(datetime.now(timezone.utc).timestamp()):
                raise RuntimeError("Restriction time is in the past; refusing stale access-time write.")
        module = module_from_readback(env, cmid)
        if target_title and module.get("name") != target_title:
            raise RuntimeError(f"Readback target mismatch: expected {target_title!r}, saw {module.get('name')!r}.")
        settings_url = settings_url_for_cmid(operation.get("target_course_url") or SANDBOX_COURSE_URL, cmid)
        page.goto(settings_url, wait_until="domcontentloaded", timeout=60000)
        wait_for_form_or_course(page, 30, settings_url, auto_click_hku=env.get("MOODLE_AUTO_CLICK_HKU_LOGIN", "0") == "1")
        save_screenshot(page, report_dir, "01_settings_before.png")
        before = read_settings_state(page, report_dir, "01_settings_before")
        result["precheck"] = before
        before_availability = parse_availability(str(before.get("availabilityconditionsjson") or ""))
        if mode == "clear":
            availability, removed_count = clear_date_restrictions(before_availability)
            result["clear"] = {"removed_date_restrictions": removed_count, "availabilityconditionsjson": availability}
            if removed_count == 0:
                result.update(
                    {
                        "ok": True,
                        "status": "PASS",
                        "course_verify_url": SANDBOX_COURSE_URL,
                        "final_access_restrictions": json.dumps(
                            before_availability,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "final_moodle_state": {
                            "settings_url": settings_url,
                            "availabilityconditionsjson": before_availability,
                            "note": "No date restriction was present.",
                        },
                        "note": "No date restriction was present; nothing needed to be cleared.",
                    }
                )
                write_result(report_dir, "result.json", result)
                return result
            release_details = set_availability_restriction(page, {"availabilityconditionsjson": availability})
        else:
            availability = release_availability_json(target_timestamp, show_condition=bool(operation.get("show_release_condition", False)))
            release_details = set_availability_restriction(page, {"availabilityconditionsjson": availability})
        result["release"] = release_details
        save_screenshot(page, report_dir, "02_settings_after_fill_before_save.png")
        write_result(report_dir, "02_settings_after_fill_before_save.json", page_summary(page))
        click_save_and_wait(page, timeout_error_type)
        result["saves_count"] = 1
        result["write_action_taken"] = True
        page.goto(settings_url, wait_until="domcontentloaded", timeout=60000)
        wait_for_form_or_course(page, 30, settings_url, auto_click_hku=env.get("MOODLE_AUTO_CLICK_HKU_LOGIN", "0") == "1")
        save_screenshot(page, report_dir, "03_settings_after_save.png")
        after = read_settings_state(page, report_dir, "03_settings_after_save")
        saved_availability = parse_availability(str(after.get("availabilityconditionsjson") or ""))
        if mode == "clear":
            if availability_has_date_restriction(saved_availability):
                raise RuntimeError("Saved Moodle availability JSON still includes a date restriction.")
        elif not availability_has_timestamp(saved_availability, target_timestamp):
            raise RuntimeError("Saved Moodle availability JSON did not include the approved restriction timestamp.")
        result.update(
            {
                "ok": True,
                "status": "PASS",
                "course_verify_url": SANDBOX_COURSE_URL,
                "final_access_restrictions": json.dumps(saved_availability, ensure_ascii=False, separators=(",", ":")),
                "final_moodle_state": {
                    "settings_url": settings_url,
                    "availabilityconditionsjson": saved_availability,
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
