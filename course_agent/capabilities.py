from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from course_agent_app.course_refresh_job import validate_refresh_job_shape
from course_agent_app.file_refresh_preview import (
    OPERATION_KIND as REFRESH_FILE_PREVIEW_KIND,
    validate_refresh_file_preview_shape,
)
from course_agent_app.file_refresh_batch_preview import (
    FINALIZE_OPERATION_KIND as FINALIZE_FILE_BATCH_PREVIEW_KIND,
    OPERATION_KIND as REFRESH_FILE_BATCH_PREVIEW_KIND,
    validate_finalize_file_batch_preview_shape,
    validate_refresh_file_batch_preview_shape,
)
from course_agent_app.reference_pack_rebuild import (
    OPERATION_KIND as REBUILD_REFERENCE_PACK_KIND,
    reference_pack_path as rebuild_reference_pack_path,
    validate_rebuild_reference_pack_shape,
)
from course_agent_app.semester_schedule_update import (
    OPERATION_KIND as UPDATE_SEMESTER_SCHEDULE_KIND,
    reference_pack_path as semester_schedule_reference_pack_path,
    validate_update_semester_schedule_shape,
)
from course_agent_app.timeline_draft_review import (
    COMMIT_OPERATION_KIND as COMMIT_TIMELINE_DRAFT_KIND,
    REVISE_OPERATION_KIND as REVISE_TIMELINE_DRAFT_KIND,
    validate_commit_timeline_draft_shape,
    validate_revise_timeline_draft_shape,
)


SANDBOX_COURSE_ID = "105330"
SANDBOX_COURSE_URL = "https://migrationtest-moodle.hku.hk/course/view.php?id=105330"
COURSE_REFRESH_BATCH_UPLOAD_KIND = "course_refresh_batch_upload"


@dataclass(frozen=True)
class CapabilityDefinition:
    name: str
    required_params: tuple[str, ...]
    risk_level: str
    executor_lane: str
    execution_mode: str
    verifier: str
    browser_allowed: bool
    approval_action: str
    approval_summary: str
    params_schema: dict[str, Any] | None = None
    local_preapproval_steps: tuple[str, ...] = ()
    post_approval_executor: str = ""
    safety_rules: tuple[str, ...] = ()


