from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from whatsapp_approval import approval_reply_command, parse_approval_command  # noqa: E402
from run_agent_smoke import ROOT, request_json, wait_for_server
from whatsapp_web import (  # noqa: E402,F401  re-exported for legacy callers
    WhatsAppSendUnconfirmedError,
    WhatsAppMessageRecord,
    first_visible,
    whatsapp_document_viewer_visible,
    close_whatsapp_document_viewer,
    wait_for_whatsapp_ready,
    select_all_shortcut,
    fill_editable_locator,
    chat_row_click_point,
    click_matching_list_item,
    click_chat_row,
    open_chat,
    dismiss_browser_overlays,
    compose_box,
    focus_compose_box,
    set_compose_text,
    normalize_multiline_text,
    whatsapp_safe_compose_text,
    semantic_multiline_lines,
    observed_multiline_candidates,
    insert_multiline_text,
    compose_visible_text,
    verify_compose_text,
    scroll_chat_to_bottom,
    _clear_editor,
    _insert_real_mention,
    set_action_message_text,
    multiline_message_matches,
    message_records,
    record_message_id,
)



try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass


TERMINAL_STATUSES = {
    "moodle_agent_checklist_pending",
    "moodle_browser_action_approval_pending",
    "moodle_agent_browser_action_pending",
    "moodle_external_action_verified",
    "moodle_external_action_blocked",
    "moodle_external_action_failed",
    "moodle_api_action_verified",
    "moodle_api_action_blocked",
    "moodle_api_action_failed",
    "course_agent_result_verified",
    "course_agent_result_blocked",
    "course_agent_result_failed",
    "dry_run_written",
    "moodle_skipped",
    "course_planning_approved",
    "rejected",
}

DEFAULT_CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"



def post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    timeout_seconds = 300 if url.rstrip("/").endswith("/agent/approval") else 10
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    env_path = ROOT / ".env"
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env.setdefault(key.strip(), value.strip())
    env["WHATSAPP_MODE"] = "codex_agent_web"
    env.setdefault("COURSE_AGENT_FREE_TASK_MODE", "0")
    env.setdefault("PORT", "8787")
    env.setdefault("LOCAL_BASE_URL", f"http://localhost:{env['PORT']}")
    env.setdefault("BATCH_ID", "SMOKE-MOODLE-WA-001")
    env.setdefault("COURSE_AGENT_WHATSAPP_CHAT_NAME", env.get("OPENCLAW_WHATSAPP_CHAT_NAME", "test approval"))
    env.setdefault("COURSE_AGENT_NAME", "whatsapp-web-automation")
    env.setdefault("WHATSAPP_WEB_AGENT_NAME", "whatsapp-web-automation")
    env.setdefault("AGENT_SINGLE_ACCOUNT_TEST_MODE", "0")
    env.setdefault("AGENT_REQUIRE_INCOMING_REPLY", "1")
    env.setdefault("REQUIRE_RUN_ID_IN_APPROVAL", "1")
    env.setdefault("MOODLE_EXECUTION_MODE", "agent_checklist")
    env.setdefault("OPEN_BROWSER", "0")
    env.setdefault("WHATSAPP_WEB_WAIT_SECONDS", "420")
    env.setdefault("WHATSAPP_WEB_PROFILE_DIR", str(ROOT / ".whatsapp_web_profile"))
    env.setdefault("WHATSAPP_WEB_DOWNLOAD_DIR", str(ROOT / "reports" / "whatsapp_web_downloads"))
    env.setdefault("WHATSAPP_WEB_CHROME_PATH", DEFAULT_CHROME_PATH)
    return env


def whatsapp_web_agent_name(env: dict[str, str]) -> str:
    return env.get("WHATSAPP_WEB_AGENT_NAME") or "whatsapp-web-automation"


def course_agent_chat_name(env: dict[str, str]) -> str:
    return env.get("COURSE_AGENT_WHATSAPP_CHAT_NAME") or env.get("OPENCLAW_WHATSAPP_CHAT_NAME") or "test approval"


def env_flag(env: dict[str, str], key: str, default: bool = False) -> bool:
    raw = env.get(key, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def env_int(env: dict[str, str], key: str, default: int) -> int:
    try:
        return int(env.get(key, str(default)))
    except ValueError:
        return default


def start_web_server(env: dict[str, str], base_url: str) -> subprocess.Popen | None:
    if wait_for_server(base_url, timeout=1.0):
        health = request_json(f"{base_url}/health")
        if health.get("mode") not in {"codex_agent_web", "openclaw_agent"}:
            raise RuntimeError(
                f"Port is already serving mode={health.get('mode')!r}. "
                "Stop that server or use another PORT before running WhatsApp Web automation."
            )
        print(f"Using existing approval server at {base_url}")
        return None

    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    log_path = reports / f"{env['BATCH_ID']}_whatsapp_web_server.log"
    log_handle = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "approval_server.py"],
        cwd=ROOT,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if not wait_for_server(base_url, timeout=15.0):
        process.terminate()
        raise RuntimeError(f"Approval server did not become healthy. See {log_path}")
    print(f"Started approval server at {base_url}")
    print(f"Server log: {log_path}")
    return process


def import_playwright():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Python Playwright is not installed. Run: python3 -m pip install playwright"
        ) from exc
    return sync_playwright, PlaywrightTimeoutError



def whatsapp_session_health(page) -> dict:
    try:
        health = page.evaluate(
            """
            () => {
              const visible = (el) => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0
                  && style.visibility !== 'hidden'
                  && style.display !== 'none';
              };
              const ready = [
                document.querySelector('#pane-side'),
                document.querySelector('[aria-label="Chat list"]'),
                document.querySelector('[data-testid="chat-list"]'),
              ].some(visible);
              const qr = Array.from(document.querySelectorAll(
                'canvas, [data-testid="qrcode"]'
              )).some(visible);
              return {
                authenticated: ready && !qr,
                qr_visible: qr,
                degraded_reason: qr
                  ? 'WhatsApp Web requires QR login.'
                  : (ready ? '' : 'WhatsApp Web chat list is not ready.'),
              };
            }
            """
        )
    except Exception as exc:
        return {
            "authenticated": False,
            "qr_visible": False,
            "degraded_reason": f"WhatsApp Web health check failed: {exc}",
        }
    return {
        "authenticated": bool((health or {}).get("authenticated")),
        "qr_visible": bool((health or {}).get("qr_visible")),
        "degraded_reason": str((health or {}).get("degraded_reason") or ""),
}



