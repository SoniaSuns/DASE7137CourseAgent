#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "course_agent_app"
REFERENCE_PACK = ROOT / "course_files" / "reference" / "DASE7137" / "2026-2027-sem1"
REQUIRED_REFERENCE_FILES = (
    "calendar.json",
    "refresh_rules.md",
    "semester_schedule.json",
    "source_manifest.json",
    "syllabus_rules.json",
    "timeline.json",
)
REQUIRED_MODULES = (
    "playwright",
    "docx",
    "openpyxl",
    "pptx",
    "pypdf",
    "pdfplumber",
)
WINDOWS_REQUIRED_MODULES = ("win32com", "pythoncom")
WINDOWS_OFFICE_PROG_IDS = (
    "Word.Application",
    "Excel.Application",
    "PowerPoint.Application",
)


def configure_utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, OSError, ValueError):
            continue


configure_utf8_streams()


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def configured_path(env: dict[str, str], key: str, fallback: str = "") -> Path | None:
    value = os.environ.get(key, "").strip() or env.get(key, "").strip() or fallback
    return Path(value).expanduser() if value else None


def configured_app_path(env: dict[str, str], key: str, fallback: str = "") -> Path | None:
    path = configured_path(env, key, fallback)
    if path is not None and not path.is_absolute():
        path = APP_ROOT / path
    return path


def first_existing(paths: list[Path | None]) -> Path | None:
    return next((path for path in paths if path is not None and path.is_file()), None)


def windows_chrome_path() -> Path | None:
    if os.name != "nt":
        return None
    program_files = Path(os.environ.get("PROGRAMFILES", ""))
    program_files_x86 = Path(os.environ.get("ProgramFiles(x86)", ""))
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    return first_existing(
        [
            program_files / "Google" / "Chrome" / "Application" / "chrome.exe",
            program_files_x86 / "Google" / "Chrome" / "Application" / "chrome.exe",
            local_app_data / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]
    )


def windows_soffice_path() -> Path | None:
    if os.name != "nt":
        return None
    return first_existing(
        [
            Path(os.environ.get("PROGRAMFILES", ""))
            / "LibreOffice"
            / "program"
            / "soffice.exe",
            Path(os.environ.get("ProgramFiles(x86)", ""))
            / "LibreOffice"
            / "program"
            / "soffice.exe",
        ]
    )


def windows_office_registration() -> dict[str, bool]:
    if os.name != "nt":
        return {}
    try:
        import winreg
    except ImportError:
        return {prog_id: False for prog_id in WINDOWS_OFFICE_PROG_IDS}
    result: dict[str, bool] = {}
    for prog_id in WINDOWS_OFFICE_PROG_IDS:
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\CLSID"):
                result[prog_id] = True
        except OSError:
            result[prog_id] = False
    return result


