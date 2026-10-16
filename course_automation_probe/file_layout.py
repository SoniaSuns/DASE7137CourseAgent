from __future__ import annotations

from enum import StrEnum
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COURSE_FILES_ROOT = ROOT / "course_files"

PRE_REFRESH_ROOT = COURSE_FILES_ROOT / "pre_refresh"
POST_REFRESH_ROOT = COURSE_FILES_ROOT / "post_refresh"
UPLOAD_READY_ROOT = COURSE_FILES_ROOT / "upload_ready"
EVIDENCE_ROOT = COURSE_FILES_ROOT / "evidence"

EVIDENCE_PRE_REFRESH_ROOT = EVIDENCE_ROOT / "pre_refresh"
EVIDENCE_POST_REFRESH_ROOT = EVIDENCE_ROOT / "post_refresh"
EVIDENCE_PROCESS_ROOT = EVIDENCE_ROOT / "process"

CURRENT_REFERENCE_PRE_ROOT = PRE_REFRESH_ROOT / "current_reference"
CURRENT_ONEDRIVE_PRE_ROOT = PRE_REFRESH_ROOT / "onedrive_unzip"
OLD7137_BACKUP_PRE_ROOT = PRE_REFRESH_ROOT / "old7137_backup"
DASE7137_BACKUP_PRE_ROOT = PRE_REFRESH_ROOT / "dase7137_backup"

CURRENT_COURSE_POST_ROOT = POST_REFRESH_ROOT / "current_course"
OLD7137_BACKUP_POST_ROOT = POST_REFRESH_ROOT / "old7137_backup"
DASE7137_BACKUP_POST_ROOT = POST_REFRESH_ROOT / "dase7137_backup"

CURRENT_COURSE_UPLOAD_READY_ROOT = UPLOAD_READY_ROOT / "current_course"
OLD7137_BACKUP_UPLOAD_READY_ROOT = UPLOAD_READY_ROOT / "old7137_backup"
DASE7137_BACKUP_UPLOAD_READY_ROOT = UPLOAD_READY_ROOT / "dase7137_backup"

COURSE_AUTOMATION_REPORT_DIR = EVIDENCE_PROCESS_ROOT / "course_automation_probe" / "reports"
MINIMAL_FLOW_REPORT_DIR = EVIDENCE_PROCESS_ROOT / "course_agent_app" / "reports"

OPENCLAW_SOURCE_RENDER_ROOT = EVIDENCE_PRE_REFRESH_ROOT / "renders"
OPENCLAW_OUTPUT_RENDER_ROOT = EVIDENCE_POST_REFRESH_ROOT / "renders"
OLD7137_BACKUP_REFRESH_WORK_ROOT = EVIDENCE_PROCESS_ROOT / "old7137_backup_refresh_work_20260630"
DASE7137_BACKUP_REFRESH_WORK_ROOT = EVIDENCE_PROCESS_ROOT / "dase7137_backup_refresh_work"

MIGRATION_MANIFEST = COURSE_FILES_ROOT / "migration_manifest.json"


class CourseFileKind(StrEnum):
    PRE_REFRESH = "pre_refresh"
    POST_REFRESH = "post_refresh"
    UPLOAD_READY = "upload_ready"
    EVIDENCE = "evidence"
    INVALID = "invalid"


REFRESH_ARTIFACT_MARKERS = (
    "2026-2027 翻新文件",
    "OpenClaw",
    "Minimal Refresh",
    "Mentor Rule Refresh",
    "Visual Date Minimal Refresh",
    "2026-2027 Refresh",
    "Refreshed",
)


def resolve_repo_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve(strict=False)
    return (ROOT / candidate).resolve(strict=False)


def repo_rel(path: str | Path) -> str:
    resolved = resolve_repo_path(path)
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def is_relative_to(path: str | Path, root: str | Path) -> bool:
    resolved = resolve_repo_path(path)
    root_resolved = resolve_repo_path(root)
    try:
        resolved.relative_to(root_resolved)
        return True
    except ValueError:
        return False


