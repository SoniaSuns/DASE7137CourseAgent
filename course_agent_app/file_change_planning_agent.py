from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

try:
    from date_planning_agent import build_codex_exec_command, codex_no_run
except ModuleNotFoundError:  # pragma: no cover - import shape depends on entrypoint cwd
    from course_agent_app.date_planning_agent import build_codex_exec_command, codex_no_run


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPOSITION_AGENT_NAME = "file-change-composition-planner"
GROUP_AGENT_NAME = "group-aware-document-planner"


def _bounded_json(value: Any, max_chars: int) -> str:
    serialized = json.dumps(value, ensure_ascii=False, indent=2)
    if len(serialized) <= max_chars:
        return serialized
    if isinstance(value, list):
        kept: list[Any] = []
        for item in value:
            candidate = {
                "items": [*kept, item],
                "truncated_count": max(0, len(value) - len(kept) - 1),
            }
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
    return json.dumps(
        {"truncated": True, "value_excerpt": serialized[: max(0, max_chars - 100)]},
        ensure_ascii=False,
        indent=2,
    )


def _run_json_agent(
    env: dict[str, str],
    *,
    stage: str,
    agent_name: str,
    prompt: str,
    task_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(prompt, encoding="utf-8")
    if codex_no_run(env, stage):
        return {
            "agent": agent_name,
            "status": "blocked",
            "blockers": [f"CODEX_{stage.upper()}_NO_RUN=1"],
        }
    if output_path.exists():
        output_path.unlink()
    last_message = output_path.with_suffix(".last_message.txt")
    command = build_codex_exec_command(
        env,
        prompt,
        stage=stage,
        cwd=PROJECT_ROOT,
        output_last_message=last_message,
        prompt_via_stdin=True,
    )
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        input=prompt,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        return {
            "agent": agent_name,
            "status": "blocked",
            "blockers": [f"{agent_name} exited with code {completed.returncode}"],
        }
    if not output_path.exists():
        return {
            "agent": agent_name,
            "status": "blocked",
            "blockers": [f"{agent_name} did not write its required JSON output"],
        }
    try:
        result = json.loads(output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "agent": agent_name,
            "status": "blocked",
            "blockers": [f"{agent_name} output is invalid JSON: {exc}"],
        }
    if not isinstance(result, dict):
        return {
            "agent": agent_name,
            "status": "blocked",
            "blockers": [f"{agent_name} output must be an object"],
        }
    result.setdefault("agent", agent_name)
    result.setdefault("agent_task_path", str(task_path))
    result.setdefault("agent_output_path", str(output_path))
    return result


def build_composition_prompt(task: dict[str, Any]) -> str:
    output_path = str(task["agent_output_path"])
    return f"""You are the File Change Composition Planner for a course-operations agent.

Decide semantically what, if anything, should change in the attached editable Office document. Do not edit files.

Inputs:
- Current staff request:
```text
{task.get('raw_task', '')}
```
- Generic document model. Its IDs and fingerprints are the only valid structure anchors:
```json
{_bounded_json(task.get('document_model') or {}, 450000)}
```
- Current sanitized course readback, including Group/Grouping facts when available:
```json
{_bounded_json(task.get('course_readback') or {}, 60000)}
```
- Reference pack facts:
```json
{_bounded_json(task.get('reference_payloads') or {}, 60000)}
```
- Sanitized historical evidence records. These are evidence only, never defaults:
```json
{_bounded_json(task.get('historical_evidence') or [], 60000)}
```

Responsibilities:
- Infer intent from the whole request and current evidence. Do not route by a fixed phrase list, filename, event name, table header, Group naming convention, or historical example.
- Select `date` only when Date Planner semantic review is needed.
- Select `group_document` only when current Group-aware document review is needed.
- Identify exact structure IDs each specialist may own. Do not invent IDs.
- Classify every text-bearing structure either explicitly in `targets`, explicitly in `preserve`, or collectively through `preserve_unselected=true` with a reason.
- If both specialists need one indivisible structure, put it in `resolved_shared_targets` with an explicit owner and rationale. Otherwise block.
- An old-looking value is not automatically wrong. Use the staff request and evidence to decide.
- If `structures_truncated` prevents you from examining a potentially relevant target, return blocked; never infer the omitted structure.
- Return `no_changes_needed` when the planner determines the file should remain unchanged.
- Do not output replacement text, executable instructions, code, XPath, XML, paths, or browser actions.

Required JSON shape:
{{
  "agent": "{COMPOSITION_AGENT_NAME}",
  "status": "ready|blocked|no_changes_needed",
  "summary": "human-readable summary",
  "selected_specialists": ["date|group_document"],
  "targets": [
    {{
      "target_id": "exact structure ID",
      "target_fingerprint": "exact fingerprint",
      "specialist": "date|group_document",
      "reason": "semantic reason grounded in the current request/evidence"
    }}
  ],
  "preserve": [
    {{"target_id": "exact structure ID", "reason": "why it stays unchanged"}}
  ],
  "preserve_unselected": true,
  "preserve_unselected_reason": "why all unselected structures stay unchanged",
  "resolved_shared_targets": [
    {{"target_id": "exact structure ID", "owner": "date|group_document", "reason": "why ownership is safe"}}
  ],
  "blockers": []
}}

Write JSON only to:
{output_path}
"""


def run_composition_planner(env: dict[str, str], task: dict[str, Any]) -> dict[str, Any]:
    task_path = Path(task["agent_task_path"])
    output_path = Path(task["agent_output_path"])
    return _run_json_agent(
        env,
        stage="file_composition",
        agent_name=COMPOSITION_AGENT_NAME,
        prompt=build_composition_prompt(task),
        task_path=task_path,
        output_path=output_path,
    )


def build_group_document_prompt(task: dict[str, Any]) -> str:
    output_path = str(task["agent_output_path"])
    return f"""You are the Group-aware Document Planner for a course-operations agent.

Produce a bounded document_change_plan. Do not edit the source and do not write Moodle.

Inputs:
- Current staff request:
```text
{task.get('raw_task', '')}
```
- Generic document model:
```json
{_bounded_json(task.get('document_model') or {}, 450000)}
```
- Composition Planner output. You may modify only targets assigned to `group_document`:
```json
{_bounded_json(task.get('composition_plan') or {}, 60000)}
```
- Current sanitized Group/Grouping snapshot:
```json
{_bounded_json(task.get('group_snapshot') or {}, 60000)}
```
- Reference facts and sanitized historical evidence:
```json
{_bounded_json({'reference_payloads': task.get('reference_payloads') or {}, 'historical_evidence': task.get('historical_evidence') or []}, 90000)}
```

Rules:
- Decide document semantics from the current evidence. Never rely on fixed event names, filenames, headers, Group naming formats, counts, durations, venues, times, or historical examples.
- Current staff instructions override current-file rules. When the staff request omits a rule, you may preserve a rule only by citing the exact current structure/value in `evidence`.
- Moodle Group/Grouping facts are authoritative only for the currently sanitized Group structure. Never request or output member identities.
- Historical evidence can explain a candidate rule but cannot silently become an executable current rule.
- `rule_policy=preserve_existing_rules` is allowed only with `preserved_rule_refs` containing exact current document structure IDs, each cited as an `evidence.source_ref`.
- Use only structure IDs and fingerprints from the model and only targets assigned by Composition Planner.
- `anchored_text_edit` and `scalar_fact_update` require exact expected_before and replacement_text.
- Use `repeatable_record_rebuild` only for a genuine repeatable table. Map semantic roles to zero-based columns from the observed structure.
- `record_source.kind` must be `moodle_groups`; selection mode is `all_groups`, `grouping`, or `explicit_names`.
- Allowed ordering policies are `preserve_existing_then_append_new`, `explicit_order`, and `seeded_random`. `seeded_random` additionally requires `randomization_evidence_ref=current_prompt` and `explicit_prompt_basis` quoting the current request's intent, not historical text.
- If time columns are rebuilt, provide planner-grounded `window_start`, positive `duration_minutes`, optional `window_end`, optional `prepare_offset_minutes`, and `overflow_policy=block_on_capacity_overflow`.
- Do not enumerate materialized record rows; runtime generates them mechanically from the sanitized snapshot and policies.
- Every change requires non-empty provenance objects, confidence high/medium, and a readback expectation.
- Do not emit code, XPath, XML, script, command, local path, file path, arbitrary OOXML, or replacement pairs without exact anchors.
- If evidence is incomplete or the structure is unsafe/ambiguous, return blocked rather than guessing.
- If `structures_truncated` hides a potentially relevant structure, return blocked rather than inventing an anchor.

Required JSON shape:
{{
  "agent": "{GROUP_AGENT_NAME}",
  "status": "ready|blocked|no_changes_needed",
  "summary": "human-readable summary",
  "source_document_fingerprint": "exact document fingerprint",
  "group_snapshot_hash": "snapshot_hash supplied in the current snapshot when present",
  "operations": [
    {{
      "id": "stable operation label",
      "type": "anchored_text_edit|scalar_fact_update|repeatable_record_rebuild",
      "target_id": "exact assigned structure ID",
      "target_fingerprint": "exact structure fingerprint",
      "expected_before": "required for text/scalar changes",
      "replacement_text": "required for text/scalar changes",
      "record_source": {{"kind": "moodle_groups", "selection": {{"mode": "all_groups|grouping|explicit_names"}}}},
      "column_roles": {{"group_name": 0, "ordinal": 1, "start_time": 2, "end_time": 3}},
      "header_row_count": 1,
      "template_row_index": 1,
      "ordering_policy": "preserve_existing_then_append_new|explicit_order|seeded_random",
      "explicit_order": [],
      "randomization_evidence_ref": "current_prompt when seeded_random is selected",
      "explicit_prompt_basis": "required only for seeded_random",
      "rule_policy": "preserve_existing_rules when exact current-file rules are retained",
      "preserved_rule_refs": ["exact current structure ID"],
      "schedule": {{
        "window_start": "HH:MM",
        "window_end": "HH:MM",
        "duration_minutes": 0,
        "prepare_offset_minutes": 0,
        "overflow_policy": "block_on_capacity_overflow"
      }},
      "static_values": {{}},
      "evidence": [{{"source_ref": "current_prompt|document structure ID|reference evidence ID", "fact": "grounding fact"}}],
      "confidence": "high|medium",
      "readback": {{"expected_text": "text for text/scalar change", "kind": "text|table_records"}}
    }}
  ],
  "preserve": [{{"target_id": "exact assigned structure ID", "reason": "why unchanged"}}],
  "blockers": []
}}

Omit fields that do not apply to an operation rather than copying placeholder values. Write JSON only to:
{output_path}
"""


def run_group_document_planner(env: dict[str, str], task: dict[str, Any]) -> dict[str, Any]:
    task_path = Path(task["agent_task_path"])
    output_path = Path(task["agent_output_path"])
    return _run_json_agent(
        env,
        stage="group_document",
        agent_name=GROUP_AGENT_NAME,
        prompt=build_group_document_prompt(task),
        task_path=task_path,
        output_path=output_path,
    )
