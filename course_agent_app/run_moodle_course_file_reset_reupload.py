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
    SANDBOX_COURSE_ID,
    SANDBOX_HOST,
    click_first_visible,
    import_playwright,
    load_env,
    looks_like_login,
    page_summary,
    save_screenshot,
    visible_text,
    wait_for_form_or_course,
    write_result,
)
from run_moodle_course_file_upload_batch import (
    execute_batch_in_one_context,
    load_manifest,
    safe_name,
    write_operations,
)


ROOT = Path(__file__).resolve().parent
REPORTS = ROOT.parent / "course_files" / "evidence" / "process" / "course_agent_app" / "reports"
SANDBOX_COURSE_URL = f"https://{SANDBOX_HOST}/course/view.php?id={SANDBOX_COURSE_ID}"
DELETE_TITLE_PREFIX = "[2026-2027]"


try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def reset_approval_phrase(manifest: dict[str, Any]) -> str:
    return f"Approved Moodle sandbox reset and reupload {manifest['batch_id']} RUN={manifest['run_id']}"


def assert_reset_manifest(manifest: dict[str, Any], approval_phrase: str) -> str:
    target = manifest.get("target") or {}
    if target.get("course_url") != SANDBOX_COURSE_URL or str(target.get("course_id")) != SANDBOX_COURSE_ID:
        raise ValueError("Refusing non-sandbox reset/reupload manifest.")
    if manifest.get("summary", {}).get("missing_upload_files"):
        raise ValueError("Refusing manifest with missing upload files.")
    expected = reset_approval_phrase(manifest)
    if approval_phrase != expected:
        raise ValueError(
            "Approval phrase mismatch for reset/reupload. "
            f"Expected exactly: {expected}"
        )
    return expected


def cmid_from_resource_href(href: str) -> str:
    parsed = urlparse(href)
    if parsed.hostname and parsed.hostname != SANDBOX_HOST:
        return ""
    if "/mod/resource/view.php" not in parsed.path:
        return ""
    values = parse_qs(parsed.query).get("id") or []
    return values[0] if values and values[0].isdigit() else ""


def collect_course_file_resources(page, *, prefix: str = DELETE_TITLE_PREFIX) -> list[dict[str, str]]:
    records = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('a[href*="/mod/resource/view.php"]')).map((anchor) => ({
          text: (anchor.innerText || anchor.textContent || '').replace(/\\s+/g, ' ').trim(),
          href: anchor.href || anchor.getAttribute('href') || ''
        }))
        """
    )
    seen: set[str] = set()
    resources: list[dict[str, str]] = []
    for record in records:
        cmid = cmid_from_resource_href(record.get("href", ""))
        title = record.get("text", "")
        if not cmid or not title.startswith(prefix):
            continue
        if cmid in seen:
            continue
        seen.add(cmid)
        resources.append({"cmid": cmid, "title": title, "href": record.get("href", "")})
    return resources


def open_course(page, env: dict[str, str], report_dir: Path, label: str) -> None:
    auto_click_hku = env.get("MOODLE_AUTO_CLICK_HKU_LOGIN", "0").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    wait_seconds = float(env.get("MOODLE_BROWSER_WAIT_SECONDS", "900"))
    goto_with_retry(page, SANDBOX_COURSE_URL, wait_until="domcontentloaded", timeout=60000)
    wait_for_form_or_course(page, wait_seconds, SANDBOX_COURSE_URL, auto_click_hku=auto_click_hku)
    save_screenshot(page, report_dir, f"{label}.png")
    write_result(report_dir, f"{label}.json", page_summary(page))
    if looks_like_login(page):
        raise RuntimeError("Moodle is still on a login page after waiting.")


def goto_with_retry(page, url: str, *, wait_until: str = "domcontentloaded", timeout: int = 60000, attempts: int = 3) -> None:
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            page.goto(url, wait_until=wait_until, timeout=timeout)
            return
        except Exception as exc:
            last_exc = exc
            if attempt + 1 < attempts:
                page.wait_for_timeout(1500 * (attempt + 1))
    if last_exc is not None:
        raise last_exc


def confirm_delete_current_page(page) -> str:
    selectors = [
        "input[type='submit'][value='Yes']",
        "button:has-text('Yes')",
        "a:has-text('Yes')",
        "#id_submitbutton",
        "input[type='submit'][value='Continue']",
        "button:has-text('Continue')",
    ]
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() and locator.is_visible(timeout=1000):
                locator.click(timeout=10000, no_wait_after=True)
                return selector
        except Exception:
            continue
    text = visible_text(page, 4000)
    raise RuntimeError(f"Could not find Moodle delete confirmation control. Page text: {text[:1000]}")


def resource_still_present(page, cmid: str, title: str) -> bool:
    records = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('a[href*="/mod/resource/view.php"]')).map((anchor) => ({
          text: (anchor.innerText || anchor.textContent || '').replace(/\\s+/g, ' ').trim(),
          href: anchor.href || anchor.getAttribute('href') || ''
        }))
        """
    )
    for record in records:
        href = record.get("href", "")
        text = record.get("text", "")
        if f"id={cmid}" in href or text == title:
            return True
    return False


