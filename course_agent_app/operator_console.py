from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
SANDBOX_COURSE_ID = "105330"
SANDBOX_COURSE_URL = "https://migrationtest-moodle.hku.hk/course/view.php?id=105330"
REPORT_DIR = ROOT / "reports" / "operator_console"

SECRET_KEY_RE = re.compile(r"(key|token|secret|password|cookie|authorization|base64)", re.I)
BASE64ISH_RE = re.compile(r"^[A-Za-z0-9+/=\s]{800,}$")
DESTRUCTIVE_PATH_RE = re.compile(r"(?:^|[/_-])(clear|delete|strong-clear|module-clear)(?:[/_.-]|$)", re.I)
SAFE_RESULT_STATUSES = {
    "PASS",
    "BLOCKED",
    "OPERATIONS_WRITTEN",
    "PLANNED_ONLY",
    "ALREADY_PRESENT",
    "SKIPPED_PREVIOUS_SUCCESS",
    "STARTED",
    "AWAITING_APPROVAL",
}
APPROVED_FLOW_STATUSES = {
    "approved",
    "course_planning_approved",
    "moodle_skipped",
    "moodle_external_action_verified",
    "moodle_external_action_blocked",
    "moodle_external_action_failed",
}

STAFF_CATEGORIES: dict[str, dict[str, Any]] = {
    "materials": {
        "label": "Materials needing staff review",
        "needles": ["manual_review", "staff_review", "refresh_required", "blocked_file", "material"],
    },
    "urls": {
        "label": "URL replacements",
        "needles": ["url", "externalurl", "sharepoint", "http://", "https://"],
    },
    "binary": {
        "label": "Binary packages",
        "needles": [".zip", ".ccdata", "binary", "package"],
    },
    "activity": {
        "label": "Activity structures",
        "needles": ["assign", "quiz", "scheduler", "choice", "forum", "activity_shell", "choicegroup"],
    },
    "group": {
        "label": "Group scaffold",
        "needles": ["group_signup", "group_scaffold", "grouping", "project grouping"],
    },
}


class ConsoleError(RuntimeError):
    pass


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rel(path: Path, *, workspace: Path = WORKSPACE) -> str:
    try:
        return str(path.resolve().relative_to(workspace.resolve()))
    except ValueError:
        return str(path)


def resolve_workspace_path(path_text: str, *, workspace: Path = WORKSPACE) -> Path:
    path = Path(str(path_text or "")).expanduser()
    if path.is_absolute():
        return path
    candidates = [Path.cwd() / path, ROOT / path, workspace / path]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return workspace / path


