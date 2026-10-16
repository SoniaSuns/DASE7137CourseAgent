from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

from moodle_choicegroup_form import read_shared_group_quota


SANDBOX_COURSE_ID = "105330"
SANDBOX_HOST = "migrationtest-moodle.hku.hk"
SANDBOX_COURSE_URL = f"https://{SANDBOX_HOST}/course/view.php?id={SANDBOX_COURSE_ID}"
GROUP_STRUCTURE_READBACK_KIND = "group_structure_readback"
GROUP_CAPABILITIES_REQUIRING_DETAIL = {
    "inspect_group_structure",
    "update_grouping_membership",
    "update_group_membership",
    "delete_group",
    "delete_grouping",
    "create_group_signup_activity",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _numeric_text(value: Any) -> str:
    text = _text(value)
    return text if text.isdigit() else ""


def _payload_items(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        source = payload
    elif isinstance(payload, dict):
        source = payload.get(key) or payload.get("items") or []
    else:
        source = []
    return [dict(item) for item in source if isinstance(item, dict)]


def _course_choicegroups(sections: Any) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for section in sections if isinstance(sections, list) else []:
        if not isinstance(section, dict):
            continue
        section_name = _text(section.get("name"))
        sectionnum = _text(section.get("sectionnum") if "sectionnum" in section else section.get("section"))
        for module in section.get("modules") or []:
            if not isinstance(module, dict) or _text(module.get("modname")).casefold() != "choicegroup":
                continue
            cmid = _numeric_text(module.get("cmid") if "cmid" in module else module.get("id"))
            if not cmid:
                continue
            results.append(
                {
                    "cmid": cmid,
                    "title": _text(module.get("name")),
                    "section": section_name,
                    "sectionnum": sectionnum,
                    "selected_group_names": [],
                    "visible": module.get("visible"),
                    "maxanswers": None,
                    "quota_enabled": None,
                    "quota_consistent": None,
                    "selected_quota_count": 0,
                }
            )
    return results


def base_group_structure(
    groups_payload: Any,
    groupings_payload: Any,
    sections: Any,
    *,
    status: str = "api_only",
) -> dict[str, Any]:
    groups: list[dict[str, Any]] = []
    for item in _payload_items(groups_payload, "groups"):
        group_id = _numeric_text(item.get("id") or item.get("groupid"))
        name = _text(item.get("name"))
        if not group_id or not name:
            continue
        groups.append(
            {
                "id": group_id,
                "name": name,
                "idnumber": _text(item.get("idnumber")),
                "visibility": item.get("visibility"),
                "participation": item.get("participation"),
                "member_count": None,
                "grouping_names": [],
                "choicegroup_refs": [],
            }
        )
    groupings: list[dict[str, Any]] = []
    for item in _payload_items(groupings_payload, "groupings"):
        grouping_id = _numeric_text(item.get("id") or item.get("groupingid"))
        name = _text(item.get("name"))
        if not grouping_id or not name:
            continue
        groupings.append(
            {
                "id": grouping_id,
                "name": name,
                "idnumber": _text(item.get("idnumber")),
                "group_names": [],
            }
        )
    snapshot = {
        "status": status,
        "course_id": SANDBOX_COURSE_ID,
        "groups": sorted(groups, key=lambda item: (item["name"].casefold(), item["id"])),
        "groupings": sorted(groupings, key=lambda item: (item["name"].casefold(), item["id"])),
        "choicegroup_activities": _course_choicegroups(sections),
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "evidence": [],
    }
    snapshot["snapshot_hash"] = group_structure_hash(snapshot)
    snapshot["action_preflight_hash"] = group_action_preflight_hash(snapshot)
    return snapshot


def _canonical_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    groups = []
    for item in snapshot.get("groups") or []:
        if not isinstance(item, dict):
            continue
        groups.append(
            {
                "id": _text(item.get("id")),
                "name": _text(item.get("name")),
                "idnumber": _text(item.get("idnumber")),
                "visibility": item.get("visibility"),
                "participation": item.get("participation"),
                "member_count": item.get("member_count"),
                "grouping_names": sorted(_text(value) for value in item.get("grouping_names") or []),
                "choicegroup_refs": sorted(
                    (
                        {
                            "cmid": _text(ref.get("cmid")),
                            "title": _text(ref.get("title")),
                            "section": _text(ref.get("section")),
                        }
                        for ref in item.get("choicegroup_refs") or []
                        if isinstance(ref, dict)
                    ),
                    key=lambda ref: (ref["cmid"], ref["title"]),
                ),
            }
        )
    groupings = []
    for item in snapshot.get("groupings") or []:
        if not isinstance(item, dict):
            continue
        groupings.append(
            {
                "id": _text(item.get("id")),
                "name": _text(item.get("name")),
                "idnumber": _text(item.get("idnumber")),
                "group_names": sorted(_text(value) for value in item.get("group_names") or []),
            }
        )
    activities = []
    for item in snapshot.get("choicegroup_activities") or []:
        if not isinstance(item, dict):
            continue
        activities.append(
            {
                "cmid": _text(item.get("cmid")),
                "title": _text(item.get("title")),
                "section": _text(item.get("section")),
                "sectionnum": _text(item.get("sectionnum")),
                "selected_group_names": sorted(
                    _text(value) for value in item.get("selected_group_names") or []
                ),
                "visible": item.get("visible"),
                "maxanswers": item.get("maxanswers"),
                "quota_enabled": item.get("quota_enabled"),
                "quota_consistent": item.get("quota_consistent"),
                "selected_quota_count": item.get("selected_quota_count"),
            }
        )
    return {
        "course_id": _text(snapshot.get("course_id") or SANDBOX_COURSE_ID),
        "groups": sorted(groups, key=lambda item: (item["name"].casefold(), item["id"])),
        "groupings": sorted(groupings, key=lambda item: (item["name"].casefold(), item["id"])),
        "choicegroup_activities": sorted(
            activities, key=lambda item: (item["title"].casefold(), item["cmid"])
        ),
    }


def group_structure_hash(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(
        _canonical_snapshot(snapshot),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def group_action_preflight_hash(snapshot: dict[str, Any]) -> str:
    """Hash only stable Group relationships that can change write safety."""
    canonical = _canonical_snapshot(snapshot)
    stable = {
        "course_id": canonical["course_id"],
        "groups": [
            {
                "id": item["id"],
                "name": item["name"],
                "grouping_names": item["grouping_names"],
                "choicegroup_refs": item["choicegroup_refs"],
            }
            for item in canonical["groups"]
        ],
        "groupings": [
            {
                "id": item["id"],
                "name": item["name"],
                "group_names": item["group_names"],
            }
            for item in canonical["groupings"]
        ],
        "choicegroup_activities": [
            {
                "cmid": item["cmid"],
                "title": item["title"],
                "selected_group_names": item["selected_group_names"],
            }
            for item in canonical["choicegroup_activities"]
        ],
    }
    payload = json.dumps(
        stable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def group_capability_names(manifest: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for operation in manifest.get("operations") or []:
        if not isinstance(operation, dict):
            continue
        capability = _text(operation.get("capability"))
        action = _text(operation.get("action"))
        kind = _text(operation.get("operation_kind"))
        if capability:
            names.add(capability)
        if capability in GROUP_CAPABILITIES_REQUIRING_DETAIL:
            continue
        if kind in {
            "group_api",
            "grouping_api",
            "browser_group_membership",
            "browser_group_signup_activity",
        }:
            names.add(action or kind)
    return names


def manifest_needs_detailed_group_structure(manifest: dict[str, Any]) -> bool:
    # Semantic planners may request one bounded, read-only detail pass while
    # keeping the selected capability unchanged.  Runtime only honors the
    # explicit typed flag; it does not infer this need from staff wording,
    # filenames, event names, headers, or Group names.
    for operation in manifest.get("operations") or []:
        if not isinstance(operation, dict):
            continue
        params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
        if params.get("require_detailed_group_structure") is True:
            return True
    names = group_capability_names(manifest)
    return bool(
        names
        & (
            GROUP_CAPABILITIES_REQUIRING_DETAIL
            | {
                "create_group",
                "create_grouping",
                "assign_group_names_to_grouping",
                "assign_group_to_grouping",
                "unassign_group_from_grouping",
            }
        )
    )


def manifest_has_group_writes(manifest: dict[str, Any]) -> bool:
    for operation in manifest.get("operations") or []:
        if not isinstance(operation, dict):
            continue
        capability = _text(operation.get("capability"))
        kind = _text(operation.get("operation_kind"))
        if capability == "inspect_group_structure" or kind == GROUP_STRUCTURE_READBACK_KIND:
            continue
        if capability in {
            "create_group",
            "create_grouping",
            "update_grouping_membership",
            "update_group_membership",
            "delete_group",
            "delete_grouping",
            "create_group_signup_activity",
        } or kind in {
            "group_api",
            "grouping_api",
            "browser_group_membership",
            "browser_group_signup_activity",
        }:
            return True
    return False


def _sandbox_url(path: str, **query: Any) -> str:
    return f"https://{SANDBOX_HOST}{path}?{urlencode(query)}"


def _ensure_sandbox_page(page: Any) -> None:
    parsed = urlparse(_text(getattr(page, "url", "")))
    if parsed.hostname != SANDBOX_HOST:
        raise RuntimeError("Group structure readback left the Moodle sandbox host.")
    page_text = ""
    try:
        page_text = _text(page.locator("body").inner_text(timeout=5000)).casefold()
    except Exception:
        pass
    if "/login/" in parsed.path or ("log in" in page_text and "forgotten" in page_text):
        raise RuntimeError("Moodle requested login during group structure readback.")


def _goto(page: Any, url: str) -> None:
    parsed = urlparse(url)
    if parsed.hostname != SANDBOX_HOST:
        raise RuntimeError("Refusing non-sandbox group structure readback URL.")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception:
        current = urlparse(_text(getattr(page, "url", "")))
        if current.hostname != SANDBOX_HOST:
            raise
        # Moodle may finish rendering the requested page while Playwright is
        # still waiting on a slow subresource. A usable sandbox DOM is enough
        # for this read-only inspection.
        if not page.locator("body").count():
            raise
    _ensure_sandbox_page(page)


def _restore_sandbox_course_page(page: Any, evidence: list[str]) -> None:
    """Best-effort UI cleanup after a complete read-only snapshot.

    A transient Playwright/navigation failure must not discard group data that
    was already collected and hashed.  Keep the failure fatal if the browser
    has left the sandbox host, because that is a safety-boundary violation
    rather than harmless UI cleanup.
    """
    try:
        _goto(page, SANDBOX_COURSE_URL)
    except Exception as exc:
        current = urlparse(_text(getattr(page, "url", "")))
        if current.hostname != SANDBOX_HOST:
            raise
        evidence.append(f"course-page-restore-warning:{type(exc).__name__}")


def _option_entries(page: Any, selectors: list[str]) -> list[dict[str, str]] | None:
    for selector in selectors:
        locator = page.locator(selector).first
        if not locator.count():
            continue
        values = locator.locator("option").evaluate_all(
            """
            options => options.map(option => ({
                value: (option.value || '').trim(),
                text: (option.textContent || '').replace(/\\s+/g, ' ').trim()
            })).filter(option => option.text)
            """
        )
        entries: list[dict[str, str]] = []
        for value in values:
            if isinstance(value, dict):
                text = _text(value.get("text"))
                option_value = _text(value.get("value"))
            else:
                # Preserve compatibility with simple test doubles while the
                # real Moodle DOM returns both the stable value and label.
                text = _text(value)
                option_value = ""
            if text:
                entries.append({"value": option_value, "text": text})
        return entries
    return None


def _group_names_from_options(
    entries: list[dict[str, str]],
    groups: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    names_by_id = {
        _numeric_text(group.get("id")): _text(group.get("name"))
        for group in groups
        if _numeric_text(group.get("id")) and _text(group.get("name"))
    }
    ids_by_name: dict[str, list[str]] = {}
    for group_id, name in names_by_id.items():
        ids_by_name.setdefault(name, []).append(group_id)

    resolved: list[str] = []
    unknown: list[str] = []
    for entry in entries:
        option_value = _numeric_text(entry.get("value"))
        option_text = _text(entry.get("text"))
        if option_value and option_value in names_by_id:
            resolved.append(names_by_id[option_value])
            continue
        matching_ids = ids_by_name.get(option_text, [])
        if len(matching_ids) == 1:
            resolved.append(option_text)
            continue
        unknown.append(option_text or option_value)
    return sorted(set(resolved), key=str.casefold), unknown


def _member_option_count(page: Any, selectors: list[str]) -> int | None:
    for selector in selectors:
        locator = page.locator(selector).first
        if not locator.count():
            continue
        return int(
            locator.locator("option").evaluate_all(
                """
                options => options.filter(option => {
                    const value = (option.value || '').trim();
                    return !option.disabled && /^[1-9]\\d*$/.test(value);
                }).length
                """
            )
        )
    return None


def _field_value(page: Any, selectors: list[str]) -> Any:
    for selector in selectors:
        locator = page.locator(selector).first
        if not locator.count():
            continue
        try:
            return locator.input_value()
        except Exception:
            try:
                return locator.get_attribute("value")
            except Exception:
                return None
    return None


def _save_evidence(page: Any, report_dir: Path | None, name: str, evidence: list[str]) -> None:
    if report_dir is None:
        evidence.append(_text(getattr(page, "url", "")))
        return
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / name
    page.screenshot(path=str(path), full_page=True)
    evidence.append(str(path))


def read_detailed_group_structure(
    page: Any,
    base_snapshot: dict[str, Any],
    *,
    report_dir: Path | None = None,
    group_names: list[str] | None = None,
    grouping_names: list[str] | None = None,
    include_choicegroup_dependencies: bool = True,
) -> dict[str, Any]:
    if _text(base_snapshot.get("course_id")) != SANDBOX_COURSE_ID:
        raise RuntimeError("Group structure readback is limited to sandbox course 105330.")
    snapshot = json.loads(json.dumps(base_snapshot, ensure_ascii=False))
    snapshot["status"] = "detailed"
    evidence: list[str] = []
    wanted_groups = {_text(value) for value in group_names or [] if _text(value)}
    wanted_groupings = {_text(value) for value in grouping_names or [] if _text(value)}
    groups = [item for item in snapshot.get("groups") or [] if isinstance(item, dict)]
    groupings = [item for item in snapshot.get("groupings") or [] if isinstance(item, dict)]

    missing_groups = wanted_groups - {_text(item.get("name")) for item in groups}
    missing_groupings = wanted_groupings - {_text(item.get("name")) for item in groupings}
    if missing_groups:
        raise RuntimeError(f"Could not find exact Moodle Group names: {sorted(missing_groups)}")
    if missing_groupings:
        raise RuntimeError(f"Could not find exact Moodle Grouping names: {sorted(missing_groupings)}")

    for index, group in enumerate(groups, start=1):
        if wanted_groups and _text(group.get("name")) not in wanted_groups:
            continue
        group_id = _numeric_text(group.get("id"))
        _goto(page, _sandbox_url("/group/members.php", group=group_id))
        member_count = _member_option_count(
            page,
            [
                "#removeselect",
                "select[name='removeselect[]']",
                "select[id*='removeselect']",
            ],
        )
        if member_count is None:
            raise RuntimeError(
                f"Moodle did not expose the member list for Group {_text(group.get('name'))!r}."
            )
        group["member_count"] = member_count
        evidence.append(
            f"group-member-count:{_text(group.get('name'))}:{group['member_count']}"
        )

    grouping_members: dict[str, list[str]] = {}
    for index, grouping in enumerate(groupings, start=1):
        if wanted_groupings and _text(grouping.get("name")) not in wanted_groupings:
            continue
        grouping_id = _numeric_text(grouping.get("id"))
        _goto(
            page,
            _sandbox_url(
                "/group/assign.php",
                id=grouping_id,
            ),
        )
        assigned_entries = _option_entries(
            page,
            [
                "#removeselect",
                "select[name='removeselect[]']",
                "select[id*='removeselect']",
            ],
        )
        if assigned_entries is None:
            raise RuntimeError(
                f"Moodle did not expose assigned Groups for Grouping {_text(grouping.get('name'))!r}."
            )
        assigned, unknown = _group_names_from_options(assigned_entries, groups)
        if unknown:
            raise RuntimeError(
                f"Grouping {_text(grouping.get('name'))!r} exposed unknown Group labels."
            )
        grouping["group_names"] = sorted(assigned, key=str.casefold)
        grouping_members[_text(grouping.get("name"))] = list(grouping["group_names"])
        _save_evidence(page, report_dir, f"grouping_{index:02d}_groups.png", evidence)

    reverse_groupings: dict[str, list[str]] = {}
    for grouping_name, assigned in grouping_members.items():
        for group_name in assigned:
            reverse_groupings.setdefault(group_name, []).append(grouping_name)
    for group in groups:
        if not wanted_groupings or _text(group.get("name")) in reverse_groupings:
            group["grouping_names"] = sorted(
                reverse_groupings.get(_text(group.get("name")), []),
                key=str.casefold,
            )

    activities = [
        item for item in snapshot.get("choicegroup_activities") or [] if isinstance(item, dict)
    ]
    if include_choicegroup_dependencies:
        for index, activity in enumerate(activities, start=1):
            cmid = _numeric_text(activity.get("cmid"))
            _goto(
                page,
                _sandbox_url(
                    "/course/modedit.php",
                    update=cmid,
                    **{"return": 0, "sr": 0},
                ),
            )
            selected_entries = _option_entries(
                page,
                [
                    "#id_selectedGroups",
                    "select[name='groups[]']",
                    "select[name='groupids[]']",
                ],
            )
            if selected_entries is None:
                raise RuntimeError(
                    f"Moodle did not expose selected Groups for Choicegroup {_text(activity.get('title'))!r}."
                )
            selected, unknown = _group_names_from_options(selected_entries, groups)
            if unknown:
                raise RuntimeError(
                    f"Choicegroup {_text(activity.get('title'))!r} exposed unknown Group labels."
                )
            activity["selected_group_names"] = sorted(selected, key=str.casefold)
            visible_value = _field_value(page, ["#id_visible", "select[name='visible']"])
            if visible_value not in (None, ""):
                activity["visible"] = _text(visible_value) not in {"0", "false"}
            quota = read_shared_group_quota(page, selected_entries)
            activity.update(
                {
                    "maxanswers": quota.get("maxanswers"),
                    "quota_enabled": quota.get("quota_enabled"),
                    "quota_consistent": quota.get("quota_consistent"),
                    "selected_quota_count": quota.get("selected_quota_count", 0),
                    "quota_contract": quota.get("contract", ""),
                }
            )
            if quota.get("quota_readback_error"):
                activity["quota_readback_error"] = quota["quota_readback_error"]
            _save_evidence(page, report_dir, f"choicegroup_{index:02d}_settings.png", evidence)

    refs_by_group: dict[str, list[dict[str, str]]] = {}
    for activity in activities:
        ref = {
            "cmid": _text(activity.get("cmid")),
            "title": _text(activity.get("title")),
            "section": _text(activity.get("section")),
        }
        for group_name in activity.get("selected_group_names") or []:
            refs_by_group.setdefault(_text(group_name), []).append(ref)
    for group in groups:
        group["choicegroup_refs"] = refs_by_group.get(_text(group.get("name")), [])

    snapshot["groups"] = sorted(groups, key=lambda item: (_text(item.get("name")).casefold(), _text(item.get("id"))))
    snapshot["groupings"] = sorted(
        groupings, key=lambda item: (_text(item.get("name")).casefold(), _text(item.get("id")))
    )
    snapshot["choicegroup_activities"] = sorted(
        activities,
        key=lambda item: (_text(item.get("title")).casefold(), _text(item.get("cmid"))),
    )
    snapshot["captured_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    snapshot["evidence"] = evidence
    snapshot["snapshot_hash"] = group_structure_hash(snapshot)
    snapshot["action_preflight_hash"] = group_action_preflight_hash(snapshot)
    _restore_sandbox_course_page(page, evidence)
    return snapshot


def group_structure_preconditions(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "snapshot_hash": _text(snapshot.get("snapshot_hash") or group_structure_hash(snapshot)),
        "group_structure": _canonical_snapshot(snapshot),
    }


def group_structure_matches_preconditions(
    snapshot: dict[str, Any],
    preconditions: dict[str, Any],
) -> bool:
    expected_hash = _text(preconditions.get("snapshot_hash"))
    return bool(expected_hash) and expected_hash == group_structure_hash(snapshot)
