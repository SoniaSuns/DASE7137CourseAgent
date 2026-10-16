from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT = PROJECT_ROOT / "course_agent_app"
AGENT_NAME = "timeline-reconciliation-agent"
DEFAULT_CODEX_BIN = "/Applications/ChatGPT.app/Contents/Resources/codex"
DEFAULT_CODEX_MODEL = "gpt-5.5"


def _first_value(env: dict[str, str], keys: list[str], default: str = "") -> str:
    for key in keys:
        value = env.get(key, "").strip()
        if value:
            return value
    return default


def codex_bin(env: dict[str, str]) -> str:
    return _first_value(env, ["CODEX_BIN"], shutil.which("codex") or DEFAULT_CODEX_BIN)


def codex_model(env: dict[str, str]) -> str:
    return _first_value(
        env,
        ["CODEX_TIMELINE_RECONCILIATION_MODEL", "CODEX_MODEL", "CODEX_FAST_MODEL"],
        DEFAULT_CODEX_MODEL,
    )


def codex_no_run(env: dict[str, str]) -> bool:
    raw = _first_value(
        env,
        ["CODEX_TIMELINE_RECONCILIATION_NO_RUN", "CODEX_AGENT_NO_RUN"],
        "0",
    )
    return raw.lower() in {"1", "true", "yes", "on"}


def build_prompt(task: dict[str, Any]) -> str:
    draft = task.get("draft") if isinstance(task.get("draft"), dict) else {}
    review_numbering = (
        draft.get("review_week_numbering")
        if isinstance(draft.get("review_week_numbering"), dict)
        else {}
    )
    display_field = str(review_numbering.get("display_field") or "calendar_week")
    if display_field not in {"calendar_week", "teaching_week"}:
        display_field = "calendar_week"
    display_label = str(
        review_numbering.get("display_label")
        or ("Teaching Week" if display_field == "teaching_week" else "Calendar Week")
    )
    slots = [
        {
            "display_week": slot.get(display_field),
            "teaching_week": slot.get("teaching_week"),
            "calendar_week": slot.get("calendar_week"),
            "teaching_dates": slot.get("teaching_dates"),
            "valid": slot.get("valid"),
            "status": slot.get("status"),
        }
        for slot in draft.get("teaching_slots") or []
        if isinstance(slot, dict)
    ]
    display_week_by_teaching_week = {
        int(slot["teaching_week"]): slot.get(display_field)
        for slot in draft.get("teaching_slots") or []
        if isinstance(slot, dict) and isinstance(slot.get("teaching_week"), int)
    }
    items = [
        {
            "item_id": item.get("item_id"),
            "source_teaching_week": item.get("source_teaching_week"),
            "source_visible_week": item.get("source_visible_week"),
            "content_kind": item.get("content_kind"),
            "text": item.get("text"),
            "target_teaching_week": item.get("target_teaching_week"),
            "target_display_week": display_week_by_teaching_week.get(
                item.get("target_teaching_week")
            ),
            "status": item.get("status"),
        }
        for item in draft.get("content_items") or []
        if isinstance(item, dict)
    ]
    occupied_weeks: dict[int, list[str]] = {}
    for item in items:
        target_week = item.get("target_teaching_week")
        if item.get("status") == "deleted" or not isinstance(target_week, int):
            continue
        occupied_weeks.setdefault(target_week, []).append(str(item.get("item_id") or ""))
    occupancy = [
        {
            "target_teaching_week": week,
            "item_ids": item_ids,
        }
        for week, item_ids in sorted(occupied_weeks.items())
    ]
    return f"""You are the DASE7137 Timeline Reconciliation Agent.

Your only job is to translate the current staff revision prompt into bounded move/delete operations
against one active timeline draft. Do not edit files, invent content, rewrite text, or change dates.

Current staff prompt:
```text
{task.get('raw_task', '')}
```

Active draft:
- draft_id: {draft.get('draft_id', '')}
- version: {draft.get('version', '')}
- status: {draft.get('status', '')}
- valid teaching weeks: {draft.get('valid_teaching_week_count', '')}

Staff-visible week-numbering contract:
- The attached review table shows exactly one week-number column: `{display_label}`.
- Its structured field is `{display_field}`.
- An unqualified reference such as "Week N" or "第N周" refers to this displayed
  `{display_label}` value, not automatically to Teaching Week N.
- Only an explicit phrase such as "Teaching Week N" selects teaching_week directly.
- Resolve every numbered source/target week to the corresponding internal teaching_week before
  selecting item IDs or emitting target_teaching_week.

Teaching slots:
```json
{json.dumps(slots, ensure_ascii=False, indent=2)[:18000]}
```

Content items:
```json
{json.dumps(items, ensure_ascii=False, indent=2)[:30000]}
```

Current target-week occupancy:
```json
{json.dumps(occupancy, ensure_ascii=False, indent=2)[:12000]}
```

Rules:
- Use only item_id values present above.
- Allowed operation types are exactly `move_items` and `delete_items`.
- `move_items` must target a valid teaching slot. Never target an overflow slot.
- A request using an unqualified Week number selects the row with that value in the Staff-visible
  `{display_label}` column. A request explicitly naming a Teaching Week selects target_teaching_week.
  A request naming a source/original week selects by source_teaching_week only when the staff
  explicitly says source/original week.
- A request naming Lecture, Workshop/Tutorial/Lab, or Submission/Deadline selects only that content kind.
- Do not infer a different text, date, weekday, or content type.
- Do not emit the same item_id in more than one operation.
- Teaching slots are already the calendar-grounded sequence after holidays, Reading Weeks, and class
  suspensions have been skipped. Never move or count those non-teaching gaps as course content.
- Distinguish an isolated item move from a sequence reflow. If the requested move or deletion removes
  every non-deleted item from a currently occupied Teaching Week, that released content slot must be
  filled by compacting later occupied weeks toward it unless the staff explicitly asks to preserve an
  empty Teaching Week or preserve the later target-week assignments.
- For a sequence reflow, work from current target_teaching_week assignments, preserve content order,
  and emit every resulting change as explicit `move_items` operations. Move all items from the next
  occupied target week into the earliest released valid week, then continue through later and overflow
  weeks until there is no empty valid Teaching Week before remaining content.
- Compaction changes target teaching weeks only. Keep every item's stable ID, source teaching week,
  content kind, and original text unchanged.
- If the request is ambiguous between multiple different item sets, return blocked with a concise reason.
- Set `week_reference_field` to `{display_field}` for unqualified numbered weeks, to `teaching_week`
  only for explicitly named Teaching Weeks, or to `none` when the prompt contains no numbered week.
- Record every numbered source/target week in `week_reference_resolution`. The first operation must
  be the Staff-requested primary move/delete; later operations may contain the required compaction.
- Output only JSON to the required path.

Required JSON shape:
{{
  "agent": "{AGENT_NAME}",
  "status": "ready|blocked",
  "draft_id": "{draft.get('draft_id', '')}",
  "base_version": {int(draft.get('version') or 0)},
  "summary": "short description",
  "week_reference_field": "{display_field}|teaching_week|none",
  "week_reference_resolution": [
    {{
      "role": "source",
      "display_week": "<integer identified from the Staff prompt>",
      "resolved_teaching_week": "<integer resolved from Teaching slots>"
    }},
    {{
      "role": "target",
      "display_week": "<integer identified from the Staff prompt>",
      "resolved_teaching_week": "<integer resolved from Teaching slots>"
    }}
  ],
  "operations": [
    {{
      "type": "move_items",
      "item_ids": ["<stable item_id from the active draft>"],
      "target_teaching_week": "<valid Teaching Week integer selected from Teaching slots>",
      "reason": "grounded in the current staff prompt"
    }},
    {{
      "type": "delete_items",
      "item_ids": ["<stable item_id from the active draft>"],
      "reason": "grounded in the current staff prompt"
    }}
  ],
  "blockers": []
}}

Write the JSON to:
{task['agent_output_path']}
"""