def discover_whatsapp_account_name(page) -> str:
    """Read the signed-in WhatsApp profile name without reusing a course-agent label."""
    selectors = [
        "header button[aria-label='Profile']",
        "header [data-testid='menu-bar-profile']",
        "header [title='Profile']",
        "[data-testid='menu-bar-profile']",
    ]
    trigger = first_visible(page, selectors, timeout_ms=1200)
    if not trigger:
        return ""
    try:
        trigger.click(timeout=3000, force=True)
        page.wait_for_timeout(500)
        value = page.evaluate(
            """
            () => {
              const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0
                  && style.visibility !== 'hidden'
                  && style.display !== 'none';
              };
              const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
              const isNameSemantic = (value) => (
                /(^|[-_\\s])(your[-_\\s]?)?(business[-_\\s]?|profile[-_\\s]?)?name($|[-_\\s])/i
                  .test(normalize(value))
              );
              const isGenericUiValue = (value) => /^(storefront|profile|business information|edit|name)$/i
                .test(normalize(value));
              const editors = Array.from(document.querySelectorAll('input, [contenteditable="true"]'))
                .filter(visible)
                .map((el) => {
                  const label = normalize([
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('aria-placeholder') || '',
                    el.getAttribute('data-testid') || '',
                    el.getAttribute('data-tab') || '',
                    el.getAttribute('title') || '',
                  ].join(' '));
                  const value = normalize(el.value || el.innerText || el.textContent || '');
                  return {
                    value,
                    explicitNameField: isNameSemantic(label),
                    unrelated: /search|message|caption|about/i.test(label),
                  };
                })
                .filter((item) => item.explicitNameField
                  && !item.unrelated
                  && item.value
                  && item.value.length <= 120
                  && !isGenericUiValue(item.value));
              if (editors[0]) return editors[0].value;

              const labels = Array.from(document.querySelectorAll('div, span, label'))
                .filter(visible)
                .filter((el) => /^(your name|business name|profile name|name)$/i.test(
                  normalize(el.innerText || el.textContent || '')
                ));
              for (const label of labels) {
                const row = label.parentElement && label.parentElement.parentElement;
                if (!row) continue;
                const values = Array.from(row.querySelectorAll('div, span, input, [contenteditable="true"]'))
                  .filter(visible)
                  .filter((el) => el.matches('input, [contenteditable="true"]') || el.children.length === 0)
                  .map((el) => normalize(el.value || el.innerText || el.textContent || ''))
                  .filter((text) => text
                    && text.length <= 120
                    && !isGenericUiValue(text)
                    && !/^(your name|business name|profile name|name)$/i.test(text))
                  .sort((left, right) => left.length - right.length);
                if (values[0]) return values[0];
              }
              return '';
            }
            """
        )
        return str(value or "").strip()
    finally:
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(250)
        except Exception:
            pass


def current_whatsapp_chat_context(page, agent_display_name: str = "") -> dict:
    context = page.evaluate(
        """
        () => {
          const messageIds = Array.from(document.querySelectorAll(
            '#main [data-id], #main .message-in, #main .message-out'
          )).map((el) => String(el.getAttribute('data-id') || ''));
          let chatType = 'unknown';
          if (messageIds.some((value) => value.includes('@g.us'))) chatType = 'group';
          else if (messageIds.some((value) => value.includes('@c.us'))) chatType = 'direct';
          if (chatType === 'unknown') {
            const header = document.querySelector('#main header');
            const text = String(header && (header.innerText || header.textContent) || '');
            const participantCue = /,\\s*[^,\\n]+/.test(text)
              || Array.from((header || document).querySelectorAll('[title], [aria-label]'))
                .some((el) => /group info|participants?/i.test(
                  String(el.getAttribute('title') || el.getAttribute('aria-label') || '')
                ));
            chatType = participantCue ? 'group' : 'direct';
          }
          return {chat_type: chatType};
        }
        """
    )
    return {
        "chat_type": str((context or {}).get("chat_type") or "unknown"),
        "agent_display_name": str(agent_display_name or "").strip(),
        "mention_ready": bool(agent_display_name) and str((context or {}).get("chat_type")) == "group",
    }



def ensure_chat_compose_ready(page, chat_name: str) -> None:
    close_whatsapp_document_viewer(page)
    try:
        compose_box(page)
        return
    except Exception:
        pass

    dismiss_browser_overlays(page)
    try:
        compose_box(page)
        return
    except Exception:
        pass

    page.reload(wait_until="domcontentloaded", timeout=60000)
    wait_for_whatsapp_ready(page, 60)
    dismiss_browser_overlays(page)
    open_chat(page, chat_name)
    compose_box(page)



def exact_mention_suggestion_count(page, mention_name: str) -> int:
    name = str(mention_name or "").strip().lstrip("@")
    if not name:
        return 0
    box = compose_box(page)
    _clear_editor(page, box)
    try:
        page.keyboard.insert_text(f"@{name}")
        page.wait_for_timeout(700)
        return int(
            page.evaluate(
                """
                (name) => {
                  const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0
                      && rect.bottom > window.innerHeight * 0.45
                      && style.visibility !== 'hidden'
                      && style.display !== 'none';
                  };
                  const normalize = (value) => String(value || '')
                    .replace(/^@/, '').trim().toLowerCase();
                  return Array.from(document.querySelectorAll(
                    '[role="listbox"] [role="option"], [data-testid*="mention"] [role="button"], '
                    + '[data-testid*="mention"] [role="listitem"], [role="listitem"], '
                    + '[data-testid="contact-mention-list-item"]'
                  )).filter(visible).filter((el) => {
                    const values = [
                      el.innerText || el.textContent || '',
                      el.getAttribute('title') || '',
                      el.getAttribute('aria-label') || '',
                      ...Array.from(el.querySelectorAll('[data-testid="mention-secondary"]'))
                        .map((node) => node.innerText || node.textContent || ''),
                    ].map(normalize);
                    return values.some((value) => value === normalize(name));
                  }).length;
                }
                """,
                name,
            )
        )
    finally:
        _clear_editor(page, box)



def send_message(
    page,
    message: str,
    *,
    mention_name: str = "",
    chat_type: str = "",
) -> dict:
    message = whatsapp_safe_compose_text(message)
    baseline_matches = outgoing_multiline_message_count(page, message)
    box = compose_box(page)
    focus_compose_box(page, box)
    delivery = set_action_message_text(page, box, message, mention_name, chat_type)
    click_send(page, coordinate_fallback=True)
    scroll_chat_to_bottom(page)
    deadline = time.time() + 12
    while time.time() < deadline:
        if outgoing_multiline_message_count(page, message) > baseline_matches:
            return delivery
        page.wait_for_timeout(500)
    raise WhatsAppSendUnconfirmedError(
        "WhatsApp send control was used, but the expected multiline outgoing message was not visible."
    )


