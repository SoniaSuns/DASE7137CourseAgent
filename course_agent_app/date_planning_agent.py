from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT = PROJECT_ROOT / "course_agent_app"
AGENT_NAME = "date-planning-agent"
DEFAULT_CODEX_BIN = "/Applications/ChatGPT.app/Contents/Resources/codex"
DEFAULT_CODEX_MODEL = "gpt-5.5"


def _bounded_json(value: Any, max_chars: int) -> str:
    serialized = json.dumps(value, ensure_ascii=False, indent=2)
    if len(serialized) <= max_chars:
        return serialized
    if isinstance(value, list):
        kept: list[Any] = []
        for item in value:
            candidate = {"items": [*kept, item], "truncated_count": len(value) - len(kept) - 1}
            if len(json.dumps(candidate, ensure_ascii=False, indent=2)) > max_chars:
                break
            kept.append(item)
        return json.dumps(
            {"items": kept, "truncated_count": len(value) - len(kept)},
            ensure_ascii=False,
            indent=2,
        )
    if isinstance(value, dict):
        kept: dict[str, Any] = {}
        omitted: list[str] = []
        for key, item in value.items():
            candidate = {**kept, str(key): item}
            if len(json.dumps(candidate, ensure_ascii=False, indent=2)) <= max_chars:
                kept[str(key)] = item
            else:
                omitted.append(str(key))
        if omitted:
            kept["_omitted_keys"] = omitted
        return json.dumps(kept, ensure_ascii=False, indent=2)
    return json.dumps({"truncated": True}, ensure_ascii=False, indent=2)


def _first_value(env: dict[str, str], keys: list[str], default: str = "") -> str:
    for key in keys:
        value = env.get(key, "").strip()
        if value:
            return value
    return default


def codex_bin(env: dict[str, str]) -> str:
    return _first_value(env, ["CODEX_BIN"], shutil.which("codex") or DEFAULT_CODEX_BIN)


def codex_model(env: dict[str, str], stage: str) -> str:
    return _first_value(env, [f"CODEX_{stage.upper()}_MODEL", "CODEX_MODEL", "CODEX_FAST_MODEL"], DEFAULT_CODEX_MODEL)


def codex_no_run(env: dict[str, str], stage: str) -> bool:
    raw = _first_value(env, [f"CODEX_{stage.upper()}_NO_RUN", "CODEX_AGENT_NO_RUN"], "0")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def build_codex_exec_command(
    env: dict[str, str],
    prompt: str,
    *,
    stage: str,
    cwd: Path,
    output_last_message: Path,
    prompt_via_stdin: bool = True,
) -> list[str]:
    command = [
        codex_bin(env),
        "exec",
        "--cd",
        str(cwd),
        "--model",
        codex_model(env, stage),
    ]
    if not any((parent / ".git").exists() for parent in (cwd.resolve(), *cwd.resolve().parents)):
        command.append("--skip-git-repo-check")
    bypass = env.get("CODEX_DANGEROUSLY_BYPASS_APPROVALS_AND_SANDBOX", "").strip().lower()
    if bypass in {"1", "true", "yes", "on"}:
        command.append("--dangerously-bypass-approvals-and-sandbox")
    else:
        command.extend(["--sandbox", env.get("CODEX_SANDBOX", "workspace-write")])
    profile = env.get("CODEX_PROFILE", "").strip()
    if profile:
        command.extend(["--profile", profile])
    command.extend(["--output-last-message", str(output_last_message)])
    command.append("-" if prompt_via_stdin else prompt)
    return command


