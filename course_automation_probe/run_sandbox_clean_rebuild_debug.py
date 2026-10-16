#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from file_layout import (
    COURSE_AUTOMATION_REPORT_DIR,
    PRE_REFRESH_ROOT,
    ROOT,
    assert_allowed_output_path,
    assert_allowed_source_path,
    ensure_layout,
    repo_rel,
    resolve_repo_path,
)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from moodle_operations.api_client import call_moodle_api, execute_api_operation


SANDBOX_COURSE_ID = "105330"
SANDBOX_COURSE_URL = "https://migrationtest-moodle.hku.hk/course/view.php?id=105330"
DEFAULT_BATCH_ID = "DASE7137-CLEAN-REBUILD-DEBUG-SANDBOX"
DEFAULT_GROUP_COUNT = 4


class PipelineBlocked(RuntimeError):
    """Raised when a debug pipeline guardrail intentionally blocks execution."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def load_local_env() -> dict[str, str]:
    env = dict(os.environ)
    env_path = ROOT / "course_agent_app" / ".env"
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


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path_text: str | Path) -> Any:
    path = resolve_repo_path(path_text)
    return json.loads(path.read_text(encoding="utf-8"))


def load_contents(env: dict[str, str], fixture: str = "") -> list[dict[str, Any]]:
    if fixture:
        payload = read_json(fixture)
    else:
        payload = call_read_api_with_retry("core_course_get_contents", course_contents_params(), env=env, timeout=60)
    if not isinstance(payload, list):
        raise PipelineBlocked(f"Unexpected Moodle contents payload type: {type(payload).__name__}")
    return payload


def call_read_api_with_retry(
    function_name: str,
    params: dict[str, Any],
    *,
    env: dict[str, str],
    timeout: int = 60,
    attempts: int = 5,
    delay_seconds: float = 3.0,
) -> Any:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return call_moodle_api(function_name, params, env=env, timeout=timeout)
        except Exception as exc:
            last_exc = exc
            if attempt == attempts:
                break
            print(f"Read-only Moodle API {function_name} failed on attempt {attempt}/{attempts}: {exc}; retrying...")
            time.sleep(delay_seconds)
    raise PipelineBlocked(f"Request failed for {function_name}: {last_exc}") from last_exc


def course_contents_params() -> dict[str, str]:
    return {
        "courseid": SANDBOX_COURSE_ID,
        "options[0][name]": "excludecontents",
        "options[0][value]": "1",
    }


def flatten_modules(contents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    modules: list[dict[str, Any]] = []
    for section in contents:
        for module in section.get("modules") or []:
            modules.append(
                {
                    "sectionnum": section.get("section"),
                    "section_id": section.get("id"),
                    "section_name": section.get("name"),
                    "cmid": module.get("id"),
                    "modname": module.get("modname"),
                    "name": module.get("name"),
                    "visible": module.get("visible"),
                    "url": module.get("url"),
                }
            )
    return modules


def flatten_sections(contents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "sectionnum": section.get("section"),
            "section_id": section.get("id"),
            "name": section.get("name"),
            "module_count": len(section.get("modules") or []),
        }
        for section in contents
    ]


def approval_phrase(batch_id: str, run_id: str, action: str) -> str:
    return f"Approved Moodle sandbox {action} {batch_id} RUN={run_id}"


def build_strong_clear_plan(contents: list[dict[str, Any]], *, batch_id: str, run_id: str) -> dict[str, Any]:
    modules = flatten_modules(contents)
    sections = flatten_sections(contents)
    delete_sections = [
        section
        for section in sections
        if str(section.get("sectionnum")) != "0" and section.get("section_id") not in (None, "")
    ]
    plan = {
        "generated_at": utc_now(),
        "batch_id": batch_id,
        "run_id": run_id,
        "approval_phrase": approval_phrase(batch_id, run_id, "strong clear"),
        "target": {
            "course_url": SANDBOX_COURSE_URL,
            "course_id": SANDBOX_COURSE_ID,
            "sandbox_only": True,
        },
        "clear_scope": {
            "delete_all_modules": True,
            "delete_sections_except_general": True,
            "preserve_general_section_only": True,
            "old_live_moodle_write_allowed": False,
        },
        "delete_modules": modules,
        "delete_sections": delete_sections,
        "summary": {
            "module_count": len(modules),
            "file_resource_count": sum(1 for module in modules if module.get("modname") == "resource"),
            "section_count": len(sections),
            "delete_section_count": len(delete_sections),
        },
    }
    validate_clear_plan(plan)
    return plan


def validate_clear_plan(plan: dict[str, Any]) -> None:
    target = plan.get("target") or {}
    if target.get("course_url") != SANDBOX_COURSE_URL or str(target.get("course_id")) != SANDBOX_COURSE_ID:
        raise PipelineBlocked("Refusing non-sandbox clear plan.")
    scope = plan.get("clear_scope") or {}
    required = ("delete_all_modules", "delete_sections_except_general", "preserve_general_section_only")
    missing = [key for key in required if not scope.get(key)]
    if missing:
        raise PipelineBlocked(f"Refusing incomplete strong-clear scope: {', '.join(missing)}")
    if scope.get("old_live_moodle_write_allowed"):
        raise PipelineBlocked("Refusing clear plan that allows old/live Moodle writes.")
    expected = approval_phrase(str(plan.get("batch_id", "")), str(plan.get("run_id", "")), "strong clear")
    if plan.get("approval_phrase") != expected:
        raise PipelineBlocked("Refusing clear plan with a non-canonical approval phrase.")


def verify_cleared_readback(contents: list[dict[str, Any]]) -> dict[str, Any]:
    sections = flatten_sections(contents)
    modules = flatten_modules(contents)
    general_sections = [section for section in sections if str(section.get("sectionnum")) == "0"]
    return {
        "ok": len(general_sections) == 1 and len(sections) == 1 and not modules,
        "section_count": len(sections),
        "module_count": len(modules),
        "file_resource_count": sum(1 for module in modules if module.get("modname") == "resource"),
        "sections": sections,
        "remaining_modules": modules,
    }


def validation_report_passes(path_text: str) -> bool:
    if not path_text:
        return True
    path = resolve_repo_path(path_text)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    status_text = " ".join(
        str(data.get(key, ""))
        for key in ("status", "result", "overall_status", "validation_status")
    ).upper()
    if "FAIL" in status_text or "BLOCK" in status_text or "NEEDS_REVIEW" in status_text:
        return False
    if data.get("ok") is False:
        return False
    return "PASS" in status_text or data.get("ok") is True or data.get("valid") is True


def validate_upload_manifest_for_clean_rebuild(
    manifest: dict[str, Any],
    *,
    upload_hidden: bool = True,
    require_zero_blocked: bool = True,
) -> None:
    target = manifest.get("target") or {}
    if target.get("course_url") != SANDBOX_COURSE_URL or str(target.get("course_id")) != SANDBOX_COURSE_ID:
        raise PipelineBlocked("Refusing non-sandbox upload manifest.")
    summary = manifest.get("summary") or {}
    if int(summary.get("missing_upload_files") or 0):
        raise PipelineBlocked("Refusing upload manifest with missing files.")
    if require_zero_blocked and int(summary.get("blocked_needs_refresh") or 0):
        raise PipelineBlocked("Refusing upload manifest with blocked/date-governance items.")
    if require_zero_blocked and manifest.get("blocked_items"):
        raise PipelineBlocked("Refusing upload manifest that still carries blocked_items.")
    upload_items = manifest.get("upload_items") or []
    if not upload_items:
        raise PipelineBlocked("Refusing upload manifest with zero upload items.")

    failed_validation: list[str] = []
    for item in upload_items:
        assert_allowed_source_path(item.get("source_path", ""))
        assert_allowed_output_path(item.get("upload_path", ""))
        if item.get("preview_path"):
            assert_allowed_output_path(item["preview_path"])
        upload_file = resolve_repo_path(item.get("upload_path", ""))
        if not upload_file.exists():
            raise PipelineBlocked(f"Upload file is missing: {repo_rel(upload_file)}")
        if upload_hidden and item.get("visible") is True:
            raise PipelineBlocked(f"Upload item is not hidden by default: {item.get('resource_name')}")
        validation_report = str(item.get("validation_report", "")).strip()
        if validation_report and not validation_report_passes(validation_report):
            failed_validation.append(validation_report)
    if failed_validation:
        raise PipelineBlocked("Refusing upload manifest with failed validation reports: " + ", ".join(failed_validation))


def build_execution_upload_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    execution_manifest = copy.deepcopy(manifest)
    blocked_items = execution_manifest.get("blocked_items") or []
    summary = dict(execution_manifest.get("summary") or {})
    original_blocked = int(summary.get("blocked_needs_refresh") or len(blocked_items))
    execution_manifest["non_upload_blocked_items"] = blocked_items
    execution_manifest["blocked_items"] = []
    summary["blocked_needs_refresh_original"] = original_blocked
    summary["blocked_needs_refresh"] = 0
    summary["execution_upload_items"] = len(execution_manifest.get("upload_items") or [])
    execution_manifest["summary"] = summary
    execution_manifest.setdefault("guardrails", []).append(
        "Blocked/date-governance source items are omitted from this execution manifest; "
        "they remain recorded in non_upload_blocked_items and must not be uploaded."
    )
    execution_manifest["debug_execution_manifest"] = {
        "created_at": utc_now(),
        "source_manifest_had_blocked_items": bool(original_blocked),
        "source_manifest_blocked_count": original_blocked,
        "scope": "upload only upload_items that passed clean-rebuild validation",
    }
    return execution_manifest


def verify_manifest_readback(
    contents: list[dict[str, Any]],
    manifest: dict[str, Any],
    *,
    expected_visible: int = 0,
) -> dict[str, Any]:
    modules = [module for module in flatten_modules(contents) if module.get("modname") == "resource"]
    by_name: dict[str, list[dict[str, Any]]] = {}
    for module in modules:
        by_name.setdefault(str(module.get("name", "")), []).append(module)

    missing: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    for item in manifest.get("upload_items", []):
        name = str(item.get("resource_name", ""))
        matches = by_name.get(name, [])
        if not matches:
            missing.append({"resource_name": name, "sectionnum": str(item.get("sectionnum", ""))})
            continue
        expected_section = str(item.get("sectionnum", ""))
        expected_vis = expected_visible if item.get("visible") is not True else 1
        ok_match = False
        mismatch_details = []
        for module in matches:
            section_ok = str(module.get("sectionnum")) == expected_section
            visible_ok = int(module.get("visible") or 0) == expected_vis
            ok_match = ok_match or (section_ok and visible_ok)
            if not (section_ok and visible_ok):
                mismatch_details.append(
                    {
                        "cmid": module.get("cmid"),
                        "sectionnum": module.get("sectionnum"),
                        "visible": module.get("visible"),
                        "expected_sectionnum": expected_section,
                        "expected_visible": expected_vis,
                    }
                )
        if not ok_match:
            mismatches.append({"resource_name": name, "matches": mismatch_details})

    return {
        "ok": not missing and not mismatches,
        "resource_module_count": len(modules),
        "expected_count": len(manifest.get("upload_items", [])),
        "missing": missing,
        "mismatches": mismatches,
    }


def run_command(cmd: list[str], *, cwd: Path = ROOT) -> dict[str, Any]:
    completed = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False)
    return {
        "cmd": cmd,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "ok": completed.returncode == 0,
    }


def parse_manifest_path(stdout: str) -> Path:
    for raw in stdout.splitlines():
        text = raw.strip()
        if text.endswith(".json") and Path(text).exists():
            return Path(text).resolve()
        if text.endswith(".json"):
            candidate = resolve_repo_path(text)
            if candidate.exists():
                return candidate
    raise PipelineBlocked("Manifest builder did not print a JSON manifest path.")


def build_upload_manifest(
    batch_id: str,
    run_id: str,
    *,
    decisions: str = "",
    allow_legacy_script_decisions: bool = False,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    cmd = [
        sys.executable,
        str(ROOT / "course_automation_probe" / "build_sandbox_upload_manifest.py"),
        "--batch-id",
        batch_id,
        "--run-id",
        run_id,
    ]
    if decisions:
        cmd.extend(["--decisions", decisions])
    if allow_legacy_script_decisions:
        cmd.append("--allow-legacy-script-decisions")
    result = run_command(cmd)
    if not result["ok"]:
        raise PipelineBlocked("Upload manifest generation failed.")
    manifest_path = parse_manifest_path(result["stdout"])
    manifest = read_json(manifest_path)
    return manifest_path, manifest, result


def build_openclaw_planning_command(args: argparse.Namespace) -> list[str]:
    cmd = [
        sys.executable,
        str(ROOT / "course_automation_probe" / "run_openclaw_continuous_monitor.py"),
        "--once",
        "--no-send",
        "--allow-local-only",
        "--source-root",
        repo_rel(PRE_REFRESH_ROOT),
        "--batch-id",
        f"{args.batch_id}-OPENCLAW-PLANNING",
    ]
    if args.openclaw_output_fixture:
        cmd.extend(["--openclaw-output-fixture", args.openclaw_output_fixture])
    return cmd


def run_openclaw_planning(args: argparse.Namespace) -> dict[str, Any]:
    return run_command(build_openclaw_planning_command(args))


def execution_enabled(args: argparse.Namespace) -> bool:
    return bool(args.debug_auto_approve and args.debug_execute_sandbox_writes and not args.no_execute)


def require_write_gate(args: argparse.Namespace, action: str) -> None:
    if args.no_execute:
        raise PipelineBlocked(f"{action} is disabled by --no-execute.")
    if not args.debug_auto_approve or not args.debug_execute_sandbox_writes:
        raise PipelineBlocked(
            f"{action} requires both --debug-auto-approve and --debug-execute-sandbox-writes."
        )


def execute_strong_clear(args: argparse.Namespace, plan_path: Path, plan: dict[str, Any]) -> dict[str, Any]:
    require_write_gate(args, "Strong clear")
    validate_clear_plan(plan)
    cmd = [
        sys.executable,
        str(ROOT / "course_agent_app" / "run_moodle_course_strong_clear.py"),
        "--plan",
        str(plan_path),
        "--execute-approved-change",
        "--approval-phrase",
        str(plan["approval_phrase"]),
    ]
    return run_command(cmd)


def execute_batch_upload(args: argparse.Namespace, manifest_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    require_write_gate(args, "Batch upload")
    validate_upload_manifest_for_clean_rebuild(manifest, upload_hidden=args.upload_hidden)
    cmd = [
        sys.executable,
        str(ROOT / "course_agent_app" / "run_moodle_course_file_upload_batch.py"),
        "--manifest",
        str(manifest_path),
        "--execute-approved-change",
        "--approval-phrase",
        str(manifest["approval_phrase"]),
    ]
    return run_command(cmd)


def build_random_group_operations(*, run_id: str, count: int = DEFAULT_GROUP_COUNT) -> list[dict[str, Any]]:
    rng = random.Random(run_id)
    suffix = run_id.replace("RUN-", "").replace(":", "").replace("-", "")[-10:]
    operations: list[dict[str, Any]] = []
    for index in range(1, count + 1):
        planned_size = rng.randint(3, 6)
        operations.append(
            {
                "operation_id": f"{run_id}-DEBUG-GROUP-{index:02d}",
                "action": "create_group",
                "target_course_id": SANDBOX_COURSE_ID,
                "group_name": f"DEBUG {suffix} Group {index}",
                "description": f"Debug empty group for sandbox clean-rebuild run {run_id}; planned_size={planned_size}.",
                "descriptionformat": 1,
                "planned_size": planned_size,
                "members": [],
                "sandbox_only": True,
            }
        )
    return operations


def execute_random_groups(args: argparse.Namespace, env: dict[str, str], operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    require_write_gate(args, "Random group creation")
    results = []
    for operation in operations:
        if operation.get("members"):
            raise PipelineBlocked("Debug group operation must not assign real students.")
        results.append(execute_api_operation(operation, env))
    return results


def command_preview(cmd: list[str]) -> str:
    return " ".join(cmd)


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean, rebuild, and reupload Moodle sandbox 105330 from pre_refresh.")
    parser.add_argument("--debug-auto-approve", action="store_true")
    parser.add_argument("--debug-execute-sandbox-writes", action="store_true")
    parser.add_argument("--clear-sandbox", action="store_true")
    parser.add_argument("--rebuild-from-pre-refresh", action="store_true")
    parser.add_argument("--upload-hidden", action="store_true")
    parser.add_argument("--create-random-groups", action="store_true")
    parser.add_argument("--no-execute", action="store_true")
    parser.add_argument("--contents-fixture", default="")
    parser.add_argument("--skip-openclaw-planning", action="store_true")
    parser.add_argument("--openclaw-output-fixture", default="")
    parser.add_argument("--decisions", default="", help="Agent-owned course planning output JSON for rebuild upload.")
    parser.add_argument(
        "--allow-legacy-script-decisions",
        action="store_true",
        help="Allow legacy CSV/local semantic decisions for regression-only rebuild tests.",
    )
    parser.add_argument("--batch-id", default=DEFAULT_BATCH_ID)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--group-count", type=int, default=DEFAULT_GROUP_COUNT)
    args = parser.parse_args()

    ensure_layout()
    run_id = args.run_id or datetime.now(timezone.utc).strftime("RUN-%Y%m%d-%H%M%S")
    run_dir = COURSE_AUTOMATION_REPORT_DIR / f"sandbox_clean_rebuild_debug_{stamp()}_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    env = load_local_env()

    result: dict[str, Any] = {
        "ok": False,
        "status": "STARTED",
        "generated_at": utc_now(),
        "run_id": run_id,
        "batch_id": args.batch_id,
        "report_dir": repo_rel(run_dir),
        "target": {"course_url": SANDBOX_COURSE_URL, "course_id": SANDBOX_COURSE_ID},
        "execution_enabled": execution_enabled(args),
        "steps": {},
    }

    try:
        before_contents = load_contents(env, args.contents_fixture)
        snapshot_path = run_dir / "01_apim_before_contents.json"
        write_json(snapshot_path, before_contents)
        result["steps"]["snapshot"] = {
            "ok": True,
            "path": repo_rel(snapshot_path),
            "module_count": len(flatten_modules(before_contents)),
            "section_count": len(flatten_sections(before_contents)),
        }

        clear_plan = build_strong_clear_plan(before_contents, batch_id=args.batch_id, run_id=run_id)
        clear_plan_path = run_dir / "02_strong_clear_plan.json"
        write_json(clear_plan_path, clear_plan)
        result["steps"]["clear_plan"] = {
            "ok": True,
            "path": repo_rel(clear_plan_path),
            "approval_phrase": clear_plan["approval_phrase"],
            "summary": clear_plan["summary"],
        }

        manifest_path: Path | None = None
        manifest: dict[str, Any] | None = None
        upload_ok = False
        if args.rebuild_from_pre_refresh:
            # Build and validate the future upload package before clearing the sandbox.
            # A strict manifest failure must leave the existing sandbox untouched.
            if args.decisions:
                result["steps"]["openclaw_planning"] = {
                    "ok": True,
                    "status": "NOT_RUN",
                    "reason": "using provided agent decisions JSON",
                    "decisions": args.decisions,
                }
            elif args.openclaw_output_fixture:
                openclaw_result = run_openclaw_planning(args)
                openclaw_result_path = run_dir / "05_openclaw_planning_command.json"
                write_json(openclaw_result_path, openclaw_result)
                if not openclaw_result["ok"]:
                    raise PipelineBlocked("OpenClaw planning fixture failed; refusing rebuild upload.")
                result["steps"]["openclaw_planning"] = {
                    "ok": True,
                    "status": "FIXTURE_ONLY",
                    "command_result": repo_rel(openclaw_result_path),
                }
            elif args.skip_openclaw_planning:
                result["steps"]["openclaw_planning"] = {
                    "ok": False,
                    "status": "SKIPPED",
                    "reason": "--skip-openclaw-planning was set",
                }
            else:
                result["steps"]["openclaw_planning"] = {
                    "ok": False,
                    "status": "NOT_RUN",
                    "reason": "agent decisions are expected to be generated outside this script",
                }
            manifest_path, manifest, manifest_command = build_upload_manifest(
                args.batch_id,
                run_id,
                decisions=args.decisions,
                allow_legacy_script_decisions=args.allow_legacy_script_decisions,
            )
            source_manifest_copy = run_dir / "06_source_upload_manifest_full.json"
            write_json(source_manifest_copy, manifest)
            manifest_command_path = run_dir / "06_manifest_command.json"
            write_json(manifest_command_path, manifest_command)
            execution_manifest = build_execution_upload_manifest(manifest)
            execution_manifest_path = run_dir / "06_execution_upload_manifest.json"
            write_json(execution_manifest_path, execution_manifest)
            validate_upload_manifest_for_clean_rebuild(execution_manifest, upload_hidden=args.upload_hidden)
            manifest = execution_manifest
            manifest_path = execution_manifest_path
            result["steps"]["rebuild_manifest"] = {
                "ok": True,
                "source": "course_files/pre_refresh",
                "source_manifest_path": repo_rel(source_manifest_copy),
                "execution_manifest_path": repo_rel(execution_manifest_path),
                "command_result": repo_rel(manifest_command_path),
                "summary": manifest.get("summary", {}),
            }

        clear_ok = False
        if args.clear_sandbox and execution_enabled(args):
            clear_result = execute_strong_clear(args, clear_plan_path, clear_plan)
            clear_result_path = run_dir / "07_strong_clear_command.json"
            write_json(clear_result_path, clear_result)
            if not clear_result["ok"]:
                raise PipelineBlocked("Strong clear command failed.")
            after_clear_contents = load_contents(env, args.contents_fixture)
            clear_verify = verify_cleared_readback(after_clear_contents)
            write_json(run_dir / "08_apim_after_clear_verify.json", clear_verify)
            if not clear_verify["ok"]:
                raise PipelineBlocked("Strong clear readback verification failed.")
            clear_ok = True
            result["steps"]["clear_execute"] = {
                "ok": True,
                "command_result": repo_rel(clear_result_path),
                "verify": clear_verify,
            }
        elif args.clear_sandbox:
            result["steps"]["clear_execute"] = {
                "ok": False,
                "status": "PLANNED_ONLY",
                "reason": "write gates are not fully enabled",
                "command": command_preview(
                    [
                        sys.executable,
                        str(ROOT / "course_agent_app" / "run_moodle_course_strong_clear.py"),
                        "--plan",
                        str(clear_plan_path),
                        "--execute-approved-change",
                        "--approval-phrase",
                        str(clear_plan["approval_phrase"]),
                    ]
                ),
            }

        if args.rebuild_from_pre_refresh and manifest_path and manifest and execution_enabled(args):
            if not args.clear_sandbox or not clear_ok:
                raise PipelineBlocked("Refusing upload because sandbox clear did not complete and verify in this run.")
            upload_result = execute_batch_upload(args, manifest_path, manifest)
            upload_result_path = run_dir / "09_upload_command.json"
            write_json(upload_result_path, upload_result)
            if not upload_result["ok"]:
                raise PipelineBlocked("Batch upload command failed.")
            after_upload_contents = load_contents(env, args.contents_fixture)
            upload_verify = verify_manifest_readback(after_upload_contents, manifest, expected_visible=0)
            write_json(run_dir / "10_apim_after_upload_verify.json", upload_verify)
            if not upload_verify["ok"]:
                raise PipelineBlocked("Upload readback verification failed.")
            result["steps"]["upload_execute"] = {
                "ok": True,
                "command_result": repo_rel(upload_result_path),
                "verify": upload_verify,
            }
            upload_ok = True
        elif args.rebuild_from_pre_refresh and manifest_path and manifest:
            result["steps"]["upload_execute"] = {
                "ok": False,
                "status": "PLANNED_ONLY",
                "reason": "write gates are not fully enabled",
                "command": command_preview(
                    [
                        sys.executable,
                        str(ROOT / "course_agent_app" / "run_moodle_course_file_upload_batch.py"),
                        "--manifest",
                        str(manifest_path),
                        "--execute-approved-change",
                        "--approval-phrase",
                        str(manifest["approval_phrase"]),
                    ]
                ),
            }

        if args.create_random_groups:
            operations = build_random_group_operations(run_id=run_id, count=args.group_count)
            operations_path = run_dir / "09_random_group_operations.json"
            write_json(operations_path, operations)
            if execution_enabled(args):
                if args.rebuild_from_pre_refresh and not upload_ok:
                    raise PipelineBlocked("Refusing random group creation before rebuild upload completes in this run.")
                group_results = execute_random_groups(args, env, operations)
                group_results_path = run_dir / "10_random_group_results.json"
                write_json(group_results_path, group_results)
                result["steps"]["random_groups"] = {
                    "ok": all(item.get("ok") for item in group_results),
                    "operations": repo_rel(operations_path),
                    "results": repo_rel(group_results_path),
                }
                if not result["steps"]["random_groups"]["ok"]:
                    raise PipelineBlocked("Random group creation failed.")
            else:
                result["steps"]["random_groups"] = {
                    "ok": False,
                    "status": "PLANNED_ONLY",
                    "operations": repo_rel(operations_path),
                }

        planned_only = any(step.get("status") == "PLANNED_ONLY" for step in result["steps"].values())
        result["ok"] = True
        result["status"] = "PLANNED_ONLY" if planned_only else "PASS"
    except Exception as exc:
        result["ok"] = False
        result["status"] = "BLOCKED"
        result["blocker"] = str(exc)
    finally:
        result_path = run_dir / "result.json"
        result["result_path"] = repo_rel(result_path)
        write_json(result_path, result)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if not result["ok"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