def outgoing_multiline_message_count(page, expected: str) -> int:
    expected_text = normalize_multiline_text(expected)
    if not expected_text:
        return 0
    count = 0
    for record in message_records(page):
        if record_direction(record) != "outgoing":
            continue
        if multiline_message_matches(str(record.get("text") or ""), expected_text):
            count += 1
    return count



def click_send(page, *, coordinate_fallback: bool = False) -> None:
    send_button = first_visible(
        page,
        [
            "button[aria-label='Send']",
            "button[aria-label='Send message']",
            "div[aria-label='Send']",
            "[data-testid='send']",
            "button span[data-icon='send']",
            "span[data-icon='send']",
            "span[data-icon='send-filled']",
            "span[data-icon='wds-ic-send-filled']",
        ],
        timeout_ms=1000,
    )
    if send_button:
        try:
            send_button.click(timeout=5000)
            return
        except Exception:
            try:
                send_button.click(timeout=5000, force=True)
                return
            except Exception:
                if not coordinate_fallback:
                    raise
    if coordinate_fallback:
        viewport = page.viewport_size or {"width": 1400, "height": 900}
        page.mouse.click(viewport["width"] - 46, viewport["height"] - 45)
    else:
        page.keyboard.press("Enter")


def approval_attachment_paths(start_result: dict) -> list[Path]:
    paths: list[Path] = []
    missing: list[str] = []
    for attachment in start_result.get("approval_attachments", []) or []:
        path_text = str(attachment.get("path", "")).strip()
        if not path_text:
            continue
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists():
            missing.append(f"{attachment.get('label', 'Approval attachment')}: {path}")
            continue
        paths.append(path)
    if missing:
        raise FileNotFoundError("Missing WhatsApp approval attachment(s): " + "; ".join(missing))
    return paths


def paste_text_into_locator(page, locator, text: str) -> None:
    locator.click()
    page.keyboard.press(select_all_shortcut())
    page.keyboard.press("Backspace")
    page.wait_for_timeout(200)
    fill_editable_locator(locator, text)
    page.wait_for_timeout(700)


def save_whatsapp_diagnostic(page, label: str) -> Path | None:
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_") or "whatsapp"
    path = reports / f"{int(time.time())}_{safe_label}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        return path
    except Exception:
        return None


def document_file_input(page):
    inputs = page.locator("input[type='file']")
    count = inputs.count()
    best = None
    best_score = 99
    for index in range(count):
        candidate = inputs.nth(index)
        accept = (candidate.get_attribute("accept") or "").lower()
        multiple = candidate.get_attribute("multiple") is not None
        if "image/" in accept and "application" not in accept and "*" not in accept:
            score = 50
        elif "*" in accept and multiple:
            score = 0
        elif "*" in accept or not accept:
            score = 1
        elif "application" in accept or ".pdf" in accept or ".doc" in accept or "text/" in accept:
            score = 2
        else:
            score = 10
        if score < best_score:
            best = candidate
            best_score = score
    return best if best_score < 50 else None


def document_menu_click_point(page) -> tuple[float, float] | None:
    point = page.evaluate(
        """
        () => {
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            return rect.width > 0 && rect.height > 0
              && style.visibility !== 'hidden'
              && style.display !== 'none';
          };
          const candidates = Array.from(document.querySelectorAll('span, div, button, [role="button"]'))
            .filter(visible)
            .filter((el) => ((el.innerText || el.textContent || '').trim() === 'Document'));
          candidates.sort((a, b) => {
            const ar = a.getBoundingClientRect();
            const br = b.getBoundingClientRect();
            return (ar.width * ar.height) - (br.width * br.height);
          });
          const label = candidates[0];
          if (!label) return null;
          let target = label;
          for (let current = label; current; current = current.parentElement) {
            const rect = current.getBoundingClientRect();
            if (rect.width >= 120 && rect.height >= 28 && rect.height <= 80) {
              target = current;
              break;
            }
          }
          const rect = target.getBoundingClientRect();
          return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
        }
        """
    )
    if not point:
        return None
    return float(point["x"]), float(point["y"])


def attach_documents(page, paths: list[Path]) -> None:
    scroll_chat_to_bottom(page)
    attach_button = first_visible(
        page,
        [
            "button[aria-label='Attach']",
            "button[title='Attach']",
            "div[aria-label='Attach']",
            "span[data-icon='clip']",
            "span[data-icon='plus']",
            "span[data-icon='plus-rounded']",
            "span[data-icon='attach-menu-plus']",
        ],
        timeout_ms=5000,
    )
    if not attach_button:
        raise RuntimeError("Could not find the WhatsApp attachment button.")
    attach_button.click()
    page.wait_for_timeout(700)

    click_point = document_menu_click_point(page)
    if click_point:
        try:
            with page.expect_file_chooser(timeout=10000) as chooser_info:
                page.mouse.click(click_point[0], click_point[1])
            chooser_info.value.set_files([str(path) for path in paths])
            return
        except Exception:
            page.mouse.click(click_point[0], click_point[1])
            page.wait_for_timeout(700)
            file_input = document_file_input(page)
            if file_input:
                file_input.set_input_files([str(path) for path in paths])
                return
            raise

    document_option = first_visible(
        page,
        [
            "div[role='button']:not([data-testid='document-thumb']):has-text('Document')",
            "li[role='button']:not([data-testid='document-thumb']):has-text('Document')",
            "span:not([data-testid='document-thumb']):has-text('Document')",
        ],
        timeout_ms=5000,
    )
    if document_option:
        try:
            with page.expect_file_chooser(timeout=5000) as chooser_info:
                document_option.click()
            chooser_info.value.set_files([str(path) for path in paths])
            return
        except Exception:
            document_option.click()
            page.wait_for_timeout(700)
            file_input = document_file_input(page)
            if file_input:
                file_input.set_input_files([str(path) for path in paths])
                return
            raise

    file_input = document_file_input(page)
    if file_input:
        file_input.set_input_files([str(path) for path in paths])
        return

    click_point = document_menu_click_point(page)
    if click_point:
        with page.expect_file_chooser(timeout=10000) as chooser_info:
            page.mouse.click(click_point[0], click_point[1])
        chooser_info.value.set_files([str(path) for path in paths])
        return

    raise RuntimeError("Could not find the WhatsApp document attachment option.")


