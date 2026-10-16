from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse


SANDBOX_COURSE_ID = "105330"
SANDBOX_HOST = "migrationtest-moodle.hku.hk"
MEMBER_SELECTORS = (
    "#removeselect",
    "select[name='removeselect[]']",
    "select[id*='removeselect']",
)
CANDIDATE_SELECTORS = (
    "#addselect",
    "select[name='addselect[]']",
    "select[id*='addselect']",
)
ADD_BUTTON_SELECTORS = ("#add", "button[name='add']", "input[name='add']")
REMOVE_BUTTON_SELECTORS = ("#remove", "button[name='remove']", "input[name='remove']")
SEARCH_CONTROLS = (
    (
        ("#removeselect_searchtext", "input[name='removeselect_searchtext']"),
        ("#removeselect_searchbutton", "button[name='removeselect_searchbutton']", "input[name='removeselect_searchbutton']"),
    ),
    (
        ("#addselect_searchtext", "input[name='addselect_searchtext']"),
        ("#addselect_searchbutton", "button[name='addselect_searchbutton']", "input[name='addselect_searchbutton']"),
    ),
)


def _numeric(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text.isdigit() or int(text) <= 0:
        raise RuntimeError(f"{label} must be a positive numeric Moodle id.")
    return text


def _group_members_url(group_id: str) -> str:
    return f"https://{SANDBOX_HOST}/group/members.php?{urlencode({'group': group_id})}"


def _ensure_sandbox(page: Any) -> None:
    parsed = urlparse(str(getattr(page, "url", "") or ""))
    if parsed.hostname != SANDBOX_HOST or parsed.path != "/group/members.php":
        raise RuntimeError("Group membership browser operation left the Moodle sandbox members page.")


def _goto_group(page: Any, group_id: str) -> None:
    url = _group_members_url(group_id)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception:
        current = urlparse(str(getattr(page, "url", "") or ""))
        if current.hostname != SANDBOX_HOST or current.path != "/group/members.php":
            raise
        if not page.locator("body").count():
            raise
    _ensure_sandbox(page)


def _first_select(page: Any, selectors: tuple[str, ...]):
    for selector in selectors:
        locator = page.locator(selector).first
        if locator.count():
            return locator
    return None


def _has_option_value(select: Any, user_id: str) -> bool:
    if select is None:
        return False
    return bool(
        select.locator("option").evaluate_all(
            "(options, expected) => options.some(option => String(option.value || '').trim() === expected)",
            user_id,
        )
    )


def _fill_first(page: Any, selectors: tuple[str, ...], value: str) -> bool:
    for selector in selectors:
        locator = page.locator(selector).first
        if not locator.count():
            continue
        locator.fill(value)
        return True
    return False


def _click_optional(page: Any, selectors: tuple[str, ...]) -> bool:
    for selector in selectors:
        locator = page.locator(selector).first
        if not locator.count():
            continue
        locator.click(timeout=10000)
        return True
    return False


def _prepare_name_search(page: Any, query: str) -> None:
    used_search_control = False
    for input_selectors, button_selectors in SEARCH_CONTROLS:
        if not _fill_first(page, input_selectors, query):
            continue
        used_search_control = True
        _click_optional(page, button_selectors)
    if used_search_control:
        page.wait_for_timeout(500)


def _matching_option_values(select: Any, query: str) -> list[str]:
    if select is None:
        return []
    values = select.locator("option").evaluate_all(
        r"""
        (options, rawQuery) => {
            const normalize = value => String(value || '')
                .normalize('NFKC')
                .replace(/\s+/g, ' ')
                .trim()
                .toLocaleLowerCase();
            const query = normalize(rawQuery);
            if (!query) return [];
            return [...new Set(options
                .filter(option => /^[1-9]\d*$/.test(String(option.value || '').trim()))
                .filter(option => normalize(option.textContent).includes(query))
                .map(option => String(option.value || '').trim()))];
        }
        """,
        query,
    )
    return [str(value) for value in values if str(value).isdigit() and int(str(value)) > 0]


def _membership_state_by_name(page: Any, group_id: str, query: str) -> tuple[str, dict[str, bool]]:
    _goto_group(page, group_id)
    _prepare_name_search(page, query)
    members = _first_select(page, MEMBER_SELECTORS)
    candidates = _first_select(page, CANDIDATE_SELECTORS)
    if members is None or candidates is None:
        raise RuntimeError("Moodle did not expose both Group member selectors; blocked before write.")
    member_ids = set(_matching_option_values(members, query))
    candidate_ids = set(_matching_option_values(candidates, query))
    matches = member_ids | candidate_ids
    if not matches:
        raise RuntimeError("Participant name search returned no match in one required Group page.")
    if len(matches) != 1:
        raise RuntimeError("Participant name search returned multiple matches in one required Group page.")
    user_id = next(iter(matches))
    return user_id, {
        "member": user_id in member_ids,
        "candidate": user_id in candidate_ids,
    }


def _membership_state(page: Any, group_id: str, user_id: str) -> dict[str, bool]:
    _goto_group(page, group_id)
    members = _first_select(page, MEMBER_SELECTORS)
    candidates = _first_select(page, CANDIDATE_SELECTORS)
    if members is None or candidates is None:
        raise RuntimeError("Moodle did not expose both Group member selectors; blocked before write.")
    return {
        "member": _has_option_value(members, user_id),
        "candidate": _has_option_value(candidates, user_id),
    }


def _click_first(page: Any, selectors: tuple[str, ...]) -> None:
    for selector in selectors:
        locator = page.locator(selector).first
        if not locator.count():
            continue
        locator.click(timeout=10000)
        return
    raise RuntimeError("Moodle did not expose the expected Group membership action control.")


def _wait_for_membership(page: Any, user_id: str, expected_member: bool) -> None:
    deadline = time.time() + 20
    while time.time() < deadline:
        members = _first_select(page, MEMBER_SELECTORS)
        if members is not None and _has_option_value(members, user_id) is expected_member:
            return
        page.wait_for_timeout(250)
    state = "present" if expected_member else "absent"
    raise RuntimeError(f"Moodle did not verify that the participant is {state} in the Group.")


def _change_current_group(page: Any, user_id: str, *, add: bool) -> None:
    source = _first_select(page, CANDIDATE_SELECTORS if add else MEMBER_SELECTORS)
    if source is None:
        raise RuntimeError("Moodle did not expose the required Group membership selector.")
    source.select_option(value=user_id)
    _click_first(page, ADD_BUTTON_SELECTORS if add else REMOVE_BUTTON_SELECTORS)
    _wait_for_membership(page, user_id, add)


def _write_sanitized_result(report_dir: Path | None, result: dict[str, Any]) -> None:
    if report_dir is None:
        return
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def execute_group_membership_operation(
    page: Any,
    operation: dict[str, Any],
    *,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    """Execute one approved membership delta and persist only sanitized results."""
    action = str(operation.get("membership_action") or "").strip().casefold()
    if action not in {"add", "remove", "move"}:
        raise RuntimeError("Group membership action must be add, remove, or move.")
    supplied_user_id = str(operation.get("user_id") or "").strip()
    user_name_query = str(operation.get("user_name_query") or "").strip()
    if bool(supplied_user_id) == bool(user_name_query):
        raise RuntimeError("Provide exactly one participant reference: user_id or user_name.")
    user_id = _numeric(supplied_user_id, "user_id") if supplied_user_id else ""
    source_group_id = str(operation.get("source_group_id") or "").strip()
    target_group_id = str(operation.get("target_group_id") or "").strip()
    if action in {"remove", "move"}:
        source_group_id = _numeric(source_group_id, "source_group_id")
    if action in {"add", "move"}:
        target_group_id = _numeric(target_group_id, "target_group_id")
    if action == "move" and source_group_id == target_group_id:
        raise RuntimeError("Source and target Groups must be different for a move.")

    result: dict[str, Any] = {
        "ok": False,
        "status": "BLOCKED",
        "operation_id": str(operation.get("operation_id") or ""),
        "target_object": str(operation.get("target_object") or "Group membership"),
        "membership_action": action,
        "source_group_name": str(operation.get("source_group_name") or ""),
        "target_group_name": str(operation.get("target_group_name") or ""),
        "saves_count": 0,
        "write_action_taken": False,
        "membership_verified": False,
        "source_count_delta": 0,
        "target_count_delta": 0,
    }
    added_to_target = False
    try:
        if user_name_query:
            source_match = (
                _membership_state_by_name(page, source_group_id, user_name_query)
                if action in {"remove", "move"}
                else None
            )
            target_match = (
                _membership_state_by_name(page, target_group_id, user_name_query)
                if action in {"add", "move"}
                else None
            )
            resolved_ids = {
                match[0] for match in (source_match, target_match) if match is not None
            }
            if len(resolved_ids) != 1:
                raise RuntimeError(
                    "Participant name search did not resolve to the same unique Moodle user across required Group pages."
                )
            user_id = next(iter(resolved_ids))
            source_state = source_match[1] if source_match else None
            target_state = target_match[1] if target_match else None
        else:
            source_state = (
                _membership_state(page, source_group_id, user_id)
                if action in {"remove", "move"}
                else None
            )
            target_state = (
                _membership_state(page, target_group_id, user_id)
                if action in {"add", "move"}
                else None
            )

        if action == "add":
            if bool(target_state and target_state["member"]):
                result.update(ok=True, status="ALREADY_SATISFIED", membership_verified=True)
            elif not bool(target_state and target_state["candidate"]):
                raise RuntimeError(
                    "The uniquely resolved participant is not available in the target Group selector."
                )
            else:
                _goto_group(page, target_group_id)
                if user_name_query:
                    _prepare_name_search(page, user_name_query)
                _change_current_group(page, user_id, add=True)
                result.update(
                    ok=True,
                    status="PASS",
                    saves_count=1,
                    write_action_taken=True,
                    membership_verified=True,
                    target_count_delta=1,
                )
        elif action == "remove":
            if not bool(source_state and source_state["member"]):
                result.update(ok=True, status="ALREADY_SATISFIED", membership_verified=True)
            else:
                _goto_group(page, source_group_id)
                if user_name_query:
                    _prepare_name_search(page, user_name_query)
                _change_current_group(page, user_id, add=False)
                result.update(
                    ok=True,
                    status="PASS",
                    saves_count=1,
                    write_action_taken=True,
                    membership_verified=True,
                    source_count_delta=-1,
                )
        else:
            if not bool(source_state and source_state["member"]):
                raise RuntimeError("The participant is not currently in the approved source Group.")
            if not bool(target_state and (target_state["member"] or target_state["candidate"])):
                raise RuntimeError(
                    "The uniquely resolved participant is not available in the target Group selector."
                )
            if target_state and not target_state["member"]:
                _goto_group(page, target_group_id)
                if user_name_query:
                    _prepare_name_search(page, user_name_query)
                _change_current_group(page, user_id, add=True)
                added_to_target = True
                result["saves_count"] = 1
                result["write_action_taken"] = True
                result["target_count_delta"] = 1
            try:
                _goto_group(page, source_group_id)
                if user_name_query:
                    _prepare_name_search(page, user_name_query)
                _change_current_group(page, user_id, add=False)
            except Exception:
                if added_to_target:
                    _goto_group(page, target_group_id)
                    if user_name_query:
                        _prepare_name_search(page, user_name_query)
                    _change_current_group(page, user_id, add=False)
                    result["saves_count"] = int(result["saves_count"]) + 1
                    result["target_count_delta"] = 0
                    result["rollback_performed"] = True
                raise
            result.update(
                ok=True,
                status="PASS",
                saves_count=int(result["saves_count"]) + 1,
                write_action_taken=True,
                membership_verified=True,
                source_count_delta=-1,
            )
    except Exception as exc:
        result["blocker"] = str(exc)
    _write_sanitized_result(report_dir, result)
    return result
