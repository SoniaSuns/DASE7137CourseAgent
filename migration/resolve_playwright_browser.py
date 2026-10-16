#!/usr/bin/env python3
"""Print the Playwright-managed Chromium executable path when available."""

from pathlib import Path

from playwright.sync_api import sync_playwright


with sync_playwright() as playwright:
    executable = Path(playwright.chromium.executable_path)
    if not executable.is_file():
        raise SystemExit(1)
    print(executable)
