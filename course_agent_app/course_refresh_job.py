from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
SANDBOX_COURSE_ID = "105330"
SANDBOX_COURSE_URL = "https://migrationtest-moodle.hku.hk/course/view.php?id=105330"
OPERATION_KIND = "course_refresh_batch_upload"
SUPPORTED_WORKFLOWS = {"dase7137_stale_cleanup_rebuild", "prepared_upload_manifest"}
FORBIDDEN_JOB_KEYS = {
    "command",
    "shell",
    "python",
    "python_code",
    "javascript",
    "javascript_code",
    "raw_code",
    "script_text",
}


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def is_course_refresh_operation(operation: dict[str, Any]) -> bool:
    return str(operation.get("operation_kind", "")).strip() == OPERATION_KIND


def resolve_local_path(path_text: str) -> Path:
    path = Path(str(path_text or "")).expanduser()
    if path.is_absolute():
        return path
    for base in (PROJECT_ROOT, ROOT, Path.cwd()):
        candidate = base / path
        if candidate.exists():
            return candidate
    return PROJECT_ROOT / path


def repo_rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def load_json(path_text: str) -> dict[str, Any]:
    path = resolve_local_path(path_text)
    return json.loads(path.read_text(encoding="utf-8"))


def validate_refresh_job_shape(operation: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    job = operation.get("refresh_job")
    manifest_path = str(operation.get("prepared_upload_manifest_path") or "").strip()
    if not manifest_path and not isinstance(job, dict):
        errors.append("course_refresh_batch_upload requires refresh_job or prepared_upload_manifest_path")
        return errors
    if isinstance(job, dict):
        forbidden = sorted(FORBIDDEN_JOB_KEYS & set(job))
        if forbidden:
            errors.append("course_refresh_batch_upload.refresh_job contains forbidden executable fields: " + ", ".join(forbidden))
        workflow = str(job.get("workflow") or "prepared_upload_manifest").strip()
        if workflow not in SUPPORTED_WORKFLOWS:
            errors.append(f"course_refresh_batch_upload.refresh_job.workflow is unsupported: {workflow}")
        for key in (
            "backup_path",
            "source_package_path",
            "timeline_path",
            "source_originals_path",
            "confirmed_correspondence_decision_path",
            "date_policy_output_path",
            "activity_policy_output_path",
        ):
            value = str(job.get(key) or "").strip()
            if not value:
                continue
            if value.startswith(("http://", "https://")):
                errors.append(f"course_refresh_batch_upload.refresh_job.{key} must be a local path")
            if ".env" in Path(value).parts:
                errors.append(f"course_refresh_batch_upload.refresh_job.{key} must not point at a secret env file")
        if workflow == "dase7137_stale_cleanup_rebuild":
            backup = str(job.get("backup_path") or job.get("source_package_path") or "").strip()
            if not backup:
                errors.append("course_refresh_batch_upload.refresh_job requires backup_path for dase7137_stale_cleanup_rebuild")
            has_date_policy = bool(job.get("date_policy_output_path") or job.get("run_codex_date_policy"))
            has_activity_policy = bool(job.get("activity_policy_output_path") or job.get("run_codex_activity_policy"))
            if not has_date_policy:
                errors.append("course_refresh_batch_upload.refresh_job requires date_policy_output_path or run_codex_date_policy")
            if not has_activity_policy:
                errors.append("course_refresh_batch_upload.refresh_job requires activity_policy_output_path or run_codex_activity_policy")
    if manifest_path:
        try:
            validate_upload_manifest(load_json(manifest_path))
        except Exception as exc:
            errors.append(f"course_refresh_batch_upload.prepared_upload_manifest_path is invalid: {exc}")
    return errors


def validate_upload_manifest(manifest: dict[str, Any]) -> None:
    target = manifest.get("target") or {}
    if str(target.get("course_id") or "") != SANDBOX_COURSE_ID or str(target.get("course_url") or "") != SANDBOX_COURSE_URL:
        raise ValueError("upload manifest must target sandbox course 105330")
    summary = manifest.get("summary") or {}
    if int(summary.get("missing_upload_files") or 0):
        raise ValueError("upload manifest contains missing upload files")
    items = manifest.get("upload_items")
    if not isinstance(items, list) or not items:
        raise ValueError("upload manifest requires non-empty upload_items")
    for index, item in enumerate(items, start=1):
        upload_path = resolve_local_path(str(item.get("upload_path") or ""))
        if not upload_path.exists():
            raise ValueError(f"upload item {index} file does not exist: {upload_path}")
        status = str(item.get("date_audit_status") or "").strip()
        if status and status != "PASS_NO_STALE_VISIBLE_DATE":
            raise ValueError(f"upload item {index} date_audit_status is not PASS_NO_STALE_VISIBLE_DATE")
        if str(item.get("expected_uploaded_filename") or "").strip() != upload_path.name:
            raise ValueError(f"upload item {index} expected_uploaded_filename must match upload_path basename")


def extract_last_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    best: dict[str, Any] | None = None
    for match in re.finditer(r"\{", text):
        try:
            value, _end = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            best = value
    if best is None:
        raise ValueError("No JSON object found in command output.")
    return best


def prepare_course_refresh_operation(
    operation: dict[str, Any],
    env: dict[str, str],
    run_id: str,
    raw_task: str,
) -> dict[str, Any]:
    materialized = dict(operation)
    job = materialized.get("refresh_job") if isinstance(materialized.get("refresh_job"), dict) else {}
    workflow = str(job.get("workflow") or ("prepared_upload_manifest" if materialized.get("prepared_upload_manifest_path") else "")).strip()
    if workflow == "dase7137_stale_cleanup_rebuild" and not materialized.get("prepared_upload_manifest_path"):
        materialized.update(run_dase7137_prepare(job, env, run_id))
    manifest_path = str(materialized.get("prepared_upload_manifest_path") or "").strip()
    if not manifest_path:
        raise ValueError("course_refresh_batch_upload did not produce prepared_upload_manifest_path")
    manifest = load_json(manifest_path)
    validate_upload_manifest(manifest)
    review_pdf = write_review_pdf(operation=materialized, manifest=manifest, run_id=run_id, raw_task=raw_task)
    upload_count = len(manifest.get("upload_items") or [])
    materialized.update(
        {
            "operation_kind": OPERATION_KIND,
            "execution_mode": "agent_browser_action",
            "action": "course_refresh_batch_upload",
            "target_course_id": SANDBOX_COURSE_ID,
            "target_course_url": SANDBOX_COURSE_URL,
            "prepared_upload_manifest_path": repo_rel(resolve_local_path(manifest_path)),
            "approval_action": materialized.get("approval_action") or "Update course files",
            "approval_item": materialized.get("approval_item") or str(job.get("course_code") or manifest.get("batch_id") or "Course file batch"),
            "approval_scope": materialized.get("approval_scope")
            or {
                "target_count": upload_count,
                "noun": "date-audited file resources",
            },
            "expected_visibility": str((manifest.get("target") or {}).get("visibility") or "Visible unless item says hidden"),
            "approval_attachments": [
                {
                    "kind": "approval_attachment",
                    "label": "Moodle file review PDF",
                    "path": repo_rel(review_pdf),
                }
            ],
            "prepared_upload_count": upload_count,
            "approval_phrase": manifest.get("approval_phrase", ""),
        }
    )
    return materialized


def run_dase7137_prepare(job: dict[str, Any], env: dict[str, str], run_id: str) -> dict[str, Any]:
    backup_path = str(job.get("backup_path") or job.get("source_package_path") or "").strip()
    command = [
        sys.executable,
        str(PROJECT_ROOT / "course_automation_probe" / "run_dase7137_stale_cleanup_rebuild.py"),
        "--backup-path",
        str(resolve_local_path(backup_path)),
        "--run-id",
        run_id,
        "--materialize-refresh",
        "--run-date-audit",
        "--auto-embedded-visual-date-review",
        "--preserve-existing-modules",
        "--no-execute",
    ]
    if job.get("date_policy_output_path"):
        command.extend(["--date-policy-output", str(resolve_local_path(str(job["date_policy_output_path"])))])
    elif job.get("run_codex_date_policy"):
        command.append("--run-codex-date-policy")
    if job.get("activity_policy_output_path"):
        command.extend(["--activity-policy-output", str(resolve_local_path(str(job["activity_policy_output_path"])))])
    elif job.get("run_codex_activity_policy"):
        command.append("--run-codex-activity-policy")
    if job.get("confirmed_correspondence_decision_path"):
        command.extend(
            [
                "--confirmed-correspondence-decision",
                str(resolve_local_path(str(job["confirmed_correspondence_decision_path"]))),
            ]
        )
    proc_env = os.environ.copy()
    proc_env["PYTHONUTF8"] = "1"
    proc_env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=proc_env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    raw_dir = REPORTS / "course_refresh_job"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{run_id}_prepare_output.txt"
    raw_path.write_text(proc.stdout, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"course refresh preparation failed; see {repo_rel(raw_path)}")
    result = extract_last_json_object(proc.stdout)
    steps = result.get("steps") or {}
    upload_manifest_path = str(((steps.get("upload_manifest") or {}).get("path")) or "").strip()
    if not upload_manifest_path:
        raise RuntimeError("course refresh preparation did not report an upload manifest")
    return {
        "prepared_upload_manifest_path": upload_manifest_path,
        "refresh_report_path": result.get("result_path", ""),
        "refresh_prepare_log_path": repo_rel(raw_path),
        "refresh_prepare_status": result.get("status", ""),
    }


def pdf_escape(text: str) -> str:
    safe = text.encode("latin-1", errors="replace").decode("latin-1")
    return safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_simple_pdf(path: Path, lines: list[str]) -> None:
    content_lines = ["BT", "/F1 11 Tf", "50 770 Td"]
    for index, line in enumerate(lines[:42]):
        if index:
            content_lines.append("0 -16 Td")
        content_lines.append(f"({pdf_escape(line)}) Tj")
    content_lines.append("ET")
    content = "\n".join(content_lines).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj_id, obj in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{obj_id} 0 obj\n".encode("ascii"))
        payload.extend(obj)
        payload.extend(b"\nendobj\n")
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(payload))


