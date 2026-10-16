from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT = PROJECT_ROOT / "course_agent_app"
AGENT_NAME = "semester-schedule-planning-agent"
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


def build_schedule_planning_prompt(task: dict[str, Any]) -> str:
    output_path = str(task["agent_output_path"])
    return f"""You are the DASE7137 Semester Schedule Planning Agent.

Your job is to convert staff-provided class meeting arrangements into structured JSON only.
Do not edit files. Do not upload to Moodle.

Inputs:
- Staff request:
```text
{task.get('raw_task', '')}
```
- Schedule text extracted from the current prompt and current attachments:
```text
{str(task.get('schedule_text') or '')[:60000]}
```
- Existing timeline/calendar reference payloads:
```json
{json.dumps(task.get('reference_payloads') or {}, ensure_ascii=False, indent=2)[:20000]}
```

Rules:
- Use only the supplied text and reference payloads.
- Keep two coordinates. Interpret staff mapping Week numbers as `teaching_week` unless staff explicitly says calendar/display week; derive continuous `calendar_week` from the semester start. File-refresh Date Planner later selects which coordinate a visible Week column represents.
- Classify the input as `recurring_weekdays` when staff gives a repeating weekday rule without fixing concrete dates, or `explicit_dates` when staff supplies concrete class dates. A weekday-only recurring rule is not an explicitly supplied class date.
- For `recurring_weekdays`, output `recurring_weekdays` and `requested_week_range`. Use an explicit staff week range when supplied; otherwise use Week 1 through timeline.json `week_count` or the complete teaching-week range represented by timeline.json.
- For `recurring_weekdays`, when an occurrence falls on a holiday or any class-suspension period, skip that occurrence and continue to the next eligible calendar week on the requested weekday. The skipped calendar week does not consume a teaching-week number.
- Never extend recurring classes into revision or assessment periods. If the requested number of teaching weeks cannot fit before `last_teaching`, return `blocked` with the number of eligible occurrences and the first teaching week that cannot be scheduled.
- Output weeks with concrete ISO dates in `teaching_dates`.
- `allowed_session_weekdays` must match the weekday of every teaching date.
- If the text gives only weekdays, derive dates from the corresponding teaching week in timeline.json or from calendar.json teaching-week anchors that skip non-teaching periods.
- When calendar.json contains `non_teaching_days` or `teaching_suspensions` with `classes_suspended=true`, exclude those dates from generated `teaching_dates`.
- Only when staff explicitly supplies a concrete class date that calendar.json marks as `classes_suspended=true`, set status to "blocked" instead of silently moving it.
- If any requested week cannot be converted to concrete dates, set status to "blocked" and explain why.
- Use merge_mode "replace_all".
- Output only JSON at the required path.

Required JSON shape:
{{
  "agent": "{AGENT_NAME}",
  "status": "ready|blocked",
  "summary": "short human-readable summary",
  "course_code": "{task.get('course_code', '')}",
  "semester": "{task.get('semester', '')}",
  "timezone": "Asia/Hong_Kong",
  "merge_mode": "replace_all",
  "arrangement_mode": "recurring_weekdays|explicit_dates",
  "recurring_weekdays": ["Tuesday"],
  "requested_week_range": {{"start": 1, "end": 12}},
  "week_numbering": {{
    "mode": "dual",
    "mapping_field": "teaching_week",
    "display_field": "date_planner_selected",
    "available_display_fields": ["calendar_week", "teaching_week"]
  }},
  "weeks": [
    {{
      "week": 1,
      "teaching_week": 1,
      "calendar_week": 1,
      "teaching_dates": ["2026-09-01"],
      "allowed_session_weekdays": ["Tuesday"],
      "notes": ""
    }}
  ],
  "blockers": []
}}

Write the JSON to:
{output_path}
"""


def run_schedule_planning_agent(env: dict[str, str], task: dict[str, Any]) -> dict[str, Any]:
    output_path = Path(task["agent_output_path"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    task_path = Path(task["agent_task_path"])
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(build_schedule_planning_prompt(task), encoding="utf-8")
    if codex_no_run(env, "schedule_planner"):
        return {
            "agent": AGENT_NAME,
            "status": "blocked",
            "blockers": ["CODEX_SCHEDULE_PLANNER_NO_RUN=1"],
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
        stage="schedule_planner",
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
            "blockers": [f"schedule planner exited with code {completed.returncode}"],
            "agent_task_path": str(task_path),
            "agent_output_path": str(output_path),
        }
    if not output_path.exists():
        return {
            "agent": AGENT_NAME,
            "status": "blocked",
            "blockers": ["schedule planner did not write the required output JSON"],
            "agent_task_path": str(task_path),
            "agent_output_path": str(output_path),
        }
    try:
        output = json.loads(output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "agent": AGENT_NAME,
            "status": "blocked",
            "blockers": [f"schedule planner output is not valid JSON: {exc}"],
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
        "blockers": ["schedule planner output must be a JSON object"],
        "agent_task_path": str(task_path),
        "agent_output_path": str(output_path),
    }
