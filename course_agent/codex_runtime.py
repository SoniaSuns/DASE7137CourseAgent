from __future__ import annotations

import os
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CODEX_BIN = "/Applications/ChatGPT.app/Contents/Resources/codex"
DEFAULT_CODEX_MODEL = "gpt-5.5"


def _is_inside_git_worktree(path: Path) -> bool:
    candidate = path.resolve()
    return any((parent / ".git").exists() for parent in (candidate, *candidate.parents))


def _first_value(env: dict[str, str], keys: list[str], default: str = "") -> str:
    for key in keys:
        value = env.get(key, "").strip()
        if value:
            return value
    return default


def codex_bin(env: dict[str, str]) -> str:
    return _first_value(
        env,
        ["CODEX_BIN"],
        shutil.which("codex") or DEFAULT_CODEX_BIN,
    )


def codex_model(env: dict[str, str], stage: str = "agent") -> str:
    stage_key = f"CODEX_{stage.upper()}_MODEL"
    return _first_value(
        env,
        [
            stage_key,
            "CODEX_MODEL",
            "CODEX_FAST_MODEL",
        ],
        DEFAULT_CODEX_MODEL,
    )


def codex_timeout(env: dict[str, str], stage: str = "agent") -> str:
    stage_key = f"CODEX_{stage.upper()}_TIMEOUT"
    return _first_value(
        env,
        [
            stage_key,
            "CODEX_AGENT_TIMEOUT",
        ],
        "900",
    )


def codex_no_run(env: dict[str, str], stage: str = "agent") -> bool:
    stage_key = f"CODEX_{stage.upper()}_NO_RUN"
    raw = _first_value(env, [stage_key, "CODEX_AGENT_NO_RUN"], "0")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def unsupported_legacy_env_warnings(env: dict[str, str]) -> list[str]:
    warnings: list[str] = []
    for key in sorted(env):
        if not key.startswith("OPENCLAW_"):
            continue
        warnings.append(f"{key} is unsupported by the Codex-only course agent; use CODEX_* or COURSE_AGENT_* instead.")
    if env.get("WHATSAPP_MODE", "").strip().lower() == "openclaw_agent":
        warnings.append("WHATSAPP_MODE=openclaw_agent is unsupported; use codex_agent_web.")
    return warnings


def build_codex_exec_command(
    env: dict[str, str],
    prompt: str,
    *,
    stage: str = "agent",
    cwd: Path | None = None,
    output_schema: Path | None = None,
    output_last_message: Path | None = None,
    prompt_via_stdin: bool = True,
) -> list[str]:
    working_directory = (cwd or PROJECT_ROOT).resolve()
    command = [
        codex_bin(env),
        "exec",
        "--cd",
        str(working_directory),
        "--model",
        codex_model(env, stage),
    ]
    if not _is_inside_git_worktree(working_directory):
        command.append("--skip-git-repo-check")
    bypass = env.get("CODEX_DANGEROUSLY_BYPASS_APPROVALS_AND_SANDBOX", "").strip().lower()
    if bypass in {"1", "true", "yes", "on"}:
        command.append("--dangerously-bypass-approvals-and-sandbox")
    else:
        command.extend(["--sandbox", env.get("CODEX_SANDBOX", "workspace-write")])
    profile = env.get("CODEX_PROFILE", "").strip()
    if profile:
        command.extend(["--profile", profile])
    if output_schema is not None:
        command.extend(["--output-schema", str(output_schema)])
    if output_last_message is not None:
        command.extend(["--output-last-message", str(output_last_message)])
    command.append("-" if prompt_via_stdin else prompt)
    return command


def env_with_codex_defaults(env: dict[str, str] | None = None) -> dict[str, str]:
    merged = dict(os.environ if env is None else env)
    merged.setdefault("CODEX_BIN", codex_bin(merged))
    merged.setdefault("CODEX_MODEL", DEFAULT_CODEX_MODEL)
    merged.setdefault("CODEX_AGENT_TIMEOUT", "900")
    merged.setdefault("CODEX_SANDBOX", "workspace-write")
    merged.setdefault("CODEX_DANGEROUSLY_BYPASS_APPROVALS_AND_SANDBOX", "0")
    return merged