def attachment_preview_visible(page, paths: list[Path]) -> bool:
    names = [path.name for path in paths]
    if not names:
        return False
    return bool(
        page.evaluate(
            """
            (names) => {
              const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0
                  && style.visibility !== 'hidden'
                  && style.display !== 'none';
              };
              const rightPanelNodes = Array.from(document.querySelectorAll('span, div, button, [role="button"]'))
                .filter(visible)
                .filter((el) => el.getBoundingClientRect().x > 560);
              const rightPanelText = rightPanelNodes
                .map((el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim())
                .filter(Boolean)
                .join('\\n');
              const hasAllNames = names.every((name) => rightPanelText.includes(name));
              const hasAnyName = names.some((name) => rightPanelText.includes(name));
              const captionBox = Array.from(document.querySelectorAll('div[contenteditable="true"][role="textbox"], div[contenteditable="true"]'))
                .filter(visible)
                .some((el) => {
                  const rect = el.getBoundingClientRect();
                  const label = [
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('aria-placeholder') || '',
                    el.getAttribute('data-testid') || ''
                  ].join(' ').toLowerCase();
                  return rect.x > 560
                    && rect.y > window.innerHeight - 160
                    && (label.includes('message') || label.includes('caption') || label.includes('media-caption'));
                });
              const selectedSend = Array.from(document.querySelectorAll('[aria-label^="Send"][aria-label*="selected"]'))
                .some(visible);
              const filledSend = Array.from(document.querySelectorAll('span[data-icon="wds-ic-send-filled"], span[data-testid="wds-ic-send-filled"]'))
                .some((el) => {
                  if (!visible(el)) return false;
                  const rect = el.getBoundingClientRect();
                  return rect.x > window.innerWidth - 160 && rect.y > window.innerHeight - 160;
                });
              const bottomThumb = rightPanelNodes.some((el) => {
                const rect = el.getBoundingClientRect();
                const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                return rect.y > window.innerHeight - 140 && names.some((name) => text.includes(name));
              });
              const pdfPreview = rightPanelText.includes('Powered by Adobe Acrobat');
              return (hasAllNames || hasAnyName) && (selectedSend || filledSend || bottomThumb || pdfPreview || captionBox);
            }
            """,
            names,
        )
    )


def wait_for_attachment_preview(page, paths: list[Path], timeout_ms: int = 20000) -> None:
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        if attachment_preview_visible(page, paths):
            return
        page.wait_for_timeout(500)
    names = ", ".join(path.name for path in paths)
    raise RuntimeError(f"WhatsApp attachment preview did not open for: {names}")


def preview_send_button(page, timeout_ms: int = 5000):
    return first_visible(
        page,
        [
            "div[role='button'][aria-label^='Send'][aria-label*='selected']",
            "button[aria-label^='Send'][aria-label*='selected']",
            "div[role='button'][aria-label='Send 1 selected']",
            "span[data-icon='wds-ic-send-filled']",
            "span[data-testid='wds-ic-send-filled']",
        ],
        timeout_ms=timeout_ms,
    )


def preview_caption_box(page):
    direct = first_visible(
        page,
        [
            "div[data-testid='media-caption-input-container'][contenteditable='true']",
            "div[contenteditable='true'][role='textbox'][aria-placeholder='Type a message']",
            "div[contenteditable='true'][role='textbox'][aria-label='Type a message']",
            "div[contenteditable='true'][aria-placeholder*='message']",
            "div[contenteditable='true'][aria-label*='message']",
        ],
        timeout_ms=5000,
    )
    if direct:
        return direct

    marker = f"course-agent-caption-{int(time.time() * 1000)}"
    found = page.evaluate(
        """
        (marker) => {
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            return rect.width > 0 && rect.height > 0
              && style.visibility !== 'hidden'
              && style.display !== 'none';
          };
          const candidates = Array.from(document.querySelectorAll('div[contenteditable="true"]'))
            .filter(visible)
            .map((el) => {
              const rect = el.getBoundingClientRect();
              const label = [
                el.getAttribute('aria-label') || '',
                el.getAttribute('aria-placeholder') || '',
                el.getAttribute('data-testid') || '',
                el.parentElement ? (el.parentElement.getAttribute('data-testid') || '') : ''
              ].join(' ').toLowerCase();
              const rightPanel = rect.x > 560;
              const nearBottom = rect.y > window.innerHeight - 190;
              const looksLikeCaption = label.includes('message')
                || label.includes('caption')
                || label.includes('media-caption');
              const score = (rightPanel ? 10 : 0)
                + (nearBottom ? 10 : 0)
                + (looksLikeCaption ? 5 : 0)
                + Math.max(0, 200 - Math.abs((rect.x + rect.width) - window.innerWidth)) / 100;
              return {el, score};
            })
            .sort((a, b) => b.score - a.score);
          const best = candidates[0];
          if (!best || best.score < 15) return false;
          best.el.setAttribute('data-course-agent-caption-target', marker);
          return true;
        }
        """,
        marker,
    )
    if found:
        locator = page.locator(f"[data-course-agent-caption-target='{marker}']").first
        locator.wait_for(state="visible", timeout=2000)
        return locator
    raise RuntimeError("Could not find the WhatsApp attachment caption box.")


def set_preview_caption(
    page,
    message: str,
    run_id: str,
    approval_code: str = "",
    *,
    mention_name: str = "",
    chat_type: str = "",
) -> dict:
    box = preview_caption_box(page)
    delivery = set_action_message_text(page, box, message, mention_name, chat_type)
    deadline = time.time() + 10
    latest = ""
    token = approval_code or run_id
    while time.time() < deadline:
        latest = normalize_message_text(box.inner_text(timeout=1000))
        if approval_code:
            expected_command_markers = [
                f"OK {token}",
            ]
            if token in latest and any(marker in latest for marker in expected_command_markers):
                return delivery
        elif token in latest:
            return delivery
        page.wait_for_timeout(300)
    raise RuntimeError(
        "WhatsApp attachment caption was not populated with the approval text; "
        f"latest caption text={latest!r}"
    )


def wait_for_attachment_preview_to_close(page, timeout_ms: int = 15000) -> None:
    try:
        page.locator("div[role='button'][aria-label^='Send'][aria-label*='selected']").first.wait_for(
            state="hidden",
            timeout=timeout_ms,
        )
    except Exception:
        return


def wait_for_attachment_counts(
    page,
    paths: list[Path],
    baseline_attachment_counts: dict[str, int],
    timeout_ms: int = 45000,
) -> None:
    deadline = time.time() + timeout_ms / 1000
    names = [path.name for path in paths]
    latest = {name: 0 for name in names}
    stable_polls = 0
    while time.time() < deadline:
        scroll_chat_to_bottom(page)
        latest = {name: document_attachment_count(page, name) for name in names}
        if all(latest.get(name, 0) > baseline_attachment_counts.get(name, 0) for name in names):
            stable_polls += 1
            if stable_polls >= 3:
                return
        else:
            stable_polls = 0
        page.wait_for_timeout(1500)
    raise RuntimeError(
        "WhatsApp attachment send could not be verified before sending approval text; "
        f"attachment counts: before={baseline_attachment_counts!r}, after={latest!r}"
    )