def safe_read_json(path: Path) -> dict[str, Any] | list[Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def scrub(value: Any, key: str = "") -> Any:
    if SECRET_KEY_RE.search(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(k): scrub(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(item, key) for item in value[:80]]
    if isinstance(value, str):
        if BASE64ISH_RE.match(value):
            return "[omitted-large-content]"
        if len(value) > 1200:
            return value[:1200] + "...[truncated]"
    return value


def report_roots(*, workspace: Path = WORKSPACE) -> list[Path]:
    roots = [
        workspace / "course_files" / "evidence" / "process" / "course_agent_app" / "reports",
        workspace / "course_files" / "evidence" / "process" / "course_automation_probe" / "reports",
        workspace / "course_agent_app" / "reports",
        workspace / "course_automation_probe" / "reports",
    ]
    return [root for root in roots if root.exists()]


def iter_json_files(*, workspace: Path = WORKSPACE, limit: int = 3000) -> list[Path]:
    files: list[Path] = []
    for root in report_roots(workspace=workspace):
        files.extend(path for path in root.rglob("*.json") if path.is_file())
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return files[:limit]


def read_ledger_records(path: Path, *, limit: int = 400) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(scrub(item))
    return records


def is_sandbox_target(manifest: dict[str, Any]) -> bool:
    target = manifest.get("target") or {}
    return str(target.get("course_id") or "") == SANDBOX_COURSE_ID and target.get("course_url") == SANDBOX_COURSE_URL


def manifest_kind(manifest: dict[str, Any]) -> str:
    if isinstance(manifest.get("upload_items"), list):
        return "file_upload_batch"
    if any(
        isinstance(manifest.get(key), list)
        for key in ("activity_shell_items", "url_activity_items", "page_placeholder_items", "group_signup_scaffold_items")
    ):
        return "activity_shell_batch"
    return "unknown"


def manifest_operation_count(manifest: dict[str, Any]) -> int:
    if manifest_kind(manifest) == "file_upload_batch":
        return len(manifest.get("upload_items") or [])
    return sum(
        len(manifest.get(key) or [])
        for key in ("activity_shell_items", "url_activity_items", "page_placeholder_items", "group_signup_scaffold_items")
    )


def is_destructive_manifest_path(path: Path) -> bool:
    return bool(DESTRUCTIVE_PATH_RE.search(str(path)))


def manifest_safe_to_execute(path: Path, manifest: dict[str, Any]) -> tuple[bool, str]:
    kind = manifest_kind(manifest)
    if kind not in {"file_upload_batch", "activity_shell_batch"}:
        return False, "Unsupported manifest type."
    if is_destructive_manifest_path(path):
        return False, "Destructive clear/delete manifests are not exposed in GUI v1."
    if not is_sandbox_target(manifest):
        return False, "Manifest target is not the approved sandbox course 105330."
    if kind == "file_upload_batch" and manifest.get("summary", {}).get("missing_upload_files"):
        return False, "Upload manifest contains missing upload files."
    if not str(manifest.get("approval_phrase") or "").strip():
        return False, "Manifest has no approval phrase."
    if manifest_operation_count(manifest) <= 0:
        return False, "Manifest has no executable items."
    return True, "Ready after action-time approval."


def latest_result_summaries(*, workspace: Path = WORKSPACE, limit: int = 24) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path in iter_json_files(workspace=workspace):
        if path.name != "result.json":
            continue
        data = safe_read_json(path)
        if not isinstance(data, dict):
            continue
        status = str(data.get("status") or "")
        if status and status not in SAFE_RESULT_STATUSES and not status.startswith("moodle_"):
            continue
        results.append(
            {
                "path": rel(path, workspace=workspace),
                "status": status or "UNKNOWN",
                "ok": bool(data.get("ok")),
                "run_id": data.get("run_id") or data.get("approval_run_id") or "",
                "batch_id": data.get("batch_id") or "",
                "saves_count": data.get("saves_count", ""),
                "blocker": str(data.get("blocker") or data.get("reason") or "")[:400],
                "mtime": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
            }
        )
        if len(results) >= limit:
            break
    return results


def approved_manifest_records(state: dict[str, Any], ledger_records: list[dict[str, Any]]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    overrides = state.get("start_env_overrides") or {}
    phrase = str(overrides.get("OPERATOR_CONSOLE_APPROVAL_PHRASE") or "")
    manifest_path = str(overrides.get("OPERATOR_CONSOLE_MANIFEST_PATH") or "")
    if phrase and state.get("status") in APPROVED_FLOW_STATUSES:
        records.append(
            {
                "approval_phrase": phrase,
                "manifest_path": manifest_path,
                "run_id": str(state.get("run_id") or ""),
                "source": "current_state",
            }
        )
    for item in ledger_records:
        if item.get("event") != "operator_console_approval_recorded":
            continue
        records.append(
            {
                "approval_phrase": str(item.get("approval_phrase") or ""),
                "manifest_path": str(item.get("manifest_path") or ""),
                "run_id": str(item.get("run_id") or ""),
                "source": "ledger",
            }
        )
    return [record for record in records if record["approval_phrase"]]


def paths_match(left: str, right: str, *, workspace: Path = WORKSPACE) -> bool:
    if not left or not right:
        return False
    try:
        return resolve_workspace_path(left, workspace=workspace).resolve() == resolve_workspace_path(right, workspace=workspace).resolve()
    except Exception:
        return left == right


def is_manifest_approved(
    manifest_path: Path,
    manifest: dict[str, Any],
    state: dict[str, Any],
    ledger_records: list[dict[str, Any]],
    *,
    workspace: Path = WORKSPACE,
) -> bool:
    phrase = str(manifest.get("approval_phrase") or "")
    for record in approved_manifest_records(state, ledger_records):
        if record["approval_phrase"] != phrase:
            continue
        recorded_path = record.get("manifest_path") or ""
        if not recorded_path or paths_match(recorded_path, str(manifest_path), workspace=workspace):
            return True
    return False


def manifest_summaries(
    state: dict[str, Any],
    ledger_records: list[dict[str, Any]],
    *,
    workspace: Path = WORKSPACE,
    limit: int = 40,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for path in iter_json_files(workspace=workspace):
        data = safe_read_json(path)
        if not isinstance(data, dict):
            continue
        kind = manifest_kind(data)
        if kind == "unknown":
            continue
        safe, reason = manifest_safe_to_execute(path, data)
        approved = is_manifest_approved(path, data, state, ledger_records, workspace=workspace)
        summaries.append(
            {
                "path": rel(path, workspace=workspace),
                "kind": kind,
                "batch_id": data.get("batch_id") or "",
                "run_id": data.get("run_id") or "",
                "approval_phrase": data.get("approval_phrase") or "",
                "operation_count": manifest_operation_count(data),
                "safe_to_execute": safe,
                "blocked_reason": "" if safe else reason,
                "approved_for_execution": bool(approved),
                "mtime": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
            }
        )
        if len(summaries) >= limit:
            break
    return summaries


def candidate_text(item: Any, key: str = "") -> str:
    if isinstance(item, dict):
        parts = [key]
        for value_key in (
            "title",
            "name",
            "modname",
            "resource_name",
            "reason",
            "decision",
            "externalurl",
            "source_path",
            "upload_path",
            "filename",
        ):
            if item.get(value_key):
                parts.append(str(item.get(value_key)))
        return " ".join(parts)
    return f"{key} {item}"


def category_for_candidate(item: Any, key: str) -> str | None:
    text = candidate_text(item, key).casefold()
    if "group_signup" in key or "group_scaffold" in text or "project grouping" in text:
        return "group"
    if ".zip" in text or ".ccdata" in text or "binary" in text:
        return "binary"
    if "url" in key or "externalurl" in text or "sharepoint" in text:
        return "urls"
    if any(word in text for word in ("assign", "quiz", "scheduler", "choice", "forum", "choicegroup", "activity_shell")):
        return "activity"
    if any(word in text for word in ("manual_review", "staff_review", "refresh_required", "blocked_file", "material")):
        return "materials"
    return None


def summarize_candidate(item: Any, path: Path, key: str, *, workspace: Path = WORKSPACE) -> dict[str, Any]:
    if isinstance(item, dict):
        title = (
            item.get("title")
            or item.get("name")
            or item.get("resource_name")
            or item.get("filename")
            or item.get("source_item_id")
            or "Untitled item"
        )
        reason = item.get("reason") or item.get("decision") or item.get("recommended_action") or item.get("status") or ""
        modname = item.get("modname") or item.get("original_type") or ""
    else:
        title = str(item)[:120]
        reason = ""
        modname = ""
    return {
        "title": str(title)[:180],
        "reason": str(reason)[:220],
        "modname": str(modname)[:80],
        "source_key": key,
        "evidence": rel(path, workspace=workspace),
    }


def collect_staff_decision_candidates(*, workspace: Path = WORKSPACE, limit_per_category: int = 60) -> dict[str, list[dict[str, Any]]]:
    categories: dict[str, list[dict[str, Any]]] = {key: [] for key in STAFF_CATEGORIES}
    interesting_keys = {
        "manual_review_items",
        "blocked_items",
        "openclaw_blocked_file_backfill_items",
        "openclaw_blocked_activity_items",
        "activity_shell_items",
        "url_activity_items",
        "group_signup_scaffold_items",
        "staff_review_required",
        "non_pdf_items",
        "binary_items",
    }
    seen: set[tuple[str, str, str]] = set()
    for path in iter_json_files(workspace=workspace, limit=1200):
        data = safe_read_json(path)
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            if key not in interesting_keys and not any(token in key.lower() for token in ("review", "blocked", "url", "binary")):
                continue
            values = value if isinstance(value, list) else [value]
            for item in values:
                category = category_for_candidate(item, key)
                if not category:
                    continue
                if len(categories[category]) >= limit_per_category:
                    continue
                summary = summarize_candidate(item, path, key, workspace=workspace)
                marker = (category, summary["title"], summary["evidence"])
                if marker in seen:
                    continue
                seen.add(marker)
                categories[category].append(summary)
    return categories


def build_dashboard_state(
    flow_state: dict[str, Any],
    env: dict[str, str],
    *,
    workspace: Path = WORKSPACE,
    ledger_path: Path | None = None,
) -> dict[str, Any]:
    ledger_path = ledger_path or ROOT / "approval_ledger.jsonl"
    ledger = read_ledger_records(ledger_path)
    manifests = manifest_summaries(flow_state, ledger, workspace=workspace)
    candidates = collect_staff_decision_candidates(workspace=workspace)
    return {
        "ok": True,
        "generated_at": now_utc(),
        "target": {"course_id": SANDBOX_COURSE_ID, "course_url": SANDBOX_COURSE_URL},
        "flow": {
            "status": flow_state.get("status", "initialized"),
            "batch_id": flow_state.get("batch_id", ""),
            "run_id": flow_state.get("run_id", ""),
            "approval_code": flow_state.get("approval_code", ""),
            "agent_whatsapp_status": flow_state.get("agent_whatsapp_status", ""),
            "moodle_agent_status": flow_state.get("moodle_agent_status", ""),
            "moodle_result": scrub(flow_state.get("moodle_result") or {}),
        },
        "latest_results": latest_result_summaries(workspace=workspace),
        "manifests": manifests,
        "actions": [item for item in manifests if item["safe_to_execute"]],
        "staff_decisions": {
            key: {
                "label": meta["label"],
                "count": len(candidates.get(key, [])),
                "items": candidates.get(key, [])[:20],
            }
            for key, meta in STAFF_CATEGORIES.items()
        },
        "ledger_tail": ledger[-24:],
        "settings": {
            key: {"configured": bool(str(env.get(key) or "").strip())}
            for key in (
                "DASE7137_KEY",
                "WHATSAPP_ACCESS_TOKEN",
                "WHATSAPP_PHONE_NUMBER_ID",
                "WHATSAPP_TO",
                "MOODLE_BROWSER_PROFILE_DIR",
            )
        },
    }


def write_text_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def package_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def staff_package_message(category: str, label: str, item_count: int, package_path: Path) -> str:
    return f"""Moodle staff decision
Action: Review planning category
Item: {label}
Course: {SANDBOX_COURSE_ID}
Items: {item_count}
Decision: Planning only; no Moodle write
Package: {package_path.name}

Reply:
OK {{approval_code}}
NO {{approval_code}}
"""


def prepare_staff_package(
    category: str,
    *,
    workspace: Path = WORKSPACE,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    env = env or {}
    normalized = str(category or "").strip().lower()
    if normalized not in STAFF_CATEGORIES:
        raise ConsoleError(f"Unknown staff decision category: {category!r}.")
    candidates = collect_staff_decision_candidates(workspace=workspace).get(normalized, [])
    label = STAFF_CATEGORIES[normalized]["label"]
    stamp = package_stamp()
    base = REPORT_DIR / f"staff_decision_{normalized}_{stamp}"
    package_path = base.with_suffix(".md")
    message_path = base.with_suffix(".message.txt")
    metadata_path = base.with_suffix(".json")
    lines = [
        f"# Staff Decision Package: {label}",
        "",
        f"- Generated: {now_utc()}",
        f"- Course: {SANDBOX_COURSE_ID}",
        "- Scope: planning decision only; Moodle writes require separate action-time approval.",
        f"- Candidate count: {len(candidates)}",
        "",
        "| # | Item | Type | Reason | Evidence |",
        "|---:|---|---|---|---|",
    ]
    for index, item in enumerate(candidates, start=1):
        lines.append(
            "| {idx} | {title} | {modname} | {reason} | {evidence} |".format(
                idx=index,
                title=str(item.get("title", "")).replace("|", "\\|"),
                modname=str(item.get("modname", "")).replace("|", "\\|"),
                reason=str(item.get("reason", "")).replace("|", "\\|"),
                evidence=str(item.get("evidence", "")).replace("|", "\\|"),
            )
        )
    if not candidates:
        lines.append("| 1 | No current candidates found | | | |")
    package_text = "\n".join(lines) + "\n"
    message_text = staff_package_message(normalized, label, len(candidates), package_path)
    metadata = {
        "generated_at": now_utc(),
        "kind": "staff_decision",
        "category": normalized,
        "label": label,
        "course_id": SANDBOX_COURSE_ID,
        "package_path": str(package_path),
        "message_path": str(message_path),
        "item_count": len(candidates),
        "env_overrides": {
            "BATCH_ID": f"STAFF-DECISION-{normalized.upper()}",
            "MOODLE_EXECUTION_MODE": "course_planning_approval",
            "APPROVAL_MESSAGE_FILE": str(message_path),
            "PLANNING_PACKAGE_PATH": str(package_path),
            "OPERATOR_CONSOLE_APPROVAL_KIND": "staff_decision",
            "OPERATOR_CONSOLE_STAFF_CATEGORY": normalized,
        },
    }
    write_text_file(package_path, package_text)
    write_text_file(message_path, message_text)
    write_text_file(metadata_path, json.dumps(metadata, indent=2, ensure_ascii=False) + "\n")
    return scrub(metadata)


def action_approval_message(manifest: dict[str, Any], manifest_path: Path, kind: str) -> str:
    return f"""Moodle approval
Action: Execute sandbox manifest
Item: {kind}
Course: {SANDBOX_COURSE_ID}
Operations: {manifest_operation_count(manifest)}
Manifest: {manifest_path.name}
Approval phrase: {manifest.get("approval_phrase", "")}
Effect: Execute only through the existing sandbox route

Reply:
OK {{approval_code}}
NO {{approval_code}}
"""


def prepare_action_approval(
    manifest_path_text: str,
    *,
    workspace: Path = WORKSPACE,
) -> dict[str, Any]:
    manifest_path = resolve_workspace_path(manifest_path_text, workspace=workspace)
    manifest = safe_read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ConsoleError("Manifest path does not contain a JSON object.")
    safe, reason = manifest_safe_to_execute(manifest_path, manifest)
    if not safe:
        raise ConsoleError(reason)
    kind = manifest_kind(manifest)
    stamp = package_stamp()
    base = REPORT_DIR / f"action_approval_{kind}_{stamp}"
    message_path = base.with_suffix(".message.txt")
    metadata_path = base.with_suffix(".json")
    message_text = action_approval_message(manifest, manifest_path, kind)
    metadata = {
        "generated_at": now_utc(),
        "kind": "moodle_action",
        "manifest_kind": kind,
        "course_id": SANDBOX_COURSE_ID,
        "manifest_path": str(manifest_path),
        "message_path": str(message_path),
        "operation_count": manifest_operation_count(manifest),
        "approval_phrase": manifest.get("approval_phrase", ""),
        "env_overrides": {
            "BATCH_ID": str(manifest.get("batch_id") or "OPERATOR-CONSOLE-ACTION"),
            "MOODLE_EXECUTION_MODE": "course_planning_approval",
            "APPROVAL_MESSAGE_FILE": str(message_path),
            "PLANNING_PACKAGE_PATH": str(manifest_path),
            "OPERATOR_CONSOLE_APPROVAL_KIND": "moodle_action",
            "OPERATOR_CONSOLE_MANIFEST_PATH": str(manifest_path),
            "OPERATOR_CONSOLE_APPROVAL_PHRASE": str(manifest.get("approval_phrase") or ""),
        },
    }
    write_text_file(message_path, message_text)
    write_text_file(metadata_path, json.dumps(metadata, indent=2, ensure_ascii=False) + "\n")
    return scrub(metadata)


def validate_manifest_approval(
    manifest_path_text: str,
    flow_state: dict[str, Any],
    *,
    workspace: Path = WORKSPACE,
    ledger_path: Path | None = None,
) -> tuple[Path, dict[str, Any], str]:
    manifest_path = resolve_workspace_path(manifest_path_text, workspace=workspace)
    manifest = safe_read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ConsoleError("Manifest path does not contain a JSON object.")
    safe, reason = manifest_safe_to_execute(manifest_path, manifest)
    if not safe:
        raise ConsoleError(reason)
    ledger = read_ledger_records(ledger_path or ROOT / "approval_ledger.jsonl")
    if not is_manifest_approved(manifest_path, manifest, flow_state, ledger, workspace=workspace):
        raise ConsoleError("Manifest approval phrase is not recorded as approved for this exact action.")
    return manifest_path, manifest, manifest_kind(manifest)


def execute_approved_manifest(
    manifest_path_text: str,
    flow_state: dict[str, Any],
    env: dict[str, str],
    *,
    workspace: Path = WORKSPACE,
    ledger_path: Path | None = None,
) -> dict[str, Any]:
    manifest_path, manifest, kind = validate_manifest_approval(
        manifest_path_text,
        flow_state,
        workspace=workspace,
        ledger_path=ledger_path,
    )
    if kind == "file_upload_batch":
        script = ROOT / "run_moodle_course_file_upload_batch.py"
    elif kind == "activity_shell_batch":
        script = ROOT / "run_moodle_course_activity_shell_batch.py"
    else:
        raise ConsoleError(f"Unsupported executable manifest kind: {kind}.")

    python_candidates = (
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
        Path(sys.executable),
    )
    python = next((candidate for candidate in python_candidates if candidate.is_file()), Path(sys.executable))
    command = [
        str(python),
        str(script),
        "--manifest",
        str(manifest_path),
        "--execute-approved-change",
        "--approval-phrase",
        str(manifest.get("approval_phrase") or ""),
    ]
    runtime_env = dict(os.environ)
    runtime_env.update(env)
    runtime_env["PYTHONUTF8"] = "1"
    runtime_env["PYTHONIOENCODING"] = "utf-8"
    timeout = int(runtime_env.get("OPERATOR_CONSOLE_EXECUTE_TIMEOUT_SECONDS", "7200"))
    completed = subprocess.run(
        command,
        cwd=str(ROOT),
        env=runtime_env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return {
        "ok": completed.returncode == 0,
        "status": "PASS" if completed.returncode == 0 else "BLOCKED",
        "manifest_path": str(manifest_path),
        "manifest_kind": kind,
        "returncode": completed.returncode,
        "stdout_tail": scrub(completed.stdout[-4000:]),
        "stderr_tail": scrub(completed.stderr[-4000:]),
        "completed_at": now_utc(),
    }


def dashboard_html() -> bytes:
    html = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Moodle Course Ops Console</title>
  <style>
    :root { color-scheme: light; --ink: #1d2530; --muted: #667085; --line: #d7dde5; --panel: #f7f9fb; --accent: #0b5fff; --ok: #167a3a; --bad: #b42318; --warn: #a15c07; }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--ink); background: #ffffff; }
    header { padding: 18px 28px; border-bottom: 1px solid var(--line); display: flex; justify-content: space-between; gap: 18px; align-items: center; }
    h1 { font-size: 20px; margin: 0; letter-spacing: 0; }
    h2 { font-size: 16px; margin: 0 0 12px; letter-spacing: 0; }
    main { display: grid; grid-template-columns: 210px 1fr; min-height: calc(100vh - 69px); }
    nav { border-right: 1px solid var(--line); padding: 18px 12px; background: var(--panel); }
    nav button { width: 100%; text-align: left; margin: 0 0 6px; }
    button { border: 1px solid var(--line); background: white; color: var(--ink); border-radius: 6px; padding: 8px 10px; font: inherit; cursor: pointer; }
    button.primary { background: var(--accent); color: white; border-color: var(--accent); }
    button:disabled { color: #98a2b3; background: #f2f4f7; cursor: not-allowed; }
    .content { padding: 22px 28px 34px; }
    .section { display: none; }
    .section.active { display: block; }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(140px, 1fr)); gap: 12px; margin-bottom: 20px; }
    .metric, .card { border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: white; }
    .label { color: var(--muted); font-size: 12px; }
    .value { font-weight: 700; margin-top: 4px; overflow-wrap: anywhere; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { border-bottom: 1px solid var(--line); padding: 9px 8px; text-align: left; vertical-align: top; }
    th { color: var(--muted); font-weight: 700; background: #fafbfc; }
    code { background: #f2f4f7; border-radius: 4px; padding: 2px 4px; }
    .pill { display: inline-block; border-radius: 999px; padding: 2px 8px; font-size: 12px; border: 1px solid var(--line); }
    .ok { color: var(--ok); }
    .bad { color: var(--bad); }
    .warn { color: var(--warn); }
    .toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 14px; }
    .path { color: var(--muted); max-width: 360px; overflow-wrap: anywhere; }
    pre { max-height: 260px; overflow: auto; background: #101828; color: #f2f4f7; padding: 12px; border-radius: 8px; }
    @media (max-width: 900px) { main { grid-template-columns: 1fr; } nav { border-right: 0; border-bottom: 1px solid var(--line); display: flex; flex-wrap: wrap; gap: 6px; } nav button { width: auto; } .grid { grid-template-columns: 1fr 1fr; } }
  </style>
</head>
<body>
  <header>
    <h1>Moodle Course Ops Console</h1>
    <div class="toolbar"><button id="refresh" class="primary">Refresh</button><span id="updated" class="label"></span></div>
  </header>
  <main>
    <nav>
      <button data-tab="overview" class="primary">Overview</button>
      <button data-tab="staff">Staff Decisions</button>
      <button data-tab="actions">Moodle Actions</button>
      <button data-tab="evidence">Evidence</button>
      <button data-tab="settings">Settings</button>
    </nav>
    <div class="content">
      <section id="overview" class="section active"></section>
      <section id="staff" class="section"></section>
      <section id="actions" class="section"></section>
      <section id="evidence" class="section"></section>
      <section id="settings" class="section"></section>
    </div>
  </main>
  <script>
    let state = null;
    const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const row = (cells) => '<tr>' + cells.map(c => `<td>${c}</td>`).join('') + '</tr>';
    const statusClass = (s) => /PASS|approved|verified|OK/i.test(s) ? 'ok' : /BLOCKED|FAIL|rejected|veto/i.test(s) ? 'bad' : 'warn';
    async function api(path, body) {
      const opts = body ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)} : {};
      const res = await fetch(path, opts);
      const data = await res.json();
      if (!res.ok || data.ok === false) throw new Error(data.error || 'Request failed');
      return data;
    }
    function renderOverview() {
      const f = state.flow || {};
      document.getElementById('overview').innerHTML = `
        <div class="grid">
          <div class="metric"><div class="label">Course</div><div class="value">${esc(state.target.course_id)}</div></div>
          <div class="metric"><div class="label">Run</div><div class="value">${esc(f.run_id || 'none')}</div></div>
          <div class="metric"><div class="label">WhatsApp</div><div class="value ${statusClass(f.agent_whatsapp_status)}">${esc(f.agent_whatsapp_status || f.status || 'none')}</div></div>
          <div class="metric"><div class="label">Moodle</div><div class="value ${statusClass(f.moodle_agent_status || f.status)}">${esc(f.moodle_agent_status || f.status || 'none')}</div></div>
        </div>
        <h2>Recent Results</h2>${tableResults(state.latest_results.slice(0, 8))}
      `;
    }
    function tableResults(items) {
      if (!items.length) return '<div class="card">No result files found.</div>';
      return `<table><thead><tr><th>Status</th><th>Run</th><th>Batch</th><th>Saves</th><th>Evidence</th></tr></thead><tbody>${
        items.map(item => row([
          `<span class="pill ${statusClass(item.status)}">${esc(item.status)}</span>`,
          esc(item.run_id),
          esc(item.batch_id),
          esc(item.saves_count),
          `<div class="path">${esc(item.path)}</div>${item.blocker ? `<div class="bad">${esc(item.blocker)}</div>` : ''}`,
        ])).join('')
      }</tbody></table>`;
    }
    async function prepareStaff(category) {
      try {
        const data = await api('/api/dashboard/staff-package', {category});
        alert(`Package written:\n${data.package_path}`);
        load();
      } catch (err) { alert(err.message); }
    }
    async function startStaff(category) {
      try {
        const data = await api('/api/dashboard/start-approval', {category});
        alert(`Approval started. Code: ${data.approval_code || ''}`);
        load();
      } catch (err) { alert(err.message); }
    }
    function renderStaff() {
      const cats = state.staff_decisions || {};
      document.getElementById('staff').innerHTML = Object.entries(cats).map(([key, cat]) => `
        <div class="card" style="margin-bottom:12px">
          <div class="toolbar"><h2 style="margin-right:auto">${esc(cat.label)}</h2><span class="pill">${esc(cat.count)} items</span><button onclick="prepareStaff('${key}')">Prepare</button><button class="primary" onclick="startStaff('${key}')">Start Approval</button></div>
          <table><thead><tr><th>Item</th><th>Type</th><th>Reason</th><th>Evidence</th></tr></thead><tbody>${
            (cat.items || []).slice(0, 8).map(item => row([esc(item.title), esc(item.modname), esc(item.reason), `<div class="path">${esc(item.evidence)}</div>`])).join('') || row(['No current candidates', '', '', ''])
          }</tbody></table>
        </div>
      `).join('');
    }
    async function startAction(path) {
      try {
        const data = await api('/api/dashboard/start-approval', {manifest_path: path});
        alert(`Approval started. Code: ${data.approval_code || ''}`);
        load();
      } catch (err) { alert(err.message); }
    }
    async function executeAction(path) {
      if (!confirm('Execute this approved sandbox action now?')) return;
      try {
        const data = await api('/api/dashboard/execute-approved', {manifest_path: path});
        alert(`Execution ${data.status}: return code ${data.returncode}`);
        load();
      } catch (err) { alert(err.message); }
    }
    function renderActions() {
      const items = state.actions || [];
      document.getElementById('actions').innerHTML = `
        <table><thead><tr><th>Kind</th><th>Ops</th><th>Approved</th><th>Manifest</th><th>Action</th></tr></thead><tbody>${
          items.map(item => row([
            esc(item.kind),
            esc(item.operation_count),
            item.approved_for_execution ? '<span class="pill ok">approved</span>' : '<span class="pill warn">needs approval</span>',
            `<div class="path">${esc(item.path)}</div><div><code>${esc(item.approval_phrase)}</code></div>`,
            `<button onclick="startAction('${esc(item.path)}')">Start Approval</button> <button class="primary" ${item.approved_for_execution ? '' : 'disabled'} onclick="executeAction('${esc(item.path)}')">Execute</button>`,
          ])).join('') || row(['No safe actions found', '', '', '', ''])
        }</tbody></table>`;
    }
    function renderEvidence() {
      document.getElementById('evidence').innerHTML = `<h2>Manifests</h2>
        <table><thead><tr><th>Kind</th><th>Status</th><th>Run</th><th>Path</th></tr></thead><tbody>${
          (state.manifests || []).map(item => row([
            esc(item.kind),
            item.safe_to_execute ? '<span class="pill ok">safe</span>' : `<span class="pill bad">${esc(item.blocked_reason)}</span>`,
            esc(item.run_id),
            `<div class="path">${esc(item.path)}</div>`,
          ])).join('') || row(['No manifests', '', '', ''])
        }</tbody></table><h2 style="margin-top:22px">Results</h2>${tableResults(state.latest_results || [])}`;
    }
    function renderSettings() {
      const rows = Object.entries(state.settings || {}).map(([key, value]) => row([
        esc(key),
        value.configured ? '<span class="pill ok">configured</span>' : '<span class="pill warn">missing</span>',
      ])).join('');
      document.getElementById('settings').innerHTML = `<table><thead><tr><th>Setting</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table><h2 style="margin-top:22px">Ledger Tail</h2><pre>${esc(JSON.stringify(state.ledger_tail || [], null, 2))}</pre>`;
    }
    function render() {
      renderOverview(); renderStaff(); renderActions(); renderEvidence(); renderSettings();
      document.getElementById('updated').textContent = state.generated_at || '';
    }
    async function load() {
      state = await api('/api/dashboard/state');
      render();
    }
    document.querySelectorAll('nav button').forEach(button => {
      button.addEventListener('click', () => {
        document.querySelectorAll('nav button').forEach(b => b.classList.remove('primary'));
        button.classList.add('primary');
        document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
        document.getElementById(button.dataset.tab).classList.add('active');
      });
    });
    document.getElementById('refresh').addEventListener('click', load);
    load().catch(err => document.getElementById('overview').innerHTML = `<div class="card bad">${esc(err.message)}</div>`);
  </script>
</body>
</html>
"""
    return html.encode("utf-8")
