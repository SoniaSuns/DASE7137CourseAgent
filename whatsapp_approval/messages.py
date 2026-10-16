from __future__ import annotations

from collections.abc import Sequence

from .commands import approval_reply_commands


LABELS = {
    "operation": "Action",
    "object": "Item",
    "section": "Section",
    "course id": "Course",
    "resource name": "Name",
    "upload file": "File",
    "expected visibility": "Visibility",
    "review attachment": "Attachment",
    "review PDF": "Attachment",
    "current file": "Current",
    "current file to remove": "Remove",
    "proposed file": "New file",
    "rollback file": "Rollback",
    "wsfunction": "API",
    "body preview": "Body",
}

VALUES = {
    "create_file_resource": "Create file",
    "upload_or_replace_file": "Replace file",
    "upload_file_api": "Upload file",
    "upload_course_file_resource": "Create/update file",
    "publish_announcement": "Post announcement",
    "create_group": "Create group",
    "create_grouping": "Create grouping",
    "delete_group": "Delete group",
    "delete_grouping": "Delete grouping",
    "assign_group_names_to_grouping": "Add groups to grouping",
    "unassign_group_names_from_grouping": "Remove groups from grouping",
    "Hide on course page": "Hidden",
    "Show on course page": "Visible",
}

EFFECTS = {
    "record approval only; no Moodle change": "No Moodle change",
    "one save: create one sandbox File resource": "Create file",
    "one save: replace/upload listed file": "Replace/upload listed file",
    "one save: change listed title only": "Change listed title",
    "one save: listed operation only": "Run listed change",
    "one test save plus one rollback save": "Test save + rollback",
    "prepare only; write needs this RUN approval": "Prepare only",
}


def display_label(label: str) -> str:
    return LABELS.get(label.strip().lower(), label.strip())


def display_value(value: str) -> str:
    return VALUES.get(value.strip(), value.strip())


def display_effect(effect: str) -> str:
    return EFFECTS.get(effect.strip(), effect.strip())


def compact_fields(fields: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    has_expected_visibility = any(label.strip().lower() == "expected visibility" for label, value in fields if value)
    has_upload_file = any(label.strip().lower() == "upload file" for label, value in fields if value)
    upload_file_values = {
        value.strip()
        for label, value in fields
        if label.strip().lower() == "upload file" and value.strip()
    }
    compacted: list[tuple[str, str]] = []
    seen_values: set[str] = set()
    for label, value in fields:
        label = label.strip()
        value = value.strip()
        normalized_label = label.lower()
        normalized_value = value.lower()
        if not value:
            continue
        if normalized_label == "upload file path" and has_upload_file:
            continue
        if normalized_label == "visible" and has_expected_visibility:
            continue
        if normalized_label == "resource name" and normalized_value in seen_values:
            continue
        if normalized_label in {"review attachment", "review pdf"} and value in upload_file_values:
            continue
        compacted.append((display_label(label), display_value(value)))
        seen_values.add(normalized_value)
    return compacted


def format_approval_request(
    *,
    batch_id: str,
    run_id: str,
    fields: Sequence[tuple[str, str]],
    effect: str,
    message_format: str = "boxed_ascii",
    scope: str = "sandbox Moodle only",
    approval_code: str | None = None,
) -> str:
    normalized_format = message_format.strip().lower()
    commands = approval_reply_commands(batch_id, run_id, approval_code=approval_code)
    command_text = " | ".join(commands.values())
    if normalized_format in {"single_line_ascii", "ascii", "single_line", "compact"}:
        body = " | ".join([f"{label}: {value}" for label, value in compact_fields(fields)])
        return f"Moodle approval | {body} | Reply: {command_text}\n"

    field_lines = "\n".join(f"{label}: {value}" for label, value in compact_fields(fields)) or "no operation fields"
    command_lines = "\n".join(
        [
            commands["approve"],
            commands["reject"],
        ]
    )
    return f"""Moodle approval
{field_lines}

Reply:
{command_lines}
"""
