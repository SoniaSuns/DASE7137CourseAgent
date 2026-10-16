from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from course_agent import normalize_browser_steps
from run_moodle_course_clear_file_resources import call_apim_contents, flatten_modules
from run_moodle_course_file_reset_reupload import open_course
from run_moodle_course_file_resource_upload import (
    SANDBOX_COURSE_ID,
    SANDBOX_HOST,
    save_screenshot,
    visible_text,
    write_result,
)
from run_moodle_course_file_upload_batch import safe_name


ROOT = Path(__file__).resolve().parent
REPORTS = ROOT.parent / "course_files" / "evidence" / "process" / "course_agent_app" / "reports"
SANDBOX_COURSE_URL = f"https://{SANDBOX_HOST}/course/view.php?id={SANDBOX_COURSE_ID}"
STEP_TYPES = {
    "goto",
    "click",
    "fill",
    "select",
    "check",
    "uncheck",
    "press",
    "wait",
    "wait_for_text",
    "assert_text",
    "assert_no_text",
    "screenshot",
    "readback_course_contents",
}
WRITE_CLICK_WORDS = (
    "save",
    "save changes",
    "save and display",
    "delete",
    "remove",
    "yes",
    "confirm",
    "update",
)
ADD_ACTIVITY_TEXT = "add an activity or resource"


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def is_sandbox_moodle_url(url: str) -> bool:
    return bool(url) and "migrationtest-moodle.hku.hk" in url and "id=118635" not in url


def call_apim_contents_with_retry(env: dict[str, str], *, attempts: int | None = None, delay_seconds: float | None = None):
    if attempts is None:
        try:
            attempts = int(float(env.get("COURSE_AGENT_READBACK_ATTEMPTS", "3")))
        except ValueError:
            attempts = 3
    if delay_seconds is None:
        try:
            delay_seconds = float(env.get("COURSE_AGENT_READBACK_RETRY_SECONDS", "1.5"))
        except ValueError:
            delay_seconds = 1.5
    attempts = max(1, attempts)
    delay_seconds = max(0.0, delay_seconds)
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