def send_message_with_attachments(
    page,
    message: str,
    paths: list[Path],
    *,
    run_id: str,
    approval_code: str = "",
    mention_name: str = "",
    chat_type: str = "",
    manual_attachment_send_seconds: float = 0,
) -> dict:
    message = whatsapp_safe_compose_text(message)
    if manual_attachment_send_seconds > 0:
        raise RuntimeError(
            "Manual WhatsApp attachment sending is disabled for this project. "
            "Fix the browser automation path instead of asking a human to click send."
        )
    if not paths:
        return send_message(page, message, mention_name=mention_name, chat_type=chat_type)

    attach_documents(page, paths)
    delivery: dict = {"mention_mode": "none", "mention_target": ""}
    try:
        wait_for_attachment_preview(page, paths)
        delivery = set_preview_caption(
            page,
            message,
            run_id,
            approval_code,
            mention_name=mention_name,
            chat_type=chat_type,
        )
    except Exception as exc:
        diagnostic = save_whatsapp_diagnostic(page, f"whatsapp_attachment_compose_failed_{run_id}")
        suffix = f" Diagnostic screenshot: {diagnostic}" if diagnostic else ""
        raise RuntimeError(f"Could not automatically compose WhatsApp approval attachment.{suffix}") from exc
    try:
        click_preview_send(page, paths)
        wait_for_attachment_preview_to_close(page)
    except Exception as exc:
        diagnostic = save_whatsapp_diagnostic(page, f"whatsapp_attachment_send_failed_{run_id}")
        suffix = f" Diagnostic screenshot: {diagnostic}" if diagnostic else ""
        raise RuntimeError(f"Could not automatically send WhatsApp approval attachment.{suffix}") from exc
    scroll_chat_to_bottom(page)
    page.wait_for_timeout(8000)
    return delivery


def click_preview_send(page, paths: list[Path] | None = None) -> None:
    if paths and not attachment_preview_visible(page, paths):
        names = ", ".join(path.name for path in paths)
        raise RuntimeError(f"Refusing to click attachment send before preview is visible for: {names}")
    point = page.evaluate(
        """
        () => {
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            return rect.width > 0 && rect.height > 0
              && style.visibility !== 'hidden'
              && style.display !== 'none';
          };
          const explicit = Array.from(document.querySelectorAll('[aria-label^="Send"][aria-label*="selected"]'))
            .filter(visible);
          const candidates = explicit.length
            ? explicit
            : Array.from(document.querySelectorAll('span[data-icon="wds-ic-send-filled"], span[data-testid="wds-ic-send-filled"]'))
                .filter(visible)
                .filter((el) => {
                  const rect = el.getBoundingClientRect();
                  return rect.x > window.innerWidth - 180 && rect.y > window.innerHeight - 180;
                });
          for (const candidate of candidates.reverse()) {
            let target = candidate;
            for (let current = candidate; current; current = current.parentElement) {
              const rect = current.getBoundingClientRect();
              if (rect.width >= 40 && rect.width <= 90 && rect.height >= 40 && rect.height <= 90) {
                target = current;
              }
              if ((current.getAttribute('role') || '') === 'button') {
                target = current;
                break;
              }
            }
            const rect = target.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
              return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
            }
          }
          return null;
        }
        """
    )
    if point:
        page.mouse.click(float(point["x"]), float(point["y"]))
        return
    viewport = page.viewport_size or {"width": 1400, "height": 900}
    page.mouse.click(viewport["width"] - 46, viewport["height"] - 45)



def record_key(record: dict) -> str:
    message_id = str(record.get("message_id") or record.get("data_id") or "").strip()
    if message_id:
        return f"message_id:{message_id}"
    documents = "\n".join(str(item) for item in record.get("documents", []) or [])
    meta = str(record.get("meta") or "")
    if meta:
        return f"{meta}\n{record.get('text', '')}\n{documents}"
    identity = "|".join(
        str(record.get(key) or "")
        for key in ("source", "data_id", "index")
    )
    global_indices = ",".join(str(item) for item in record.get("document_global_indices", []) or [])
    return f"{identity}\n{global_indices}\n{record.get('text', '')}\n{documents}"


def record_direction(record: dict) -> str:
    direction = str(record.get("direction") or "").strip().lower()
    if direction in {"incoming", "outgoing"}:
        return direction
    fallback = direction_from_meta(str(record.get("meta") or ""))
    return "outgoing" if fallback == "outgoing_self_test" else "incoming"


def record_sender_display_name(record: dict) -> str:
    sender = str(record.get("sender_display_name") or "").strip()
    if sender:
        return sender
    return sender_from_meta(str(record.get("meta") or ""))



def record_has_real_mention(record: dict, display_name: str) -> bool:
    target = str(display_name or "").strip().lstrip("@").casefold()
    if not target:
        return False
    for mention in record.get("mentions") or []:
        if not isinstance(mention, dict):
            continue
        label = str(mention.get("label") or "").strip().lstrip("@").casefold()
        if label == target:
            return True
    return False


def strip_record_mentions(text: str, record: dict) -> str:
    cleaned = str(text or "")
    labels = []
    for mention in record.get("mentions") or []:
        if not isinstance(mention, dict):
            continue
        label = str(mention.get("label") or "").strip().lstrip("@")
        if label:
            labels.append(label)
    for label in sorted(labels, key=len, reverse=True):
        cleaned = re.sub(rf"(?<!\S)@{re.escape(label)}(?=\s|$)", "", cleaned, flags=re.IGNORECASE)
    return normalize_message_text(cleaned)


def record_visible_text(record: dict) -> str:
    documents = "\n".join(str(item) for item in record.get("documents", []) or [])
    return f"{record.get('text', '')}\n{documents}".replace("\u200e", "")


def record_contains_approval_request(record: dict, approval_code: str, run_id: str = "") -> bool:
    token = str(approval_code or run_id or "").strip()
    if not token:
        return False
    text = record_visible_text(record)
    compact = re.sub(r"\s+", " ", text).strip()
    has_commands = f"OK {token}" in compact and f"NO {token}" in compact
    if not has_commands:
        return False
    has_approval_intro = "Please approve this Moodle change" in compact or "Moodle approval" in compact
    has_reply_marker = "Reply" in compact
    has_moodle_context = "Moodle" in compact and ("Course:" in compact or "course" in compact.lower())
    return has_approval_intro and has_reply_marker and has_moodle_context


def record_contains_collapsed_approval_request(record: dict) -> bool:
    compact = re.sub(r"\s+", " ", record_visible_text(record)).strip()
    return bool(
        ("Please approve this Moodle change" in compact or "Moodle approval" in compact)
        and "Read more" in compact
        and "Your request:" in compact
        and ("This approval covers:" in compact or "Scope:" in compact)
        and "Moodle" in compact
    )


