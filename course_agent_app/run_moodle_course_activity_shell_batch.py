from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from run_moodle_course_file_resource_upload import (
    DEFAULT_CHROME_PATH,
    SANDBOX_COURSE_ID,
    SANDBOX_HOST,
    click_save_and_wait,
    import_playwright,
    load_env,
    looks_like_login,
    page_summary,
    save_screenshot,
    select_hidden_visibility,
    set_intro,
    visible_text,
    wait_for_form_or_course,
    write_result,
)
from run_moodle_course_file_upload_batch import safe_name
from run_moodle_course_clear_file_resources import call_apim_contents
from moodle_readback_consistency import (
    TerminalReadbackError,
    wait_for_expected_readback,
)
from moodle_choicegroup_form import set_shared_group_quota


ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))
REPORTS = WORKSPACE / "course_files" / "evidence" / "process" / "course_agent_app" / "reports"
SANDBOX_COURSE_URL = f"https://{SANDBOX_HOST}/course/view.php?id={SANDBOX_COURSE_ID}"
ALLOWED_ACTIVITY_TYPES = {
    "assign",
    "forum",
    "choice",
    "folder",
    "page",
    "quiz",
    "choicegroup",
    "peerwork",
    "questionnaire",
    "qbank",
    "url",
}


try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def cmid_from_activity_url(url: object) -> str:
    parsed = urlparse(str(url or ""))
    if parsed.hostname != SANDBOX_HOST or not parsed.path.startswith("/mod/"):
        return ""
    values = parse_qs(parsed.query).get("id") or []
    return values[0] if values and str(values[0]).isdigit() else ""


def exact_activity_modules(
    contents: list[dict[str, Any]],
    item: dict[str, Any],
    *,
    observed_cmid: str = "",
) -> list[dict[str, Any]]:
    title = str(item.get("title") or item.get("name") or "").strip()
    modname = str(item.get("modname") or "").strip()
    raw_sectionnum = item.get("sectionnum")
    sectionnum = "" if raw_sectionnum is None else str(raw_sectionnum).strip()
    matches: list[dict[str, Any]] = []
    for section in contents:
        if not isinstance(section, dict):
            continue
        current_sectionnum = (
            ""
            if section.get("section") is None
            else str(section.get("section")).strip()
        )
        for module in section.get("modules") or []:
            if not isinstance(module, dict):
                continue
            cmid = str(module.get("id") or "").strip()
            if (
                str(module.get("modname") or "").strip() == modname
                and str(module.get("name") or "").strip() == title
                and (not sectionnum or current_sectionnum == sectionnum)
                and (not observed_cmid or cmid == observed_cmid)
            ):
                matches.append(
                    {
                        "sectionnum": section.get("section"),
                        "section_name": section.get("name"),
                        "cmid": module.get("id"),
                        "instance": module.get("instance"),
                        "modname": module.get("modname"),
                        "name": module.get("name"),
                        "visible": module.get("visible"),
                        "url": module.get("url"),
                    }
                )
    return matches


def wait_for_saved_activity_readback(
    env: dict[str, str],
    item: dict[str, Any],
    *,
    observed_cmid: str,
) -> dict[str, Any]:
    def read_unique() -> dict[str, Any]:
        contents = call_apim_contents(env)
        matches = exact_activity_modules(
            contents,
            item,
            observed_cmid=observed_cmid,
        )
        if len(matches) > 1:
            raise TerminalReadbackError(
                f"Saved activity readback is ambiguous: found {len(matches)} exact matches."
            )
        return {"matches": matches}

    observed = wait_for_expected_readback(
        read_unique,
        lambda current: len(current.get("matches") or []) == 1,
        env,
        description=f"saved activity {str(item.get('title') or item.get('name') or '')!r}",
    )
    return dict(observed["matches"][0])


def current_activity_preflight(
    env: dict[str, str],
    item: dict[str, Any],
) -> list[dict[str, Any]]:
    contents = wait_for_expected_readback(
        lambda: call_apim_contents(env),
        lambda _current: True,
        env,
        description="current activity inventory before create",
    )
    matches = exact_activity_modules(contents, item)
    if len(matches) > 1:
        raise TerminalReadbackError(
            f"Activity create preflight is ambiguous: found {len(matches)} exact matches."
        )
    return matches


def load_manifest(path_text: str) -> tuple[Path, dict[str, Any]]:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        candidates = [Path.cwd() / path, ROOT / path, WORKSPACE / path]
        path = next((candidate for candidate in candidates if candidate.exists()), WORKSPACE / path)
    return path, json.loads(path.read_text(encoding="utf-8"))