def write_review_pdf(operation: dict[str, Any], manifest: dict[str, Any], run_id: str, raw_task: str) -> Path:
    review_dir = REPORTS / "course_refresh_job"
    review_dir.mkdir(parents=True, exist_ok=True)
    path = review_dir / f"moodle_file_review_{run_id}.pdf"
    items = manifest.get("upload_items") or []
    lines = [
        "Moodle file review",
        f"Run: {run_id}",
        f"Course: {SANDBOX_COURSE_ID}",
        f"Request: {shorten(raw_task, 92)}",
        f"Prepared files: {len(items)}",
        f"Visibility: {shorten(str((manifest.get('target') or {}).get('visibility') or ''), 92)}",
        "",
        "Files:",
    ]
    for item in items[:28]:
        title = shorten(str(item.get("resource_name") or item.get("title") or "Course file"), 52)
        section = shorten(str(item.get("section_name") or item.get("moodle_destination") or ""), 24)
        filename = shorten(str(item.get("expected_uploaded_filename") or Path(str(item.get("upload_path") or "")).name), 42)
        lines.append(f"- {title} | {section} | {filename}")
    if len(items) > 28:
        lines.append(f"- plus {len(items) - 28} additional files in the manifest")
    lines.extend(
        [
            "",
            "Checks:",
            "- Sandbox course 105330 only.",
            "- Local files exist before upload.",
            "- Date-audit-clean items only when audit evidence is present.",
        ]
    )
    write_simple_pdf(path, lines)
    return path


