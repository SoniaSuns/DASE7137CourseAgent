from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import stat
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from pypdf import PdfReader, PdfWriter

try:
    from file_refresh_preview import (
        SUPPORTED_SUFFIXES,
        audit_visible_text,
        convert_to_pdf,
        execute_refresh_file_preview_operation,
        repo_rel,
        resolve_local_path,
        safe_name,
    )
except ModuleNotFoundError:  # pragma: no cover - import shape depends on entrypoint cwd
    from course_agent_app.file_refresh_preview import (
        SUPPORTED_SUFFIXES,
        audit_visible_text,
        convert_to_pdf,
        execute_refresh_file_preview_operation,
        repo_rel,
        resolve_local_path,
        safe_name,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPERATION_KIND = "refresh_file_batch_preview"
FINALIZE_OPERATION_KIND = "finalize_file_batch_preview"
EXECUTION_MODE = "local_file_action"

STAGING_ROOT = (
    PROJECT_ROOT
    / "course_files"
    / "evidence"
    / "process"
    / "course_agent_app"
    / "file_refresh_batch_staging"
)
REPORT_ROOT = (
    PROJECT_ROOT
    / "course_files"
    / "evidence"
    / "process"
    / "course_agent_app"
    / "file_refresh_batch_preview"
)
FINAL_PDF_ROOT = PROJECT_ROOT / "course_files" / "upload_ready" / "current_course" / "batch"
LATEST_BATCH_CONTEXT_PATH = (
    PROJECT_ROOT
    / "course_files"
    / "evidence"
    / "process"
    / "course_agent_app"
    / "latest_file_refresh_batch_context.json"
)

DEFAULT_MAX_FILES = 20
DEFAULT_MAX_FILE_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 500 * 1024 * 1024
DEFAULT_MAX_ARCHIVE_DEPTH = 8
FINAL_FILENAME_TRACE_WORDS = (
    "automation",
    "automated",
    "refresh",
    "refreshed",
    "revision",
    "review",
    "codex",
    "agent",
    "翻新",
    "改稿",
    "修改痕迹",
)


class BatchRefreshBlocked(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _positive_int(env: dict[str, str], key: str, default: int) -> int:
    try:
        value = int(str(env.get(key, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(1, value)


def batch_limits(env: dict[str, str]) -> dict[str, int]:
    return {
        "max_files": _positive_int(env, "COURSE_REFRESH_BATCH_MAX_FILES", DEFAULT_MAX_FILES),
        "max_file_bytes": _positive_int(
            env,
            "COURSE_REFRESH_BATCH_MAX_FILE_BYTES",
            DEFAULT_MAX_FILE_BYTES,
        ),
        "max_total_bytes": _positive_int(
            env,
            "COURSE_REFRESH_BATCH_MAX_TOTAL_BYTES",
            DEFAULT_MAX_TOTAL_BYTES,
        ),
        "max_archive_depth": _positive_int(
            env,
            "COURSE_REFRESH_BATCH_MAX_ARCHIVE_DEPTH",
            DEFAULT_MAX_ARCHIVE_DEPTH,
        ),
    }


def _params(operation: dict[str, Any]) -> dict[str, Any]:
    value = operation.get("params")
    return value if isinstance(value, dict) else {}


def batch_file_paths(operation: dict[str, Any]) -> list[str]:
    params = _params(operation)
    raw = params.get("file_paths") or params.get("files") or operation.get("file_paths") or []
    if isinstance(raw, (str, Path)):
        raw = [str(raw)]
    paths: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                text = str(item.get("local_path") or item.get("file_path") or item.get("path") or "").strip()
            else:
                text = str(item or "").strip()
            if text and text not in paths:
                paths.append(text)
    return paths


def is_refresh_file_batch_preview_operation(operation: dict[str, Any]) -> bool:
    return str(operation.get("operation_kind") or "").strip() == OPERATION_KIND


def is_finalize_file_batch_preview_operation(operation: dict[str, Any]) -> bool:
    return str(operation.get("operation_kind") or "").strip() == FINALIZE_OPERATION_KIND


def validate_refresh_file_batch_preview_shape(operation: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    params = _params(operation)
    forbidden = sorted(
        {
            "command",
            "shell",
            "python",
            "python_code",
            "javascript",
            "javascript_code",
            "raw_code",
        }
        & set(params)
    )
    if forbidden:
        errors.append(
            "refresh_file_batch_preview.params contains forbidden executable fields: "
            + ", ".join(forbidden)
        )
    paths = batch_file_paths(operation)
    if not paths:
        errors.append("refresh_file_batch_preview requires params.file_paths")
    for index, path_text in enumerate(paths):
        if path_text.startswith(("http://", "https://")):
            errors.append(f"refresh_file_batch_preview.params.file_paths[{index}] must be a local path")
            continue
        if ".env" in Path(path_text).parts:
            errors.append(
                f"refresh_file_batch_preview.params.file_paths[{index}] must not point at a secret env file"
            )
            continue
        path = resolve_local_path(path_text)
        if not path.exists():
            errors.append(
                f"refresh_file_batch_preview.params.file_paths[{index}] does not exist: {path_text}"
            )
        elif not path.is_file():
            errors.append(
                f"refresh_file_batch_preview.params.file_paths[{index}] must be a regular file"
            )
    return errors


def validate_finalize_file_batch_preview_shape(operation: dict[str, Any]) -> list[str]:
    params = _params(operation)
    errors: list[str] = []
    batch_id = str(params.get("batch_id") or operation.get("batch_id") or "").strip()
    if not batch_id:
        errors.append("finalize_file_batch_preview requires params.batch_id")
    try:
        version = int(params.get("batch_version") or operation.get("batch_version") or 0)
    except (TypeError, ValueError):
        version = 0
    if version < 1:
        errors.append("finalize_file_batch_preview requires a positive params.batch_version")
    include_ids = params.get("include_item_ids")
    if include_ids is not None and (
        not isinstance(include_ids, list)
        or any(not str(item or "").strip() for item in include_ids)
    ):
        errors.append("finalize_file_batch_preview.params.include_item_ids must be a list of item IDs")
    return errors


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        Path(temp_name).replace(path)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def batch_state_path(batch_id: str) -> Path:
    return REPORT_ROOT / safe_name(batch_id, "batch") / "batch_state.json"


def load_batch_state(batch_id: str = "") -> dict[str, Any]:
    candidates: list[Path] = []
    if batch_id:
        candidates.append(batch_state_path(batch_id))
    candidates.append(LATEST_BATCH_CONTEXT_PATH)
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and (
            not batch_id or str(payload.get("batch_id") or "") == batch_id
        ):
            return payload
    return {}


def save_batch_state(state: dict[str, Any]) -> dict[str, Any]:
    state = dict(state)
    state["updated_at"] = now_iso()
    batch_id = str(state.get("batch_id") or "").strip()
    if not batch_id:
        raise ValueError("batch state is missing batch_id")
    state_path = batch_state_path(batch_id)
    state["state_path"] = repo_rel(state_path)
    _atomic_write_json(state_path, state)
    _atomic_write_json(LATEST_BATCH_CONTEXT_PATH, state)
    return state


def planner_safe_active_batch_context(requester: str = "") -> dict[str, Any]:
    state = load_batch_state()
    if not state or str(state.get("status") or "") == "FINALIZED":
        return {}
    expected = str(state.get("whatsapp_requester") or "").strip()
    if expected and (
        not requester or expected.casefold() != requester.strip().casefold()
    ):
        return {}
    items: list[dict[str, Any]] = []
    excerpt_budget = 5000
    for item in state.get("items") or []:
        if not isinstance(item, dict):
            continue
        excerpt = str(item.get("review_source_text_excerpt") or "")[:500]
        if len(excerpt) > excerpt_budget:
            excerpt = excerpt[:excerpt_budget]
        excerpt_budget -= len(excerpt)
        safe_item = {
            key: item.get(key)
            for key in (
                "item_id",
                "input_index",
                "source_file_name",
                "archive_member",
                "item_version",
                "status",
                "preview_delivered_at",
                "verified_refreshed_file_path",
                "verified_upload_ready_path",
                "verified_preview_pdf_path",
            )
            if item.get(key) not in ("", None)
        }
        if excerpt:
            safe_item["review_source_text_excerpt"] = excerpt
        items.append(safe_item)
    return {
        "batch_id": state.get("batch_id"),
        "batch_version": state.get("batch_version"),
        "status": state.get("status"),
        "preview_delivery_complete": bool(state.get("preview_delivery_complete")),
        "whatsapp_requester": state.get("whatsapp_requester"),
        "items": items,
    }


def planner_safe_recent_finalized_batch_context(requester: str = "") -> dict[str, Any]:
    state = load_batch_state()
    if not state or str(state.get("status") or "") != "FINALIZED":
        return {}
    expected = str(state.get("whatsapp_requester") or "").strip()
    if expected and (
        not requester or expected.casefold() != requester.strip().casefold()
    ):
        return {}
    final_path = str(state.get("verified_final_pdf_path") or "").strip()
    if not final_path or not resolve_local_path(final_path).exists():
        return {}
    return {
        "batch_id": state.get("batch_id"),
        "batch_version": state.get("batch_version"),
        "status": "FINALIZED",
        "final_pdf_name": state.get("final_pdf_name"),
        "verified_final_pdf_path": final_path,
        "included_item_ids": list(state.get("included_item_ids") or []),
        "excluded_items": list(state.get("excluded_items") or []),
        "finalized_at": state.get("finalized_at"),
        "source_verified_by": "finalized_file_refresh_batch",
    }


def active_batch_editable_path_keys(requester: str = "") -> set[str]:
    context = planner_safe_active_batch_context(requester)
    keys: set[str] = set()
    for item in context.get("items") or []:
        path_text = str(item.get("verified_refreshed_file_path") or "").strip()
        if path_text:
            keys.add(str(resolve_local_path(path_text).resolve(strict=False)))
    return keys


def active_batch_upload_path_keys(requester: str = "") -> set[str]:
    context = planner_safe_active_batch_context(requester)
    keys: set[str] = set()
    for item in context.get("items") or []:
        for key in ("verified_upload_ready_path", "verified_preview_pdf_path"):
            path_text = str(item.get(key) or "").strip()
            if path_text:
                keys.add(str(resolve_local_path(path_text).resolve(strict=False)))
    state = load_batch_state()
    expected = str(state.get("whatsapp_requester") or "").strip()
    if expected and (
        not requester or expected.casefold() != requester.strip().casefold()
    ):
        return keys
    final_path = str(state.get("verified_final_pdf_path") or "").strip()
    if final_path:
        keys.add(str(resolve_local_path(final_path).resolve(strict=False)))
    return keys


def _archive_member_path(name: str, max_depth: int) -> PurePosixPath:
    normalized = str(name or "").replace("\\", "/")
    if "\x00" in normalized:
        raise BatchRefreshBlocked("ZIP contains a member name with a NUL byte.")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or re.match(r"^[A-Za-z]:/", normalized)
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise BatchRefreshBlocked(f"ZIP contains an unsafe member path: {name}")
    if len(path.parts) > max_depth:
        raise BatchRefreshBlocked(f"ZIP member exceeds the allowed directory depth: {name}")
    return path


def _zip_entry_is_unsafe_type(info: zipfile.ZipInfo) -> bool:
    unix_mode = (info.external_attr >> 16) & 0o177777
    if not unix_mode:
        return False
    file_type = stat.S_IFMT(unix_mode)
    return file_type not in {0, stat.S_IFREG, stat.S_IFDIR}


def _skip_archive_member(path: PurePosixPath) -> bool:
    return (
        path.name in {".DS_Store"}
        or "__MACOSX" in path.parts
        or path.name.startswith("._")
    )


def _extract_zip_sources(
    zip_path: Path,
    target_dir: Path,
    limits: dict[str, int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    if zip_path.stat().st_size > limits["max_file_bytes"]:
        raise BatchRefreshBlocked(f"ZIP exceeds the per-file size limit: {zip_path.name}")
    extracted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    total_bytes = 0
    seen: set[str] = set()
    try:
        with zipfile.ZipFile(zip_path) as archive:
            infos = sorted(
                archive.infolist(),
                key=lambda info: str(info.filename).replace("\\", "/").casefold(),
            )
            for info in infos:
                member = _archive_member_path(info.filename, limits["max_archive_depth"])
                key = member.as_posix().casefold()
                if key in seen:
                    raise BatchRefreshBlocked(
                        f"ZIP contains duplicate normalized member paths: {member.as_posix()}"
                    )
                seen.add(key)
                if info.flag_bits & 0x1:
                    raise BatchRefreshBlocked(f"Encrypted ZIP members are unsupported: {member.as_posix()}")
                if _zip_entry_is_unsafe_type(info):
                    raise BatchRefreshBlocked(f"ZIP contains a link or special file: {member.as_posix()}")
                if info.is_dir():
                    continue
                if _skip_archive_member(member):
                    skipped.append({"archive_member": member.as_posix(), "reason": "metadata file"})
                    continue
                suffix = Path(member.name).suffix.lower()
                if suffix == ".zip":
                    raise BatchRefreshBlocked(f"Nested ZIP archives are unsupported: {member.as_posix()}")
                if suffix not in SUPPORTED_SUFFIXES:
                    skipped.append(
                        {
                            "archive_member": member.as_posix(),
                            "reason": f"unsupported file type: {suffix or '(none)'}",
                        }
                    )
                    continue
                if info.file_size > limits["max_file_bytes"]:
                    raise BatchRefreshBlocked(
                        f"ZIP member exceeds the per-file size limit: {member.as_posix()}"
                    )
                total_bytes += int(info.file_size)
                if total_bytes > limits["max_total_bytes"]:
                    raise BatchRefreshBlocked("ZIP expanded size exceeds the batch limit.")
                target = target_dir.joinpath(*member.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
                extracted.append(
                    {
                        "path": target,
                        "source_file_name": target.name,
                        "archive_path": zip_path,
                        "archive_member": member.as_posix(),
                    }
                )
                if len(extracted) > limits["max_files"]:
                    raise BatchRefreshBlocked(
                        f"ZIP contains more than {limits['max_files']} refreshable files."
                    )
    except zipfile.BadZipFile as exc:
        raise BatchRefreshBlocked(f"Invalid ZIP attachment: {zip_path.name}") from exc
    return extracted, skipped, total_bytes


def normalize_batch_sources(
    operation: dict[str, Any],
    env: dict[str, str],
    batch_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    limits = batch_limits(env)
    paths = [resolve_local_path(path) for path in batch_file_paths(operation)]
    staging_dir = STAGING_ROOT / safe_name(batch_id, "batch")
    staging_dir.mkdir(parents=True, exist_ok=True)
    normalized: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    total_bytes = 0
    seen_source_digests: dict[str, Path] = {}
    for source_index, source_path in enumerate(paths, start=1):
        if not source_path.exists() or not source_path.is_file():
            raise BatchRefreshBlocked(f"Batch source is not an existing regular file: {source_path}")
        size = source_path.stat().st_size
        if size > limits["max_file_bytes"]:
            raise BatchRefreshBlocked(f"Source exceeds the per-file size limit: {source_path.name}")
        source_digest = file_sha256(source_path)
        duplicate_of = seen_source_digests.get(source_digest)
        if duplicate_of is not None:
            skipped.append(
                {
                    "source_file_name": source_path.name,
                    "top_level_index": source_index,
                    "reason": "duplicate attachment content",
                    "skip_category": "duplicate_source",
                    "duplicate_of": duplicate_of.name,
                    "counts_as_skipped": False,
                }
            )
            continue
        seen_source_digests[source_digest] = source_path
        if source_path.suffix.lower() == ".zip":
            extracted, archive_skipped, archive_bytes = _extract_zip_sources(
                source_path,
                staging_dir / f"source-{source_index:02d}",
                limits,
            )
            for item in extracted:
                item["top_level_index"] = source_index
                normalized.append(item)
            skipped.extend(
                {
                    **item,
                    "source_file_name": source_path.name,
                    "top_level_index": source_index,
                }
                for item in archive_skipped
            )
            total_bytes += archive_bytes
            if total_bytes > limits["max_total_bytes"]:
                raise BatchRefreshBlocked("Batch source size exceeds the total expanded-size limit.")
            if not extracted:
                skipped.append(
                    {
                        "source_file_name": source_path.name,
                        "top_level_index": source_index,
                        "reason": "ZIP contained no supported refresh files",
                    }
                )
            continue
        suffix = source_path.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            skipped.append(
                {
                    "source_file_name": source_path.name,
                    "top_level_index": source_index,
                    "reason": f"unsupported file type: {suffix or '(none)'}",
                }
            )
            continue
        total_bytes += size
        if total_bytes > limits["max_total_bytes"]:
            raise BatchRefreshBlocked("Batch source size exceeds the total expanded-size limit.")
        normalized.append(
            {
                "path": source_path,
                "source_file_name": source_path.name,
                "archive_path": None,
                "archive_member": "",
                "top_level_index": source_index,
            }
        )
    if len(normalized) > limits["max_files"]:
        raise BatchRefreshBlocked(
            f"Batch contains {len(normalized)} refreshable files; limit is {limits['max_files']}."
        )
    if not normalized:
        raise BatchRefreshBlocked("The batch contains no supported files to refresh.")
    for input_index, item in enumerate(normalized, start=1):
        item["input_index"] = input_index
    return normalized, skipped


def _review_excerpt(path_text: str) -> str:
    path = resolve_local_path(path_text)
    if not path.exists():
        return ""
    try:
        from file_refresh_preview import visible_text
    except ModuleNotFoundError:  # pragma: no cover
        from course_agent_app.file_refresh_preview import visible_text
    return re.sub(r"\s+", " ", visible_text(path)).strip()[:500]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_batch_item_preview(item: dict[str, Any]) -> Path:
    path_text = str(
        item.get("verified_preview_pdf_path")
        or item.get("preview_pdf_path")
        or ""
    ).strip()
    path = resolve_local_path(path_text) if path_text else Path()
    if not path_text or not path.exists() or not path.is_file():
        raise BatchRefreshBlocked(
            f"Review PDF is unavailable for batch item {item.get('item_id') or '(unknown)'}."
        )
    expected = str(item.get("preview_pdf_sha256") or "").strip()
    if not expected or file_sha256(path) != expected:
        raise BatchRefreshBlocked(
            f"Review PDF integrity check failed for batch item {item.get('item_id') or '(unknown)'}."
        )
    return path


def _result_item(
    source: dict[str, Any],
    result: dict[str, Any],
    *,
    item_id: str,
    item_version: int,
) -> dict[str, Any]:
    refreshed_path = str(result.get("refreshed_file_path") or "").strip()
    upload_path = str(result.get("upload_ready_path") or "").strip()
    preview_path = str(result.get("preview_pdf_path") or "").strip()
    resolved_preview = resolve_local_path(preview_path) if preview_path else None
    return {
        "item_id": item_id,
        "input_index": int(source["input_index"]),
        "top_level_index": int(source.get("top_level_index") or source["input_index"]),
        "source_file_name": str(source.get("source_file_name") or ""),
        "archive_member": str(source.get("archive_member") or ""),
        "item_version": item_version,
        "status": str(result.get("status") or "BLOCKED"),
        "ok": bool(result.get("ok")),
        "replacement_count": int(result.get("replacement_count") or 0),
        "audit": result.get("audit") if isinstance(result.get("audit"), dict) else {},
        "note": str(result.get("note") or ""),
        "blocker": str(result.get("blocker") or ""),
        "user_reason": str(result.get("user_reason") or ""),
        "refreshed_file_path": refreshed_path,
        "upload_ready_path": upload_path,
        "preview_pdf_path": preview_path,
        "verified_refreshed_file_path": (
            str(resolve_local_path(refreshed_path).resolve())
            if refreshed_path and resolve_local_path(refreshed_path).exists()
            else ""
        ),
        "verified_upload_ready_path": (
            str(resolve_local_path(upload_path).resolve())
            if upload_path and resolve_local_path(upload_path).exists()
            else ""
        ),
        "verified_preview_pdf_path": (
            str(resolved_preview.resolve())
            if resolved_preview is not None and resolved_preview.exists()
            else ""
        ),
        "preview_pdf_sha256": (
            file_sha256(resolved_preview)
            if resolved_preview is not None and resolved_preview.exists()
            else ""
        ),
        "review_source_text_excerpt": _review_excerpt(refreshed_path) if refreshed_path else "",
        "preview_delivered_at": "",
        "result_path": str(result.get("result_path") or ""),
    }


def _ensure_result_preview_pdf(
    result: dict[str, Any],
    env: dict[str, str],
) -> dict[str, Any]:
    if not result.get("ok"):
        return result
    normalized = dict(result)
    for key in ("preview_pdf_path", "upload_ready_path", "refreshed_file_path"):
        path_text = str(normalized.get(key) or "").strip()
        if not path_text:
            continue
        path = resolve_local_path(path_text)
        if path.exists() and path.suffix.lower() == ".pdf":
            normalized["preview_pdf_path"] = repo_rel(path)
            normalized["attachments"] = [repo_rel(path)]
            return normalized
    source_text = str(
        normalized.get("refreshed_file_path")
        or normalized.get("upload_ready_path")
        or ""
    ).strip()
    if source_text:
        source = resolve_local_path(source_text)
        if source.exists():
            conversion = convert_to_pdf(source, source.parent / "pdf", env)
            pdf_text = str(conversion.get("pdf_path") or "").strip()
            pdf_path = resolve_local_path(pdf_text) if pdf_text else None
            if conversion.get("ok") and pdf_path is not None and pdf_path.exists():
                normalized["preview_pdf_path"] = repo_rel(pdf_path)
                normalized["attachments"] = [repo_rel(pdf_path)]
                return normalized
    normalized.update(
        {
            "ok": False,
            "status": "BLOCKED",
            "blocker": "The refreshed file could not be rendered as a PDF review copy.",
            "user_reason": "I prepared the file but could not render the PDF needed for WhatsApp review.",
            "preview_pdf_path": "",
            "attachments": [],
        }
    )
    return normalized


def _item_by_editable_path(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in state.get("items") or []:
        if not isinstance(item, dict):
            continue
        path_text = str(item.get("verified_refreshed_file_path") or item.get("refreshed_file_path") or "").strip()
        if path_text:
            result[str(resolve_local_path(path_text).resolve(strict=False))] = item
    return result


def _batch_counts(items: list[dict[str, Any]], skipped: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "ready_count": sum(item.get("status") == "READY_FOR_REVIEW" for item in items),
        "needs_review_count": sum(item.get("status") == "NEEDS_REVIEW" for item in items),
        "failed_count": sum(
            item.get("status") not in {"READY_FOR_REVIEW", "NEEDS_REVIEW"}
            for item in items
        ),
        "skipped_count": sum(item.get("counts_as_skipped", True) for item in skipped),
        "deduplicated_count": sum(
            item.get("skip_category") == "duplicate_source" for item in skipped
        ),
    }


def _batch_status(counts: dict[str, int]) -> str:
    successful = counts["ready_count"] + counts["needs_review_count"]
    if successful == 0:
        return "BLOCKED"
    if counts["failed_count"] or counts["skipped_count"] or counts["needs_review_count"]:
        return "PARTIAL_REVIEW_READY"
    return "READY_FOR_REVIEW"


def execute_refresh_file_batch_preview_operation(
    operation: dict[str, Any],
    env: dict[str, str],
    run_id: str,
    raw_task: str,
    *,
    source_authority: str = "whatsapp",
) -> dict[str, Any]:
    errors = validate_refresh_file_batch_preview_shape(operation)
    if errors:
        return {
            "ok": False,
            "status": "BLOCKED",
            "operation_kind": OPERATION_KIND,
            "execution_mode": EXECUTION_MODE,
            "run_id": run_id,
            "blocker": "; ".join(errors),
            "attachments": [],
        }
    params = _params(operation)
    requested_batch_id = str(params.get("batch_id") or operation.get("batch_id") or "").strip()
    existing = load_batch_state(requested_batch_id) if requested_batch_id else {}
    if requested_batch_id:
        if not existing:
            return {
                "ok": False,
                "status": "BLOCKED",
                "operation_kind": OPERATION_KIND,
                "run_id": run_id,
                "blocker": "The requested file-refresh batch is not active.",
                "attachments": [],
            }
        requested_version = int(params.get("batch_version") or operation.get("batch_version") or 0)
        if requested_version != int(existing.get("batch_version") or 0):
            return {
                "ok": False,
                "status": "BLOCKED",
                "operation_kind": OPERATION_KIND,
                "run_id": run_id,
                "blocker": "The requested file-refresh batch version is stale.",
                "attachments": [],
            }
        batch_id = requested_batch_id
        batch_version = requested_version + 1
    else:
        batch_id = f"BATCH-{safe_name(run_id, now_stamp())}"
        batch_version = 1
    try:
        sources, skipped = normalize_batch_sources(operation, env, batch_id)
    except BatchRefreshBlocked as exc:
        return {
            "ok": False,
            "status": "BLOCKED",
            "operation_kind": OPERATION_KIND,
            "execution_mode": EXECUTION_MODE,
            "run_id": run_id,
            "batch_id": batch_id,
            "batch_version": batch_version,
            "blocker": str(exc),
            "attachments": [],
        }

    existing_by_path = _item_by_editable_path(existing) if existing else {}
    prior_items = [
        dict(item)
        for item in existing.get("items") or []
        if isinstance(item, dict)
    ]
    updated_ids: set[str] = set()
    processed_items: list[dict[str, Any]] = []
    for source in sources:
        source_path = Path(source["path"]).resolve(strict=False)
        previous = existing_by_path.get(str(source_path))
        if existing and previous is None:
            return {
                "ok": False,
                "status": "BLOCKED",
                "operation_kind": OPERATION_KIND,
                "run_id": run_id,
                "batch_id": batch_id,
                "batch_version": int(existing.get("batch_version") or 0),
                "blocker": "A batch revision may use only editable files from the active batch.",
                "attachments": [],
            }
        item_id = (
            str(previous.get("item_id"))
            if previous
            else f"F{int(source['input_index']):03d}"
        )
        item_version = int(previous.get("item_version") or 0) + 1 if previous else 1
        if previous:
            source["input_index"] = int(previous.get("input_index") or source["input_index"])
            source["top_level_index"] = int(
                previous.get("top_level_index") or source["top_level_index"]
            )
            source["source_file_name"] = str(
                previous.get("source_file_name") or source["source_file_name"]
            )
            source["archive_member"] = str(
                previous.get("archive_member") or source.get("archive_member") or ""
            )
        child_params = {
            key: value
            for key, value in params.items()
            if key
            not in {
                "file_paths",
                "files",
                "batch_id",
                "batch_version",
                "include_item_ids",
                "final_pdf_name",
            }
        }
        child_params["file_path"] = str(source_path)
        child_operation = {
            "operation_kind": "refresh_file_preview",
            "execution_mode": EXECUTION_MODE,
            "params": child_params,
        }
        child_run_id = f"{safe_name(run_id)}-{item_id}-V{item_version}"
        result = execute_refresh_file_preview_operation(
            child_operation,
            env,
            child_run_id,
            raw_task,
        )
        result = _ensure_result_preview_pdf(result, env)
        processed_items.append(
            _result_item(
                source,
                result,
                item_id=item_id,
                item_version=item_version,
            )
        )
        updated_ids.add(item_id)

    if existing:
        items = [
            item
            for item in prior_items
            if str(item.get("item_id") or "") not in updated_ids
        ] + processed_items
        items.sort(key=lambda item: int(item.get("input_index") or 0))
        skipped = list(existing.get("skipped") or [])
    else:
        items = processed_items
    counts = _batch_counts(items, skipped)
    status = _batch_status(counts)
    attachments = [
        str(item.get("preview_pdf_path"))
        for item in items
        if str(item.get("preview_pdf_path") or "").strip()
    ]
    report_dir = REPORT_ROOT / safe_name(batch_id, "batch")
    report_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "ok": bool(counts["ready_count"] or counts["needs_review_count"]),
        "status": status,
        "operation_kind": OPERATION_KIND,
        "execution_mode": EXECUTION_MODE,
        "run_id": run_id,
        "batch_id": batch_id,
        "batch_version": batch_version,
        "source_authority": source_authority,
        "course_code": str(
            params.get("course_code")
            or env.get("COURSE_AGENT_DEFAULT_COURSE_CODE")
            or ""
        ),
        "raw_task": raw_task,
        "whatsapp_requester": str(env.get("_WHATSAPP_REQUESTER") or ""),
        "whatsapp_root_message_id": str(env.get("_WHATSAPP_ROOT_MESSAGE_ID") or ""),
        "items": items,
        "skipped": skipped,
        **counts,
        "preview_delivery_complete": source_authority == "operator_cli" and bool(attachments),
        "attachments": attachments,
        "report_dir": repo_rel(report_dir),
        "note": "Prepared per-file PDF review copies. No Moodle change was made.",
    }
    if source_authority == "operator_cli" and state["ok"] and attachments:
        state["status"] = "AWAITING_CONFIRMATION"
        delivered_at = now_iso()
        for item in state["items"]:
            if item.get("preview_pdf_path"):
                item["preview_delivered_at"] = delivered_at
    state = save_batch_state(state)
    result_path = report_dir / f"batch_result_v{batch_version}.json"
    state["result_path"] = repo_rel(result_path)
    _atomic_write_json(result_path, state)
    state = save_batch_state(state)
    return state


def mark_batch_item_delivered(
    batch_id: str,
    batch_version: int,
    item_id: str,
) -> dict[str, Any]:
    state = load_batch_state(batch_id)
    if not state or int(state.get("batch_version") or 0) != int(batch_version):
        raise BatchRefreshBlocked("Cannot record delivery for a stale file-refresh batch.")
    found = False
    for item in state.get("items") or []:
        if str(item.get("item_id") or "") == str(item_id):
            verify_batch_item_preview(item)
            item["preview_delivered_at"] = now_iso()
            item.pop("preview_delivery_error", None)
            item.pop("preview_delivery_failed_at", None)
            found = True
            break
    if not found:
        raise BatchRefreshBlocked(f"Unknown file-refresh batch item: {item_id}")
    deliverable = [
        item
        for item in state.get("items") or []
        if str(item.get("preview_pdf_path") or "").strip()
    ]
    state["preview_delivery_complete"] = bool(deliverable) and all(
        str(item.get("preview_delivered_at") or "").strip() for item in deliverable
    )
    if state["preview_delivery_complete"]:
        state["status"] = "AWAITING_CONFIRMATION"
    return save_batch_state(state)


def mark_batch_item_delivery_failed(
    batch_id: str,
    batch_version: int,
    item_id: str,
    reason: str,
) -> dict[str, Any]:
    state = load_batch_state(batch_id)
    if not state or int(state.get("batch_version") or 0) != int(batch_version):
        raise BatchRefreshBlocked("Cannot record delivery failure for a stale file-refresh batch.")
    found = False
    for item in state.get("items") or []:
        if str(item.get("item_id") or "") != str(item_id):
            continue
        item["preview_delivery_error"] = str(reason or "WhatsApp delivery failed.")
        item["preview_delivery_failed_at"] = now_iso()
        item["preview_delivered_at"] = ""
        found = True
        break
    if not found:
        raise BatchRefreshBlocked(f"Unknown file-refresh batch item: {item_id}")
    state["preview_delivery_complete"] = False
    state["status"] = "PARTIAL_REVIEW_READY"
    return save_batch_state(state)


def mark_final_batch_pdf_delivered(
    batch_id: str,
    batch_version: int,
) -> dict[str, Any]:
    state = load_batch_state(batch_id)
    if not state or int(state.get("batch_version") or 0) != int(batch_version):
        raise BatchRefreshBlocked("Cannot record final PDF delivery for a stale file-refresh batch.")
    if not str(state.get("verified_final_pdf_path") or "").strip():
        raise BatchRefreshBlocked("The file-refresh batch has no verified final PDF to deliver.")
    verify_final_batch_pdf(state)
    state["final_pdf_delivered_at"] = now_iso()
    state["finalized_at"] = now_iso()
    state["status"] = "FINALIZED"
    return save_batch_state(state)


def verify_final_batch_pdf(record: dict[str, Any]) -> Path:
    path_text = str(
        record.get("verified_final_pdf_path")
        or record.get("final_pdf_path")
        or ""
    ).strip()
    path = resolve_local_path(path_text) if path_text else Path()
    if not path_text or not path.exists() or not path.is_file():
        raise BatchRefreshBlocked("The combined PDF is no longer available.")
    expected = str(record.get("final_pdf_sha256") or "").strip()
    if not expected or file_sha256(path) != expected:
        raise BatchRefreshBlocked("The combined PDF integrity check failed before delivery.")
    return path


def _clean_final_pdf_name(name: str, course_code: str) -> str:
    candidate = Path(str(name or "")).name
    if candidate.lower().endswith(".pdf"):
        candidate = candidate[:-4]
    for marker in FINAL_FILENAME_TRACE_WORDS:
        candidate = re.sub(re.escape(marker), "", candidate, flags=re.IGNORECASE)
    fallback_name = (
        f"{safe_name(course_code, '')} Course Documents"
        if safe_name(course_code, "")
        else "Course Documents"
    )
    candidate = safe_name(candidate, fallback_name)
    return f"{candidate}.pdf"


def _page_is_blank(page: Any) -> bool:
    try:
        if str(page.extract_text() or "").strip():
            return False
    except Exception:
        pass
    try:
        resources = page.get("/Resources") or {}
        if resources.get("/XObject"):
            return False
    except Exception:
        pass
    try:
        contents = page.get_contents()
        if contents is not None and bytes(contents.get_data()).strip():
            return False
    except Exception:
        pass
    return True


def _merge_preview_pdfs(
    paths: list[Path],
    output_path: Path,
    title: str,
) -> dict[str, Any]:
    writer = PdfWriter()
    expected_pages = 0
    blank_pages: list[int] = []
    merged_text: list[str] = []
    for path in paths:
        reader = PdfReader(str(path))
        expected_pages += len(reader.pages)
        for page in reader.pages:
            writer.add_page(page)
            page_number = len(writer.pages)
            if _page_is_blank(page):
                blank_pages.append(page_number)
            try:
                merged_text.append(str(page.extract_text() or ""))
            except Exception:
                merged_text.append("")
    writer.add_metadata(
        {
            "/Title": title,
            "/Author": "",
            "/Subject": "",
            "/Creator": "",
            "/Producer": "",
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{output_path.stem}.", suffix=".pdf", dir=output_path.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with temp_path.open("wb") as handle:
            writer.write(handle)
        verification = PdfReader(str(temp_path))
        actual_pages = len(verification.pages)
        audit = audit_visible_text("\n".join(merged_text))
        if actual_pages != expected_pages:
            raise BatchRefreshBlocked(
                f"Final PDF page-count verification failed: expected {expected_pages}, found {actual_pages}."
            )
        if blank_pages:
            raise BatchRefreshBlocked(
                "Final PDF contains apparently blank pages: "
                + ", ".join(str(number) for number in blank_pages[:20])
            )
        if audit.get("status") != "PASS":
            raise BatchRefreshBlocked(
                "Final PDF failed the visible date/trace audit and was not released."
            )
        temp_path.replace(output_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return {
        "page_count": expected_pages,
        "blank_pages": blank_pages,
        "audit": audit,
    }


def execute_finalize_file_batch_preview_operation(
    operation: dict[str, Any],
    env: dict[str, str],
    run_id: str,
    raw_task: str,
    *,
    source_authority: str = "whatsapp",
) -> dict[str, Any]:
    errors = validate_finalize_file_batch_preview_shape(operation)
    if errors:
        return {
            "ok": False,
            "status": "BLOCKED",
            "operation_kind": FINALIZE_OPERATION_KIND,
            "execution_mode": EXECUTION_MODE,
            "run_id": run_id,
            "blocker": "; ".join(errors),
            "attachments": [],
        }
    params = _params(operation)
    batch_id = str(params.get("batch_id") or operation.get("batch_id") or "").strip()
    requested_version = int(params.get("batch_version") or operation.get("batch_version") or 0)
    state = load_batch_state(batch_id)
    if not state:
        blocker = "The requested file-refresh batch is not active."
    elif requested_version != int(state.get("batch_version") or 0):
        blocker = "The requested file-refresh batch version is stale."
    elif str(state.get("status") or "") == "FINALIZED":
        blocker = "The requested file-refresh batch was already finalized."
    elif str(state.get("status") or "") == "FINAL_PDF_READY":
        blocker = "The final PDF for this batch is already generated and awaiting delivery."
    elif not bool(state.get("preview_delivery_complete")):
        blocker = "All available per-file previews must be delivered before the batch can be finalized."
    else:
        expected_requester = str(state.get("whatsapp_requester") or "").strip()
        observed_requester = str(env.get("_WHATSAPP_REQUESTER") or "").strip()
        blocker = (
            "The final confirmation requester does not match the active file-refresh batch."
            if source_authority != "operator_cli"
            and expected_requester
            and expected_requester.casefold() != observed_requester.casefold()
            else ""
        )
    if blocker:
        return {
            "ok": False,
            "status": "BLOCKED",
            "operation_kind": FINALIZE_OPERATION_KIND,
            "execution_mode": EXECUTION_MODE,
            "run_id": run_id,
            "batch_id": batch_id,
            "batch_version": requested_version,
            "blocker": blocker,
            "attachments": [],
        }
    requested_ids = [
        str(item).strip()
        for item in params.get("include_item_ids") or []
        if str(item or "").strip()
    ]
    all_items = [item for item in state.get("items") or [] if isinstance(item, dict)]
    eligible = [
        item
        for item in all_items
        if item.get("status") == "READY_FOR_REVIEW"
        and str(item.get("preview_delivered_at") or "").strip()
        and str(item.get("preview_pdf_path") or "").strip()
    ]
    if requested_ids:
        eligible_by_id = {str(item.get("item_id")): item for item in eligible}
        invalid = [item_id for item_id in requested_ids if item_id not in eligible_by_id]
        if invalid:
            return {
                "ok": False,
                "status": "BLOCKED",
                "operation_kind": FINALIZE_OPERATION_KIND,
                "execution_mode": EXECUTION_MODE,
                "run_id": run_id,
                "batch_id": batch_id,
                "batch_version": requested_version,
                "blocker": "Requested final PDF items are not ready: " + ", ".join(invalid),
                "attachments": [],
            }
        selected = [eligible_by_id[item_id] for item_id in requested_ids]
        selected.sort(key=lambda item: int(item.get("input_index") or 0))
    else:
        selected = sorted(eligible, key=lambda item: int(item.get("input_index") or 0))
    if not selected:
        return {
            "ok": False,
            "status": "BLOCKED",
            "operation_kind": FINALIZE_OPERATION_KIND,
            "execution_mode": EXECUTION_MODE,
            "run_id": run_id,
            "batch_id": batch_id,
            "batch_version": requested_version,
            "blocker": "The batch has no delivered, audit-passing previews to merge.",
            "attachments": [],
        }
    try:
        paths = [verify_batch_item_preview(item) for item in selected]
    except BatchRefreshBlocked as exc:
        return {
            "ok": False,
            "status": "BLOCKED",
            "operation_kind": FINALIZE_OPERATION_KIND,
            "execution_mode": EXECUTION_MODE,
            "run_id": run_id,
            "batch_id": batch_id,
            "batch_version": requested_version,
            "blocker": str(exc),
            "attachments": [],
        }
    course_code = str(state.get("course_code") or "")
    final_name = _clean_final_pdf_name(str(params.get("final_pdf_name") or ""), course_code)
    final_path = FINAL_PDF_ROOT / safe_name(batch_id, "batch") / final_name
    try:
        verification = _merge_preview_pdfs(paths, final_path, final_path.stem)
    except (BatchRefreshBlocked, OSError, ValueError) as exc:
        return {
            "ok": False,
            "status": "BLOCKED",
            "operation_kind": FINALIZE_OPERATION_KIND,
            "execution_mode": EXECUTION_MODE,
            "run_id": run_id,
            "batch_id": batch_id,
            "batch_version": requested_version,
            "blocker": str(exc),
            "attachments": [],
        }
    selected_ids = [str(item.get("item_id")) for item in selected]
    excluded = [
        {
            "item_id": str(item.get("item_id") or ""),
            "source_file_name": str(item.get("source_file_name") or ""),
            "status": str(item.get("status") or ""),
        }
        for item in all_items
        if str(item.get("item_id") or "") not in selected_ids
    ]
    excluded.extend(
        {
            "item_id": "",
            "source_file_name": str(
                item.get("archive_member")
                or item.get("source_file_name")
                or "skipped package item"
            ),
            "status": "SKIPPED",
        }
        for item in state.get("skipped") or []
        if isinstance(item, dict)
    )
    final_state_status = "FINALIZED" if source_authority == "operator_cli" else "FINAL_PDF_READY"
    state.update(
        {
            "status": final_state_status,
            "final_pdf_generated_at": now_iso(),
            "finalized_at": now_iso() if source_authority == "operator_cli" else "",
            "finalized_run_id": run_id,
            "finalized_raw_task": raw_task,
            "final_pdf_path": repo_rel(final_path),
            "verified_final_pdf_path": str(final_path.resolve()),
            "final_pdf_sha256": file_sha256(final_path),
            "final_pdf_name": final_path.name,
            "final_page_count": verification["page_count"],
            "final_audit": verification["audit"],
            "included_item_ids": selected_ids,
            "excluded_items": excluded,
            "final_pdf_delivered_at": now_iso() if source_authority == "operator_cli" else "",
        }
    )
    save_batch_state(state)
    report_path = REPORT_ROOT / safe_name(batch_id, "batch") / f"final_result_v{requested_version}.json"
    result = {
        "ok": True,
        "status": final_state_status,
        "operation_kind": FINALIZE_OPERATION_KIND,
        "execution_mode": EXECUTION_MODE,
        "run_id": run_id,
        "batch_id": batch_id,
        "batch_version": requested_version,
        "final_pdf_path": repo_rel(final_path),
        "verified_final_pdf_path": str(final_path.resolve()),
        "final_pdf_sha256": file_sha256(final_path),
        "final_pdf_name": final_path.name,
        "page_count": verification["page_count"],
        "audit": verification["audit"],
        "included_item_ids": selected_ids,
        "excluded_items": excluded,
        "attachments": [repo_rel(final_path)],
        "result_path": repo_rel(report_path),
        "note": "Combined the latest eligible review PDFs. No Moodle change was made.",
    }
    _atomic_write_json(report_path, result)
    return result
