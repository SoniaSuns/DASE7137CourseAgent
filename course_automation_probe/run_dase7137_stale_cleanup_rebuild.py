#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from file_layout import (
    COURSE_AUTOMATION_REPORT_DIR,
    DASE7137_BACKUP_POST_ROOT,
    DASE7137_BACKUP_PRE_ROOT,
    DASE7137_BACKUP_UPLOAD_READY_ROOT,
    CURRENT_ONEDRIVE_PRE_ROOT,
    CURRENT_REFERENCE_PRE_ROOT,
    ROOT,
    assert_allowed_output_path,
    ensure_layout,
    repo_rel,
    resolve_repo_path,
)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_sandbox_clean_rebuild_debug import (  # noqa: E402
    PipelineBlocked,
    SANDBOX_COURSE_ID,
    SANDBOX_COURSE_URL,
    build_strong_clear_plan,
    execute_strong_clear,
    flatten_modules,
    flatten_sections,
    load_contents,
    load_local_env,
    run_command,
    utc_now,
    write_json,
)
from course_agent import build_codex_exec_command  # noqa: E402


BATCH_ID = "DASE7137-BACKUP-STALE-CLEANUP-REBUILD-SANDBOX"
DEFAULT_BACKUP_PATH = ROOT / "backup-moodle2-course-128134-dase7137_1a_2025-20260707-1636-nu.mbz"
SOURCE_COURSE_ID = "128134"
SOURCE_COURSE_SHORTNAME = "DASE7137_1A_2025"
SOURCE_COURSE_CODE = "DASE7137"
SOURCE_SECTION_COUNT = 15
SOURCE_ACTIVITY_COUNT = 61
CURRENT_SEMESTER_POLICY = "2026-2027 first semester"
SEM1_TIMELINE_EVIDENCE = "course_files/upload_ready/current_course/20260703/build_report.json"
FORBIDDEN_SOURCE_MARKERS = (
    "old7137_backup",
    "IMSE7137",
    "118635",
    "RUN-20260707-065238",
    "backup-moodle2-course-128135",
    "dase7137_2b_2025",
    "dase7137_visual_correspondence_20260708_2b",
)
OFFICE_SUFFIXES = {".docx", ".pptx", ".xlsx"}
SUPPORTED_UPLOAD_SUFFIXES = {".pdf"}
CORRESPONDENCE_ROOT = ROOT / "course_files" / "evidence" / "process" / "dase7137_visual_correspondence_20260709_1a"
CORRESPONDENCE_METRICS_JSON = CORRESPONDENCE_ROOT / "file_level_metrics.json"
CORRESPONDENCE_REPORT_MD = CORRESPONDENCE_ROOT / "file_level_report.md"
CONFIRMED_CORRESPONDENCE_DECISION_JSON = CORRESPONDENCE_ROOT / "confirmed_correspondence_decision.json"
RELIABLE_CORRESPONDENCE_VERDICTS = {"FILE_VISUAL_MATCH", "FILE_VISUAL_PREFIX_SUBSET"}
RUNTIME_CONFIRMED_1A_UPLOAD_SCOPE = "runtime_confirmed_1a_correspondence_office_originals_only"
REFRESH_SOURCE_KINDS = {"md_office_original", "backup_office_original", "title_office_original", "pdf_fallback"}
OFFICE_REFRESH_SOURCE_KINDS = {"md_office_original", "backup_office_original", "title_office_original"}
DATE_REPLACEMENT_KINDS = {"sem1_calendar_grounded", "moodle_setting_required", "remove_or_generalize_date"}
CODEX_REFRESH_DECISIONS = {"refresh", "no_replacement_needed", "blocked", "manual_review"}
CODEX_ACTIVITY_CREATION_DECISIONS = {
    "create_hidden_activity",
    "create_hidden_choice",
    "create_hidden_choicegroup",
    "create_hidden_group_scaffold",
    "create_hidden_url",
    "skip_existing",
    "block_staff_review",
}
CODEX_FILE_BACKFILL_DECISIONS = {
    "upload_missing_visible_file",
    "skip_existing",
    "block_staff_review",
}
EMBEDDED_VISUAL_DATE_AUDIT_CLASSIFICATION = "mentor_approved_illustrative_screenshot"
EMBEDDED_VISUAL_DATE_STRATEGY = {
    "scope": "project_wide",
    "rule": (
        "Do not pixel-edit old dates inside non-authoritative screenshots, form mockups, "
        "file-browser views, or UI examples solely to scrub them. Add or rely on a grounded "
        "current-semester example in adjacent student-facing text, record evidence, and rerun "
        "visual/date audit. This exception is forbidden for authoritative course deadlines, "
        "release dates, event dates, Moodle settings, or the only student-facing instruction."
    ),
    "audit_classification_after_validation": EMBEDDED_VISUAL_DATE_AUDIT_CLASSIFICATION,
}
AUTHORITATIVE_VISUAL_DATE_SNIPPET_MARKERS = (
    "final presentation will take place",
    "demo day * date",
    "date: 21/",
    "date: 14",
    "submission deadline",
    "start time:",
    "deadline:",
    "open date",
    "close date",
    "cutoff",
)
NON_AUTHORITATIVE_VISUAL_DATE_SNIPPET_MARKERS = (
    "date modified",
    "file folder",
    "application extension",
    "windows batch file",
    "unitycrashhandler",
    "unityplayer",
    "monobleedingedge",
    "_data",
    "program files",
    "thispc",
    "this pc",
)
MOODLE_BREADCRUMB_SCREENSHOT_MARKERS = (
    "dase7137_1a_2025 /",
    "dase7137 1a. 2025 /",
)
SEM2_FORBIDDEN_VISIBLE_MARKERS = (
    "2026-2027 second semester",
    "second semester",
    "semester 2",
    "Semester 2",
    "Jan 2027",
    "Feb 2027",
    "Mar 2027",
    "Apr 2027",
    "May 2027",
    "19 Apr 2027",
    "26 Apr 2027",
    "April 2027",
)
SEM2_FORBIDDEN_VISIBLE_REGEXES = (
    (r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may)\.?\s+\d{1,2},?\s+2027\b", "Sem2 month date in 2027"),
    (r"\b\d{1,2}\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may)\.?\s+2027\b", "Sem2 day-month date in 2027"),
)
STUDENT_VISIBLE_TRACE_MARKERS = (
    "Codex",
    "Codex",
    "agent",
    "AI",
    "modified by",
    "minimal refresh",
    "2026-2027 Refresh",
    "refreshed",
    "revised",
    "revision",
    "marked",
    "highlighted",
)
SAFE_ACTIVITY_SHELL_TYPES = {
    "assign",
    "forum",
    "choice",
    "folder",
    "quiz",
    "choicegroup",
    "peerwork",
    "questionnaire",
    "qbank",
}
ACTIVITY_DATE_SETTING_FIELDS = (
    "timeopen",
    "timeclose",
    "allowsubmissionsfromdate",
    "duedate",
    "cutoffdate",
    "gradingduedate",
    "completionexpected",
)
DISALLOWED_SETTING_KEYS = (
    "deadline",
    "due",
    "duedate",
    "cutoff",
    "availabilityconditionsjson",
    "slot",
    "grade",
    "student",
    "submission",
    "user",
    "group_members",
    "question_text",
    "question_bank_content",
)
BUNDLED_BIN = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/bin"
BUNDLED_OVERRIDE_BIN = BUNDLED_BIN / "override"
BUNDLED_LIBREOFFICE = (
    Path.home()
    / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/libreoffice-headless/libreoffice/LibreOfficeDev.app/Contents/MacOS/soffice"
)
BUNDLED_POPPLER_BIN = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/poppler/bin"
SOFFICE = next(
    (
        candidate
        for candidate in [
            Path(os.environ["SOFFICE"]) if os.environ.get("SOFFICE") else None,
            BUNDLED_OVERRIDE_BIN / "soffice",
            BUNDLED_LIBREOFFICE,
            BUNDLED_BIN / "soffice",
        ]
        if candidate is not None and candidate.exists()
    ),
    BUNDLED_BIN / "soffice",
)
PDFTOTEXT = BUNDLED_POPPLER_BIN / "pdftotext"


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def text(node: ET.Element | None, default: str = "") -> str:
    if node is None or node.text is None:
        return default
    value = node.text.strip()
    return "" if value == "$@NULL@$" else value


def parse_xml(path: Path) -> ET.Element:
    return ET.parse(path).getroot()


def load_json(path: str | Path) -> Any:
    return json.loads(resolve_repo_path(path).read_text(encoding="utf-8"))


def is_date_policy_object(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("source_course"), dict)
        and isinstance(value.get("date_replacement_policy"), list)
        and isinstance(value.get("refresh_items"), list)
    )


def is_activity_policy_object(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("policy_kind") == "codex_activity_creation_and_diff_backfill"
        and isinstance(value.get("activity_creation_decisions"), list)
        and isinstance(value.get("file_backfill_decisions"), list)
    )


