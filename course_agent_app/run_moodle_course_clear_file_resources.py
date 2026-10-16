from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_moodle_course_file_reset_reupload import delete_one_resource, open_course
from run_moodle_course_file_resource_upload import (
    DEFAULT_CHROME_PATH,
    SANDBOX_COURSE_ID,
    SANDBOX_HOST,
    import_playwright,
    load_env,
    write_result,
)
from run_moodle_course_file_upload_batch import safe_name


ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
REPORTS = ROOT.parent / "course_files" / "evidence" / "process" / "course_agent_app" / "reports"
SANDBOX_COURSE_URL = f"https://{SANDBOX_HOST}/course/view.php?id={SANDBOX_COURSE_ID}"
APIM_URL = "https://apidev.hku.hk/dase7137agent/"


try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def load_plan(path_text: str) -> tuple[Path, dict[str, Any]]:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        candidates = [Path.cwd() / path, ROOT / path, WORKSPACE / path]
        path = next((candidate for candidate in candidates if candidate.exists()), WORKSPACE / path)
    return path, json.loads(path.read_text(encoding="utf-8"))


def assert_plan(plan: dict[str, Any], approval_phrase: str) -> str:
    target = plan.get("target") or {}
    if target.get("course_url") != SANDBOX_COURSE_URL or str(target.get("course_id")) != SANDBOX_COURSE_ID:
        raise ValueError("Refusing non-sandbox clear plan.")
    expected = plan.get("approval_phrase", "")
    if approval_phrase != expected:
        raise ValueError(f"Approval phrase mismatch. Expected exactly: {expected}")
    scope = plan.get("clear_scope") or {}
    if not scope.get("delete_file_resources"):
        raise ValueError("Refusing clear plan that does not explicitly delete File resources.")
    if not scope.get("preserve_announcements_forum"):
        raise ValueError("Refusing plan that does not preserve Announcements forum.")
    if scope.get("old_live_moodle_write_allowed"):
        raise ValueError("Refusing plan that allows old/live Moodle writes.")
    return expected


def load_local_env() -> dict[str, str]:
    env = load_env()
    env.update(os.environ)
    env_path = ROOT / ".env"
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env.setdefault(key.strip(), value.strip())
    env.setdefault("MOODLE_AUTO_CLICK_HKU_LOGIN", "0")
    env.setdefault("MOODLE_BROWSER_WAIT_SECONDS", "900")
    return env


def call_apim_contents(env: dict[str, str]) -> list[dict[str, Any]]:
    key = env.get("DASE7137_KEY")
    if not key:
        raise RuntimeError("DASE7137_KEY is missing from environment/course_agent_app/.env.")
    data = urllib.parse.urlencode(
        {"wsfunction": "core_course_get_contents", "courseid": SANDBOX_COURSE_ID}
    ).encode("utf-8")
    request = urllib.request.Request(
        APIM_URL,
        data=data,
        method="POST",
        headers={
            "DASE7137-Agent-Subscription-Key": key,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected APIM payload type: {type(payload).__name__}")
    return payload


def flatten_modules(contents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    modules: list[dict[str, Any]] = []
    for section in contents:
        for module in section.get("modules") or []:
            modules.append(
                {
                    "sectionnum": section.get("section"),
                    "section_name": section.get("name"),
                    "cmid": module.get("id"),
                    "modname": module.get("modname"),
                    "name": module.get("name"),
                    "visible": module.get("visible"),
                    "url": module.get("url"),
                }
            )
    return modules


def verify_targets_match_plan(contents: list[dict[str, Any]], plan: dict[str, Any]) -> dict[str, Any]:
    modules = flatten_modules(contents)
    by_cmid = {str(module["cmid"]): module for module in modules}
    mismatches: list[dict[str, Any]] = []
    for item in plan.get("delete_items", []):
        cmid = str(item.get("cmid", ""))
        live = by_cmid.get(cmid)
        if not live:
            mismatches.append({"cmid": cmid, "expected_name": item.get("name"), "issue": "missing_live_cmid"})
            continue
        if live.get("modname") != "resource" or live.get("name") != item.get("name"):
            mismatches.append({"cmid": cmid, "expected": item, "live": live})
    return {
        "ok": not mismatches,
        "mismatches": mismatches,
        "module_count": len(modules),
        "file_resource_count": sum(1 for module in modules if module.get("modname") == "resource"),
        "non_resource_modules": [module for module in modules if module.get("modname") != "resource"],
    }


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
    parser = argparse.ArgumentParser(description="Clear approved File resources from Moodle sandbox course 105330.")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--execute-approved-change", action="store_true")
    parser.add_argument("--approval-phrase", default="")
    args = parser.parse_args()

    if not args.execute_approved_change:
        raise SystemExit("This runner is execute-only and requires --execute-approved-change.")

    plan_path, plan = load_plan(args.plan)
    approval_phrase = assert_plan(plan, args.approval_phrase)
    env = load_local_env()
    report_dir = REPORTS / f"moodle_course_clear_file_resources_{stamp()}_{safe_name(plan['batch_id'])}"
    report_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "ok": False,
        "status": "STARTED",
        "plan_path": str(plan_path),
        "report_dir": str(report_dir),
        "approval_phrase": approval_phrase,
        "target": plan["target"],
        "delete_results": [],
    }

    playwright = None
    context = None
    try:
        before_contents = call_apim_contents(env)
        preflight = verify_targets_match_plan(before_contents, plan)
        result["preflight"] = preflight
        write_result(report_dir, "01_apim_preflight.json", preflight)
        if not preflight["ok"]:
            raise RuntimeError("APIM preflight target mismatch; refusing delete.")

        playwright, context, page, timeout_error_type = launch_page(env)
        open_course(page, env, report_dir, "02_course_before_delete")

        for item in plan.get("delete_items", []):
            resource = {"cmid": str(item["cmid"]), "title": str(item["name"]), "href": str(item.get("url", ""))}
            print(f"Deleting File resource {resource['cmid']}: {resource['title']}")
            delete_result = delete_one_resource(page, timeout_error_type, resource, report_dir)
            result["delete_results"].append(delete_result)
            write_result(report_dir, "delete_progress.json", result["delete_results"])
            if not delete_result.get("ok"):
                break

        after_contents = call_apim_contents(env)
        after_modules = flatten_modules(after_contents)
        after_file_resources = [module for module in after_modules if module.get("modname") == "resource"]
        after_non_resources = [module for module in after_modules if module.get("modname") != "resource"]
        verify = {
            "ok": not after_file_resources,
            "section_count": len(after_contents),
            "module_count": len(after_modules),
            "file_resource_count": len(after_file_resources),
            "remaining_file_resources": after_file_resources,
            "non_resource_modules": after_non_resources,
        }
        result["apim_verify"] = verify
        write_result(report_dir, "99_apim_verify.json", verify)
        open_course(page, env, report_dir, "99_course_after_delete")

        result["ok"] = (
            len(result["delete_results"]) == len(plan.get("delete_items", []))
            and all(item.get("ok") for item in result["delete_results"])
            and verify["ok"]
        )
        result["status"] = "PASS" if result["ok"] else "BLOCKED"
        if not result["ok"]:
            result["blocker"] = "One or more delete actions failed or APIM verification found remaining File resources."
    except Exception as exc:
        result["ok"] = False
        result["status"] = "BLOCKED"
        result["blocker"] = str(exc)
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

    write_result(report_dir, "result.json", result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