def expand_collapsed_approval_requests(page) -> int:
    expanded = int(
        page.evaluate(
            """
            () => {
              const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
              let clicked = 0;
              const roots = Array.from(document.querySelectorAll('.message-out, [data-id]'));
              for (const root of roots) {
                const text = normalize(root.innerText || root.textContent || '');
                if (!text.includes('Please approve this Moodle change')
                    && !text.includes('Moodle approval')) continue;
                const controls = Array.from(root.querySelectorAll(
                  'button, [role="button"], span'
                ));
                const readMore = controls.find((control) =>
                  /^Read more$/i.test(normalize(control.innerText || control.textContent || ''))
                );
                if (!readMore) continue;
                readMore.click();
                clicked += 1;
              }
              return clicked;
            }
            """
        )
    )
    if expanded:
        page.wait_for_timeout(300)
    return expanded


def document_attachment_count(page, filename: str) -> int:
    return int(
        page.evaluate(
            """
            (filename) => {
              const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0
                  && rect.x > 484
                  && style.visibility !== 'hidden'
                  && style.display !== 'none';
              };
              return Array.from(document.querySelectorAll('[data-testid="document-thumb"]'))
                .filter(visible)
                .filter((el) => {
                  const message = el.closest('[data-testid^="conv-msg-"]');
                  return Boolean(message && message.closest('[role="row"]'));
                })
                .filter((el) => {
                  const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                  const title = el.getAttribute('title') || '';
                  return text.includes(filename) || title.includes(filename);
                }).length;
            }
            """,
            filename,
        )
    )


def document_attachment_counts(page, attachment_paths: list[Path]) -> dict[str, int]:
    return {path.name: document_attachment_count(page, path.name) for path in attachment_paths}


def captioned_document_message_seen(page, run_id: str, filenames: list[str], approval_code: str = "") -> bool:
    if not filenames:
        return False
    token = approval_code or run_id
    return bool(
        page.evaluate(
            """
            ({runId, filenames}) => {
              const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0
                  && rect.x > 484
                  && style.visibility !== 'hidden'
                  && style.display !== 'none';
              };
              const thumbs = Array.from(document.querySelectorAll('[data-testid="document-thumb"]'))
                .filter(visible)
                .filter((el) => {
                  const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                  const title = el.getAttribute('title') || '';
                  return filenames.some((name) => text.includes(name) || title.includes(name));
                });
              for (const thumb of thumbs) {
                let current = thumb;
                for (let depth = 0; current && depth < 12; depth++, current = current.parentElement) {
                  const text = (current.innerText || current.textContent || '')
                    .replace(/\\u200e/g, '')
                    .replace(/\\s+/g, ' ')
                    .trim();
                  if (!text.includes(runId)) continue;
                  if (filenames.every((name) => text.includes(name))) return true;
                }
              }
              return false;
            }
            """,
            {"runId": token, "filenames": filenames},
        )
    )


def wait_for_sent_request(
    page,
    run_id: str,
    attachment_paths: list[Path],
    baseline_keys: set[str],
    baseline_attachment_counts: dict[str, int] | None = None,
    *,
    approval_code: str = "",
    require_attachment_verification: bool = True,
    require_single_caption_message: bool = True,
) -> set[str]:
    token = approval_code or run_id
    expected_names = [path.name for path in attachment_paths]
    baseline_attachment_counts = baseline_attachment_counts or {name: 0 for name in expected_names}
    deadline = time.time() + 45
    last_new_text = ""
    last_attachment_text = ""
    last_attachment_counts: dict[str, int] = {}
    single_caption_seen = False
    while time.time() < deadline:
        scroll_chat_to_bottom(page)
        expand_collapsed_approval_requests(page)
        records = message_records(page)
        new_records = [record for record in records if record_key(record) not in baseline_keys]
        combined = "\n".join(record_visible_text(record) for record in new_records)
        last_new_text = combined[-1000:]
        approval_records = [
            record
            for record in new_records
            if (
                record_contains_approval_request(record, approval_code, run_id)
                or record_contains_collapsed_approval_request(record)
            )
        ]
        text_seen = bool(approval_records)
        attachment_records = [
            record_visible_text(record)
            for record in new_records
            if "CCHU9056 approval" not in record.get("text", "")
            and "review attachment:" not in record.get("text", "").lower()
        ]
        last_attachment_text = "\n".join(attachment_records)[-1000:]
        attachments_seen = all(
            any(name in record_text for record_text in attachment_records)
            for name in expected_names
        )
        if expected_names:
            last_attachment_counts = {
                name: document_attachment_count(page, name)
                for name in expected_names
            }
            attachments_seen = all(
                last_attachment_counts.get(name, 0) > baseline_attachment_counts.get(name, 0)
                for name in expected_names
            )
            single_caption_seen = any(
                (
                    record_contains_approval_request(record, approval_code, run_id)
                    or record_contains_collapsed_approval_request(record)
                )
                and all(
                    name in record_visible_text(record)
                    or any(name in document_text for document_text in record.get("documents", []))
                    for name in expected_names
                )
                for record in new_records
            )
            text_seen = text_seen or single_caption_seen
        if (
            text_seen
            and (attachments_seen or not require_attachment_verification)
            and (not expected_names or not require_single_caption_message or single_caption_seen)
        ):
            return {record_key(record) for record in records}
        page.wait_for_timeout(2000)
    missing = []
    if f"OK {token}" not in last_new_text or f"NO {token}" not in last_new_text:
        missing.append(f"approval commands for {token}")
    if "Please approve this Moodle change" not in last_new_text and "Moodle approval" not in last_new_text:
        missing.append("approval request body")
    if require_attachment_verification:
        for name in expected_names:
            if last_attachment_counts.get(name, 0) <= baseline_attachment_counts.get(name, 0):
                missing.append(f"attachment {name}")
    if expected_names and require_single_caption_message and not single_caption_seen:
        missing.append("single attachment caption message containing the approval text")
    raise RuntimeError(
        "WhatsApp send could not be verified in chat history; missing "
        + ", ".join(missing)
        + (
            f". Last new message text: {last_new_text!r}; last attachment-only text: {last_attachment_text!r}; "
            f"attachment counts: before={baseline_attachment_counts!r}, after={last_attachment_counts!r}"
        )
    )


def normalize_message_text(text: str) -> str:
    lines = [line.strip() for line in text.replace("\u200e", "").splitlines() if line.strip()]
    return "\n".join(lines).strip()


def matching_command(text: str, batch_id: str, run_id: str, approval_code: str = "") -> str | None:
    normalized = normalize_message_text(text)
    parsed = parse_approval_command(normalized, batch_id)
    valid_ids = {run_id}
    if approval_code:
        valid_ids.add(approval_code)
    if not parsed or parsed.get("run_id") not in valid_ids:
        return None
    command = parsed.get("command")
    note = parsed.get("note", "")
    if command == "APPROVE" and not note:
        return normalized
    if command == "REJECT" and not note:
        return normalized
    return None