CAPABILITY_REGISTRY: dict[str, CapabilityDefinition] = {
    "create_announcement": CapabilityDefinition(
        name="create_announcement",
        required_params=("subject", "body"),
        risk_level="medium",
        executor_lane="announcement_api",
        execution_mode="api_action",
        verifier="announcement_readback",
        browser_allowed=False,
        approval_action="Post announcement",
        approval_summary="Create one sandbox announcement discussion.",
        params_schema={
            "required": ["subject", "body|message"],
            "target": (
                "uses the unique current news forum; when the requested forum must first be created, "
                "requires a top-level target_ref naming that prior create_forum_activity producer"
            ),
        },
    ),
    "create_forum_post": CapabilityDefinition(
        name="create_forum_post",
        required_params=("subject", "body", "forum_target"),
        risk_level="medium",
        executor_lane="announcement_api",
        execution_mode="api_action",
        verifier="forum_post_readback",
        browser_allowed=False,
        approval_action="Post forum message",
        approval_summary="Post one sandbox Moodle forum discussion/message through APIM.",
        params_schema={
            "required": ["subject", "body|message", "forum_name|target_forum_id|target_ref"],
            "optional": ["target_forum_cmid"],
            "target_resolution": "exact current-course forum readback",
        },
    ),
    "reply_forum_post": CapabilityDefinition(
        name="reply_forum_post",
        required_params=("subject", "body", "forum_target", "discussion_target"),
        risk_level="medium",
        executor_lane="browser_forum_post_reply",
        execution_mode="agent_browser_action",
        verifier="forum_reply_readback",
        browser_allowed=True,
        approval_action="Reply to forum question",
        approval_summary="Reply once to one exact sandbox Moodle Forum discussion through the stable browser lane.",
        params_schema={
            "required": [
                "subject",
                "body|message",
                "forum_name|target_forum_id",
                "discussion_id|discussion_url|discussion_title",
            ],
            "optional": ["parent_post_id|post_url"],
            "target_resolution": (
                "exact current-course forum plus exact sanitized discussion readback; "
                "the action-time browser preflight resolves one exact Reply control"
            ),
        },
        safety_rules=(
            "sandbox course 105330 only",
            "reply to exactly one readback-resolved discussion; never create a new discussion",
            "action-time browser readback must show the approved discussion title and URL",
            "exactly one root/approved-parent Reply control must resolve before any write",
            "submit exactly once and verify exactly one new matching post under the approved discussion",
        ),
    ),
    "delete_forum_post": CapabilityDefinition(
        name="delete_forum_post",
        required_params=("target",),
        risk_level="high",
        executor_lane="browser_forum_post_delete",
        execution_mode="agent_browser_action",
        verifier="forum_post_absent_readback",
        browser_allowed=True,
        approval_action="Delete forum message",
        approval_summary=(
            "Delete one sandbox Moodle forum post/discussion through the stable browser delete lane."
        ),
        params_schema={
            "one_of": [
                "post_url",
                "discussion_url",
                "post_id",
                "discussion_id",
                "forum_name|forum_url plus target_subject|target_ref",
            ],
            "recommended": ["forum_name", "target_subject|target_ref for visible confirmation"],
        },
        safety_rules=(
            "sandbox course 105330 only",
            "must resolve to one exact forum post/discussion URL or unique browser text match before deleting",
            "browser confirmation page must show the approved subject/ref before delete is confirmed",
            "do not delete an entire forum module; use delete_module for an approved exact forum activity delete",
        ),
    ),
    "delete_forum_reply": CapabilityDefinition(
        name="delete_forum_reply",
        required_params=("target",),
        risk_level="high",
        executor_lane="browser_forum_reply_delete",
        execution_mode="agent_browser_action",
        verifier="forum_reply_absent_readback",
        browser_allowed=True,
        approval_action="Delete forum reply",
        approval_summary=(
            "Delete one exact reply from one exact sandbox Moodle Forum discussion."
        ),
        params_schema={
            "required": [
                "forum_name|target_forum_id",
                "discussion_id|discussion_url|discussion_title",
                "reply_post_id|reply_post_url|reply_text|target_ref",
            ],
            "recommended": [
                "reply_post_id",
                "reply_text for visible confirmation",
            ],
            "target_resolution": (
                "exact current-course forum and discussion; action-time browser "
                "preflight must resolve exactly one non-root reply"
            ),
        },
        safety_rules=(
            "sandbox course 105330 only",
            "delete exactly one non-root reply; never delete the discussion root post",
            "the approved discussion identity and one exact reply must resolve before any write",
            "submit exactly once and verify the resolved reply post id is absent",
            "zero or multiple reply matches block without writing",
        ),
    ),
    "inspect_forum_structure": CapabilityDefinition(
        name="inspect_forum_structure",
        required_params=("scope",),
        risk_level="low",
        executor_lane="forum_structure_readback",
        execution_mode="read_only_action",
        verifier="forum_structure_snapshot",
        browser_allowed=True,
        approval_action="Inspect forums",
        approval_summary="Read a sanitized sandbox Forum discussion inventory without changing Moodle.",
        params_schema={
            "required": ["scope"],
            "scope": ["course", "forums"],
            "optional": ["forum_names"],
        },
        safety_rules=(
            "sandbox course 105330 only",
            "read-only; never click a Moodle save, submit, edit, or delete control",
            "return discussion ids, titles, and URLs only; never expose author identities or message bodies",
            "no WhatsApp approval code is consumed",
        ),
    ),
    "create_group": CapabilityDefinition(
        name="create_group",
        required_params=("name",),
        risk_level="medium",
        executor_lane="group_api",
        execution_mode="api_action",
        verifier="group_readback",
        browser_allowed=False,
        approval_action="Create group",
        approval_summary="Create one Moodle group in the sandbox course.",
    ),
    "inspect_group_structure": CapabilityDefinition(
        name="inspect_group_structure",
        required_params=("scope",),
        risk_level="low",
        executor_lane="group_structure_readback",
        execution_mode="read_only_action",
        verifier="group_structure_snapshot",
        browser_allowed=True,
        approval_action="Inspect groups",
        approval_summary="Read sandbox Group, Grouping, and Choicegroup structure without changing Moodle.",
        params_schema={
            "required": ["scope"],
            "scope": ["course", "groups", "groupings", "choicegroup_dependencies"],
            "optional": [
                "group_names",
                "grouping_names",
                "include_choicegroup_dependencies",
            ],
        },
        safety_rules=(
            "sandbox course 105330 only",
            "read-only; never click a Moodle save or submit control",
            "return member counts only and never expose member identities",
            "no WhatsApp approval code is consumed",
        ),
    ),
    "create_grouping": CapabilityDefinition(
        name="create_grouping",
        required_params=("name",),
        risk_level="medium",
        executor_lane="grouping_api",
        execution_mode="api_action",
        verifier="grouping_readback",
        browser_allowed=False,
        approval_action="Create grouping",
        approval_summary="Create one Moodle grouping in the sandbox course.",
    ),
    "update_grouping_membership": CapabilityDefinition(
        name="update_grouping_membership",
        required_params=("grouping_name",),
        risk_level="medium",
        executor_lane="grouping_api",
        execution_mode="api_action",
        verifier="grouping_membership_readback",
        browser_allowed=False,
        approval_action="Update grouping",
        approval_summary="Explicitly add or remove named Groups in one sandbox Moodle Grouping.",
        params_schema={
            "required": ["grouping_name", "add_group_names|remove_group_names"],
            "optional": ["add_group_names", "remove_group_names"],
            "semantics": "explicit delta only; unmentioned Groups are unchanged",
        },
        safety_rules=(
            "sandbox course 105330 only",
            "add_group_names and remove_group_names must be disjoint",
            "all Group and Grouping names must resolve uniquely from detailed readback",
            "do not infer an exact replacement set from an incomplete prompt",
        ),
    ),
    "update_group_membership": CapabilityDefinition(
        name="update_group_membership",
        required_params=("action", "user_id|user_name"),
        risk_level="high",
        executor_lane="browser_group_membership",
        execution_mode="agent_browser_action",
        verifier="group_member_count_and_transient_unique_participant_readback",
        browser_allowed=True,
        approval_action="Update group member",
        approval_summary=(
            "Add, remove, or move one staff-identified participant between exact sandbox Groups."
        ),
        params_schema={
            "required": ["action", "exactly one of user_id|user_name"],
            "action": ["add", "remove", "move"],
            "conditional": {
                "add": ["target_group_name"],
                "remove": ["source_group_name"],
                "move": ["source_group_name", "target_group_name"],
            },
            "user_id": "positive numeric Moodle user id supplied explicitly by staff",
            "user_name": (
                "staff-supplied participant name query; normalized case-insensitive search must "
                "resolve to exactly one numeric Moodle option value at action time"
            ),
        },
        safety_rules=(
            "sandbox course 105330 only",
            "require exactly one participant reference: a positive numeric Moodle user id or a non-empty name query",
            "name search must resolve uniquely before any write; zero or multiple matches are blocked",
            "resolve source and target Group names uniquely from detailed readback",
            "name matching is transient in the Moodle DOM; never return, screenshot, or persist candidate display text",
            "move preflights both Groups before write, adds the target first, and rolls back that add if source removal fails",
            "verify exact membership transiently and persist only sanitized count deltas",
        ),
    ),
    "delete_group": CapabilityDefinition(
        name="delete_group",
        required_params=("name",),
        risk_level="high",
        executor_lane="group_api",
        execution_mode="api_action",
        verifier="group_absent_readback",
        browser_allowed=False,
        approval_action="Delete group",
        approval_summary="Delete one exact empty, unreferenced sandbox Moodle Group.",
        params_schema={"required": ["name"]},
        safety_rules=(
            "sandbox course 105330 only",
            "the Group must have zero members",
            "the Group must have no Choicegroup references",
            "all Grouping links must be removed by earlier operations in the same approved manifest",
        ),
    ),
    "delete_grouping": CapabilityDefinition(
        name="delete_grouping",
        required_params=("name",),
        risk_level="high",
        executor_lane="grouping_api",
        execution_mode="api_action",
        verifier="grouping_absent_readback",
        browser_allowed=False,
        approval_action="Delete grouping",
        approval_summary="Delete one exact empty sandbox Moodle Grouping.",
        params_schema={"required": ["name"]},
        safety_rules=(
            "sandbox course 105330 only",
            "the Grouping must be empty after earlier approved unassign operations",
            "the target name must resolve uniquely from detailed readback",
        ),
    ),
    "create_group_signup_activity": CapabilityDefinition(
        name="create_group_signup_activity",
        required_params=("section", "title", "group_names"),
        risk_level="medium",
        executor_lane="browser_group_signup_activity",
        execution_mode="agent_browser_action",
        verifier="group_signup_activity_readback",
        browser_allowed=True,
        approval_action="Create group sign-up",
        approval_summary="Create one Choicegroup group sign-up activity in a sandbox course section.",
        params_schema={
            "required": ["section", "title|name", "group_names"],
            "optional": ["grouping_name", "intro", "visible", "maxanswers", "post_create_reference"],
            "maxanswers": (
                "one non-negative integer shared by every selected Group; a name-to-integer "
                "mapping is accepted only when it names exactly the selected Groups and all "
                "values are identical"
            ),
            "modname": "choicegroup",
        },
        safety_rules=(
            "sandbox course 105330 only",
            "API group/grouping creation must be ordered before this browser operation when the groups do not already exist",
            "selected groups must be explicit by name",
            "visibility must be approved; default is visible unless staff asks for hidden",
        ),
    ),
    "create_assignment_activity": CapabilityDefinition(
        name="create_assignment_activity",
        required_params=("section", "title"),
        risk_level="medium",
        executor_lane="browser_assignment_activity",
        execution_mode="agent_browser_action",
        verifier="assignment_activity_readback",
        browser_allowed=True,
        approval_action="Create assignment",
        approval_summary="Create one Assignment activity in a sandbox course section.",
        params_schema={
            "required": ["section", "title|name"],
            "optional": ["intro", "visible", "post_create_reference"],
            "modname": "assign",
        },
        safety_rules=(
            "sandbox course 105330 only",
            "create exactly one Assignment activity in the approved section",
            "visibility must be approved; default is visible unless staff asks for hidden",
            "leave assignment dates disabled unless staff explicitly approves dates",
        ),
    ),
    "create_forum_activity": CapabilityDefinition(
        name="create_forum_activity",
        required_params=("section", "title"),
        risk_level="medium",
        executor_lane="browser_forum_activity",
        execution_mode="agent_browser_action",
        verifier="forum_activity_readback",
        browser_allowed=True,
        approval_action="Create forum",
        approval_summary="Create one Forum activity in a sandbox course section.",
        params_schema={
            "required": ["section", "title|name"],
            "optional": ["intro", "visible", "post_create_reference"],
            "modname": "forum",
        },
        safety_rules=(
            "sandbox course 105330 only",
            "create exactly one Forum activity in the approved section",
            "use only as an explicitly approved dependency when the requested exact forum is absent",
        ),
    ),
    "upload_file_resource": CapabilityDefinition(
        name="upload_file_resource",
        required_params=("file_path",),
        risk_level="medium",
        executor_lane="browser_course_file_resource_upload",
        execution_mode="agent_browser_action",
        verifier="file_resource_readback",
        browser_allowed=True,
        approval_action="Create file",
        approval_summary="Create one Moodle File resource from an approved local file.",
    ),
    "replace_file_resource": CapabilityDefinition(
        name="replace_file_resource",
        required_params=("target_module_id", "file_path"),
        risk_level="high",
        executor_lane="file_upload_replace",
        execution_mode="agent_browser_action",
        verifier="file_resource_readback",
        browser_allowed=True,
        approval_action="Replace file",
        approval_summary="Replace one exact Moodle File resource.",
    ),
    "delete_file_resource": CapabilityDefinition(
        name="delete_file_resource",
        required_params=("target",),
        risk_level="high",
        executor_lane="browser_course_file_resource_delete",
        execution_mode="agent_browser_action",
        verifier="file_resource_absent_readback",
        browser_allowed=True,
        approval_action="Delete file",
        approval_summary="Delete one exact Moodle File resource.",
    ),
    "delete_modules_in_section": CapabilityDefinition(
        name="delete_modules_in_section",
        required_params=(),
        risk_level="high",
        executor_lane="browser_module_delete",
        execution_mode="agent_browser_action",
        verifier="section_module_absent_readback",
        browser_allowed=True,
        approval_action="Delete section contents",
        approval_summary="Delete exact Moodle modules resolved from one section readback.",
    ),
    "delete_module": CapabilityDefinition(
        name="delete_module",
        required_params=("target",),
        risk_level="high",
        executor_lane="browser_module_delete",
        execution_mode="agent_browser_action",
        verifier="module_absent_readback",
        browser_allowed=True,
        approval_action="Delete item",
        approval_summary="Delete one exact Moodle module resolved from readback.",
    ),
    "rename_activity": CapabilityDefinition(
        name="rename_activity",
        required_params=("new_title",),
        risk_level="medium",
        executor_lane="direct_title_edit",
        execution_mode="agent_browser_action",
        verifier="activity_title_readback",
        browser_allowed=True,
        approval_action="Rename item",
        approval_summary="Rename one exact Moodle activity/module.",
        params_schema={
            "required": ["new_title", "target_module_id|cmid|target_ref"],
            "target_ref": "must name one explicit prior producer in the same ordered manifest",
        },
    ),
    "rename_section": CapabilityDefinition(
        name="rename_section",
        required_params=("section", "new_title"),
        risk_level="medium",
        executor_lane="browser_section_rename",
        execution_mode="agent_browser_action",
        verifier="section_title_readback",
        browser_allowed=True,
        approval_action="Rename section",
        approval_summary="Update one sandbox course section title using the stable section tool.",
    ),
    "set_visibility": CapabilityDefinition(
        name="set_visibility",
        required_params=("target_module_id", "visible"),
        risk_level="high",
        executor_lane="browser_module_visibility",
        execution_mode="agent_browser_action",
        verifier="visibility_readback",
        browser_allowed=True,
        approval_action="Set visibility",
        approval_summary="Set visibility for one exact Moodle course module.",
        params_schema={
            "required": ["visible", "target_module_id|cmid|target_ref"],
            "target_ref": "must name one explicit prior producer in the same ordered manifest",
        },
    ),
    "set_restriction_time": CapabilityDefinition(
        name="set_restriction_time",
        required_params=("target_module_id", "restriction_time"),
        risk_level="medium",
        executor_lane="browser_module_access_time",
        execution_mode="agent_browser_action",
        verifier="restriction_time_readback",
        browser_allowed=True,
        approval_action="Set access time",
        approval_summary="Set an approved restriction/release time for one exact module.",
        params_schema={
            "required": ["restriction_time", "target_module_id|cmid|target_ref"],
            "target_ref": "must name one explicit prior producer in the same ordered manifest",
        },
    ),
    "course_refresh_batch_upload": CapabilityDefinition(
        name="course_refresh_batch_upload",
        required_params=("refresh_job",),
        risk_level="high",
        executor_lane=COURSE_REFRESH_BATCH_UPLOAD_KIND,
        execution_mode="agent_browser_action",
        verifier="batch_upload_readback",
        browser_allowed=True,
        approval_action="Update course files",
        approval_summary="Prepare/audit a local course refresh package and upload the approved batch.",
        params_schema={
            "one_of": [
                {
                    "workflow": "dase7137_stale_cleanup_rebuild",
                    "required": ["refresh_job.backup_path", "refresh_job.date_policy_output_path|run_codex_date_policy", "refresh_job.activity_policy_output_path|run_codex_activity_policy"],
                    "optional": [
                        "refresh_job.timeline_path",
                        "refresh_job.source_originals_path",
                        "refresh_job.confirmed_correspondence_decision_path",
                        "refresh_job.course_code",
                    ],
                },
                {
                    "workflow": "prepared_upload_manifest",
                    "required": ["prepared_upload_manifest_path"],
                },
            ],
            "forbidden": ["command", "shell", "python_code", "javascript_code", "raw_code"],
        },
        local_preapproval_steps=(
            "resolve local source package/timeline/source-original paths",
            "run Codex planning-only date policy when a validated date policy JSON is not supplied",
            "materialize source-first refreshed Office/PDF artifacts",
            "run visual/date audit and require PASS_NO_STALE_VISIBLE_DATE for upload candidates",
            "build a sandbox-only Moodle upload manifest",
            "generate a clean PDF approval preview attachment",
        ),
        post_approval_executor="course_agent_app.course_refresh_job.execute_course_refresh_batch_upload",
        safety_rules=(
            "sandbox course 105330 only",
            "no arbitrary generated code or shell commands in refresh_job",
            "WhatsApp OK code approves only the prepared upload manifest",
            "do not regenerate upload scope after approval",
            "use Codex policy artifacts only; OpenClaw execution paths are unsupported",
        ),
    ),
    "refresh_file_preview": CapabilityDefinition(
        name="refresh_file_preview",
        required_params=("file_path",),
        risk_level="low",
        executor_lane=REFRESH_FILE_PREVIEW_KIND,
        execution_mode="local_file_action",
        verifier="local_file_audit",
        browser_allowed=False,
        approval_action="Prepare review PDF",
        approval_summary="Refresh one WhatsApp-attached local file and send a PDF back for staff review; no Moodle write.",
        params_schema={
            "required": ["file_path"],
            "optional": [
                "reference_pack_path",
                "instructions_path",
                "course_code",
                "semester",
                "resource_name",
                "require_detailed_group_structure",
            ],
            "reference_pack": "Local timeline/calendar/refresh rules under course_files/reference or course_files/pre_refresh/current_reference.",
            "semantic_planning": (
                "A bounded File Change Composition Planner inspects the current request, generic Office structure model, "
                "course readback, and references, then selects specialist planners and exact targets. The manifest must "
                "not pre-encode change domains, target tables, column meanings, or replacement pairs."
            ),
            "date_planning": "Date mappings are produced only by the Date Planner Agent when selected by composition; direct old_text/replacement_text pairs and fixed reference-pack substitutions are not executable.",
            "group_document_planning": (
                "Group-aware changes are produced only by the Group-aware Document Planner from a sanitized current "
                "Group/Grouping snapshot. Runtime never routes by staff phrases, filenames, event names, headers, "
                "Group naming formats, counts, durations, venues, or historical examples."
            ),
            "output": "PDF review copy sent back to WhatsApp, with the refreshed editable file kept as a derived artifact when available; Moodle upload must be a later approved operation.",
            "forbidden": ["command", "shell", "python_code", "javascript_code", "raw_code"],
        },
        local_preapproval_steps=(
            "verify the source file came from a local WhatsApp attachment path",
            "load local timeline/calendar reference pack as grounding context only",
            "build a format-neutral Office structure model with stable IDs and fingerprints",
            "invoke the File Change Composition Planner to select semantic specialists and targets",
            "execute only validated, anchored specialist plans; unselected structures remain unchanged",
            "export a PDF review copy when LibreOffice can render the refreshed Office file",
            "audit visible stale dates and automation traces before returning the file",
        ),
        safety_rules=(
            "local PDF review copy only; no Moodle write and no approval OK code is consumed",
            "do not invent source file paths when a WhatsApp attachment local_path is missing",
            "do not execute direct params.replacements/text_replacements or fixed replacements inferred from the reference pack",
            "do not encode semantic routing in runtime phrase lists, filenames, event names, table headers, Group names, or historical values",
            "PDF-only refresh is blocked unless explicitly allowed as a copy",
            "student-facing output filenames must not include automation or revision trace wording",
            "uploading the result to Moodle must be requested as a separate approved operation",
        ),
    ),
    "refresh_file_batch_preview": CapabilityDefinition(
        name="refresh_file_batch_preview",
        required_params=("file_paths",),
        risk_level="low",
        executor_lane=REFRESH_FILE_BATCH_PREVIEW_KIND,
        execution_mode="local_file_action",
        verifier="local_file_batch_audit",
        browser_allowed=False,
        approval_action="Prepare per-file review PDFs",
        approval_summary=(
            "Refresh multiple current WhatsApp attachments or one ZIP package and send each PDF "
            "separately for staff review; no Moodle write."
        ),
        params_schema={
            "required": ["file_paths"],
            "optional": [
                "batch_id",
                "batch_version",
                "reference_pack_path",
                "instructions_path",
                "course_code",
                "semester",
                "allow_pdf_only_copy",
                "require_detailed_group_structure",
            ],
            "file_paths": (
                "Ordered current-prompt WhatsApp attachment local_path values. A .zip attachment is "
                "safely expanded by runtime; Office files remain individual files."
            ),
            "revision": (
                "For an active batch revision, bind the exact batch_id and batch_version and use only "
                "verified editable paths from that active batch."
            ),
            "output": (
                "One independently delivered PDF review attachment per generated item plus a batch "
                "summary. Final PDF merge is a later finalize_file_batch_preview operation."
            ),
            "forbidden": ["command", "shell", "python_code", "javascript_code", "raw_code"],
        },
        local_preapproval_steps=(
            "verify every top-level source against current WhatsApp attachment provenance or the active batch",
            "safely expand ZIP inputs into bounded run-scoped staging",
            "reuse the single-file Date Planner, conversion, and audit lane for each item",
            "persist stable batch/item IDs and versions",
            "return each generated PDF as an independent review attachment",
        ),
        safety_rules=(
            "local review only; no Moodle write and no approval OK code is consumed",
            "one unauthorized top-level path blocks the whole batch before processing",
            "reject unsafe, encrypted, nested, oversized, or path-traversing ZIP members",
            "partial success applies to refresh failures, not source authorization failures",
            "do not combine batch refresh with Moodle upload/replace in one manifest",
        ),
    ),
    "finalize_file_batch_preview": CapabilityDefinition(
        name="finalize_file_batch_preview",
        required_params=("batch_id", "batch_version"),
        risk_level="low",
        executor_lane=FINALIZE_FILE_BATCH_PREVIEW_KIND,
        execution_mode="local_file_action",
        verifier="local_final_pdf_audit",
        browser_allowed=False,
        approval_action="Combine reviewed PDFs",
        approval_summary=(
            "Combine the latest delivered, audit-passing PDFs from one exact active batch after "
            "explicit staff confirmation; no Moodle write."
        ),
        params_schema={
            "required": ["batch_id", "batch_version"],
            "optional": ["include_item_ids", "final_pdf_name"],
            "confirmation": (
                "Planner selects this capability only for an explicit natural-language request to "
                "confirm the active batch and generate the combined PDF. Generic OK/acknowledgement "
                "is insufficient."
            ),
            "output": "One clean combined PDF preserving eligible item order, with excluded items reported.",
            "forbidden": ["command", "shell", "python_code", "javascript_code", "raw_code"],
        },
        local_preapproval_steps=(
            "bind the request to the exact active batch ID, version, and WhatsApp requester",
            "require all available per-file previews to have been delivered",
            "select only READY_FOR_REVIEW items unless explicit eligible item IDs are supplied",
            "merge pages in stable input order and clear process metadata",
            "verify page count, blank pages, and visible date/trace audit before delivery",
        ),
        safety_rules=(
            "local PDF finalization only; no Moodle write and no approval OK code is consumed",
            "stale batch versions and requester mismatches are blocked",
            "NEEDS_REVIEW, BLOCKED, FAILED, and undelivered items are excluded",
            "later Moodle upload remains a separate current-run approved operation",
        ),
    ),
    "rebuild_reference_pack": CapabilityDefinition(
        name="rebuild_reference_pack",
        required_params=(),
        risk_level="low",
        executor_lane=REBUILD_REFERENCE_PACK_KIND,
        execution_mode="local_file_action",
        verifier="local_reference_pack_audit",
        browser_allowed=False,
        approval_action="Rebuild reference pack",
        approval_summary="Stage uploaded reference files and rebuild local timeline/calendar/schedule/rules references from the target calendar plus staff-provided class weekdays; no Moodle write.",
        params_schema={
            "required": [
                "reference_files or explicit reference paths before execution",
                "class_weekdays from the current staff prompt",
            ],
            "optional": [
                "reference_files",
                "class_weekdays",
                "build_report_path",
                "calendar_report_path",
                "timeline_reference_path",
                "calendar_reference_path",
                "source_calendar_reference_path",
                "target_calendar_reference_path",
                "syllabus_reference_path",
                "reference_pack_path",
                "course_code",
                "semester",
            ],
            "reference_files": (
                "List of objects with WhatsApp attachment local_path/path plus a planner-selected role based on file content, not filename. "
                "Roles describe semantic content and are independent of filename extensions or document containers. timeline_reference means the content actually "
                "contains a readable Week/Date/Lecture structure; source_calendar_reference means old-year calendar facts used to resolve source calendar week, "
                "teaching week, and weekday; target_calendar_reference means the new semester calendar used to generate target dates. The legacy calendar_reference "
                "role remains accepted when the source/target purpose cannot yet be distinguished; do not force one attachment into each role. "
                "The runtime detects available parsers from file content and accepts a candidate only after the required structured facts are extracted. "
                "Allowed roles include timeline_build_report, calendar_grounding_report, timeline_reference, source_calendar_reference, target_calendar_reference, "
                "calendar_reference, syllabus_reference, "
                "refresh_instructions, timeline_json, calendar_json, and reference. The planner may omit this only to let runtime collect reference attachments "
                "after selecting rebuild_reference_pack."
            ),
            "class_weekdays": "Canonical English weekday names derived by the planner from the current staff prompt, for example [\"Tuesday\"]. This is mandatory for execution and must not be inherited from an older reference pack.",
            "output": "timeline.json and semester_schedule.json with both teaching_week and calendar_week, target calendar.json, optional source_calendar.json with old-year date coordinates, syllabus_rules.json, source_manifest.json, plus stable refresh_rules.md.",
            "forbidden": ["command", "shell", "python_code", "javascript_code", "raw_code"],
        },
        local_preapproval_steps=(
            "verify uploaded reference files are local attachment paths",
            "stage source files under the target reference pack sources folder",
            "use the planner-declared content roles for raw timeline/calendar references, structured reports, or existing reference JSON files",
            "derive target teaching dates from class_weekdays while excluding calendar holidays and teaching suspensions",
            "resolve old timeline dates through the source academic-year calendar when one is supplied",
            "rebuild timeline.json and semester_schedule.json with teaching_week plus calendar_week, then rebuild syllabus_rules.json and source_manifest.json; keep refresh_rules.md",
        ),
        safety_rules=(
            "local reference data only; no Moodle write and no approval OK code is consumed",
            "do not invent reference file paths when a WhatsApp attachment local_path is missing",
            "do not preserve or infer class weekdays from an older semester_schedule.json when the current rebuild prompt omits them",
            "for source-year conflicts, preserve explicit Timeline teaching rows and record timeline_overrides_source_calendar evidence; source calendar remains auxiliary context",
            "when content exceeds valid teaching slots, leave the current JSON pack unchanged and create a versioned timeline review draft",
            "extract only supported timeline DOCX tables and HKU semester calendar PDFs, including calendar-marked holidays/suspensions when present; return a missing-input result instead of inventing dates",
        ),
    ),
    "update_semester_schedule": CapabilityDefinition(
        name="update_semester_schedule",
        required_params=(),
        risk_level="low",
        executor_lane=UPDATE_SEMESTER_SCHEDULE_KIND,
        execution_mode="local_file_action",
        verifier="local_semester_schedule_audit",
        browser_allowed=False,
        approval_action="Update semester schedule",
        approval_summary=(
            "Atomically reflow local semester_schedule.json and timeline.json from current WhatsApp schedule attachments or prompt text, using actual teaching-week numbering and calendar.json to exclude class-suspended dates; "
            "no Moodle write."
        ),
        params_schema={
            "required": ["schedule_files or schedule_text"],
            "optional": [
                "schedule_files",
                "schedule_text",
                "course_code",
                "semester",
                "reference_pack_path",
                "merge_mode",
            ],
            "schedule_files": "List of current WhatsApp attachment local_path values containing raw schedule data.",
            "schedule_text": "Current staff prompt text describing week/day class arrangements.",
            "merge_mode": "replace_all only; the uploaded/parsed schedule must cover every teaching week represented by timeline.json.",
            "output": "semester_schedule.json and timeline.json in the target local reference pack, with source_manifest.json checksums refreshed.",
            "forbidden": ["command", "shell", "python_code", "javascript_code", "raw_code"],
        },
        local_preapproval_steps=(
            "verify schedule files are current WhatsApp attachment paths when files are supplied",
            "parse JSON/CSV/XLSX/DOCX schedule tables deterministically where possible",
            "use the bounded Schedule Planner Agent only when natural language must be structured",
            "validate ISO dates, weekdays, week uniqueness, target semester bounds, and calendar class-suspension blocks before writing",
            "reflow timeline content onto the new actual teaching dates and validate complete Teaching Week 1-N coverage",
            "backup the previous semester_schedule.json, timeline.json, and source_manifest.json in evidence before replacing them",
        ),
        safety_rules=(
            "local reference data only; no Moodle write and no approval OK code is consumed",
            "do not invent schedule file paths when a WhatsApp attachment local_path is missing",
            "replace the full semester schedule instead of merging partial old and new arrangements",
            "when content exceeds valid teaching slots, leave the current JSON pack unchanged and create a versioned timeline review draft",
            "block rather than guess when natural-language class arrangements cannot be mapped to concrete dates",
        ),
    ),
    "revise_timeline_draft": CapabilityDefinition(
        name="revise_timeline_draft",
        required_params=("draft_id", "draft_version"),
        risk_level="low",
        executor_lane=REVISE_TIMELINE_DRAFT_KIND,
        execution_mode="local_file_action",
        verifier="timeline_draft_revision_audit",
        browser_allowed=False,
        approval_action="Revise timeline draft",
        approval_summary=(
            "Apply staff-requested moves or deletions to the exact active timeline draft version and send a new preview; no Moodle write."
        ),
        params_schema={
            "required": ["draft_id", "draft_version"],
            "input": "The current staff prompt plus the exact active draft shown in planner context.",
            "allowed_changes": ["move_items", "delete_items"],
            "forbidden": [
                "rewriting content text",
                "changing dates directly",
                "using a stale draft version",
                "committing in the same prompt",
            ],
        },
        local_preapproval_steps=(
            "bind the operation to the exact active immutable draft version",
            "use the bounded Timeline Reconciliation Agent to select stable content item IDs",
            "when a complete occupied teaching week is released, let the planner explicitly reflow later content items into the available teaching slots unless staff preserves the gap",
            "validate that only move_items and delete_items are present",
            "save a new immutable draft version and send its PDF or DOCX preview",
        ),
        safety_rules=(
            "local timeline review only; no Moodle write and no approval OK code is consumed",
            "planner cannot rewrite original content text or assign arbitrary dates",
            "all target teaching weeks must be valid slots in the active draft",
            "calendar holidays, reading weeks, and suspensions are not content weeks and are never shifted by draft revisions",
            "revision and final confirmation must be separate staff prompts",
        ),
    ),
    "commit_timeline_draft": CapabilityDefinition(
        name="commit_timeline_draft",
        required_params=("draft_id", "draft_version"),
        risk_level="low",
        executor_lane=COMMIT_TIMELINE_DRAFT_KIND,
        execution_mode="local_file_action",
        verifier="timeline_draft_commit_audit",
        browser_allowed=False,
        approval_action="Commit timeline draft",
        approval_summary=(
            "Atomically commit the exact latest staff-reviewed timeline draft to the local reference pack; no Moodle write."
        ),
        params_schema={
            "required": ["draft_id", "draft_version"],
            "precondition": "The active exact version is READY_FOR_CONFIRMATION and the current prompt is a separate explicit natural-language confirmation.",
            "output": "Atomic local reference JSON commit with backups, checksums, and content_week_mappings.",
        },
        local_preapproval_steps=(
            "bind to the exact latest active draft version",
            "verify all non-deleted content is in valid teaching weeks",
            "stage and checksum all target JSON payloads",
            "back up current reference JSON and atomically replace the complete commit set",
        ),
        safety_rules=(
            "local reference data only; no Moodle write and no approval OK code is consumed",
            "stale versions, historical drafts, ambiguous confirmation, and same-prompt revise plus confirm must block",
            "final reference JSON cannot contain overflow content",
            "a failed multi-file commit must restore every canonical file",
        ),
    ),
    "browser_moodle_task": CapabilityDefinition(
        name="browser_moodle_task",
        required_params=("browser_goal", "browser_steps", "readback_expectations"),
        risk_level="medium",
        executor_lane="browser_moodle_task",
        execution_mode="agent_browser_action",
        verifier="browser_task_readback",
        browser_allowed=True,
        approval_action="Update Moodle",
        approval_summary=(
            "Execute one bounded sandbox Moodle browser task through the constrained Playwright DSL "
            "when no stable API or project browser capability covers the staff request."
        ),
        params_schema={
            "required": [
                "fallback_reason",
                "browser_goal",
                "browser_steps",
                "readback_expectations|verification_steps",
            ],
            "browser_step_types": [
                "goto",
                "click",
                "fill",
                "select",
                "check",
                "uncheck",
                "press",
                "wait",
                "wait_for_text",
                "assert_text",
                "assert_no_text",
                "screenshot",
                "readback_course_contents",
            ],
            "verification_expectation_types": [
                "module_present",
                "module_absent",
                "text_present",
                "text_absent",
                "url_contains",
            ],
            "forbidden": [
                "javascript_code",
                "python_code",
                "shell",
                "raw_code",
                "credentials",
                "non_sandbox_url",
                "legacy_course_url",
            ],
        },
        safety_rules=(
            "use a stable capability or API lane first when one covers the task",
            "sandbox course 105330 only",
            "the staff prompt must be bounded enough to produce explicit browser steps and a clear approval boundary",
            "browser steps must use only the constrained DSL; no arbitrary JavaScript or generated code",
            "Moodle writes still require current-run WhatsApp OK approval before execution",
            "readback_expectations or verification_steps must prove the requested result before reporting PASS",
        ),
    ),
}


