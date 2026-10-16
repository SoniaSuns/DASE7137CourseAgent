from __future__ import annotations

import hashlib
import html
import io
import json
import os
import re
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
import xml.etree.ElementTree as ET


EXCLUDED_PATH_PARTS = {
    "users.xml",
    "grades.xml",
    "grade_history.xml",
    "roles.xml",
    "completion.xml",
    "logs.xml",
    "questions.xml",
}
EXCLUDED_SAFETY_TERMS = {
    "submission",
    "assignsubmission",
    "feedback",
    "grade",
    "user",
    "member",
    "attempt",
    "log",
}
TEXT_TAGS = {
    "name",
    "title",
    "intro",
    "description",
    "filename",
    "mimetype",
    "component",
    "filearea",
}
SAFE_ATTACHMENT_COMPONENTS = {"mod_resource"}
SAFE_ATTACHMENT_MIMETYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain",
    "text/csv",
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _clean_text(value: str, *, limit: int = 4000) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _safe_member_name(name: str) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        return False
    lowered_parts = {part.casefold() for part in path.parts}
    if lowered_parts & EXCLUDED_PATH_PARTS:
        return False
    joined = "/".join(path.parts).casefold()
    return not any(term in joined for term in EXCLUDED_SAFETY_TERMS)


def _xml_evidence(source_name: str, member_name: str, payload: bytes) -> dict[str, Any] | None:
    if not _safe_member_name(member_name):
        return None
    # files.xml contains metadata for every file area, including private
    # submission/user areas. It is handled only by the strict safe-resource
    # index above and must never be exposed as generic XML evidence.
    if PurePosixPath(member_name).name.casefold() == "files.xml":
        return None
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None
    facts: list[dict[str, str]] = []
    for element in root.iter():
        tag = _local_name(str(element.tag)).casefold()
        if tag not in TEXT_TAGS or not element.text:
            continue
        text = _clean_text(element.text)
        if not text:
            continue
        lowered = text.casefold()
        if any(term in lowered for term in EXCLUDED_SAFETY_TERMS):
            continue
        facts.append({"field": tag, "text": text})
        if len(facts) >= 80:
            break
    if not facts:
        return None
    digest = hashlib.sha256(payload).hexdigest()
    return {
        "evidence_id": f"mbz:{digest[:16]}",
        "source_name": source_name,
        "source_member": member_name,
        "sha256": digest,
        "facts": facts,
        "structure": {
            "kind": "xml_metadata",
            "root_tag": _local_name(str(root.tag)),
            "fact_count": len(facts),
        },
        "usage": "sanitized historical evidence only; planner decides relevance",
    }


def _files_index(payload: bytes) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []
    records: list[dict[str, Any]] = []
    for file_element in root:
        values = {
            _local_name(str(child.tag)).casefold(): str(child.text or "").strip()
            for child in file_element
        }
        component = values.get("component", "").casefold()
        filearea = values.get("filearea", "").casefold()
        filename = values.get("filename", "")
        mimetype = values.get("mimetype", "").casefold()
        contenthash = values.get("contenthash", "").casefold()
        if component not in SAFE_ATTACHMENT_COMPONENTS:
            continue
        if any(term in filearea for term in EXCLUDED_SAFETY_TERMS):
            continue
        if mimetype not in SAFE_ATTACHMENT_MIMETYPES:
            continue
        if filename in {"", "."} or not re.fullmatch(r"[0-9a-f]{40}", contenthash):
            continue
        try:
            filesize = int(values.get("filesize") or 0)
        except ValueError:
            continue
        if filesize <= 0 or filesize > 10_000_000:
            continue
        records.append(
            {
                "filename": Path(filename).name,
                "mimetype": mimetype,
                "contenthash": contenthash,
                "filesize": filesize,
            }
        )
    return records


def _ooxml_visible_text(payload: bytes) -> str:
    parts: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for info in archive.infolist():
                name = info.filename
                visible = (
                    name == "word/document.xml"
                    or (name.startswith("ppt/slides/slide") and name.endswith(".xml"))
                    or name == "xl/sharedStrings.xml"
                    or (name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
                )
                if not visible:
                    continue
                try:
                    root = ET.fromstring(archive.read(name))
                except ET.ParseError:
                    continue
                for element in root.iter():
                    if _local_name(str(element.tag)) in {"t", "v"} and element.text:
                        parts.append(element.text)
    except zipfile.BadZipFile:
        return ""
    return _clean_text(" ".join(parts), limit=12_000)


def _attachment_visible_text(payload: bytes, mimetype: str) -> str:
    if mimetype == "application/pdf":
        try:
            import pdfplumber

            with pdfplumber.open(io.BytesIO(payload)) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages[:20])
            return _clean_text(text, limit=12_000)
        except Exception:
            return ""
    if "openxmlformats-officedocument" in mimetype:
        return _ooxml_visible_text(payload)
    if mimetype.startswith("text/"):
        return _clean_text(payload.decode("utf-8", errors="replace"), limit=12_000)
    return ""