def shorten(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def execute_course_refresh_batch_upload(
    operation: dict[str, Any],
    env: dict[str, str],
    page,
    timeout_error_type,
) -> dict[str, Any]:
    from run_moodle_course_file_upload_batch import (  # noqa: WPS433
        REPORTS as BATCH_REPORTS,
        execute_one_in_context,
        find_prior_success,
        safe_name,
        skipped_previous_success_result,
        transient_unwritten_failure,
        write_operations,
        write_result,
    )

    manifest_path = resolve_local_path(str(operation.get("prepared_upload_manifest_path") or ""))
    manifest = load_json(str(manifest_path))
    validate_upload_manifest(manifest)
    report_dir = BATCH_REPORTS / f"moodle_course_file_batch_upload_{now_stamp()}_{safe_name(manifest['batch_id'])}"
    report_dir.mkdir(parents=True, exist_ok=True)
    op_paths = write_operations(manifest_path, manifest, report_dir)
    item_results: list[dict[str, Any]] = []
    items_dir = report_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    item_retry_attempts = max(1, int(env.get("MOODLE_UPLOAD_ITEM_RETRY_ATTEMPTS", "3")))
    for index, op_path in enumerate(op_paths, start=1):
        upload_operation = json.loads(op_path.read_text(encoding="utf-8"))
        item_report_dir = items_dir / f"{index:02d}_{safe_name(upload_operation.get('resource_name', op_path.stem))}"
        prior = find_prior_success(upload_operation, report_dir)
        if prior is not None:
            item_results.append(skipped_previous_success_result(op_path, upload_operation, item_report_dir, prior))
            continue
        item_result: dict[str, Any] = {}
        for attempt in range(1, item_retry_attempts + 1):
            attempt_report_dir = item_report_dir if attempt == 1 else item_report_dir / f"retry_{attempt}"
            item_result = execute_one_in_context(
                page,
                timeout_error_type,
                env,
                op_path,
                upload_operation,
                str(manifest["approval_phrase"]),
                attempt_report_dir,
            )
            item_result["attempt"] = attempt
            if item_result.get("ok") or not transient_unwritten_failure(item_result):
                break
            try:
                page.goto(upload_operation["target_course_url"], wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass
        if item_result and Path(str(item_result.get("report_dir", ""))).resolve() != item_report_dir.resolve():
            write_result(item_report_dir, "result.json", item_result)
        item_results.append(item_result)
        if not item_result.get("ok"):
            break
    result = {
        "ok": bool(item_results) and all(item.get("ok") for item in item_results) and len(item_results) == len(op_paths),
        "status": "PASS" if item_results and all(item.get("ok") for item in item_results) and len(item_results) == len(op_paths) else "BLOCKED",
        "operation_id": operation.get("operation_id", ""),
        "operation_kind": OPERATION_KIND,
        "approval_run_id": operation.get("approval_run_id", ""),
        "manifest_path": repo_rel(manifest_path),
        "report_dir": str(report_dir),
        "resource_name": f"{len(item_results)}/{len(op_paths)} course files",
        "saves_count": sum(int(item.get("saves_count", 0) or 0) for item in item_results),
        "item_results": item_results,
        "final_moodle_state": {
            "course_url": SANDBOX_COURSE_URL,
            "uploaded_items": len([item for item in item_results if item.get("ok")]),
            "expected_items": len(op_paths),
            "item_results": item_results,
        },
    }
    (report_dir / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result
