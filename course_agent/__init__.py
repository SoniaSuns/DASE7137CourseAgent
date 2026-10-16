"""Course agent contracts and Codex runtime helpers.

Names load lazily so a caller that needs only the Codex command builder does not
import the legacy capability/pipeline modules (and their course_agent_app imports).
"""
from importlib import import_module

_SOURCES = {
    "build_codex_exec_command": "codex_runtime",
    "codex_model": "codex_runtime",
    "codex_no_run": "codex_runtime",
    "unsupported_legacy_env_warnings": "codex_runtime",
    "CAPABILITY_REGISTRY": "capabilities",
    "capability_names": "capabilities",
    "capability_specs": "capabilities",
    "resolve_capability_manifest": "capabilities",
    "validate_capability_operation": "capabilities",
    "ALLOWED_OPERATION_KINDS": "contracts",
    "SANDBOX_COURSE_ID": "contracts",
    "SANDBOX_COURSE_URL": "contracts",
    "apply_intent_defaults": "contracts",
    "apply_manifest_defaults": "contracts",
    "apply_result_defaults": "contracts",
    "high_risk_reapproval_reasons": "contracts",
    "load_schema": "contracts",
    "validate_course_agent_result": "contracts",
    "validate_course_operation_manifest": "contracts",
    "validate_course_task_intent": "contracts",
    "PIPELINE_ROLES": "pipeline",
    "REQUIRED_VERIFIERS": "pipeline",
    "normalize_browser_step": "pipeline",
    "normalize_browser_steps": "pipeline",
    "role_contract_markdown": "pipeline",
    "summarize_execution_verification": "pipeline",
}

__all__ = [
    "ALLOWED_OPERATION_KINDS",
    "CAPABILITY_REGISTRY",
    "SANDBOX_COURSE_ID",
    "SANDBOX_COURSE_URL",
    "apply_intent_defaults",
    "apply_manifest_defaults",
    "apply_result_defaults",
    "build_codex_exec_command",
    "capability_names",
    "capability_specs",
    "codex_model",
    "codex_no_run",
    "unsupported_legacy_env_warnings",
    "high_risk_reapproval_reasons",
    "load_schema",
    "PIPELINE_ROLES",
    "REQUIRED_VERIFIERS",
    "normalize_browser_step",
    "normalize_browser_steps",
    "role_contract_markdown",
    "resolve_capability_manifest",
    "summarize_execution_verification",
    "validate_capability_operation",
    "validate_course_agent_result",
    "validate_course_operation_manifest",
    "validate_course_task_intent",
]


def __getattr__(name):
    if name not in _SOURCES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module("." + _SOURCES[name], __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_SOURCES))
