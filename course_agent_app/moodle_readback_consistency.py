from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TypeVar


SnapshotT = TypeVar("SnapshotT")

DEFAULT_ATTEMPTS = 12
DEFAULT_DELAY_SECONDS = 2.0


class TerminalReadbackError(RuntimeError):
    """A deterministic safety conflict that retries cannot make unambiguous."""


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


def post_write_readback_settings(env: dict[str, str]) -> tuple[int, float]:
    return (
        _positive_int(
            env.get("COURSE_AGENT_POST_WRITE_READBACK_ATTEMPTS"),
            DEFAULT_ATTEMPTS,
        ),
        _nonnegative_float(
            env.get("COURSE_AGENT_POST_WRITE_READBACK_RETRY_SECONDS"),
            DEFAULT_DELAY_SECONDS,
        ),
    )


def wait_for_expected_readback(
    read_snapshot: Callable[[], SnapshotT],
    expected: Callable[[SnapshotT], bool],
    env: dict[str, str],
    *,
    description: str,
) -> SnapshotT:
    """Poll a read-only Moodle snapshot until the approved state is observable."""

    attempts, delay_seconds = post_write_readback_settings(env)
    last_snapshot: SnapshotT | None = None
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            snapshot = read_snapshot()
            last_snapshot = snapshot
            last_error = None
            if expected(snapshot):
                return snapshot
        except TerminalReadbackError:
            raise
        except Exception as exc:
            last_error = exc

        if attempt < attempts and delay_seconds:
            time.sleep(delay_seconds)

    detail = f" Last read error: {last_error}" if last_error is not None else ""
    observed = (
        " No readable snapshot was returned."
        if last_snapshot is None
        else " Moodle kept returning a readable snapshot that did not contain the expected state."
    )
    raise RuntimeError(
        f"Timed out waiting for Moodle readback to verify {description} "
        f"after {attempts} attempts.{observed}{detail}"
    )
