from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from course_agent_app.moodle_readback_consistency import (
    TerminalReadbackError,
    wait_for_expected_readback,
)


DEFAULT_API_URL = "https://apidev.hku.hk/dase7137agent/"
DEFAULT_COURSE_ID = "105330"
DEFAULT_COURSE_URL = "https://migrationtest-moodle.hku.hk/course/view.php?id=105330"
SUBSCRIPTION_KEY_ENV = "DASE7137_KEY"
DEFAULT_READ_ATTEMPTS = 3
DEFAULT_READ_RETRY_SECONDS = 1.0


class MoodleAPIError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def env_value(env: dict[str, str], key: str, default: str = "") -> str:
    return str(env.get(key) or os.environ.get(key) or default).strip()


def moodle_error(payload: Any) -> str:
    if isinstance(payload, dict) and ("exception" in payload or "errorcode" in payload):
        return str(payload.get("message") or payload.get("errorcode") or payload.get("exception"))
    return ""


def call_moodle_api(
    wsfunction: str,
    params: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
    timeout: int = 30,
) -> Any:
    env = env or {}
    key = env_value(env, SUBSCRIPTION_KEY_ENV)
    if not key:
        raise MoodleAPIError(
            f"Missing {SUBSCRIPTION_KEY_ENV}; export it in the shell or set it in the runtime environment."
        )

    api_url = env_value(env, "MOODLE_API_URL", DEFAULT_API_URL)
    body_params = {"wsfunction": wsfunction, **{key: str(value) for key, value in params.items()}}
    request = urllib.request.Request(
        api_url,
        data=urllib.parse.urlencode(body_params).encode("utf-8"),
        method="POST",
        headers={
            "DASE7137-Agent-Subscription-Key": key,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except Exception as exc:
        raise MoodleAPIError(f"Request failed for {wsfunction}: {type(exc).__name__}: {exc}") from exc

    try:
        payload = json.loads(body) if body else None
    except json.JSONDecodeError:
        payload = body

    error = moodle_error(payload)
    if not 200 <= status < 300 or error:
        raise MoodleAPIError(
            error or f"Moodle API HTTP {status} for {wsfunction}",
            status=status,
            payload=payload,
        )
    return payload


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _nonnegative_float(value: Any, default: float) -> float:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _is_transient_read_error(exc: MoodleAPIError) -> bool:
    return exc.status is None or exc.status in {408, 425, 429} or (
        exc.status is not None and exc.status >= 500
    )


def call_moodle_read_api(
    wsfunction: str,
    params: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
    timeout: int = 30,
) -> Any:
    """Retry transport/server failures only for a caller-designated read operation."""

    env = env or {}
    attempts = _positive_int(
        env.get("COURSE_AGENT_API_READ_ATTEMPTS"),
        DEFAULT_READ_ATTEMPTS,
    )
    delay_seconds = _nonnegative_float(
        env.get("COURSE_AGENT_API_READ_RETRY_SECONDS"),
        DEFAULT_READ_RETRY_SECONDS,
    )
    last_error: MoodleAPIError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return call_moodle_api(
                wsfunction,
                params,
                env=env,
                timeout=timeout,
            )
        except MoodleAPIError as exc:
            last_error = exc
            if not _is_transient_read_error(exc) or attempt >= attempts:
                raise
            if delay_seconds:
                time.sleep(delay_seconds)
    if last_error is not None:
        raise last_error
    raise MoodleAPIError(f"Read operation {wsfunction} did not return a result.")


def operation_course_id(operation: dict[str, Any], env: dict[str, str]) -> str:
    return str(
        operation.get("target_course_id")
        or operation.get("courseid")
        or operation.get("course_id")
        or env_value(env, "MOODLE_COURSE_ID", DEFAULT_COURSE_ID)
    )


def course_forums(env: dict[str, str], course_id: str) -> list[dict[str, Any]]:
    forums = call_moodle_read_api(
        "mod_forum_get_forums_by_courses",
        {"courseids[0]": course_id},
        env=env,
    )
    forum_list = forums if isinstance(forums, list) else forums.get("forums", []) if isinstance(forums, dict) else []
    return [dict(forum) for forum in forum_list if isinstance(forum, dict)]


def _unique_forum_match(
    matches: list[dict[str, Any]],
    *,
    description: str,
    course_id: str,
) -> dict[str, Any]:
    if not matches:
        raise MoodleAPIError(f"No {description} exists in current course {course_id}.")
    if len(matches) > 1:
        raise MoodleAPIError(
            f"Current course {course_id} has {len(matches)} matches for {description}; target is ambiguous."
        )
    return matches[0]


def discover_announcement_forum(env: dict[str, str], course_id: str) -> dict[str, Any]:
    matches = [
        forum
        for forum in course_forums(env, course_id)
        if str(forum.get("type", "")).casefold() == "news"
    ]
    return _unique_forum_match(
        matches,
        description="Moodle announcement/news forum",
        course_id=course_id,
    )


def discover_announcement_forum_id(env: dict[str, str], course_id: str) -> str:
    return str(discover_announcement_forum(env, course_id).get("id") or "")


def blocked_result(operation: dict[str, Any], reason: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": "moodle_api_action_blocked",
        "ok": False,
        "moodle_execution_mode": "api_action",
        "operation_id": operation.get("operation_id", ""),
        "action": operation.get("action", ""),
        "target_course_id": operation.get("target_course_id", DEFAULT_COURSE_ID),
        "reason": reason,
        "details": details or {},
        "completed_at": now_utc(),
    }


def verified_result(
    operation: dict[str, Any],
    *,
    wsfunction: str,
    payload: Any,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "moodle_api_action_verified",
        "ok": True,
        "moodle_execution_mode": "api_action",
        "operation_id": operation.get("operation_id", ""),
        "action": operation.get("action", ""),
        "target_course_id": operation.get("target_course_id", DEFAULT_COURSE_ID),
        "wsfunction": wsfunction,
        "payload": payload,
        "details": details or {},
        "completed_at": now_utc(),
    }


def failed_result(operation: dict[str, Any], exc: MoodleAPIError) -> dict[str, Any]:
    return {
        "status": "moodle_api_action_failed",
        "ok": False,
        "moodle_execution_mode": "api_action",
        "operation_id": operation.get("operation_id", ""),
        "action": operation.get("action", ""),
        "target_course_id": operation.get("target_course_id", DEFAULT_COURSE_ID),
        "error": str(exc),
        "http_status": exc.status,
        "payload": exc.payload,
        "completed_at": now_utc(),
    }


def execute_api_operation(operation: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    action = str(operation.get("action", "")).strip()
    try:
        if action in {"reply_forum_post", "reply_to_forum_post"}:
            return reply_forum_post(operation, env)
        if action in {"publish_announcement", "post_announcement", "publish_forum_post", "post_forum_message", "create_forum_post"}:
            return publish_announcement(operation, env)
        if action in {"create_group", "create_moodle_group"}:
            return create_group(operation, env)
        if action in {"delete_group", "delete_moodle_group"}:
            return delete_group(operation, env)
        if action in {"create_grouping", "create_moodle_grouping"}:
            return create_grouping(operation, env)
        if action in {"delete_grouping", "delete_moodle_grouping"}:
            return delete_grouping(operation, env)
        if action in {"assign_group_names_to_grouping", "assign_groups_to_grouping_by_name"}:
            return assign_group_names_to_grouping(operation, env)
        if action in {"unassign_group_names_from_grouping", "unassign_groups_from_grouping_by_name"}:
            return unassign_group_names_from_grouping(operation, env)
        if action in {"assign_group_to_grouping", "core_group_assign_grouping"}:
            return assign_group_to_grouping(operation, env)
        if action in {"unassign_group_from_grouping", "core_group_unassign_grouping"}:
            return unassign_group_from_grouping(operation, env)
        if action in {"upload_file_api", "core_files_upload", "upload_file"}:
            return upload_file(operation, env)
        if action in {
            "upload_course_file_resource",
            "create_course_file_resource",
            "replace_course_file_resource",
            "local_courseops_api_upload_file_resource",
        }:
            return upload_course_file_resource(operation, env)
    except MoodleAPIError as exc:
        return failed_result(operation, exc)

    unsupported = {
        "edit_assignment_title": "The current HKU APIM whitelist has no Moodle function for editing assignment/resource titles.",
        "set_access_restriction_time": (
            "The current HKU APIM whitelist has no Moodle function for updating module availability/access "
            "restriction JSON. This needs a custom Moodle plugin."
        ),
        "upload_or_replace_file": (
            "The current HKU APIM whitelist has no Moodle function for creating/updating File resources or "
            "uploading files into course modules. This needs a custom Moodle plugin."
        ),
        "create_file_resource": (
            "Creating a Moodle course File resource through the browser-agent fallback is not an APIM action. "
            "Use MOODLE_EXECUTION_MODE=agent_browser_action, or install the custom Moodle plugin for api_action."
        ),
        "create_or_update_hidden_marker": (
            "The current HKU APIM whitelist has no Moodle function for creating/updating Label/Text and media "
            "area resources. This needs a custom Moodle plugin."
        ),
        "delete_forum_post": (
            "The current HKU APIM whitelist and stable browser tools do not expose a safe Moodle forum post/"
            "discussion delete operation. Provide a whitelisted delete API or an approved exact-post browser tool."
        ),
        "delete_forum_reply": (
            "Forum reply deletion is browser-only. Use operation_kind=browser_forum_reply_delete "
            "with current-run approval and an exact discussion/reply target."
        ),
    }
    return blocked_result(operation, unsupported.get(action, f"Unsupported Moodle API action: {action!r}."))


def resolve_file_path(path_text: str, env: dict[str, str]) -> Path:
    path = Path(path_text).expanduser()
    if path.is_absolute():
        return path
    base_dir = env_value(env, "MOODLE_FILE_BASE_DIR", os.getcwd())
    return Path(base_dir).expanduser() / path


def operation_upload_path(operation: dict[str, Any], env: dict[str, str]) -> Path | None:
    path_text = str(
        operation.get("target_file_path")
        or operation.get("upload_file_path")
        or operation.get("source_file_path")
        or env_value(env, "MOODLE_UPLOAD_FILE_PATH")
        or env_value(env, "MOODLE_TARGET_FILE_PATH")
        or env_value(env, "MOODLE_SOURCE_FILE_PATH")
    ).strip()
    if not path_text:
        return None
    return resolve_file_path(path_text, env)


def normalize_moodle_filepath(filepath: str) -> str:
    filepath = filepath.strip() or "/"
    if not filepath.startswith("/"):
        filepath = f"/{filepath}"
    if not filepath.endswith("/"):
        filepath = f"{filepath}/"
    return filepath


def _first_payload_file(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                return item
    return {}


def env_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on", "y"}


def discover_user_id(env: dict[str, str]) -> str:
    explicit = env_value(env, "MOODLE_FILE_UPLOAD_INSTANCEID") or env_value(env, "MOODLE_USER_ID")
    if explicit:
        return explicit
    payload = call_moodle_read_api("core_webservice_get_site_info", {}, env=env)
    if isinstance(payload, dict) and payload.get("userid"):
        return str(payload["userid"])
    raise MoodleAPIError("core_webservice_get_site_info did not return a Moodle userid for file upload.")


def upload_file(operation: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    path = operation_upload_path(operation, env)
    if path is None:
        return blocked_result(operation, "File upload API operation requires MOODLE_UPLOAD_FILE_PATH or target_file_path.")
    if not path.is_file():
        return blocked_result(operation, f"Upload file does not exist: {path}", details={"path": str(path)})

    filename = str(
        operation.get("expected_uploaded_filename")
        or operation.get("filename")
        or env_value(env, "MOODLE_EXPECTED_UPLOADED_FILENAME")
        or path.name
    ).strip()
    if not filename:
        return blocked_result(operation, "File upload API operation requires a filename.")

    filepath = normalize_moodle_filepath(
        str(operation.get("filepath") or env_value(env, "MOODLE_FILE_UPLOAD_FILEPATH", "/"))
    )

    wsfunction = str(
        operation.get("api_execution", {}).get("wsfunction")
        or operation.get("wsfunction")
        or env_value(env, "MOODLE_FILE_UPLOAD_WSFUNCTION", "core_files_upload")
    ).strip()
    if wsfunction != "core_files_upload":
        return blocked_result(
            operation,
            "Only core_files_upload is supported for the Moodle API file upload test.",
            details={"wsfunction": wsfunction},
        )

    contextid = str(operation.get("contextid") or env_value(env, "MOODLE_FILE_UPLOAD_CONTEXTID")).strip()
    contextlevel = str(
        operation.get("contextlevel") or env_value(env, "MOODLE_FILE_UPLOAD_CONTEXTLEVEL", "user")
    ).strip()
    instanceid = str(
        operation.get("instanceid")
        or env_value(env, "MOODLE_FILE_UPLOAD_INSTANCEID")
        or env_value(env, "MOODLE_USER_ID")
    ).strip()
    if not contextid or contextid == "0":
        if not contextlevel:
            return blocked_result(operation, "File upload API operation requires contextid or contextlevel.")
        if not instanceid and contextlevel == "user":
            try:
                instanceid = discover_user_id(env)
            except MoodleAPIError:
                instanceid = "0"
        if not instanceid:
            instanceid = "0"

    component = str(operation.get("component") or env_value(env, "MOODLE_FILE_UPLOAD_COMPONENT", "user")).strip()
    filearea = str(operation.get("filearea") or env_value(env, "MOODLE_FILE_UPLOAD_FILEAREA", "draft")).strip()
    itemid = str(operation.get("itemid") or env_value(env, "MOODLE_FILE_UPLOAD_ITEMID", "0")).strip()
    if not component or not filearea:
        return blocked_result(operation, "File upload API operation requires component and filearea.")

    raw = path.read_bytes()
    params: dict[str, Any] = {
        "component": component,
        "filearea": filearea,
        "itemid": itemid,
        "filepath": filepath,
        "filename": filename,
        "filecontent": base64.b64encode(raw).decode("ascii"),
    }
    if contextid and contextid != "0":
        params["contextid"] = contextid
    else:
        params["contextlevel"] = contextlevel
        params["instanceid"] = instanceid

    payload = call_moodle_api(wsfunction, params, env=env)
    returned_file = _first_payload_file(payload)
    return verified_result(
        operation,
        wsfunction=wsfunction,
        payload=payload,
        details={
            "path": str(path),
            "filename": filename,
            "bytes": len(raw),
            "contextid": contextid,
            "contextlevel": params.get("contextlevel", ""),
            "instanceid": params.get("instanceid", ""),
            "component": component,
            "filearea": filearea,
            "itemid": itemid,
            "returned_itemid": returned_file.get("itemid", ""),
            "contextid_returned": returned_file.get("contextid", ""),
            "fileurl": returned_file.get("fileurl") or returned_file.get("url") or "",
            "filepath": filepath,
        },
    )


def upload_course_file_resource(operation: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    path = operation_upload_path(operation, env)
    if path is None:
        return blocked_result(
            operation,
            "Course File resource upload requires MOODLE_UPLOAD_FILE_PATH or target_file_path.",
        )
    if not path.is_file():
        return blocked_result(operation, f"Upload file does not exist: {path}", details={"path": str(path)})

    course_id = operation_course_id(operation, env)
    filename = str(
        operation.get("expected_uploaded_filename")
        or operation.get("filename")
        or env_value(env, "MOODLE_EXPECTED_UPLOADED_FILENAME")
        or path.name
    ).strip()
    if not filename:
        return blocked_result(operation, "Course File resource upload requires a filename.")

    resource_name = str(
        operation.get("resource_name")
        or operation.get("name")
        or operation.get("target_object")
        or env_value(env, "MOODLE_RESOURCE_NAME")
        or filename
    ).strip()
    if not resource_name:
        return blocked_result(operation, "Course File resource upload requires a resource_name/name.")

    wsfunction = str(
        operation.get("api_execution", {}).get("wsfunction")
        or operation.get("wsfunction")
        or env_value(
            env,
            "MOODLE_COURSE_FILE_UPLOAD_WSFUNCTION",
            "local_courseops_api_upload_file_resource",
        )
    ).strip()
    if wsfunction != "local_courseops_api_upload_file_resource":
        return blocked_result(
            operation,
            "Course File resource upload requires the custom Moodle plugin web service.",
            details={"wsfunction": wsfunction},
        )

    raw = path.read_bytes()
    visible = env_bool(
        operation.get("visible", env_value(env, "MOODLE_RESOURCE_VISIBLE")),
        default=True,
    )
    replacefiles = env_bool(operation.get("replacefiles", env_value(env, "MOODLE_REPLACE_FILES")), default=True)
    sectionnum = str(operation.get("sectionnum") or env_value(env, "MOODLE_SECTION_NUM", "0")).strip() or "0"
    cmid = str(
        operation.get("cmid")
        or operation.get("target_module_id")
        or env_value(env, "MOODLE_TARGET_MODULE_ID", "0")
    ).strip() or "0"
    if not sectionnum.isdigit():
        return blocked_result(
            operation,
            "Course File resource upload requires numeric sectionnum/MOODLE_SECTION_NUM.",
            details={"sectionnum": sectionnum},
        )
    if not cmid.isdigit():
        return blocked_result(
            operation,
            "Course File resource upload requires numeric cmid/MOODLE_TARGET_MODULE_ID, or 0 to create a new resource.",
            details={"cmid": cmid},
        )
    params = {
        "courseid": course_id,
        "sectionnum": sectionnum,
        "cmid": cmid,
        "name": resource_name,
        "intro": str(operation.get("intro") or env_value(env, "MOODLE_RESOURCE_INTRO", "")),
        "visible": "1" if visible else "0",
        "filename": filename,
        "filecontent": base64.b64encode(raw).decode("ascii"),
        "draftitemid": str(operation.get("draftitemid") or env_value(env, "MOODLE_DRAFT_ITEM_ID", "0")),
        "replacefiles": "1" if replacefiles else "0",
        "operationid": str(operation.get("operation_id", "")),
        "approvalrunid": str(operation.get("approval_run_id", "")),
    }

    payload = call_moodle_api(wsfunction, params, env=env, timeout=60)
    files = payload.get("files", []) if isinstance(payload, dict) else []
    return verified_result(
        {**operation, "target_course_id": course_id},
        wsfunction=wsfunction,
        payload=payload,
        details={
            "path": str(path),
            "filename": filename,
            "bytes": len(raw),
            "courseid": course_id,
            "sectionnum": params["sectionnum"],
            "cmid": params["cmid"],
            "returned_cmid": payload.get("cmid", "") if isinstance(payload, dict) else "",
            "resource_url": payload.get("url", "") if isinstance(payload, dict) else "",
            "visible": visible,
            "replacefiles": replacefiles,
            "files": files,
        },
    )


def discover_forum_id_by_name(env: dict[str, str], course_id: str, forum_name: str) -> str:
    forum_list = course_forums(env, course_id)
    matches = [
        forum
        for forum in forum_list
        if isinstance(forum, dict) and str(forum.get("name", "")).strip().casefold() == forum_name.strip().casefold()
    ]
    if not matches:
        raise MoodleAPIError(f"No forum named {forum_name!r} found for course {course_id}.")
    if len(matches) > 1:
        raise MoodleAPIError(f"Forum name {forum_name!r} is ambiguous in course {course_id}.")
    return str(matches[0]["id"])


def resolve_forum_target(
    operation: dict[str, Any],
    env: dict[str, str],
    course_id: str,
) -> dict[str, Any]:
    """Resolve and validate one forum against fresh current-course facts.

    Planner/env ids are never executed on trust: even an explicit id must be
    present in the current course readback, and a supplied id/name pair must
    identify the same forum.
    """

    explicit_forum_id = str(
        operation.get("target_forum_id")
        or operation.get("forumid")
        or env_value(env, "MOODLE_TARGET_FORUM_ID")
    ).strip()
    forum_name = str(
        operation.get("target_forum_name")
        or operation.get("forum_name")
        or env_value(env, "MOODLE_TARGET_FORUM_NAME")
    ).strip()
    action = str(operation.get("action") or "").strip()
    target_ref = str(operation.get("target_ref") or "").strip()

    if target_ref and explicit_forum_id:
        try:
            forum_list = wait_for_expected_readback(
                lambda: course_forums(env, course_id),
                lambda forums: any(
                    str(forum.get("id") or "") == explicit_forum_id
                    for forum in forums
                ),
                env,
                description=(
                    f"runtime-created forum id {explicit_forum_id} "
                    f"for reference {target_ref!r}"
                ),
            )
        except RuntimeError as exc:
            raise MoodleAPIError(str(exc)) from exc
    else:
        forum_list = course_forums(env, course_id)

    if explicit_forum_id:
        target = _unique_forum_match(
            [
                forum
                for forum in forum_list
                if str(forum.get("id") or "") == explicit_forum_id
            ],
            description=f"forum with id {explicit_forum_id}",
            course_id=course_id,
        )
        current_name = str(target.get("name") or "").strip()
        if forum_name and current_name.casefold() != forum_name.casefold():
            raise MoodleAPIError(
                f"Approved forum id {explicit_forum_id!r} and name {forum_name!r} "
                "do not identify the same current forum."
            )
        return target

    if forum_name:
        return _unique_forum_match(
            [
                forum
                for forum in forum_list
                if str(forum.get("name") or "").strip().casefold() == forum_name.casefold()
            ],
            description=f"forum named {forum_name!r}",
            course_id=course_id,
        )

    if action in {"publish_announcement", "post_announcement"}:
        return _unique_forum_match(
            [
                forum
                for forum in forum_list
                if str(forum.get("type", "")).casefold() == "news"
            ],
            description="Moodle announcement/news forum",
            course_id=course_id,
        )

    raise MoodleAPIError("Forum post operation has no exact current-course forum target.")


def _payload_items(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, dict):
        values = payload.get(key, [])
    else:
        values = []
    return [dict(item) for item in values if isinstance(item, dict)]


def _numeric_url_param(url: str, key: str) -> str:
    try:
        values = urllib.parse.parse_qs(urllib.parse.urlparse(str(url or "")).query).get(key, [])
    except ValueError:
        return ""
    value = str(values[0] if values else "").strip()
    return value if value.isdigit() else ""


def forum_discussions(
    env: dict[str, str],
    forum_id: str,
) -> list[dict[str, Any]]:
    payload = call_moodle_read_api(
        "mod_forum_get_forum_discussions",
        {
            "forumid": forum_id,
            "sortorder": "-1",
            "page": "0",
            "perpage": "0",
            "groupid": "0",
        },
        env=env,
    )
    return _payload_items(payload, "discussions")


def forum_discussion_posts(
    env: dict[str, str],
    discussion_id: str,
) -> list[dict[str, Any]]:
    payload = call_moodle_read_api(
        "mod_forum_get_discussion_posts",
        {
            "discussionid": discussion_id,
            "sortby": "created",
            "sortdirection": "ASC",
        },
        env=env,
    )
    return _payload_items(payload, "posts")


def _normalized_forum_text(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def resolve_forum_discussion_target(
    operation: dict[str, Any],
    env: dict[str, str],
    forum_id: str,
) -> dict[str, Any]:
    discussion_id = str(
        operation.get("target_discussion_id")
        or operation.get("discussion_id")
        or env_value(env, "MOODLE_FORUM_DISCUSSION_ID")
    ).strip()
    discussion_url = str(
        operation.get("target_discussion_url")
        or operation.get("discussion_url")
        or env_value(env, "MOODLE_FORUM_DISCUSSION_URL")
    ).strip()
    discussion_title = str(
        operation.get("target_discussion_title")
        or operation.get("discussion_title")
        or operation.get("target_subject")
    ).strip()
    if not discussion_id and discussion_url:
        discussion_id = _numeric_url_param(discussion_url, "d")
    if not discussion_id and not discussion_title:
        raise MoodleAPIError(
            "Forum reply operation requires an exact discussion id, URL, or title."
        )

    matches = forum_discussions(env, forum_id)
    if discussion_id:
        matches = [
            item
            for item in matches
            if str(
                item.get("id")
                or item.get("discussion")
                or item.get("discussionid")
                or ""
            ).strip()
            == discussion_id
        ]
    if discussion_title:
        matches = [
            item
            for item in matches
            if _normalized_forum_text(
                item.get("name") or item.get("subject") or item.get("title")
            )
            == _normalized_forum_text(discussion_title)
        ]
    if len(matches) != 1:
        raise MoodleAPIError(
            "Forum reply action-time preflight requires one exact discussion in "
            f"forum {forum_id}; current readback found {len(matches)} matches."
        )
    return matches[0]


def _reply_parent_post(
    operation: dict[str, Any],
    env: dict[str, str],
    discussion_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    explicit_post_id = str(
        operation.get("target_post_id")
        or operation.get("parent_post_id")
        or operation.get("post_id")
        or env_value(env, "MOODLE_FORUM_POST_ID")
    ).strip()
    posts = forum_discussion_posts(env, discussion_id)
    if explicit_post_id:
        matches = [
            post
            for post in posts
            if str(post.get("id") or post.get("postid") or "").strip()
            == explicit_post_id
        ]
    else:
        matches = [
            post
            for post in posts
            if str(post.get("parent") or post.get("parentid") or "0").strip()
            in {"", "0"}
        ]
    if len(matches) != 1:
        target = f"post {explicit_post_id}" if explicit_post_id else "root question post"
        raise MoodleAPIError(
            f"Forum reply action-time preflight requires one exact {target}; "
            f"current discussion readback found {len(matches)} matches."
        )
    post = matches[0]
    current_discussion_id = str(
        post.get("discussion") or post.get("discussionid") or ""
    ).strip()
    if current_discussion_id and current_discussion_id != discussion_id:
        raise MoodleAPIError(
            f"Approved parent post belongs to discussion {current_discussion_id}, "
            f"not approved discussion {discussion_id}."
        )
    can_reply = post.get("canreply")
    if can_reply is False or str(can_reply).strip().casefold() in {"0", "false", "no"}:
        raise MoodleAPIError("The approved Forum post is not currently replyable.")
    return post, posts


def reply_forum_post(operation: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    course_id = operation_course_id(operation, env)
    if course_id != DEFAULT_COURSE_ID:
        return blocked_result(
            operation,
            f"Forum replies are restricted to sandbox course {DEFAULT_COURSE_ID}.",
            details={"course_id": course_id},
        )
    subject = str(
        operation.get("subject")
        or env_value(env, "MOODLE_FORUM_POST_SUBJECT")
    ).strip()
    message = str(
        operation.get("message")
        or operation.get("body")
        or env_value(env, "MOODLE_FORUM_POST_MESSAGE")
    ).strip()
    if not subject or not message:
        return blocked_result(
            operation,
            "Forum reply API operation requires both subject and message/body.",
            details={"subject_present": bool(subject), "message_present": bool(message)},
        )

    try:
        forum = resolve_forum_target(operation, env, course_id)
        forum_id = str(forum.get("id") or "").strip()
        forum_name = str(forum.get("name") or "").strip()
        discussion = resolve_forum_discussion_target(
            operation,
            env,
            forum_id,
        )
        discussion_id = str(
            discussion.get("id")
            or discussion.get("discussion")
            or discussion.get("discussionid")
            or ""
        ).strip()
        if not discussion_id.isdigit():
            raise MoodleAPIError(
                "Exact Forum discussion readback did not return a numeric discussion id."
            )
        parent_post, _before_posts = _reply_parent_post(
            operation,
            env,
            discussion_id,
        )
    except MoodleAPIError as exc:
        return blocked_result(
            operation,
            f"Forum reply target preflight failed: {exc}",
            details={"course_id": course_id, "subject": subject},
        )

    parent_post_id = str(
        parent_post.get("id") or parent_post.get("postid") or ""
    ).strip()
    if not parent_post_id.isdigit():
        return blocked_result(
            operation,
            "Forum reply action-time preflight did not resolve a numeric parent post id.",
            details={"discussion_id": discussion_id},
        )
    payload = call_moodle_api(
        "mod_forum_add_discussion_post",
        {
            "postid": parent_post_id,
            "subject": subject,
            "message": message,
        },
        env=env,
    )
    new_post_id = str(
        (payload.get("postid") or "") if isinstance(payload, dict) else ""
    ).strip()
    if not new_post_id.isdigit():
        raise MoodleAPIError(
            "mod_forum_add_discussion_post did not return the new post id.",
            payload=payload,
        )
    try:
        readback_posts = wait_for_expected_readback(
            lambda: forum_discussion_posts(env, discussion_id),
            lambda posts: sum(
                1
                for post in posts
                if str(post.get("id") or post.get("postid") or "").strip()
                == new_post_id
                and str(
                    post.get("discussion")
                    or post.get("discussionid")
                    or discussion_id
                ).strip()
                == discussion_id
                and str(post.get("parent") or post.get("parentid") or "").strip()
                == parent_post_id
                and _normalized_forum_text(post.get("subject"))
                == _normalized_forum_text(subject)
            )
            == 1,
            env,
            description=(
                f"new Forum reply post {new_post_id} in discussion {discussion_id}"
            ),
        )
    except RuntimeError as exc:
        raise MoodleAPIError(str(exc), payload=payload) from exc

    return verified_result(
        {**operation, "target_course_id": course_id},
        wsfunction="mod_forum_add_discussion_post",
        payload=payload,
        details={
            "forum_id": forum_id,
            "forum_name": forum_name,
            "discussion_id": discussion_id,
            "discussion_title": str(
                discussion.get("name")
                or discussion.get("subject")
                or discussion.get("title")
                or ""
            ).strip(),
            "parent_post_id": parent_post_id,
            "new_post_id": new_post_id,
            "subject": subject,
            "readback_match_count": sum(
                1
                for post in readback_posts
                if str(post.get("id") or post.get("postid") or "").strip()
                == new_post_id
            ),
        },
    )


def publish_announcement(operation: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    course_id = operation_course_id(operation, env)
    subject = str(
        operation.get("subject")
        or env_value(env, "MOODLE_FORUM_POST_SUBJECT")
        or env_value(env, "MOODLE_ANNOUNCEMENT_SUBJECT")
    ).strip()
    message = str(
        operation.get("message")
        or operation.get("body")
        or env_value(env, "MOODLE_FORUM_POST_MESSAGE")
        or env_value(env, "MOODLE_ANNOUNCEMENT_BODY")
    ).strip()
    if not subject or not message:
        return blocked_result(
            operation,
            "Announcement API operation requires both subject and message/body.",
            details={"subject_present": bool(subject), "message_present": bool(message)},
        )

    try:
        forum = resolve_forum_target(operation, env, course_id)
    except MoodleAPIError as exc:
        return blocked_result(
            operation,
            f"Forum target preflight failed: {exc}",
            details={"course_id": course_id, "subject": subject},
        )
    forum_id = str(forum.get("id") or "").strip()
    forum_name = str(forum.get("name") or "").strip()

    payload = call_moodle_api(
        "mod_forum_add_discussion",
        {
            "forumid": forum_id,
            "subject": subject,
            "message": message,
        },
        env=env,
    )
    return verified_result(
        {**operation, "target_course_id": course_id},
        wsfunction="mod_forum_add_discussion",
        payload=payload,
        details={"forum_id": forum_id, "forum_name": forum_name, "subject": subject},
    )


def create_group(operation: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    course_id = operation_course_id(operation, env)
    name = str(operation.get("group_name") or operation.get("name") or env_value(env, "MOODLE_GROUP_NAME")).strip()
    if not name:
        return blocked_result(operation, "Group API operation requires group_name.")
    description = str(operation.get("description") or env_value(env, "MOODLE_GROUP_DESCRIPTION", "")).strip()
    try:
        payload = call_moodle_api(
            "core_group_create_groups",
            {
                "groups[0][courseid]": course_id,
                "groups[0][name]": name,
                "groups[0][description]": description,
                "groups[0][descriptionformat]": str(operation.get("descriptionformat", 1)),
            },
            env=env,
        )
    except MoodleAPIError as exc:
        if not _is_same_name_exists_error(exc):
            raise
        return _verified_existing_named_create(
            operation={**operation, "target_course_id": course_id},
            create_wsfunction="core_group_create_groups",
            readback_wsfunction="core_group_get_course_groups",
            course_id=course_id,
            noun="group",
            name=name,
            env=env,
            original_error=exc,
        )
    _readback_payload, match = _wait_for_named_item(
        readback_wsfunction="core_group_get_course_groups",
        course_id=course_id,
        name=name,
        env=env,
        noun="group",
    )
    return verified_result(
        {**operation, "target_course_id": course_id},
        wsfunction="core_group_create_groups",
        payload={"create": payload, "readback": match},
        details={
            "group_name": name,
            "group_id": str(match.get("id") or match.get("groupid") or ""),
            "readback_wsfunction": "core_group_get_course_groups",
        },
    )


def delete_group(operation: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    course_id = operation_course_id(operation, env)
    group_id = str(operation.get("group_id") or operation.get("groupid") or env_value(env, "MOODLE_GROUP_ID")).strip()
    if not group_id:
        return blocked_result(operation, "Delete group API operation requires group_id.")
    initial_readback = call_moodle_read_api(
        "core_group_get_course_groups",
        {"courseid": course_id},
        env=env,
    )
    initially_present = any(
        str(item.get("id") or item.get("groupid") or "") == group_id
        for items in _items_by_exact_name(initial_readback).values()
        for item in items
    )
    if not initially_present:
        return verified_result(
            {**operation, "target_course_id": course_id},
            wsfunction="core_group_delete_groups",
            payload={"delete": "already_absent", "readback_absent": True},
            details={
                "group_id": group_id,
                "group_name": str(operation.get("group_name") or ""),
                "readback_wsfunction": "core_group_get_course_groups",
                "already_applied": True,
            },
        )
    payload = call_moodle_api("core_group_delete_groups", {"groupids[0]": group_id}, env=env)
    _wait_for_absent_item_id(
        readback_wsfunction="core_group_get_course_groups",
        course_id=course_id,
        item_id=group_id,
        env=env,
        noun="group",
    )
    return verified_result(
        {**operation, "target_course_id": course_id},
        wsfunction="core_group_delete_groups",
        payload={"delete": payload, "readback_absent": True},
        details={
            "group_id": group_id,
            "group_name": str(operation.get("group_name") or ""),
            "readback_wsfunction": "core_group_get_course_groups",
        },
    )


def create_grouping(operation: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    course_id = operation_course_id(operation, env)
    name = str(
        operation.get("grouping_name") or operation.get("name") or env_value(env, "MOODLE_GROUPING_NAME")
    ).strip()
    idnumber = str(operation.get("idnumber") or env_value(env, "MOODLE_GROUPING_IDNUMBER")).strip()
    if not name or not idnumber:
        return blocked_result(
            operation,
            "Grouping API operation requires grouping_name and idnumber. Moodle returns invalidresponse without idnumber.",
            details={"name_present": bool(name), "idnumber_present": bool(idnumber)},
        )
    description = str(operation.get("description") or env_value(env, "MOODLE_GROUPING_DESCRIPTION", "")).strip()
    try:
        payload = call_moodle_api(
            "core_group_create_groupings",
            {
                "groupings[0][courseid]": course_id,
                "groupings[0][name]": name,
                "groupings[0][idnumber]": idnumber,
                "groupings[0][description]": description,
                "groupings[0][descriptionformat]": str(operation.get("descriptionformat", 1)),
            },
            env=env,
        )
    except MoodleAPIError as exc:
        if not _is_same_name_exists_error(exc):
            raise
        return _verified_existing_named_create(
            operation={**operation, "target_course_id": course_id},
            create_wsfunction="core_group_create_groupings",
            readback_wsfunction="core_group_get_course_groupings",
            course_id=course_id,
            noun="grouping",
            name=name,
            env=env,
            original_error=exc,
            details={"idnumber": idnumber},
        )
    _readback_payload, match = _wait_for_named_item(
        readback_wsfunction="core_group_get_course_groupings",
        course_id=course_id,
        name=name,
        env=env,
        noun="grouping",
    )
    return verified_result(
        {**operation, "target_course_id": course_id},
        wsfunction="core_group_create_groupings",
        payload={"create": payload, "readback": match},
        details={
            "grouping_name": name,
            "grouping_id": str(match.get("id") or match.get("groupingid") or ""),
            "idnumber": idnumber,
            "readback_wsfunction": "core_group_get_course_groupings",
        },
    )


def delete_grouping(operation: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    course_id = operation_course_id(operation, env)
    grouping_id = str(
        operation.get("grouping_id")
        or operation.get("groupingid")
        or env_value(env, "MOODLE_GROUPING_ID")
    ).strip()
    if not grouping_id:
        return blocked_result(operation, "Delete grouping API operation requires grouping_id.")
    initial_readback = call_moodle_read_api(
        "core_group_get_course_groupings",
        {"courseid": course_id},
        env=env,
    )
    initially_present = any(
        str(item.get("id") or item.get("groupingid") or "") == grouping_id
        for items in _items_by_exact_name(initial_readback).values()
        for item in items
    )
    if not initially_present:
        return verified_result(
            {**operation, "target_course_id": course_id},
            wsfunction="core_group_delete_groupings",
            payload={"delete": "already_absent", "readback_absent": True},
            details={
                "grouping_id": grouping_id,
                "grouping_name": str(operation.get("grouping_name") or ""),
                "readback_wsfunction": "core_group_get_course_groupings",
                "already_applied": True,
            },
        )
    payload = call_moodle_api(
        "core_group_delete_groupings",
        {"groupingids[0]": grouping_id},
        env=env,
    )
    _wait_for_absent_item_id(
        readback_wsfunction="core_group_get_course_groupings",
        course_id=course_id,
        item_id=grouping_id,
        env=env,
        noun="grouping",
    )
    return verified_result(
        {**operation, "target_course_id": course_id},
        wsfunction="core_group_delete_groupings",
        payload={"delete": payload, "readback_absent": True},
        details={
            "grouping_id": grouping_id,
            "grouping_name": str(operation.get("grouping_name") or ""),
            "readback_wsfunction": "core_group_get_course_groupings",
        },
    )


def assign_group_to_grouping(operation: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    group_id = str(operation.get("group_id") or operation.get("groupid") or env_value(env, "MOODLE_GROUP_ID")).strip()
    grouping_id = str(
        operation.get("grouping_id") or operation.get("groupingid") or env_value(env, "MOODLE_GROUPING_ID")
    ).strip()
    if not group_id or not grouping_id:
        return blocked_result(operation, "Assign grouping API operation requires group_id and grouping_id.")
    payload = call_moodle_api(
        "core_group_assign_grouping",
        {"assignments[0][groupingid]": grouping_id, "assignments[0][groupid]": group_id},
        env=env,
    )
    return verified_result(
        operation,
        wsfunction="core_group_assign_grouping",
        payload=payload,
        details={"group_id": group_id, "grouping_id": grouping_id},
    )


def _group_names(operation: dict[str, Any], env: dict[str, str]) -> list[str]:
    raw = operation.get("group_names") or operation.get("groups") or env_value(env, "MOODLE_GROUP_NAMES")
    if isinstance(raw, (list, tuple)):
        return [str(item).strip() for item in raw if str(item or "").strip()]
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, (list, tuple)):
        return [str(item).strip() for item in parsed if str(item or "").strip()]
    return [part.strip() for part in text.split(",") if part.strip()]


def _items_by_exact_name(items: Any) -> dict[str, list[dict[str, Any]]]:
    if isinstance(items, dict):
        source = items.get("groups") or items.get("groupings") or items.get("items") or []
    elif isinstance(items, list):
        source = items
    else:
        source = []
    by_name: dict[str, list[dict[str, Any]]] = {}
    for item in source:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        item_id = str(item.get("id") or item.get("groupid") or item.get("groupingid") or "").strip()
        if name and item_id:
            by_name.setdefault(name, []).append(item)
    return by_name


def _payload_text(payload: Any) -> str:
    if isinstance(payload, (dict, list)):
        return json.dumps(payload, ensure_ascii=False)
    return str(payload or "")


def _is_same_name_exists_error(exc: MoodleAPIError) -> bool:
    text = f"{exc} {_payload_text(exc.payload)}".casefold()
    return "same name" in text and "already exists" in text


def _wait_for_named_item(
    *,
    readback_wsfunction: str,
    course_id: str,
    name: str,
    env: dict[str, str],
    noun: str,
) -> tuple[Any, dict[str, Any]]:
    def read_named_items() -> Any:
        current = call_moodle_read_api(
            readback_wsfunction,
            {"courseid": course_id},
            env=env,
        )
        matches = _items_by_exact_name(current).get(name, [])
        if len(matches) > 1:
            raise TerminalReadbackError(
                f"Moodle {noun} name {name!r} is ambiguous: "
                f"readback found {len(matches)} exact matches."
            )
        return current

    try:
        payload = wait_for_expected_readback(
            read_named_items,
            lambda current: len(_items_by_exact_name(current).get(name, [])) == 1,
            env,
            description=f"unique {noun} {name!r}",
        )
    except RuntimeError as exc:
        raise MoodleAPIError(str(exc)) from exc
    matches = _items_by_exact_name(payload).get(name, [])
    if len(matches) != 1:
        raise MoodleAPIError(
            f"Moodle readback did not resolve one unique {noun} named {name!r}."
        )
    return payload, matches[0]


def _wait_for_absent_item_id(
    *,
    readback_wsfunction: str,
    course_id: str,
    item_id: str,
    env: dict[str, str],
    noun: str,
) -> Any:
    def remaining(payload: Any) -> list[dict[str, Any]]:
        return [
            item
            for items in _items_by_exact_name(payload).values()
            for item in items
            if str(
                item.get("id")
                or item.get("groupid")
                or item.get("groupingid")
                or ""
            )
            == item_id
        ]

    try:
        return wait_for_expected_readback(
            lambda: call_moodle_read_api(
                readback_wsfunction,
                {"courseid": course_id},
                env=env,
            ),
            lambda current: not remaining(current),
            env,
            description=f"deletion of {noun} id {item_id}",
        )
    except RuntimeError as exc:
        raise MoodleAPIError(str(exc)) from exc


def _verified_existing_named_create(
    *,
    operation: dict[str, Any],
    create_wsfunction: str,
    readback_wsfunction: str,
    course_id: str,
    noun: str,
    name: str,
    env: dict[str, str],
    original_error: MoodleAPIError,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        _readback_payload, item = _wait_for_named_item(
            readback_wsfunction=readback_wsfunction,
            course_id=course_id,
            name=name,
            env=env,
            noun=noun,
        )
    except MoodleAPIError as exc:
        exc.status = original_error.status
        exc.payload = original_error.payload
        raise
    item_id = str(item.get("id") or item.get("groupid") or item.get("groupingid") or "")
    result_details = {
        f"{noun}_name": name,
        f"{noun}_id": item_id,
        "status": "already_present",
        "readback_wsfunction": readback_wsfunction,
    }
    result_details.update(details or {})
    return verified_result(
        operation,
        wsfunction=create_wsfunction,
        payload={
            "status": "already_present",
            "existing": item,
            "original_error": original_error.payload,
        },
        details=result_details,
    )


def _unique_named_id(by_name: dict[str, list[dict[str, Any]]], name: str, noun: str) -> str:
    matches = by_name.get(name, [])
    if not matches:
        raise MoodleAPIError(f"Could not find Moodle {noun} named {name!r}.")
    if len(matches) > 1:
        raise MoodleAPIError(f"Moodle {noun} name {name!r} is ambiguous.")
    return str(matches[0].get("id") or matches[0].get("groupid") or matches[0].get("groupingid") or "")


def assign_group_names_to_grouping(operation: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    course_id = operation_course_id(operation, env)
    grouping_name = str(
        operation.get("grouping_name") or operation.get("name") or env_value(env, "MOODLE_GROUPING_NAME")
    ).strip()
    names = _group_names(operation, env)
    if not grouping_name or not names:
        return blocked_result(
            operation,
            "Assign grouping API operation requires grouping_name and group_names.",
            details={"grouping_name_present": bool(grouping_name), "group_names_count": len(names)},
        )

    groups = call_moodle_read_api("core_group_get_course_groups", {"courseid": course_id}, env=env)
    groupings = call_moodle_read_api("core_group_get_course_groupings", {"courseid": course_id}, env=env)
    group_by_name = _items_by_exact_name(groups)
    grouping_by_name = _items_by_exact_name(groupings)
    grouping_id = _unique_named_id(grouping_by_name, grouping_name, "grouping")

    assignment_payloads: list[dict[str, Any]] = []
    for name in names:
        group_id = _unique_named_id(group_by_name, name, "group")
        try:
            payload = call_moodle_api(
                "core_group_assign_grouping",
                {"assignments[0][groupingid]": grouping_id, "assignments[0][groupid]": group_id},
                env=env,
            )
            status = "assigned"
        except MoodleAPIError as exc:
            payload_text = json.dumps(exc.payload, ensure_ascii=False) if exc.payload is not None else str(exc)
            if "already" not in payload_text.casefold():
                raise
            payload = exc.payload
            status = "already_present"
        assignment_payloads.append(
            {
                "status": status,
                "group_name": name,
                "group_id": group_id,
                "grouping_name": grouping_name,
                "grouping_id": grouping_id,
                "payload": payload,
            }
        )

    fresh_groups = call_moodle_read_api("core_group_get_course_groups", {"courseid": course_id}, env=env)
    fresh_groupings = call_moodle_read_api("core_group_get_course_groupings", {"courseid": course_id}, env=env)
    fresh_group_by_name = _items_by_exact_name(fresh_groups)
    fresh_grouping_by_name = _items_by_exact_name(fresh_groupings)
    _unique_named_id(fresh_grouping_by_name, grouping_name, "grouping")
    for name in names:
        _unique_named_id(fresh_group_by_name, name, "group")
    return verified_result(
        {**operation, "target_course_id": course_id},
        wsfunction="core_group_assign_grouping",
        payload={"assignments": assignment_payloads},
        details={
            "grouping_name": grouping_name,
            "grouping_id": grouping_id,
            "group_names": names,
            "readback_wsfunctions": [
                "core_group_get_course_groups",
                "core_group_get_course_groupings",
            ],
            "relationship_readback_required": True,
        },
    )


def unassign_group_names_from_grouping(operation: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    course_id = operation_course_id(operation, env)
    grouping_name = str(
        operation.get("grouping_name") or operation.get("name") or env_value(env, "MOODLE_GROUPING_NAME")
    ).strip()
    names = _group_names(operation, env)
    if not grouping_name or not names:
        return blocked_result(
            operation,
            "Unassign grouping API operation requires grouping_name and group_names.",
            details={"grouping_name_present": bool(grouping_name), "group_names_count": len(names)},
        )
    groups = call_moodle_read_api("core_group_get_course_groups", {"courseid": course_id}, env=env)
    groupings = call_moodle_read_api("core_group_get_course_groupings", {"courseid": course_id}, env=env)
    group_by_name = _items_by_exact_name(groups)
    grouping_by_name = _items_by_exact_name(groupings)
    if not grouping_by_name.get(grouping_name):
        return verified_result(
            {**operation, "target_course_id": course_id},
            wsfunction="core_group_unassign_grouping",
            payload={"unassignments": "grouping_already_absent"},
            details={
                "grouping_name": grouping_name,
                "group_names": names,
                "relationship_readback_required": False,
                "already_applied": True,
            },
        )
    grouping_id = _unique_named_id(grouping_by_name, grouping_name, "grouping")
    unassignment_payloads: list[dict[str, Any]] = []
    for name in names:
        group_id = _unique_named_id(group_by_name, name, "group")
        try:
            payload = call_moodle_api(
                "core_group_unassign_grouping",
                {"unassignments[0][groupingid]": grouping_id, "unassignments[0][groupid]": group_id},
                env=env,
            )
            status = "unassigned"
        except MoodleAPIError as exc:
            payload_text = json.dumps(exc.payload, ensure_ascii=False) if exc.payload is not None else str(exc)
            lowered = payload_text.casefold()
            if "not assigned" not in lowered and "does not exist" not in lowered:
                raise
            payload = exc.payload
            status = "already_absent"
        unassignment_payloads.append(
            {
                "status": status,
                "group_name": name,
                "group_id": group_id,
                "grouping_name": grouping_name,
                "grouping_id": grouping_id,
                "payload": payload,
            }
        )
    fresh_groups = call_moodle_read_api("core_group_get_course_groups", {"courseid": course_id}, env=env)
    fresh_groupings = call_moodle_read_api("core_group_get_course_groupings", {"courseid": course_id}, env=env)
    fresh_group_by_name = _items_by_exact_name(fresh_groups)
    fresh_grouping_by_name = _items_by_exact_name(fresh_groupings)
    _unique_named_id(fresh_grouping_by_name, grouping_name, "grouping")
    for name in names:
        _unique_named_id(fresh_group_by_name, name, "group")
    return verified_result(
        {**operation, "target_course_id": course_id},
        wsfunction="core_group_unassign_grouping",
        payload={"unassignments": unassignment_payloads},
        details={
            "grouping_name": grouping_name,
            "grouping_id": grouping_id,
            "group_names": names,
            "readback_wsfunctions": [
                "core_group_get_course_groups",
                "core_group_get_course_groupings",
            ],
            "relationship_readback_required": True,
        },
    )


def unassign_group_from_grouping(operation: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    group_id = str(operation.get("group_id") or operation.get("groupid") or env_value(env, "MOODLE_GROUP_ID")).strip()
    grouping_id = str(
        operation.get("grouping_id") or operation.get("groupingid") or env_value(env, "MOODLE_GROUPING_ID")
    ).strip()
    if not group_id or not grouping_id:
        return blocked_result(operation, "Unassign grouping API operation requires group_id and grouping_id.")
    payload = call_moodle_api(
        "core_group_unassign_grouping",
        {"unassignments[0][groupingid]": grouping_id, "unassignments[0][groupid]": group_id},
        env=env,
    )
    return verified_result(
        operation,
        wsfunction="core_group_unassign_grouping",
        payload=payload,
        details={"group_id": group_id, "grouping_id": grouping_id},
    )