def assert_manifest(manifest: dict[str, Any], approval_phrase: str | None) -> None:
    target = manifest.get("target") or {}
    if target.get("course_url") != SANDBOX_COURSE_URL or str(target.get("course_id")) != SANDBOX_COURSE_ID:
        raise ValueError("Refusing non-sandbox activity shell manifest.")
    if approval_phrase is not None and approval_phrase != manifest.get("approval_phrase"):
        raise ValueError("Approval phrase mismatch for activity shell batch.")
    for item in activity_items(manifest):
        modname = str(item.get("modname", "")).strip()
        if modname not in ALLOWED_ACTIVITY_TYPES:
            raise ValueError(f"Refusing unsupported activity shell type: {modname}")
        if str(item.get("visibility")) != "Hide on course page":
            raise ValueError("Activity shells and Page placeholders must be hidden.")
        if str(item.get("target_course_id")) != SANDBOX_COURSE_ID:
            raise ValueError("Refusing non-sandbox activity shell target.")
    for scaffold in group_signup_scaffolds(manifest):
        if str(scaffold.get("target_course_id")) != SANDBOX_COURSE_ID:
            raise ValueError("Refusing non-sandbox group signup scaffold target.")
        if str(scaffold.get("visibility")) != "Hide on course page":
            raise ValueError("Group signup scaffolds must remain hidden.")
        groups = list(scaffold.get("groups") or [])
        if len(groups) != 14:
            raise ValueError("Project Grouping scaffold must contain 14 sandbox groups.")
        if any(int(group.get("member_count") or 0) != 0 for group in groups):
            raise ValueError("Refusing group signup scaffold with copied members.")


