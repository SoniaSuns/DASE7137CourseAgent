from __future__ import annotations

import re


COMMAND_NAMESPACE = "[M]"

SHORT_COMMANDS = {
    "APPROVE": "OK",
    "REJECT": "NO",
}

COMMAND_ALIASES = {
    "APPROVE": "APPROVE",
    "OK": "APPROVE",
    "REJECT": "REJECT",
    "NO": "REJECT",
}


def canonical_command(command: str) -> str:
    normalized = command.strip().upper()
    return COMMAND_ALIASES.get(normalized, normalized)


def approval_reply_command(
    command: str,
    batch_id: str,
    run_id: str,
    note: str = "",
    style: str = "short",
    approval_code: str | None = None,
) -> str:
    command = canonical_command(command)
    if command not in SHORT_COMMANDS:
        raise ValueError(f"unsupported approval command: {command}")
    return f"{SHORT_COMMANDS[command]} {approval_code or run_id}"


def approval_reply_commands(batch_id: str, run_id: str, approval_code: str | None = None) -> dict[str, str]:
    return {
        "approve": approval_reply_command("APPROVE", batch_id, run_id, approval_code=approval_code),
        "reject": approval_reply_command("REJECT", batch_id, run_id, approval_code=approval_code),
    }


def normalize_command_body(body: str) -> str:
    text = str(body or "").strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.strip("`").strip()
        if "\n" in text:
            first, rest = text.split("\n", 1)
            if re.fullmatch(r"[A-Za-z0-9_-]+", first.strip()):
                text = rest.strip()
    return text.strip().strip("`").strip()


def parse_approval_command(body: str, batch_id: str) -> dict | None:
    text = normalize_command_body(body)
    simple_pattern = r"^(OK|NO)\s+([A-Za-z0-9_-]+)$"
    match = re.match(simple_pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    command = canonical_command(match.group(1))
    return {
        "command": command,
        "run_id": (match.group(2) or "").strip(),
        "note": "",
    }