def run_timeline_reconciliation_agent(
    env: dict[str, str],
    task: dict[str, Any],
) -> dict[str, Any]:
    output_path = Path(task["agent_output_path"])
    task_path = Path(task["agent_task_path"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(build_prompt(task), encoding="utf-8")
    if codex_no_run(env):
        return {
            "agent": AGENT_NAME,
            "status": "blocked",
            "blockers": ["CODEX_TIMELINE_RECONCILIATION_NO_RUN=1"],
        }
    output_path.unlink(missing_ok=True)
    last_message_path = output_path.with_suffix(".last_message.txt")
    command = [
        codex_bin(env),
        "exec",
        "--cd",
        str(PROJECT_ROOT),
        "--model",
        codex_model(env),
    ]
    if not any((parent / ".git").exists() for parent in (PROJECT_ROOT.resolve(), *PROJECT_ROOT.resolve().parents)):
        command.append("--skip-git-repo-check")
    bypass = env.get("CODEX_DANGEROUSLY_BYPASS_APPROVALS_AND_SANDBOX", "").strip().lower()
    if bypass in {"1", "true", "yes", "on"}:
        command.append("--dangerously-bypass-approvals-and-sandbox")
    else:
        command.extend(["--sandbox", env.get("CODEX_SANDBOX", "workspace-write")])
    profile = env.get("CODEX_PROFILE", "").strip()
    if profile:
        command.extend(["--profile", profile])
    prompt_text = task_path.read_text(encoding="utf-8")
    command.extend(["--output-last-message", str(last_message_path), "-"])
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
            "blockers": [f"timeline reconciliation planner exited with code {completed.returncode}"],
        }
    try:
        output = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "agent": AGENT_NAME,
            "status": "blocked",
            "blockers": [f"timeline reconciliation planner output is unavailable or invalid: {exc}"],
        }
    if not isinstance(output, dict):
        return {
            "agent": AGENT_NAME,
            "status": "blocked",
            "blockers": ["timeline reconciliation planner output must be a JSON object"],
        }
    output.setdefault("agent", AGENT_NAME)
    return output