def build_date_planning_prompt(task: dict[str, Any]) -> str:
    output_path = str(task["agent_output_path"])
    source_excerpt = str(task.get("source_text_excerpt") or "")
    reference_excerpt = str(task.get("reference_text_excerpt") or "")
    reference_payloads = task.get("reference_payloads") if isinstance(task.get("reference_payloads"), dict) else {}
    timeline_payload = (
        reference_payloads.get("timeline.json")
        if isinstance(reference_payloads.get("timeline.json"), dict)
        else {}
    )
    content_week_mappings = (
        timeline_payload.get("content_week_mappings")
        if isinstance(timeline_payload.get("content_week_mappings"), list)
        else []
    )
    date_mentions = task.get("date_mentions") if isinstance(task.get("date_mentions"), list) else []
    return f"""You are the Date Planner Agent for a course-operations workflow.

Your job is semantic date planning only. Do not edit files. Do not upload to Moodle.

Inputs:
- Staff request: {task.get('raw_task', '')}
- Source file name: {task.get('source_file_name', '')}
- Source visible text excerpt:
```text
{source_excerpt}
```
- Candidate date mentions extracted by the runtime. Cover every candidate by placing it in date_edits, unchanged_dates, blockers, or a grounded schedule_table_rebuild that replaces the containing teaching-schedule rows:
```json
{_bounded_json(date_mentions, 30000)}
```
- Reference JSON payloads:
```json
{_bounded_json(reference_payloads, 30000)}
```
- Authoritative content-level teaching-week mappings:
```json
{_bounded_json(content_week_mappings, 24000)}
```
- Reference text excerpt:
```text
{reference_excerpt}
```
- Generic Office document model and Composition Planner ownership, when supplied:
```json
{_bounded_json({'document_model': task.get('document_model') or {}, 'composition_plan': task.get('composition_plan') or {}}, 500000)}
```

Rules:
- Decide which visible date strings in the source should be changed, kept, or blocked.
- Do not infer course semantics from a filename, fixed event name, fixed header, or historical example.
- When a Composition Planner output is supplied, every `date_edits` item must use an exact text-bearing structure assigned to `date`, and must echo its `target_id` and `target_fingerprint`. A `schedule_table_rebuilds` item is instead bounded by its exact visible headers and source data-row count; the runtime requires those to identify exactly one DOCX table before writing.
- Do not use raw old_text/replacement_text instructions from staff or fixed replacement pairs from refresh_rules.md.
- Ground every proposed date edit in the source context plus the reference timeline/calendar/syllabus facts.
- Keep two week coordinates distinct. Use `teaching_week` for lab/session/content and assignment/deadline mapping. For each rebuilt schedule table, inspect the exact header, source row numbering, and non-teaching rows, then explicitly choose `display_week_field: "calendar_week"` or `"teaching_week"` for its visible Week column. Do not assume every visible Week column has the same semantics.
- When an editable DOCX has a teaching schedule table whose old Holiday/Reading Week rows do not correspond one-for-one with the target calendar, use `schedule_table_rebuilds` instead of blocking or forcing date-only replacements. This is a row-level plan: the runtime will rebuild teaching rows from `timeline.json.week_calendar`, validate every teaching date against `semester_schedule.json`, and insert only target `timeline.json.non_teaching_calendar_gaps`.
- A `teaching_week_schedule_rebuild` must identify the table by its exact visible headers and state the source data-row count. For each target non-teaching gap, select the semantically corresponding old excluded row by `source_week`; do not map an unrelated old Holiday row merely to keep the row count.
- For every `non_teaching_rows[].target_date_iso`, copy the exact canonical course-session gap date from `timeline.json.non_teaching_calendar_gaps`. A suspension range start from `calendar.json` describes the whole period and is not interchangeable with that course's cancelled recurring session date. The runtime may normalize an unambiguous date within the same grounded suspension range, but the plan should emit the canonical timeline date directly.
- When `display_week_field` is `calendar_week`, show continuous calendar-week numbers on teaching and non-teaching rows. When it is `teaching_week`, show consecutive teaching-week numbers on teaching rows and leave non-teaching rows unnumbered. This display choice never changes the teaching-week coordinate used for mappings.
- Do not also emit date_edits for date strings inside a table covered by `schedule_table_rebuilds`; the row-level rebuild covers those candidates. Continue to emit date_edits or unchanged_dates for dates elsewhere in the document.
- When `semester_schedule.json` is available in Reference JSON payloads, treat it as the authority for weekly class meeting dates.
- When `source_calendar.json` is available, use it to resolve an old visible date to its source academic year, source semester, source calendar week, and weekday. The confirmed Timeline remains authoritative for whether an old row consumed a teaching week and for its source teaching-week coordinate. Respect `timeline.json.source_calendar_precedence=timeline_overrides_source_calendar` and its recorded overrides; do not discard an explicit Timeline class merely because the source calendar marks that date as suspended.
- Prefer explicit `source_date_iso`, `source_calendar_week`, `source_teaching_week`, and `source_weekday` fields from `timeline.json` and `content_week_mappings`. A generic legacy `source_week` field is not authoritative when these typed fields are present.
- When `calendar.json` has `non_teaching_days` or `teaching_suspensions` with `classes_suspended=true`, do not place lab/class/session-facing dates on those calendar-blocked dates. If `semester_schedule.json` conflicts with `calendar.json`, block instead of guessing.
- Lab visit, class visit, site visit, tutorial/session dates, and similar session-facing dates must map to one of the target week `teaching_dates` in `semester_schedule.json`.
- Lecture, class, tutorial, workshop, lab, visit, and other teaching/session-facing dates use `mapping_policy: "same_week_teaching_date"`.
- Assignment, submission, quiz, and deliverable deadlines should preserve the old file's teaching-week number, weekday, and time when the source context identifies a week-based deadline; map it to the target semester date with the same teaching week and weekday.
- Before applying same-week mapping, inspect `timeline.json.content_week_mappings`. These records are authoritative at the content-item level. Match by source teaching week, semantic content kind, content text/context, and stable item_id.
- If a matched content item has status `moved`, use its `target_teaching_week` even when it differs from `source_teaching_week`, and include both `content_kind` and `mapping_item_id` in the edit. For example, a moved Submission uses its new target Teaching Week while preserving the source weekday and time.
- If the matched content item has status `deleted` or has no target teaching week, block that date refresh. Do not fall back to same-week mapping and do not guess another content item.
- If more than one content mapping could match the source date/context and the exact item cannot be selected confidently, block. Unchanged content without an explicit remap continues to use the same actual teaching week.
- `source_week` and `target_week` in mapping plans always mean the actual `teaching_week` occurrence after holidays, reading weeks, and class suspensions have been skipped. They never mean a visible `calendar_week` label.
- Do not move assignment/submission/deadline dates merely because the mapped same-weekday date is a holiday or non-teaching day; preserve week, weekday, and time unless the reference pack or current staff prompt explicitly says that deadline should shift.
- Assignment/submission/deadline edits that preserve week + weekday use `mapping_policy: "same_weekday_deadline"`.
- If a candidate date mention includes `source_date_candidates`, treat those as explicit runtime-provided source-date context for visible dates that omit a year. Use them to fill `source_date_iso`, `source_weekday`, and, when present, `source_week`/`target_week`; do not ignore that context merely because the visible text itself has no year.
- If `source_date_candidates[].same_weekday_deadline_candidates` gives one grounded candidate for the chosen source date, use its `target_same_weekday_date` as the target for a `same_weekday_deadline` edit, preserving the visible deadline time.
- If a lab/session date cannot be mapped to a concrete teaching date because `semester_schedule.json` is missing, incomplete, or has multiple plausible dates with no source/context tie-breaker, set status to "blocked" instead of guessing.
- For any edit with `mapping_policy`, include machine-checkable `source_week`, `target_week`, `source_weekday`, `source_date_iso` when known, `target_date_iso`, `source_time` when known, and `replacement_time` when the replacement text includes a time.
- Identify as many real date edits as possible. Do not stop after the first obvious old year.
- Cover every candidate date mention. If a candidate is a citation, historical source note, or otherwise should stay unchanged, put it in unchanged_dates with a reason and confidence.
- Keep dates internally consistent, including weekday, day, month, year, deadline time, and table row context.
- If you cannot identify the correct target date confidently, set status to "blocked" and explain why.
- Output only JSON at the path below. Do not include Markdown.
- Values wrapped in angle brackets in the shape below are type placeholders only. Replace them with values grounded in the current source and reference payloads; never copy a placeholder or infer a value from this shape.

Required JSON shape:
{{
  "agent": "{AGENT_NAME}",
  "status": "ready|blocked|no_changes_needed",
  "summary": "short human-readable summary",
  "date_edits": [
    {{
      "id": "date-1",
      "target_id": "exact assigned structure ID when Composition Planner is supplied",
      "target_fingerprint": "exact structure fingerprint when Composition Planner is supplied",
      "visible_text": "exact visible date text from the source",
      "replacement_text": "new date text to show in the source",
      "date_kind": "session_date|deadline|academic_year|other",
      "content_kind": "lecture|workshop|submission|",
      "mapping_item_id": "stable item_id from timeline.json.content_week_mappings when one governs this edit",
      "mapping_policy": "same_week_teaching_date|same_weekday_deadline|",
      "source_week": "<grounded teaching-week integer>",
      "target_week": "<grounded teaching-week integer>",
      "source_weekday": "<grounded weekday>",
      "source_date_iso": "YYYY-MM-DD",
      "target_date_iso": "YYYY-MM-DD",
      "source_time": "HH:MM",
      "replacement_time": "HH:MM",
      "basis": "why this mapping is grounded",
      "confidence": "high|medium|low",
      "requires_human_review": true
    }}
  ],
  "schedule_table_rebuilds": [
    {{
      "id": "schedule-table-1",
      "table_headers": ["<exact visible header 1>", "<exact visible header 2>"],
      "source_data_row_count": "<integer counted from the current source table>",
      "mapping_policy": "teaching_week_schedule_rebuild",
      "display_week_field": "calendar_week|teaching_week",
      "non_teaching_rows": [
        {{
          "source_week": "<source week selected from timeline.json excluded_source_rows>",
          "target_date_iso": "YYYY-MM-DD",
          "label": "target non-teaching period label from timeline.json"
        }}
      ],
      "basis": "why the whole table can be rebuilt from the structured timeline, schedule, and calendar",
      "confidence": "high|medium|low",
      "requires_human_review": true
    }}
  ],
  "unchanged_dates": [
    {{
      "visible_text": "exact visible date text kept unchanged",
      "date_kind": "citation|historical_reference|current_semester_date|other",
      "reason": "why it should stay",
      "confidence": "high|medium|low",
      "requires_human_review": false
    }}
  ],
  "blockers": []
}}

Write the JSON to:
{output_path}
"""


