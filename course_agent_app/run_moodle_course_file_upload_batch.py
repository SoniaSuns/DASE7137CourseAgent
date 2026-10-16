from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from run_moodle_course_file_resource_upload import (
    DEFAULT_CHROME_PATH,
    assert_create_file_resource,
    assert_phrase,
    click_save_and_wait,
    course_add_resource_url,
    find_exact_resource_link,
    import_playwright,
    keep_moodle_browser_open,
    load_env,
    looks_like_login,
    page_summary,
    resolve_local_path,
    save_screenshot,
    select_hidden_visibility,
    set_intro,
    upload_file_via_filepicker,
    visible_text,
    wait_for_form_or_course,
    write_result,
)


ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
REPORTS = ROOT.parent / "course_files" / "evidence" / "process" / "course_agent_app" / "reports"
SANDBOX_COURSE_URL = "https://migrationtest-moodle.hku.hk/course/view.php?id=105330"
SANDBOX_COURSE_ID = "105330"


try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass


def safe_name(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-")
    return cleaned[:80] or "item"


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def load_manifest(path_text: str) -> tuple[Path, dict[str, Any]]:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        candidates = [Path.cwd() / path, ROOT / path, WORKSPACE / path]
        path = next((candidate for candidate in candidates if candidate.exists()), WORKSPACE / path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return path, data


def workspace_relative(path_text: str) -> str:
    path = Path(path_text)
    if path.is_absolute():
        try:
            return str(path.relative_to(ROOT))
        except ValueError:
            return str(path)
    workspace_path = WORKSPACE / path
    if workspace_path.exists():
        return os.path.relpath(workspace_path, ROOT)
    return path_text


def assert_manifest(manifest: dict[str, Any], approval_phrase: str | None) -> None:
    target = manifest.get("target") or {}
    if target.get("course_url") != SANDBOX_COURSE_URL or str(target.get("course_id")) != SANDBOX_COURSE_ID:
        raise ValueError("Refusing non-sandbox upload manifest.")
    if manifest.get("summary", {}).get("missing_upload_files"):
        raise ValueError("Refusing manifest with missing upload files.")
    if approval_phrase is not None and approval_phrase != manifest.get("approval_phrase"):
        raise ValueError("Approval phrase mismatch for batch upload.")
    if approval_phrase is not None:
        now_ts = int(datetime.now(timezone.utc).timestamp())
        for item in manifest.get("upload_items", []):
            release_at = str(item.get("release_at", "")).strip()
            if release_at and release_timestamp(release_at) <= now_ts:
                raise ValueError(
                    f"Refusing release-batch write for {item.get('resource_name', 'item')}: "
                    "release_at is already in the past. Generate a fresh manifest."
                )
            min_pre_release = int(item.get("min_pre_release_seconds") or 0)
            if release_at and min_pre_release and release_timestamp(release_at) - now_ts < min_pre_release:
                raise ValueError(
                    f"Refusing release-batch write for {item.get('resource_name', 'item')}: "
                    f"release_at is less than {min_pre_release}s away. Generate a fresh manifest."
                )


def release_timestamp(value: str) -> int:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("release_at is empty.")
    if cleaned.isdigit():
        return int(cleaned)
    parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def release_availability_json(timestamp: int, *, show_condition: bool = False) -> dict[str, Any]:
    return {
        "op": "&",
        "c": [{"type": "date", "d": ">=", "t": timestamp}],
        "showc": [show_condition],
    }


def assert_release_window_before_save(operation: dict[str, Any]) -> None:
    release_ts = operation.get("release_timestamp")
    if not release_ts:
        return
    min_pre_release = int(operation.get("min_pre_release_seconds") or 0)
    remaining = int(release_ts) - int(datetime.now(timezone.utc).timestamp())
    if remaining <= 0:
        raise RuntimeError("Release timestamp was reached before save; refusing stale release-batch write.")
    if min_pre_release and remaining < min_pre_release:
        raise RuntimeError(
            f"Release timestamp is only {remaining}s away before save; "
            f"minimum required is {min_pre_release}s for pre-release verification."
        )


def operation_for_item(manifest: dict[str, Any], item: dict[str, Any], index: int) -> dict[str, Any]:
    upload_path = workspace_relative(item["upload_path"])
    filename = item.get("expected_uploaded_filename") or Path(upload_path).name
    operation_id = f"{manifest['batch_id']}-ITEM-{index:02d}-{safe_name(item['resource_name'])}"
    release_at = str(item.get("release_at", "")).strip()
    release_json = item.get("availabilityconditionsjson")
    release_ts = release_timestamp(release_at) if release_at else None
    if release_ts is not None and not release_json:
        release_json = release_availability_json(
            release_ts,
            show_condition=bool(item.get("show_release_condition", False)),
        )
    visible = bool(item.get("visible", True))
    expected_visibility = item.get("expected_visibility") or (
        "Show on course page" if visible else "Hide on course page"
    )
    return {
        "batch_id": manifest["batch_id"],
        "approval_run_id": manifest["run_id"],
        "operation_id": operation_id,
        "operation_kind": "browser_course_file_resource_upload",
        "risk_level": "medium",
        "target_course_url": SANDBOX_COURSE_URL,
        "target_course_id": SANDBOX_COURSE_ID,
        "section_name": item.get("section_name", "General"),
        "sectionnum": str(item.get("sectionnum", "0")),
        "target_object": item["resource_name"],
        "object_type": "Moodle File resource",
        "action": "create_file_resource",
        "resource_name": item["resource_name"],
        "target_module_id": "0",
        "target_settings_url": "",
        "create_new_resource": True,
        "target_file_path": upload_path,
        "upload_file_path": upload_path,
        "expected_uploaded_filename": filename,
        "intro": item.get("intro", "<p>Course material.</p>"),
        "visible": visible,
        "expected_visibility": expected_visibility,
        "release_at": release_at,
        "release_timestamp": release_ts,
        "min_pre_release_seconds": int(item.get("min_pre_release_seconds") or 0),
        "availabilityconditionsjson": release_json,
        "approval_attachments": [
            {
                "kind": "approval_attachment",
                "label": "DASE7137 sandbox course material upload",
                "path": upload_path,
            }
        ],
        "browser_execution": {
            "execute_approval_phrase": manifest["approval_phrase"],
            "revert_approval_phrase": "",
            "default_mode": "execute_only",
        },
        "guardrails": [
            "browser automation fallback is allowed only for the migrationtest sandbox course id 105330",
            "create exactly one sandbox Moodle File resource for this approved manifest item",
            "upload only the approved local file named in this operation package",
            "do not change grades, due dates, groups, completion, or notifications",
            "if release_at is present, set visibility to Show on course page and hide until the date restriction is met",
            "do not create duplicate resources if the exact approved title/file is already present",
            "do not use the legacy real DASE7137/IMSE7137 Moodle course",
            "the exact batch approval phrase is required before save",
        ],
        "manifest_item": item,
    }


def set_availability_restriction(page, operation: dict[str, Any]) -> dict[str, Any]:
    availability = operation.get("availabilityconditionsjson")
    if not availability:
        return {"applied": False}
    locator = page.locator("input[name='availabilityconditionsjson'], #id_availabilityconditionsjson").first
    if locator.count() != 1:
        raise RuntimeError("Moodle form did not expose availabilityconditionsjson for release restriction.")
    value = json.dumps(availability, separators=(",", ":"))
    locator.evaluate(
        """
        (el, value) => {
          el.value = value;
          el.dispatchEvent(new Event('input', {bubbles: true}));
          el.dispatchEvent(new Event('change', {bubbles: true}));
        }
        """,
        value,
    )
    return {"applied": True, "availabilityconditionsjson": availability}


def cmid_from_resource_url(url: str) -> str:
    parsed = urlparse(url)
    ids = parse_qs(parsed.query).get("id", [])
    return ids[0] if ids else ""


def read_settings_state(page, course_url: str, display_url: str, item_report_dir: Path) -> dict[str, Any]:
    cmid = cmid_from_resource_url(display_url)
    if not cmid:
        return {"ok": False, "reason": "display_url did not include a cmid id parameter", "display_url": display_url}
    root = f"{urlparse(course_url).scheme}://{urlparse(course_url).netloc}"
    settings_url = f"{root}/course/modedit.php?update={cmid}&return=1"
    page.goto(settings_url, wait_until="domcontentloaded", timeout=60000)
    save_screenshot(page, item_report_dir, "06_verify_settings_after_save.png")
    state = page.evaluate(
        """
        () => {
          const visible = document.querySelector('#id_visible');
          const availability = document.querySelector('[name="availabilityconditionsjson"], #id_availabilityconditionsjson');
          return {
            url: location.href,
            visibility_value: visible ? visible.value : '',
            visibility_label: visible && visible.selectedOptions && visible.selectedOptions.length
              ? visible.selectedOptions[0].textContent.trim()
              : '',
            availabilityconditionsjson: availability ? availability.value : ''
          };
        }
        """
    )
    write_result(item_report_dir, "06_verify_settings_after_save.json", state)
    return state


def write_operations(manifest_path: Path, manifest: dict[str, Any], report_dir: Path) -> list[Path]:
    ops_dir = report_dir / "operations"
    ops_dir.mkdir(parents=True, exist_ok=True)
    op_paths = []
    for index, item in enumerate(manifest.get("upload_items", []), start=1):
        operation = operation_for_item(manifest, item, index)
        path = ops_dir / f"{index:02d}_{safe_name(item['resource_name'])}_operation.json"
        path.write_text(json.dumps(operation, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        op_paths.append(path)
    summary = {
        "manifest_path": str(manifest_path),
        "report_dir": str(report_dir),
        "operation_count": len(op_paths),
        "operation_paths": [str(path) for path in op_paths],
        "approval_phrase": manifest["approval_phrase"],
    }
    (report_dir / "batch_operations_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return op_paths


def find_prior_success(operation: dict[str, Any], current_report_dir: Path) -> dict[str, Any] | None:
    operation_id = operation.get("operation_id", "")
    if not operation_id:
        return None
    report_glob = f"moodle_course_file_batch_upload_*_{safe_name(operation.get('batch_id', ''))}"
    for path in sorted(REPORTS.glob(f"{report_glob}/items/*/result.json"), reverse=True):
        try:
            if current_report_dir in path.parents:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("operation_id") != operation_id:
            continue
        if data.get("approval_run_id") != operation.get("approval_run_id"):
            continue
        if data.get("ok") and data.get("status") in {"PASS", "ALREADY_PRESENT", "SKIPPED_PREVIOUS_SUCCESS"}:
            data["_result_path"] = str(path)
            return data
    return None


def skipped_previous_success_result(
    operation_path: Path,
    operation: dict[str, Any],
    item_report_dir: Path,
    prior: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "ok": True,
        "status": "SKIPPED_PREVIOUS_SUCCESS",
        "mode": "execute_approved_change",
        "operation_path": str(operation_path),
        "report_dir": str(item_report_dir),
        "operation_id": operation.get("operation_id", ""),
        "approval_run_id": operation.get("approval_run_id", ""),
        "course_url": operation.get("target_course_url", ""),
        "resource_name": operation.get("resource_name", ""),
        "expected_uploaded_filename": operation.get("expected_uploaded_filename", ""),
        "saves_count": 0,
        "write_action_taken": False,
        "prior_result_path": prior.get("_result_path", ""),
        "prior_status": prior.get("status", ""),
        "prior_display_url": prior.get("display_url", ""),
        "note": "A prior successful result for this exact operation_id exists; no duplicate write was attempted.",
    }
    item_report_dir.mkdir(parents=True, exist_ok=True)
    write_result(item_report_dir, "result.json", result)
    return result


def transient_unwritten_failure(result: dict[str, Any]) -> bool:
    if result.get("ok") or result.get("write_action_taken") or int(result.get("saves_count") or 0):
        return False
    blocker = str(result.get("blocker", "")).lower()
    transient_markers = (
        "did not appear in moodle filemanager",
        "no files attached",
        "file picker opened, but no usable file input",
        "could not find moodle filemanager add button",
        "could not confirm moodle file picker upload",
        "net::err_aborted",
    )
    return any(marker in blocker for marker in transient_markers)


def execute_one_in_context(
    page,
    timeout_error_type,
    env: dict[str, str],
    operation_path: Path,
    operation: dict[str, Any],
    approval_phrase: str,
    item_report_dir: Path,
) -> dict[str, Any]:
    assert_create_file_resource(operation)
    assert_phrase(operation, approval_phrase)

    upload_path = resolve_local_path(operation.get("target_file_path") or operation.get("upload_file_path"))
    if not upload_path.exists():
        raise FileNotFoundError(f"Upload file does not exist: {upload_path}")
    expected_filename = operation.get("expected_uploaded_filename") or upload_path.name
    if upload_path.name != expected_filename:
        raise ValueError(f"Upload file basename {upload_path.name!r} does not match {expected_filename!r}.")

    item_report_dir.mkdir(parents=True, exist_ok=True)
    auto_click_hku = env.get("MOODLE_AUTO_CLICK_HKU_LOGIN", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    wait_seconds = float(env.get("MOODLE_BROWSER_WAIT_SECONDS", "900"))

    result: dict[str, Any] = {
        "ok": False,
        "status": "STARTED",
        "mode": "execute_approved_change",
        "operation_path": str(operation_path),
        "report_dir": str(item_report_dir),
        "operation_id": operation.get("operation_id", ""),
        "approval_run_id": operation.get("approval_run_id", ""),
        "course_url": operation.get("target_course_url", ""),
        "resource_name": operation.get("resource_name", ""),
        "expected_uploaded_filename": expected_filename,
        "saves_count": 0,
        "write_action_taken": False,
    }

    try:
        course_url = operation["target_course_url"]
        add_url = course_add_resource_url(operation)
        print(f"[{operation.get('operation_id', operation_path.name)}] Opening Moodle course: {course_url}")
        page.goto(course_url, wait_until="domcontentloaded", timeout=60000)
        wait_for_form_or_course(page, wait_seconds, course_url, auto_click_hku=auto_click_hku)
        save_screenshot(page, item_report_dir, "01_course_or_login_after_wait.png")
        write_result(item_report_dir, "01_course_or_login_after_wait.json", page_summary(page))

        if looks_like_login(page):
            raise RuntimeError("Moodle is still on a login page after waiting.")

        existing = find_exact_resource_link(page, operation["resource_name"])
        course_text = visible_text(page, 80000)
        if existing is not None or operation["resource_name"] in course_text:
            result.update(
                {
                    "ok": True,
                    "status": "ALREADY_PRESENT",
                    "write_action_taken": False,
                    "duplicate_detection": "exact_link" if existing is not None else "course_text",
                    "note": "Exact resource title already exists on the course page; no duplicate was created.",
                }
            )
            write_result(item_report_dir, "result.json", result)
            return result

        print(f"[{operation.get('operation_id', operation_path.name)}] Opening add-resource form: {add_url}")
        page.goto(add_url, wait_until="domcontentloaded", timeout=60000)
        wait_for_form_or_course(page, wait_seconds, add_url, auto_click_hku=auto_click_hku)
        if page.locator("#id_name").count() != 1:
            raise RuntimeError("Moodle add-resource form did not expose #id_name.")
        save_screenshot(page, item_report_dir, "02_add_resource_form_before_fill.png")
        write_result(item_report_dir, "02_add_resource_form_before_fill.json", page_summary(page))

        page.locator("#id_name").fill(operation["resource_name"])
        set_intro(page, operation.get("intro", ""))
        select_hidden_visibility(page, operation.get("expected_visibility", "Show on course page"))
        release_details = set_availability_restriction(page, operation)
        upload_details = upload_file_via_filepicker(page, item_report_dir, upload_path, expected_filename)
        save_screenshot(page, item_report_dir, "03_after_fill_and_file_upload_before_save.png")
        write_result(
            item_report_dir,
            "03_after_fill_and_file_upload_before_save.json",
            {"page": page_summary(page), "upload": upload_details, "release": release_details},
        )

        assert_release_window_before_save(operation)
        click_save_and_wait(page, timeout_error_type)
        result["saves_count"] = 1
        result["write_action_taken"] = True
        save_screenshot(page, item_report_dir, "04_after_save_display.png")
        display_summary = page_summary(page)
        write_result(item_report_dir, "04_after_save_display.json", display_summary)
        display_text = visible_text(page, 30000)
        title_signal = operation["resource_name"] in display_text or operation["resource_name"] in display_summary.get("h1", "")
        file_signal = expected_filename in display_text

        page.goto(course_url, wait_until="domcontentloaded", timeout=60000)
        wait_for_form_or_course(page, 30, course_url, auto_click_hku=auto_click_hku)
        save_screenshot(page, item_report_dir, "05_verify_course_after_save.png")
        course_summary = page_summary(page)
        write_result(item_report_dir, "05_verify_course_after_save.json", course_summary)
        course_text = visible_text(page, 30000)
        course_title_signal = operation["resource_name"] in course_text

        if not title_signal and not course_title_signal:
            raise RuntimeError("Saved resource title was not visible on display page or course page.")
        if not file_signal and expected_filename not in course_text:
            raise RuntimeError("Uploaded file signal was not visible after save.")

        settings_state: dict[str, Any] = {}
        if operation.get("availabilityconditionsjson"):
            settings_state = read_settings_state(page, course_url, display_summary.get("url", ""), item_report_dir)
            saved_availability = settings_state.get("availabilityconditionsjson", "")
            expected_release = str(operation.get("release_timestamp") or "")
            if expected_release and expected_release not in saved_availability:
                raise RuntimeError("Saved settings did not contain the approved release timestamp.")
            if settings_state.get("visibility_value") != "1":
                raise RuntimeError("Release-batch item must be visible with a date restriction, not hidden.")
            page.goto(course_url, wait_until="domcontentloaded", timeout=60000)
            wait_for_form_or_course(page, 30, course_url, auto_click_hku=auto_click_hku)

        result.update(
            {
                "ok": True,
                "status": "PASS",
                "display_url": display_summary.get("url", ""),
                "title_visible_after_save": bool(title_signal or course_title_signal),
                "file_visible_after_save": bool(file_signal or expected_filename in course_text),
                "course_verify_url": course_summary.get("url", ""),
                "settings_readback": settings_state,
                "release_at": operation.get("release_at", ""),
                "release_timestamp": operation.get("release_timestamp"),
                "screenshots": [
                    str(item_report_dir / "04_after_save_display.png"),
                    str(item_report_dir / "05_verify_course_after_save.png"),
                ],
            }
        )
        write_result(item_report_dir, "result.json", result)
        return result
    except Exception as exc:
        result.update({"ok": False, "status": "BLOCKED", "blocker": str(exc), "current_url": page.url})
        try:
            save_screenshot(page, item_report_dir, "error_state.png")
            result["error_screenshot"] = str(item_report_dir / "error_state.png")
        except Exception:
            pass
        write_result(item_report_dir, "result.json", result)
        return result


def execute_batch_in_one_context(
    op_paths: list[Path],
    manifest: dict[str, Any],
    report_dir: Path,
    *,
    keep_open: bool = False,
) -> list[dict[str, Any]]:
    env = load_env()
    env.update(os.environ)
    env.setdefault("MOODLE_AUTO_CLICK_HKU_LOGIN", "0")
    env.setdefault("MOODLE_BROWSER_WAIT_SECONDS", "900")
    item_retry_attempts = max(1, int(env.get("MOODLE_UPLOAD_ITEM_RETRY_ATTEMPTS", "3")))

    sync_playwright, PlaywrightTimeoutError = import_playwright()
    item_results: list[dict[str, Any]] = []
    items_dir = report_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
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
        try:
            for index, op_path in enumerate(op_paths, start=1):
                operation = json.loads(op_path.read_text(encoding="utf-8"))
                item_report_dir = items_dir / f"{index:02d}_{safe_name(operation.get('resource_name', op_path.stem))}"
                print(f"\nBatch item {index}/{len(op_paths)}: {operation.get('resource_name', op_path.name)}")
                prior = find_prior_success(operation, report_dir)
                if prior is not None:
                    item_result = skipped_previous_success_result(op_path, operation, item_report_dir, prior)
                    item_results.append(item_result)
                    print(json.dumps(item_result, indent=2, ensure_ascii=False))
                    continue
                item_result = {}
                for attempt in range(1, item_retry_attempts + 1):
                    attempt_report_dir = item_report_dir if attempt == 1 else item_report_dir / f"retry_{attempt}"
                    item_result = execute_one_in_context(
                        page,
                        PlaywrightTimeoutError,
                        env,
                        op_path,
                        operation,
                        manifest["approval_phrase"],
                        attempt_report_dir,
                    )
                    item_result["attempt"] = attempt
                    if item_result.get("ok") or not transient_unwritten_failure(item_result):
                        break
                    print(
                        f"Transient upload failure for {operation.get('resource_name', op_path.name)} "
                        f"on attempt {attempt}/{item_retry_attempts}; retrying in the same browser session."
                    )
                    try:
                        page.goto(operation["target_course_url"], wait_until="domcontentloaded", timeout=60000)
                    except Exception:
                        pass
                if item_result and Path(str(item_result.get("report_dir", ""))).resolve() != item_report_dir.resolve():
                    write_result(item_report_dir, "result.json", item_result)
                item_results.append(item_result)
                print(json.dumps(item_result, indent=2, ensure_ascii=False))
                if not item_result.get("ok"):
                    break
        finally:
            if not keep_open:
                try:
                    context.close()
                except Exception:
                    pass

    return item_results


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate or execute Moodle sandbox upload operations from a manifest.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--execute-approved-change", action="store_true")
    parser.add_argument("--approval-phrase", default="")
    parser.add_argument("--keep-open", action="store_true", help="Leave Chrome open after the batch action.")
    args = parser.parse_args()

    manifest_path, manifest = load_manifest(args.manifest)
    approval_phrase = args.approval_phrase if args.execute_approved_change else None
    assert_manifest(manifest, approval_phrase)

    REPORTS.mkdir(exist_ok=True)
    report_dir = REPORTS / f"moodle_course_file_batch_upload_{now_stamp()}_{safe_name(manifest['batch_id'])}"
    report_dir.mkdir(parents=True, exist_ok=True)
    op_paths = write_operations(manifest_path, manifest, report_dir)

    result: dict[str, Any] = {
        "ok": True,
        "status": "OPERATIONS_WRITTEN",
        "manifest_path": str(manifest_path),
        "report_dir": str(report_dir),
        "operation_count": len(op_paths),
        "approval_phrase": manifest["approval_phrase"],
        "operations": [str(path) for path in op_paths],
    }

    if args.execute_approved_change:
        env = load_env()
        item_results = execute_batch_in_one_context(
            op_paths,
            manifest,
            report_dir,
            keep_open=keep_moodle_browser_open(env, args.keep_open),
        )
        result["ok"] = bool(item_results) and all(item.get("ok") for item in item_results)
        result["status"] = "PASS" if result["ok"] and len(item_results) == len(op_paths) else "BLOCKED"
        result["saves_count"] = sum(int(item.get("saves_count", 0)) for item in item_results)
        result["item_results"] = item_results

    result_path = report_dir / "result.json"
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