def capability_names() -> list[str]:
    return sorted(CAPABILITY_REGISTRY)


def capability_specs() -> list[dict[str, Any]]:
    return [
        {
            "capability": definition.name,
            "required_params": list(definition.required_params),
            "risk_level": definition.risk_level,
            "executor_lane": definition.executor_lane,
            "execution_mode": definition.execution_mode,
            "verifier": definition.verifier,
            "browser_allowed": definition.browser_allowed,
            "approval_action": definition.approval_action,
            "approval_summary": definition.approval_summary,
            "params_schema": definition.params_schema or {},
            "local_preapproval_steps": list(definition.local_preapproval_steps),
            "post_approval_executor": definition.post_approval_executor,
            "safety_rules": list(definition.safety_rules),
        }
        for definition in sorted(CAPABILITY_REGISTRY.values(), key=lambda item: item.name)
    ]


def get_capability(name: str) -> CapabilityDefinition:
    try:
        return CAPABILITY_REGISTRY[name]
    except KeyError as exc:
        raise ValueError(f"Unsupported Moodle capability: {name!r}") from exc


def validate_capability_operation(operation: dict[str, Any], index: int) -> list[str]:
    errors: list[str] = []
    capability = str(operation.get("capability", "")).strip()
    if not capability:
        return errors
    if capability == "browser_moodle_task":
        kind = str(operation.get("operation_kind", "")).strip()
        if kind and kind != "browser_moodle_task":
            errors.append(
                f"operations[{index}].operation_kind {kind!r} does not match capability lane 'browser_moodle_task'"
            )
        return errors
    definition = CAPABILITY_REGISTRY.get(capability)
    if definition is None:
        return [f"operations[{index}].capability is unsupported: {capability}"]
    params = operation.get("params")
    if not isinstance(params, dict):
        return [f"operations[{index}].params must be an object for capability {capability}"]
    course_id = str(operation.get("target_course_id") or SANDBOX_COURSE_ID)
    if course_id != SANDBOX_COURSE_ID:
        errors.append(f"operations[{index}].target_course_id must be sandbox course {SANDBOX_COURSE_ID}")
    missing = _missing_required_params(definition, params)
    if missing:
        errors.append(f"operations[{index}].params missing required capability params: {missing}")
    if capability == "update_grouping_membership":
        add_names = set(_list_param(params, "add_group_names", "add_groups"))
        remove_names = set(_list_param(params, "remove_group_names", "remove_groups"))
        overlap = sorted(add_names & remove_names, key=str.casefold)
        if overlap:
            errors.append(
                f"operations[{index}].update_grouping_membership cannot add and remove the same Groups: {overlap}"
            )
    if capability == "update_group_membership":
        action = str(_param(params, "action", "membership_action") or "").strip().casefold()
        if action not in {"add", "remove", "move"}:
            errors.append(
                f"operations[{index}].update_group_membership action must be add, remove, or move"
            )
        user_id = str(_param(params, "user_id", "moodle_user_id") or "").strip()
        user_name = str(
            _param(params, "user_name", "participant_name", "name_query") or ""
        ).strip()
        if bool(user_id) == bool(user_name):
            errors.append(
                f"operations[{index}].update_group_membership requires exactly one of user_id or user_name"
            )
        elif user_id and (not user_id.isdigit() or int(user_id) <= 0):
            errors.append(
                f"operations[{index}].update_group_membership user_id must be a positive numeric Moodle id"
            )
        forbidden_identity_fields = sorted(
            key
            for key in {"user_email", "email"}
            if params.get(key) not in (None, "")
        )
        if forbidden_identity_fields:
            errors.append(
                f"operations[{index}].update_group_membership does not support participant email fields: "
                f"{forbidden_identity_fields}"
            )
    kind = str(operation.get("operation_kind", "")).strip()
    if kind and kind != definition.executor_lane:
        if not (capability == "delete_modules_in_section" and kind in {"browser_module_delete", "browser_course_file_resource_delete"}):
            errors.append(
                f"operations[{index}].operation_kind {kind!r} does not match capability lane "
                f"{definition.executor_lane!r}"
            )
    if capability == "course_refresh_batch_upload":
        refresh_operation = _refresh_operation_from_params(operation, params)
        errors.extend(validate_refresh_job_shape(refresh_operation))
    if capability == "refresh_file_preview":
        errors.extend(validate_refresh_file_preview_shape(operation))
    if capability == "refresh_file_batch_preview":
        errors.extend(validate_refresh_file_batch_preview_shape(operation))
    if capability == "finalize_file_batch_preview":
        errors.extend(validate_finalize_file_batch_preview_shape(operation))
    if capability == "rebuild_reference_pack":
        errors.extend(validate_rebuild_reference_pack_shape(operation))
    if capability == "update_semester_schedule":
        errors.extend(validate_update_semester_schedule_shape(operation))
    if capability == "revise_timeline_draft":
        errors.extend(validate_revise_timeline_draft_shape(operation))
    if capability == "commit_timeline_draft":
        errors.extend(validate_commit_timeline_draft_shape(operation))
    return errors