def run_date_planning_agent(env: dict[str, str], task: dict[str, Any]) -> dict[str, Any]:
    output_path = Path(task["agent_output_path"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    task_path = Path(task["agent_task_path"])
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(build_date_planning_prompt(task), encoding="utf-8")
    if codex_no_run(env, "date_planner"):
        return {
            "agent": AGENT_NAME,
            "status": "blocked",
            "blockers": ["CODEX_DATE_PLANNER_NO_RUN=1"],
            "agent_task_path": str(task_path),
            "agent_output_path": str(output_path),
        }
    if output_path.exists():
        output_path.unlink()
    last_message_path = output_path.with_suffix(".last_message.txt")
    prompt_text = task_path.read_text(encoding="utf-8")
    command = build_codex_exec_command(
        env,
        prompt_text,
        stage="date_planner",
        cwd=PROJECT_ROOT,
        output_last_message=last_message_path,
        prompt_via_stdin=True,
    )
    completed = subprocess.run(
        command,
        cwd=ROOT,
        input=prompt_text,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        return {
            "agent": AGENT_NAME,
            "status": "blocked",
            "blockers": [f"date planner exited with code {completed.returncode}"],
            "agent_task_path": str(task_path),
            "agent_output_path": str(output_path),
        }
    if not output_path.exists():
        return {
            "agent": AGENT_NAME,
            "status": "blocked",
            "blockers": ["date planner did not write the required output JSON"],
            "agent_task_path": str(task_path),
            "agent_output_path": str(output_path),
        }
    try:
        output = json.loads(output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "agent": AGENT_NAME,
            "status": "blocked",
            "blockers": [f"date planner output is not valid JSON: {exc}"],
            "agent_task_path": str(task_path),
            "agent_output_path": str(output_path),
        }
    if isinstance(output, dict):
        output.setdefault("agent", AGENT_NAME)
        output.setdefault("agent_task_path", str(task_path))
        output.setdefault("agent_output_path", str(output_path))
        return output
    return {
        "agent": AGENT_NAME,
        "status": "blocked",
        "blockers": ["date planner output must be a JSON object"],
        "agent_task_path": str(task_path),
        "agent_output_path": str(output_path),
    }
