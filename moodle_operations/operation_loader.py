from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT = PROJECT_ROOT / "course_agent_app"
INHERITANCE_KEYS = ("inherits_from", "inherit_from", "base_operation", "base_template")


def resolve_operation_path(path_text: str | Path, base_dir: Path | None = None) -> Path:
    path = Path(path_text).expanduser()
    if path.is_absolute():
        return path
    if base_dir is not None:
        candidate = base_dir / path
        if candidate.exists():
            return candidate
    return ROOT / path


def deep_merge_operation(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if key in INHERITANCE_KEYS:
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge_operation(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _inheritance_ref(operation: dict[str, Any]) -> str:
    for key in INHERITANCE_KEYS:
        value = operation.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def load_operation(path_text: str | Path, *, root: Path = ROOT) -> tuple[Path, dict[str, Any]]:
    path = resolve_operation_path(path_text, root)
    return path, _load_operation(path, seen=set())


def _load_operation(path: Path, *, seen: set[Path]) -> dict[str, Any]:
    path = path.resolve()
    if path in seen:
        chain = " -> ".join(str(item) for item in [*seen, path])
        raise ValueError(f"Operation inheritance cycle detected: {chain}")
    if not path.exists():
        raise FileNotFoundError(f"Operation file not found: {path}")

    seen.add(path)
    operation = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(operation, dict):
        raise ValueError(f"Operation file must contain a JSON object: {path}")

    base_ref = _inheritance_ref(operation)
    if not base_ref:
        operation.setdefault("_operation_source_path", str(path))
        return operation

    base_path = resolve_operation_path(base_ref, path.parent)
    base_operation = _load_operation(base_path, seen=seen)
    resolved = deep_merge_operation(base_operation, operation)
    resolved["_operation_source_path"] = str(path)
    resolved["_operation_inheritance"] = {
        "base_path": str(base_path.resolve()),
        "overlay_path": str(path),
    }
    return resolved