def extract_json_object(raw: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    first_object: dict[str, Any] | None = None
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            if is_date_policy_object(value):
                return value
            if first_object is None:
                first_object = value
            for payload in as_list(((value.get("result") or {}).get("payloads") if isinstance(value.get("result"), dict) else [])):
                text_value = payload.get("text") if isinstance(payload, dict) else ""
                if not text_value:
                    continue
                nested = extract_json_object(str(text_value))
                if is_date_policy_object(nested):
                    return nested
    if first_object is not None:
        return first_object
    raise PipelineBlocked("Codex did not return a JSON object.")


def extract_activity_policy_object(raw: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    first_object: dict[str, Any] | None = None
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        if is_activity_policy_object(value):
            return value
        if first_object is None:
            first_object = value
        payloads = (value.get("result") or {}).get("payloads") if isinstance(value.get("result"), dict) else []
        for payload in as_list(payloads):
            text_value = payload.get("text") if isinstance(payload, dict) else ""
            if not text_value:
                continue
            try:
                nested = extract_activity_policy_object(str(text_value))
            except PipelineBlocked:
                continue
            if is_activity_policy_object(nested):
                return nested
    if first_object is not None and is_activity_policy_object(first_object):
        return first_object
    raise PipelineBlocked("Codex did not return an activity/diff policy JSON object.")


def safe_name(value: str, fallback: str = "item") -> str:
    value = re.sub(r"\s+", " ", str(value)).strip()
    value = re.sub(r"[^\w().&+ -]+", "-", value, flags=re.UNICODE)
    value = re.sub(r"\s+", "-", value).strip("-")
    return value[:100] or fallback


def clean_upload_filename(title: str, suffix: str = ".pdf") -> str:
    cleaned = re.sub(r"[/:*?\"<>|]+", " ", str(title))
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    return f"{cleaned or 'Course material'}{suffix or '.pdf'}"


def clean_student_title(title: str) -> str:
    cleaned = re.sub(r"IMSE7137", SOURCE_COURSE_CODE, str(title), flags=re.I)
    cleaned = re.sub(r"[_\-]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "Course material"


def clean_student_filename(filename: str) -> str:
    path = Path(str(filename))
    return clean_upload_filename(clean_student_title(path.stem), path.suffix)


def normalize_title(value: str) -> str:
    text_value = re.sub(r"[_\-]+", " ", str(value)).casefold()
    text_value = re.sub(r"\b(?:dase7137|2025|2026|2027|1a|2b|draft|final|version|updated)\b", " ", text_value)
    text_value = re.sub(r"[^a-z0-9]+", " ", text_value)
    return re.sub(r"\s+", " ", text_value).strip()


def token_set(value: str) -> set[str]:
    return {token for token in normalize_title(value).split() if len(token) > 1}


def source_item_id(record: dict[str, Any], file_record: dict[str, Any], item_title: str) -> str:
    seed = "|".join(
        [
            SOURCE_COURSE_ID,
            str(record.get("moduleid") or ""),
            str(record.get("section_number") or ""),
            str(item_title or ""),
            str(file_record.get("restored_path") or ""),
        ]
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def sem1_policy_hits_for_text(value: str) -> list[str]:
    text_value = str(value or "")
    lowered = text_value.casefold()
    hits = {marker for marker in SEM2_FORBIDDEN_VISIBLE_MARKERS if marker.casefold() in lowered}
    for pattern, label in SEM2_FORBIDDEN_VISIBLE_REGEXES:
        if re.search(pattern, text_value, flags=re.IGNORECASE):
            hits.add(label)
    return sorted(hits)


def extract_pdf_text_for_semester_check(path: Path) -> str:
    path = resolve_repo_path(path)
    if not path.exists():
        raise PipelineBlocked(f"Sem1 PDF policy check cannot read missing PDF: {repo_rel(path)}")
    extract_errors: list[str] = []
    for module_name in ("pypdf", "PyPDF2"):
        try:
            module = __import__(module_name)
            reader = module.PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as exc:  # pragma: no cover - exercised only when local PDF libs vary
            extract_errors.append(f"{module_name}: {exc}")
    pdftotext = str(PDFTOTEXT) if PDFTOTEXT.exists() else shutil.which("pdftotext")
    if pdftotext:
        proc = subprocess.run([pdftotext, str(path), "-"], text=True, capture_output=True, timeout=120, check=False)
        if proc.returncode == 0:
            return proc.stdout
        extract_errors.append(f"pdftotext: {proc.stderr or proc.stdout}")
    raise PipelineBlocked("Sem1 PDF policy check requires readable PDF text. " + "; ".join(extract_errors))


def sem1_policy_check_for_pdf(path: Path) -> dict[str, Any]:
    extracted_text = extract_pdf_text_for_semester_check(path)
    hits = sem1_policy_hits_for_text(extracted_text)
    return {
        "semester_policy": CURRENT_SEMESTER_POLICY,
        "semester_policy_evidence": SEM1_TIMELINE_EVIDENCE,
        "status": "PASS_SEM1" if not hits else "BLOCKED_SEMESTER_POLICY",
        "forbidden_semester_markers": hits,
    }


def assert_no_forbidden_source(path_text: str) -> None:
    text_value = str(path_text)
    if "IMSE7137" in text_value and (
        "course_files/pre_refresh/dase7137_backup/" in text_value
        or "tmp/dase7137_backup_materials_" in text_value
        or "tmp/dase7137_backup_extract_" in text_value
    ):
        return
    if any(marker in text_value for marker in FORBIDDEN_SOURCE_MARKERS):
        raise PipelineBlocked(f"Refusing non-DASE7137 active source/input: {path_text}")


def assert_dase_output_path(path_text: str | Path) -> Path:
    path = assert_allowed_output_path(path_text)
    try:
        path.relative_to(DASE7137_BACKUP_POST_ROOT.resolve(strict=False))
        return path
    except ValueError:
        pass
    try:
        path.relative_to(DASE7137_BACKUP_UPLOAD_READY_ROOT.resolve(strict=False))
        return path
    except ValueError:
        raise PipelineBlocked(f"DASE rebuild outputs must stay under dase7137_backup roots: {repo_rel(path)}")


def assert_allowed_refresh_source_path(path_text: str | Path) -> Path:
    path = resolve_repo_path(path_text)
    assert_no_forbidden_source(repo_rel(path))
    allowed_roots = [CURRENT_REFERENCE_PRE_ROOT, CURRENT_ONEDRIVE_PRE_ROOT, DASE7137_BACKUP_PRE_ROOT]
    for root in allowed_roots:
        try:
            path.resolve(strict=False).relative_to(root.resolve(strict=False))
            return path
        except ValueError:
            continue
    raise PipelineBlocked(f"Refresh source is outside the DASE allowed source roots: {repo_rel(path)}")


def reject_disallowed_setting_keys(value: Any, path: str = "$", *, allow_externalurl: bool = False) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).casefold()
            if lowered == "externalurl" and allow_externalurl:
                reject_disallowed_setting_keys(child, f"{path}.{key}", allow_externalurl=allow_externalurl)
                continue
            if any(marker in lowered for marker in DISALLOWED_SETTING_KEYS):
                raise PipelineBlocked(f"Refusing disallowed setting key in DASE target model: {path}.{key}")
            reject_disallowed_setting_keys(child, f"{path}.{key}", allow_externalurl=allow_externalurl)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_disallowed_setting_keys(child, f"{path}[{index}]", allow_externalurl=allow_externalurl)


def validate_dase_backup_identity(extraction_or_course: dict[str, Any]) -> None:
    course = extraction_or_course.get("source_course") or extraction_or_course
    course_id = str(course.get("original_course_id") or "")
    shortname = str(course.get("original_course_shortname") or "")
    fullname = str(course.get("original_course_fullname") or "")
    if course_id != SOURCE_COURSE_ID or shortname != SOURCE_COURSE_SHORTNAME or SOURCE_COURSE_CODE not in fullname:
        raise PipelineBlocked(
            "Refusing backup that is not the active DASE7137 source "
            f"({SOURCE_COURSE_ID}/{SOURCE_COURSE_SHORTNAME})."
        )


def safe_extract_tar(backup_path: Path, extract_dir: Path) -> None:
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)
    root = extract_dir.resolve(strict=False)
    with tarfile.open(backup_path, "r:*") as archive:
        for member in archive.getmembers():
            target = (extract_dir / member.name).resolve(strict=False)
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise PipelineBlocked(f"Unsafe path in backup archive: {member.name}") from exc
        archive.extractall(extract_dir)


def parse_settings(backup_root: ET.Element) -> dict[str, str]:
    settings: dict[str, str] = {}
    for setting in backup_root.findall("./information/settings/setting"):
        settings[text(setting.find("name"))] = text(setting.find("value"))
    return settings


def parse_backup_activities(backup_root: ET.Element) -> list[dict[str, str]]:
    activities: list[dict[str, str]] = []
    for activity in backup_root.findall("./information/contents/activities/activity"):
        activities.append(
            {
                "moduleid": text(activity.find("moduleid")),
                "sectionid": text(activity.find("sectionid")),
                "modulename": text(activity.find("modulename")),
                "title": text(activity.find("title")),
                "directory": text(activity.find("directory")),
            }
        )
    return activities


def parse_sections(extract_dir: Path) -> dict[str, dict[str, Any]]:
    sections: dict[str, dict[str, Any]] = {}
    for path in sorted((extract_dir / "sections").glob("section_*/section.xml")):
        root = parse_xml(path)
        section_id = str(root.attrib.get("id", "") or path.parent.name.removeprefix("section_"))
        section_number = text(root.find("number"))
        section_name = text(root.find("name")) or ("General" if section_number == "0" else f"Week {section_number}")
        sections[section_id] = {
            "section_id": section_id,
            "section_number": section_number,
            "section_name": section_name,
            "sequence": [item for item in text(root.find("sequence")).split(",") if item],
            "visible": text(root.find("visible")),
        }
    return sections


def parse_files(extract_dir: Path) -> dict[str, dict[str, Any]]:
    root = parse_xml(extract_dir / "files.xml")
    files: dict[str, dict[str, Any]] = {}
    for file_node in root.findall("file"):
        file_id = str(file_node.attrib.get("id", ""))
        filename = text(file_node.find("filename"))
        files[file_id] = {
            "id": file_id,
            "contenthash": text(file_node.find("contenthash")),
            "contextid": text(file_node.find("contextid")),
            "component": text(file_node.find("component")),
            "filearea": text(file_node.find("filearea")),
            "itemid": text(file_node.find("itemid")),
            "filepath": text(file_node.find("filepath")) or "/",
            "filename": filename,
            "filesize": int(text(file_node.find("filesize"), "0") or 0),
            "mimetype": text(file_node.find("mimetype")),
            "source": text(file_node.find("source")),
            "timemodified": text(file_node.find("timemodified")),
        }
    return files


def activity_context(activity_dir: Path, modname: str) -> str:
    xml_path = activity_dir / f"{modname}.xml"
    if not xml_path.exists():
        return ""
    root = parse_xml(xml_path)
    return str(root.attrib.get("contextid", ""))


def parse_url(activity_dir: Path) -> dict[str, str]:
    root = parse_xml(activity_dir / "url.xml")
    url = root.find("url")
    return {
        "externalurl": text(url.find("externalurl") if url is not None else None),
        "intro": text(url.find("intro") if url is not None else None),
    }


def parse_int(value: str, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def activity_payload(activity_dir: Path, modname: str) -> ET.Element | None:
    xml_path = activity_dir / f"{modname}.xml"
    if not xml_path.exists():
        return None
    root = parse_xml(xml_path)
    return root.find(modname)


def old_date_fields_from(payload: ET.Element | None) -> list[dict[str, Any]]:
    if payload is None:
        return []
    blocked: list[dict[str, Any]] = []
    for field in ACTIVITY_DATE_SETTING_FIELDS:
        value = text(payload.find(field))
        if value and value not in {"0", "$@NULL@$"}:
            blocked.append({"field": field, "old_value_present": True})
    return blocked


def parse_choice_options(payload: ET.Element | None) -> list[dict[str, Any]]:
    if payload is None:
        return []
    options: list[dict[str, Any]] = []
    for option in payload.findall("./options/option"):
        label = clean_student_title(text(option.find("text")))
        if not label:
            continue
        options.append(
            {
                "label": label,
                "maxanswers": parse_int(text(option.find("maxanswers")), 0),
                "source_option_id": str(option.attrib.get("id", "")),
            }
        )
    return options


def parse_group_records(extract_dir: Path) -> list[dict[str, Any]]:
    groups_path = extract_dir / "groups.xml"
    if not groups_path.exists():
        return []
    root = parse_xml(groups_path)
    groups: list[dict[str, Any]] = []
    group_nodes = root.findall("./group") or root.findall("./groups/group")
    for group in group_nodes:
        group_id = str(group.attrib.get("id", ""))
        name = clean_student_title(text(group.find("name")))
        if not group_id or not name:
            continue
        groups.append(
            {
                "backup_groupid": group_id,
                "backup_group_name": name,
                "member_count": len(group.findall("./group_members/group_member")),
                "source_evidence": [f"dase_backup_group:{group_id}"],
            }
        )
    return groups


def parse_choicegroup_options(payload: ET.Element | None, groups_by_id: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    if payload is None:
        return [], []
    options: list[dict[str, Any]] = []
    skipped: list[str] = []
    for option in payload.findall("./options/option"):
        backup_groupid = text(option.find("groupid"))
        if not backup_groupid:
            continue
        group = groups_by_id.get(backup_groupid)
        if not group:
            skipped.append(backup_groupid)
            continue
        options.append(
            {
                "backup_groupid": backup_groupid,
                "backup_group_name": group.get("backup_group_name", ""),
                "maxanswers": parse_int(text(option.find("maxanswers")), 0),
                "source_option_id": str(option.attrib.get("id", "")),
            }
        )
    return options, skipped


def parse_activity_simulation(activity_dir: Path, modname: str, groups_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    payload = activity_payload(activity_dir, modname)
    blocked_date_fields = old_date_fields_from(payload)
    if modname == "choice":
        return {
            "simulation_mode": "structure_aware_hidden_choice",
            "staff_refresh_required": False,
            "blocked_old_date_fields": blocked_date_fields,
            "choice_options": parse_choice_options(payload),
            "copy_policy": "copy_safe_options_and_limits_only",
        }
    if modname == "choicegroup":
        options, skipped = parse_choicegroup_options(payload, groups_by_id)
        return {
            "simulation_mode": "structure_aware_hidden_choicegroup",
            "staff_refresh_required": False,
            "blocked_old_date_fields": blocked_date_fields,
            "choicegroup_options": options,
            "skipped_orphaned_option_groupids": skipped,
            "copy_policy": "create_empty_sandbox_groups_and_grouping_only",
        }
    if modname == "url":
        return {
            "simulation_mode": "hidden_real_url",
            "staff_refresh_required": False,
            "blocked_old_date_fields": blocked_date_fields,
            "copy_policy": "copy_backup_externalurl_only_with_generic_hidden_intro",
        }
    return {
        "simulation_mode": "hidden_shell_staff_refresh_required",
        "staff_refresh_required": modname in {"assign", "peerwork", "questionnaire", "quiz", "qbank", "folder"},
        "blocked_old_date_fields": blocked_date_fields,
        "copy_policy": "do_not_copy_submissions_grades_attempts_questions_memberships_or_old_dates",
    }


def safe_copy_path(directory: Path, filename: str) -> Path:
    target = directory / filename
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for index in range(2, 1000):
        candidate = directory / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise PipelineBlocked(f"Could not make a unique path for {target}")


def restore_file(extract_dir: Path, staging_dir: Path, section_name: str, activity_title: str, record: dict[str, Any]) -> str:
    contenthash = record["contenthash"]
    if not contenthash or contenthash == "da39a3ee5e6b4b0d3255bfef95601890afd80709":
        return ""
    stored = extract_dir / "files" / contenthash[:2] / contenthash
    if not stored.exists():
        return ""
    output_dir = staging_dir / safe_name(section_name, "section") / safe_name(clean_student_title(activity_title), "activity")
    relative_filepath = str(record.get("filepath", "/")).strip("/")
    if relative_filepath:
        output_dir = output_dir / relative_filepath
    output_dir.mkdir(parents=True, exist_ok=True)
    target = safe_copy_path(output_dir, clean_student_filename(record["filename"]))
    if not target.exists():
        shutil.copy2(stored, target)
    return repo_rel(target)


def build_extraction_report(backup_path: Path, extract_dir: Path, staging_dir: Path) -> dict[str, Any]:
    backup_root = parse_xml(extract_dir / "moodle_backup.xml")
    settings = parse_settings(backup_root)
    source_course = {
        "original_course_id": text(backup_root.find("./information/original_course_id")),
        "original_course_fullname": text(backup_root.find("./information/original_course_fullname")),
        "original_course_shortname": text(backup_root.find("./information/original_course_shortname")),
    }
    validate_dase_backup_identity(source_course)
    sections = parse_sections(extract_dir)
    files = parse_files(extract_dir)
    group_records = parse_group_records(extract_dir)
    groups_by_id = {str(group["backup_groupid"]): group for group in group_records}
    files_by_context: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in files.values():
        if record["filename"] and record["filename"] != "." and record["filesize"] > 0:
            files_by_context[record["contextid"]].append(record)

    activities = parse_backup_activities(backup_root)
    material_records: list[dict[str, Any]] = []
    restored_files: list[dict[str, Any]] = []
    url_records: list[dict[str, Any]] = []
    activity_records: list[dict[str, Any]] = []

    for activity in activities:
        modname = activity["modulename"]
        section = sections.get(activity["sectionid"], {})
        activity_dir = extract_dir / activity["directory"]
        contextid = activity_context(activity_dir, modname)
        common = {
            "moduleid": activity["moduleid"],
            "modulename": modname,
            "title": activity["title"],
            "section_id": activity["sectionid"],
            "section_number": section.get("section_number", ""),
            "section_name": section.get("section_name", ""),
            "contextid": contextid,
            "activity_simulation": parse_activity_simulation(activity_dir, modname, groups_by_id),
        }
        if modname in {"resource", "folder"}:
            activity_files = []
            for file_record in files_by_context.get(contextid, []):
                restored_path = restore_file(
                    extract_dir,
                    staging_dir,
                    str(section.get("section_name", "")),
                    activity["title"],
                    file_record,
                )
                file_summary = {
                    "filename": clean_student_filename(file_record["filename"]),
                    "filepath": file_record["filepath"],
                    "filesize": file_record["filesize"],
                    "mimetype": file_record["mimetype"],
                    "restored_path": restored_path,
                }
                activity_files.append(file_summary)
                if restored_path:
                    restored_files.append({**common, **file_summary})
            material_records.append({**common, "file_count": len(activity_files), "files": activity_files})
        elif modname == "url":
            url_records.append({**common, **parse_url(activity_dir)})
        else:
            activity_records.append(common)

    report = {
        "generated_at": utc_now(),
        "source_backup": repo_rel(backup_path),
        "extract_dir": repo_rel(extract_dir),
        "staging_dir": repo_rel(staging_dir),
        "source_course": source_course,
        "safety": {
            "users": settings.get("users"),
            "role_assignments": settings.get("role_assignments"),
            "comments": settings.get("comments"),
            "userscompletion": settings.get("userscompletion"),
            "logs": settings.get("logs"),
            "grade_histories": settings.get("grade_histories"),
            "groups": settings.get("groups"),
            "files": settings.get("files"),
        },
        "summary": {
            "activities": len(activities),
            "sections": len(sections),
            "backup_groups": len(group_records),
            "material_activities": len(material_records),
            "url_activities": len(url_records),
            "other_activity_records": len(activity_records),
            "restored_files": len(restored_files),
            "restored_bytes": sum(item["filesize"] for item in restored_files),
            "activity_counts": dict(Counter(activity["modulename"] for activity in activities)),
            "restored_mimetypes": dict(Counter(item["mimetype"] for item in restored_files)),
        },
        "sections": sorted(sections.values(), key=lambda item: int(item.get("section_number") or 0)),
        "material_records": material_records,
        "restored_files": restored_files,
        "group_records": group_records,
        "url_records": url_records,
        "activity_records": activity_records,
    }
    validate_dase_extraction_report(report)
    return report


def validate_dase_extraction_report(report: dict[str, Any]) -> None:
    validate_dase_backup_identity(report)
    if int((report.get("summary") or {}).get("sections") or 0) != SOURCE_SECTION_COUNT:
        raise PipelineBlocked(f"DASE7137 active backup must expose {SOURCE_SECTION_COUNT} sections.")
    if int((report.get("summary") or {}).get("activities") or 0) != SOURCE_ACTIVITY_COUNT:
        raise PipelineBlocked(f"DASE7137 active backup must expose {SOURCE_ACTIVITY_COUNT} activities.")
    for value in [report.get("source_backup"), report.get("extract_dir"), report.get("staging_dir")]:
        assert_no_forbidden_source(str(value))


def ensure_extraction(backup_path: Path) -> dict[str, Any]:
    backup_path = resolve_repo_path(backup_path)
    if not backup_path.exists():
        raise PipelineBlocked(f"DASE7137 backup file is missing: {repo_rel(backup_path)}")
    assert_no_forbidden_source(repo_rel(backup_path))
    extract_dir = DASE7137_BACKUP_PRE_ROOT / "extracted_backup"
    staging_dir = DASE7137_BACKUP_PRE_ROOT / "materials"
    safe_extract_tar(backup_path, extract_dir)
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    return build_extraction_report(backup_path, extract_dir, staging_dir)


def office_source_candidates() -> list[Path]:
    roots = [CURRENT_REFERENCE_PRE_ROOT, CURRENT_ONEDRIVE_PRE_ROOT, DASE7137_BACKUP_PRE_ROOT]
    candidates: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in OFFICE_SUFFIXES:
                assert_no_forbidden_source(repo_rel(path))
                candidates.append(path)
    return candidates


def match_score(title: str, filenames: list[str], candidate: Path) -> float:
    candidate_stem = normalize_title(candidate.stem)
    title_norm = normalize_title(title)
    filename_norms = [normalize_title(Path(name).stem) for name in filenames]
    names = [title_norm] + filename_norms
    if any(candidate_stem == name and name for name in names):
        return 1.0
    if any(candidate_stem and name and (f" {candidate_stem} " in f" {name} " or f" {name} " in f" {candidate_stem} ") for name in names):
        return 0.92
    candidate_tokens = token_set(candidate.stem)
    best = 0.0
    for name in [title, *filenames]:
        source_tokens = token_set(name)
        if len(source_tokens) < 2 or len(candidate_tokens) < 2:
            continue
        overlap = len(source_tokens & candidate_tokens)
        union = len(source_tokens | candidate_tokens)
        best = max(best, overlap / union if union else 0.0)
    return best


def find_office_original(record: dict[str, Any], candidates: list[Path] | None = None) -> dict[str, Any] | None:
    candidates = candidates if candidates is not None else office_source_candidates()
    filenames = [str(file_record.get("filename") or "") for file_record in as_list(record.get("files"))]
    best: tuple[float, Path] | None = None
    for candidate in candidates:
        score = match_score(str(record.get("title") or ""), filenames, candidate)
        if score >= 0.72 and (best is None or score > best[0]):
            best = (score, candidate)
    if not best:
        return None
    return {"path": repo_rel(best[1]), "score": round(best[0], 3), "match_rule": "title_or_filename_strong_match"}


def correspondence_backup_key(path_text: str | Path) -> str:
    path = Path(str(path_text).replace("\\", "/"))
    return normalize_title(clean_student_title(f"{path.parent.name} {path.stem}"))


def validate_confirmed_correspondence_decision(decision: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    source_course = decision.get("source_course") or {}
    if (
        str(source_course.get("original_course_id") or "") != SOURCE_COURSE_ID
        or str(source_course.get("original_course_shortname") or "") != SOURCE_COURSE_SHORTNAME
    ):
        raise PipelineBlocked("Confirmed correspondence decision is not for the active DASE7137 backup.")
    if decision.get("planner_write_allowed") not in (False, None):
        raise PipelineBlocked("Confirmed correspondence decision must be planning-only.")
    if decision.get("moodle_write_authorized") not in (False, None):
        raise PipelineBlocked("Confirmed correspondence decision must not authorize Moodle writes.")
    if decision.get("upload_scope") != RUNTIME_CONFIRMED_1A_UPLOAD_SCOPE:
        raise PipelineBlocked(f"Confirmed correspondence decision must use upload_scope {RUNTIME_CONFIRMED_1A_UPLOAD_SCOPE}.")
    if decision.get("require_matched_office_original") is not True:
        raise PipelineBlocked("Confirmed correspondence decision must require matched Office originals.")
    if decision.get("allow_partial_course_directory") is not True:
        raise PipelineBlocked("Confirmed correspondence decision must explicitly allow a partial course directory.")
    confirmed = decision.get("confirmed_correspondence_ids")
    if not isinstance(confirmed, list) or not confirmed or any(not isinstance(item, str) or not item for item in confirmed):
        raise PipelineBlocked("Confirmed correspondence decision requires confirmed_correspondence_ids.")
    return {
        "path": repo_rel(path) if path else str(decision.get("path") or ""),
        "decision_source": str(decision.get("decision_source") or "runtime_decision"),
        "source_course": {
            "original_course_id": SOURCE_COURSE_ID,
            "original_course_shortname": SOURCE_COURSE_SHORTNAME,
        },
        "upload_scope": RUNTIME_CONFIRMED_1A_UPLOAD_SCOPE,
        "confirmed_correspondence_ids": sorted(set(confirmed)),
        "require_matched_office_original": True,
        "allow_partial_course_directory": True,
        "planner_write_allowed": False,
        "moodle_write_authorized": False,
    }


def load_confirmed_correspondence_decision(path: Path | str | None = None) -> dict[str, Any]:
    decision_path = resolve_repo_path(path or CONFIRMED_CORRESPONDENCE_DECISION_JSON)
    if not decision_path.exists():
        raise PipelineBlocked(f"Confirmed correspondence decision is required: {repo_rel(decision_path)}")
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    if not isinstance(decision, dict):
        raise PipelineBlocked("Confirmed correspondence decision JSON must be an object.")
    return validate_confirmed_correspondence_decision(decision, path=decision_path)


def decision_confirmed_ids(decision: dict[str, Any]) -> set[str]:
    return {str(item) for item in as_list(decision.get("confirmed_correspondence_ids")) if str(item)}


def empty_correspondence_fields() -> dict[str, Any]:
    return {
        "correspondence_report_path": "",
        "correspondence_id": "",
        "correspondence_verdict": "",
        "correspondence_candidate_path": "",
        "correspondence_staff_confirmed": False,
        "correspondence_upload_scope": RUNTIME_CONFIRMED_1A_UPLOAD_SCOPE,
        "source_selection_rule": "",
    }


def correspondence_fields(
    entry: dict[str, Any],
    confirmed_ids: set[str],
    *,
    source_selection_rule: str = "",
) -> dict[str, Any]:
    correspondence_id = str(entry.get("id") or "")
    return {
        "correspondence_report_path": str(entry.get("report_path") or ""),
        "correspondence_id": correspondence_id,
        "correspondence_verdict": str(entry.get("verdict") or ""),
        "correspondence_candidate_path": str(entry.get("candidate") or ""),
        "correspondence_staff_confirmed": correspondence_id in confirmed_ids,
        "correspondence_upload_scope": RUNTIME_CONFIRMED_1A_UPLOAD_SCOPE,
        "source_selection_rule": source_selection_rule,
    }


def normalize_correspondence_entry(raw: dict[str, Any], report_path: Path) -> dict[str, Any]:
    backup = str(raw.get("backup") or raw.get("backup_path") or "").strip()
    candidate = str(raw.get("candidate") or raw.get("candidate_path") or "").strip()
    if not backup or not candidate:
        raise PipelineBlocked("DASE correspondence entry must include backup and candidate paths.")
    assert_no_forbidden_source(backup)
    assert_no_forbidden_source(candidate)
    candidate_suffix = Path(candidate).suffix.lower()
    if candidate_suffix in OFFICE_SUFFIXES:
        assert_allowed_refresh_source_path(candidate)
    return {
        "id": str(raw.get("id") or safe_name(str(raw.get("question") or "correspondence"))),
        "question": str(raw.get("question") or ""),
        "backup": repo_rel(backup),
        "candidate": repo_rel(candidate),
        "candidate_suffix": candidate_suffix,
        "verdict": str(raw.get("verdict") or raw.get("visual") or ""),
        "sheet": repo_rel(str(raw.get("sheet") or "")) if raw.get("sheet") else "",
        "report_path": repo_rel(report_path),
    }


def parse_correspondence_md(report_path: Path) -> list[dict[str, Any]]:
    if not report_path.exists():
        return []
    lines = report_path.read_text(encoding="utf-8").splitlines()
    verdict_by_question: dict[str, str] = {}
    for line in lines:
        if not line.startswith("|") or "`" not in line:
            continue
        columns = [column.strip() for column in line.strip().strip("|").split("|")]
        if len(columns) < 5 or not columns[0].isdigit():
            continue
        match = re.search(r"`([^`]+)`", columns[4])
        if match:
            verdict_by_question[columns[1]] = match.group(1)

    entries: list[dict[str, Any]] = []
    current: dict[str, str] | None = None

    def flush() -> None:
        if current and current.get("backup") and current.get("candidate"):
            question = current.get("question", "")
            entries.append(
                normalize_correspondence_entry(
                    {
                        "id": current.get("id", ""),
                        "question": question,
                        "backup": current.get("backup", ""),
                        "candidate": current.get("candidate", ""),
                        "verdict": verdict_by_question.get(question, ""),
                        "sheet": current.get("sheet", ""),
                    },
                    report_path,
                )
            )

    for line in lines:
        heading = re.match(r"^##\s+([^\s]+)\s+-\s+(.+)$", line)
        if heading:
            flush()
            current = {"id": heading.group(1), "question": heading.group(2)}
            continue
        if current is None:
            continue
        backup_match = re.match(r"^-\s+Backup:\s+`([^`]+)`", line)
        if backup_match:
            current["backup"] = backup_match.group(1)
            continue
        candidate_match = re.match(r"^-\s+Candidate:\s+`([^`]+)`", line)
        if candidate_match:
            current["candidate"] = candidate_match.group(1)
            continue
        sheet_match = re.match(r"^-\s+Sheet:\s+`([^`]+)`", line)
        if sheet_match:
            current["sheet"] = sheet_match.group(1)
    flush()
    return entries


def load_correspondence_entries(
    metrics_path: Path | None = None,
    report_path: Path | None = None,
) -> list[dict[str, Any]]:
    metrics_path = metrics_path or CORRESPONDENCE_METRICS_JSON
    report_path = report_path or CORRESPONDENCE_REPORT_MD
    if metrics_path.exists():
        data = json.loads(metrics_path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise PipelineBlocked(f"DASE correspondence metrics must be a list: {repo_rel(metrics_path)}")
        return [normalize_correspondence_entry(entry, report_path) for entry in data]
    return parse_correspondence_md(report_path)


def build_correspondence_index(entries: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        index[correspondence_backup_key(str(entry.get("backup") or ""))].append(entry)
    return index


def resolve_correspondence(
    restored_path: str,
    correspondence_index: dict[str, list[dict[str, Any]]],
    confirmed_ids: set[str],
) -> dict[str, Any] | None:
    matches = correspondence_index.get(correspondence_backup_key(restored_path), [])
    if not matches:
        return None
    if len(matches) > 1:
        return {
            **empty_correspondence_fields(),
            "status": "BLOCKED",
            "reason": "ambiguous_correspondence_candidates",
            "correspondence_candidate_path": ", ".join(str(item.get("candidate") or "") for item in matches),
        }
    entry = matches[0]
    fields = correspondence_fields(entry, confirmed_ids)
    correspondence_id = str(entry.get("id") or "")
    candidate = str(entry.get("candidate") or "")
    candidate_suffix = Path(candidate).suffix.lower()
    if correspondence_id not in confirmed_ids:
        return {
            **fields,
            "status": "BLOCKED",
            "reason": "not_in_staff_confirmed_1a_correspondence_upload_scope",
        }
    if candidate_suffix not in OFFICE_SUFFIXES:
        return {
            **fields,
            "status": "BLOCKED",
            "reason": f"staff_confirmed_correspondence_candidate_not_office:{candidate_suffix or 'missing_suffix'}",
        }
    candidate_path = assert_allowed_refresh_source_path(candidate)
    if not candidate_path.exists():
        return {
            **fields,
            "status": "BLOCKED",
            "reason": "correspondence_candidate_missing",
        }
    return {
        **fields,
        "status": "OFFICE",
        "source_path": repo_rel(candidate_path),
    }


def refresh_output_paths(record: dict[str, Any], run_id: str, source_suffix: str) -> dict[str, str]:
    title = clean_student_title(str(record.get("title") or "Course material"))
    office_dir = DASE7137_BACKUP_POST_ROOT / "office_refreshed" / run_id
    pdf_dir = DASE7137_BACKUP_POST_ROOT / "pdf_exported" / run_id
    fallback_dir = DASE7137_BACKUP_POST_ROOT / "pdf_fallback" / run_id
    office_name = clean_upload_filename(title, source_suffix)
    pdf_name = clean_upload_filename(title, ".pdf")
    return {
        "output_office_path": repo_rel(office_dir / office_name),
        "exported_pdf_path": repo_rel(pdf_dir / pdf_name),
        "fallback_pdf_path": repo_rel(fallback_dir / pdf_name),
    }


def build_refresh_plan(
    extraction: dict[str, Any],
    run_id: str,
    correspondence_entries: list[dict[str, Any]] | None = None,
    confirmed_correspondence_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_dase_extraction_report(extraction)
    confirmed_decision = confirmed_correspondence_decision or load_confirmed_correspondence_decision()
    confirmed_ids = decision_confirmed_ids(confirmed_decision)
    correspondence_entries = load_correspondence_entries() if correspondence_entries is None else correspondence_entries
    correspondence_index = build_correspondence_index(correspondence_entries)
    items: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for record in as_list(extraction.get("material_records")):
        if record.get("modulename") != "resource":
            continue
        files = [file_record for file_record in as_list(record.get("files")) if file_record.get("restored_path")]
        multi_file_resource = len(files) > 1
        if not files:
            blocked.append({"title": record.get("title"), "reason": "resource_has_no_restored_file", "source_evidence": [f"dase_backup_activity:{record.get('moduleid')}"]})
            continue
        for file_record in files:
            restored_path = str(file_record.get("restored_path") or "")
            assert_no_forbidden_source(restored_path)
            suffix = Path(restored_path).suffix.lower()
            item_title = (
                clean_student_title(Path(str(file_record.get("filename") or restored_path)).stem)
                if multi_file_resource
                else clean_student_title(str(record.get("title") or ""))
            )
            item_id = source_item_id(record, file_record, item_title)
            item_record = {**record, "title": item_title}
            paths = refresh_output_paths(item_record, run_id, suffix if suffix in OFFICE_SUFFIXES else ".pdf")
            correspondence = resolve_correspondence(restored_path, correspondence_index, confirmed_ids)
            correspondence_evidence = (
                {key: value for key, value in correspondence.items() if key not in {"status", "source_path", "reason"}}
                if correspondence
                else empty_correspondence_fields()
            )
            base = {
                "source_item_id": item_id,
                "title": item_title,
                "sectionnum": str(record.get("section_number")),
                "section_name": record.get("section_name") or ("General" if str(record.get("section_number")) == "0" else f"Week {record.get('section_number')}"),
                "semester_policy": CURRENT_SEMESTER_POLICY,
                "semester_policy_evidence": SEM1_TIMELINE_EVIDENCE,
                "source_evidence": [
                    f"dase_backup_activity:{record.get('moduleid')}",
                    f"dase_backup_file:{clean_student_filename(str(file_record.get('filename') or 'file'))}",
                ],
                "backup_file_path": restored_path,
                "office_match": None,
                "status": "PLANNED",
                **correspondence_evidence,
            }
            if not correspondence:
                blocked.append(
                    {
                        **base,
                        "status": "BLOCKED",
                        "reason": "not_in_staff_confirmed_1a_correspondence_upload_scope",
                    }
                )
                continue
            if correspondence and correspondence.get("status") == "BLOCKED":
                blocked.append({**base, "status": "BLOCKED", "reason": correspondence.get("reason")})
                continue
            if correspondence and correspondence.get("status") == "OFFICE":
                source = str(correspondence["source_path"])
                source_suffix = Path(source).suffix.lower()
                office_paths = refresh_output_paths(item_record, run_id, source_suffix)
                items.append(
                    {
                        **base,
                        **correspondence_evidence,
                        "source_selection_rule": RUNTIME_CONFIRMED_1A_UPLOAD_SCOPE,
                        "refresh_source_kind": "md_office_original",
                        "source_original_path": source,
                        "output_office_path": office_paths["output_office_path"],
                        "exported_pdf_path": office_paths["exported_pdf_path"],
                        "fallback_reason": "",
                    }
                )
            else:
                blocked.append(
                    {
                        **base,
                        "status": "BLOCKED",
                        "reason": f"staff_confirmed_correspondence_unusable:{correspondence.get('reason') if correspondence else suffix}",
                    }
                )
    plan = {
        "generated_at": utc_now(),
        "run_id": run_id,
        "semester_policy": CURRENT_SEMESTER_POLICY,
        "semester_policy_evidence": SEM1_TIMELINE_EVIDENCE,
        "date_policy_source": "pending_codex_runtime",
        "date_replacement_policy": None,
        "manual_review_items": [],
        "source_course": extraction.get("source_course"),
        "confirmed_correspondence_decision": confirmed_decision,
        "summary": {
            "items": len(items),
            "office_original_count": sum(1 for item in items if item["refresh_source_kind"] in OFFICE_REFRESH_SOURCE_KINDS),
            "md_correspondence_count": sum(1 for item in [*items, *blocked] if item.get("correspondence_id")),
            "runtime_confirmed_correspondence_count": sum(1 for item in items if item.get("correspondence_staff_confirmed")),
            "runtime_confirmed_upload_scope": RUNTIME_CONFIRMED_1A_UPLOAD_SCOPE,
            "md_office_source_count": sum(1 for item in items if item["refresh_source_kind"] == "md_office_original"),
            "title_match_count": sum(1 for item in items if item["refresh_source_kind"] == "title_office_original"),
            "pdf_fallback_count": sum(1 for item in items if item["refresh_source_kind"] == "pdf_fallback"),
            "md_blocked_count": sum(1 for item in blocked if item.get("correspondence_id")),
            "blocked_count": len(blocked),
        },
        "items": items,
        "blocked_items": blocked,
    }
    return plan


def compact_text(value: str, limit: int = 3200) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    return value[:limit]


def extract_ooxml_text_snippet(path: Path, limit: int = 3200) -> str:
    path = resolve_repo_path(path)
    if path.suffix.lower() not in OFFICE_SUFFIXES or not path.exists():
        return ""
    parts: list[str] = []
    try:
        with zipfile.ZipFile(path, "r") as archive:
            for name in archive.namelist():
                if not name.endswith(".xml"):
                    continue
                try:
                    xml_text = archive.read(name).decode("utf-8", errors="ignore")
                except Exception:
                    continue
                text_bits = re.findall(r">([^<>]{2,})<", xml_text)
                if text_bits:
                    parts.append(" ".join(text_bits))
                if sum(len(part) for part in parts) >= limit:
                    break
    except zipfile.BadZipFile:
        return ""
    return compact_text(" ".join(parts), limit)


def extract_pdf_text_snippet(path: Path, limit: int = 3200) -> str:
    path = resolve_repo_path(path)
    if path.suffix.lower() != ".pdf" or not path.exists():
        return ""
    try:
        return compact_text(extract_pdf_text_for_semester_check(path), limit)
    except Exception:
        return ""


def source_text_snippet(path_text: str, limit: int = 3200) -> str:
    path = resolve_repo_path(path_text)
    if path.suffix.lower() in OFFICE_SUFFIXES:
        return extract_ooxml_text_snippet(path, limit)
    if path.suffix.lower() == ".pdf":
        return extract_pdf_text_snippet(path, limit)
    return ""


def visual_date_evidence_from_audit_report(audit_report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not audit_report:
        return []
    evidence: list[dict[str, Any]] = []
    for record in as_list(audit_report.get("results")):
        hits = as_list(record.get("visible_needs_review_hits"))
        if not hits:
            continue
        evidence.append(
            {
                "resource_name": record.get("resource_name", ""),
                "upload_path": record.get("upload_path", ""),
                "item_report_dir": record.get("item_report_dir", ""),
                "visible_needs_review_hits": [
                    {
                        "surface": hit.get("surface", ""),
                        "kind": hit.get("kind", ""),
                        "value": hit.get("value", ""),
                        "snippet": compact_text(str(hit.get("snippet") or ""), 420),
                        "project_embedded_visual_strategy": embedded_visual_date_exception_reason(hit) or "",
                    }
                    for hit in hits[:30]
                    if isinstance(hit, dict)
                ],
            }
        )
    return evidence


def embedded_visual_date_exception_reason(hit: dict[str, Any]) -> str:
    if str(hit.get("surface") or "") != "visual_ocr":
        return ""
    snippet = str(hit.get("snippet") or "")
    lowered = snippet.casefold()
    if any(marker in lowered for marker in AUTHORITATIVE_VISUAL_DATE_SNIPPET_MARKERS):
        return ""
    if any(marker in lowered for marker in NON_AUTHORITATIVE_VISUAL_DATE_SNIPPET_MARKERS):
        return "non_authoritative_file_browser_or_ui_timestamp"
    if any(marker in lowered for marker in MOODLE_BREADCRUMB_SCREENSHOT_MARKERS):
        return "non_authoritative_old_moodle_breadcrumb_screenshot"
    return ""


def semantic_review_rule_for_hit(hit: dict[str, Any], reason: str) -> dict[str, str]:
    snippet = str(hit.get("snippet") or "")
    lowered = snippet.casefold()
    if any(marker in lowered for marker in NON_AUTHORITATIVE_VISUAL_DATE_SNIPPET_MARKERS):
        if "date modified" in lowered:
            snippet_contains = "Date modified"
        elif "unitycrashhandler" in lowered:
            snippet_contains = "UnityCrashHandler"
        elif "windows batch file" in lowered:
            snippet_contains = "Windows Batch File"
        elif "_data" in lowered:
            snippet_contains = "_Data"
        else:
            snippet_contains = "File folder" if "file folder" in lowered else str(hit.get("value") or "")
    elif any(marker in lowered for marker in MOODLE_BREADCRUMB_SCREENSHOT_MARKERS):
        snippet_contains = "Group Project Topic Selection" if "group project topic selection" in lowered else "DASE7137"
    else:
        snippet_contains = compact_text(snippet, 120)
    return {
        "surface": str(hit.get("surface") or "visual_ocr"),
        "kind": str(hit.get("kind") or ""),
        "value": str(hit.get("value") or ""),
        "snippet_contains": snippet_contains,
        "reason": reason,
        "classification": EMBEDDED_VISUAL_DATE_AUDIT_CLASSIFICATION,
    }


def build_embedded_visual_date_semantic_review(audit_report: dict[str, Any], *, run_id: str) -> dict[str, Any]:
    reviews: list[dict[str, Any]] = []
    for record in as_list(audit_report.get("results")):
        allowed_hits: list[dict[str, str]] = []
        reasons: set[str] = set()
        for hit in as_list(record.get("visible_needs_review_hits")):
            if not isinstance(hit, dict):
                continue
            reason = embedded_visual_date_exception_reason(hit)
            if not reason:
                continue
            allowed_hits.append(semantic_review_rule_for_hit(hit, reason))
            reasons.add(reason)
        if not allowed_hits:
            continue
        reviews.append(
            {
                "status": "APPROVED",
                "approved": True,
                "resource_name": record.get("resource_name", ""),
                "upload_path": record.get("upload_path", ""),
                "expected_uploaded_filename": record.get("expected_uploaded_filename", ""),
                "policy": "embedded_visual_date_modification_strategy",
                "classification": EMBEDDED_VISUAL_DATE_AUDIT_CLASSIFICATION,
                "reason": "; ".join(sorted(reasons)),
                "date_basis": CURRENT_SEMESTER_POLICY,
                "review_evidence": {
                    "strategy": EMBEDDED_VISUAL_DATE_STRATEGY,
                    "item_report_dir": record.get("item_report_dir", ""),
                    "existing_or_added_current_semester_example_required": True,
                    "authoritative_visual_dates_must_remain_blocked": True,
                },
                "allowed_hits": allowed_hits,
            }
        )
    return {
        "generated_at": utc_now(),
        "run_id": run_id,
        "policy": "embedded_visual_date_modification_strategy",
        "classification": EMBEDDED_VISUAL_DATE_AUDIT_CLASSIFICATION,
        "reviews": reviews,
        "summary": {"reviews": len(reviews), "allowed_hits": sum(len(review["allowed_hits"]) for review in reviews)},
    }


def semester_evidence_summary() -> dict[str, Any]:
    build_report_path = resolve_repo_path(SEM1_TIMELINE_EVIDENCE)
    build_report_summary: Any = {}
    if build_report_path.exists():
        try:
            payload = json.loads(build_report_path.read_text(encoding="utf-8"))
            build_report_summary = {
                "semester": payload.get("semester"),
                "timeline": payload.get("timeline") or payload.get("schedule") or payload,
            }
        except Exception:
            build_report_summary = compact_text(build_report_path.read_text(encoding="utf-8", errors="ignore"), 2400)
    semester_pdf = ROOT / "Dates of Semesters 2026-2027.pdf"
    semester_pdf_text = extract_pdf_text_snippet(semester_pdf, 2400) if semester_pdf.exists() else ""
    return {
        "required_semester_policy": CURRENT_SEMESTER_POLICY,
        "timeline_evidence_path": SEM1_TIMELINE_EVIDENCE,
        "timeline_evidence_summary": build_report_summary,
        "semester_pdf_path": repo_rel(semester_pdf) if semester_pdf.exists() else "",
        "semester_pdf_text_excerpt": semester_pdf_text,
    }


def build_codex_date_policy_input(
    extraction: dict[str, Any],
    source_selection_plan: dict[str, Any],
    before_contents: list[dict[str, Any]],
    *,
    run_id: str,
    visual_date_audit_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_dase_extraction_report(extraction)
    candidates = []
    for item in as_list(source_selection_plan.get("items")):
        source_path = str(item.get("source_original_path") or "")
        candidates.append(
            {
                "source_item_id": item.get("source_item_id"),
                "title": item.get("title"),
                "sectionnum": item.get("sectionnum"),
                "section_name": item.get("section_name"),
                "refresh_source_kind": item.get("refresh_source_kind"),
                "source_original_path": source_path,
                "backup_file_path": item.get("backup_file_path"),
                "correspondence_id": item.get("correspondence_id", ""),
                "correspondence_verdict": item.get("correspondence_verdict", ""),
                "correspondence_candidate_path": item.get("correspondence_candidate_path", ""),
                "source_selection_rule": item.get("source_selection_rule", ""),
                "source_evidence": item.get("source_evidence", []),
                "source_text_excerpt": source_text_snippet(source_path),
            }
        )
    return {
        "generated_at": utc_now(),
        "run_id": run_id,
        "planner_write_allowed": False,
        "target": {
            "course_id": SANDBOX_COURSE_ID,
            "course_url": SANDBOX_COURSE_URL,
            "old_live_course_write_allowed": False,
        },
        "source_course": extraction.get("source_course"),
        "semester_evidence": semester_evidence_summary(),
        "safety_rules": {
            "required_semester_policy": CURRENT_SEMESTER_POLICY,
            "visible_sem2_dates_forbidden": True,
            "codex_planning_only": True,
            "no_moodle_or_whatsapp_actions": True,
            "staff_confirmation_required_blocks_visible_upload": True,
        },
        "current_sandbox_readback_summary": {
            "section_count": len(flatten_sections(before_contents)),
            "module_count": len(flatten_modules(before_contents)),
        },
        "source_selection_summary": source_selection_plan.get("summary", {}),
        "refresh_candidates": candidates,
        "preblocked_items": as_list(source_selection_plan.get("blocked_items")),
        "project_wide_embedded_visual_date_strategy": EMBEDDED_VISUAL_DATE_STRATEGY,
        "visual_date_audit_evidence": visual_date_evidence_from_audit_report(visual_date_audit_evidence),
    }


def render_codex_date_policy_prompt(input_path: Path) -> str:
    return f"""You are Codex acting only as a planning agent for DASE7137 file refresh.

Read this JSON evidence file:
{repo_rel(input_path)}

Planning-only restrictions:
- Do not open Moodle, WhatsApp, Chrome, or any browser.
- Do not upload, delete, rename, save, or change Moodle.
- Do not modify local files.
- Return JSON only.

Goal:
Generate a fresh runtime date replacement policy for the provided DASE7137 refresh candidates.
Use the DASE backup evidence, current Sem1 schedule evidence, semester PDF text, file text excerpts,
and correspondence evidence. Do not reuse previous plans or hardcoded date mappings.

Required output JSON shape:
{{
  "planner_write_allowed": false,
  "semester_policy": "{CURRENT_SEMESTER_POLICY}",
  "source_course": {{"original_course_id": "{SOURCE_COURSE_ID}", "original_course_shortname": "{SOURCE_COURSE_SHORTNAME}"}},
  "date_replacement_policy": [
    {{
      "replacement_id": "stable-id",
      "source_item_id": "id from refresh_candidates",
      "old_text": "exact visible/source text to replace",
      "replacement_text": "Sem1-safe replacement text",
      "replacement_kind": "sem1_calendar_grounded|moodle_setting_required|remove_or_generalize_date",
      "evidence_refs": ["input evidence refs"],
      "current_schedule_basis": "why this replacement is valid",
      "staff_confirmation_required": false,
      "confidence": 0.0
    }}
  ],
  "refresh_items": [
    {{
      "source_item_id": "id from refresh_candidates",
      "decision": "refresh|no_replacement_needed|blocked|manual_review",
      "reason": "short reason",
      "evidence_refs": ["input evidence refs"]
    }}
  ],
  "blocked_items": [],
  "manual_review_items": []
}}

Rules:
- Concrete old-date to new-date mappings must come from your output, not from the runner.
- If a date cannot be grounded in the current first-semester schedule, use blocked or manual_review.
- If a replacement needs staff confirmation, set staff_confirmation_required=true.
- Do not output Sem2/Apr-May 2027 dates in replacement_text.
- Do not include Codex/Codex/agent/refresh/revision traces in replacement_text.
- Include one refresh_items decision for every refresh_candidates source_item_id.
- Apply project_wide_embedded_visual_date_strategy when visual_date_audit_evidence is present:
  old dates inside non-authoritative screenshots, form mockups, file-browser views, Moodle breadcrumbs,
  or UI examples do not need pixel edits solely for date scrubbing when current-semester text evidence
  exists or can be added around the instruction; authoritative course deadlines, release dates, event
  dates, Moodle settings, and only-instruction dates must be replaced, generalized, blocked, or sent to
  manual_review.
- If visual_date_audit_evidence shows an authoritative stale visible date that is absent from
  source_text_excerpt but present in rendered OCR, include a replacement for the exact OCR date string
  when it can be grounded to {CURRENT_SEMESTER_POLICY}; otherwise block or manual_review that item.
"""


def validate_date_replacement_policy_output(output: dict[str, Any], source_selection_plan: dict[str, Any]) -> None:
    source_course = output.get("source_course") or {}
    if (
        str(source_course.get("original_course_id") or "") != SOURCE_COURSE_ID
        or str(source_course.get("original_course_shortname") or "") != SOURCE_COURSE_SHORTNAME
    ):
        raise PipelineBlocked("Codex date policy source_course is not the active DASE7137 backup.")
    fullname = str(source_course.get("original_course_fullname") or "")
    if fullname and SOURCE_COURSE_CODE not in fullname:
        raise PipelineBlocked("Codex date policy source_course fullname is not DASE7137.")
    if output.get("planner_write_allowed") not in (False, None):
        raise PipelineBlocked("Codex date policy must be planning-only.")
    if output.get("semester_policy") != CURRENT_SEMESTER_POLICY:
        raise PipelineBlocked(f"Codex date policy must use {CURRENT_SEMESTER_POLICY}.")
    if "date_replacement_policy" not in output or not isinstance(output.get("date_replacement_policy"), list):
        raise PipelineBlocked("Codex output must include date_replacement_policy.")
    if "refresh_items" not in output or not isinstance(output.get("refresh_items"), list):
        raise PipelineBlocked("Codex output must include refresh_items.")

    known_ids = {str(item.get("source_item_id")) for item in as_list(source_selection_plan.get("items")) if item.get("source_item_id")}
    refresh_ids = set()
    for decision in as_list(output.get("refresh_items")):
        source_id = str(decision.get("source_item_id") or "")
        if source_id not in known_ids:
            raise PipelineBlocked(f"Codex refresh item references unknown source_item_id: {source_id}")
        if decision.get("decision") not in CODEX_REFRESH_DECISIONS:
            raise PipelineBlocked(f"Codex refresh decision is invalid: {decision.get('decision')}")
        if not isinstance(decision.get("evidence_refs"), list):
            raise PipelineBlocked("Codex refresh item requires evidence_refs list.")
        refresh_ids.add(source_id)
    missing_ids = sorted(known_ids - refresh_ids)
    if missing_ids:
        raise PipelineBlocked("Codex refresh_items missing source_item_id decisions: " + ", ".join(missing_ids))

    replacement_ids: set[str] = set()
    for replacement in as_list(output.get("date_replacement_policy")):
        required = [
            "replacement_id",
            "source_item_id",
            "old_text",
            "replacement_text",
            "replacement_kind",
            "evidence_refs",
            "current_schedule_basis",
            "staff_confirmation_required",
            "confidence",
        ]
        missing = [field for field in required if field not in replacement]
        if missing:
            raise PipelineBlocked("Codex replacement missing fields: " + ", ".join(missing))
        replacement_id = str(replacement.get("replacement_id") or "")
        if not replacement_id or replacement_id in replacement_ids:
            raise PipelineBlocked("Codex replacement_id must be non-empty and unique.")
        replacement_ids.add(replacement_id)
        source_id = str(replacement.get("source_item_id") or "")
        if source_id not in known_ids:
            raise PipelineBlocked(f"Codex replacement references unknown source_item_id: {source_id}")
        if not str(replacement.get("old_text") or ""):
            raise PipelineBlocked("Codex replacement old_text must be non-empty.")
        if replacement.get("replacement_kind") not in DATE_REPLACEMENT_KINDS:
            raise PipelineBlocked(f"Codex replacement_kind is invalid: {replacement.get('replacement_kind')}")
        if not isinstance(replacement.get("evidence_refs"), list) or not replacement.get("evidence_refs"):
            raise PipelineBlocked("Codex replacement requires non-empty evidence_refs.")
        if not str(replacement.get("current_schedule_basis") or ""):
            raise PipelineBlocked("Codex replacement requires current_schedule_basis.")
        if not isinstance(replacement.get("staff_confirmation_required"), bool):
            raise PipelineBlocked("Codex replacement staff_confirmation_required must be boolean.")
        confidence = replacement.get("confidence")
        if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
            raise PipelineBlocked("Codex replacement confidence must be a number from 0 to 1.")
        replacement_text = str(replacement.get("replacement_text") or "")
        lowered = replacement_text.casefold()
        if any(marker.casefold() in lowered for marker in STUDENT_VISIBLE_TRACE_MARKERS):
            raise PipelineBlocked("Codex replacement_text contains automation or refresh trace text.")
        if sem1_policy_hits_for_text(replacement_text):
            raise PipelineBlocked("Codex replacement_text violates the Sem1 policy.")


def run_codex_policy(
    prompt: str,
    *,
    run_id: str,
    raw_path: Path,
    session_label: str,
    extractor,
) -> dict[str, Any]:
    last_message = raw_path.with_suffix(".last_message.json")
    codex_prompt = prompt + "\n\nReturn JSON only. Do not perform Moodle writes, browser actions, or file edits."
    command = build_codex_exec_command(
        dict(os.environ),
        codex_prompt,
        stage="planner",
        cwd=ROOT,
        output_last_message=last_message,
    )
    proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    last_text = last_message.read_text(encoding="utf-8") if last_message.exists() else ""
    raw_path.write_text(
        "COMMAND_RETURN_CODE={code}\nSESSION_LABEL={label}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}\nLAST_MESSAGE:\n{last}\n".format(
            code=proc.returncode,
            label=f"codex-policy-{session_label}-{run_id}",
            stdout=proc.stdout,
            stderr=proc.stderr,
            last=last_text,
        ),
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise PipelineBlocked(f"Codex {session_label} planning failed; see raw output report.")
    output = extractor(last_text or proc.stdout)
    output["_codex"] = True
    return output


def apply_codex_date_policy(source_selection_plan: dict[str, Any], codex_output: dict[str, Any]) -> dict[str, Any]:
    validate_date_replacement_policy_output(codex_output, source_selection_plan)
    if codex_output.get("_codex"):
        policy_source = "codex_runtime"
    else:
        policy_source = "codex_runtime_fixture" if codex_output.get("_fixture") else "codex_runtime"
    replacements_by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for replacement in as_list(codex_output.get("date_replacement_policy")):
        replacements_by_item[str(replacement.get("source_item_id"))].append(replacement)
    decisions = {str(item.get("source_item_id")): item for item in as_list(codex_output.get("refresh_items"))}

    items: list[dict[str, Any]] = []
    blocked = list(as_list(source_selection_plan.get("blocked_items")))
    manual_review = list(as_list(source_selection_plan.get("manual_review_items")))
    codex_blocked_added = 0
    codex_manual_review_added = 0
    for item in as_list(source_selection_plan.get("items")):
        source_id = str(item.get("source_item_id") or "")
        decision = decisions[source_id]
        merged = {
            **item,
            "date_policy_source": policy_source,
            "date_replacements": replacements_by_item.get(source_id, []),
            "codex_refresh_decision": decision.get("decision"),
            "codex_refresh_reason": decision.get("reason", ""),
            "codex_refresh_evidence_refs": decision.get("evidence_refs", []),
        }
        if decision.get("decision") == "blocked":
            blocked.append({**merged, "status": "BLOCKED", "reason": decision.get("reason") or "codex_blocked"})
            codex_blocked_added += 1
        elif decision.get("decision") == "manual_review":
            manual_review.append({**merged, "status": "MANUAL_REVIEW", "reason": decision.get("reason") or "codex_manual_review"})
            codex_manual_review_added += 1
        else:
            items.append(merged)

    plan = {
        **source_selection_plan,
        "generated_at": utc_now(),
        "date_policy_source": policy_source,
        "date_replacement_policy": as_list(codex_output.get("date_replacement_policy")),
        "codex_policy_summary": {
            "refresh_items": len(as_list(codex_output.get("refresh_items"))),
            "replacement_count": len(as_list(codex_output.get("date_replacement_policy"))),
            "blocked_items": len(as_list(codex_output.get("blocked_items"))),
            "manual_review_items": len(as_list(codex_output.get("manual_review_items"))),
        },
        "items": items,
        "blocked_items": blocked + as_list(codex_output.get("blocked_items")),
        "manual_review_items": manual_review + as_list(codex_output.get("manual_review_items")),
        "summary": {
            **source_selection_plan.get("summary", {}),
            "items": len(items),
            "codex_replacement_count": len(as_list(codex_output.get("date_replacement_policy"))),
            "codex_blocked_count": codex_blocked_added + len(as_list(codex_output.get("blocked_items"))),
            "codex_manual_review_count": codex_manual_review_added + len(as_list(codex_output.get("manual_review_items"))),
        },
    }
    validate_refresh_plan(plan)
    return plan


def validate_refresh_plan(plan: dict[str, Any]) -> None:
    validate_dase_backup_identity({"source_course": plan.get("source_course") or {}})
    confirmed_decision = validate_confirmed_correspondence_decision(plan.get("confirmed_correspondence_decision") or {})
    confirmed_ids = decision_confirmed_ids(confirmed_decision)
    upload_scope = str(confirmed_decision.get("upload_scope") or "")
    if plan.get("semester_policy") != CURRENT_SEMESTER_POLICY:
        raise PipelineBlocked(f"DASE refresh plan must use {CURRENT_SEMESTER_POLICY}.")
    if plan.get("date_policy_source") not in {"codex_runtime", "codex_runtime_fixture"}:
        raise PipelineBlocked("DASE refresh plan requires a runtime date policy.")
    if not isinstance(plan.get("date_replacement_policy"), list):
        raise PipelineBlocked("DASE refresh plan requires date_replacement_policy from Codex.")
    for item in as_list(plan.get("items")):
        if item.get("semester_policy") != CURRENT_SEMESTER_POLICY:
            raise PipelineBlocked(f"DASE refresh item must use {CURRENT_SEMESTER_POLICY}.")
        if not isinstance(item.get("date_replacements"), list):
            raise PipelineBlocked("DASE refresh item requires per-item Codex date_replacements.")
        for field in (
            "source_original_path",
            "backup_file_path",
            "output_office_path",
            "exported_pdf_path",
            "correspondence_candidate_path",
        ):
            value = str(item.get(field) or "")
            if value:
                assert_no_forbidden_source(value)
        kind = item.get("refresh_source_kind")
        if kind not in REFRESH_SOURCE_KINDS:
            raise PipelineBlocked(f"Unknown refresh source kind: {kind}")
        if kind != "md_office_original":
            raise PipelineBlocked("DASE7137 upload scope is limited to staff-confirmed 1A correspondence Office originals.")
        if str(item.get("correspondence_id") or "") not in confirmed_ids:
            raise PipelineBlocked("DASE7137 refresh item is outside the staff-confirmed 1A correspondence upload scope.")
        if item.get("correspondence_staff_confirmed") is not True:
            raise PipelineBlocked("DASE7137 refresh item requires staff-confirmed correspondence evidence.")
        if item.get("source_selection_rule") != upload_scope:
            raise PipelineBlocked("DASE7137 refresh item must use the staff-confirmed 1A upload scope rule.")
        source_original = str(item.get("source_original_path") or "")
        if kind in OFFICE_REFRESH_SOURCE_KINDS:
            source_path = assert_allowed_refresh_source_path(source_original)
            if source_path.suffix.lower() not in OFFICE_SUFFIXES:
                raise PipelineBlocked("Office refresh source must be DOCX/PPTX/XLSX.")
            output_office = str(item.get("output_office_path") or "")
            if not output_office:
                raise PipelineBlocked("Office refresh item must carry output_office_path.")
            assert_dase_output_path(output_office)
            if Path(str(item.get("correspondence_candidate_path") or "")).suffix.lower() not in OFFICE_SUFFIXES:
                raise PipelineBlocked("MD Office refresh requires an Office correspondence candidate.")
        else:
            source_path = assert_allowed_refresh_source_path(source_original)
            if source_path.suffix.lower() != ".pdf":
                raise PipelineBlocked("PDF fallback source must be a PDF.")
            fallback_reason = str(item.get("fallback_reason") or "")
            export_failed_fallback = bool(item.get("output_office_path")) and (
                "office_export_failed" in fallback_reason or "LibreOffice binary missing" in fallback_reason
            )
            reliable_md_office = (
                item.get("correspondence_verdict") in RELIABLE_CORRESPONDENCE_VERDICTS
                and Path(str(item.get("correspondence_candidate_path") or "")).suffix.lower() in OFFICE_SUFFIXES
            )
            if item.get("office_match") and not export_failed_fallback:
                raise PipelineBlocked("PDF fallback is forbidden when a strong Office original match exists.")
            if reliable_md_office and not export_failed_fallback:
                raise PipelineBlocked("PDF fallback is forbidden when reliable MD Office correspondence exists.")
            if not fallback_reason:
                raise PipelineBlocked("PDF fallback requires fallback_reason evidence.")
        assert_dase_output_path(str(item.get("exported_pdf_path") or ""))


def convert_office_to_pdf(source: Path, target_pdf: Path) -> tuple[bool, str]:
    target_pdf.parent.mkdir(parents=True, exist_ok=True)
    if not SOFFICE.exists():
        return False, f"LibreOffice binary missing: {SOFFICE}"
    tmp_out = target_pdf.parent / "_soffice"
    tmp_out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("HOME", str(tmp_out / "home"))
    proc = subprocess.run(
        [str(SOFFICE), "--headless", "--convert-to", "pdf", "--outdir", str(tmp_out), str(source)],
        text=True,
        capture_output=True,
        timeout=600,
        env=env,
        check=False,
    )
    produced = tmp_out / f"{source.stem}.pdf"
    if proc.returncode != 0 or not produced.exists():
        return False, f"office_export_failed: {proc.stderr or proc.stdout}"
    shutil.copy2(produced, target_pdf)
    return True, ""


DATE_FRAGMENT_REGEXES = (
    re.compile(
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December|"
        r"Jan\.?|Feb\.?|Mar\.?|Apr\.?|Jun\.?|Jul\.?|Aug\.?|Sep\.?|Sept\.?|Oct\.?|Nov\.?|Dec\.?)"
        r"\s+\d{1,2}(?:st|nd|rd|th)?(?:,\s*|\s+)\d{4}(?:,\s+at\s+\d{1,2}:\d{2})?\b"
    ),
    re.compile(
        r"\b\d{1,2}(?:st|nd|rd|th)?\s+"
        r"(?:January|February|March|April|May|June|July|August|September|October|November|December|"
        r"Jan\.?|Feb\.?|Mar\.?|Apr\.?|Jun\.?|Jul\.?|Aug\.?|Sep\.?|Sept\.?|Oct\.?|Nov\.?|Dec\.?)"
        r"\s+\d{4}(?:,\s+\d{1,2}:\d{2}\s*(?:AM|PM|am|pm))?\b"
    ),
    re.compile(r"\b\d{1,2}/\d{1,2}/(?:20)?\d{2}\b"),
)


def replacement_text_pairs(replacement: dict[str, Any]) -> list[tuple[str, str]]:
    old = str(replacement.get("old_text") or "")
    new = str(replacement.get("replacement_text") or "")
    pairs: list[tuple[str, str]] = []
    if old and new:
        pairs.append((old, new))
    seen = set(pairs)
    for regex in DATE_FRAGMENT_REGEXES:
        old_hits = regex.findall(old)
        new_hits = regex.findall(new)
        if not old_hits or len(old_hits) != len(new_hits):
            continue
        for old_hit, new_hit in zip(old_hits, new_hits):
            pair = (old_hit, new_hit)
            if old_hit != new_hit and pair not in seen:
                pairs.append(pair)
                seen.add(pair)
    return pairs


def apply_ooxml_date_refresh(office_path: Path, replacement_records: list[dict[str, Any]]) -> dict[str, Any]:
    if office_path.suffix.lower() not in OFFICE_SUFFIXES:
        return {"status": "not_ooxml_office", "replacement_count": 0, "replacements": {}, "applied_replacement_ids": []}
    if not replacement_records:
        return {
            "status": "unchanged_no_codex_replacements",
            "replacement_count": 0,
            "replacements": {},
            "applied_replacement_ids": [],
        }
    replacements: dict[str, int] = {}
    applied_replacement_ids: list[str] = []
    replacement_count = 0
    tmp_path = office_path.with_suffix(office_path.suffix + ".tmp")
    try:
        with zipfile.ZipFile(office_path, "r") as src, zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as dst:
            for info in src.infolist():
                payload = src.read(info.filename)
                if info.filename.endswith(".xml"):
                    try:
                        text_payload = payload.decode("utf-8")
                    except UnicodeDecodeError:
                        text_payload = ""
                    if text_payload:
                        updated_payload = text_payload
                        for replacement in replacement_records:
                            for old, new in replacement_text_pairs(replacement):
                                if not old:
                                    continue
                                count = updated_payload.count(old)
                                if count:
                                    updated_payload = updated_payload.replace(old, new)
                                    replacements[old] = replacements.get(old, 0) + count
                                    applied_replacement_ids.append(str(replacement.get("replacement_id") or ""))
                                    replacement_count += count
                        if updated_payload != text_payload:
                            payload = updated_payload.encode("utf-8")
                dst.writestr(info, payload)
        tmp_path.replace(office_path)
    except zipfile.BadZipFile as exc:
        if tmp_path.exists():
            tmp_path.unlink()
        return {"status": "blocked_bad_ooxml_zip", "replacement_count": 0, "replacements": {}, "applied_replacement_ids": [], "blocker": str(exc)}
    return {
        "status": "modified" if replacement_count else "unchanged_no_known_stale_date_replacements",
        "replacement_count": replacement_count,
        "replacements": replacements,
        "applied_replacement_ids": sorted(set(applied_replacement_ids)),
    }


def materialize_refresh_plan(plan: dict[str, Any]) -> dict[str, Any]:
    validate_refresh_plan(plan)
    materialized: list[dict[str, Any]] = []
    for item in as_list(plan.get("items")):
        source = resolve_repo_path(str(item["source_original_path"]))
        exported_pdf = assert_dase_output_path(str(item["exported_pdf_path"]))
        updated = dict(item)
        replacement_records = as_list(item.get("date_replacements"))
        updated["office_edit_policy_replacements"] = replacement_records
        updated["office_edit_requires_staff_review"] = any(bool(record.get("staff_confirmation_required")) for record in replacement_records)
        if item["refresh_source_kind"] in OFFICE_REFRESH_SOURCE_KINDS:
            output_office = assert_dase_output_path(str(item["output_office_path"]))
            output_office.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, output_office)
            office_edit = apply_ooxml_date_refresh(output_office, replacement_records)
            updated["office_edit_status"] = office_edit.get("status")
            updated["office_edit_replacement_count"] = office_edit.get("replacement_count", 0)
            updated["office_edit_replacements"] = office_edit.get("replacements", {})
            updated["office_edit_applied_replacement_ids"] = office_edit.get("applied_replacement_ids", [])
            if office_edit.get("status") == "blocked_bad_ooxml_zip":
                updated["status"] = "BLOCKED"
                updated["blocker"] = str(office_edit.get("blocker") or "Office source could not be edited as OOXML.")
                materialized.append(updated)
                continue
            ok, blocker = convert_office_to_pdf(output_office, exported_pdf)
            if not ok:
                backup_pdf = resolve_repo_path(str(item.get("backup_file_path") or ""))
                if replacement_records:
                    updated["status"] = "BLOCKED"
                    updated["blocker"] = blocker + "; PDF fallback refused because Codex replacements would not be applied."
                elif backup_pdf.suffix.lower() == ".pdf" and backup_pdf.exists():
                    shutil.copy2(backup_pdf, exported_pdf)
                    updated["refresh_source_kind"] = "pdf_fallback"
                    updated["fallback_reason"] = blocker
                    updated["output_office_path"] = repo_rel(output_office)
                    updated["source_original_path"] = repo_rel(backup_pdf)
                    updated["source_selection_rule"] = "office_export_failed_pdf_fallback"
                else:
                    updated["status"] = "BLOCKED"
                    updated["blocker"] = blocker
            else:
                updated["output_office_path"] = repo_rel(output_office)
        else:
            if replacement_records:
                updated["status"] = "BLOCKED"
                updated["blocker"] = "pdf_fallback_replacements_not_supported"
                materialized.append(updated)
                continue
            exported_pdf.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, exported_pdf)
            updated["office_edit_status"] = ""
            updated["office_edit_replacement_count"] = 0
            updated["office_edit_replacements"] = {}
            updated["office_edit_applied_replacement_ids"] = []
            updated["office_edit_requires_staff_review"] = False
        if exported_pdf.exists() and updated.get("status") != "BLOCKED":
            updated["status"] = "MATERIALIZED"
            updated["exported_pdf_path"] = repo_rel(exported_pdf)
            updated["expected_uploaded_filename"] = exported_pdf.name
        materialized.append(updated)
    return {**plan, "items": materialized, "summary": {**plan.get("summary", {}), "materialized_count": sum(1 for item in materialized if item.get("status") == "MATERIALIZED")}}


def build_candidate_audit_manifest(refresh_plan: dict[str, Any], run_id: str, report_dir: Path) -> dict[str, Any]:
    upload_items = []
    for item in as_list(refresh_plan.get("items")):
        pdf_path = resolve_repo_path(str(item.get("exported_pdf_path") or ""))
        if item.get("status") != "MATERIALIZED" or not pdf_path.exists():
            continue
        upload_items.append(
            {
                "resource_name": item["title"],
                "upload_path": repo_rel(pdf_path),
                "expected_uploaded_filename": pdf_path.name,
                "sectionnum": str(item.get("sectionnum")),
                "section_name": item.get("section_name"),
                "semester_policy": item.get("semester_policy", CURRENT_SEMESTER_POLICY),
                "semester_policy_evidence": item.get("semester_policy_evidence", SEM1_TIMELINE_EVIDENCE),
            }
        )
    return {
        "generated_at": utc_now(),
        "batch_id": f"{BATCH_ID}-DATE-AUDIT-CANDIDATES",
        "run_id": run_id,
        "semester_policy": CURRENT_SEMESTER_POLICY,
        "semester_policy_evidence": SEM1_TIMELINE_EVIDENCE,
        "source_refresh_plan": repo_rel(report_dir / "04_refresh_plan.json"),
        "target": {"course_url": SANDBOX_COURSE_URL, "course_id": SANDBOX_COURSE_ID},
        "summary": {"upload_items": len(upload_items)},
        "upload_items": upload_items,
    }


def date_audit_records_by_path(audit_report: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    if not audit_report:
        return records
    for record in as_list(audit_report.get("results")):
        upload_path = str(record.get("upload_path") or "")
        if upload_path:
            records[repo_rel(upload_path)] = record
            records[f"filename::{Path(upload_path).name}"] = record
    return records


def section_title(section: dict[str, Any]) -> str:
    number = str(section.get("section_number") or "")
    return "General" if number == "0" else str(section.get("section_name") or f"Week {number}")


def source_evidence_for(record: dict[str, Any]) -> list[str]:
    return [f"dase_backup_activity:{record.get('moduleid')}"]


def shell_intro(modname: str) -> str:
    return (
        "<p>Sandbox empty activity shell from DASE7137 backup. "
        "Current settings require staff refresh before release.</p>"
        f"<p>Original Moodle type: {modname}. Student data, grades, submissions, attempts, "
        "old dates, and membership data were not copied.</p>"
    )


def build_activity_shell(record: dict[str, Any]) -> dict[str, Any]:
    sectionnum = str(record.get("section_number") or "0")
    simulation = dict(record.get("activity_simulation") or {})
    item = {
        "title": clean_student_title(str(record.get("title") or f"Untitled {record.get('modulename')}")),
        "sectionnum": sectionnum,
        "section_name": str(record.get("section_name") or ("General" if sectionnum == "0" else f"Week {sectionnum}")),
        "modname": str(record.get("modulename") or ""),
        "visibility": "Hide on course page",
        "intro": shell_intro(str(record.get("modulename") or "")),
        "source_evidence": source_evidence_for(record),
        "target_course_id": SANDBOX_COURSE_ID,
        "simulation_mode": simulation.get("simulation_mode", "hidden_shell_staff_refresh_required"),
        "staff_refresh_required": bool(simulation.get("staff_refresh_required", True)),
        "blocked_old_date_fields": as_list(simulation.get("blocked_old_date_fields")),
        "copy_policy": simulation.get("copy_policy", "do_not_copy_old_settings"),
    }
    if item["modname"] == "choice":
        item["choice_options"] = as_list(simulation.get("choice_options"))
    elif item["modname"] == "choicegroup":
        item["choicegroup_options"] = as_list(simulation.get("choicegroup_options"))
        item["skipped_orphaned_option_groupids"] = as_list(simulation.get("skipped_orphaned_option_groupids"))
    return item


def build_url_item(record: dict[str, Any]) -> dict[str, Any]:
    sectionnum = str(record.get("section_number") or "0")
    simulation = dict(record.get("activity_simulation") or {})
    return {
        "title": clean_student_title(str(record.get("title") or "Untitled URL")),
        "sectionnum": sectionnum,
        "section_name": str(record.get("section_name") or ("General" if sectionnum == "0" else f"Week {sectionnum}")),
        "modname": "url",
        "externalurl": str(record.get("externalurl") or ""),
        "visibility": "Hide on course page",
        "intro": shell_intro("url"),
        "source_evidence": source_evidence_for(record),
        "target_course_id": SANDBOX_COURSE_ID,
        "simulation_mode": simulation.get("simulation_mode", "hidden_real_url"),
        "copy_policy": simulation.get("copy_policy", "copy_backup_externalurl_only_with_generic_hidden_intro"),
        "blocked_old_date_fields": as_list(simulation.get("blocked_old_date_fields")),
    }


def target_group_base_name(backup_group_name: str) -> str:
    match = re.search(r"(\d+)", backup_group_name)
    if match:
        return f"Project Group {int(match.group(1))}"
    return clean_student_title(backup_group_name) or "Project Group"


def build_group_signup_scaffolds(extraction: dict[str, Any]) -> list[dict[str, Any]]:
    backup_groups = {
        str(group.get("backup_groupid")): group
        for group in as_list(extraction.get("group_records"))
        if str(group.get("backup_groupid") or "")
    }
    scaffolds: list[dict[str, Any]] = []
    for record in as_list(extraction.get("activity_records")):
        if record.get("modulename") != "choicegroup":
            continue
        simulation = dict(record.get("activity_simulation") or {})
        groups: list[dict[str, Any]] = []
        seen: set[str] = set()
        for option in as_list(simulation.get("choicegroup_options")):
            backup_groupid = str(option.get("backup_groupid") or "")
            backup_group = backup_groups.get(backup_groupid)
            if not backup_group or backup_groupid in seen:
                continue
            seen.add(backup_groupid)
            backup_name = str(backup_group.get("backup_group_name") or option.get("backup_group_name") or "")
            groups.append(
                {
                    "backup_groupid": backup_groupid,
                    "backup_group_name": backup_name,
                    "target_group_base_name": target_group_base_name(backup_name),
                    "maxanswers": parse_int(str(option.get("maxanswers") or 0), 0),
                    "member_count": int(backup_group.get("member_count") or 0),
                    "source_evidence": backup_group.get("source_evidence", []),
                }
            )
        scaffolds.append(
            {
                "title": clean_student_title(str(record.get("title") or "Project Grouping")),
                "sectionnum": str(record.get("section_number") or "0"),
                "section_name": str(record.get("section_name") or "General"),
                "visibility": "Hide on course page",
                "target_course_id": SANDBOX_COURSE_ID,
                "grouping_base_name": "DASE7137 Project Groups",
                "grouping_idnumber_base": "dase7137-project-groups-2026-2027-sem1",
                "run_scoped_names_required": True,
                "groups": groups,
                "group_count": len(groups),
                "maxanswers": max((parse_int(str(group.get("maxanswers") or 0), 0) for group in groups), default=0),
                "skipped_orphaned_option_groupids": as_list(simulation.get("skipped_orphaned_option_groupids")),
                "blocked_old_date_fields": as_list(simulation.get("blocked_old_date_fields")),
                "source_evidence": source_evidence_for(record),
                "copy_policy": "create_empty_sandbox_groups_and_grouping_only",
            }
        )
    return scaffolds


def build_target_model(
    extraction: dict[str, Any],
    refresh_plan: dict[str, Any],
    *,
    audit_report: dict[str, Any] | None = None,
    operator_excluded_items: set[str] | None = None,
) -> dict[str, Any]:
    validate_dase_extraction_report(extraction)
    validate_refresh_plan(refresh_plan)
    audit_by_path = date_audit_records_by_path(audit_report)
    excluded_items = {str(item).casefold() for item in as_list(operator_excluded_items) if str(item).strip()}
    file_resource_items: list[dict[str, Any]] = []
    blocked_items: list[dict[str, Any]] = list(as_list(refresh_plan.get("blocked_items")))
    for item in as_list(refresh_plan.get("items")):
        if item.get("status") == "BLOCKED":
            blocked_items.append({**item, "reason": item.get("blocker") or "materialization_blocked"})
            continue
        if excluded_items:
            title = clean_student_title(str(item.get("title") or ""))
            item_id = str(item.get("source_item_id") or "")
            if title.casefold() in excluded_items or item_id.casefold() in excluded_items:
                blocked_items.append({**item, "reason": "excluded_by_operator_request"})
                continue
        exported_pdf_text = str(item.get("exported_pdf_path") or "")
        audit = audit_by_path.get(repo_rel(exported_pdf_text)) or audit_by_path.get(f"filename::{Path(exported_pdf_text).name}")
        if not audit:
            blocked_items.append({**item, "reason": "date_audit_not_run_or_missing"})
            continue
        if audit.get("status") != "PASS_NO_STALE_VISIBLE_DATE" or audit.get("visible_needs_review_hits"):
            blocked_items.append({**item, "reason": "date_audit_not_pass", "date_audit_status": audit.get("status")})
            continue
        if item.get("office_edit_requires_staff_review"):
            blocked_items.append({**item, "reason": "office_refresh_requires_staff_review"})
            continue
        visible_text_value = " ".join(
            str(item.get(field) or "")
            for field in ("title", "expected_uploaded_filename", "section_name")
        )
        if any(marker.casefold() in visible_text_value.casefold() for marker in STUDENT_VISIBLE_TRACE_MARKERS):
            raise PipelineBlocked("DASE visible File resource title/filename contains automation or refresh trace text.")
        if item.get("refresh_source_kind") in OFFICE_REFRESH_SOURCE_KINDS:
            if item.get("office_edit_status") not in {"modified", "unchanged_no_known_stale_date_replacements", "unchanged_no_codex_replacements"}:
                blocked_items.append({**item, "reason": "missing_or_invalid_office_edit_evidence"})
                continue
            if not item.get("output_office_path"):
                blocked_items.append({**item, "reason": "missing_output_office_path"})
                continue
        source_pdf = resolve_repo_path(exported_pdf_text)
        if not source_pdf.exists():
            blocked_items.append({**item, "reason": "missing_materialized_pdf"})
            continue
        semester_check = sem1_policy_check_for_pdf(source_pdf)
        if semester_check.get("status") != "PASS_SEM1" or semester_check.get("forbidden_semester_markers"):
            blocked_items.append(
                {
                    **item,
                    "reason": "semester_policy_not_sem1",
                    "semester_policy_status": semester_check.get("status"),
                    "forbidden_semester_markers": semester_check.get("forbidden_semester_markers", []),
                }
            )
            continue
        upload_ready = DASE7137_BACKUP_UPLOAD_READY_ROOT / "stale_cleanup_files" / str(refresh_plan["run_id"]) / clean_upload_filename(str(item["title"]), ".pdf")
        upload_ready.parent.mkdir(parents=True, exist_ok=True)
        if upload_ready.resolve(strict=False) != source_pdf.resolve(strict=False):
            shutil.copy2(source_pdf, upload_ready)
        file_resource_items.append(
            {
                "title": item["title"],
                "sectionnum": str(item["sectionnum"]),
                "section_name": item["section_name"],
                "semester_policy": CURRENT_SEMESTER_POLICY,
                "semester_policy_evidence": SEM1_TIMELINE_EVIDENCE,
                "semester_policy_status": semester_check.get("status"),
                "forbidden_semester_markers": semester_check.get("forbidden_semester_markers", []),
                "audit_source_path": exported_pdf_text,
                "upload_path": repo_rel(upload_ready),
                "expected_uploaded_filename": upload_ready.name,
                "visibility": "Show on course page",
                "source_evidence": item.get("source_evidence", []),
                "date_audit_status": audit.get("status"),
                "refresh_source_kind": item.get("refresh_source_kind"),
                "source_original_path": item.get("source_original_path"),
                "output_office_path": item.get("output_office_path"),
                "exported_pdf_path": exported_pdf_text,
                "fallback_reason": item.get("fallback_reason", ""),
                "date_replacements": item.get("date_replacements", []),
                "codex_refresh_decision": item.get("codex_refresh_decision", ""),
                "codex_refresh_reason": item.get("codex_refresh_reason", ""),
                "codex_refresh_evidence_refs": item.get("codex_refresh_evidence_refs", []),
                "office_edit_status": item.get("office_edit_status", ""),
                "office_edit_replacement_count": item.get("office_edit_replacement_count", 0),
                "office_edit_replacements": item.get("office_edit_replacements", {}),
                "office_edit_policy_replacements": item.get("office_edit_policy_replacements", []),
                "office_edit_applied_replacement_ids": item.get("office_edit_applied_replacement_ids", []),
                "office_edit_requires_staff_review": item.get("office_edit_requires_staff_review", False),
                "correspondence_report_path": item.get("correspondence_report_path", ""),
                "correspondence_id": item.get("correspondence_id", ""),
                "correspondence_verdict": item.get("correspondence_verdict", ""),
                "correspondence_candidate_path": item.get("correspondence_candidate_path", ""),
                "correspondence_staff_confirmed": item.get("correspondence_staff_confirmed", False),
                "correspondence_upload_scope": item.get("correspondence_upload_scope", RUNTIME_CONFIRMED_1A_UPLOAD_SCOPE),
                "source_selection_rule": item.get("source_selection_rule", ""),
            }
        )

    activity_shell_items = [
        build_activity_shell(record)
        for record in as_list(extraction.get("activity_records"))
        if str(record.get("modulename") or "") in SAFE_ACTIVITY_SHELL_TYPES
    ]
    for record in as_list(extraction.get("material_records")):
        if record.get("modulename") == "folder":
            activity_shell_items.append(build_activity_shell(record))
    unsupported_activity_items = [
        {**record, "reason": "unsupported_activity_type"}
        for record in as_list(extraction.get("activity_records"))
        if str(record.get("modulename") or "") not in SAFE_ACTIVITY_SHELL_TYPES
    ]
    url_activity_items = [build_url_item(record) for record in as_list(extraction.get("url_records"))]
    group_signup_scaffold_items = build_group_signup_scaffolds(extraction)
    model = {
        "planner_write_allowed": False,
        "semester_policy": CURRENT_SEMESTER_POLICY,
        "semester_policy_evidence": SEM1_TIMELINE_EVIDENCE,
        "target": {
            "course_id": SANDBOX_COURSE_ID,
            "course_url": SANDBOX_COURSE_URL,
            "target_sandbox_course_id": SANDBOX_COURSE_ID,
            "source_course_code": SOURCE_COURSE_CODE,
            "source_course_id": SOURCE_COURSE_ID,
            "source_course_shortname": SOURCE_COURSE_SHORTNAME,
            "old_live_course_write_allowed": False,
        },
        "source_course": extraction.get("source_course"),
        "target_sections": [
            {
                "sectionnum": str(section.get("section_number")),
                "title": section_title(section),
                "source_evidence": [f"dase_backup_section:{section.get('section_id')}"],
            }
            for section in as_list(extraction.get("sections"))
        ],
        "file_resource_items": file_resource_items,
        "activity_shell_items": activity_shell_items,
        "url_activity_items": url_activity_items,
        "group_signup_scaffold_items": group_signup_scaffold_items,
        "page_placeholder_items": [],
        "blocked_items": blocked_items + unsupported_activity_items,
        "manual_review_items": [],
        "confirmed_correspondence_decision": refresh_plan.get("confirmed_correspondence_decision", {}),
        "refresh_plan_summary": refresh_plan.get("summary", {}),
    }
    validate_target_model(model)
    return model


def validate_target_model(model: dict[str, Any]) -> None:
    required = [
        "semester_policy",
        "target",
        "source_course",
        "target_sections",
        "file_resource_items",
        "activity_shell_items",
        "url_activity_items",
        "group_signup_scaffold_items",
        "blocked_items",
        "manual_review_items",
    ]
    missing = [field for field in required if field not in model]
    if missing:
        raise PipelineBlocked("DASE target model missing required fields: " + ", ".join(missing))
    validate_dase_backup_identity({"source_course": model.get("source_course") or {}})
    if model.get("semester_policy") != CURRENT_SEMESTER_POLICY:
        raise PipelineBlocked(f"DASE target model must use {CURRENT_SEMESTER_POLICY}.")
    target = model.get("target") or {}
    if str(target.get("course_id")) != SANDBOX_COURSE_ID or target.get("course_url") != SANDBOX_COURSE_URL:
        raise PipelineBlocked("Refusing non-sandbox DASE target model.")
    if str(target.get("source_course_id")) != SOURCE_COURSE_ID or target.get("old_live_course_write_allowed"):
        raise PipelineBlocked("Refusing DASE target model that allows old/live writes.")
    if model.get("planner_write_allowed") not in (False, None):
        raise PipelineBlocked("Planner must not be write-enabled.")
    sections = as_list(model.get("target_sections"))
    section_nums = {str(section.get("sectionnum")) for section in sections}
    if section_nums != {str(index) for index in range(SOURCE_SECTION_COUNT)}:
        raise PipelineBlocked(f"DASE target sections must be exactly source sections 0-{SOURCE_SECTION_COUNT - 1}.")
    if {str(section.get("sectionnum")): str(section.get("title")) for section in sections}.get("0") != "General":
        raise PipelineBlocked("DASE target section 0 must be General.")
    reject_disallowed_setting_keys(model.get("file_resource_items"))
    reject_disallowed_setting_keys(model.get("activity_shell_items"))
    reject_disallowed_setting_keys(model.get("url_activity_items"), allow_externalurl=True)
    reject_disallowed_setting_keys(model.get("group_signup_scaffold_items"))
    confirmed_decision = validate_confirmed_correspondence_decision(model.get("confirmed_correspondence_decision") or {})
    confirmed_ids = decision_confirmed_ids(confirmed_decision)
    upload_scope = str(confirmed_decision.get("upload_scope") or "")
    for item in as_list(model.get("file_resource_items")):
        for field in ("audit_source_path", "upload_path", "source_original_path", "output_office_path", "exported_pdf_path"):
            value = str(item.get(field) or "")
            if value:
                assert_no_forbidden_source(value)
        if item.get("visibility") != "Show on course page":
            raise PipelineBlocked("DASE File resources must be visible only after date audit PASS.")
        if item.get("date_audit_status") != "PASS_NO_STALE_VISIBLE_DATE":
            raise PipelineBlocked("DASE File resources require PASS_NO_STALE_VISIBLE_DATE.")
        if item.get("refresh_source_kind") != "md_office_original":
            raise PipelineBlocked("DASE visible File resources are limited to staff-confirmed 1A correspondence Office originals.")
        if str(item.get("correspondence_id") or "") not in confirmed_ids:
            raise PipelineBlocked("DASE visible File resource is outside the staff-confirmed 1A correspondence upload scope.")
        if item.get("correspondence_staff_confirmed") is not True:
            raise PipelineBlocked("DASE visible File resource requires staff-confirmed correspondence evidence.")
        if item.get("source_selection_rule") != upload_scope:
            raise PipelineBlocked("DASE visible File resource must use the staff-confirmed 1A upload scope rule.")
        if item.get("semester_policy") != CURRENT_SEMESTER_POLICY:
            raise PipelineBlocked(f"DASE visible File resources must use {CURRENT_SEMESTER_POLICY}.")
        if item.get("semester_policy_status") != "PASS_SEM1" or item.get("forbidden_semester_markers"):
            raise PipelineBlocked("DASE visible File resource violates the Sem1 policy.")
        visible_text_value = " ".join(
            str(item.get(field) or "")
            for field in ("title", "expected_uploaded_filename", "section_name")
        )
        lowered_visible = visible_text_value.casefold()
        if any(marker.casefold() in lowered_visible for marker in STUDENT_VISIBLE_TRACE_MARKERS):
            raise PipelineBlocked("DASE visible File resource title/filename contains automation or refresh trace text.")
        if item.get("refresh_source_kind") in OFFICE_REFRESH_SOURCE_KINDS:
            if item.get("office_edit_status") not in {"modified", "unchanged_no_known_stale_date_replacements", "unchanged_no_codex_replacements"}:
                raise PipelineBlocked("DASE Office-sourced visible File resource requires valid Office edit evidence.")
            if not item.get("output_office_path"):
                raise PipelineBlocked("DASE Office-sourced visible File resource requires output_office_path.")
        assert_dase_output_path(str(item.get("upload_path")))
    for item in as_list(model.get("activity_shell_items")):
        if item.get("modname") not in SAFE_ACTIVITY_SHELL_TYPES:
            raise PipelineBlocked(f"Unsupported DASE activity shell type: {item.get('modname')}")
        if item.get("visibility") != "Hide on course page":
            raise PipelineBlocked("DASE activity shells must be hidden.")
        if item.get("modname") == "choice" and not item.get("choice_options"):
            raise PipelineBlocked("DASE choice simulation requires backup option labels and limits.")
        if item.get("modname") == "choicegroup" and not item.get("choicegroup_options"):
            raise PipelineBlocked("DASE choicegroup simulation requires backup group options.")
    for item in as_list(model.get("url_activity_items")):
        if item.get("modname") != "url" or not item.get("externalurl"):
            raise PipelineBlocked("DASE URL items must be real URL activities with an externalurl.")
        if item.get("visibility") != "Hide on course page":
            raise PipelineBlocked("DASE URL items must remain hidden.")
    for scaffold in as_list(model.get("group_signup_scaffold_items")):
        if scaffold.get("visibility") != "Hide on course page":
            raise PipelineBlocked("DASE group signup scaffolds must remain hidden.")
        if str(scaffold.get("target_course_id")) != SANDBOX_COURSE_ID:
            raise PipelineBlocked("DASE group signup scaffold must target sandbox 105330.")
        groups = as_list(scaffold.get("groups"))
        if len(groups) != 14 or int(scaffold.get("group_count") or 0) != 14:
            raise PipelineBlocked("DASE Project Grouping must scaffold exactly 14 named sandbox groups.")
        if any(int(group.get("member_count") or 0) != 0 for group in groups):
            raise PipelineBlocked("DASE group signup scaffold must not copy members.")
        if "394120" not in {str(item) for item in as_list(scaffold.get("skipped_orphaned_option_groupids"))}:
            raise PipelineBlocked("DASE group signup scaffold must record skipped orphaned option 394120.")


def build_upload_manifest(model: dict[str, Any], run_id: str, report_dir: Path) -> dict[str, Any]:
    validate_target_model(model)
    upload_items = []
    for item in as_list(model.get("file_resource_items")):
        upload_path = resolve_repo_path(str(item["upload_path"]))
        upload_items.append(
            {
                "source_path": item["upload_path"],
                "audit_source_path": item.get("audit_source_path"),
                "upload_path": repo_rel(upload_path),
                "expected_uploaded_filename": item["expected_uploaded_filename"],
                "resource_name": item["title"],
                "moodle_destination": item.get("section_name"),
                "section_name": item.get("section_name"),
                "sectionnum": str(item.get("sectionnum")),
                "include_reason": "date-audit-clean DASE7137 1A file resource from staff-confirmed correspondence scope",
                "planning_decision": "dase7137_visible_file_resource",
                "visible": True,
                "expected_visibility": "Show on course page",
                "intro": "<p>Course material restored from DASE7137 backup after stale-date cleanup.</p>",
                "semester_policy": item.get("semester_policy"),
                "semester_policy_evidence": item.get("semester_policy_evidence"),
                "semester_policy_status": item.get("semester_policy_status"),
                "forbidden_semester_markers": item.get("forbidden_semester_markers", []),
                "source_evidence": item.get("source_evidence", []),
                "date_audit_status": item.get("date_audit_status"),
                "refresh_source_kind": item.get("refresh_source_kind"),
                "source_original_path": item.get("source_original_path"),
                "output_office_path": item.get("output_office_path"),
                "exported_pdf_path": item.get("exported_pdf_path"),
                "fallback_reason": item.get("fallback_reason", ""),
                "date_replacements": item.get("date_replacements", []),
                "codex_refresh_decision": item.get("codex_refresh_decision", ""),
                "codex_refresh_reason": item.get("codex_refresh_reason", ""),
                "codex_refresh_evidence_refs": item.get("codex_refresh_evidence_refs", []),
                "office_edit_status": item.get("office_edit_status", ""),
                "office_edit_replacement_count": item.get("office_edit_replacement_count", 0),
                "office_edit_replacements": item.get("office_edit_replacements", {}),
                "office_edit_policy_replacements": item.get("office_edit_policy_replacements", []),
                "office_edit_applied_replacement_ids": item.get("office_edit_applied_replacement_ids", []),
                "office_edit_requires_staff_review": item.get("office_edit_requires_staff_review", False),
                "correspondence_report_path": item.get("correspondence_report_path", ""),
                "correspondence_id": item.get("correspondence_id", ""),
                "correspondence_verdict": item.get("correspondence_verdict", ""),
                "correspondence_candidate_path": item.get("correspondence_candidate_path", ""),
                "correspondence_staff_confirmed": item.get("correspondence_staff_confirmed", False),
                "correspondence_upload_scope": item.get("correspondence_upload_scope", RUNTIME_CONFIRMED_1A_UPLOAD_SCOPE),
                "source_selection_rule": item.get("source_selection_rule", ""),
                "file": {
                    "path": repo_rel(upload_path),
                    "exists": upload_path.exists(),
                    "size_bytes": upload_path.stat().st_size if upload_path.exists() else 0,
                    "filename": upload_path.name,
                },
            }
        )
    return {
        "generated_at": utc_now(),
        "batch_id": BATCH_ID,
        "run_id": run_id,
        "semester_policy": CURRENT_SEMESTER_POLICY,
        "semester_policy_evidence": SEM1_TIMELINE_EVIDENCE,
        "confirmed_correspondence_decision": model.get("confirmed_correspondence_decision", {}),
        "approval_phrase": f"Approved Moodle sandbox upload {BATCH_ID} RUN={run_id}",
        "source_target_model": repo_rel(report_dir / "06_target_model.json"),
        "target": {
            "course_url": SANDBOX_COURSE_URL,
            "course_id": SANDBOX_COURSE_ID,
            "visibility": "Show on course page",
            "write_policy": "debug_auto_approve_sandbox_only",
        },
        "summary": {
            "upload_items": len(upload_items),
            "missing_upload_files": sum(1 for item in upload_items if not resolve_repo_path(item["upload_path"]).exists()),
        },
        "upload_items": upload_items,
        "guardrails": [
            "Only write to sandbox course 105330.",
            "Only upload the first 10 staff-confirmed DASE7137 1A file-level correspondences.",
            "The rebuilt sandbox directory may be incomplete while source files are incomplete.",
            "Visible files must come from the confirmed correspondence Office originals after refresh.",
            "Only upload DASE7137 date-audit-clean visible PDFs.",
            f"Visible files must satisfy {CURRENT_SEMESTER_POLICY}; Sem2 2027 dates are blocked.",
            "Do not use legacy non-DASE backup sources.",
        ],
    }


def existing_resource_keys(contents: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for section in contents:
        sectionnum = str(section.get("section") if section.get("section") is not None else section.get("sectionnum") or "")
        for module in as_list(section.get("modules")):
            if str(module.get("modname") or "") != "resource":
                continue
            keys.add((clean_student_title(str(module.get("name") or "")), sectionnum, "resource"))
    return keys


def filter_upload_manifest_to_missing(upload_manifest: dict[str, Any], contents: list[dict[str, Any]]) -> dict[str, Any]:
    existing = existing_resource_keys(contents)
    manifest = deepcopy(upload_manifest)
    kept: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in as_list(upload_manifest.get("upload_items")):
        key = (clean_student_title(str(item.get("resource_name") or "")), str(item.get("sectionnum") or ""), "resource")
        if key in existing:
            skipped.append({**item, "skip_reason": "already_present_in_sandbox_readback"})
        else:
            kept.append({**item, "include_reason": f"{item.get('include_reason', 'date-audit-clean')} and missing from sandbox readback"})
    manifest["upload_items"] = kept
    summary = dict(manifest.get("summary") or {})
    summary.update(
        {
            "upload_items": len(kept),
            "skipped_existing_items": len(skipped),
            "missing_upload_files": sum(1 for item in kept if not resolve_repo_path(item["upload_path"]).exists()),
        }
    )
    manifest["summary"] = summary
    manifest["skipped_existing_items"] = skipped
    manifest.setdefault("guardrails", []).append("Preserve existing sandbox File resources; upload only missing compliant resources.")
    return manifest


def activity_item_id(item: dict[str, Any], prefix: str = "activity") -> str:
    return f"{prefix}:{item.get('modname')}:{item.get('sectionnum')}:{safe_name(str(item.get('title') or item.get('name') or 'item')).lower()}"


def file_backfill_item_id(item: dict[str, Any], prefix: str = "file") -> str:
    return f"{prefix}:{item.get('sectionnum')}:{safe_name(str(item.get('resource_name') or item.get('title') or 'file')).lower()}"


def expected_activity_decision(item: dict[str, Any]) -> str:
    modname = str(item.get("modname") or "")
    if modname == "url":
        return "create_hidden_url"
    if modname == "choice":
        return "create_hidden_choice"
    if modname == "choicegroup":
        return "create_hidden_choicegroup"
    return "create_hidden_activity"


def activity_policy_candidates(activity_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in as_list(activity_manifest.get("activity_shell_items")):
        decision = expected_activity_decision(item)
        candidates.append(
            {
                "activity_item_id": activity_item_id(item),
                "title": item.get("title"),
                "modname": item.get("modname"),
                "sectionnum": str(item.get("sectionnum")),
                "section_name": item.get("section_name"),
                "visibility": item.get("visibility"),
                "baseline_decision": decision,
                "allowed_decisions": [decision, "skip_existing", "block_staff_review"],
                "staff_refresh_required": item.get("staff_refresh_required", False),
                "blocked_old_date_fields": item.get("blocked_old_date_fields", []),
                "choice_options": item.get("choice_options", []),
                "choicegroup_options": item.get("choicegroup_options", []),
                "choicegroup_target_group_names": item.get("choicegroup_target_group_names", []),
                "skipped_orphaned_option_groupids": item.get("skipped_orphaned_option_groupids", []),
                "source_evidence": item.get("source_evidence", []),
            }
        )
    for item in as_list(activity_manifest.get("url_activity_items")):
        decision = "create_hidden_url"
        candidates.append(
            {
                "activity_item_id": activity_item_id(item, "url"),
                "title": item.get("title"),
                "modname": "url",
                "sectionnum": str(item.get("sectionnum")),
                "section_name": item.get("section_name"),
                "visibility": item.get("visibility"),
                "externalurl": item.get("externalurl"),
                "baseline_decision": decision,
                "allowed_decisions": [decision, "skip_existing", "block_staff_review"],
                "blocked_old_date_fields": item.get("blocked_old_date_fields", []),
                "source_evidence": item.get("source_evidence", []),
            }
        )
    for scaffold in as_list(activity_manifest.get("group_signup_scaffold_items")):
        decision = "create_hidden_group_scaffold"
        candidates.append(
            {
                "activity_item_id": activity_item_id({"modname": "group_scaffold", **scaffold}, "group_scaffold"),
                "title": scaffold.get("title"),
                "modname": "group_scaffold",
                "sectionnum": str(scaffold.get("sectionnum")),
                "section_name": scaffold.get("section_name"),
                "visibility": scaffold.get("visibility"),
                "baseline_decision": decision,
                "allowed_decisions": [decision, "skip_existing", "block_staff_review"],
                "group_count": scaffold.get("group_count"),
                "maxanswers": scaffold.get("maxanswers"),
                "groups": [
                    {
                        "backup_groupid": group.get("backup_groupid"),
                        "backup_group_name": group.get("backup_group_name"),
                        "target_group_name": group.get("target_group_name"),
                        "member_count": group.get("member_count", 0),
                    }
                    for group in as_list(scaffold.get("groups"))
                ],
                "skipped_orphaned_option_groupids": scaffold.get("skipped_orphaned_option_groupids", []),
                "blocked_old_date_fields": scaffold.get("blocked_old_date_fields", []),
                "source_evidence": scaffold.get("source_evidence", []),
            }
        )
    return candidates


def file_backfill_policy_candidates(upload_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in as_list(upload_manifest.get("upload_items")):
        candidates.append(
            {
                "file_item_id": file_backfill_item_id(item),
                "resource_name": item.get("resource_name"),
                "sectionnum": str(item.get("sectionnum")),
                "section_name": item.get("section_name"),
                "current_status": "missing_from_sandbox_readback",
                "baseline_decision": "upload_missing_visible_file",
                "allowed_decisions": ["upload_missing_visible_file", "block_staff_review"],
                "date_audit_status": item.get("date_audit_status"),
                "refresh_source_kind": item.get("refresh_source_kind"),
                "source_original_path": item.get("source_original_path"),
                "upload_path": item.get("upload_path"),
                "source_evidence": item.get("source_evidence", []),
            }
        )
    for item in as_list(upload_manifest.get("skipped_existing_items")):
        candidates.append(
            {
                "file_item_id": file_backfill_item_id(item),
                "resource_name": item.get("resource_name"),
                "sectionnum": str(item.get("sectionnum")),
                "section_name": item.get("section_name"),
                "current_status": "already_present_in_sandbox_readback",
                "baseline_decision": "skip_existing",
                "allowed_decisions": ["skip_existing", "block_staff_review"],
                "date_audit_status": item.get("date_audit_status"),
                "refresh_source_kind": item.get("refresh_source_kind"),
                "source_original_path": item.get("source_original_path"),
                "upload_path": item.get("upload_path"),
                "source_evidence": item.get("source_evidence", []),
            }
        )
    return candidates


def build_codex_activity_policy_input(
    extraction: dict[str, Any],
    upload_manifest: dict[str, Any],
    activity_manifest: dict[str, Any],
    before_contents: list[dict[str, Any]],
    *,
    run_id: str,
    preserve_existing_modules: bool,
) -> dict[str, Any]:
    activity_candidates = activity_policy_candidates(activity_manifest)
    file_candidates = file_backfill_policy_candidates(upload_manifest)
    return {
        "generated_at": utc_now(),
        "run_id": run_id,
        "planner_write_allowed": False,
        "moodle_write_authorized": False,
        "policy_kind": "codex_activity_creation_and_diff_backfill",
        "source_course": extraction.get("source_course"),
        "target": {
            "course_id": SANDBOX_COURSE_ID,
            "course_url": SANDBOX_COURSE_URL,
            "old_live_course_write_allowed": False,
        },
        "semester_policy": CURRENT_SEMESTER_POLICY,
        "preserve_existing_modules": preserve_existing_modules,
        "current_sandbox_readback_summary": {
            "section_count": len(flatten_sections(before_contents)),
            "module_count": len(flatten_modules(before_contents)),
            "resource_count": sum(1 for module in flatten_modules(before_contents) if module.get("modname") == "resource"),
            "modules": [
                {
                    "id": module.get("id"),
                    "name": module.get("name"),
                    "modname": module.get("modname"),
                    "visible": module.get("visible"),
                    "sectionnum": module.get("sectionnum"),
                }
                for module in flatten_modules(before_contents)
            ],
        },
        "backup_activity_summary": extraction.get("summary", {}).get("activity_counts", {}),
        "activity_creation_candidates": activity_candidates,
        "file_backfill_candidates": file_candidates,
        "required_output_contract": {
            "planner_write_allowed": False,
            "moodle_write_authorized": False,
            "activity_creation_decisions": "one decision for every activity_creation_candidates.activity_item_id",
            "file_backfill_decisions": "one decision for every file_backfill_candidates.file_item_id",
            "allowed_activity_decisions": sorted(CODEX_ACTIVITY_CREATION_DECISIONS),
            "allowed_file_backfill_decisions": sorted(CODEX_FILE_BACKFILL_DECISIONS),
        },
        "safety_rules": {
            "sandbox_only": True,
            "activity_and_url_visibility_must_be_hidden": True,
            "do_not_copy_student_data_submissions_grades_attempts_or_memberships": True,
            "disable_old_date_fields": True,
            "file_backfill_must_preserve_existing_modules_when_requested": preserve_existing_modules,
            "writes_still_require_exact_action_time_approval": True,
        },
    }


def render_codex_activity_policy_prompt(input_path: Path) -> str:
    return f"""You are Codex acting only as a planning agent for Moodle activity creation and file-diff backfill.

Read this JSON evidence file:
{repo_rel(input_path)}

Planning-only restrictions:
- Do not open Moodle, WhatsApp, Chrome, or any browser.
- Do not upload, delete, rename, save, or change Moodle.
- Do not modify local files.
- Return JSON only.

Goal:
Independently decide which proposed hidden activities/URLs/group scaffolds and missing File-resource backfill
items should proceed for the sandbox. This decision artifact must be reusable for other course migration tasks:
use the evidence fields and allowed decisions, not DASE-specific hardcoded titles.

Required output JSON shape:
{{
  "planner_write_allowed": false,
  "moodle_write_authorized": false,
  "policy_kind": "codex_activity_creation_and_diff_backfill",
  "source_course": {{"original_course_id": "{SOURCE_COURSE_ID}", "original_course_shortname": "{SOURCE_COURSE_SHORTNAME}"}},
  "target": {{"course_id": "{SANDBOX_COURSE_ID}", "course_url": "{SANDBOX_COURSE_URL}", "old_live_course_write_allowed": false}},
  "preserve_existing_modules": true,
  "activity_creation_decisions": [
    {{
      "activity_item_id": "id from activity_creation_candidates",
      "decision": "create_hidden_activity|create_hidden_choice|create_hidden_choicegroup|create_hidden_group_scaffold|create_hidden_url|skip_existing|block_staff_review",
      "target_course_id": "{SANDBOX_COURSE_ID}",
      "visibility": "Hide on course page",
      "disable_old_dates": true,
      "copy_student_data": false,
      "reason": "short reason grounded in input evidence",
      "evidence_refs": ["input evidence refs"]
    }}
  ],
  "file_backfill_decisions": [
    {{
      "file_item_id": "id from file_backfill_candidates",
      "decision": "upload_missing_visible_file|skip_existing|block_staff_review",
      "target_course_id": "{SANDBOX_COURSE_ID}",
      "reason": "short reason grounded in input evidence",
      "evidence_refs": ["input evidence refs"]
    }}
  ],
  "blocked_items": []
}}

Rules:
- Include exactly one activity_creation_decisions item for every activity_creation_candidates item.
- Include exactly one file_backfill_decisions item for every file_backfill_candidates item.
- Use only each candidate's allowed_decisions.
- Hidden activity creation may be approved only when visibility remains "Hide on course page", old date fields are disabled, and no student data, grades, submissions, attempts, question contents, or memberships are copied.
- File backfill may upload only candidates that are missing from sandbox readback and have PASS_NO_STALE_VISIBLE_DATE.
- Existing sandbox resources should be skipped, not replaced, when preserve_existing_modules is true.
- block_staff_review any candidate whose safe controls, source evidence, current state, or destination is uncertain.
- This planning does not authorize Moodle writes; exact action-time approval is still required later.
"""


def validate_activity_policy_output(output: dict[str, Any], policy_input: dict[str, Any]) -> None:
    if output.get("planner_write_allowed") not in (False, None):
        raise PipelineBlocked("Codex activity/diff policy must be planning-only.")
    if output.get("moodle_write_authorized") not in (False, None):
        raise PipelineBlocked("Codex activity/diff policy must not authorize Moodle writes.")
    if output.get("policy_kind") != "codex_activity_creation_and_diff_backfill":
        raise PipelineBlocked("Codex activity/diff policy has the wrong policy_kind.")
    source_course = output.get("source_course") or {}
    if (
        str(source_course.get("original_course_id") or "") != SOURCE_COURSE_ID
        or str(source_course.get("original_course_shortname") or "") != SOURCE_COURSE_SHORTNAME
    ):
        raise PipelineBlocked("Codex activity/diff policy source_course is not the active DASE7137 backup.")
    fullname = str(source_course.get("original_course_fullname") or "")
    if fullname and SOURCE_COURSE_CODE not in fullname:
        raise PipelineBlocked("Codex activity/diff policy source_course fullname is not DASE7137.")
    target = output.get("target") or {}
    if str(target.get("course_id") or "") != SANDBOX_COURSE_ID or str(target.get("course_url") or "") != SANDBOX_COURSE_URL:
        raise PipelineBlocked("Codex activity/diff policy targets the wrong Moodle course.")
    if target.get("old_live_course_write_allowed"):
        raise PipelineBlocked("Codex activity/diff policy must not allow old/live course writes.")

    activity_candidates = {str(item.get("activity_item_id")): item for item in as_list(policy_input.get("activity_creation_candidates"))}
    file_candidates = {str(item.get("file_item_id")): item for item in as_list(policy_input.get("file_backfill_candidates"))}
    activity_decisions = {str(item.get("activity_item_id")): item for item in as_list(output.get("activity_creation_decisions"))}
    file_decisions = {str(item.get("file_item_id")): item for item in as_list(output.get("file_backfill_decisions"))}
    if set(activity_decisions) != set(activity_candidates):
        missing = sorted(set(activity_candidates) - set(activity_decisions))
        extra = sorted(set(activity_decisions) - set(activity_candidates))
        raise PipelineBlocked(f"Codex activity decisions do not match candidates; missing={missing}, extra={extra}")
    if set(file_decisions) != set(file_candidates):
        missing = sorted(set(file_candidates) - set(file_decisions))
        extra = sorted(set(file_decisions) - set(file_candidates))
        raise PipelineBlocked(f"Codex file backfill decisions do not match candidates; missing={missing}, extra={extra}")

    for item_id, decision in activity_decisions.items():
        candidate = activity_candidates[item_id]
        value = str(decision.get("decision") or "")
        if value not in CODEX_ACTIVITY_CREATION_DECISIONS or value not in as_list(candidate.get("allowed_decisions")):
            raise PipelineBlocked(f"Codex activity decision is not allowed for {item_id}: {value}")
        if value.startswith("create_hidden"):
            if str(decision.get("target_course_id") or "") != SANDBOX_COURSE_ID:
                raise PipelineBlocked(f"Codex activity decision targets the wrong course: {item_id}")
            if decision.get("visibility") != "Hide on course page":
                raise PipelineBlocked(f"Codex activity decision must keep activity hidden: {item_id}")
            if decision.get("disable_old_dates") is not True:
                raise PipelineBlocked(f"Codex activity decision must disable old date fields: {item_id}")
            if decision.get("copy_student_data") not in (False, None):
                raise PipelineBlocked(f"Codex activity decision must not copy student data: {item_id}")
        if not as_list(decision.get("evidence_refs")):
            raise PipelineBlocked(f"Codex activity decision requires evidence_refs: {item_id}")
    for item_id, decision in file_decisions.items():
        candidate = file_candidates[item_id]
        value = str(decision.get("decision") or "")
        if value not in CODEX_FILE_BACKFILL_DECISIONS or value not in as_list(candidate.get("allowed_decisions")):
            raise PipelineBlocked(f"Codex file backfill decision is not allowed for {item_id}: {value}")
        if value == "upload_missing_visible_file":
            if candidate.get("current_status") != "missing_from_sandbox_readback":
                raise PipelineBlocked(f"Codex may only upload missing file candidates: {item_id}")
            if candidate.get("date_audit_status") != "PASS_NO_STALE_VISIBLE_DATE":
                raise PipelineBlocked(f"Codex file upload decision requires date audit PASS: {item_id}")
            if str(decision.get("target_course_id") or "") != SANDBOX_COURSE_ID:
                raise PipelineBlocked(f"Codex file decision targets the wrong course: {item_id}")
        if value == "skip_existing" and candidate.get("current_status") != "already_present_in_sandbox_readback":
            raise PipelineBlocked(f"Codex may only skip existing readback-backed file candidates: {item_id}")
        if not as_list(decision.get("evidence_refs")):
            raise PipelineBlocked(f"Codex file backfill decision requires evidence_refs: {item_id}")


def apply_activity_policy_to_manifests(
    upload_manifest: dict[str, Any],
    activity_manifest: dict[str, Any],
    policy_output: dict[str, Any],
    policy_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    activity_decisions = {str(item.get("activity_item_id")): item for item in as_list(policy_output.get("activity_creation_decisions"))}
    file_decisions = {str(item.get("file_item_id")): item for item in as_list(policy_output.get("file_backfill_decisions"))}

    upload_result = deepcopy(upload_manifest)
    kept_uploads: list[dict[str, Any]] = []
    blocked_files: list[dict[str, Any]] = []
    for item in as_list(upload_manifest.get("upload_items")):
        decision = file_decisions[file_backfill_item_id(item)]
        if decision.get("decision") == "upload_missing_visible_file":
            kept_uploads.append({**item, "codex_file_backfill_decision": decision})
        else:
            blocked_files.append({**item, "codex_file_backfill_decision": decision})
    upload_result["upload_items"] = kept_uploads
    upload_result["codex_blocked_file_backfill_items"] = blocked_files
    upload_result["skipped_existing_items"] = [
        {**item, "codex_file_backfill_decision": file_decisions.get(file_backfill_item_id(item), {})}
        for item in as_list(upload_manifest.get("skipped_existing_items"))
    ]
    upload_summary = dict(upload_result.get("summary") or {})
    upload_summary.update(
        {
            "upload_items": len(kept_uploads),
            "codex_blocked_file_backfill_items": len(blocked_files),
        }
    )
    upload_result["summary"] = upload_summary
    upload_result["codex_activity_policy"] = {
        "path": repo_rel(policy_path),
        "policy_kind": policy_output.get("policy_kind"),
        "file_backfill_decisions": len(file_decisions),
    }

    activity_result = deepcopy(activity_manifest)
    blocked_activities: list[dict[str, Any]] = []
    for bucket, prefix in [
        ("activity_shell_items", "activity"),
        ("url_activity_items", "url"),
    ]:
        kept: list[dict[str, Any]] = []
        for item in as_list(activity_manifest.get(bucket)):
            decision = activity_decisions[activity_item_id(item, prefix)]
            if str(decision.get("decision") or "").startswith("create_hidden"):
                kept.append({**item, "codex_activity_decision": decision})
            else:
                blocked_activities.append({**item, "codex_activity_decision": decision})
        activity_result[bucket] = kept
    kept_scaffolds: list[dict[str, Any]] = []
    for scaffold in as_list(activity_manifest.get("group_signup_scaffold_items")):
        decision = activity_decisions[activity_item_id({"modname": "group_scaffold", **scaffold}, "group_scaffold")]
        if decision.get("decision") == "create_hidden_group_scaffold":
            kept_scaffolds.append({**scaffold, "codex_activity_decision": decision})
        else:
            blocked_activities.append({**scaffold, "codex_activity_decision": decision})
    activity_result["group_signup_scaffold_items"] = kept_scaffolds
    activity_result["codex_blocked_activity_items"] = blocked_activities
    activity_summary = dict(activity_result.get("summary") or {})
    activity_summary.update(
        {
            "activity_shell_items": len(activity_result.get("activity_shell_items") or []),
            "url_activity_items": len(activity_result.get("url_activity_items") or []),
            "group_signup_scaffold_items": len(kept_scaffolds),
            "group_signup_groups": sum(len(as_list(scaffold.get("groups"))) for scaffold in kept_scaffolds),
            "codex_blocked_activity_items": len(blocked_activities),
        }
    )
    activity_result["summary"] = activity_summary
    activity_result["codex_activity_policy"] = {
        "path": repo_rel(policy_path),
        "policy_kind": policy_output.get("policy_kind"),
        "activity_creation_decisions": len(activity_decisions),
    }
    return upload_result, activity_result


def run_scoped_group_signup_scaffolds(scaffolds: list[dict[str, Any]], run_id: str) -> list[dict[str, Any]]:
    scoped: list[dict[str, Any]] = []
    id_suffix = safe_name(run_id, "run").lower()
    for scaffold in scaffolds:
        item = dict(scaffold)
        item["grouping_name"] = f"{scaffold.get('grouping_base_name', 'DASE7137 Project Groups')} ({run_id})"
        item["grouping_idnumber"] = f"{scaffold.get('grouping_idnumber_base', 'dase7137-project-groups')}-{id_suffix}"[:100]
        groups = []
        for group in as_list(scaffold.get("groups")):
            base = str(group.get("target_group_base_name") or target_group_base_name(str(group.get("backup_group_name") or "")))
            groups.append({**group, "target_group_name": f"{base} ({run_id})"})
        item["groups"] = groups
        item["group_count"] = len(groups)
        scoped.append(item)
    return scoped


def attach_group_targets_to_activity_items(activity_items: list[dict[str, Any]], scaffolds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_title = {str(scaffold.get("title") or ""): scaffold for scaffold in scaffolds}
    resolved: list[dict[str, Any]] = []
    for activity in activity_items:
        item = dict(activity)
        if item.get("modname") == "choicegroup":
            scaffold = by_title.get(str(item.get("title") or ""))
            if scaffold:
                item["choicegroup_target_group_names"] = [group["target_group_name"] for group in as_list(scaffold.get("groups"))]
                item["choicegroup_grouping_name"] = scaffold.get("grouping_name", "")
        resolved.append(item)
    return resolved


def build_activity_shell_manifest(model: dict[str, Any], run_id: str, report_dir: Path) -> dict[str, Any]:
    validate_target_model(model)
    group_signup_scaffolds = run_scoped_group_signup_scaffolds(as_list(model.get("group_signup_scaffold_items")), run_id)
    activity_shell_items = attach_group_targets_to_activity_items(as_list(model.get("activity_shell_items")), group_signup_scaffolds)
    return {
        "generated_at": utc_now(),
        "batch_id": BATCH_ID,
        "run_id": run_id,
        "semester_policy": CURRENT_SEMESTER_POLICY,
        "semester_policy_evidence": SEM1_TIMELINE_EVIDENCE,
        "approval_phrase": f"Approved Moodle sandbox full activity simulation {BATCH_ID} RUN={run_id}",
        "source_target_model": repo_rel(report_dir / "06_target_model.json"),
        "target": {
            "course_url": SANDBOX_COURSE_URL,
            "course_id": SANDBOX_COURSE_ID,
            "write_policy": "debug_auto_approve_sandbox_only",
        },
        "summary": {
            "activity_shell_items": len(activity_shell_items),
            "url_activity_items": len(as_list(model.get("url_activity_items"))),
            "group_signup_scaffold_items": len(group_signup_scaffolds),
            "group_signup_groups": sum(len(as_list(scaffold.get("groups"))) for scaffold in group_signup_scaffolds),
            "page_placeholder_items": len(as_list(model.get("page_placeholder_items"))),
        },
        "activity_shell_items": activity_shell_items,
        "url_activity_items": as_list(model.get("url_activity_items")),
        "group_signup_scaffold_items": group_signup_scaffolds,
        "page_placeholder_items": as_list(model.get("page_placeholder_items")),
        "guardrails": [
            "Only write hidden DASE7137 activity shells/URLs in sandbox 105330.",
            "Create only run-id scoped empty sandbox groups/grouping for Project Grouping.",
            "Do not copy student data, grades, submissions, attempts, group members, or old deadline settings.",
            "Do not create File-resource placeholders.",
        ],
    }


def build_section_setup_manifest(model: dict[str, Any], run_id: str) -> dict[str, Any]:
    validate_target_model(model)
    return {
        "generated_at": utc_now(),
        "batch_id": BATCH_ID,
        "run_id": run_id,
        "semester_policy": CURRENT_SEMESTER_POLICY,
        "semester_policy_evidence": SEM1_TIMELINE_EVIDENCE,
        "approval_phrase": f"Approved Moodle sandbox section setup {BATCH_ID} RUN={run_id}",
        "target": {
            "course_url": SANDBOX_COURSE_URL,
            "course_id": SANDBOX_COURSE_ID,
            "write_policy": "debug_auto_approve_sandbox_only",
        },
        "summary": {"target_sections": len(model["target_sections"]), "non_general_sections": max(0, len(model["target_sections"]) - 1)},
        "target_sections": model["target_sections"],
        "guardrails": [
            "Section setup must run after strong clear and before uploads/activity creation.",
            f"Do not proceed with Moodle writes unless APIM readback confirms sections 0-{len(model['target_sections']) - 1} with expected titles.",
        ],
    }


def build_module_clear_plan(contents: list[dict[str, Any]], target_sections: list[dict[str, Any]], *, run_id: str) -> dict[str, Any]:
    modules = flatten_modules(contents)
    sections = flatten_sections(contents)
    target_sectionnums = [str(section.get("sectionnum")) for section in target_sections]
    plan = {
        "generated_at": utc_now(),
        "batch_id": BATCH_ID,
        "run_id": run_id,
        "approval_phrase": f"Approved Moodle sandbox module clear {BATCH_ID} RUN={run_id}",
        "target": {
            "course_url": SANDBOX_COURSE_URL,
            "course_id": SANDBOX_COURSE_ID,
            "sandbox_only": True,
        },
        "clear_scope": {
            "delete_all_modules": True,
            "preserve_existing_sections": True,
            "delete_sections": False,
            "old_live_moodle_write_allowed": False,
        },
        "expected_sectionnums": target_sectionnums,
        "existing_sectionnums": [str(section.get("sectionnum")) for section in sections],
        "delete_modules": modules,
        "summary": {
            "module_count": len(modules),
            "file_resource_count": sum(1 for module in modules if module.get("modname") == "resource"),
            "section_count": len(sections),
            "expected_section_count": len(target_sectionnums),
        },
    }
    validate_module_clear_plan(plan)
    return plan


def build_preserve_existing_modules_plan(contents: list[dict[str, Any]], target_sections: list[dict[str, Any]], *, run_id: str) -> dict[str, Any]:
    modules = flatten_modules(contents)
    sections = flatten_sections(contents)
    target_sectionnums = [str(section.get("sectionnum")) for section in target_sections]
    return {
        "generated_at": utc_now(),
        "batch_id": BATCH_ID,
        "run_id": run_id,
        "approval_phrase": "",
        "target": {
            "course_url": SANDBOX_COURSE_URL,
            "course_id": SANDBOX_COURSE_ID,
            "sandbox_only": True,
        },
        "clear_scope": {
            "delete_all_modules": False,
            "preserve_existing_modules": True,
            "preserve_existing_sections": True,
            "delete_sections": False,
            "old_live_moodle_write_allowed": False,
        },
        "expected_sectionnums": target_sectionnums,
        "existing_sectionnums": [str(section.get("sectionnum")) for section in sections],
        "delete_modules": [],
        "summary": {
            "module_count": len(modules),
            "file_resource_count": sum(1 for module in modules if module.get("modname") == "resource"),
            "section_count": len(sections),
            "expected_section_count": len(target_sectionnums),
            "preserve_existing_modules": True,
        },
    }


def validate_module_clear_plan(plan: dict[str, Any]) -> None:
    target = plan.get("target") or {}
    if target.get("course_url") != SANDBOX_COURSE_URL or str(target.get("course_id")) != SANDBOX_COURSE_ID:
        raise PipelineBlocked("Refusing non-sandbox module-clear plan.")
    scope = plan.get("clear_scope") or {}
    if not scope.get("delete_all_modules") or not scope.get("preserve_existing_sections"):
        raise PipelineBlocked("Refusing incomplete module-clear scope.")
    if scope.get("delete_sections") or scope.get("old_live_moodle_write_allowed"):
        raise PipelineBlocked("Refusing module-clear plan that deletes sections or allows old/live writes.")
    expected = f"Approved Moodle sandbox module clear {plan.get('batch_id')} RUN={plan.get('run_id')}"
    if plan.get("approval_phrase") != expected:
        raise PipelineBlocked("Refusing module-clear plan with a non-canonical approval phrase.")


def assert_module_clear_verified(contents: list[dict[str, Any]], target_sections: list[dict[str, Any]]) -> None:
    modules = flatten_modules(contents)
    if modules:
        raise PipelineBlocked("Module-clear verification failed: module_count is not 0.")
    if not flatten_sections(contents):
        raise PipelineBlocked("Module-clear verification failed: no sections remained.")


def expected_sectionnums_present(contents: list[dict[str, Any]], target_sections: list[dict[str, Any]]) -> bool:
    existing = {str(section.get("sectionnum")) for section in flatten_sections(contents)}
    expected = {str(section.get("sectionnum")) for section in target_sections}
    return expected.issubset(existing)


def build_section_rename_plan(contents: list[dict[str, Any]], model: dict[str, Any], run_id: str) -> dict[str, Any]:
    validate_target_model(model)
    live_by_num = {str(section.get("sectionnum")): section for section in flatten_sections(contents)}
    renames = []
    for target in model["target_sections"]:
        sectionnum = str(target["sectionnum"])
        live = live_by_num.get(sectionnum)
        if not live:
            raise PipelineBlocked(f"Cannot rename missing sandbox section {sectionnum}.")
        old_name = str(live.get("name") or "")
        new_name = str(target["title"])
        if old_name != new_name:
            renames.append(
                {
                    "section_id": live.get("section_id"),
                    "sectionnum": sectionnum,
                    "old_name": old_name,
                    "new_name": new_name,
                    "source_evidence": target.get("source_evidence", []),
                }
            )
    return {
        "generated_at": utc_now(),
        "batch_id": f"{BATCH_ID}-SECTION-SETUP",
        "run_id": run_id,
        "semester_policy": CURRENT_SEMESTER_POLICY,
        "semester_policy_evidence": SEM1_TIMELINE_EVIDENCE,
        "approval_phrase": f"Approved Moodle sandbox simple rename {BATCH_ID}-SECTION-SETUP RUN={run_id}",
        "target": {
            "course_url": SANDBOX_COURSE_URL,
            "course_id": SANDBOX_COURSE_ID,
            "write_policy": "debug_auto_approve_sandbox_only",
        },
        "summary": {"section_renames": len(renames), "resource_renames": 0},
        "section_renames": renames,
        "resource_renames": [],
    }


def assert_section_titles_verified(contents: list[dict[str, Any]], model: dict[str, Any]) -> None:
    live_by_num = {str(section.get("sectionnum")): str(section.get("name") or "") for section in flatten_sections(contents)}
    mismatches = [
        {"sectionnum": str(section["sectionnum"]), "expected": section["title"], "actual": live_by_num.get(str(section["sectionnum"]))}
        for section in model["target_sections"]
        if live_by_num.get(str(section["sectionnum"])) != section["title"]
    ]
    if mismatches:
        raise PipelineBlocked("Section title verification failed: " + json.dumps(mismatches, ensure_ascii=False))


def run_date_audit(candidate_manifest_path: Path, semantic_review_path: Path | None = None) -> dict[str, Any]:
    command = [
        sys.executable,
        str(ROOT / "course_automation_probe" / "audit_upload_date_surfaces.py"),
        "--manifest",
        str(candidate_manifest_path),
        "--item-timeout-seconds",
        "600",
    ]
    if semantic_review_path:
        command.extend(["--semantic-review", str(semantic_review_path)])
    result = run_command(command)
    if not result.get("ok"):
        raise PipelineBlocked("DASE date audit command failed.")
    report_dirs = sorted(COURSE_AUTOMATION_REPORT_DIR.glob("date_surface_audit_*"), key=lambda path: path.stat().st_mtime)
    if not report_dirs:
        raise PipelineBlocked("DASE date audit did not produce a report directory.")
    report_path = report_dirs[-1] / "date_surface_audit.json"
    return load_json(report_path)


def execute_upload(manifest_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    if not manifest.get("upload_items"):
        return {"ok": True, "status": "SKIPPED_EMPTY", "saves_count": 0}
    return run_command(
        [
            sys.executable,
            str(ROOT / "course_agent_app" / "run_moodle_course_file_upload_batch.py"),
            "--manifest",
            str(manifest_path),
            "--execute-approved-change",
            "--approval-phrase",
            str(manifest["approval_phrase"]),
        ]
    )


def execute_activity_shells(manifest_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    if (
        not manifest.get("activity_shell_items")
        and not manifest.get("url_activity_items")
        and not manifest.get("page_placeholder_items")
        and not manifest.get("group_signup_scaffold_items")
    ):
        return {"ok": True, "status": "SKIPPED_EMPTY", "saves_count": 0}
    return run_command(
        [
            sys.executable,
            str(ROOT / "course_agent_app" / "run_moodle_course_activity_shell_batch.py"),
            "--manifest",
            str(manifest_path),
            "--execute-approved-change",
            "--approval-phrase",
            str(manifest["approval_phrase"]),
        ]
    )


def execute_module_clear(plan_path: Path, plan: dict[str, Any]) -> dict[str, Any]:
    return run_command(
        [
            sys.executable,
            str(ROOT / "course_agent_app" / "run_moodle_course_module_clear_preserve_sections.py"),
            "--plan",
            str(plan_path),
            "--execute-approved-change",
            "--approval-phrase",
            str(plan["approval_phrase"]),
        ]
    )


def execute_section_renames(plan_path: Path, plan: dict[str, Any]) -> dict[str, Any]:
    if not plan.get("section_renames"):
        return {"ok": True, "status": "SKIPPED_EMPTY", "saves_count": 0}
    return run_command(
        [
            sys.executable,
            str(ROOT / "course_agent_app" / "run_moodle_course_simple_rename.py"),
            "--plan",
            str(plan_path),
            "--execute-approved-change",
            "--approval-phrase",
            str(plan["approval_phrase"]),
        ]
    )


def execute_section_setup(manifest_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    return run_command(
        [
            sys.executable,
            str(ROOT / "course_agent_app" / "run_moodle_course_section_setup.py"),
            "--manifest",
            str(manifest_path),
            "--execute-approved-change",
            "--approval-phrase",
            str(manifest["approval_phrase"]),
        ]
    )


def assert_clear_verified(contents: list[dict[str, Any]]) -> None:
    if flatten_modules(contents):
        raise PipelineBlocked("Clear verification failed: module_count is not 0.")


def assert_strong_clear_verified(contents: list[dict[str, Any]]) -> None:
    modules = flatten_modules(contents)
    sections = flatten_sections(contents)
    general_sections = [section for section in sections if str(section.get("sectionnum")) == "0"]
    if modules:
        raise PipelineBlocked("Strong-clear verification failed: module_count is not 0.")
    if len(sections) != 1 or len(general_sections) != 1:
        raise PipelineBlocked("Strong-clear verification failed: course is not General-only.")


def main() -> None:
    parser = argparse.ArgumentParser(description="DASE7137 backup strong-clear rebuild with Office-first refresh planning.")
    parser.add_argument("--backup-path", default=str(DEFAULT_BACKUP_PATH))
    parser.add_argument("--debug-auto-approve", action="store_true")
    parser.add_argument("--debug-execute-sandbox-writes", action="store_true")
    parser.add_argument("--no-execute", action="store_true")
    parser.add_argument("--contents-fixture", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--materialize-refresh", action="store_true")
    parser.add_argument("--run-date-audit", action="store_true")
    parser.add_argument("--date-audit-report", default="")
    parser.add_argument("--date-audit-semantic-review", default="")
    parser.add_argument("--auto-embedded-visual-date-review", action="store_true")
    parser.add_argument("--run-codex-date-policy", action="store_true")
    parser.add_argument("--date-policy-output", default="", help="Validated Codex date policy JSON to consume.")
    parser.add_argument("--run-codex-activity-policy", action="store_true")
    parser.add_argument("--activity-policy-output", default="", help="Validated Codex activity creation and diff-backfill policy JSON to consume.")
    parser.add_argument(
        "--visual-date-audit-evidence",
        default="",
        help="Optional previous date-surface audit JSON whose visual OCR hits are included in Codex planning input.",
    )
    parser.add_argument(
        "--confirmed-correspondence-decision",
        default=str(CONFIRMED_CORRESPONDENCE_DECISION_JSON),
        help="Runtime agent/staff decision JSON listing confirmed file-level correspondence ids.",
    )
    parser.add_argument(
        "--exclude-from-staff-review",
        default="",
        help="Comma-separated titles or source_item_id values to exclude from staff-review/upload consideration.",
    )
    parser.add_argument(
        "--strong-clear-before-rebuild",
        action="store_true",
        help="Clear sandbox 105330 to General-only before section setup and rebuild.",
    )
    parser.add_argument(
        "--preserve-existing-modules",
        action="store_true",
        help="Preserve current sandbox modules/sections and upload only missing compliant File resources before activity simulation.",
    )
    args = parser.parse_args()
    if args.strong_clear_before_rebuild and args.preserve_existing_modules:
        raise PipelineBlocked("--strong-clear-before-rebuild and --preserve-existing-modules are mutually exclusive.")

    ensure_layout()
    run_id = args.run_id or datetime.now(timezone.utc).strftime("RUN-%Y%m%d-%H%M%S")
    report_dir = COURSE_AUTOMATION_REPORT_DIR / f"dase7137_stale_cleanup_rebuild_{stamp()}_{run_id}"
    report_dir.mkdir(parents=True, exist_ok=True)
    env = load_local_env()
    execution_enabled = bool(args.debug_auto_approve and args.debug_execute_sandbox_writes and not args.no_execute)
    result: dict[str, Any] = {
        "ok": False,
        "status": "STARTED",
        "run_id": run_id,
        "batch_id": BATCH_ID,
        "semester_policy": CURRENT_SEMESTER_POLICY,
        "semester_policy_evidence": SEM1_TIMELINE_EVIDENCE,
        "report_dir": repo_rel(report_dir),
        "execution_enabled": execution_enabled,
        "target": {"course_url": SANDBOX_COURSE_URL, "course_id": SANDBOX_COURSE_ID},
        "source_backup": repo_rel(resolve_repo_path(args.backup_path)),
        "steps": {},
    }
    try:
        before_contents = load_contents(env, args.contents_fixture)
        write_json(report_dir / "01_apim_before_contents.json", before_contents)

        extraction = ensure_extraction(resolve_repo_path(args.backup_path))
        extraction_path = report_dir / "03_dase_backup_extraction.json"
        write_json(extraction_path, extraction)

        confirmed_correspondence_decision = load_confirmed_correspondence_decision(args.confirmed_correspondence_decision)
        source_selection_plan = build_refresh_plan(
            extraction,
            run_id,
            confirmed_correspondence_decision=confirmed_correspondence_decision,
        )
        source_selection_path = report_dir / "04_source_selection_plan.json"
        write_json(source_selection_path, source_selection_plan)

        visual_date_audit_evidence = load_json(args.visual_date_audit_evidence) if args.visual_date_audit_evidence else None
        date_policy_input = build_codex_date_policy_input(
            extraction,
            source_selection_plan,
            before_contents,
            run_id=run_id,
            visual_date_audit_evidence=visual_date_audit_evidence,
        )
        date_policy_input_path = report_dir / "04_codex_date_policy_input.json"
        write_json(date_policy_input_path, date_policy_input)
        date_policy_prompt = render_codex_date_policy_prompt(date_policy_input_path)
        date_policy_prompt_path = report_dir / "04_codex_date_policy_prompt.md"
        date_policy_prompt_path.write_text(date_policy_prompt, encoding="utf-8")
        if args.date_policy_output:
            date_policy_output = load_json(args.date_policy_output)
        elif args.run_codex_date_policy:
            date_policy_output = run_codex_policy(
                date_policy_prompt,
                run_id=run_id,
                raw_path=report_dir / "04_codex_date_policy_raw.txt",
                session_label="date-policy",
                extractor=extract_json_object,
            )
        else:
            raise PipelineBlocked(
                "Codex date policy output is required. Pass --date-policy-output with a validated "
                "planning JSON or use --run-codex-date-policy."
            )
        validate_date_replacement_policy_output(date_policy_output, source_selection_plan)
        date_policy_path = report_dir / "04_codex_date_policy.json"
        write_json(date_policy_path, date_policy_output)

        refresh_plan = apply_codex_date_policy(source_selection_plan, date_policy_output)
        if args.materialize_refresh:
            refresh_plan = materialize_refresh_plan(refresh_plan)
        refresh_plan_path = report_dir / "04_refresh_plan.json"
        write_json(refresh_plan_path, refresh_plan)

        candidate_audit_manifest = build_candidate_audit_manifest(refresh_plan, run_id, report_dir)
        candidate_audit_manifest_path = report_dir / "05_candidate_date_audit_manifest.json"
        write_json(candidate_audit_manifest_path, candidate_audit_manifest)
        audit_report = load_json(args.date_audit_report) if args.date_audit_report else None
        auto_semantic_review_path: Path | None = None
        initial_audit_report_path: Path | None = None
        if args.run_date_audit:
            explicit_semantic_review_path = resolve_repo_path(args.date_audit_semantic_review) if args.date_audit_semantic_review else None
            audit_report = run_date_audit(candidate_audit_manifest_path, explicit_semantic_review_path)
            if args.auto_embedded_visual_date_review:
                initial_audit_report_path = report_dir / "05_date_audit_report_initial.json"
                write_json(initial_audit_report_path, audit_report)
                auto_semantic_review = build_embedded_visual_date_semantic_review(audit_report, run_id=run_id)
                auto_semantic_review_path = report_dir / "05_embedded_visual_date_semantic_review.json"
                write_json(auto_semantic_review_path, auto_semantic_review)
                if auto_semantic_review["summary"]["reviews"]:
                    audit_report = run_date_audit(candidate_audit_manifest_path, auto_semantic_review_path)
            write_json(report_dir / "05_date_audit_report.json", audit_report)

        excluded_items = {value.strip() for value in args.exclude_from_staff_review.split(",") if value.strip()}
        target_model = build_target_model(
            extraction,
            refresh_plan,
            audit_report=audit_report,
            operator_excluded_items=excluded_items,
        )
        target_model_path = report_dir / "06_target_model.json"
        write_json(target_model_path, target_model)

        if args.preserve_existing_modules:
            clear_plan = build_preserve_existing_modules_plan(before_contents, target_model["target_sections"], run_id=run_id)
            clear_plan_path = report_dir / "02_preserve_existing_modules_plan.json"
            clear_strategy = "preserve_existing_modules"
        elif args.strong_clear_before_rebuild:
            clear_plan = build_strong_clear_plan(before_contents, batch_id=BATCH_ID, run_id=run_id)
            clear_plan_path = report_dir / "02_strong_clear_plan.json"
            clear_strategy = "strong_clear_to_general_only"
        else:
            clear_plan = build_module_clear_plan(before_contents, target_model["target_sections"], run_id=run_id)
            clear_plan_path = report_dir / "02_module_clear_plan.json"
            clear_strategy = "delete_modules_preserve_existing_sections"
        write_json(clear_plan_path, clear_plan)

        upload_manifest = build_upload_manifest(target_model, run_id, report_dir)
        if args.preserve_existing_modules:
            upload_manifest = filter_upload_manifest_to_missing(upload_manifest, before_contents)
        activity_manifest = build_activity_shell_manifest(target_model, run_id, report_dir)

        activity_policy_input = build_codex_activity_policy_input(
            extraction,
            upload_manifest,
            activity_manifest,
            before_contents,
            run_id=run_id,
            preserve_existing_modules=args.preserve_existing_modules,
        )
        activity_policy_input_path = report_dir / "08_codex_activity_policy_input.json"
        write_json(activity_policy_input_path, activity_policy_input)
        activity_policy_prompt = render_codex_activity_policy_prompt(activity_policy_input_path)
        activity_policy_prompt_path = report_dir / "08_codex_activity_policy_prompt.md"
        activity_policy_prompt_path.write_text(activity_policy_prompt, encoding="utf-8")
        if args.activity_policy_output:
            activity_policy_output = load_json(args.activity_policy_output)
        elif args.run_codex_activity_policy:
            activity_policy_output = run_codex_policy(
                activity_policy_prompt,
                run_id=run_id,
                raw_path=report_dir / "08_codex_activity_policy_raw.txt",
                session_label="activity-policy",
                extractor=extract_activity_policy_object,
            )
        else:
            raise PipelineBlocked(
                "Codex activity/diff policy output is required. Pass --activity-policy-output "
                "with validated planning JSON or use --run-codex-activity-policy."
            )
        validate_activity_policy_output(activity_policy_output, activity_policy_input)
        activity_policy_path = report_dir / "08_codex_activity_policy.json"
        write_json(activity_policy_path, activity_policy_output)
        upload_manifest, activity_manifest = apply_activity_policy_to_manifests(
            upload_manifest,
            activity_manifest,
            activity_policy_output,
            activity_policy_path,
        )
        upload_manifest_path = report_dir / "07_upload_manifest.json"
        write_json(upload_manifest_path, upload_manifest)
        activity_manifest_path = report_dir / "08_activity_shell_manifest.json"
        write_json(activity_manifest_path, activity_manifest)
        section_manifest = build_section_setup_manifest(target_model, run_id)
        section_manifest_path = report_dir / "09_section_setup_manifest.json"
        write_json(section_manifest_path, section_manifest)

        dry_summary = {
            "source_course": extraction["source_course"],
            "semester_policy": CURRENT_SEMESTER_POLICY,
            "delete_count": len(clear_plan.get("delete_modules", [])),
            "delete_section_count": len(clear_plan.get("delete_sections", [])),
            "clear_strategy": clear_strategy,
            "section_count": len(target_model["target_sections"]),
            "activity_count": int(extraction["summary"]["activities"]),
            "office_original_refresh_count": refresh_plan["summary"]["office_original_count"],
            "md_correspondence_count": refresh_plan["summary"].get("md_correspondence_count", 0),
            "md_office_source_count": refresh_plan["summary"].get("md_office_source_count", 0),
            "title_match_count": refresh_plan["summary"].get("title_match_count", 0),
            "pdf_fallback_count": refresh_plan["summary"]["pdf_fallback_count"],
            "md_blocked_count": refresh_plan["summary"].get("md_blocked_count", 0),
            "candidate_date_audit_count": candidate_audit_manifest["summary"]["upload_items"],
            "upload_count": upload_manifest["summary"]["upload_items"],
            "activity_shell_count": activity_manifest["summary"]["activity_shell_items"],
            "hidden_url_count": activity_manifest["summary"]["url_activity_items"],
            "blocked_count": len(target_model["blocked_items"]),
            "manual_review_count": len(target_model["manual_review_items"]),
        }
        result["steps"] = {
            "preflight": {
                "ok": True,
                "module_count": len(flatten_modules(before_contents)),
                "delete_count": len(clear_plan.get("delete_modules", [])),
                "delete_section_count": len(clear_plan.get("delete_sections", [])),
                "clear_plan": repo_rel(clear_plan_path),
                "clear_strategy": clear_strategy,
            },
            "extraction": {"ok": True, "path": repo_rel(extraction_path), "summary": extraction["summary"]},
            "source_selection_plan": {"ok": True, "path": repo_rel(source_selection_path), "summary": source_selection_plan["summary"]},
            "codex_date_policy": {
                "ok": True,
                "input": repo_rel(date_policy_input_path),
                "prompt": repo_rel(date_policy_prompt_path),
                "output": repo_rel(date_policy_path),
                "summary": refresh_plan.get("codex_policy_summary", {}),
            },
            "refresh_plan": {"ok": True, "path": repo_rel(refresh_plan_path), "summary": refresh_plan["summary"]},
            "candidate_date_audit_manifest": {
                "ok": True,
                "path": repo_rel(candidate_audit_manifest_path),
                "summary": candidate_audit_manifest["summary"],
                "final_audit_report": repo_rel(report_dir / "05_date_audit_report.json") if audit_report else "",
                "initial_audit_report": repo_rel(initial_audit_report_path) if initial_audit_report_path else "",
                "semantic_review": repo_rel(auto_semantic_review_path) if auto_semantic_review_path else args.date_audit_semantic_review,
            },
            "target_model": {"ok": True, "path": repo_rel(target_model_path), "summary": dry_summary},
            "upload_manifest": {"ok": True, "path": repo_rel(upload_manifest_path), "summary": upload_manifest["summary"]},
            "codex_activity_policy": {
                "ok": True,
                "input": repo_rel(activity_policy_input_path),
                "prompt": repo_rel(activity_policy_prompt_path),
                "output": repo_rel(activity_policy_path),
                "summary": {
                    "activity_creation_candidates": len(as_list(activity_policy_input.get("activity_creation_candidates"))),
                    "file_backfill_candidates": len(as_list(activity_policy_input.get("file_backfill_candidates"))),
                    "activity_creation_decisions": len(as_list(activity_policy_output.get("activity_creation_decisions"))),
                    "file_backfill_decisions": len(as_list(activity_policy_output.get("file_backfill_decisions"))),
                    "blocked_items": len(as_list(activity_policy_output.get("blocked_items"))),
                },
            },
            "activity_shell_manifest": {
                "ok": True,
                "path": repo_rel(activity_manifest_path),
                "summary": activity_manifest["summary"],
            },
            "section_setup_manifest": {
                "ok": True,
                "path": repo_rel(section_manifest_path),
                "summary": section_manifest["summary"],
            },
        }

        if not execution_enabled:
            result["ok"] = True
            result["status"] = "PLANNED_ONLY"
        else:
            if args.preserve_existing_modules:
                clear_result = {"ok": True, "status": "SKIPPED_PRESERVE_EXISTING_MODULES", "saves_count": 0}
                clear_command_path = report_dir / "10_preserve_existing_modules_command.json"
            elif args.strong_clear_before_rebuild:
                clear_result = execute_strong_clear(args, clear_plan_path, clear_plan)
                clear_command_path = report_dir / "10_strong_clear_command.json"
            else:
                clear_result = execute_module_clear(clear_plan_path, clear_plan)
                clear_command_path = report_dir / "10_module_clear_command.json"
            write_json(clear_command_path, clear_result)
            if not clear_result.get("ok"):
                raise PipelineBlocked("Strong clear command failed." if args.strong_clear_before_rebuild else "Module clear command failed.")
            after_clear = before_contents if args.preserve_existing_modules else load_contents(env, args.contents_fixture)
            write_json(report_dir / "11_apim_after_clear_contents.json", after_clear)
            if args.preserve_existing_modules:
                if not expected_sectionnums_present(after_clear, target_model["target_sections"]):
                    raise PipelineBlocked("Preserve-existing mode requires sandbox sections 0-14 to be present before activity simulation.")
            elif args.strong_clear_before_rebuild:
                assert_strong_clear_verified(after_clear)
            else:
                assert_module_clear_verified(after_clear, target_model["target_sections"])

            if args.preserve_existing_modules and expected_sectionnums_present(after_clear, target_model["target_sections"]):
                section_setup_result = {"ok": True, "status": "SKIPPED_SECTIONS_PRESENT", "saves_count": 0}
            else:
                section_setup_result = execute_section_setup(section_manifest_path, section_manifest)
            write_json(report_dir / "12_section_setup_command.json", section_setup_result)
            if not section_setup_result.get("ok"):
                raise PipelineBlocked("Section setup command failed.")
            after_section_setup = after_clear if section_setup_result.get("status") == "SKIPPED_SECTIONS_PRESENT" else load_contents(env, args.contents_fixture)
            write_json(report_dir / "13_apim_after_section_setup_contents.json", after_section_setup)

            section_rename_plan = build_section_rename_plan(after_section_setup, target_model, run_id)
            section_rename_plan_path = report_dir / "12_section_rename_plan.json"
            write_json(section_rename_plan_path, section_rename_plan)
            if args.preserve_existing_modules:
                section_result = {"ok": True, "status": "SKIPPED_PRESERVE_EXISTING_SECTIONS", "saves_count": 0}
            else:
                section_result = execute_section_renames(section_rename_plan_path, section_rename_plan)
            write_json(report_dir / "14_section_rename_command.json", section_result)
            if not section_result.get("ok"):
                raise PipelineBlocked("Section rename/setup command failed.")
            after_sections = after_section_setup if args.preserve_existing_modules else load_contents(env, args.contents_fixture)
            write_json(report_dir / "15_apim_after_section_rename_contents.json", after_sections)
            if args.preserve_existing_modules:
                if not expected_sectionnums_present(after_sections, target_model["target_sections"]):
                    raise PipelineBlocked("Preserve-existing mode lost expected sections before upload/activity simulation.")
            else:
                assert_section_titles_verified(after_sections, target_model)

            upload_result = execute_upload(upload_manifest_path, upload_manifest)
            write_json(report_dir / "16_upload_command.json", upload_result)
            if not upload_result.get("ok"):
                raise PipelineBlocked("Visible File resource upload command failed.")
            activity_result = execute_activity_shells(activity_manifest_path, activity_manifest)
            write_json(report_dir / "17_activity_shell_command.json", activity_result)
            if not activity_result.get("ok"):
                raise PipelineBlocked("Activity shell/URL command failed.")
            final_contents = load_contents(env, args.contents_fixture)
            write_json(report_dir / "18_apim_final_contents.json", final_contents)
            result["ok"] = True
            result["status"] = "PASS"
    except Exception as exc:
        result["ok"] = False
        result["status"] = "BLOCKED"
        result["blocker"] = str(exc)
    finally:
        result_path = report_dir / "result.json"
        result["result_path"] = repo_rel(result_path)
        write_json(result_path, result)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if not result["ok"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