def activity_items(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return (
        list(manifest.get("activity_shell_items") or [])
        + list(manifest.get("url_activity_items") or [])
        + list(manifest.get("page_placeholder_items") or [])
    )


def group_signup_scaffolds(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return list(manifest.get("group_signup_scaffold_items") or [])


def add_activity_url(item: dict[str, Any]) -> str:
    section = str(item.get("sectionnum") or "0")
    return (
        f"https://{SANDBOX_HOST}/course/modedit.php?"
        + urlencode(
            {
                "add": item["modname"],
                "type": "",
                "course": SANDBOX_COURSE_ID,
                "section": section,
                "return": "0",
                "sr": section,
            }
        )
    )


def set_page_content(page, html: str) -> None:
    if not html:
        return
    editable = page.locator("#id_pageeditoreditable")
    if editable.count():
        editable.evaluate(
            """
            (el, html) => {
              el.innerHTML = html;
              el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: ''}));
              el.dispatchEvent(new Event('change', {bubbles: true}));
            }
            """,
            html,
        )
    textarea = page.locator("textarea[name='page[text]']")
    if textarea.count():
        try:
            textarea.fill(html)
        except Exception:
            textarea.evaluate(
                """
                (el, html) => {
                  el.value = html;
                  el.dispatchEvent(new Event('input', {bubbles: true}));
                  el.dispatchEvent(new Event('change', {bubbles: true}));
                }
                """,
                html,
            )


def disable_assignment_dates(page) -> list[str]:
    changed: list[str] = []
    names = [
        "allowsubmissionsfromdate[enabled]",
        "duedate[enabled]",
        "cutoffdate[enabled]",
        "gradingduedate[enabled]",
    ]
    for name in names:
        locator = page.locator(f"input[name='{name}']").first
        if not locator.count():
            continue
        try:
            locator.evaluate(
                """
                (el) => {
                  if (el.checked) el.click();
                  el.checked = false;
                  el.dispatchEvent(new Event('change', {bubbles: true}));
                }
                """
            )
            changed.append(name)
        except Exception:
            continue
    return changed


def disable_common_date_fields(page) -> list[str]:
    changed: list[str] = []
    names = [
        "timeopen[enabled]",
        "timeclose[enabled]",
        "allowsubmissionsfromdate[enabled]",
        "duedate[enabled]",
        "cutoffdate[enabled]",
        "gradingduedate[enabled]",
        "completionexpected[enabled]",
    ]
    for name in names:
        locator = page.locator(f"input[name='{name}']").first
        if not locator.count():
            continue
        try:
            locator.evaluate(
                """
                (el) => {
                  if (el.checked) el.click();
                  el.checked = false;
                  el.dispatchEvent(new Event('change', {bubbles: true}));
                }
                """
            )
            changed.append(name)
        except Exception:
            continue
    return changed


def fill_first_available(page, selectors: list[str], value: str) -> str:
    for selector in selectors:
        locator = page.locator(selector).first
        if not locator.count():
            continue
        try:
            locator.fill(value)
            return selector
        except Exception:
            continue
    return ""


def set_checkbox_like(page, selectors: list[str], checked: bool) -> str:
    for selector in selectors:
        locator = page.locator(selector).first
        if not locator.count():
            continue
        try:
            locator.evaluate(
                """
                (el, checked) => {
                  if ('checked' in el) {
                    el.checked = checked;
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                  } else {
                    el.value = checked ? '1' : '0';
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                  }
                }
                """,
                checked,
            )
            return selector
        except Exception:
            continue
    return ""


def click_add_choice_fields(page) -> bool:
    selectors = [
        "input[name='option_add_fields']",
        "button[name='option_add_fields']",
        "input[type='submit'][value*='field']",
        "button:has-text('field(s) to form')",
        "input[type='submit'][value*='option']",
    ]
    for selector in selectors:
        locator = page.locator(selector).first
        if not locator.count():
            continue
        try:
            locator.click()
            try:
                page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            page.wait_for_timeout(500)
            return True
        except Exception:
            continue
    return False


def fill_choice_options(page, item: dict[str, Any]) -> dict[str, Any]:
    options = list(item.get("choice_options") or [])
    if not options:
        raise RuntimeError("Choice simulation item is missing backup option labels.")
    details: dict[str, Any] = {"options_expected": len(options), "options_filled": [], "limit_fields": []}
    limit_toggle = set_checkbox_like(page, ["#id_limitanswers", "input[name='limitanswers']", "select[name='limitanswers']"], True)
    if limit_toggle:
        details["limitanswers_selector"] = limit_toggle
    for index, option in enumerate(options):
        label = str(option.get("label") or "").strip()
        maxanswers = str(int(option.get("maxanswers") or 0))
        if not label:
            raise RuntimeError(f"Choice option {index + 1} is missing a safe label.")
        option_selectors = [
            f"input[name='option[{index}]']",
            f"textarea[name='option[{index}]']",
            f"#id_option_{index}",
            f"#id_optionhdr_{index} input[type='text']",
        ]
        option_selector = ""
        for _attempt in range(4):
            option_selector = fill_first_available(page, option_selectors, label)
            if option_selector:
                break
            if not click_add_choice_fields(page):
                break
        if not option_selector:
            raise RuntimeError(f"Choice form did not expose option field {index + 1}; blocked before save.")
        details["options_filled"].append({"index": index, "selector": option_selector, "label": label})
        if int(option.get("maxanswers") or 0) > 0:
            limit_selectors = [
                f"input[name='limit[{index}]']",
                f"input[name='maxanswers[{index}]']",
                f"#id_limit_{index}",
                f"#id_maxanswers_{index}",
                f"#id_optionhdr_{index} input[type='number']",
            ]
            limit_selector = ""
            for _attempt in range(4):
                limit_selector = fill_first_available(page, limit_selectors, maxanswers)
                if limit_selector:
                    break
                if not click_add_choice_fields(page):
                    break
            if not limit_selector:
                raise RuntimeError(f"Choice form did not expose limit field {index + 1}; blocked before save.")
            details["limit_fields"].append({"index": index, "selector": limit_selector, "maxanswers": maxanswers})
    return details


def fill_choicegroup_options(page, item: dict[str, Any]) -> dict[str, Any]:
    group_names = [str(value) for value in (item.get("choicegroup_target_group_names") or []) if str(value).strip()]
    if not group_names:
        raise RuntimeError("Choicegroup simulation item is missing run-scoped target group names.")
    details: dict[str, Any] = {"groups_expected": len(group_names), "groups_selected": []}
    selected_entries: list[dict[str, str]] = []
    available_groups = page.locator("#availablegroups").first
    selected_groups = page.locator("#id_selectedGroups").first
    if available_groups.count() and selected_groups.count():
        expand_button = page.locator("#expandButton").first
        if expand_button.count():
            expand_button.click()
            page.wait_for_timeout(500)
        matched = available_groups.evaluate(
            """
            (select, labels) => {
              const wanted = new Set(labels.map((label) => label.replace(/\\s+/g, ' ').trim()));
              const matchedLabels = [];
              for (const option of Array.from(select.options || [])) {
                const text = (option.textContent || '').replace(/\\s+/g, ' ').trim();
                const classes = String(option.className || '').split(/\\s+/);
                const isGroup = classes.includes('group');
                option.selected = isGroup && wanted.has(text);
                if (option.selected) matchedLabels.push(text);
              }
              select.dispatchEvent(new Event('change', {bubbles: true}));
              return matchedLabels;
            }
            """,
            group_names,
        )
        if len(matched) != len(group_names):
            raise RuntimeError("Choicegroup form did not expose all run-scoped groups after expanding groupings.")
        page.locator("#addGroupButton").click()
        page.wait_for_timeout(500)
        selected_entries = selected_groups.evaluate(
            """
            (select) => Array.from(select.options || []).map((option) => ({
              value: String(option.value || '').trim(),
              text: (option.textContent || '').replace(/\\s+/g, ' ').trim()
            }))
            """
        )
        selected = [str(entry.get("text") or "") for entry in selected_entries]
        if set(selected) != set(group_names):
            raise RuntimeError("Choicegroup form did not move all run-scoped groups to Selected Groups.")
        details["selector"] = "#availablegroups -> #id_selectedGroups"
        details["groups_selected"] = selected
    else:
        select_selectors = ["select[name='groups[]']", "select[name='groupids[]']", "select[name='option[]']", "select[multiple]"]
        for selector in select_selectors:
            locator = page.locator(selector).first
            if not locator.count():
                continue
            selected = locator.evaluate(
                """
                (select, labels) => {
                  const wanted = new Set(labels);
                  const selectedLabels = [];
                  for (const option of Array.from(select.options || [])) {
                    const text = (option.textContent || '').trim();
                    if (wanted.has(text)) {
                      option.selected = true;
                      selectedLabels.push(text);
                    }
                  }
                  select.dispatchEvent(new Event('change', {bubbles: true}));
                  return selectedLabels;
                }
                """,
                group_names,
            )
            if len(selected) == len(group_names):
                details["selector"] = selector
                details["groups_selected"] = selected
                selected_entries = locator.evaluate(
                    """
                    (select) => Array.from(select.options || [])
                      .filter((option) => option.selected)
                      .map((option) => ({
                        value: String(option.value || '').trim(),
                        text: (option.textContent || '').replace(/\\s+/g, ' ').trim()
                      }))
                    """
                )
                break
        if len(details["groups_selected"]) != len(group_names):
            raise RuntimeError("Choicegroup form did not expose/select all run-scoped groups; blocked before save.")
    if item.get("maxanswers") in (None, ""):
        details["maxanswers_skipped"] = "activity item has no explicit maxanswers; leaving Moodle default limit setting unchanged"
        return details
    quota_details = set_shared_group_quota(
        page,
        item.get("maxanswers"),
        selected_entries,
    )
    details["quota"] = quota_details
    details["maxanswers"] = quota_details.get("maxanswers")
    return details


def fill_url_external(page, externalurl: str) -> dict[str, Any]:
    if not externalurl:
        raise RuntimeError("URL activity item is missing externalurl.")
    selectors = ["#id_externalurl", "input[name='externalurl']"]
    for selector in selectors:
        locator = page.locator(selector).first
        if not locator.count():
            continue
        locator.fill(externalurl)
        return {"externalurl_set": True, "selector": selector}
    raise RuntimeError("Moodle URL form did not expose externalurl.")


def fill_common_fields(page, item: dict[str, Any]) -> dict[str, Any]:
    name = str(item.get("title") or item.get("name") or "").strip()
    if not name:
        raise RuntimeError("Activity shell is missing title.")
    if page.locator("#id_name").count() != 1:
        raise RuntimeError("Moodle activity form did not expose #id_name.")
    page.locator("#id_name").fill(name)
    set_intro(page, str(item.get("intro") or ""))
    visibility = str(item.get("visibility") or item.get("expected_visibility") or "Hide on course page")
    select_hidden_visibility(page, visibility)
    details: dict[str, Any] = {"title": name, "visibility": visibility}
    modname = str(item.get("modname", ""))
    if modname == "page":
        set_page_content(page, str(item.get("page_content") or item.get("intro") or ""))
        details["page_content_set"] = True
    elif modname == "url":
        details.update(fill_url_external(page, str(item.get("externalurl") or "")))
    elif modname == "assign":
        details["disabled_assignment_dates"] = disable_assignment_dates(page)
    elif modname == "quiz":
        details["disabled_quiz_dates"] = disable_common_date_fields(page)
    elif modname == "choice":
        details["choice_options"] = fill_choice_options(page, item)
        details["disabled_choice_dates"] = disable_common_date_fields(page)
    elif modname == "choicegroup":
        details["choicegroup_options"] = fill_choicegroup_options(page, item)
        details["disabled_choicegroup_dates"] = disable_common_date_fields(page)
    elif modname in {"peerwork", "questionnaire", "qbank", "folder", "forum"}:
        details["disabled_common_dates"] = disable_common_date_fields(page)
    return details


def execute_one_in_context(
    page,
    timeout_error_type,
    env: dict[str, str],
    item: dict[str, Any],
    manifest: dict[str, Any],
    item_report_dir: Path,
) -> dict[str, Any]:
    item_report_dir.mkdir(parents=True, exist_ok=True)
    title = str(item.get("title") or item.get("name") or "")
    add_url = add_activity_url(item)
    auto_click_hku = env.get("MOODLE_AUTO_CLICK_HKU_LOGIN", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    wait_seconds = float(env.get("MOODLE_BROWSER_WAIT_SECONDS", "900"))
    result: dict[str, Any] = {
        "ok": False,
        "status": "STARTED",
        "mode": "execute_approved_change",
        "batch_id": manifest.get("batch_id"),
        "approval_run_id": manifest.get("run_id"),
        "title": title,
        "modname": item.get("modname"),
        "sectionnum": str(item.get("sectionnum")),
        "visibility": item.get("visibility"),
        "saves_count": 0,
        "write_action_taken": False,
    }
    try:
        page.goto(SANDBOX_COURSE_URL, wait_until="domcontentloaded", timeout=60000)
        wait_for_form_or_course(page, wait_seconds, SANDBOX_COURSE_URL, auto_click_hku=auto_click_hku)
        save_screenshot(page, item_report_dir, "01_course_or_login_after_wait.png")
        write_result(item_report_dir, "01_course_or_login_after_wait.json", page_summary(page))
        if looks_like_login(page):
            raise RuntimeError("Moodle is still on a login page after waiting.")
        preflight_matches = current_activity_preflight(env, item)
        write_result(
            item_report_dir,
            "01_apim_activity_preflight.json",
            {
                "exact_match_count": len(preflight_matches),
                "matches": preflight_matches,
            },
        )
        if len(preflight_matches) == 1:
            existing = preflight_matches[0]
            result.update(
                {
                    "ok": True,
                    "status": "ALREADY_PRESENT",
                    "write_action_taken": False,
                    "target_module_id": str(existing.get("cmid") or ""),
                    "module_readback": existing,
                    "note": (
                        "An exact activity already exists in structured Moodle "
                        "readback; no duplicate was created."
                    ),
                }
            )
            write_result(item_report_dir, "result.json", result)
            return result
        page.goto(add_url, wait_until="domcontentloaded", timeout=60000)
        wait_for_form_or_course(page, wait_seconds, add_url, auto_click_hku=auto_click_hku)
        save_screenshot(page, item_report_dir, "02_add_activity_form_before_fill.png")
        write_result(item_report_dir, "02_add_activity_form_before_fill.json", page_summary(page))
        fill_details = fill_common_fields(page, item)
        save_screenshot(page, item_report_dir, "03_after_fill_before_save.png")
        write_result(item_report_dir, "03_after_fill_before_save.json", {"page": page_summary(page), "fill": fill_details})
        click_save_and_wait(page, timeout_error_type)
        result["saves_count"] = 1
        result["write_action_taken"] = True
        save_screenshot(page, item_report_dir, "04_after_save.png")
        summary = page_summary(page)
        write_result(item_report_dir, "04_after_save.json", summary)
        observed_cmid = cmid_from_activity_url(summary.get("url"))
        page.goto(SANDBOX_COURSE_URL, wait_until="domcontentloaded", timeout=60000)
        wait_for_form_or_course(page, 30, SANDBOX_COURSE_URL, auto_click_hku=auto_click_hku)
        save_screenshot(page, item_report_dir, "05_verify_course_after_save.png")
        course_summary = page_summary(page)
        write_result(item_report_dir, "05_verify_course_after_save.json", course_summary)
        course_text = visible_text(page, 30000)
        structured_readback = wait_for_saved_activity_readback(
            env,
            item,
            observed_cmid=observed_cmid,
        )
        write_result(
            item_report_dir,
            "06_apim_activity_readback.json",
            structured_readback,
        )
        result.update(
            {
                "ok": True,
                "status": "PASS",
                "display_url": summary.get("url", ""),
                "course_verify_url": course_summary.get("url", ""),
                "title_visible_after_save": title in course_text,
                "structured_readback_verified": True,
                "target_module_id": str(structured_readback.get("cmid") or ""),
                "module_readback": structured_readback,
                "fill_details": fill_details,
            }
        )
    except Exception as exc:
        result.update({"ok": False, "status": "BLOCKED", "blocker": str(exc), "current_url": page.url})
        try:
            save_screenshot(page, item_report_dir, "error_state.png")
            result["error_screenshot"] = str(item_report_dir / "error_state.png")
        except Exception:
            pass
    write_result(item_report_dir, "result.json", result)
    return result


def recursive_id_for_name(payload: Any, name: str) -> str:
    if isinstance(payload, dict):
        candidate_name = str(payload.get("name") or payload.get("group_name") or payload.get("grouping_name") or "")
        candidate_id = payload.get("id") or payload.get("groupid") or payload.get("groupingid")
        if candidate_name == name and candidate_id:
            return str(candidate_id)
        for value in payload.values():
            found = recursive_id_for_name(value, name)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = recursive_id_for_name(value, name)
            if found:
                return found
    return ""


def read_existing_api_named_items(env: dict[str, str], wsfunction: str, name_field: str) -> dict[str, str]:
    from moodle_operations.api_client import call_moodle_api

    payload = call_moodle_api(wsfunction, {"courseid": SANDBOX_COURSE_ID}, env=env)
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("groups") or payload.get("groupings") or payload.get("items") or []
    else:
        items = []
    by_name: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get(name_field) or item.get("name") or "")
        item_id = str(item.get("id") or item.get("groupid") or item.get("groupingid") or "")
        if name and item_id:
            by_name[name] = item_id
    return by_name


def execute_group_signup_scaffolds(manifest: dict[str, Any], report_dir: Path) -> list[dict[str, Any]]:
    scaffolds = group_signup_scaffolds(manifest)
    if not scaffolds:
        return []
    from moodle_operations.api_client import execute_api_operation

    env = load_env()
    env.update(os.environ)
    results: list[dict[str, Any]] = []
    groups_dir = report_dir / "group_signup_scaffolds"
    groups_dir.mkdir(parents=True, exist_ok=True)
    existing_groups = read_existing_api_named_items(env, "core_group_get_course_groups", "name")
    existing_groupings = read_existing_api_named_items(env, "core_group_get_course_groupings", "name")
    for scaffold_index, scaffold in enumerate(scaffolds, start=1):
        scaffold_dir = groups_dir / f"{scaffold_index:02d}_{safe_name(str(scaffold.get('title') or 'group-signup'))}"
        scaffold_dir.mkdir(parents=True, exist_ok=True)
        scaffold_result: dict[str, Any] = {
            "ok": True,
            "status": "PASS",
            "title": scaffold.get("title"),
            "target_course_id": SANDBOX_COURSE_ID,
            "group_results": [],
            "grouping_result": {},
            "assignment_results": [],
        }
        created_group_ids: dict[str, str] = {}
        for group_index, group in enumerate(scaffold.get("groups") or [], start=1):
            name = str(group.get("target_group_name") or "").strip()
            if not name:
                raise RuntimeError("Group scaffold is missing target_group_name.")
            if name in existing_groups:
                group_result = {
                    "ok": True,
                    "status": "ALREADY_PRESENT",
                    "action": "create_group",
                    "group_name": name,
                    "group_id": existing_groups[name],
                }
            else:
                operation = {
                    "operation_id": f"{manifest.get('run_id')}:group:{group_index:02d}",
                    "action": "create_group",
                    "target_course_id": SANDBOX_COURSE_ID,
                    "group_name": name,
                    "description": "Empty sandbox project group for hidden DASE7137 activity simulation.",
                    "descriptionformat": 1,
                }
                group_result = execute_api_operation(operation, env)
                if not group_result.get("ok"):
                    scaffold_result["ok"] = False
                    scaffold_result["status"] = "BLOCKED"
                    scaffold_result["group_results"].append(group_result)
                    break
                group_id = recursive_id_for_name(group_result.get("payload"), name)
                if not group_id:
                    raise RuntimeError(f"Could not read created group id for {name}.")
                existing_groups[name] = group_id
                group_result["group_id"] = group_id
            created_group_ids[name] = str(group_result.get("group_id") or existing_groups.get(name) or "")
            scaffold_result["group_results"].append(group_result)
        if not scaffold_result["ok"]:
            results.append(scaffold_result)
            write_result(scaffold_dir, "result.json", scaffold_result)
            break

        grouping_name = str(scaffold.get("grouping_name") or "").strip()
        grouping_id = existing_groupings.get(grouping_name, "")
        if grouping_id:
            grouping_result = {
                "ok": True,
                "status": "ALREADY_PRESENT",
                "action": "create_grouping",
                "grouping_name": grouping_name,
                "grouping_id": grouping_id,
            }
        else:
            operation = {
                "operation_id": f"{manifest.get('run_id')}:grouping",
                "action": "create_grouping",
                "target_course_id": SANDBOX_COURSE_ID,
                "grouping_name": grouping_name,
                "idnumber": scaffold.get("grouping_idnumber"),
                "description": "Empty sandbox grouping for hidden DASE7137 activity simulation.",
                "descriptionformat": 1,
            }
            grouping_result = execute_api_operation(operation, env)
            if not grouping_result.get("ok"):
                scaffold_result["ok"] = False
                scaffold_result["status"] = "BLOCKED"
                scaffold_result["grouping_result"] = grouping_result
                results.append(scaffold_result)
                write_result(scaffold_dir, "result.json", scaffold_result)
                break
            grouping_id = recursive_id_for_name(grouping_result.get("payload"), grouping_name)
            if not grouping_id:
                raise RuntimeError(f"Could not read created grouping id for {grouping_name}.")
            existing_groupings[grouping_name] = grouping_id
            grouping_result["grouping_id"] = grouping_id
        scaffold_result["grouping_result"] = grouping_result

        for group_name, group_id in created_group_ids.items():
            operation = {
                "operation_id": f"{manifest.get('run_id')}:assign:{safe_name(group_name)}",
                "action": "assign_group_to_grouping",
                "target_course_id": SANDBOX_COURSE_ID,
                "group_id": group_id,
                "grouping_id": grouping_id,
            }
            assign_result = execute_api_operation(operation, env)
            if not assign_result.get("ok") and "already" in str(assign_result.get("error") or assign_result.get("reason") or "").casefold():
                assign_result = {**assign_result, "ok": True, "status": "ALREADY_PRESENT"}
            if not assign_result.get("ok"):
                scaffold_result["ok"] = False
                scaffold_result["status"] = "BLOCKED"
            scaffold_result["assignment_results"].append(assign_result)
        results.append(scaffold_result)
        write_result(scaffold_dir, "result.json", scaffold_result)
        if not scaffold_result["ok"]:
            break
    write_result(groups_dir, "result.json", {"results": results, "ok": all(item.get("ok") for item in results)})
    return results


def module_key(sectionnum: Any, modname: Any, title: Any) -> tuple[str, str, str]:
    normalized_title = re.sub(r"\s+", " ", str(title or "")).strip().casefold()
    return str(sectionnum), str(modname or ""), normalized_title


def item_module_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return module_key(item.get("sectionnum"), item.get("modname"), item.get("title") or item.get("name"))


def read_existing_module_keys(env: dict[str, str]) -> set[tuple[str, str, str]]:
    from moodle_operations.api_client import call_moodle_api

    payload = call_moodle_api("core_course_get_contents", {"courseid": SANDBOX_COURSE_ID}, env=env, timeout=60)
    if not isinstance(payload, list):
        raise RuntimeError("core_course_get_contents returned an unexpected shape before activity resume.")
    keys: set[tuple[str, str, str]] = set()
    for section in payload:
        sectionnum = str(section.get("section") if section.get("section") is not None else section.get("sectionnum") or "")
        for module in section.get("modules") or []:
            keys.add(module_key(sectionnum, module.get("modname"), module.get("name")))
    return keys


def execute_batch(manifest: dict[str, Any], report_dir: Path) -> list[dict[str, Any]]:
    env = load_env()
    env.update(os.environ)
    env.setdefault("MOODLE_AUTO_CLICK_HKU_LOGIN", "0")
    env.setdefault("MOODLE_BROWSER_WAIT_SECONDS", "900")
    existing_keys = read_existing_module_keys(env)
    sync_playwright, PlaywrightTimeoutError = import_playwright()
    items = activity_items(manifest)
    results: list[dict[str, Any]] = []
    items_dir = report_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        profile_dir = Path(env["MOODLE_BROWSER_PROFILE_DIR"]).expanduser()
        if not profile_dir.is_absolute():
            profile_dir = ROOT / profile_dir
        profile_dir.mkdir(parents=True, exist_ok=True)
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(profile_dir),
            "headless": False,
            "viewport": {"width": 1400, "height": 900},
        }
        chrome_path = env.get("MOODLE_BROWSER_CHROME_PATH", DEFAULT_CHROME_PATH)
        if chrome_path and Path(chrome_path).exists():
            launch_kwargs["executable_path"] = chrome_path
        context = playwright.chromium.launch_persistent_context(**launch_kwargs)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            for index, item in enumerate(items, start=1):
                item_report_dir = items_dir / f"{index:02d}_{safe_name(str(item.get('title') or item.get('name') or 'activity'))}"
                print(f"\nActivity item {index}/{len(items)}: {item.get('modname')} {item.get('title')}")
                key = item_module_key(item)
                if key in existing_keys:
                    item_result = {
                        "ok": True,
                        "status": "ALREADY_PRESENT_READBACK",
                        "mode": "execute_approved_change",
                        "batch_id": manifest.get("batch_id"),
                        "approval_run_id": manifest.get("run_id"),
                        "title": item.get("title") or item.get("name"),
                        "modname": item.get("modname"),
                        "sectionnum": str(item.get("sectionnum")),
                        "visibility": item.get("visibility"),
                        "saves_count": 0,
                        "write_action_taken": False,
                        "note": "Activity title/modname/section already appears in APIM course readback; no duplicate was created.",
                    }
                    item_report_dir.mkdir(parents=True, exist_ok=True)
                    write_result(item_report_dir, "result.json", item_result)
                    results.append(item_result)
                    print(json.dumps(item_result, indent=2, ensure_ascii=False))
                    continue
                item_result = execute_one_in_context(page, PlaywrightTimeoutError, env, item, manifest, item_report_dir)
                results.append(item_result)
                if item_result.get("ok"):
                    existing_keys.add(key)
                print(json.dumps(item_result, indent=2, ensure_ascii=False))
                if not item_result.get("ok"):
                    break
        finally:
            try:
                context.close()
            except Exception:
                pass
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Create hidden Moodle activity shells/Page placeholders in sandbox 105330.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--execute-approved-change", action="store_true")
    parser.add_argument("--approval-phrase", default="")
    args = parser.parse_args()

    manifest_path, manifest = load_manifest(args.manifest)
    approval_phrase = args.approval_phrase if args.execute_approved_change else None
    assert_manifest(manifest, approval_phrase)

    REPORTS.mkdir(parents=True, exist_ok=True)
    report_dir = REPORTS / f"moodle_course_activity_shell_batch_{now_stamp()}_{safe_name(manifest['batch_id'])}"
    report_dir.mkdir(parents=True, exist_ok=True)
    items = activity_items(manifest)
    scaffolds = group_signup_scaffolds(manifest)
    result: dict[str, Any] = {
        "ok": True,
        "status": "OPERATIONS_WRITTEN",
        "manifest_path": str(manifest_path),
        "report_dir": str(report_dir),
        "operation_count": len(items),
        "group_signup_scaffold_count": len(scaffolds),
        "group_signup_group_count": sum(len(scaffold.get("groups") or []) for scaffold in scaffolds),
        "approval_phrase": manifest["approval_phrase"],
    }
    (report_dir / "activity_shell_operations.json").write_text(
        json.dumps(items, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (report_dir / "group_signup_scaffold_operations.json").write_text(
        json.dumps(scaffolds, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if args.execute_approved_change:
        scaffold_results = execute_group_signup_scaffolds(manifest, report_dir)
        if scaffolds and (not scaffold_results or not all(item.get("ok") for item in scaffold_results)):
            item_results: list[dict[str, Any]] = []
        else:
            item_results = execute_batch(manifest, report_dir)
        result["ok"] = (
            (not scaffolds or bool(scaffold_results))
            and all(item.get("ok") for item in scaffold_results)
            and bool(item_results)
            and all(item.get("ok") for item in item_results)
            and len(item_results) == len(items)
        )
        result["status"] = "PASS" if result["ok"] else "BLOCKED"
        result["saves_count"] = sum(int(item.get("saves_count", 0)) for item in item_results)
        result["group_signup_results"] = scaffold_results
        result["item_results"] = item_results
    result_path = report_dir / "result.json"
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