def approval_command_from_record(
    record: dict,
    batch_id: str,
    run_id: str,
    approval_code: str = "",
) -> str | None:
    """Extract one exact approval command without scanning arbitrary message lines."""
    text = strip_record_mentions(
        str(record.get("message_text") or record.get("text") or ""),
        record,
    )
    direct_match = matching_command(text, batch_id, run_id, approval_code)
    if direct_match:
        return direct_match

    metadata_values = {
        normalize_message_text(record.get("sender_display_name", "")).casefold(),
        normalize_message_text(record.get("sender_id", "")).casefold(),
    }
    metadata_values.discard("")
    body_lines: list[str] = []
    for line in text.splitlines():
        normalized = normalize_message_text(line)
        if not normalized:
            continue
        if normalized.casefold() in metadata_values:
            continue
        if re.fullmatch(r"\+?\d[\d\s().-]{6,}\d", normalized):
            continue
        if re.fullmatch(
            r"(?:1[0-2]|0?[1-9]):[0-5]\d\s*[AP]M(?:\s*[✓✔]+)?",
            normalized,
            flags=re.IGNORECASE,
        ):
            continue
        body_lines.append(normalized)

    # Requiring exactly one non-metadata line keeps quoted/replied-to messages
    # from being searched for an approval token.
    if len(body_lines) != 1:
        return None
    return matching_command(body_lines[0], batch_id, run_id, approval_code)


def sender_from_meta(meta: str) -> str:
    match = re.search(r"\]\s*(.*?):\s*$", meta)
    if match:
        return match.group(1).strip()
    return meta.strip() or "unknown"


def direction_from_meta(meta: str) -> str:
    sender = sender_from_meta(meta).lower()
    if sender in {"you", "me"} or " you:" in meta.lower():
        return "outgoing_self_test"
    return "incoming"


def wait_for_approval_reply(
    page,
    env: dict[str, str],
    run_id: str,
    baseline_keys: set[str],
    approval_code: str = "",
    *,
    expected_requester: str = "",
    chat_type: str = "",
    agent_display_name: str = "",
) -> dict:
    batch_id = env["BATCH_ID"]
    wait_seconds = float(env.get("WHATSAPP_WEB_WAIT_SECONDS", "420"))
    deadline = time.time() + wait_seconds
    print(f"Waiting up to {wait_seconds:g}s for current-run WhatsApp approval...")
    print(f"Expected: {approval_reply_command('APPROVE', batch_id, run_id, approval_code=approval_code)}")
    while time.time() < deadline:
        for record in message_records(page):
            if record_key(record) in baseline_keys:
                continue
            direction = record_direction(record)
            single_account = env_flag(env, "AGENT_SINGLE_ACCOUNT_TEST_MODE", False)
            if direction != "incoming" and not (single_account and direction == "outgoing"):
                continue
            sender = record_sender_display_name(record)
            if expected_requester and sender.casefold() != expected_requester.casefold():
                continue
            effective_chat_type = str(record.get("chat_type") or chat_type or "").strip().lower()
            mentioned = record_has_real_mention(record, agent_display_name)
            if effective_chat_type == "group" and not mentioned and not expected_requester:
                continue
            message_id = record_message_id(record)
            if not message_id and not single_account:
                continue
            command = approval_command_from_record(
                record,
                batch_id,
                run_id,
                approval_code,
            )
            if not command:
                continue
            return {
                "agent_name": whatsapp_web_agent_name(env),
                "source_channel": "whatsapp_web_automation",
                "chat_name": env.get("COURSE_AGENT_WHATSAPP_CHAT_NAME", env.get("OPENCLAW_WHATSAPP_CHAT_NAME", "")),
                "observed_direction": (
                    "outgoing_self_test" if direction == "outgoing" else "incoming"
                ),
                "run_id": run_id,
                "whatsapp_sender": sender,
                "profile_name": sender,
                "message_id": message_id,
                "chat_type": effective_chat_type,
                "agent_mentioned": (
                    effective_chat_type != "group"
                    or mentioned
                ),
                "body": command,
                "evidence": "WhatsApp Web automation observed a new message after the approval request.",
            }
        time.sleep(2)
    raise TimeoutError(f"No current-run approval command appeared within {wait_seconds:g}s.")


def post_progress(base_url: str, env: dict[str, str], run_id: str, event: str, **extra) -> dict:
    payload = {
        "event": event,
        "run_id": run_id,
        "agent_name": whatsapp_web_agent_name(env),
        "chat_name": env.get("COURSE_AGENT_WHATSAPP_CHAT_NAME", env.get("OPENCLAW_WHATSAPP_CHAT_NAME", "")),
    }
    payload.update(extra)
    return post_json(f"{base_url}/agent/progress", payload)


def wait_for_terminal_status(base_url: str, run_id: str, timeout_seconds: float = 20.0) -> dict:
    deadline = time.time() + timeout_seconds
    last_status = {}
    while time.time() < deadline:
        last_status = request_json(f"{base_url}/status")
        if last_status.get("run_id") == run_id and last_status.get("status") in TERMINAL_STATUSES:
            return last_status
        time.sleep(1)
    return last_status


def launch_whatsapp_context(playwright, env: dict[str, str]):
    profile_dir = Path(env["WHATSAPP_WEB_PROFILE_DIR"]).expanduser()
    if not profile_dir.is_absolute():
        profile_dir = ROOT / profile_dir
    profile_dir.mkdir(parents=True, exist_ok=True)
    download_dir = Path(env.get("WHATSAPP_WEB_DOWNLOAD_DIR") or (ROOT / "reports" / "whatsapp_web_downloads")).expanduser()
    if not download_dir.is_absolute():
        download_dir = ROOT / download_dir
    download_dir.mkdir(parents=True, exist_ok=True)
    chrome_path = env.get("WHATSAPP_WEB_CHROME_PATH", DEFAULT_CHROME_PATH)
    launch_kwargs = {
        "user_data_dir": str(profile_dir),
        "headless": False,
        "accept_downloads": True,
        "downloads_path": str(download_dir),
        # Keep the page viewport below the visible Chrome content area. If this
        # is taller than the real window, WhatsApp pins the compose footer below
        # what the operator can see.
        "viewport": {"width": 1280, "height": 720},
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--disable-session-crashed-bubble",
            "--hide-crash-restore-bubble",
            "--window-size=1400,900",
        ],
    }
    if chrome_path and Path(chrome_path).exists():
        launch_kwargs["executable_path"] = chrome_path
    return playwright.chromium.launch_persistent_context(**launch_kwargs)