def classify_course_file(path: str | Path) -> str:
    resolved = resolve_repo_path(path)
    if is_relative_to(resolved, PRE_REFRESH_ROOT):
        return CourseFileKind.PRE_REFRESH.value
    if is_relative_to(resolved, POST_REFRESH_ROOT):
        return CourseFileKind.POST_REFRESH.value
    if is_relative_to(resolved, UPLOAD_READY_ROOT):
        return CourseFileKind.UPLOAD_READY.value
    if is_relative_to(resolved, EVIDENCE_ROOT):
        return CourseFileKind.EVIDENCE.value
    return CourseFileKind.INVALID.value


def assert_allowed_source_path(path: str | Path) -> Path:
    resolved = resolve_repo_path(path)
    if classify_course_file(resolved) != CourseFileKind.PRE_REFRESH.value:
        raise ValueError(f"source_path must be under {repo_rel(PRE_REFRESH_ROOT)}: {repo_rel(resolved)}")
    rel_text = repo_rel(resolved.relative_to(PRE_REFRESH_ROOT) if is_relative_to(resolved, PRE_REFRESH_ROOT) else resolved)
    if any(marker in rel_text for marker in REFRESH_ARTIFACT_MARKERS):
        raise ValueError(f"refresh artifact cannot be used from pre_refresh: {repo_rel(resolved)}")
    return resolved


def assert_allowed_output_path(path: str | Path) -> Path:
    resolved = resolve_repo_path(path)
    kind = classify_course_file(resolved)
    if kind not in {CourseFileKind.POST_REFRESH.value, CourseFileKind.UPLOAD_READY.value}:
        raise ValueError(
            "output/upload path must be under "
            f"{repo_rel(POST_REFRESH_ROOT)} or {repo_rel(UPLOAD_READY_ROOT)}: {repo_rel(resolved)}"
        )
    return resolved


def assert_allowed_preview_path(path: str | Path) -> Path:
    resolved = resolve_repo_path(path)
    kind = classify_course_file(resolved)
    if kind in {CourseFileKind.POST_REFRESH.value, CourseFileKind.UPLOAD_READY.value}:
        return resolved
    if is_relative_to(resolved, EVIDENCE_POST_REFRESH_ROOT):
        return resolved
    raise ValueError(
        "preview_path must be under post_refresh, upload_ready, or evidence/post_refresh: "
        f"{repo_rel(resolved)}"
    )


def assert_allowed_evidence_path(path: str | Path) -> Path:
    resolved = resolve_repo_path(path)
    if classify_course_file(resolved) != CourseFileKind.EVIDENCE.value:
        raise ValueError(f"evidence path must be under {repo_rel(EVIDENCE_ROOT)}: {repo_rel(resolved)}")
    return resolved


def ensure_layout() -> None:
    for directory in [
        PRE_REFRESH_ROOT,
        POST_REFRESH_ROOT,
        UPLOAD_READY_ROOT,
        EVIDENCE_PRE_REFRESH_ROOT,
        EVIDENCE_POST_REFRESH_ROOT,
        EVIDENCE_PROCESS_ROOT,
        CURRENT_REFERENCE_PRE_ROOT,
        CURRENT_ONEDRIVE_PRE_ROOT,
        OLD7137_BACKUP_PRE_ROOT,
        DASE7137_BACKUP_PRE_ROOT,
        CURRENT_COURSE_POST_ROOT,
        OLD7137_BACKUP_POST_ROOT,
        DASE7137_BACKUP_POST_ROOT,
        CURRENT_COURSE_UPLOAD_READY_ROOT,
        OLD7137_BACKUP_UPLOAD_READY_ROOT,
        DASE7137_BACKUP_UPLOAD_READY_ROOT,
        COURSE_AUTOMATION_REPORT_DIR,
        MINIMAL_FLOW_REPORT_DIR,
    ]:
        directory.mkdir(parents=True, exist_ok=True)