def resolve_capability_manifest(
    manifest: dict[str, Any],
    *,
    readback_context: dict[str, Any] | None = None,
    latest_resource: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = dict(manifest)
    operations = normalized.get("operations")
    if not isinstance(operations, list):
        return normalized
    resolved: list[dict[str, Any]] = []
    known_runtime_refs: set[str] = set()
    group_state = _initial_group_resolution_state(readback_context or {})
    no_change_notes: list[str] = []
    for index, operation in enumerate(operations, start=1):
        declared_refs = _declared_runtime_target_refs(operation) if isinstance(operation, dict) else set()
        duplicate_refs = sorted(declared_refs & known_runtime_refs)
        if duplicate_refs:
            raise ValueError(
                f"operations[{index}] declares runtime reference(s) already produced earlier: {duplicate_refs}"
            )
        if not isinstance(operation, dict) or not str(operation.get("capability", "")).strip():
            resolved.append(operation)
            known_runtime_refs.update(declared_refs)
            continue
        if str(operation.get("capability", "")).strip() == "browser_moodle_task":
            resolved.append(operation)
            known_runtime_refs.update(declared_refs)
            continue
        resolved_operations = resolve_capability_operation(
            operation,
            readback_context=readback_context or {},
            latest_resource=latest_resource or {},
            index=index,
            known_runtime_refs=known_runtime_refs,
            group_state=group_state,
        )
        if not resolved_operations:
            no_change_notes.append(
                (
                    "The requested Grouping additions and removals are already "
                    "satisfied by the current Moodle structure"
                )
                if str(operation.get("capability") or "")
                == "update_grouping_membership"
                else "The requested Moodle state is already satisfied"
            )
        resolved.extend(resolved_operations)
        for resolved_operation in resolved_operations:
            known_runtime_refs.update(_declared_runtime_target_refs(resolved_operation))
    normalized["operations"] = resolved
    if operations and not resolved and no_change_notes:
        normalized["read_only"] = True
        normalized["requires_approval"] = False
        normalized["resolution_status"] = "no_changes"
        normalized["resolution_notes"] = no_change_notes
    return normalized


def resolve_capability_operation(
    operation: dict[str, Any],
    *,
    readback_context: dict[str, Any],
    latest_resource: dict[str, Any],
    index: int = 1,
    known_runtime_refs: set[str] | None = None,
    group_state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    capability = str(operation.get("capability", "")).strip()
    if capability == "browser_moodle_task":
        return [operation]
    definition = get_capability(capability)
    params = operation.get("params")
    if not isinstance(params, dict):
        raise ValueError(f"operations[{index}].params must be an object for capability {capability}")
    missing = _missing_required_params(definition, params)
    if missing:
        raise ValueError(f"operations[{index}].params missing required capability params: {missing}")
    if capability == "delete_modules_in_section":
        return _resolve_delete_modules_in_section(operation, definition, params, readback_context, index)
    if capability == "delete_module":
        return [_resolve_delete_module(operation, definition, params, readback_context, index)]
    if capability == "delete_file_resource":
        return [_resolve_delete_file_resource(operation, definition, params, readback_context, latest_resource, index)]
    if capability == "inspect_group_structure":
        return [_resolve_inspect_group_structure(operation, definition, params, readback_context)]
    if capability == "inspect_forum_structure":
        return [_resolve_inspect_forum_structure(operation, definition, params)]
    if capability == "update_grouping_membership":
        return _resolve_update_grouping_membership(
            operation,
            definition,
            params,
            readback_context,
            group_state or {},
            index,
        )
    if capability == "update_group_membership":
        return [
            _resolve_update_group_membership(
                operation,
                definition,
                params,
                readback_context,
                group_state or {},
                index,
            )
        ]
    if capability == "delete_group":
        return [
            _resolve_delete_group(
                operation,
                definition,
                params,
                readback_context,
                group_state or {},
                index,
            )
        ]
    if capability == "delete_grouping":
        return [
            _resolve_delete_grouping(
                operation,
                definition,
                params,
                readback_context,
                group_state or {},
                index,
            )
        ]
    if capability == "create_group_signup_activity":
        return [
            _resolve_group_signup_activity(
                operation,
                definition,
                params,
                readback_context,
                group_state=group_state or {},
                index=index,
            )
        ]
    if capability == "create_assignment_activity":
        return [_resolve_assignment_activity(operation, definition, params, readback_context)]
    if capability == "create_forum_activity":
        return [_resolve_forum_activity(operation, definition, params, readback_context)]
    if capability == "rename_section":
        return [_resolve_rename_section(operation, definition, params, readback_context)]
    if capability == "course_refresh_batch_upload":
        return [_resolve_course_refresh_batch_upload(operation, definition, params)]
    if capability == "refresh_file_preview":
        return [_resolve_refresh_file_preview(operation, definition, params)]
    if capability == "refresh_file_batch_preview":
        return [_resolve_refresh_file_batch_preview(operation, definition, params)]
    if capability == "finalize_file_batch_preview":
        return [_resolve_finalize_file_batch_preview(operation, definition, params)]
    if capability == "rebuild_reference_pack":
        return [_resolve_rebuild_reference_pack(operation, definition, params)]
    if capability == "update_semester_schedule":
        return [_resolve_update_semester_schedule(operation, definition, params)]
    if capability in {"revise_timeline_draft", "commit_timeline_draft"}:
        return [_resolve_timeline_draft_operation(operation, definition, params)]
    if capability == "create_grouping":
        resolved_grouping = _resolve_create_grouping(
            operation,
            definition,
            params,
            readback_context,
            group_state=group_state or {},
            index=index,
        )
        snapshot_fields = _group_snapshot_fields(group_state or {})
        for item in resolved_grouping:
            item.update(snapshot_fields)
        _apply_planned_group_operation(group_state or {}, capability, params)
        return resolved_grouping
    resolved_simple = _resolve_simple_capability(
        operation,
        definition,
        params,
        readback_context,
        known_runtime_refs,
    )
    if capability == "create_group":
        resolved_simple.update(_group_snapshot_fields(group_state or {}))
    _apply_planned_group_operation(group_state or {}, capability, params)
    return [resolved_simple]


def _initial_group_resolution_state(readback_context: dict[str, Any]) -> dict[str, Any]:
    structure = (
        readback_context.get("group_structure")
        if isinstance(readback_context, dict) and isinstance(readback_context.get("group_structure"), dict)
        else {}
    )
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in structure.get("groups") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            groups.setdefault(name, []).append(dict(item))
    groupings: dict[str, list[dict[str, Any]]] = {}
    grouping_members: dict[str, set[str]] = {}
    for item in structure.get("groupings") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        groupings.setdefault(name, []).append(dict(item))
        grouping_members[name] = {
            str(value).strip() for value in item.get("group_names") or [] if str(value or "").strip()
        }
    return {
        "status": str(structure.get("status") or ""),
        "snapshot_hash": str(structure.get("snapshot_hash") or ""),
        "action_preflight_hash": str(structure.get("action_preflight_hash") or ""),
        "groups": groups,
        "groupings": groupings,
        "grouping_members": grouping_members,
    }


def _require_detailed_group_state(group_state: dict[str, Any], index: int, capability: str) -> None:
    if str(group_state.get("status") or "") != "detailed":
        raise ValueError(
            f"operations[{index}].{capability} requires detailed sandbox Group structure readback"
        )


def _unique_group_state_item(
    group_state: dict[str, Any],
    *,
    collection: str,
    name: str,
    noun: str,
    index: int,
    capability: str,
) -> dict[str, Any]:
    matches = (group_state.get(collection) or {}).get(name, [])
    if not matches:
        raise ValueError(f"operations[{index}].{capability} could not find exact {noun} {name!r}")
    if len(matches) != 1:
        raise ValueError(f"operations[{index}].{capability} target {noun} {name!r} is ambiguous")
    return dict(matches[0])


def _group_snapshot_fields(group_state: dict[str, Any]) -> dict[str, Any]:
    snapshot_hash = str(
        group_state.get("action_preflight_hash")
        or group_state.get("snapshot_hash")
        or ""
    ).strip()
    return (
        {
            "group_snapshot_hash": snapshot_hash,
            "group_preflight_required": True,
        }
        if snapshot_hash
        else {}
    )


def _apply_planned_group_operation(
    group_state: dict[str, Any],
    capability: str,
    params: dict[str, Any],
) -> None:
    if not group_state:
        return
    if capability == "create_group":
        name = str(_param(params, "name", "group_name")).strip()
        if name:
            group_state.setdefault("groups", {}).setdefault(
                name,
                [
                    {
                        "id": "",
                        "name": name,
                        "member_count": 0,
                        "grouping_names": [],
                        "choicegroup_refs": [],
                        "planned": True,
                    }
                ],
            )
    elif capability == "create_grouping":
        name = str(_param(params, "name", "grouping_name")).strip()
        names = set(_list_param(params, "group_names", "groups"))
        if name:
            group_state.setdefault("groupings", {}).setdefault(
                name,
                [{"id": "", "name": name, "group_names": sorted(names), "planned": True}],
            )
            group_state.setdefault("grouping_members", {})[name] = set(names)


def _missing_required_params(definition: CapabilityDefinition, params: dict[str, Any]) -> list[str]:
    if definition.name == "inspect_group_structure":
        scope = str(_param(params, "scope")).strip()
        return [] if scope in {"course", "groups", "groupings", "choicegroup_dependencies"} else ["scope"]
    if definition.name == "inspect_forum_structure":
        scope = str(_param(params, "scope")).strip()
        return [] if scope in {"course", "forums"} else ["scope"]
    if definition.name == "update_grouping_membership":
        missing: list[str] = []
        if _param(params, "grouping_name", "grouping", "name") in ("", None):
            missing.append("grouping_name")
        add_names = _list_param(params, "add_group_names", "add_groups")
        remove_names = _list_param(params, "remove_group_names", "remove_groups")
        if not add_names and not remove_names:
            missing.append("add_group_names|remove_group_names")
        return missing
    if definition.name == "update_group_membership":
        missing: list[str] = []
        action = str(_param(params, "action", "membership_action") or "").strip().casefold()
        if action not in {"add", "remove", "move"}:
            missing.append("action:add|remove|move")
        user_id = str(_param(params, "user_id", "moodle_user_id") or "").strip()
        user_name = str(
            _param(params, "user_name", "participant_name", "name_query") or ""
        ).strip()
        if bool(user_id) == bool(user_name):
            missing.append("exactly one of user_id|user_name")
        source = _param(params, "source_group_name", "source_group", "from_group")
        target = _param(params, "target_group_name", "target_group", "to_group")
        if action in {"remove", "move"} and source in ("", None):
            missing.append("source_group_name")
        if action in {"add", "move"} and target in ("", None):
            missing.append("target_group_name")
        return missing
    if definition.name == "create_forum_post":
        target_params = _flatten_target_params(params)
        missing: list[str] = []
        if _param(target_params, "subject", "title") in ("", None):
            missing.append("subject")
        if _param(target_params, "body", "message", "content") in ("", None):
            missing.append("body/message")
        if _param(
            target_params,
            "forum_name",
            "forum",
            "target_forum",
            "target_forum_name",
            "target_forum_id",
            "forumid",
            "forum_id",
            "target_ref",
        ) in ("", None):
            missing.append("forum_name/target_forum_id/target_ref")
        return missing
    if definition.name == "reply_forum_post":
        target_params = _flatten_target_params(params)
        missing: list[str] = []
        if _param(target_params, "subject", "title") in ("", None):
            missing.append("subject")
        if _param(target_params, "body", "message", "content") in ("", None):
            missing.append("body/message")
        if _param(
            target_params,
            "forum_name",
            "forum",
            "target_forum",
            "target_forum_name",
            "target_forum_id",
            "forumid",
            "forum_id",
        ) in ("", None):
            missing.append("forum_name/target_forum_id")
        if _param(
            target_params,
            "discussion_id",
            "target_discussion_id",
            "discussionid",
            "discussion_url",
            "target_discussion_url",
            "discussion_title",
            "target_discussion_title",
            "target_subject",
        ) in ("", None):
            missing.append("discussion_id/discussion_url/discussion_title")
        return missing
    if definition.name == "delete_forum_reply":
        target_params = _flatten_target_params(params)
        missing: list[str] = []
        if _param(
            target_params,
            "forum_name",
            "forum",
            "target_forum",
            "target_forum_name",
            "target_forum_id",
            "forumid",
            "forum_id",
        ) in ("", None):
            missing.append("forum_name/target_forum_id")
        if _param(
            target_params,
            "discussion_id",
            "target_discussion_id",
            "discussionid",
            "discussion_url",
            "target_discussion_url",
            "discussion_title",
            "target_discussion_title",
        ) in ("", None):
            missing.append("discussion_id/discussion_url/discussion_title")
        if _param(
            target_params,
            "reply_post_id",
            "target_reply_post_id",
            "reply_post_url",
            "target_reply_post_url",
            "reply_text",
            "reply_body",
            "target_ref",
        ) in ("", None):
            missing.append("reply_post_id/reply_post_url/reply_text/target_ref")
        return missing
    if definition.name == "delete_forum_post":
        target_params = _flatten_target_params(params)
        if _param(
            target_params,
            "post_id",
            "target_post_id",
            "postid",
            "post_url",
            "target_post_url",
            "discussion_id",
            "target_discussion_id",
            "discussionid",
            "discussion_url",
            "target_discussion_url",
        ):
            return []
        target_text = _param(
            target_params,
            "target_subject",
            "subject",
            "target_ref",
            "title",
            "name",
            "text",
            "message",
            "body",
            "content",
        )
        forum_ref = _param(
            target_params,
            "forum_name",
            "forum",
            "target_forum",
            "target_forum_name",
            "forum_url",
            "target_forum_url",
            "forum_cmid",
            "target_forum_cmid",
            "forum_module_id",
        )
        if target_text and forum_ref:
            return []
        return ["post_url/discussion_url/post_id/discussion_id or forum_name/forum_url plus target_subject/target_ref"]
    if definition.name == "create_group_signup_activity":
        missing: list[str] = []
        if _param(params, "section", "section_name", "sectionnum", "section_number") in ("", None):
            missing.append("section")
        if _param(params, "title", "name", "activity_name") in ("", None):
            missing.append("title/name")
        if not _list_param(params, "group_names", "groups", "choicegroup_target_group_names"):
            missing.append("group_names")
        return missing
    if definition.name in {"create_assignment_activity", "create_forum_activity"}:
        missing = []
        if _param(params, "section", "section_name", "sectionnum", "section_number") in ("", None):
            missing.append("section")
        if _param(params, "title", "name", "activity_name") in ("", None):
            missing.append("title/name")
        return missing
    if definition.name == "delete_file_resource":
        if _resource_target_from_params(params):
            return []
        if _param(params, "target_ref", "target", "ref"):
            return []
        return ["target_module_id/cmid/resource_name or target_ref"]
    if definition.name == "course_refresh_batch_upload":
        if params.get("refresh_job") or params.get("prepared_upload_manifest_path"):
            return []
        return ["refresh_job or prepared_upload_manifest_path"]
    if definition.name == "refresh_file_preview":
        if _param(params, "file_path", "source_file_path", "path"):
            return []
        return ["file_path"]
    if definition.name == "refresh_file_batch_preview":
        file_paths = params.get("file_paths") or params.get("files")
        if isinstance(file_paths, list) and any(str(item or "").strip() for item in file_paths):
            return []
        return ["file_paths"]
    if definition.name == "finalize_file_batch_preview":
        missing = []
        if not _param(params, "batch_id"):
            missing.append("batch_id")
        if not _param(params, "batch_version"):
            missing.append("batch_version")
        return missing
    if definition.name == "delete_modules_in_section":
        if _param(params, "section", "section_name", "sectionnum", "section_number"):
            return []
        if params.get("resolved_targets") or params.get("targets"):
            return []
        if _delete_target_filter(params):
            return []
        return ["section or unique module target"]
    if definition.name == "delete_module":
        if _param(params, "target_module_id", "cmid", "target_ref", "target", "name", "title", "target_object"):
            return []
        return ["target_module_id/cmid or unique target_ref"]
    if definition.name == "set_visibility":
        missing = []
        if not _param(params, "target_module_id", "cmid", "target_ref"):
            missing.append("target_module_id")
        if "visible" not in params and "target_visible" not in params:
            missing.append("visible")
        return missing
    if definition.name == "set_restriction_time":
        missing = []
        if not _param(params, "target_module_id", "cmid", "target_ref"):
            missing.append("target_module_id")
        restriction_value = _restriction_time_param(params)
        if not restriction_value:
            missing.append("restriction_time")
        return missing
    if definition.name == "rename_activity":
        missing = []
        if not _param(params, "target_module_id", "cmid", "target_ref"):
            missing.append("target_module_id")
        if "new_title" not in params:
            missing.append("new_title")
        return missing
    missing = []
    for key in definition.required_params:
        if key == "new_title":
            if key not in params:
                missing.append(key)
            continue
        if key == "target":
            continue
        if _param(params, key) in ("", None):
            missing.append(key)
    return missing


def _current_forum_facts(readback_context: dict[str, Any]) -> list[dict[str, Any]] | None:
    forum_structure = readback_context.get("forum_structure") if isinstance(readback_context, dict) else None
    if not isinstance(forum_structure, dict):
        return None
    if str(forum_structure.get("status") or "").casefold() != "ok":
        reason = str(forum_structure.get("reason") or "forum readback unavailable").strip()
        raise ValueError(f"Current-course forum target readback is unavailable: {reason}")

    forum_modules = [
        _with_forum_defaults(module)
        for module in _modules_from_readback(readback_context)
        if _looks_like_forum_module(module)
    ]
    facts: list[dict[str, Any]] = []
    for item in forum_structure.get("forums", []) or []:
        if not isinstance(item, dict):
            continue
        fact = dict(item)
        instance_id = str(fact.get("id") or "").strip()
        module_matches = [
            module
            for module in forum_modules
            if str(module.get("instance") or module.get("forum_id") or "").strip() == instance_id
        ]
        if len(module_matches) == 1:
            module = module_matches[0]
            fact["cmid"] = module.get("cmid")
            fact["url"] = module.get("url")
            fact["section_name"] = module.get("section_name")
            fact["sectionnum"] = module.get("sectionnum")
        facts.append(fact)
    return facts


def _resolve_current_forum_target(
    readback_context: dict[str, Any],
    *,
    forum_name: str = "",
    forum_id: str = "",
    require_news: bool = False,
    operation_label: str,
) -> dict[str, Any]:
    facts = _current_forum_facts(readback_context)
    if facts is None:
        return {}
    matches = list(facts)
    if require_news:
        matches = [item for item in matches if _normalize_text(item.get("type")) == "news"]
    if forum_id:
        matches = [item for item in matches if str(item.get("id") or "").strip() == forum_id]
    if forum_name:
        matches = [item for item in matches if _normalize_text(item.get("name")) == _normalize_text(forum_name)]
    if len(matches) != 1:
        target = "announcement/news forum" if require_news else "approved forum target"
        raise ValueError(
            f"{operation_label} requires one exact current-course {target}; fresh readback found {len(matches)} matches"
        )
    return matches[0]


def _forum_operation_fields(forum: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "target_forum_id": str(forum.get("id") or ""),
        "target_forum_name": str(forum.get("name") or ""),
    }
    if str(forum.get("cmid") or "").strip():
        fields["target_forum_cmid"] = str(forum.get("cmid"))
        fields["target_module_id"] = str(forum.get("cmid"))
    if str(forum.get("url") or "").strip():
        fields["target_forum_url"] = str(forum.get("url"))
    return fields


def _forum_env_fields(forum: dict[str, Any]) -> dict[str, str]:
    fields = {
        "MOODLE_TARGET_FORUM_ID": str(forum.get("id") or ""),
        "MOODLE_TARGET_FORUM_NAME": str(forum.get("name") or ""),
        "MOODLE_TARGET_FORUM_CMID": str(forum.get("cmid") or ""),
        "MOODLE_TARGET_MODULE_ID": str(forum.get("cmid") or ""),
    }
    return {key: value for key, value in fields.items() if value}


def _numeric_query_or_fragment_id(url: str, query_key: str, fragment_prefix: str = "") -> str:
    text = str(url or "").strip()
    if query_key:
        query_match = re.search(
            rf"(?:[?&]){re.escape(query_key)}=(\d+)(?:[&#]|$)",
            text,
        )
        if query_match:
            return query_match.group(1)
    if fragment_prefix:
        fragment_match = re.search(rf"#{re.escape(fragment_prefix)}(\d+)(?:$|[^\d])", text)
        if fragment_match:
            return fragment_match.group(1)
    return ""


def _resolve_current_forum_discussion(
    forum: dict[str, Any],
    params: dict[str, Any],
    *,
    operation_label: str = "reply_forum_post",
) -> dict[str, Any]:
    discussion_id = str(
        _param(params, "discussion_id", "target_discussion_id", "discussionid")
    ).strip()
    discussion_url = str(
        _param(params, "discussion_url", "target_discussion_url")
    ).strip()
    discussion_title = str(
        _param(
            params,
            "discussion_title",
            "target_discussion_title",
            "target_subject",
        )
    ).strip()
    if not discussion_id and discussion_url:
        discussion_id = _numeric_query_or_fragment_id(discussion_url, "d")

    discussions = forum.get("discussions")
    if not isinstance(discussions, list):
        raise ValueError(
            f"{operation_label} requires detailed current-course Forum discussion readback"
        )
    matches = [item for item in discussions if isinstance(item, dict)]
    if discussion_id:
        matches = [
            item
            for item in matches
            if str(item.get("discussion_id") or item.get("id") or "").strip()
            == discussion_id
        ]
    if discussion_url:
        normalized_url = discussion_url.split("#", 1)[0].rstrip("/")
        matches = [
            item
            for item in matches
            if str(item.get("url") or "").split("#", 1)[0].rstrip("/")
            == normalized_url
        ]
    if discussion_title:
        matches = [
            item
            for item in matches
            if _normalize_text(item.get("title") or item.get("subject"))
            == _normalize_text(discussion_title)
        ]
    if len(matches) != 1:
        raise ValueError(
            f"{operation_label} requires one exact current-course discussion target; "
            f"fresh detailed readback found {len(matches)} matches"
        )
    return dict(matches[0])


def _resolve_simple_capability(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
    readback_context: dict[str, Any],
    known_runtime_refs: set[str] | None = None,
) -> dict[str, Any]:
    resolved = _base_operation(operation, definition, params)
    env = dict(resolved.get("env_overrides") or {})
    capability = definition.name
    if capability == "create_announcement":
        subject = str(_param(params, "subject", "title")).strip()
        body = str(_param(params, "body", "message", "content")).strip()
        deferred_ref = _deferred_runtime_target_ref(params, known_runtime_refs)
        forum_target = (
            {}
            if deferred_ref
            else _resolve_current_forum_target(
                readback_context,
                require_news=True,
                operation_label="create_announcement",
            )
        )
        env.update(
            {
                "MOODLE_OPERATION_KIND": "announcement_api",
                "MOODLE_ANNOUNCEMENT_SUBJECT": subject,
                "MOODLE_ANNOUNCEMENT_BODY": body,
            }
        )
        resolved.update(
            {
                "action": "publish_announcement",
                "target_ref": deferred_ref,
                "subject": subject,
                "message": body,
                "target_object": subject,
                "approval_item": subject,
            }
        )
        if forum_target:
            resolved.update(_forum_operation_fields(forum_target))
            env.update(_forum_env_fields(forum_target))
    elif capability == "create_forum_post":
        target_params = _flatten_target_params(params)
        subject = str(_param(target_params, "subject", "title")).strip()
        body = str(_param(target_params, "body", "message", "content")).strip()
        forum_name = str(_param(target_params, "forum_name", "forum", "target_forum", "target_forum_name")).strip()
        forum_id = str(_param(target_params, "target_forum_id", "forumid", "forum_id")).strip()
        deferred_ref = _deferred_runtime_target_ref(target_params, known_runtime_refs)
        forum_target = (
            {}
            if deferred_ref
            else _resolve_current_forum_target(
                readback_context,
                forum_name=forum_name,
                forum_id=forum_id,
                operation_label="create_forum_post",
            )
        )
        if forum_target:
            forum_name = str(forum_target.get("name") or forum_name).strip()
            forum_id = str(forum_target.get("id") or forum_id).strip()
        target = forum_name or (f"forum {forum_id}" if forum_id else "Moodle forum")
        env.update(
            {
                "MOODLE_OPERATION_KIND": "announcement_api",
                "MOODLE_FORUM_ACTION": "publish_forum_post",
                "MOODLE_FORUM_POST_SUBJECT": subject,
                "MOODLE_FORUM_POST_MESSAGE": body,
            }
        )
        if forum_name:
            env["MOODLE_TARGET_FORUM_NAME"] = forum_name
        if forum_id:
            env["MOODLE_TARGET_FORUM_ID"] = forum_id
        resolved.update(
            {
                "action": "publish_forum_post",
                "target_ref": deferred_ref,
                "subject": subject,
                "message": body,
                "target_forum_name": forum_name,
                "target_forum_id": forum_id,
                "target_object": f"{target} forum",
                "approval_item": f"{target} forum: {subject}",
            }
        )
        if forum_target:
            resolved.update(_forum_operation_fields(forum_target))
            env.update(_forum_env_fields(forum_target))
    elif capability == "reply_forum_post":
        target_params = _flatten_target_params(params)
        subject = str(_param(target_params, "subject", "title")).strip()
        body = str(_param(target_params, "body", "message", "content")).strip()
        forum_name = str(
            _param(
                target_params,
                "forum_name",
                "forum",
                "target_forum",
                "target_forum_name",
            )
        ).strip()
        forum_id = str(
            _param(target_params, "target_forum_id", "forumid", "forum_id")
        ).strip()
        forum_target = _resolve_current_forum_target(
            readback_context,
            forum_name=forum_name,
            forum_id=forum_id,
            operation_label="reply_forum_post",
        )
        if not forum_target:
            raise ValueError(
                "reply_forum_post requires current-course Forum readback before approval"
            )
        discussion = _resolve_current_forum_discussion(
            forum_target,
            target_params,
        )
        forum_name = str(forum_target.get("name") or forum_name).strip()
        forum_id = str(forum_target.get("id") or forum_id).strip()
        discussion_id = str(
            discussion.get("discussion_id") or discussion.get("id") or ""
        ).strip()
        discussion_title = str(
            discussion.get("title")
            or discussion.get("subject")
            or _param(
                target_params,
                "discussion_title",
                "target_discussion_title",
                "target_subject",
            )
        ).strip()
        discussion_url = str(discussion.get("url") or "").strip()
        parent_post_url = str(
            _param(target_params, "post_url", "target_post_url")
        ).strip()
        parent_post_id = str(
            _param(
                target_params,
                "parent_post_id",
                "reply_to_post_id",
                "post_id",
                "target_post_id",
                "postid",
            )
        ).strip()
        if not parent_post_id and parent_post_url:
            parent_post_id = (
                _numeric_query_or_fragment_id(parent_post_url, "p")
                or _numeric_query_or_fragment_id(parent_post_url, "", "p")
            )
        env.update(
            {
                "MOODLE_OPERATION_KIND": "browser_forum_post_reply",
                "MOODLE_FORUM_ACTION": "reply_forum_post",
                "MOODLE_FORUM_POST_SUBJECT": subject,
                "MOODLE_FORUM_POST_MESSAGE": body,
                "MOODLE_FORUM_DISCUSSION_ID": discussion_id,
                "MOODLE_FORUM_DISCUSSION_URL": discussion_url,
                "MOODLE_FORUM_DISCUSSION_TITLE": discussion_title,
            }
        )
        if parent_post_id:
            env["MOODLE_FORUM_POST_ID"] = parent_post_id
        resolved.update(
            {
                "operation_kind": definition.executor_lane,
                "execution_mode": definition.execution_mode,
                "action": "reply_forum_post",
                "subject": subject,
                "message": body,
                "target_forum_name": forum_name,
                "target_forum_id": forum_id,
                "target_discussion_id": discussion_id,
                "target_discussion_url": discussion_url,
                "target_discussion_title": discussion_title,
                "target_post_id": parent_post_id,
                "target_post_url": parent_post_url,
                "target_object": f"{forum_name} forum: {discussion_title}",
                "approval_item": f"{forum_name} forum: {discussion_title}",
            }
        )
        resolved.update(_forum_operation_fields(forum_target))
        env.update(_forum_env_fields(forum_target))
    elif capability == "delete_forum_reply":
        target_params = _flatten_target_params(params)
        forum_name = str(
            _param(
                target_params,
                "forum_name",
                "forum",
                "target_forum",
                "target_forum_name",
            )
        ).strip()
        forum_id = str(
            _param(target_params, "target_forum_id", "forumid", "forum_id")
        ).strip()
        forum_target = _resolve_current_forum_target(
            readback_context,
            forum_name=forum_name,
            forum_id=forum_id,
            operation_label="delete_forum_reply",
        )
        discussion = _resolve_current_forum_discussion(
            forum_target,
            target_params,
            operation_label="delete_forum_reply",
        )
        forum_name = str(forum_target.get("name") or forum_name).strip()
        forum_id = str(forum_target.get("id") or forum_id).strip()
        discussion_id = str(
            discussion.get("discussion_id") or discussion.get("id") or ""
        ).strip()
        discussion_url = str(discussion.get("url") or "").strip()
        discussion_title = str(
            discussion.get("title")
            or discussion.get("subject")
            or _param(
                target_params,
                "discussion_title",
                "target_discussion_title",
            )
        ).strip()
        reply_post_url = str(
            _param(
                target_params,
                "reply_post_url",
                "target_reply_post_url",
            )
        ).strip()
        reply_post_id = str(
            _param(
                target_params,
                "reply_post_id",
                "target_reply_post_id",
            )
        ).strip()
        if not reply_post_id and reply_post_url:
            reply_post_id = (
                _numeric_query_or_fragment_id(reply_post_url, "delete")
                or _numeric_query_or_fragment_id(reply_post_url, "", "p")
            )
        reply_text = str(
            _param(
                target_params,
                "reply_text",
                "reply_body",
                "target_ref",
            )
        ).strip()
        reply_subject = str(
            _param(
                target_params,
                "reply_subject",
                "target_reply_subject",
            )
        ).strip()
        env.update(
            {
                "MOODLE_OPERATION_KIND": "browser_forum_reply_delete",
                "MOODLE_FORUM_ACTION": "delete_forum_reply",
                "MOODLE_FORUM_DISCUSSION_ID": discussion_id,
                "MOODLE_FORUM_DISCUSSION_URL": discussion_url,
                "MOODLE_FORUM_DISCUSSION_TITLE": discussion_title,
            }
        )
        for key, value in {
            "MOODLE_FORUM_REPLY_POST_ID": reply_post_id,
            "MOODLE_FORUM_REPLY_POST_URL": reply_post_url,
            "MOODLE_FORUM_REPLY_TARGET_TEXT": reply_text,
            "MOODLE_FORUM_REPLY_TARGET_SUBJECT": reply_subject,
        }.items():
            if value:
                env[key] = value
        resolved.update(
            {
                "operation_kind": definition.executor_lane,
                "execution_mode": definition.execution_mode,
                "action": "delete_forum_reply",
                "target_forum_name": forum_name,
                "target_forum_id": forum_id,
                "target_discussion_id": discussion_id,
                "target_discussion_url": discussion_url,
                "target_discussion_title": discussion_title,
                "target_reply_post_id": reply_post_id,
                "target_reply_post_url": reply_post_url,
                "target_reply_text": reply_text,
                "target_reply_subject": reply_subject,
                "target_object": (
                    f"{forum_name} forum: reply in {discussion_title}"
                ),
                "approval_item": (
                    f"{forum_name} forum: reply in {discussion_title}"
                ),
            }
        )
        resolved.update(_forum_operation_fields(forum_target))
        env.update(_forum_env_fields(forum_target))
    elif capability == "delete_forum_post":
        target_params = _flatten_target_params(params)
        forum_module = _resolve_forum_module(target_params, readback_context)
        post_id = str(_param(target_params, "post_id", "target_post_id", "postid")).strip()
        discussion_id = str(_param(target_params, "discussion_id", "target_discussion_id", "discussionid")).strip()
        post_url = str(_param(target_params, "post_url", "target_post_url", "url", "target_url")).strip()
        discussion_url = str(_param(target_params, "discussion_url", "target_discussion_url")).strip()
        subject = str(_param(target_params, "subject", "target_subject", "title", "name")).strip()
        target_ref = str(_param(target_params, "target_ref", "target", "text", "message", "body", "content")).strip()
        forum_name = str(
            _param(target_params, "forum_name", "forum", "target_forum", "target_forum_name")
            or forum_module.get("name", "")
        ).strip()
        forum_id = str(
            _param(target_params, "target_forum_id", "forumid", "forum_id")
            or forum_module.get("instance", "")
            or forum_module.get("forum_id", "")
        ).strip()
        forum_url = str(
            _param(target_params, "target_forum_url", "forum_url", "forum_link", "module_url")
            or forum_module.get("url", "")
        ).strip()
        forum_cmid = str(
            _param(target_params, "target_forum_cmid", "forum_cmid", "forum_module_id")
            or forum_module.get("cmid", "")
        ).strip()
        if not forum_url and forum_cmid.isdigit():
            forum_url = f"https://migrationtest-moodle.hku.hk/mod/forum/view.php?id={forum_cmid}"
        target = subject or target_ref or (f"post {post_id}" if post_id else "") or (f"discussion {discussion_id}" if discussion_id else "")
        env.update(
            {
                "MOODLE_OPERATION_KIND": "browser_forum_post_delete",
                "MOODLE_FORUM_ACTION": "delete_forum_post",
            }
        )
        for key, value in {
            "MOODLE_FORUM_POST_ID": post_id,
            "MOODLE_FORUM_POST_URL": post_url,
            "MOODLE_FORUM_DISCUSSION_ID": discussion_id,
            "MOODLE_FORUM_DISCUSSION_URL": discussion_url,
            "MOODLE_FORUM_TARGET_SUBJECT": subject,
            "MOODLE_TARGET_FORUM_NAME": forum_name,
            "MOODLE_TARGET_FORUM_ID": forum_id,
            "MOODLE_TARGET_FORUM_URL": forum_url,
            "MOODLE_TARGET_FORUM_CMID": forum_cmid,
        }.items():
            if value:
                env[key] = value
        resolved.update(
            {
                "action": "delete_forum_post",
                "target_post_id": post_id,
                "target_post_url": post_url,
                "target_discussion_id": discussion_id,
                "target_discussion_url": discussion_url,
                "target_subject": subject,
                "target_ref": target_ref,
                "target_forum_name": forum_name,
                "target_forum_id": forum_id,
                "target_forum_url": forum_url,
                "target_forum_cmid": forum_cmid,
                "target_object": target or "Forum message",
                "approval_item": target or "Forum message",
            }
        )
    elif capability == "create_group":
        name = str(_param(params, "name", "group_name")).strip()
        env.update({"MOODLE_OPERATION_KIND": "group_api", "MOODLE_GROUP_NAME": name})
        if params.get("description"):
            env["MOODLE_GROUP_DESCRIPTION"] = str(params["description"])
        resolved.update({"action": "create_group", "group_name": name, "target_object": name, "approval_item": name})
    elif capability == "create_grouping":
        name = str(_param(params, "name", "grouping_name")).strip()
        env.update({"MOODLE_OPERATION_KIND": "grouping_api", "MOODLE_GROUPING_NAME": name})
        if params.get("idnumber"):
            env["MOODLE_GROUPING_IDNUMBER"] = str(params["idnumber"])
        if params.get("description"):
            env["MOODLE_GROUPING_DESCRIPTION"] = str(params["description"])
        resolved.update(
            {
                "action": "create_grouping",
                "grouping_name": name,
                "idnumber": str(params.get("idnumber", "")),
                "target_object": name,
                "approval_item": name,
            }
        )
    elif capability == "upload_file_resource":
        file_path = str(_param(params, "file_path", "path", "upload_path")).strip()
        resource_name = str(_param(params, "resource_name", "name", "title", "target_object") or Path(file_path).stem).strip()
        section = _resolve_section(params, readback_context)
        visible_param = _param(params, "visible", "resource_visible")
        visible = True if visible_param in ("", None) else _truthy(visible_param)
        expected_filename = str(_param(params, "expected_uploaded_filename", "filename") or Path(file_path).name)
        env.update(
            {
                "MOODLE_OPERATION_KIND": definition.executor_lane,
                "MOODLE_UPLOAD_FILE_PATH": file_path,
                "MOODLE_EXPECTED_UPLOADED_FILENAME": expected_filename,
                "MOODLE_RESOURCE_NAME": resource_name,
                "MOODLE_RESOURCE_VISIBLE": "1" if visible else "0",
            }
        )
        if section:
            env.update(_section_env(section))
        resolved.update(
            {
                "action": "create_file_resource",
                "resource_name": resource_name,
                "target_object": resource_name,
                "approval_item": resource_name,
                "target_file_path": file_path,
                "upload_file_path": file_path,
                "expected_uploaded_filename": expected_filename,
                "visible": visible,
                "expected_visibility": "Visible" if visible else "Hidden",
                **_section_public_fields(section),
            }
        )
    elif capability == "replace_file_resource":
        target = _resolve_module_target(params, readback_context)
        file_path = str(_param(params, "file_path", "path", "upload_path")).strip()
        env.update(
            {
                "MOODLE_OPERATION_KIND": definition.executor_lane,
                "MOODLE_TARGET_MODULE_ID": str(target.get("cmid", "")),
                "MOODLE_TARGET_FILE_PATH": file_path,
                "MOODLE_UPLOAD_FILE_PATH": file_path,
                "MOODLE_TEST_OBJECT_NAME": str(target.get("name", "")),
            }
        )
        resolved.update(
            {
                "action": "upload_or_replace_file",
                "target_module_id": str(target.get("cmid", "")),
                "target_object": str(target.get("name", "")),
                "approval_item": str(target.get("name", "")),
                "target_file_path": file_path,
                "upload_file_path": file_path,
            }
        )
    elif capability == "rename_activity":
        deferred_ref = _deferred_runtime_target_ref(params, known_runtime_refs)
        target = (
            {
                "cmid": "",
                "name": str(_param(params, "target_title", "title", "name", "target_object") or deferred_ref),
            }
            if deferred_ref
            else _resolve_module_target(params, readback_context)
        )
        new_title = str(params.get("new_title", ""))
        env.update(
            {
                "MOODLE_OPERATION_KIND": definition.executor_lane,
                "MOODLE_TARGET_MODULE_ID": str(target.get("cmid", "")),
                "MOODLE_ORIGINAL_TITLE": str(target.get("name", "")),
                "MOODLE_TARGET_TITLE": new_title,
            }
        )
        resolved.update(
            {
                "action": "edit_assignment_title",
                "target_module_id": str(target.get("cmid", "")),
                "target_ref": deferred_ref,
                "target_object": str(target.get("name", "")),
                "approval_item": str(target.get("name", "")),
                "original_title": str(target.get("name", "")),
                "target_title": new_title,
            }
        )
    elif capability == "set_visibility":
        visible = _truthy(params.get("visible"))
        deferred_ref = _deferred_runtime_target_ref(params, known_runtime_refs)
        target = (
            {
                "cmid": "",
                "name": str(_param(params, "target_title", "title", "name", "target_object") or deferred_ref),
            }
            if deferred_ref
            else _resolve_module_target(params, readback_context)
        )
        env.update(
            {
                "MOODLE_OPERATION_KIND": definition.executor_lane,
                "MOODLE_TARGET_MODULE_ID": str(target.get("cmid", "")),
                "MOODLE_TARGET_TITLE": str(target.get("name", "")),
                "MOODLE_TARGET_VISIBLE": "1" if visible else "0",
            }
        )
        resolved.update(
            {
                "operation_kind": definition.executor_lane,
                "execution_mode": definition.execution_mode,
                "action": "set_visibility",
                "target_module_id": str(target.get("cmid", "")),
                "target_ref": deferred_ref,
                "target_object": str(target.get("name", "")),
                "approval_item": str(target.get("name", "")),
                "visible": visible,
                "expected_visibility": "Visible" if visible else "Hidden",
            }
        )
    elif capability == "set_restriction_time":
        deferred_ref = _deferred_runtime_target_ref(params, known_runtime_refs)
        target = (
            {
                "cmid": "",
                "name": str(_param(params, "target_title", "title", "name", "target_object") or deferred_ref),
            }
            if deferred_ref
            else _resolve_module_target(params, readback_context)
        )
        raw_restriction_time = _restriction_time_param(params)
        clear_requested = _is_clear_restriction_request(raw_restriction_time)
        restriction_time = "" if clear_requested else str(raw_restriction_time).strip()
        env.update(
            {
                "MOODLE_OPERATION_KIND": definition.executor_lane,
                "MOODLE_TARGET_MODULE_ID": str(target.get("cmid", "")),
                "MOODLE_TARGET_ACCESS_TIME": "clear" if clear_requested else restriction_time,
                "MOODLE_ACCESS_TIME_ACTION": "clear" if clear_requested else "set",
                "MOODLE_TEST_OBJECT_NAME": str(target.get("name", "")),
            }
        )
        resolved.update(
            {
                "action": "set_access_restriction_time",
                "access_time_action": "clear" if clear_requested else "set",
                "target_module_id": str(target.get("cmid", "")),
                "target_ref": deferred_ref,
                "target_object": str(target.get("name", "")),
                "approval_item": str(target.get("name", "")),
                "target_value": "clear" if clear_requested else restriction_time,
            }
        )
    resolved["env_overrides"] = env
    return resolved


def _resolve_group_signup_activity(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
    readback_context: dict[str, Any],
    *,
    group_state: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    resolved = _base_operation(operation, definition, params)
    section = _resolve_section(params, readback_context)
    title = str(_param(params, "title", "name", "activity_name")).strip()
    group_names = _dedupe_group_names(
        _list_param(params, "group_names", "groups", "choicegroup_target_group_names")
    )
    grouping_name = str(_param(params, "grouping_name", "grouping")).strip()
    if grouping_name:
        _require_detailed_group_state(group_state, index, definition.name)
        _unique_group_state_item(
            group_state,
            collection="groupings",
            name=grouping_name,
            noun="Grouping",
            index=index,
            capability=definition.name,
        )
        for group_name in group_names:
            _unique_group_state_item(
                group_state,
                collection="groups",
                name=group_name,
                noun="Group",
                index=index,
                capability=definition.name,
            )
        grouping_members = set((group_state.get("grouping_members") or {}).get(grouping_name, set()))
        outside_grouping = sorted(set(group_names) - grouping_members, key=str.casefold)
        if outside_grouping:
            raise ValueError(
                f"operations[{index}].{definition.name} Groups are not in Grouping "
                f"{grouping_name!r}: {outside_grouping}"
            )
    intro = str(_param(params, "intro", "description")).strip()
    visible_param = _param(params, "visible", "resource_visible")
    visible = True if visible_param in ("", None) else _truthy(visible_param)
    expected_visibility = "Show on course page" if visible else "Hide on course page"
    env = dict(resolved.get("env_overrides") or {})
    env.update(
        {
            "MOODLE_OPERATION_KIND": definition.executor_lane,
            "MOODLE_SECTION_NUM": str(section.get("sectionnum", "")),
            "MOODLE_SECTION_NAME": str(section.get("name", "")),
            "MOODLE_ACTIVITY_TITLE": title,
            "MOODLE_GROUP_NAMES": json.dumps(group_names, ensure_ascii=False),
            "MOODLE_EXPECTED_VISIBILITY": expected_visibility,
        }
    )
    if grouping_name:
        env["MOODLE_GROUPING_NAME"] = grouping_name
    resolved.update(
        {
            "operation_kind": definition.executor_lane,
            "execution_mode": definition.execution_mode,
            "action": "create_group_signup_activity",
            "modname": "choicegroup",
            "title": title,
            "target_object": title,
            "approval_item": title,
            "sectionnum": str(section.get("sectionnum", "")),
            "section_name": str(section.get("name", "")),
            "section_id": str(section.get("section_id", "")),
            "grouping_name": grouping_name,
            "group_names": group_names,
            "choicegroup_target_group_names": group_names,
            "intro": intro,
            "visible": visible,
            "expected_visibility": expected_visibility,
            "approval_scope": f"Choicegroup sign-up for groups {', '.join(group_names)}",
            "env_overrides": env,
            **_group_snapshot_fields(group_state),
        }
    )
    maxanswers = _choicegroup_maxanswers(params, group_names, index=index)
    if maxanswers is not None:
        resolved["maxanswers"] = maxanswers
    return resolved


def _resolve_inspect_group_structure(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
    readback_context: dict[str, Any],
) -> dict[str, Any]:
    resolved = _base_operation(operation, definition, params)
    scope = str(_param(params, "scope")).strip()
    group_names = _dedupe_group_names(_list_param(params, "group_names", "groups"))
    grouping_names = _dedupe_group_names(
        _list_param(params, "grouping_names", "groupings")
    )
    include_choicegroups = _truthy(
        params.get("include_choicegroup_dependencies", scope == "choicegroup_dependencies")
    )
    resolved.update(
        {
            "operation_kind": definition.executor_lane,
            "execution_mode": definition.execution_mode,
            "action": "inspect_group_structure",
            "scope": scope,
            "group_names": group_names,
            "grouping_names": grouping_names,
            "include_choicegroup_dependencies": include_choicegroups,
            "target_object": "Sandbox Group structure",
            "approval_item": "Sandbox Group structure",
            "requires_approval": False,
            "read_only": True,
            "env_overrides": {},
        }
    )
    return resolved


def _resolve_inspect_forum_structure(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
) -> dict[str, Any]:
    resolved = _base_operation(operation, definition, params)
    scope = str(_param(params, "scope")).strip()
    forum_names = _dedupe_group_names(_list_param(params, "forum_names", "forums"))
    resolved.update(
        {
            "operation_kind": definition.executor_lane,
            "execution_mode": definition.execution_mode,
            "action": "inspect_forum_structure",
            "scope": scope,
            "forum_names": forum_names,
            "target_object": "Sandbox Forum structure",
            "approval_item": "Sandbox Forum structure",
            "requires_approval": False,
            "read_only": True,
            "env_overrides": {},
        }
    )
    return resolved


def _resolve_update_grouping_membership(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
    readback_context: dict[str, Any],
    group_state: dict[str, Any],
    index: int,
) -> list[dict[str, Any]]:
    _require_detailed_group_state(group_state, index, definition.name)
    grouping_name = str(_param(params, "grouping_name", "grouping", "name")).strip()
    grouping = _unique_group_state_item(
        group_state,
        collection="groupings",
        name=grouping_name,
        noun="Grouping",
        index=index,
        capability=definition.name,
    )
    add_names = _dedupe_group_names(
        _list_param(params, "add_group_names", "add_groups")
    )
    remove_names = _dedupe_group_names(
        _list_param(params, "remove_group_names", "remove_groups")
    )
    overlap = sorted(set(add_names) & set(remove_names), key=str.casefold)
    if overlap:
        raise ValueError(
            f"operations[{index}].{definition.name} cannot add and remove the same Groups: {overlap}"
        )
    for name in [*add_names, *remove_names]:
        _unique_group_state_item(
            group_state,
            collection="groups",
            name=name,
            noun="Group",
            index=index,
            capability=definition.name,
        )
    current_members = set((group_state.get("grouping_members") or {}).get(grouping_name, set()))
    effective_add = [name for name in add_names if name not in current_members]
    effective_remove = [name for name in remove_names if name in current_members]
    operations: list[dict[str, Any]] = []
    snapshot_fields = _group_snapshot_fields(group_state)
    if effective_add:
        add_operation = _base_operation(operation, definition, params)
        add_operation.update(
            {
                "action": "assign_group_names_to_grouping",
                "grouping_id": str(grouping.get("id") or ""),
                "grouping_name": grouping_name,
                "group_names": effective_add,
                "target_object": f"{grouping_name} grouping members",
                "approval_item": f"{grouping_name} grouping",
                "approval_scope": f"Add Groups {', '.join(effective_add)} to Grouping {grouping_name}",
                "summary": f"Add Groups {', '.join(effective_add)} to Moodle Grouping {grouping_name}.",
                "env_overrides": {
                    "MOODLE_OPERATION_KIND": definition.executor_lane,
                    "MOODLE_GROUPING_ACTION": "assign_group_names_to_grouping",
                    "MOODLE_GROUPING_NAME": grouping_name,
                    "MOODLE_GROUP_NAMES": json.dumps(effective_add, ensure_ascii=False),
                },
                **snapshot_fields,
            }
        )
        operations.append(add_operation)
        current_members.update(effective_add)
    if effective_remove:
        remove_operation = _base_operation(operation, definition, params)
        remove_operation.update(
            {
                "action": "unassign_group_names_from_grouping",
                "grouping_id": str(grouping.get("id") or ""),
                "grouping_name": grouping_name,
                "group_names": effective_remove,
                "target_object": f"{grouping_name} grouping members",
                "approval_item": f"{grouping_name} grouping",
                "approval_scope": f"Remove Groups {', '.join(effective_remove)} from Grouping {grouping_name}",
                "summary": f"Remove Groups {', '.join(effective_remove)} from Moodle Grouping {grouping_name}.",
                "env_overrides": {
                    "MOODLE_OPERATION_KIND": definition.executor_lane,
                    "MOODLE_GROUPING_ACTION": "unassign_group_names_from_grouping",
                    "MOODLE_GROUPING_NAME": grouping_name,
                    "MOODLE_GROUP_NAMES": json.dumps(effective_remove, ensure_ascii=False),
                },
                **snapshot_fields,
            }
        )
        operations.append(remove_operation)
        current_members.difference_update(effective_remove)
    group_state.setdefault("grouping_members", {})[grouping_name] = current_members
    grouping["group_names"] = sorted(current_members, key=str.casefold)
    group_state.setdefault("groupings", {})[grouping_name] = [grouping]
    return operations


def _resolve_update_group_membership(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
    readback_context: dict[str, Any],
    group_state: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    _require_detailed_group_state(group_state, index, definition.name)
    action = str(_param(params, "action", "membership_action") or "").strip().casefold()
    if action not in {"add", "remove", "move"}:
        raise ValueError(
            f"operations[{index}].{definition.name} action must be add, remove, or move"
        )
    user_id = str(_param(params, "user_id", "moodle_user_id") or "").strip()
    user_name_query = str(
        _param(params, "user_name", "participant_name", "name_query") or ""
    ).strip()
    if bool(user_id) == bool(user_name_query):
        raise ValueError(
            f"operations[{index}].{definition.name} requires exactly one of user_id or user_name"
        )
    if user_id and (not user_id.isdigit() or int(user_id) <= 0):
        raise ValueError(
            f"operations[{index}].{definition.name} requires a positive numeric Moodle user_id"
        )
    forbidden_identity_fields = sorted(
        key
        for key in {"user_email", "email"}
        if params.get(key) not in (None, "")
    )
    if forbidden_identity_fields:
        raise ValueError(
            f"operations[{index}].{definition.name} does not support participant email fields: "
            f"{forbidden_identity_fields}"
        )
    source_name = str(
        _param(params, "source_group_name", "source_group", "from_group") or ""
    ).strip()
    target_name = str(
        _param(params, "target_group_name", "target_group", "to_group") or ""
    ).strip()
    source: dict[str, Any] = {}
    target: dict[str, Any] = {}
    if action in {"remove", "move"}:
        source = _unique_group_state_item(
            group_state,
            collection="groups",
            name=source_name,
            noun="Group",
            index=index,
            capability=definition.name,
        )
    if action in {"add", "move"}:
        target = _unique_group_state_item(
            group_state,
            collection="groups",
            name=target_name,
            noun="Group",
            index=index,
            capability=definition.name,
        )
    if action == "move" and source_name == target_name:
        raise ValueError(
            f"operations[{index}].{definition.name} source and target Groups must be different"
        )
    source_id = str(source.get("id") or "").strip()
    target_id = str(target.get("id") or "").strip()
    if action in {"remove", "move"} and (not source_id.isdigit() or int(source_id) <= 0):
        raise ValueError(
            f"operations[{index}].{definition.name} source Group requires an existing numeric id"
        )
    if action in {"add", "move"} and (not target_id.isdigit() or int(target_id) <= 0):
        raise ValueError(
            f"operations[{index}].{definition.name} target Group requires an existing numeric id"
        )

    participant_method = (
        "exact numeric Moodle user id"
        if user_id
        else "staff-supplied name with a required unique Moodle match"
    )
    if action == "add":
        boundary = f"Add one participant to Group {target_name}"
        target_object = f"{target_name} Group membership"
        summary = f"Add one uniquely resolved participant to Group {target_name}."
    elif action == "remove":
        boundary = f"Remove one participant from Group {source_name}"
        target_object = f"{source_name} Group membership"
        summary = f"Remove one uniquely resolved participant from Group {source_name}."
    else:
        boundary = (
            f"Move one participant from Group {source_name} to Group {target_name}"
        )
        target_object = f"{source_name} to {target_name} Group membership"
        summary = (
            f"Move one uniquely resolved participant from Group {source_name} "
            f"to Group {target_name}."
        )
    resolved = _base_operation(operation, definition, params)
    resolved.update(
        {
            "action": f"{action}_group_member",
            "membership_action": action,
            "user_id": user_id,
            "user_name_query": user_name_query,
            "participant_lookup_mode": "user_id" if user_id else "user_name",
            "source_group_id": source_id,
            "source_group_name": source_name,
            "target_group_id": target_id,
            "target_group_name": target_name,
            "target_object": target_object,
            "approval_item": target_object,
            "approval_scope": (
                f"{boundary}; participant resolution uses {participant_method}"
            ),
            "summary": summary,
            "env_overrides": {
                "MOODLE_OPERATION_KIND": definition.executor_lane,
                "MOODLE_MEMBERSHIP_ACTION": action,
                "MOODLE_USER_ID": user_id,
                "MOODLE_USER_NAME_QUERY": user_name_query,
                "MOODLE_SOURCE_GROUP_ID": source_id,
                "MOODLE_SOURCE_GROUP_NAME": source_name,
                "MOODLE_TARGET_GROUP_ID": target_id,
                "MOODLE_TARGET_GROUP_NAME": target_name,
            },
            **_group_snapshot_fields(group_state),
        }
    )
    return resolved


def _resolve_delete_group(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
    readback_context: dict[str, Any],
    group_state: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    _require_detailed_group_state(group_state, index, definition.name)
    name = str(_param(params, "name", "group_name")).strip()
    group = _unique_group_state_item(
        group_state,
        collection="groups",
        name=name,
        noun="Group",
        index=index,
        capability=definition.name,
    )
    member_count = group.get("member_count")
    if not isinstance(member_count, int):
        raise ValueError(f"operations[{index}].{definition.name} requires a verified member count")
    if member_count:
        raise ValueError(
            f"operations[{index}].{definition.name} refuses non-empty Group {name!r} "
            f"with {member_count} member(s)"
        )
    choicegroup_refs = [ref for ref in group.get("choicegroup_refs") or [] if isinstance(ref, dict)]
    if choicegroup_refs:
        titles = sorted(
            {str(ref.get("title") or "Choicegroup").strip() for ref in choicegroup_refs},
            key=str.casefold,
        )
        raise ValueError(
            f"operations[{index}].{definition.name} refuses Group {name!r} because it is used by "
            f"Choicegroup activities: {titles}"
        )
    linked_groupings = sorted(
        name_ for name_, members in (group_state.get("grouping_members") or {}).items() if name in members
    )
    if linked_groupings:
        raise ValueError(
            f"operations[{index}].{definition.name} requires earlier explicit unassign operations "
            f"for Groupings: {linked_groupings}"
        )
    group_id = str(group.get("id") or "").strip()
    if not group_id.isdigit():
        raise ValueError(f"operations[{index}].{definition.name} requires an existing numeric Group id")
    resolved = _base_operation(operation, definition, params)
    resolved.update(
        {
            "action": "delete_group",
            "group_id": group_id,
            "group_name": name,
            "target_object": name,
            "approval_item": name,
            "approval_scope": (
                f"Delete Group {name}; members 0; Grouping links none; "
                "Choicegroup references none"
            ),
            "env_overrides": {
                "MOODLE_OPERATION_KIND": definition.executor_lane,
                "MOODLE_GROUP_ACTION": "delete_group",
                "MOODLE_GROUP_ID": group_id,
                "MOODLE_GROUP_NAME": name,
            },
            **_group_snapshot_fields(group_state),
        }
    )
    group_state.setdefault("groups", {}).pop(name, None)
    return resolved


def _resolve_delete_grouping(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
    readback_context: dict[str, Any],
    group_state: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    _require_detailed_group_state(group_state, index, definition.name)
    name = str(_param(params, "name", "grouping_name")).strip()
    grouping = _unique_group_state_item(
        group_state,
        collection="groupings",
        name=name,
        noun="Grouping",
        index=index,
        capability=definition.name,
    )
    members = sorted((group_state.get("grouping_members") or {}).get(name, set()), key=str.casefold)
    if members:
        raise ValueError(
            f"operations[{index}].{definition.name} requires earlier explicit unassign operations "
            f"for Groups: {members}"
        )
    grouping_id = str(grouping.get("id") or "").strip()
    if not grouping_id.isdigit():
        raise ValueError(f"operations[{index}].{definition.name} requires an existing numeric Grouping id")
    resolved = _base_operation(operation, definition, params)
    resolved.update(
        {
            "action": "delete_grouping",
            "grouping_id": grouping_id,
            "grouping_name": name,
            "target_object": name,
            "approval_item": name,
            "approval_scope": f"Delete empty Grouping {name}",
            "env_overrides": {
                "MOODLE_OPERATION_KIND": definition.executor_lane,
                "MOODLE_GROUPING_ACTION": "delete_grouping",
                "MOODLE_GROUPING_ID": grouping_id,
                "MOODLE_GROUPING_NAME": name,
            },
            **_group_snapshot_fields(group_state),
        }
    )
    group_state.setdefault("groupings", {}).pop(name, None)
    group_state.setdefault("grouping_members", {}).pop(name, None)
    return resolved


def _resolve_assignment_activity(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
    readback_context: dict[str, Any],
) -> dict[str, Any]:
    resolved = _base_operation(operation, definition, params)
    section = _resolve_section(params, readback_context)
    title = str(_param(params, "title", "name", "activity_name")).strip()
    intro = str(_param(params, "intro", "description")).strip()
    visible_param = _param(params, "visible", "resource_visible")
    visible = True if visible_param in ("", None) else _truthy(visible_param)
    expected_visibility = "Show on course page" if visible else "Hide on course page"
    env = dict(resolved.get("env_overrides") or {})
    env.update(
        {
            "MOODLE_OPERATION_KIND": definition.executor_lane,
            "MOODLE_SECTION_NUM": str(section.get("sectionnum", "")),
            "MOODLE_SECTION_NAME": str(section.get("name", "")),
            "MOODLE_ACTIVITY_TITLE": title,
            "MOODLE_ACTIVITY_INTRO": intro,
            "MOODLE_EXPECTED_VISIBILITY": expected_visibility,
        }
    )
    resolved.update(
        {
            "operation_kind": definition.executor_lane,
            "execution_mode": definition.execution_mode,
            "action": "create_assignment_activity",
            "modname": "assign",
            "title": title,
            "target_object": title,
            "approval_item": title,
            "sectionnum": str(section.get("sectionnum", "")),
            "section_name": str(section.get("name", "")),
            "section_id": str(section.get("section_id", "")),
            "intro": intro,
            "visible": visible,
            "expected_visibility": expected_visibility,
            "approval_scope": f"One Assignment activity in {_section_label(section)}",
            "env_overrides": env,
        }
    )
    return resolved


def _resolve_forum_activity(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
    readback_context: dict[str, Any],
) -> dict[str, Any]:
    resolved = _base_operation(operation, definition, params)
    section = _resolve_section(params, readback_context)
    title = str(_param(params, "title", "name", "activity_name")).strip()
    intro = str(_param(params, "intro", "description")).strip()
    visible_param = _param(params, "visible", "resource_visible")
    visible = True if visible_param in ("", None) else _truthy(visible_param)
    expected_visibility = "Show on course page" if visible else "Hide on course page"
    env = dict(resolved.get("env_overrides") or {})
    env.update(
        {
            "MOODLE_OPERATION_KIND": definition.executor_lane,
            "MOODLE_SECTION_NUM": str(section.get("sectionnum", "")),
            "MOODLE_SECTION_NAME": str(section.get("name", "")),
            "MOODLE_ACTIVITY_TITLE": title,
            "MOODLE_ACTIVITY_INTRO": intro,
            "MOODLE_EXPECTED_VISIBILITY": expected_visibility,
        }
    )
    resolved.update(
        {
            "operation_kind": definition.executor_lane,
            "execution_mode": definition.execution_mode,
            "action": "create_forum_activity",
            "modname": "forum",
            "title": title,
            "target_object": title,
            "approval_item": title,
            "sectionnum": str(section.get("sectionnum", "")),
            "section_name": str(section.get("name", "")),
            "section_id": str(section.get("section_id", "")),
            "intro": intro,
            "visible": visible,
            "expected_visibility": expected_visibility,
            "approval_scope": f"One Forum activity in {_section_label(section)}",
            "env_overrides": env,
        }
    )
    return resolved


def _resolve_create_grouping(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
    readback_context: dict[str, Any],
    *,
    group_state: dict[str, Any],
    index: int,
) -> list[dict[str, Any]]:
    grouping_operation = _resolve_simple_capability(operation, definition, params, readback_context)
    group_names = _dedupe_group_names(_list_param(params, "group_names", "groups"))
    if not group_names:
        return [grouping_operation]
    if str(group_state.get("status") or "") == "detailed":
        for group_name in group_names:
            _unique_group_state_item(
                group_state,
                collection="groups",
                name=group_name,
                noun="Group",
                index=index,
                capability=definition.name,
            )

    grouping_name = str(_param(params, "name", "grouping_name")).strip()
    assign_operation = {
        "operation_kind": definition.executor_lane,
        "execution_mode": definition.execution_mode,
        "risk_level": definition.risk_level,
        "target_course_id": SANDBOX_COURSE_ID,
        "target_course_url": SANDBOX_COURSE_URL,
        "action": "assign_group_names_to_grouping",
        "grouping_name": grouping_name,
        "group_names": group_names,
        "target_object": f"{grouping_name} grouping members",
        "approval_action": "Create grouping",
        "approval_item": f"{grouping_name} grouping members",
        "approval_scope": f"Assign groups {', '.join(group_names)} to grouping {grouping_name}",
        "summary": f"Assign groups {', '.join(group_names)} to Moodle grouping {grouping_name}.",
        "verifier": definition.verifier,
        "capability_definition": definition.name,
        "env_overrides": {
            "MOODLE_OPERATION_KIND": definition.executor_lane,
            "MOODLE_GROUPING_ACTION": "assign_group_names_to_grouping",
            "MOODLE_GROUPING_NAME": grouping_name,
            "MOODLE_GROUP_NAMES": json.dumps(group_names, ensure_ascii=False),
        },
    }
    return [grouping_operation, assign_operation]


def _resolve_delete_file_resource(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
    readback_context: dict[str, Any],
    latest_resource: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    target_params = _flatten_target_params(params)
    target = _resource_target_from_params(params) or {}
    target_ref = _normalize_text(_param(target_params, "target_ref", "target", "ref"))
    if target_ref in {"latestupload", "latestfile", "previousupload", "lastuploadedfile", "上一步上传", "最新上传"}:
        if not latest_resource:
            raise ValueError(f"operations[{index}].delete_file_resource needs latest upload context but none is available")
        target = {
            "cmid": latest_resource.get("target_module_id"),
            "name": latest_resource.get("resource_name"),
            "url": latest_resource.get("display_url"),
            "modname": "resource",
        }
    if not target.get("cmid") or not target.get("name"):
        target = _resolve_module_target(params, readback_context)
    if str(target.get("modname", "resource")) != "resource":
        raise ValueError(f"operations[{index}].delete_file_resource target is not a Moodle File resource")
    return _delete_file_resource_operation(operation, definition, params, target)


def _resolve_delete_module(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
    readback_context: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    target = _resolve_unique_module_target(params, readback_context, index, "delete_module")
    return _delete_module_operation(operation, definition, params, target)


def _resolve_delete_modules_in_section(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
    readback_context: dict[str, Any],
    index: int,
) -> list[dict[str, Any]]:
    section = _resolve_section(params, readback_context)
    inferred_scope = False
    if not section:
        section, inferred_modules = _infer_delete_scope_from_readback(params, readback_context, index)
        modules = inferred_modules
        inferred_scope = True
    else:
        modules = _module_targets_from_params(params, section)
    explicit_empty_targets = "resolved_targets" in params and not modules
    if not modules:
        modules = _filter_delete_modules(params, _section_modules(section))
    modules = [module for module in modules if str(module.get("cmid", "")).strip()]
    resolved: list[dict[str, Any]] = []
    for module in modules:
        target = {
            "cmid": module.get("cmid"),
            "name": module.get("name"),
            "url": module.get("url"),
            "modname": module.get("modname"),
        }
        item = _delete_module_operation(operation, definition, params, target)
        item["capability"] = "delete_modules_in_section"
        item["params"] = {
            "target_module_id": str(target["cmid"]),
            "target_object": str(target["name"]),
            "modname": str(target.get("modname", "")),
            "source_capability": "delete_modules_in_section",
            "section": section.get("name") or section.get("sectionnum"),
        }
        item["approval_scope"] = {
            "target_count": len(modules),
            "noun": "exact Moodle modules",
            "scope": f"{_section_label(section)}" + (" (inferred from readback)" if inferred_scope else ""),
        }
        item.update(_section_public_fields(section))
        resolved.append(item)
    if not resolved:
        if explicit_empty_targets or _truthy(params.get("delete_empty_section_requested")):
            return []
        raise ValueError(f"delete_modules_in_section found no Moodle modules in {_section_label(section)}")
    return resolved


def _resolve_unique_module_target(
    params: dict[str, Any],
    readback_context: dict[str, Any],
    index: int,
    capability: str,
) -> dict[str, Any]:
    target_params = _flatten_target_params(params)
    explicit_cmid = str(_param(target_params, "target_module_id", "cmid", "id") or "").strip()
    explicit_name = str(_param(target_params, "target_object", "title", "name", "resource_name") or "").strip()
    explicit_modname = str(_param(target_params, "modname", "module_type") or "").strip()
    if explicit_cmid and explicit_name:
        return {"cmid": explicit_cmid, "name": explicit_name, "modname": explicit_modname}
    cmid = explicit_cmid
    ref = _param(target_params, "target_ref", "target", "name", "title", "target_object")
    modules = _modules_from_readback(readback_context)
    if cmid:
        for module in modules:
            if str(module.get("cmid", "")).strip() == cmid:
                return module
        name = str(_param(target_params, "target_object", "title", "name") or "").strip()
        if name:
            return {"cmid": cmid, "name": name, "modname": str(_param(target_params, "modname", "module_type") or "")}
        raise ValueError(f"operations[{index}].{capability} requires readback or exact title for cmid {cmid}")
    if ref in ("", None):
        raise ValueError(f"operations[{index}].{capability} requires target_module_id/cmid or target_ref")
    if not modules:
        raise ValueError(f"operations[{index}].{capability} cannot resolve target because Moodle readback is unavailable")
    matches = [module for module in modules if _module_matches_ref(module, str(ref))]
    unique: dict[str, dict[str, Any]] = {}
    for module in matches:
        module_cmid = str(module.get("cmid", "")).strip()
        if module_cmid:
            unique[module_cmid] = module
    if not unique:
        raise ValueError(f"operations[{index}].{capability} could not find target {ref!r} in Moodle readback")
    if len(unique) > 1:
        labels = ", ".join(str(module.get("section_name") or module.get("sectionnum") or module.get("cmid")) for module in unique.values())
        raise ValueError(
            f"operations[{index}].{capability} matched multiple Moodle modules ({labels}); "
            "staff must approve an exact cmid or narrower target"
        )
    return next(iter(unique.values()))


def _module_targets_from_params(params: dict[str, Any], section: dict[str, Any]) -> list[dict[str, Any]]:
    raw_targets = params.get("resolved_targets") or params.get("targets")
    if not isinstance(raw_targets, list):
        return []
    targets: list[dict[str, Any]] = []
    for target in raw_targets:
        if not isinstance(target, dict):
            continue
        item = dict(target)
        if "cmid" not in item and "id" in item:
            item["cmid"] = item.get("id")
        if "cmid" not in item and "target_module_id" in item:
            item["cmid"] = item.get("target_module_id")
        if "name" not in item and "title" in item:
            item["name"] = item.get("title")
        if "name" not in item and "target_object" in item:
            item["name"] = item.get("target_object")
        item.setdefault("section_name", section.get("name", ""))
        item.setdefault("sectionnum", section.get("sectionnum", ""))
        targets.append(item)
    return targets


def _filter_delete_modules(params: dict[str, Any], modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    target_filter = _delete_target_filter(params)
    if not isinstance(target_filter, dict) or not target_filter:
        return modules
    wanted_cmid = str(target_filter.get("cmid") or target_filter.get("target_module_id") or "").strip()
    wanted_modname = str(target_filter.get("modname") or "").strip()
    wanted_name = str(target_filter.get("name") or target_filter.get("title") or "").strip()
    filtered: list[dict[str, Any]] = []
    for module in modules:
        if wanted_cmid and str(module.get("cmid", "")).strip() != wanted_cmid:
            continue
        if wanted_modname and str(module.get("modname", "")).strip() != wanted_modname:
            continue
        if wanted_name and _normalize_text(str(module.get("name", ""))) != _normalize_text(wanted_name):
            continue
        filtered.append(module)
    return filtered


def _delete_target_filter(params: dict[str, Any]) -> dict[str, Any]:
    target_filter = params.get("target_filter")
    result = dict(target_filter) if isinstance(target_filter, dict) else {}
    cmid = _param(params, "target_module_id", "cmid", "id")
    if cmid not in ("", None):
        result["cmid"] = cmid
    ref = _param(params, "target_ref", "target", "name", "title", "target_object")
    if ref not in ("", None):
        if isinstance(ref, dict):
            pass
        elif str(ref).strip().isdigit():
            result.setdefault("cmid", ref)
        else:
            result.setdefault("name", ref)
    modname = _param(params, "modname", "module_type")
    if modname not in ("", None):
        result["modname"] = modname
    return {key: value for key, value in result.items() if value not in ("", None)}


def _infer_delete_scope_from_readback(
    params: dict[str, Any],
    readback_context: dict[str, Any],
    index: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sections = _sections_from_readback(readback_context)
    if not sections:
        raise ValueError(f"operations[{index}].delete_modules_in_section cannot infer section because Moodle readback is unavailable")
    explicit_targets = _module_targets_from_params(params, {})
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for section in sections:
        modules = _section_modules(section)
        if explicit_targets:
            matched = _match_explicit_delete_targets(explicit_targets, modules)
        else:
            matched = _filter_delete_modules(params, modules)
        for module in matched:
            if str(module.get("cmid", "")).strip():
                candidates.append((section, module))
    if not candidates:
        raise ValueError(f"operations[{index}].delete_modules_in_section could not infer a matching Moodle module from readback")
    unique_sections: dict[str, dict[str, Any]] = {}
    modules_by_key: dict[str, list[dict[str, Any]]] = {}
    for section, module in candidates:
        key = _section_unique_key(section)
        unique_sections[key] = section
        modules_by_key.setdefault(key, []).append(module)
    if len(unique_sections) != 1:
        labels = ", ".join(_section_label(section) for section in unique_sections.values())
        raise ValueError(
            f"operations[{index}].delete_modules_in_section matched modules in multiple sections ({labels}); "
            "staff must approve a bounded section or exact targets"
        )
    key = next(iter(unique_sections))
    return unique_sections[key], modules_by_key[key]


def _match_explicit_delete_targets(targets: list[dict[str, Any]], modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    seen: set[str] = set()
    for target in targets:
        wanted_cmid = str(target.get("cmid") or target.get("target_module_id") or "").strip()
        wanted_name = str(target.get("name") or target.get("target_object") or target.get("title") or "").strip()
        wanted_modname = str(target.get("modname") or target.get("module_type") or "").strip()
        for module in modules:
            if wanted_cmid and str(module.get("cmid", "")).strip() != wanted_cmid:
                continue
            if wanted_name and _normalize_text(module.get("name", "")) != _normalize_text(wanted_name):
                continue
            if wanted_modname and str(module.get("modname", "")).strip() != wanted_modname:
                continue
            key = str(module.get("cmid", "")).strip()
            if key and key not in seen:
                matched.append(module)
                seen.add(key)
    return matched


def _delete_module_operation(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
    target: dict[str, Any],
) -> dict[str, Any]:
    resolved = _base_operation(operation, definition, params)
    cmid = str(target.get("cmid", "")).strip()
    title = str(target.get("name", "")).strip()
    modname = str(target.get("modname", "")).strip()
    url = str(target.get("url", "")).strip()
    env = dict(resolved.get("env_overrides") or {})
    env.update(
        {
            "MOODLE_OPERATION_KIND": "browser_module_delete",
            "MOODLE_TARGET_MODULE_ID": cmid,
            "MOODLE_TARGET_TITLE": title,
            "MOODLE_TARGET_MODNAME": modname,
            "MOODLE_TARGET_RESOURCE_URL": url,
        }
    )
    resolved.update(
        {
            "operation_kind": "browser_module_delete",
            "execution_mode": "agent_browser_action",
            "action": "delete_module",
            "target_module_id": cmid,
            "target_object": title,
            "target_resource_url": url,
            "modname": modname,
            "approval_item": title,
            "env_overrides": env,
        }
    )
    return resolved


def _delete_file_resource_operation(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
    target: dict[str, Any],
) -> dict[str, Any]:
    resolved = _base_operation(operation, definition, params)
    cmid = str(target.get("cmid", "")).strip()
    title = str(target.get("name", "")).strip()
    url = str(target.get("url", "")).strip() or f"https://migrationtest-moodle.hku.hk/mod/resource/view.php?id={cmid}&forceview=1"
    env = dict(resolved.get("env_overrides") or {})
    env.update(
        {
            "MOODLE_OPERATION_KIND": "browser_course_file_resource_delete",
            "MOODLE_TARGET_MODULE_ID": cmid,
            "MOODLE_RESOURCE_NAME": title,
            "MOODLE_RESOURCE_URL": url,
        }
    )
    resolved.update(
        {
            "operation_kind": "browser_course_file_resource_delete",
            "execution_mode": "agent_browser_action",
            "action": "delete_file_resource",
            "target_module_id": cmid,
            "target_object": title,
            "resource_name": title,
            "target_resource_url": url,
            "approval_item": title,
            "env_overrides": env,
        }
    )
    return resolved


def _resolve_rename_section(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
    readback_context: dict[str, Any],
) -> dict[str, Any]:
    section = _resolve_section(params, readback_context)
    if not section:
        section = {"name": str(params.get("section", "section")).strip(), "sectionnum": params.get("sectionnum", "")}
    new_title = str(params.get("new_title", ""))
    resolved = _base_operation(operation, definition, params)
    env = dict(resolved.get("env_overrides") or {})
    env.update(
        {
            "MOODLE_OPERATION_KIND": "browser_section_rename",
            "MOODLE_SECTION_NAME": str(section.get("name") or ""),
            "MOODLE_SECTION_NUM": (
                "" if section.get("sectionnum") is None else str(section.get("sectionnum"))
            ),
            "MOODLE_TARGET_TITLE": new_title,
        }
    )
    if section.get("section_id"):
        env["MOODLE_SECTION_ID"] = str(section["section_id"])
    resolved.update(
        {
            "operation_kind": "browser_section_rename",
            "execution_mode": "agent_browser_action",
            "action": "rename_section",
            "approval_item": _section_label(section),
            "target_object": _section_label(section),
            "new_title": new_title,
            "target_title": new_title,
            "env_overrides": env,
            **_section_public_fields(section),
        }
    )
    return resolved


def _resolve_course_refresh_batch_upload(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
) -> dict[str, Any]:
    resolved = _base_operation(operation, definition, params)
    refresh_job = params.get("refresh_job")
    if isinstance(refresh_job, dict):
        resolved["refresh_job"] = refresh_job
    if params.get("prepared_upload_manifest_path"):
        resolved["prepared_upload_manifest_path"] = str(params["prepared_upload_manifest_path"])
    if params.get("timeline_path"):
        job = resolved.get("refresh_job") if isinstance(resolved.get("refresh_job"), dict) else {}
        job["timeline_path"] = str(params["timeline_path"])
        resolved["refresh_job"] = job
    if params.get("source_originals_path"):
        job = resolved.get("refresh_job") if isinstance(resolved.get("refresh_job"), dict) else {}
        job["source_originals_path"] = str(params["source_originals_path"])
        resolved["refresh_job"] = job
    resolved.update(
        {
            "operation_kind": COURSE_REFRESH_BATCH_UPLOAD_KIND,
            "execution_mode": "agent_browser_action",
            "action": "course_refresh_batch_upload",
            "approval_item": str(
                operation.get("approval_item")
                or params.get("approval_item")
                or params.get("batch_name")
                or "Course file batch"
            ),
            "refresh_capability_plan": {
                "local_preapproval_steps": list(definition.local_preapproval_steps),
                "post_approval_executor": definition.post_approval_executor,
                "verifier": definition.verifier,
                "safety_rules": list(definition.safety_rules),
            },
        }
    )
    resolved["env_overrides"]["MOODLE_OPERATION_KIND"] = COURSE_REFRESH_BATCH_UPLOAD_KIND
    return resolved


def _resolve_refresh_file_preview(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
) -> dict[str, Any]:
    resolved = _base_operation(operation, definition, params)
    file_path = str(_param(params, "file_path", "source_file_path", "path")).strip()
    resource_name = str(_param(params, "resource_name", "name", "title") or Path(file_path).stem).strip()
    reference_pack = str(_param(params, "reference_pack_path", "reference_pack")).strip()
    instructions = str(_param(params, "instructions_path", "refresh_rules_path")).strip()
    env = dict(resolved.get("env_overrides") or {})
    env.update(
        {
            "MOODLE_OPERATION_KIND": REFRESH_FILE_PREVIEW_KIND,
            "COURSE_REFRESH_PREVIEW_FILE_PATH": file_path,
            "COURSE_REFRESH_PREVIEW_RESOURCE_NAME": resource_name,
        }
    )
    if reference_pack:
        env["COURSE_REFERENCE_PACK_PATH"] = reference_pack
    if instructions:
        env["COURSE_REFRESH_INSTRUCTIONS_PATH"] = instructions
    resolved.update(
        {
            "operation_kind": definition.executor_lane,
            "execution_mode": definition.execution_mode,
            "action": "refresh_file_preview",
            "file_path": file_path,
            "source_file_path": file_path,
            "resource_name": resource_name,
            "target_object": resource_name,
            "approval_item": resource_name,
            "reference_pack_path": reference_pack,
            "instructions_path": instructions,
            "env_overrides": env,
            "readback_required": False,
        }
    )
    return resolved


def _resolve_refresh_file_batch_preview(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
) -> dict[str, Any]:
    resolved = _base_operation(operation, definition, params)
    raw_paths = params.get("file_paths") or params.get("files") or []
    file_paths = [
        str(
            item.get("local_path") or item.get("file_path") or item.get("path") or ""
            if isinstance(item, dict)
            else item
        ).strip()
        for item in raw_paths
    ]
    file_paths = [path for path in file_paths if path]
    env = dict(resolved.get("env_overrides") or {})
    env["MOODLE_OPERATION_KIND"] = REFRESH_FILE_BATCH_PREVIEW_KIND
    resolved.update(
        {
            "operation_kind": definition.executor_lane,
            "execution_mode": definition.execution_mode,
            "action": "refresh_file_batch_preview",
            "file_paths": file_paths,
            "batch_id": str(params.get("batch_id") or ""),
            "batch_version": params.get("batch_version") or "",
            "target_object": f"{len(file_paths)} file refresh source(s)",
            "approval_item": "File refresh batch",
            "env_overrides": env,
            "readback_required": False,
        }
    )
    return resolved


def _resolve_finalize_file_batch_preview(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
) -> dict[str, Any]:
    resolved = _base_operation(operation, definition, params)
    batch_id = str(params.get("batch_id") or "").strip()
    try:
        batch_version: int | str = int(params.get("batch_version") or 0)
    except (TypeError, ValueError):
        batch_version = str(params.get("batch_version") or "")
    env = dict(resolved.get("env_overrides") or {})
    env["MOODLE_OPERATION_KIND"] = FINALIZE_FILE_BATCH_PREVIEW_KIND
    resolved.update(
        {
            "operation_kind": definition.executor_lane,
            "execution_mode": definition.execution_mode,
            "action": "finalize_file_batch_preview",
            "batch_id": batch_id,
            "batch_version": batch_version,
            "include_item_ids": list(params.get("include_item_ids") or []),
            "final_pdf_name": str(params.get("final_pdf_name") or ""),
            "target_object": f"File refresh batch {batch_id}",
            "approval_item": "Combined course documents PDF",
            "env_overrides": env,
            "readback_required": False,
        }
    )
    return resolved


def _resolve_rebuild_reference_pack(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
) -> dict[str, Any]:
    resolved = _base_operation(operation, definition, params)
    reference_files = params.get("reference_files") or params.get("reference_file_paths") or []
    class_weekdays = params.get("class_weekdays") or params.get("teaching_weekdays") or []
    reference_pack = str(rebuild_reference_pack_path(params, {}))
    env = dict(resolved.get("env_overrides") or {})
    env["MOODLE_OPERATION_KIND"] = REBUILD_REFERENCE_PACK_KIND
    env["COURSE_REFERENCE_PACK_PATH"] = reference_pack
    resolved.update(
        {
            "operation_kind": definition.executor_lane,
            "execution_mode": definition.execution_mode,
            "action": "rebuild_reference_pack",
            "reference_files": reference_files,
            "class_weekdays": class_weekdays,
            "reference_pack_path": reference_pack,
            "target_object": str(params.get("course_code") or "reference pack"),
            "approval_item": str(params.get("course_code") or "reference pack"),
            "env_overrides": env,
            "readback_required": False,
        }
    )
    return resolved


def _resolve_update_semester_schedule(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
) -> dict[str, Any]:
    resolved = _base_operation(operation, definition, params)
    schedule_files = params.get("schedule_files") or params.get("schedule_file_paths") or []
    schedule_text = str(_param(params, "schedule_text", "text") or "").strip()
    reference_pack = str(semester_schedule_reference_pack_path(params, {}))
    env = dict(resolved.get("env_overrides") or {})
    env["MOODLE_OPERATION_KIND"] = UPDATE_SEMESTER_SCHEDULE_KIND
    env["COURSE_REFERENCE_PACK_PATH"] = reference_pack
    resolved.update(
        {
            "operation_kind": definition.executor_lane,
            "execution_mode": definition.execution_mode,
            "action": "update_semester_schedule",
            "schedule_files": schedule_files,
            "schedule_text": schedule_text,
            "reference_pack_path": reference_pack,
            "target_object": str(params.get("course_code") or "semester schedule"),
            "approval_item": str(params.get("course_code") or "semester schedule"),
            "merge_mode": str(params.get("merge_mode") or "replace_all"),
            "env_overrides": env,
            "readback_required": False,
        }
    )
    return resolved


def _resolve_timeline_draft_operation(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
) -> dict[str, Any]:
    resolved = _base_operation(operation, definition, params)
    draft_id = str(params.get("draft_id") or "").strip()
    draft_version = int(params.get("draft_version"))
    env = dict(resolved.get("env_overrides") or {})
    env["MOODLE_OPERATION_KIND"] = definition.executor_lane
    resolved.update(
        {
            "operation_kind": definition.executor_lane,
            "execution_mode": definition.execution_mode,
            "action": definition.name,
            "draft_id": draft_id,
            "draft_version": draft_version,
            "target_object": f"timeline draft {draft_id} version {draft_version}",
            "approval_item": f"timeline draft version {draft_version}",
            "env_overrides": env,
            "readback_required": False,
        }
    )
    return resolved


def _refresh_operation_from_params(operation: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    candidate = dict(operation)
    candidate.setdefault("operation_kind", COURSE_REFRESH_BATCH_UPLOAD_KIND)
    if "refresh_job" not in candidate and isinstance(params.get("refresh_job"), dict):
        candidate["refresh_job"] = params["refresh_job"]
    if "prepared_upload_manifest_path" not in candidate and params.get("prepared_upload_manifest_path"):
        candidate["prepared_upload_manifest_path"] = str(params["prepared_upload_manifest_path"])
    return candidate


def _base_operation(
    operation: dict[str, Any],
    definition: CapabilityDefinition,
    params: dict[str, Any],
) -> dict[str, Any]:
    resolved = dict(operation)
    resolved["params"] = dict(params)
    resolved.setdefault("capability", definition.name)
    resolved.setdefault("operation_kind", definition.executor_lane)
    resolved.setdefault("execution_mode", definition.execution_mode)
    resolved.setdefault("risk_level", definition.risk_level)
    resolved.setdefault("target_course_id", SANDBOX_COURSE_ID)
    resolved.setdefault("target_course_url", SANDBOX_COURSE_URL)
    resolved.setdefault("summary", definition.approval_summary)
    resolved.setdefault("approval_action", definition.approval_action)
    resolved.setdefault("approval_item", _approval_item_from_params(params) or definition.approval_summary)
    resolved.setdefault("verifier", definition.verifier)
    resolved.setdefault("capability_definition", definition.name)
    resolved["env_overrides"] = dict(resolved.get("env_overrides") or {})
    return resolved


def _resolve_module_target(params: dict[str, Any], readback_context: dict[str, Any]) -> dict[str, Any]:
    target_params = _flatten_target_params(params)
    target = _resource_target_from_params(params)
    if target:
        return target
    cmid = str(_param(target_params, "target_module_id", "cmid", "resource_cmid") or "").strip()
    if cmid:
        for module in _modules_from_readback(readback_context):
            if str(module.get("cmid", "")).strip() == cmid:
                return module
        return {"cmid": cmid, "name": str(_param(target_params, "target_object", "title", "name")), "modname": ""}
    ref = _param(target_params, "target_ref", "target", "name", "title", "resource_name")
    if ref in ("", None):
        return {}
    ref_text = str(ref)
    for module in _modules_from_readback(readback_context):
        if _module_matches_ref(module, ref_text):
            return module
    if str(ref).isdigit():
        return {"cmid": str(ref), "name": str(_param(target_params, "resource_name", "target_object", "title", "name")), "modname": ""}
    return {"cmid": str(_param(target_params, "target_module_id", "cmid")), "name": ref_text, "modname": ""}


def _resource_target_from_params(params: dict[str, Any]) -> dict[str, Any]:
    target_params = _flatten_target_params(params)
    cmid = str(_param(target_params, "target_module_id", "cmid", "resource_cmid") or "").strip()
    name = str(_param(target_params, "resource_name", "target_object", "title", "name") or "").strip()
    url = str(_param(target_params, "resource_url", "target_resource_url", "display_url") or "").strip()
    modname = str(_param(target_params, "modname", "module_type") or "resource").strip()
    if cmid and name:
        return {"cmid": cmid, "name": name, "url": url, "modname": modname}
    return {}


def _flatten_target_params(params: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for key in ("target", "forum_target"):
        target = params.get(key)
        if isinstance(target, dict):
            merged.update(target)
    for key, value in params.items():
        if key not in {"target", "forum_target"}:
            merged[key] = value
    return merged or params


def _resolve_forum_module(params: dict[str, Any], readback_context: dict[str, Any]) -> dict[str, Any]:
    target_params = _flatten_target_params(params)
    cmid = str(
        _param(target_params, "target_forum_cmid", "forum_cmid", "forum_module_id", "target_module_id", "cmid")
        or ""
    ).strip()
    url = str(_param(target_params, "target_forum_url", "forum_url", "forum_link", "module_url") or "").strip()
    name = str(_param(target_params, "forum_name", "forum", "target_forum", "target_forum_name") or "").strip()
    section_ref = _param(target_params, "target_section", "section", "section_name", "sectionnum", "section_number")

    if section_ref not in ("", None):
        sections = [section for section in _sections_from_readback(readback_context) if _section_matches_ref(section, section_ref)]
        modules = [module for section in sections for module in _section_modules(section)]
    else:
        modules = _modules_from_readback(readback_context)
    forum_modules = [_with_forum_defaults(module) for module in modules if _looks_like_forum_module(module)]
    if not forum_modules:
        return {}

    if cmid:
        for module in forum_modules:
            if str(module.get("cmid", "")).strip() == cmid:
                return module
    if url:
        wanted_url = _normalize_text(url)
        for module in forum_modules:
            if _normalize_text(module.get("url", "")) == wanted_url:
                return module
            if str(module.get("cmid", "")).strip() and f"id={module.get('cmid')}" in url:
                return module
    if name:
        wanted_name = _normalize_text(name)
        exact = [module for module in forum_modules if _module_matches_ref(module, name)]
        if len(exact) == 1:
            return exact[0]
        generic_names = {"forum", "forums", "course forum", "general forum", "讨论区", "论坛"}
        if wanted_name in generic_names and len(forum_modules) == 1:
            return forum_modules[0]
    if not name and len(forum_modules) == 1:
        return forum_modules[0]
    return {}


def _looks_like_forum_module(module: dict[str, Any]) -> bool:
    modname = _normalize_text(module.get("modname") or module.get("module_type") or module.get("mod") or "")
    if modname == "forum":
        return True
    return "/mod/forum/" in str(module.get("url", ""))


def _with_forum_defaults(module: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(module)
    if "cmid" not in normalized and "id" in normalized:
        normalized["cmid"] = normalized.get("id")
    if not normalized.get("url") and str(normalized.get("cmid", "")).strip().isdigit():
        normalized["url"] = f"https://migrationtest-moodle.hku.hk/mod/forum/view.php?id={normalized['cmid']}"
    return normalized


def _resolve_section(params: dict[str, Any], readback_context: dict[str, Any]) -> dict[str, Any]:
    section_ref = _param(params, "target_section", "section", "section_name", "sectionnum", "section_number")
    if section_ref in ("", None):
        return {}
    if isinstance(section_ref, dict):
        normalized_ref = _normalize_section_dict(section_ref)
        for section in _sections_from_readback(readback_context):
            if _section_matches_ref(section, normalized_ref):
                return section
        return normalized_ref
    for section in _sections_from_readback(readback_context):
        if _section_matches_ref(section, section_ref):
            return section
    if str(section_ref).strip().isdigit():
        return {"sectionnum": str(section_ref).strip(), "name": f"Section {section_ref}", "modules": []}
    return {"name": str(section_ref).strip(), "modules": []}


def _normalize_section_dict(section: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(section)
    if "sectionnum" not in normalized and "section" in normalized:
        normalized["sectionnum"] = normalized.get("section")
    if "section_id" not in normalized:
        for key in ("id", "sectionid"):
            if normalized.get(key) not in ("", None):
                normalized["section_id"] = normalized.get(key)
                break
    if "name" not in normalized:
        for key in ("current_title", "title", "section_name"):
            if normalized.get(key) not in ("", None):
                normalized["name"] = normalized.get(key)
                break
    normalized.setdefault("modules", [])
    return normalized


def _sections_from_readback(readback_context: dict[str, Any]) -> list[dict[str, Any]]:
    sections = readback_context.get("sections") if isinstance(readback_context, dict) else None
    if not isinstance(sections, list):
        return []
    normalized: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        item = dict(section)
        if "sectionnum" not in item and "section" in item:
            item["sectionnum"] = item.get("section")
        normalized.append(item)
    return normalized


def _section_modules(section: dict[str, Any]) -> list[dict[str, Any]]:
    modules = section.get("modules")
    if not isinstance(modules, list):
        return []
    normalized: list[dict[str, Any]] = []
    for module in modules:
        if not isinstance(module, dict):
            continue
        item = dict(module)
        if "cmid" not in item and "id" in item:
            item["cmid"] = item.get("id")
        normalized.append(item)
    return normalized


def _modules_from_readback(readback_context: dict[str, Any]) -> list[dict[str, Any]]:
    modules: list[dict[str, Any]] = []
    for section in _sections_from_readback(readback_context):
        for module in _section_modules(section):
            module.setdefault("section_name", section.get("name", ""))
            module.setdefault("sectionnum", section.get("sectionnum", ""))
            modules.append(module)
    return modules


def _section_matches_ref(section: dict[str, Any], ref: Any) -> bool:
    if isinstance(ref, dict):
        wanted_id = str(ref.get("section_id") or ref.get("id") or ref.get("sectionid") or "").strip()
        wanted_num = str(ref.get("sectionnum") if ref.get("sectionnum") is not None else ref.get("section", "")).strip()
        wanted_name = _normalize_text(ref.get("name") or ref.get("current_title") or ref.get("title") or ref.get("section_name") or "")
        if wanted_id:
            live_id = str(section.get("section_id") or section.get("id") or section.get("sectionid") or "").strip()
            if live_id == wanted_id:
                return True
        if wanted_num and str(section.get("sectionnum", "")).strip() == wanted_num:
            return True
        if wanted_name:
            return any(_normalize_text(section.get(key, "")) == wanted_name for key in ("name", "title", "section_name"))
        return False
    text = str(ref).strip()
    if not text:
        return False
    if text.isdigit() and str(section.get("sectionnum", "")).strip() == text:
        return True
    wanted = _normalize_text(text)
    names = [
        section.get("name", ""),
        section.get("title", ""),
        f"section {section.get('sectionnum', '')}",
        f"week {section.get('sectionnum', '')}",
        f"week{section.get('sectionnum', '')}",
    ]
    if str(section.get("sectionnum", "")).strip() == "0":
        names.append("general")
    return any(_normalize_text(name) == wanted for name in names if str(name).strip())


def _module_matches_ref(module: dict[str, Any], ref: str) -> bool:
    wanted = _normalize_text(ref)
    if str(module.get("cmid", "")).strip() == str(ref).strip():
        return True
    return any(
        _normalize_text(module.get(key, "")) == wanted
        for key in ("name", "title", "target_object", "resource_name")
    )


def _section_env(section: dict[str, Any]) -> dict[str, str]:
    env: dict[str, str] = {}
    if section.get("name"):
        env["MOODLE_SECTION_NAME"] = str(section["name"])
    if section.get("sectionnum") not in ("", None):
        env["MOODLE_SECTION_NUM"] = str(section["sectionnum"])
    if section.get("section_id"):
        env["MOODLE_SECTION_ID"] = str(section["section_id"])
    return env


def _section_public_fields(section: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if section.get("name"):
        fields["section_name"] = str(section["name"])
    if section.get("sectionnum") not in ("", None):
        fields["sectionnum"] = str(section["sectionnum"])
    if section.get("section_id"):
        fields["section_id"] = section["section_id"]
    return fields


def _section_label(section: dict[str, Any]) -> str:
    name = str(section.get("name") or "").strip()
    if name:
        return name
    number = (
        ""
        if section.get("sectionnum") is None
        else str(section.get("sectionnum")).strip()
    )
    return f"Section {number}" if number else "Course section"


def _section_unique_key(section: dict[str, Any]) -> str:
    section_id = str(section.get("section_id") or section.get("id") or section.get("sectionid") or "").strip()
    if section_id:
        return f"id:{section_id}"
    sectionnum = str(section.get("sectionnum") if section.get("sectionnum") is not None else section.get("section", "")).strip()
    if sectionnum:
        return f"num:{sectionnum}"
    return f"name:{_normalize_text(_section_label(section))}"


def _approval_item_from_params(params: dict[str, Any]) -> str:
    return str(
        _param(
            params,
            "approval_item",
            "resource_name",
            "name",
            "title",
            "subject",
            "section",
            "target_ref",
        )
        or ""
    ).strip()


def _param(params: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in params and params[key] not in (None, ""):
            return params[key]
    return ""


def _list_param(params: dict[str, Any], *keys: str) -> list[str]:
    value = _param(params, *keys)
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item or "").strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            parsed = None
        if isinstance(parsed, (list, tuple)):
            return [str(item).strip() for item in parsed if str(item or "").strip()]
        return [part.strip() for part in text.split(",") if part.strip()]
    return []


def _dedupe_group_names(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _choicegroup_maxanswers(
    params: dict[str, Any],
    group_names: list[str],
    *,
    index: int,
) -> int | None:
    """Normalize Moodle Choicegroup's one shared per-Group quota setting."""
    raw = _param(params, "maxanswers", "max_answers")
    if raw in ("", None):
        return None
    if isinstance(raw, bool):
        raise ValueError(
            f"operations[{index}].create_group_signup_activity maxanswers must be an integer"
        )
    if isinstance(raw, dict):
        quotas = {str(name).strip(): value for name, value in raw.items() if str(name).strip()}
        expected = set(group_names)
        actual = set(quotas)
        if actual != expected:
            missing = sorted(expected - actual, key=str.casefold)
            extra = sorted(actual - expected, key=str.casefold)
            raise ValueError(
                f"operations[{index}].create_group_signup_activity maxanswers mapping must "
                f"name exactly the selected Groups; missing={missing}, extra={extra}"
            )
        normalized_values: list[int] = []
        for group_name in group_names:
            value = quotas[group_name]
            if isinstance(value, bool):
                raise ValueError(
                    f"operations[{index}].create_group_signup_activity quota for "
                    f"{group_name!r} must be an integer"
                )
            try:
                normalized_values.append(int(value))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"operations[{index}].create_group_signup_activity quota for "
                    f"{group_name!r} must be an integer"
                ) from exc
        if len(set(normalized_values)) != 1:
            raise ValueError(
                f"operations[{index}].create_group_signup_activity cannot apply different "
                "per-Group quotas because Moodle Choicegroup exposes one shared maxanswers setting"
            )
        result = normalized_values[0]
    else:
        try:
            result = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"operations[{index}].create_group_signup_activity maxanswers must be an integer"
            ) from exc
    if result < 0:
        raise ValueError(
            f"operations[{index}].create_group_signup_activity maxanswers cannot be negative"
        )
    return result


def _restriction_time_param(params: dict[str, Any]) -> Any:
    value = _param(params, "restriction_time", "open_at", "available_from", "release_at")
    if value not in ("", None):
        return value
    for key in (
        "restriction_action",
        "access_time_action",
        "restriction_time_action",
        "target_action",
        "action",
        "mode",
        "operation",
    ):
        if key in params and _is_clear_restriction_request(params[key]):
            return "clear"
    if _is_clear_restriction_request(params):
        return "clear"
    return ""


def _declared_runtime_target_refs(operation: dict[str, Any]) -> set[str]:
    capability = str(operation.get("capability") or "").strip()
    kind = str(operation.get("operation_kind") or "").strip()
    if capability not in {
        "create_assignment_activity",
        "create_forum_activity",
        "create_group_signup_activity",
        "upload_file_resource",
    } and kind not in {
        "browser_assignment_activity",
        "browser_forum_activity",
        "browser_group_signup_activity",
        "browser_course_file_resource_upload",
    }:
        return set()
    params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
    refs: set[str] = set()
    for key in ("post_create_reference", "result_reference", "created_reference", "reference", "ref"):
        value = str(operation.get(key) or params.get(key) or "").strip()
        if value:
            refs.add(value)
    return refs


def _deferred_runtime_target_ref(params: dict[str, Any], known_runtime_refs: set[str] | None = None) -> str:
    ref = str(_param(params, "target_ref", "ref", "target") or "").strip()
    if not ref:
        return ""
    if ref in (known_runtime_refs or set()):
        return ref
    return ""


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "visible", "show"}


def _is_clear_restriction_request(value: Any) -> bool:
    if isinstance(value, dict):
        action = str(
            value.get("action")
            or value.get("mode")
            or value.get("operation")
            or value.get("value")
            or ""
        ).strip().lower()
        return action in {"clear", "remove", "delete", "unset", "none", "清除", "删除", "移除", "去掉"} or bool(
            value.get("clear")
        )
    text = str(value).strip()
    lowered = text.lower()
    if lowered.startswith("{") and lowered.endswith("}"):
        try:
            return _is_clear_restriction_request(ast.literal_eval(text))
        except (SyntaxError, ValueError):
            pass
    return lowered in {"clear", "remove", "delete", "unset", "none", "null", "清除", "删除", "移除", "去掉"}


def _normalize_text(value: Any) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "").strip().lower())
