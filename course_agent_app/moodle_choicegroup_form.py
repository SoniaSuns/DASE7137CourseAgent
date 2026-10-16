from __future__ import annotations

from typing import Any


CURRENT_LIMIT_TOGGLE_SELECTORS = (
    "#id_limitanswers",
    "select[name='limitanswers']",
)
CURRENT_GENERAL_LIMIT_SELECTORS = (
    "#id_generallimitation",
    "input[name='generallimitation']",
)
CURRENT_APPLY_LIMIT_SELECTORS = (
    "#id_setlimit",
    "button[name='setlimit']",
)
LEGACY_SHARED_LIMIT_SELECTORS = (
    "input[name='maxanswers']",
    "#id_maxanswers",
    "input[name='limit']",
    "#id_limit",
)


class ChoicegroupQuotaBlocked(RuntimeError):
    pass


def _first_locator(page: Any, selectors: tuple[str, ...]) -> tuple[str, Any | None]:
    for selector in selectors:
        locator = page.locator(selector).first
        if locator.count():
            return selector, locator
    return "", None


def _field_value(page: Any, selectors: tuple[str, ...]) -> str:
    _selector, locator = _first_locator(page, selectors)
    if locator is None:
        return ""
    try:
        return str(locator.input_value() or "").strip()
    except Exception:
        try:
            return str(locator.get_attribute("value") or "").strip()
        except Exception:
            return ""


def _positive_integer(value: Any) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _selected_group_ids(selected_entries: list[dict[str, str]]) -> list[str]:
    return sorted(
        {
            str(entry.get("value") or "").strip()
            for entry in selected_entries
            if str(entry.get("value") or "").strip().isdigit()
            and int(str(entry.get("value") or "").strip()) > 0
        },
        key=int,
    )


def _group_limit_value(page: Any, group_id: str) -> str:
    return _field_value(
        page,
        (
            f"#group_{group_id}_limit",
            f"input[name='group_{group_id}_limit']",
        ),
    )


def read_shared_group_quota(
    page: Any,
    selected_entries: list[dict[str, str]],
) -> dict[str, Any]:
    toggle_selector, _toggle = _first_locator(page, CURRENT_LIMIT_TOGGLE_SELECTORS)
    if toggle_selector:
        toggle_value = _field_value(page, CURRENT_LIMIT_TOGGLE_SELECTORS).casefold()
        enabled = toggle_value in {"1", "true", "yes", "on", "enable", "enabled"}
        result: dict[str, Any] = {
            "contract": "limitanswers_general_per_group",
            "quota_enabled": enabled,
            "maxanswers": None,
            "quota_consistent": True,
            "selected_quota_count": 0,
        }
        if not enabled:
            return result
        selected_ids = _selected_group_ids(selected_entries)
        if len(selected_ids) != len(selected_entries):
            result["quota_consistent"] = False
            result["quota_readback_error"] = (
                "Selected Choicegroup options did not expose unique numeric Group ids."
            )
            return result
        values = [_positive_integer(_group_limit_value(page, group_id)) for group_id in selected_ids]
        result["selected_quota_count"] = sum(value is not None for value in values)
        general_value = _positive_integer(_field_value(page, CURRENT_GENERAL_LIMIT_SELECTORS))
        if not values or any(value is None for value in values):
            result["quota_consistent"] = False
            result["quota_readback_error"] = (
                "At least one selected Choicegroup option has no positive quota."
            )
            return result
        distinct = {int(value) for value in values if value is not None}
        if len(distinct) != 1:
            result["quota_consistent"] = False
            result["quota_readback_error"] = (
                "Selected Choicegroup options expose inconsistent quotas."
            )
            return result
        shared_value = next(iter(distinct))
        if general_value is not None and general_value != shared_value:
            result["quota_consistent"] = False
            result["quota_readback_error"] = (
                "The Choicegroup general quota disagrees with selected Group quotas."
            )
            return result
        result["maxanswers"] = shared_value
        return result

    legacy_selector, _legacy = _first_locator(page, LEGACY_SHARED_LIMIT_SELECTORS)
    if legacy_selector:
        value = _positive_integer(_field_value(page, LEGACY_SHARED_LIMIT_SELECTORS))
        return {
            "contract": "legacy_shared_limit",
            "quota_enabled": value is not None,
            "maxanswers": value,
            "quota_consistent": value is not None,
            "selected_quota_count": len(selected_entries) if value is not None else 0,
        }
    return {
        "contract": "unavailable",
        "quota_enabled": False,
        "maxanswers": None,
        "quota_consistent": False,
        "selected_quota_count": 0,
        "quota_readback_error": "Choicegroup quota controls are unavailable.",
    }


def set_shared_group_quota(
    page: Any,
    quota: Any,
    selected_entries: list[dict[str, str]],
) -> dict[str, Any]:
    expected = _positive_integer(quota)
    if expected is None:
        raise ChoicegroupQuotaBlocked("Choicegroup quota must be a positive integer.")
    selected_ids = _selected_group_ids(selected_entries)
    if len(selected_ids) != len(selected_entries) or not selected_ids:
        raise ChoicegroupQuotaBlocked(
            "Selected Choicegroup options did not expose unique numeric Group ids."
        )

    toggle_selector, toggle = _first_locator(page, CURRENT_LIMIT_TOGGLE_SELECTORS)
    if toggle is not None:
        try:
            toggle.select_option("1")
        except Exception:
            toggle.evaluate(
                """
                (el) => {
                  el.value = '1';
                  el.dispatchEvent(new Event('input', {bubbles: true}));
                  el.dispatchEvent(new Event('change', {bubbles: true}));
                }
                """
            )
        page.wait_for_timeout(250)
        input_selector, general_input = _first_locator(page, CURRENT_GENERAL_LIMIT_SELECTORS)
        apply_selector, apply_button = _first_locator(page, CURRENT_APPLY_LIMIT_SELECTORS)
        if general_input is None or apply_button is None:
            raise ChoicegroupQuotaBlocked(
                "Choicegroup quota controls are incomplete after limits were enabled."
            )
        try:
            general_input.fill(str(expected))
            apply_button.click()
        except Exception as exc:
            raise ChoicegroupQuotaBlocked(
                "Choicegroup quota controls could not apply the approved shared quota."
            ) from exc
        page.wait_for_timeout(250)
        readback = read_shared_group_quota(page, selected_entries)
        if (
            readback.get("maxanswers") != expected
            or not readback.get("quota_consistent")
            or int(readback.get("selected_quota_count") or 0) != len(selected_ids)
        ):
            raise ChoicegroupQuotaBlocked(
                str(readback.get("quota_readback_error") or "")
                or "Choicegroup quota did not read back across every selected Group."
            )
        return {
            **readback,
            "limit_toggle_selector": toggle_selector,
            "general_limit_selector": input_selector,
            "apply_limit_selector": apply_selector,
        }

    legacy_selector, legacy_input = _first_locator(page, LEGACY_SHARED_LIMIT_SELECTORS)
    if legacy_input is not None:
        try:
            legacy_input.fill(str(expected))
        except Exception as exc:
            raise ChoicegroupQuotaBlocked(
                "Legacy Choicegroup quota control could not apply the approved quota."
            ) from exc
        readback = read_shared_group_quota(page, selected_entries)
        if readback.get("maxanswers") != expected:
            raise ChoicegroupQuotaBlocked(
                "Legacy Choicegroup quota did not read back after it was set."
            )
        return {**readback, "shared_limit_selector": legacy_selector}

    raise ChoicegroupQuotaBlocked(
        "Choicegroup form did not expose a supported shared quota contract."
    )