def assert_browser_moodle_task(operation: dict[str, Any]) -> None:
    if operation.get("operation_kind") != "browser_moodle_task":
        raise ValueError("Operation kind must be browser_moodle_task.")
    if str(operation.get("target_course_id", "")) != SANDBOX_COURSE_ID:
        raise ValueError(f"Refusing non-sandbox course id: {operation.get('target_course_id')!r}")
    if not is_sandbox_moodle_url(str(operation.get("target_course_url", ""))):
        raise ValueError(f"Refusing non-sandbox Moodle course URL: {operation.get('target_course_url')!r}")
    steps = operation.get("browser_steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("browser_moodle_task requires non-empty browser_steps.")
    if not isinstance(operation.get("readback_expectations") or operation.get("verification_steps"), list):
        raise ValueError("browser_moodle_task requires readback_expectations or verification_steps.")


def _with_step_index(locator, step: dict[str, Any]):
    if step.get("nth") in (None, ""):
        return locator.first
    try:
        index = int(step["nth"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Browser step nth must be a non-negative integer.") from exc
    if index < 0:
        raise ValueError("Browser step nth must be a non-negative integer.")
    return locator.nth(index)


def locator_for_step(page, step: dict[str, Any]):
    if step.get("selector"):
        return _with_step_index(page.locator(str(step["selector"])), step)
    exact = bool(step.get("exact", False))
    if step.get("role"):
        name = step.get("name") or step.get("text") or step.get("label") or None
        return _with_step_index(page.get_by_role(str(step["role"]), name=name, exact=exact), step)
    if step.get("label"):
        return _with_step_index(page.get_by_label(str(step["label"]), exact=exact), step)
    if step.get("placeholder"):
        return _with_step_index(page.get_by_placeholder(str(step["placeholder"]), exact=exact), step)
    if step.get("text"):
        return _with_step_index(page.get_by_text(str(step["text"]), exact=exact), step)
    raise ValueError("Browser step requires selector, role/name, label, placeholder, or text.")


def wait_for_text(page, text: str, timeout_ms: int) -> None:
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        if text in visible_text(page, 30000):
            return
        page.wait_for_timeout(500)
    raise RuntimeError(f"Timed out waiting for page text: {text!r}")


def editing_control_requested(step: dict[str, Any]) -> bool:
    label = " ".join(
        str(step.get(key, ""))
        for key in ("name", "text", "label", "selector")
        if step.get(key)
    ).casefold()
    return any(text in label for text in ("edit mode", "turn editing on", "start editing"))


def edit_mode_is_enabled(page) -> bool:
    for selector in (
        "body.editing",
        '[data-action="open-chooser"]',
        '[data-for="sectionAddCM"]',
    ):
        try:
            if page.locator(selector).count() > 0:
                return True
        except Exception:
            continue
    return False


def click_editing_control(page, timeout_ms: int) -> None:
    if edit_mode_is_enabled(page):
        return
    patterns = ["Edit mode", "Turn editing on", "Start editing"]
    candidates = [
        page.locator("#user-editing").first,
        page.locator('form.editmode-switch-form input[type="checkbox"]').first,
        page.get_by_role("switch", name=re.compile("Edit mode", re.IGNORECASE)).first,
        page.get_by_label(re.compile("Edit mode", re.IGNORECASE)).first,
    ]
    for text in patterns:
        candidates.extend(
            (
            page.get_by_role("button", name=re.compile(text, re.IGNORECASE)).first,
            page.get_by_role("link", name=re.compile(text, re.IGNORECASE)).first,
            page.get_by_text(re.compile(text, re.IGNORECASE)).first,
            )
        )
    errors: list[str] = []
    for locator in candidates:
        try:
            if locator.count() <= 0:
                continue
            locator.click(timeout=timeout_ms)
            deadline = time.time() + timeout_ms / 1000
            while time.time() < deadline:
                if edit_mode_is_enabled(page):
                    return
                page.wait_for_timeout(250)
            errors.append("control clicked but Moodle edit mode did not become enabled")
        except Exception as exc:
            errors.append(str(exc)[:200])
    raise RuntimeError(
        "Could not enable Moodle edit mode and verify that section add controls became available: "
        + "; ".join(errors[-4:])
    )


def is_add_activity_step(step: dict[str, Any]) -> bool:
    label = " ".join(
        str(step.get(key, ""))
        for key in ("name", "text", "label")
        if step.get(key)
    ).casefold()
    return ADD_ACTIVITY_TEXT in label


def section_context_for_operation(
    operation: dict[str, Any],
    course_contents: list[dict[str, Any]],
) -> dict[str, str]:
    refs: list[str] = []
    for expectation in operation.get("readback_expectations") or []:
        if not isinstance(expectation, dict):
            continue
        kind = str(expectation.get("type") or expectation.get("kind") or "").strip()
        if kind not in {"module_present", "module_exists"}:
            continue
        ref = str(expectation.get("section") or expectation.get("section_name") or "").strip()
        if ref and ref.casefold() not in {item.casefold() for item in refs}:
            refs.append(ref)
    if len(refs) != 1:
        return {}
    ref = refs[0]
    matches = [
        section
        for section in course_contents
        if isinstance(section, dict)
        and (
            str(section.get("section", "")).strip() == ref
            or str(section.get("name", "")).strip().casefold() == ref.casefold()
        )
    ]
    if len(matches) != 1:
        return {}
    section = matches[0]
    return {
        "section": str(section.get("name") or ref),
        "sectionnum": str(section.get("section", "")),
        "section_id": str(section.get("id", "")),
    }


def click_add_activity_control(page, step: dict[str, Any], timeout_ms: int) -> None:
    if not edit_mode_is_enabled(page):
        click_editing_control(page, timeout_ms)
    sectionnum = str(step.get("sectionnum") or "").strip()
    section_id = str(step.get("section_id") or "").strip()
    section_name = str(step.get("section") or step.get("section_name") or "").strip()
    roots = []
    if sectionnum.isdigit():
        roots.append(page.locator(f"#section-{sectionnum}").first)
    if section_id.isdigit():
        roots.append(page.locator(f'[data-sectionid="{section_id}"]').first)
    if section_name:
        roots.append(
            page.locator('[data-for="section"], .course-section')
            .filter(has_text=re.compile(re.escape(section_name), re.IGNORECASE))
            .first
        )
    if not roots:
        raise RuntimeError(
            "The Add activity step requires an exact section or sectionnum; "
            "refusing to click an unscoped course-page add control."
        )

    errors: list[str] = []
    for root in roots:
        try:
            if root.count() <= 0:
                continue
            root.scroll_into_view_if_needed(timeout=min(timeout_ms, 5000))
            collapsed_toggle = root.locator(
                '[data-for="sectiontoggler"][aria-expanded="false"], '
                '[data-bs-toggle="collapse"][aria-expanded="false"], '
                '[data-toggle="collapse"][aria-expanded="false"]'
            ).first
            if collapsed_toggle.count() > 0:
                collapsed_toggle.click(timeout=min(timeout_ms, 5000))
                page.wait_for_timeout(500)
        except Exception as exc:
            errors.append(str(exc)[:200])
            continue

        # The HKU Space/RemUI course format uses a two-stage, section-scoped
        # add menu: first open the section's trailing "+" menu, then choose
        # "Activity or resource". Excluding data-beforemod avoids inserting
        # before an existing activity.
        add_content = root.locator(
            'button[data-action="open-addingcontent"]:not([data-beforemod])'
        ).first
        if add_content.count() > 0:
            try:
                add_content.scroll_into_view_if_needed(timeout=min(timeout_ms, 5000))
                add_content.click(timeout=timeout_ms)
                chooser_selector = (
                    'button[data-action="open-chooser"]:not([data-beforemod])'
                )
                if sectionnum.isdigit():
                    chooser_selector += f'[data-sectionnum="{sectionnum}"]'
                chooser = root.locator(chooser_selector).first
                chooser.click(timeout=timeout_ms)
                return
            except Exception as exc:
                errors.append(str(exc)[:200])

        for candidate in (
            root.locator('[data-action="open-chooser"]').first,
            root.locator('[data-for="sectionAddCM"] button').first,
            root.get_by_role(
                "button",
                name=re.compile(r"add an activity|add activity", re.IGNORECASE),
            ).first,
        ):
            try:
                if candidate.count() <= 0:
                    continue
                candidate.scroll_into_view_if_needed(timeout=min(timeout_ms, 5000))
                candidate.click(timeout=timeout_ms)
                return
            except Exception as exc:
                errors.append(str(exc)[:200])
    raise RuntimeError(
        f"Could not find the Moodle Add activity control inside section "
        f"{section_name or sectionnum or section_id!r}: {'; '.join(errors[-3:])}"
    )


def click_visible_menuitem(page, step: dict[str, Any], timeout_ms: int) -> bool:
    text = str(step.get("text") or step.get("name") or "").strip()
    if not text:
        return False
    options = page.get_by_role("menuitem", name=text, exact=True)
    try:
        count = options.count()
    except Exception:
        return False
    for index in range(count):
        option = options.nth(index)
        try:
            if not option.is_visible():
                continue
            action_link = option.locator('[data-action="add-chooser-option"]').first
            if action_link.count() > 0 and action_link.is_visible():
                action_link.click(timeout=timeout_ms)
            else:
                option.click(timeout=timeout_ms)
            return True
        except Exception:
            continue
    return False


def is_redundant_activity_chooser_add(page, step: dict[str, Any]) -> bool:
    label = str(step.get("text") or step.get("name") or "").strip().casefold()
    url = str(getattr(page, "url", "") or "")
    return (
        label == "add"
        and ("/course/modedit.php" in url or "/course/mod.php" in url)
        and "add=" in url
    )


def click_step(page, step: dict[str, Any]) -> None:
    timeout_ms = int(step.get("timeout_ms", 30000))
    if editing_control_requested(step):
        click_editing_control(page, timeout_ms)
        return
    if is_redundant_activity_chooser_add(page, step):
        return
    if click_visible_menuitem(page, step, timeout_ms):
        return
    try:
        locator_for_step(page, step).click(timeout=timeout_ms)
        return
    except Exception:
        label = " ".join(
            str(step.get(key, ""))
            for key in ("name", "text", "label", "selector")
            if step.get(key)
        )
        if is_add_activity_step(step):
            click_add_activity_control(page, step, timeout_ms)
            return
        raise


def step_is_write_commit(step: dict[str, Any]) -> bool:
    step_type = str(step.get("type") or step.get("action") or "").strip()
    if step_type != "click":
        return False
    label = " ".join(
        str(step.get(key, ""))
        for key in ("name", "text", "label", "selector")
        if step.get(key)
    ).lower()
    return any(word in label for word in WRITE_CLICK_WORDS)


def run_step(page, env: dict[str, str], report_dir: Path, step: dict[str, Any], index: int) -> dict[str, Any]:
    step_type = str(step.get("type") or step.get("action") or "").strip()
    if step_type not in STEP_TYPES:
        raise ValueError(f"Unsupported browser step type: {step_type!r}")
    label = safe_name(str(step.get("label") or step_type or f"step-{index}"))
    result: dict[str, Any] = {"index": index, "type": step_type, "label": label, "ok": False}

    if step_type == "goto":
        url = str(step.get("url", "")).strip()
        if not is_sandbox_moodle_url(url):
            raise ValueError(f"Refusing non-sandbox URL: {url!r}")
        page.goto(url, wait_until="domcontentloaded", timeout=int(step.get("timeout_ms", 60000)))
    elif step_type == "click":
        click_step(page, step)
    elif step_type == "fill":
        locator_for_step(page, step).fill(str(step.get("value", "")), timeout=int(step.get("timeout_ms", 30000)))
    elif step_type == "select":
        locator = locator_for_step(page, step)
        if step.get("label_value"):
            locator.select_option(label=str(step["label_value"]))
        elif step.get("value") is not None:
            locator.select_option(value=str(step["value"]))
        else:
            raise ValueError("select step requires value or label_value.")
    elif step_type == "check":
        locator_for_step(page, step).check(timeout=int(step.get("timeout_ms", 30000)))
    elif step_type == "uncheck":
        locator_for_step(page, step).uncheck(timeout=int(step.get("timeout_ms", 30000)))
    elif step_type == "press":
        key = str(step.get("key", "")).strip()
        if not key:
            raise ValueError("press step requires key.")
        target = locator_for_step(page, step) if any(step.get(key_name) for key_name in ("selector", "text", "label", "role")) else page
        target.press(key, timeout=int(step.get("timeout_ms", 30000)))
    elif step_type == "wait":
        page.wait_for_timeout(min(int(step.get("timeout_ms", 1000)), 30000))
    elif step_type == "wait_for_text":
        wait_for_text(page, str(step.get("text", "")), int(step.get("timeout_ms", 30000)))
    elif step_type == "assert_text":
        text = str(step.get("text", ""))
        if text not in visible_text(page, 30000):
            raise RuntimeError(f"Expected page text was not found: {text!r}")
    elif step_type == "assert_no_text":
        text = str(step.get("text", ""))
        if text in visible_text(page, 30000):
            raise RuntimeError(f"Unexpected page text is still present: {text!r}")
    elif step_type == "screenshot":
        result["screenshot"] = str(save_screenshot(page, report_dir, f"{index:02d}_{label}.png"))
    elif step_type == "readback_course_contents":
        contents = call_apim_contents_with_retry(env)
        write_result(report_dir, f"{index:02d}_{label}_course_contents.json", {"contents": contents})
        result["module_count"] = len(flatten_modules(contents))

    if bool(step.get("screenshot_after", False)) and step_type != "screenshot":
        result["screenshot_after"] = str(save_screenshot(page, report_dir, f"{index:02d}_{label}_after.png"))
    result.update({"ok": True, "url": page.url})
    return result


def module_matches(module: dict[str, Any], expectation: dict[str, Any]) -> bool:
    name = str(expectation.get("name", "")).strip()
    if name:
        live_name = str(module.get("name", "")).strip()
        if expectation.get("exact", True):
            if live_name != name:
                return False
        elif name not in live_name:
            return False
    modname = str(expectation.get("modname", "")).strip()
    if modname and str(module.get("modname", "")).strip() != modname:
        return False
    if "visible" in expectation and str(module.get("visible", "")).lower() != str(expectation["visible"]).lower():
        return False
    if expectation.get("cmid") and str(module.get("cmid", "")) != str(expectation["cmid"]):
        return False
    return True


def verify_expectations(page, contents: list[dict[str, Any]], expectations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    modules = flatten_modules(contents)
    results: list[dict[str, Any]] = []
    page_text = visible_text(page, 30000)
    for index, expectation in enumerate(expectations, start=1):
        if not isinstance(expectation, dict):
            results.append({"index": index, "ok": False, "reason": "expectation must be an object"})
            continue
        expectation = normalize_expectation(expectation)
        kind = str(expectation.get("type") or expectation.get("kind") or "").strip()
        ok = False
        detail: dict[str, Any] = {}
        if kind in {"module_present", "module_exists"}:
            matches = [module for module in modules if module_matches(module, expectation)]
            ok = bool(matches)
            detail["matches"] = matches[:5]
        elif kind in {"module_absent", "module_missing"}:
            matches = [module for module in modules if module_matches(module, expectation)]
            ok = not matches
            detail["matches"] = matches[:5]
        elif kind in {"text_present", "course_contains_text"}:
            text = str(expectation.get("text", ""))
            ok = text in page_text
        elif kind in {"text_absent", "course_not_contains_text"}:
            text = str(expectation.get("text", ""))
            ok = text not in page_text
        elif kind == "url_contains":
            ok = str(expectation.get("text", "")) in page.url
        else:
            detail["reason"] = f"unsupported expectation kind {kind!r}"
        results.append({"index": index, "kind": kind, "ok": ok, **detail})
    return results


def normalize_expectation(expectation: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(expectation)
    if normalized.get("type") or normalized.get("kind"):
        return normalized
    shorthand = {
        "text_absent": "text_absent",
        "text_present": "text_present",
        "course_not_contains_text": "text_absent",
        "course_contains_text": "text_present",
        "module_absent": "module_absent",
        "module_missing": "module_absent",
        "module_present": "module_present",
        "module_exists": "module_present",
    }
    for key, kind in shorthand.items():
        if key in normalized:
            value = normalized.pop(key)
            normalized["type"] = kind
            if kind.startswith("text_"):
                normalized["text"] = value
            elif isinstance(value, dict):
                normalized.update(value)
            else:
                normalized["name"] = value
            return normalized
    return normalized


def execute_browser_moodle_task(
    operation: dict[str, Any],
    env: dict[str, str],
    page,
    timeout_error_type,
    *,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    operation = dict(operation)
    operation["browser_steps"] = normalize_browser_steps(operation.get("browser_steps"))
    assert_browser_moodle_task(operation)
    operation_id = str(operation.get("operation_id", "SANDBOX-BROWSER-MOODLE-TASK"))
    report_dir = report_dir or REPORTS / f"moodle_browser_task_{stamp()}_{safe_name(operation_id)}"
    report_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "ok": False,
        "status": "STARTED",
        "operation_id": operation_id,
        "approval_run_id": operation.get("approval_run_id", ""),
        "course_url": SANDBOX_COURSE_URL,
        "target_object": operation.get("target_object") or operation.get("approval_item", ""),
        "browser_goal": operation.get("browser_goal") or operation.get("summary", ""),
        "report_dir": str(report_dir),
        "expected_write_count": int(operation.get("expected_write_count", 1)),
        "saves_count": 0,
        "write_action_taken": False,
        "step_results": [],
    }
    try:
        before_contents = call_apim_contents_with_retry(env)
        write_result(report_dir, "01_apim_preflight.json", {"contents": before_contents})
        open_course(page, env, report_dir, "02_course_before_browser_task")
        default_section_context = section_context_for_operation(operation, before_contents)
        for index, raw_step in enumerate(operation.get("browser_steps") or [], start=1):
            step = dict(raw_step)
            if is_add_activity_step(step) and not any(
                step.get(key) not in (None, "")
                for key in ("section", "section_name", "sectionnum", "section_id")
            ):
                step.update(default_section_context)
            step_result = run_step(page, env, report_dir, step, index)
            result["step_results"].append(step_result)
            if step_is_write_commit(step):
                result["saves_count"] += 1
                result["write_action_taken"] = True
            write_result(report_dir, "step_progress.json", {"steps": result["step_results"]})
        after_contents = call_apim_contents_with_retry(env)
        write_result(report_dir, "99_apim_after.json", {"contents": after_contents})
        expectations = operation.get("readback_expectations") or operation.get("verification_steps") or []
        verification = verify_expectations(page, after_contents, expectations)
        result["verification"] = verification
        write_result(report_dir, "99_verification.json", {"verification": verification})
        if not verification or not all(item.get("ok") for item in verification):
            raise RuntimeError("One or more browser task verification expectations failed.")
        save_screenshot(page, report_dir, "99_final_page.png")
        result.update(
            {
                "ok": True,
                "status": "PASS",
                "course_verify_url": SANDBOX_COURSE_URL,
                "final_moodle_state": {
                    "course_url": SANDBOX_COURSE_URL,
                    "final_browser_url": page.url,
                    "verification": verification,
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