def codex_candidates(file_env: dict[str, str]) -> list[Path]:
    candidates: list[Path] = []
    configured = configured_path(file_env, "CODEX_BIN")
    if configured:
        candidates.append(configured)
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        user_bin = local / "OpenAI" / "Codex" / "bin"
        if user_bin.is_dir():
            candidates.extend(sorted(user_bin.rglob("codex.exe"), key=lambda path: path.stat().st_mtime, reverse=True))
        candidates.extend(
            [
                local / "Programs" / "ChatGPT" / "resources" / "codex.exe",
                local / "OpenAI" / "ChatGPT" / "codex.exe",
            ]
        )
    discovered = shutil.which("codex")
    if discovered:
        candidates.append(Path(discovered))
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(str(candidate.resolve(strict=False)))
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def executable_responds(path: Path, *args: str) -> bool:
    if not path.is_file():
        return False
    try:
        completed = subprocess.run(
            [str(path), *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def playwright_chromium_path() -> Path | None:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            path = Path(playwright.chromium.executable_path)
            return path if path.is_file() else None
    except Exception:
        return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check a migrated course-agent runtime.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail when machine-local credentials or browser authentication are not ready.",
    )
    return parser.parse_args()


def macos_keychain_has(service: str, account: str) -> bool:
    security = shutil.which("security")
    if not security:
        return False
    completed = subprocess.run(
        [
            security,
            "find-generic-password",
            "-w",
            "-s",
            service,
            "-a",
            account,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def main() -> int:
    args = parse_args()
    env_path = APP_ROOT / ".env"
    file_env = load_env_file(env_path)
    failures: list[str] = []
    warnings: list[str] = []
    notices: list[str] = []
    strict_failures: list[str] = []

    required_files = (
        APP_ROOT / "run_course_agent_app.py",
        APP_ROOT / "approval_server.py",
        APP_ROOT / "windows_office_pdf_worker.py",
        APP_ROOT / "requirements.txt",
        ROOT / "course_agent" / "schemas" / "course_task_intent.schema.json",
        ROOT / "course_agent" / "schemas" / "course_operation_manifest.schema.json",
        ROOT / "course_agent" / "schemas" / "course_agent_result.schema.json",
    )
    for path in required_files:
        if not path.is_file():
            failures.append(f"missing runtime file: {path.relative_to(ROOT)}")

    if sys.version_info < (3, 11):
        failures.append(f"Python 3.11+ required; found {sys.version.split()[0]}")
    for module in REQUIRED_MODULES:
        if importlib.util.find_spec(module) is None:
            failures.append(f"missing Python module: {module}")
    if os.name == "nt":
        for module in WINDOWS_REQUIRED_MODULES:
            if importlib.util.find_spec(module) is None:
                failures.append(f"missing Windows Python module: {module}")

    missing_reference = [
        name for name in REQUIRED_REFERENCE_FILES if not (REFERENCE_PACK / name).is_file()
    ]
    if missing_reference:
        warnings.append("reference pack incomplete: " + ", ".join(missing_reference))

    if not any(executable_responds(candidate, "--version") for candidate in codex_candidates(file_env)):
        failures.append("no runnable Codex executable was found")

    chrome_path = configured_path(file_env, "WHATSAPP_WEB_CHROME_PATH")
    if not chrome_path or not chrome_path.is_file():
        chrome_path = configured_path(file_env, "MOODLE_BROWSER_CHROME_PATH")
    if not chrome_path or not chrome_path.is_file():
        chrome_path = windows_chrome_path()
    if not chrome_path or not chrome_path.is_file():
        chrome_path = playwright_chromium_path()
    if not chrome_path or not chrome_path.is_file():
        failures.append("neither Chrome nor Playwright Chromium is installed")

    soffice_path = configured_path(file_env, "SOFFICE_BIN")
    if not soffice_path or not soffice_path.is_file():
        soffice_path = windows_soffice_path()
    office_registration = windows_office_registration()
    missing_office_apps = [name for name, available in office_registration.items() if not available]
    libreoffice_available = bool(soffice_path or shutil.which("soffice"))
    if not libreoffice_available and os.name == "nt" and not missing_office_apps:
        notices.append("Microsoft Office COM is the active DOCX/XLSX/PPTX PDF renderer")
    elif not libreoffice_available and os.name == "nt":
        strict_failures.append(
            "Office-to-PDF support is incomplete; install LibreOffice or Microsoft Word, Excel, and PowerPoint (missing: "
            + ", ".join(missing_office_apps)
            + ")"
        )
    elif not libreoffice_available:
        strict_failures.append("LibreOffice was not found; Office-to-PDF refresh operations are unavailable")
    if not env_path.is_file():
        warnings.append("course_agent_app/.env has not been created")
        strict_failures.append("course_agent_app/.env has not been created")

    key_present = bool(
        os.environ.get("DASE7137_KEY", "").strip()
        or file_env.get("DASE7137_KEY", "").strip()
    )
    if not key_present:
        warnings.append("DASE7137_KEY is not configured locally")
        strict_failures.append("DASE7137_KEY is not configured locally")

    whatsapp_profile = configured_app_path(
        file_env,
        "WHATSAPP_WEB_PROFILE_DIR",
        str(APP_ROOT / ".whatsapp_web_profile"),
    )
    if not whatsapp_profile or not whatsapp_profile.is_dir() or not any(whatsapp_profile.iterdir()):
        warnings.append("WhatsApp browser profile is new; QR authentication is required on first launch")

    moodle_profile = configured_app_path(
        file_env,
        "MOODLE_BROWSER_PROFILE_DIR",
        str(APP_ROOT / ".moodle_browser_profile"),
    )
    moodle_login_present = bool(
        os.environ.get("MOODLE_LOGIN_USERNAME", "").strip()
        or file_env.get("MOODLE_LOGIN_USERNAME", "").strip()
    ) and bool(
        os.environ.get("MOODLE_LOGIN_PASSWORD", "").strip()
        or file_env.get("MOODLE_LOGIN_PASSWORD", "").strip()
    )
    keychain_login_present = macos_keychain_has(
        "course-agent-moodle-username",
        "sandbox-105330",
    ) and macos_keychain_has(
        "course-agent-moodle-password",
        "sandbox-105330",
    )
    if (
        not (moodle_login_present or keychain_login_present)
        and (not moodle_profile or not moodle_profile.is_dir() or not any(moodle_profile.iterdir()))
    ):
        warnings.append("Moodle browser session/login is not configured on this machine")
        strict_failures.append("Moodle browser session/login is not configured on this machine")

    print(f"Runtime root: {ROOT}")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Reference pack: {'ready' if not missing_reference else 'incomplete'}")
    print(f"API key configured: {'yes' if key_present else 'no'}")
    for notice in notices:
        print(f"INFO: {notice}")
    for warning in warnings:
        print(f"WARNING: {warning}")
    for failure in failures:
        print(f"ERROR: {failure}")
    if args.strict:
        for failure in strict_failures:
            print(f"ERROR: {failure}")

    if failures:
        return 1
    if args.strict and strict_failures:
        return 2
    print("Preflight: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