def delete_one_resource(page, timeout_error_type, resource: dict[str, str], report_dir: Path) -> dict[str, Any]:
    cmid = resource["cmid"]
    title = resource["title"]
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
        clicked = ""
        try:
            clicked = confirm_delete_current_page(page)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=45000)
            except timeout_error_type:
                page.wait_for_timeout(2000)
        except Exception as click_exc:
            page.wait_for_timeout(2000)
            if SANDBOX_COURSE_ID not in page.url:
                goto_with_retry(page, SANDBOX_COURSE_URL, wait_until="domcontentloaded", timeout=60000)
            if resource_still_present(page, cmid, title):
                raise click_exc
            clicked = "delete_confirm_navigation_detected"
        try:
            save_screenshot(page, item_dir, "02_after_delete.png")
        except Exception as screenshot_exc:
            result["post_delete_screenshot_warning"] = str(screenshot_exc)
        try:
            write_result(item_dir, "02_after_delete.json", page_summary(page))
        except Exception as summary_exc:
            result["post_delete_summary_warning"] = str(summary_exc)
        result.update({"ok": True, "status": "PASS", "clicked": clicked, "after_url": page.url})
    except Exception as exc:
        result.update({"ok": False, "status": "BLOCKED", "blocker": str(exc), "current_url": page.url})
        try:
            save_screenshot(page, item_dir, "error_state.png")
        except Exception:
            pass
    write_result(item_dir, "result.json", result)
    return result


def delete_existing_resources(report_dir: Path) -> dict[str, Any]:
    env = load_env()
    env.update(os.environ)
    env.setdefault("MOODLE_AUTO_CLICK_HKU_LOGIN", "0")
    env.setdefault("MOODLE_BROWSER_WAIT_SECONDS", "900")

    sync_playwright, PlaywrightTimeoutError = import_playwright()
    delete_dir = report_dir / "delete_existing"
    delete_dir.mkdir(parents=True, exist_ok=True)

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
            open_course(page, env, delete_dir, "01_course_before_delete")
            before = collect_course_file_resources(page)
            write_result(delete_dir, "resources_before_delete.json", {"resources": before, "count": len(before)})
            delete_results = []
            for resource in before:
                print(f"Deleting {resource['cmid']}: {resource['title']}")
                delete_results.append(delete_one_resource(page, PlaywrightTimeoutError, resource, delete_dir))
                if not delete_results[-1].get("ok"):
                    break

            open_course(page, env, delete_dir, "99_course_after_delete")
            remaining = collect_course_file_resources(page)
            write_result(delete_dir, "resources_after_delete.json", {"resources": remaining, "count": len(remaining)})
        finally:
            try:
                context.close()
            except Exception:
                pass

    ok = all(item.get("ok") for item in delete_results) and not remaining
    return {
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "delete_scope": f"Moodle File resources in sandbox course {SANDBOX_COURSE_ID} with title prefix {DELETE_TITLE_PREFIX!r}",
        "delete_count": sum(1 for item in delete_results if item.get("ok")),
        "resources_before_delete": before,
        "delete_results": delete_results,
        "remaining_resources": remaining,
        "report_dir": str(delete_dir),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete existing sandbox [2026-2027] File resources, then reupload from a sandbox manifest."
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--execute-approved-change", action="store_true")
    parser.add_argument("--approval-phrase", default="")
    args = parser.parse_args()

    if not args.execute_approved_change:
        raise SystemExit("This reset/reupload runner is execute-only and requires --execute-approved-change.")

    manifest_path, manifest = load_manifest(args.manifest)
    approval_phrase = assert_reset_manifest(manifest, args.approval_phrase)
    manifest = dict(manifest)
    manifest["approval_phrase"] = approval_phrase

    REPORTS.mkdir(exist_ok=True)
    report_dir = REPORTS / f"moodle_course_file_reset_reupload_{stamp()}_{safe_name(manifest['batch_id'])}"
    report_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "ok": False,
        "status": "STARTED",
        "manifest_path": str(manifest_path),
        "report_dir": str(report_dir),
        "approval_phrase": approval_phrase,
        "delete_scope": f"Moodle File resources in sandbox course {SANDBOX_COURSE_ID} with title prefix {DELETE_TITLE_PREFIX!r}",
        "upload_count": len(manifest.get("upload_items", [])),
    }

    delete_result = delete_existing_resources(report_dir)
    result["delete_result"] = delete_result
    if not delete_result.get("ok"):
        result.update({"ok": False, "status": "BLOCKED", "blocker": "Deletion did not fully complete; upload was not attempted."})
        write_result(report_dir, "result.json", result)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        raise SystemExit(1)

    op_paths = write_operations(manifest_path, manifest, report_dir)
    upload_results = execute_batch_in_one_context(op_paths, manifest, report_dir)
    result["upload_results"] = upload_results
    result["operations"] = [str(path) for path in op_paths]
    result["saves_count"] = sum(int(item.get("saves_count", 0)) for item in upload_results)
    result["ok"] = bool(upload_results) and all(item.get("ok") for item in upload_results) and len(upload_results) == len(op_paths)
    result["status"] = "PASS" if result["ok"] else "BLOCKED"
    if not result["ok"]:
        result["blocker"] = "One or more upload items failed after deletion."
    write_result(report_dir, "result.json", result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