def run_whatsapp_web_approval(
    env: dict[str, str],
    base_url: str,
    start_result: dict,
    *,
    no_send: bool = False,
    keep_open: bool = False,
    manual_attachment_send_seconds: float = 0,
    context=None,
) -> dict:
    if context is not None:
        page = context.pages[0] if context.pages else context.new_page()
        return run_whatsapp_web_approval_on_page(
            page,
            env,
            base_url,
            start_result,
            no_send=no_send,
            manual_attachment_send_seconds=manual_attachment_send_seconds,
        )

    sync_playwright, _ = import_playwright()
    with sync_playwright() as playwright:
        owned_context = launch_whatsapp_context(playwright, env)
        try:
            page = owned_context.pages[0] if owned_context.pages else owned_context.new_page()
            return run_whatsapp_web_approval_on_page(
                page,
                env,
                base_url,
                start_result,
                no_send=no_send,
                manual_attachment_send_seconds=manual_attachment_send_seconds,
            )
        finally:
            if not keep_open:
                owned_context.close()


def run_whatsapp_web_approval_on_page(
    page,
    env: dict[str, str],
    base_url: str,
    start_result: dict,
    *,
    no_send: bool = False,
    manual_attachment_send_seconds: float = 0,
) -> dict:
    run_id = start_result["run_id"]
    approval_code = str(
        start_result.get("approval_code")
        or (start_result.get("state") or {}).get("approval_code")
        or ""
    ).strip()
    approval_text = start_result["approval_message"]
    attachments = approval_attachment_paths(start_result)
    print("\n=== WhatsApp Web deterministic approval ===")
    print(f"Run id: {run_id}")
    if approval_code:
        print(f"Approval code: {approval_code}")
    chat_name = course_agent_chat_name(env)
    print(f"Target chat: {chat_name}")
    print(f"Reply command: {approval_reply_command('APPROVE', env['BATCH_ID'], run_id, approval_code=approval_code)}")
    if attachments:
        print("Approval attachment(s):")
        for path in attachments:
            print(f"- {path}")

    if "web.whatsapp.com" not in page.url:
        page.goto("https://web.whatsapp.com/", wait_until="domcontentloaded", timeout=60000)
    wait_for_whatsapp_ready(page, float(env.get("WHATSAPP_WEB_WAIT_SECONDS", "420")))
    post_progress(
        base_url,
        env,
        run_id,
        "whatsapp_opened",
        detail="WhatsApp Web loaded by deterministic browser script.",
    )
    open_chat(page, chat_name)
    ensure_chat_compose_ready(page, chat_name)
    post_progress(
        base_url,
        env,
        run_id,
        "approval_chat_opened",
        detail="Target WhatsApp chat opened by deterministic browser script.",
    )
    baseline_before_send = {record_key(record) for record in message_records(page)}

    if no_send:
        print("\n--no-send enabled. Message that would be sent:\n")
        print(approval_text)
        if attachments:
            print("\nAttachment(s) that would be sent:")
            for path in attachments:
                print(path)
        return request_json(f"{base_url}/status")

    post_progress(
        base_url,
        env,
        run_id,
        "approval_request_send_started",
        detail="Deterministic browser script is composing the current-run approval request.",
    )
    try:
        baseline_attachment_counts = document_attachment_counts(page, attachments)
        send_message_with_attachments(
            page,
            approval_text,
            attachments,
            run_id=run_id,
            approval_code=approval_code,
            manual_attachment_send_seconds=manual_attachment_send_seconds if attachments else 0,
        )
        wait_for_sent_request(
            page,
            run_id,
            attachments,
            baseline_before_send,
            baseline_attachment_counts,
            approval_code=approval_code,
            require_attachment_verification=env_flag(
                env,
                "WHATSAPP_REQUIRE_ATTACHMENT_VERIFICATION",
                True,
            ),
            require_single_caption_message=env_flag(
                env,
                "WHATSAPP_REQUIRE_SINGLE_CAPTION_MESSAGE",
                True,
            ),
        )
    except Exception as exc:
        post_progress(
            base_url,
            env,
            run_id,
            "approval_request_send_failed",
            detail=str(exc),
            evidence="Deterministic WhatsApp Web script failed while sending the approval request.",
        )
        raise

    post_progress(
        base_url,
        env,
        run_id,
        "approval_request_sent",
        evidence=(
            f"Deterministic WhatsApp Web script sent current {approval_code or run_id} request"
            + (
                f" with {len(attachments)} approval attachment(s)."
                if attachments
                else "."
            )
        ),
    )

    payload = wait_for_approval_reply(page, env, run_id, baseline_before_send, approval_code)
    print("\n=== Approval detected by WhatsApp Web automation ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    result = post_json(f"{base_url}/agent/approval", payload)
    print("\n=== Local approval server result ===")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return request_json(f"{base_url}/status")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the WhatsApp Web automation smoke test.")
    parser.add_argument("--no-send", action="store_true", help="Open the chat and print the message, but do not send it.")
    parser.add_argument("--keep-open", action="store_true", help="Leave the browser open after the script finishes.")
    parser.add_argument(
        "--manual-attachment-send",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    env = load_env()
    base_url = env["LOCAL_BASE_URL"].rstrip("/")
    server = start_web_server(env, base_url)
    manual_attachment_send_seconds = 0.0
    legacy_manual_seconds = float(env.get("WHATSAPP_ATTACHMENT_MANUAL_SEND_SECONDS", "0") or "0")
    if args.manual_attachment_send or legacy_manual_seconds > 0:
        raise RuntimeError(
            "Manual WhatsApp attachment sending is disabled. "
            "Unset WHATSAPP_ATTACHMENT_MANUAL_SEND_SECONDS and run the automated path."
        )

    shared_playwright = None
    shared_context = None
    try:
        start_result = request_json(f"{base_url}/start")
        final_status = run_whatsapp_web_approval(
            env,
            base_url,
            start_result,
            no_send=args.no_send,
            keep_open=args.keep_open,
            manual_attachment_send_seconds=manual_attachment_send_seconds,
            context=shared_context,
        )
        if not args.no_send:
            final_status = wait_for_terminal_status(base_url, start_result["run_id"])
        print("\n=== Final status ===")
        print(json.dumps(final_status, indent=2, ensure_ascii=False))
    finally:
        if shared_context is not None:
            try:
                shared_context.close()
            except Exception as exc:
                print(f"WhatsApp browser was already closed: {exc}")
        if shared_playwright is not None:
            try:
                shared_playwright.stop()
            except Exception as exc:
                print(f"Playwright was already stopped: {exc}")
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted by user; stopped current WhatsApp approval run.")
        sys.exit(130)