def _attachment_evidence(
    source_name: str,
    record: dict[str, Any],
    payload: bytes,
) -> dict[str, Any] | None:
    if hashlib.sha1(payload).hexdigest() != record["contenthash"]:
        return None
    visible_text = _attachment_visible_text(payload, str(record["mimetype"]))
    facts = [
        {"field": "filename", "text": str(record["filename"])},
        {"field": "mimetype", "text": str(record["mimetype"])},
    ]
    if visible_text:
        facts.append({"field": "visible_text", "text": visible_text})
    digest = hashlib.sha256(payload).hexdigest()
    contenthash = str(record["contenthash"])
    return {
        "evidence_id": f"mbz-file:{digest[:16]}",
        "source_name": source_name,
        "source_member": str(record.get("source_member") or f"files/{contenthash[:2]}/{contenthash}"),
        "sha256": digest,
        "facts": facts,
        "structure": {
            "kind": "course_resource_attachment",
            "mimetype": str(record["mimetype"]),
            "visible_character_count": len(visible_text),
        },
        "usage": "sanitized historical course-resource evidence only; planner decides relevance",
    }


def _iter_directory_xml(root: Path) -> Iterable[tuple[str, bytes]]:
    for path in sorted(root.rglob("*.xml")):
        relative = path.relative_to(root).as_posix()
        if _safe_member_name(relative):
            yield relative, path.read_bytes()


def _iter_tar_xml(path: Path) -> Iterable[tuple[str, bytes]]:
    with tarfile.open(path, "r:*") as archive:
        for member in archive.getmembers():
            if not member.isfile() or not member.name.casefold().endswith(".xml"):
                continue
            if not _safe_member_name(member.name):
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            payload = handle.read(2_000_001)
            if len(payload) > 2_000_000:
                continue
            yield member.name, payload


def extract_moodle_backup_evidence(
    source: str | Path,
    *,
    max_records: int = 300,
) -> dict[str, Any]:
    max_records = max(1, int(max_records))
    attachment_limit = min(50, max(1, max_records // 3))
    path = Path(source).expanduser().resolve(strict=True)
    if path.is_dir():
        iterator = list(_iter_directory_xml(path))
        files_payload = next(
            (payload for name, payload in iterator if PurePosixPath(name).name.casefold() == "files.xml"),
            b"",
        )
        attachment_index = _files_index(files_payload)[:attachment_limit]
        attachment_payloads: dict[str, bytes] = {}
        for record in attachment_index:
            contenthash = str(record["contenthash"])
            candidate = next(
                (
                    item for item in path.rglob(contenthash)
                    if item.is_file() and item.parent.name.casefold() == contenthash[:2]
                ),
                None,
            )
            if candidate is not None:
                record["source_member"] = candidate.relative_to(path).as_posix()
                attachment_payloads[contenthash] = candidate.read_bytes()
    elif path.is_file() and path.suffix.casefold() == ".mbz":
        iterator = list(_iter_tar_xml(path))
        files_payload = next(
            (payload for name, payload in iterator if PurePosixPath(name).name.casefold() == "files.xml"),
            b"",
        )
        attachment_index = _files_index(files_payload)[:attachment_limit]
        wanted = {str(record["contenthash"]): str(record["contenthash"]) for record in attachment_index}
        attachment_payloads: dict[str, bytes] = {}
        with tarfile.open(path, "r:*") as archive:
            for member in archive.getmembers():
                member_path = PurePosixPath(member.name)
                contenthash = wanted.get(member_path.name)
                if contenthash and member_path.parent.name.casefold() != contenthash[:2]:
                    contenthash = None
                if not contenthash or not member.isfile():
                    continue
                handle = archive.extractfile(member)
                if handle is not None:
                    for record in attachment_index:
                        if str(record["contenthash"]) == contenthash:
                            record["source_member"] = member.name
                            break
                    attachment_payloads[contenthash] = handle.read(10_000_001)
    else:
        raise ValueError("Moodle backup evidence source must be an .mbz file or extracted backup directory")
    attachment_records: list[dict[str, Any]] = []
    for attachment in attachment_index:
        payload = attachment_payloads.get(str(attachment["contenthash"]))
        if payload is None or len(payload) > 10_000_000:
            continue
        item = _attachment_evidence(path.name, attachment, payload)
        if item is not None:
            attachment_records.append(item)
        if len(attachment_records) >= attachment_limit:
            break
    xml_limit = max_records - len(attachment_records)
    records: list[dict[str, Any]] = []
    if xml_limit > 0:
        for member_name, payload in iterator:
            item = _xml_evidence(path.name, member_name, payload)
            if item is not None:
                records.append(item)
            if len(records) >= xml_limit:
                break
    records.extend(attachment_records)
    return {
        "source_name": path.name,
        "source_type": "directory" if path.is_dir() else "mbz",
        "record_count": len(records),
        "records": records,
        "privacy": {
            "member_identities_included": False,
            "grades_included": False,
            "submissions_included": False,
        },
    }


def configured_backup_evidence(env: dict[str, str]) -> list[dict[str, Any]]:
    raw = str(env.get("COURSE_AGENT_HISTORY_BACKUP_PATHS") or "").strip()
    if not raw:
        return []
    paths = [value.strip() for value in raw.split(os.pathsep) if value.strip()]
    evidence: list[dict[str, Any]] = []
    for path_text in paths:
        try:
            result = extract_moodle_backup_evidence(path_text)
        except (OSError, ValueError, tarfile.TarError):
            continue
        evidence.extend(result.get("records") or [])
    return evidence


def write_evidence_json(source: str | Path, output: str | Path) -> dict[str, Any]:
    result = extract_moodle_backup_evidence(source)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
