"""WhatsApp Web page helpers used by the shared-Chrome runtime.

Extracted verbatim from run_whatsapp_web_smoke.py so the runtime does not load the
legacy smoke harness; that module re-exports these names for its own callers.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from typing import TypedDict


class WhatsAppSendUnconfirmedError(RuntimeError):
    """The send control was used, but chat-history delivery is still uncertain."""


class WhatsAppMessageRecord(TypedDict, total=False):
    message_id: str
    data_id: str
    direction: str
    sender_display_name: str
    sender_id: str
    chat_type: str
    mentions: list[dict[str, str]]
    timestamp: str
    text: str
    meta: str
    documents: list[str]
    document_global_indices: list[int]
    provenance_confidence: str
    source: str
    index: int


def first_visible(page, selectors: list[str], timeout_ms: int = 1500):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.wait_for(state="visible", timeout=timeout_ms)
            return locator
        except Exception:
            continue
    return None


def whatsapp_document_viewer_visible(page) -> bool:
    try:
        return bool(
            page.evaluate(
                """
                () => {
                  const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0
                      && style.visibility !== 'hidden'
                      && style.display !== 'none';
                  };
                  const nodes = Array.from(document.querySelectorAll('header, span, div, button, [role="button"]'))
                    .filter(visible);
                  const topText = nodes
                    .filter((el) => el.getBoundingClientRect().y < 180)
                    .map((el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim())
                    .filter(Boolean)
                    .join('\\n');
                  const pageText = nodes
                    .map((el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim())
                    .filter(Boolean)
                    .slice(0, 80)
                    .join('\\n');
                  const hasViewerCue = /Edit PDF/i.test(topText)
                    || /Powered by Adobe Acrobat/i.test(pageText)
                    || /You\\s*[•·]\\s*[^\\n]+\\.pdf/i.test(topText);
                  const topRightClose = nodes.some((el) => {
                    const label = [
                      el.getAttribute('aria-label') || '',
                      el.getAttribute('title') || '',
                      el.getAttribute('data-testid') || '',
                      el.getAttribute('data-icon') || '',
                    ].join(' ').toLowerCase();
                    if (!/(close|x-viewer|x\\b)/.test(label)) return false;
                    const rect = el.getBoundingClientRect();
                    return rect.x > window.innerWidth - 180 && rect.y < 180;
                  });
                  return hasViewerCue && topRightClose;
                }
                """
            )
        )
    except Exception:
        return False


def close_whatsapp_document_viewer(page) -> bool:
    closed = False
    for _ in range(2):
        if not whatsapp_document_viewer_visible(page):
            return closed
        point = None
        try:
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
                  const candidates = Array.from(document.querySelectorAll(
                    '[aria-label="Close"], [title="Close"], [data-testid*="x"], [data-icon*="x"], button, [role="button"]'
                  ))
                    .filter(visible)
                    .map((el) => {
                      const rect = el.getBoundingClientRect();
                      const label = [
                        el.getAttribute('aria-label') || '',
                        el.getAttribute('title') || '',
                        el.getAttribute('data-testid') || '',
                        el.getAttribute('data-icon') || '',
                        el.innerText || el.textContent || '',
                      ].join(' ').toLowerCase();
                      return {el, rect, label};
                    })
                    .filter((item) => item.rect.x > window.innerWidth - 180 && item.rect.y < 180)
                    .filter((item) => /(close|x-viewer|^x$|\\bx\\b)/.test(item.label));
                  candidates.sort((a, b) => (b.rect.x - a.rect.x) || (a.rect.y - b.rect.y));
                  const hit = candidates[0];
                  if (!hit) return null;
                  return {x: hit.rect.x + hit.rect.width / 2, y: hit.rect.y + hit.rect.height / 2};
                }
                """
            )
        except Exception:
            point = None
        try:
            if point:
                page.mouse.click(float(point["x"]), float(point["y"]))
            else:
                page.keyboard.press("Escape")
            page.wait_for_timeout(700)
            closed = True
        except Exception:
            return closed
    return closed


def wait_for_whatsapp_ready(page, timeout_seconds: float) -> None:
    deadline = time.time() + timeout_seconds
    qr_notice_printed = False
    ready_selectors = [
        "[aria-label='Chat list']",
        "[data-testid='chat-list']",
        "#pane-side",
        "div[aria-label='Search input textbox']",
        "div[aria-label='Search or start a new chat']",
        "div[aria-placeholder='Search or start a new chat']",
        "div[contenteditable='true'][aria-label*='Search']",
        "div[contenteditable='true'][aria-placeholder*='Search']",
        "div[contenteditable='true'][data-tab='3']",
        "div[role='textbox'][contenteditable='true']",
        "footer div[contenteditable='true']",
    ]
    while time.time() < deadline:
        if first_visible(page, ready_selectors, timeout_ms=750):
            return
        qr_visible = page.locator("canvas, [data-testid='qrcode']").count() > 0
        if qr_visible and not qr_notice_printed:
            print("\nWhatsApp Web is asking for QR login.")
            print("Use your phone: WhatsApp -> Settings -> Linked devices -> Link a device.")
            print("After scanning, keep this Chrome window open; the script will continue automatically.\n")
            qr_notice_printed = True
        time.sleep(1)
    raise TimeoutError("WhatsApp Web was not ready before the timeout.")


def select_all_shortcut() -> str:
    return "Meta+A" if sys.platform == "darwin" else "Control+A"


def fill_editable_locator(locator, text: str) -> None:
    try:
        locator.fill(text)
        return
    except Exception:
        pass
    locator.evaluate(
        """
        (el, text) => {
          el.focus();
          if ('value' in el) {
            el.value = text;
          } else {
            const selection = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(el);
            selection.removeAllRanges();
            selection.addRange(range);
            document.execCommand('insertText', false, text);
          }
          el.dispatchEvent(new Event('input', {bubbles: true, cancelable: true}));
          el.dispatchEvent(new Event('change', {bubbles: true, cancelable: true}));
        }
        """,
        text,
    )


def chat_row_click_point(page, chat_name: str) -> tuple[float, float] | None:
    point = page.evaluate(
        """
        (chatName) => {
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            return rect.width > 0 && rect.height > 0
              && style.visibility !== 'hidden'
              && style.display !== 'none';
          };
          const matches = Array.from(document.querySelectorAll('span, div'))
            .filter(visible)
            .filter((el) => {
              const title = (el.getAttribute('title') || '').trim();
              const text = (el.innerText || el.textContent || '').trim();
              return title === chatName || text === chatName;
            });
          for (const match of matches) {
            let best = null;
            for (let current = match; current; current = current.parentElement) {
              const rect = current.getBoundingClientRect();
              if (rect.x >= 60 && rect.x < 520 && rect.width >= 220 && rect.width <= 480
                  && rect.height >= 48 && rect.height <= 96) {
                best = rect;
              }
            }
            if (best) {
              return {x: best.x + best.width / 2, y: best.y + best.height / 2};
            }
            const rect = match.getBoundingClientRect();
            if (rect.x >= 60 && rect.x < 520) {
              return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
            }
          }
          return null;
        }
        """,
        chat_name,
    )
    if not point:
        return None
    return float(point["x"]), float(point["y"])


def click_matching_list_item(page, chat_name: str) -> bool:
    items = page.locator('[data-testid^="list-item-"]')
    try:
        count = items.count()
    except Exception:
        return False
    for index in range(count):
        item = items.nth(index)
        try:
            text = item.inner_text(timeout=1000)
        except Exception:
            continue
        if chat_name not in [part.strip() for part in text.splitlines()]:
            continue
        item.click(force=True, timeout=5000)
        page.wait_for_timeout(1200)
        return True
    return False


def click_chat_row(page, chat_name: str) -> bool:
    # Current WhatsApp uses ordinary input controls and responsive sidebar widths.
    # Resolve the exact title within the chat list before any legacy layout fallback.
    exact = page.locator('#pane-side [title=' + json.dumps(chat_name) + ']:visible')
    count = exact.count()
    if count > 1:
        raise RuntimeError('More than one visible WhatsApp chat has the requested exact title.')
    if count == 1:
        # A background Chrome tab may throttle animation frames indefinitely.
        # The target is already exact and unique; verify the resulting header.
        exact.click(force=True, timeout=5000)
        page.locator('#main header').get_by_text(chat_name, exact=True).first.wait_for(
            state='visible', timeout=10000)
        return True
    if click_matching_list_item(page, chat_name):
        return True
    point = chat_row_click_point(page, chat_name)
    if not point:
        return False
    page.mouse.click(point[0], point[1])
    page.wait_for_timeout(1200)
    return True


def open_chat(page, chat_name: str) -> None:
    dismiss_browser_overlays(page)
    if click_chat_row(page, chat_name):
        return

    search = first_visible(
        page,
        [
            "#side input[role='textbox'][data-tab='3']",
            "#side input[type='search']",
            "div[aria-label='Search input textbox']",
            "div[aria-label='Search or start a new chat']",
            "div[aria-placeholder='Search or start a new chat']",
            "div[contenteditable='true'][aria-label*='Search']",
            "div[contenteditable='true'][aria-placeholder*='Search']",
            "div[contenteditable='true'][data-tab='3']",
            "div[role='textbox'][contenteditable='true'][aria-label*='Search']",
        ],
        timeout_ms=5000,
    )
    if not search:
        raise RuntimeError("Could not find the WhatsApp search box.")

    dismiss_browser_overlays(page)
    try:
        search.click(timeout=1500, force=True)
    except Exception:
        search.evaluate("(el) => el.focus()")
    page.keyboard.press(select_all_shortcut())
    page.keyboard.press("Backspace")
    try:
        search.fill(chat_name)
    except Exception:
        fill_editable_locator(search, chat_name)
    page.wait_for_timeout(1200)

    if click_chat_row(page, chat_name):
        return
    try:
        page.keyboard.press("Enter")
        page.wait_for_timeout(1200)
        if click_chat_row(page, chat_name):
            return
    except Exception:
        pass
    for candidate in [page.locator(f"span[title={json.dumps(chat_name)}]").first]:
        try:
            candidate.wait_for(state="visible", timeout=5000)
            candidate.evaluate(
                """
                (el) => {
                  const event = new MouseEvent('click', {bubbles: true, cancelable: true, view: window});
                  let target = el;
                  for (let current = el; current; current = current.parentElement) {
                    const rect = current.getBoundingClientRect();
                    if (rect.x >= 60 && rect.x < 520 && rect.width >= 220 && rect.height >= 48) {
                      target = current;
                    }
                  }
                  target.dispatchEvent(event);
                }
                """
            )
            page.wait_for_timeout(1200)
            return
        except Exception:
            continue
    raise RuntimeError(f"Could not open WhatsApp chat named {chat_name!r}.")


def dismiss_browser_overlays(page) -> None:
    close_whatsapp_document_viewer(page)
    # Chrome's crash-restore bubble is browser UI, but Escape usually closes it
    # and also dismisses transient WhatsApp popovers without changing the chat.
    for _ in range(2):
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(250)
        except Exception:
            return


def compose_box(page):
    box = first_visible(
        page,
        [
            "div[data-testid='conversation-compose-box-input'][contenteditable='true']",
            "div[aria-label^='Type a message to'][contenteditable='true']",
            "footer div[contenteditable='true'][role='textbox']",
            "footer div[contenteditable='true']",
            "div[aria-label='Type a message']",
            "div[aria-placeholder='Type a message']",
            "div[contenteditable='true'][aria-label*='message']",
            "div[contenteditable='true'][aria-placeholder*='message']",
        ],
        timeout_ms=5000,
    )
    if not box:
        raise RuntimeError("Could not find the WhatsApp message compose box.")
    return box


def focus_compose_box(page, box) -> None:
    errors: list[str] = []
    try:
        box.evaluate(
            """
            (el) => {
              el.focus();
              const selection = window.getSelection();
              const range = document.createRange();
              range.selectNodeContents(el);
              range.collapse(false);
              selection.removeAllRanges();
              selection.addRange(range);
            }
            """
        )
        page.wait_for_timeout(200)
        return
    except Exception as exc:
        errors.append(f"dom_focus={exc}")
    scroll_chat_to_bottom(page)
    try:
        box.click(timeout=1500, force=True)
        return
    except Exception as exc:
        errors.append(f"force_click={exc}")
    try:
        box.click(timeout=1500)
        return
    except Exception as exc:
        errors.append(f"click={exc}")
        raise RuntimeError("Could not focus the WhatsApp message compose box: " + " | ".join(errors)) from exc


def set_compose_text(page, box, message: str) -> None:
    clipboard_error: Exception | None = None
    if sys.platform == "darwin":
        try:
            subprocess.run(["pbcopy"], input=message, text=True, check=True)
            focus_compose_box(page, box)
            page.keyboard.press(select_all_shortcut())
            page.keyboard.press("Backspace")
            page.wait_for_timeout(200)
            page.keyboard.press("Meta+V")
            page.wait_for_timeout(700)
            return
        except Exception as exc:
            clipboard_error = exc
    try:
        _clear_editor(page, box)
        insert_multiline_text(page, message)
        page.wait_for_timeout(500)
        verify_compose_text(box, message)
        return
    except Exception as exc:
        raise RuntimeError(f"Could not set WhatsApp compose text: clipboard={clipboard_error}; dom={exc}") from exc


def normalize_multiline_text(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u200e", "").replace("\u200f", "").replace("\xa0", " ")
    return "\n".join(line.rstrip() for line in normalized.split("\n")).strip()


def whatsapp_safe_compose_text(text: str) -> str:
    """Avoid WhatsApp Web turning plain numbered lines into editable list nodes."""
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(
        re.sub(r"^(\s*)(\d{1,3})[.)]\s+", r"\1Item \2: ", line)
        for line in normalized.split("\n")
    )


def semantic_multiline_lines(text: str) -> list[str]:
    """Compare ordered message content while ignoring editor-generated list markup."""
    lines: list[str] = []
    for raw_line in normalize_multiline_text(text).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        previous = None
        while line != previous:
            previous = line
            line = re.sub(
                r"^(?:\d{1,3}[.)]|[\-\*\u2022\u25e6\u25aa\u2023\u2043])\s+",
                "",
                line,
            ).strip()
        lines.append(line)
    return lines


def observed_multiline_candidates(text: str) -> set[str]:
    exact = normalize_multiline_text(text)
    doubled_breaks = re.sub(
        r"\n+",
        lambda match: "\n" * max(1, (len(match.group(0)) + 1) // 2),
        exact,
    )
    whatsapp_break_nodes = re.sub(
        r"\n+",
        lambda match: "\n" * max(1, (len(match.group(0)) + 1) // 3),
        exact,
    )
    return {exact, doubled_breaks, whatsapp_break_nodes}


def insert_multiline_text(page, message: str) -> None:
    lines = str(message or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for index, line in enumerate(lines):
        if line:
            page.keyboard.insert_text(line)
        if index < len(lines) - 1:
            page.keyboard.press("Shift+Enter")


def compose_visible_text(box) -> str:
    return str(box.evaluate("(el) => el.innerText || el.textContent || ''") or "")


def verify_compose_text(box, expected: str, *, allow_prefix: bool = False) -> None:
    expected_text = normalize_multiline_text(expected)
    actual_text = normalize_multiline_text(compose_visible_text(box))
    actual_candidates = observed_multiline_candidates(actual_text)
    matches = (
        any(candidate.endswith(expected_text) for candidate in actual_candidates)
        if allow_prefix
        else expected_text in actual_candidates
    )
    if not matches:
        expected_semantic = semantic_multiline_lines(expected_text)
        actual_semantic = semantic_multiline_lines(actual_text)
        matches = (
            actual_semantic[-len(expected_semantic) :] == expected_semantic
            if allow_prefix and expected_semantic
            else actual_semantic == expected_semantic
        )
    if not matches:
        raise RuntimeError(
            "WhatsApp compose box did not preserve the expected multiline text; "
            f"expected_lines={expected_text.splitlines()!r}, actual_lines={actual_text.splitlines()!r}"
        )


def scroll_chat_to_bottom(page) -> None:
    close_whatsapp_document_viewer(page)
    try:
        page.keyboard.press("End")
        page.wait_for_timeout(300)
    except Exception:
        pass
    try:
        page.evaluate(
            """
            () => {
              const candidates = Array.from(document.querySelectorAll('[role="application"], main, div'))
                .filter((el) => el.scrollHeight > el.clientHeight + 100);
              candidates.sort((a, b) => b.scrollHeight - a.scrollHeight);
              for (const el of candidates.slice(0, 5)) {
                el.scrollTop = el.scrollHeight;
              }
            }
            """
        )
        page.wait_for_timeout(300)
    except Exception:
        pass


def _clear_editor(page, box) -> None:
    focus_compose_box(page, box)
    page.keyboard.press(select_all_shortcut())
    page.keyboard.press("Backspace")
    page.wait_for_timeout(150)


def _insert_real_mention(page, box, mention_name: str) -> bool:
    name = str(mention_name or "").strip().lstrip("@")
    if not name:
        return False
    _clear_editor(page, box)
    page.keyboard.insert_text(f"@{name}")
    page.wait_for_timeout(700)
    marker = f"course-agent-mention-{int(time.time() * 1000)}"
    match_count = int(
        page.evaluate(
            """
            ({name, marker}) => {
              const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0
                  && rect.bottom > window.innerHeight * 0.45
                  && style.visibility !== 'hidden'
                  && style.display !== 'none';
              };
              const normalize = (value) => String(value || '').replace(/^@/, '').trim().toLowerCase();
              const candidates = Array.from(document.querySelectorAll(
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
              });
              candidates.forEach((el) => el.setAttribute('data-course-agent-mention-target', marker));
              return candidates.length;
            }
            """,
            {"name": name, "marker": marker},
        )
    )
    if match_count != 1:
        _clear_editor(page, box)
        return False
    target = page.locator(f'[data-course-agent-mention-target="{marker}"]').first
    target.click(timeout=3000, force=True)
    page.wait_for_timeout(250)
    try:
        return bool(
            box.evaluate(
                """
                (el) => Boolean(
                  el.querySelector('[data-mention-jid], [data-testid*="mention"]')
                  || Array.from(el.querySelectorAll('span')).some((node) => {
                    const text = String(node.innerText || node.textContent || '').trim();
                    return text.startsWith('@');
                  })
                )
                """
            )
        )
    except Exception:
        return False


def set_action_message_text(page, box, message: str, mention_name: str, chat_type: str) -> dict:
    name = str(mention_name or "").strip().lstrip("@")
    if not name:
        set_compose_text(page, box, message)
        return {"mention_mode": "none", "mention_target": ""}
    if str(chat_type or "").strip().lower() == "group" and _insert_real_mention(page, box, name):
        page.keyboard.press("Shift+Enter")
        page.keyboard.press("Shift+Enter")
        insert_multiline_text(page, message)
        page.wait_for_timeout(350)
        verify_compose_text(box, message, allow_prefix=True)
        return {"mention_mode": "real", "mention_target": name}
    set_compose_text(page, box, f"{name},\n\n{message}")
    return {"mention_mode": "name_prefix", "mention_target": name}


def multiline_message_matches(observed: str, expected: str) -> bool:
    expected_text = normalize_multiline_text(expected)
    if not expected_text:
        return False
    if any(
        expected_text in candidate
        for candidate in observed_multiline_candidates(observed)
    ):
        return True

    expected_lines = semantic_multiline_lines(expected_text)
    observed_lines = semantic_multiline_lines(observed)
    if not expected_lines or len(observed_lines) < len(expected_lines):
        return False
    width = len(expected_lines)
    return any(
        observed_lines[start : start + width] == expected_lines
        for start in range(len(observed_lines) - width + 1)
    )


def message_records(page) -> list[WhatsAppMessageRecord]:
    return page.evaluate(
        """
        () => {
          const normalize = (value) => String(value || '').replace(/\\u200e/g, '').replace(/\\s+/g, ' ').trim();
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            return rect.width > 0 && rect.height > 0
              && rect.bottom > 0
              && rect.right > 0
              && rect.x < window.innerWidth
              && rect.y < window.innerHeight
              && style.visibility !== 'hidden'
              && style.display !== 'none';
          };
          const unobscured = (el) => {
            if (!visible(el)) return false;
            const rect = el.getBoundingClientRect();
            const points = [
              [rect.x + rect.width / 2, rect.y + rect.height / 2],
              [rect.x + Math.min(rect.width - 2, 12), rect.y + Math.min(rect.height - 2, 12)],
              [rect.right - Math.min(rect.width - 2, 12), rect.bottom - Math.min(rect.height - 2, 12)],
            ];
            return points.some(([x, y]) => {
              if (x < 0 || y < 0 || x >= window.innerWidth || y >= window.innerHeight) return false;
              const top = document.elementFromPoint(x, y);
              return top && (el.contains(top) || top.contains(el));
            });
          };
          const documentText = (doc) => normalize([
            doc.innerText || doc.textContent || '',
            doc.getAttribute('aria-label') || '',
            doc.getAttribute('title') || '',
          ].join(' '));
          const looksLikeDocument = (text) => /\\.[A-Za-z0-9]{1,12}\\b/.test(text)
            && /\\b(PDF|DOCX?|PPTX?|XLSX?|TXT|CSV|ZIP|RAR|pages?|kB|MB)\\b/i.test(text);
          const allDocumentControls = Array.from(
            document.querySelectorAll('[data-testid="document-thumb"], [data-icon="document"], [aria-label], [title]')
          ).filter((node) => looksLikeDocument(documentText(node)));
          allDocumentControls.forEach((node, index) => {
            node.setAttribute('data-course-agent-document-global-index', String(index));
          });
          const candidateNodes = [];
          const seen = new Set();
          const addCandidate = (el, source) => {
            if (!el || seen.has(el)) return;
            if (!unobscured(el)) return;
            seen.add(el);
            candidateNodes.push({el, source});
          };
          Array.from(document.querySelectorAll('[data-pre-plain-text]')).forEach((el) => addCandidate(el, 'data_pre'));
          allDocumentControls.forEach((doc) => {
            const message = doc.closest('[data-pre-plain-text]')
              || doc.closest('[data-id], .message-in, .message-out, [role="row"]')
              || doc.parentElement;
            addCandidate(message, 'document_fallback');
          });
          Array.from(document.querySelectorAll('[data-testid="image-thumb"]')).forEach((image) => {
            if (image.closest('[data-testid*="quoted"],blockquote')) return;
            const message = image.closest('[data-pre-plain-text]') || image.closest('[data-id]');
            addCandidate(message, 'image_fallback');
          });
          const senderDisplayNames = new Map();
          for (const {el} of candidateNodes) {
            const root = el.closest('[data-id], .message-in, .message-out') || el;
            const metaNode = el.hasAttribute('data-pre-plain-text')
              ? el
              : root.querySelector('[data-pre-plain-text]');
            const meta = (metaNode && metaNode.getAttribute('data-pre-plain-text')) || '';
            const senderMatch = meta.match(/\\]\\s*(.*?):\\s*$/);
            const author = root.querySelector('[data-testid="author"]');
            const authorName = normalize(author && (author.innerText || author.textContent) || '');
            const authorRowText = normalize(author && author.parentElement
              && (author.parentElement.innerText || author.parentElement.textContent) || '');
            const phoneMatch = authorRowText.match(/\\+?\\d[\\d\\s().-]{6,}\\d/);
            const senderId = senderMatch
              ? senderMatch[1].trim()
              : normalize(phoneMatch && phoneMatch[0] || '');
            if (senderId && authorName) senderDisplayNames.set(senderId.toLowerCase(), authorName);
          }
          return candidateNodes
            .map(({el, source}, index) => {
              const root = el.closest('[data-id], .message-in, .message-out') || el;
              const metaNode = el.hasAttribute('data-pre-plain-text')
                ? el
                : root.querySelector('[data-pre-plain-text]');
              const meta = (metaNode && metaNode.getAttribute('data-pre-plain-text')) || '';
              const messageId = root.getAttribute('data-id') || el.getAttribute('data-id') || '';
              const author = root.querySelector('[data-testid="author"]');
              const authorName = normalize(author && (author.innerText || author.textContent) || '');
              const authorRowText = normalize(author && author.parentElement
                && (author.parentElement.innerText || author.parentElement.textContent) || '');
              const phoneMatch = authorRowText.match(/\\+?\\d[\\d\\s().-]{6,}\\d/);
              const senderMatch = meta.match(/\\]\\s*(.*?):\\s*$/);
              const senderId = senderMatch
                ? senderMatch[1].trim()
                : normalize(phoneMatch && phoneMatch[0] || '');
              const explicitSelfAuthor = Boolean(root.querySelector(
                '[aria-label="You:"], [data-testid="msg-meta"] [aria-label*="Delivered"], '
                + '[data-testid="msg-meta"] [aria-label*="Read"]'
              )) || Array.from(root.querySelectorAll('[data-testid="msg-meta"] title'))
                .some((title) => /wds-ic-(?:read|sent|delivered)/i.test(
                  String(title.textContent || '')
                ));
              const direction = root.classList.contains('message-out')
                || Boolean(root.closest('.message-out'))
                || explicitSelfAuthor
                ? 'outgoing'
                : (
                    root.classList.contains('message-in') || Boolean(root.closest('.message-in'))
                      ? 'incoming'
                      : (messageId && (meta || authorName) ? 'incoming' : 'unknown')
                  );
              const senderDisplayName = authorName
                || senderDisplayNames.get(senderId.toLowerCase())
                || senderId;
              const timestampMatch = meta.match(/^\\[([^\\]]+)\\]/);
              const timestamp = timestampMatch ? timestampMatch[1].trim() : '';
              const mentionEntries = [];
              const seenMentions = new Set();
              const explicitMentionSelector = [
                '[data-mention-jid]',
                '[data-testid*="mention"]',
                '[aria-label^="@"]',
                '[data-app-text-template][data-plain-text^="@"]',
              ].join(', ');
              for (const mention of Array.from(root.querySelectorAll(
                `${explicitMentionSelector}, button, [role="button"]`
              ))) {
                const visibleLabel = normalize(
                  mention.getAttribute('data-plain-text')
                  || mention.innerText
                  || mention.textContent
                  || mention.getAttribute('aria-label')
                  || mention.getAttribute('title')
                  || ''
                );
                const isExplicitMention = mention.matches(explicitMentionSelector);
                if (!isExplicitMention && !visibleLabel.startsWith('@')) continue;
                const label = visibleLabel.replace(/^@/, '');
                const jid = normalize(
                  mention.getAttribute('data-mention-jid')
                  || mention.getAttribute('data-app-text-template')
                  || ''
                ).replace(/[\u200b-\u200f\u2060\ufeff]/g, '');
                const key = `${label.toLowerCase()}|${jid.toLowerCase()}`;
                if ((!label && !jid) || seenMentions.has(key)) continue;
                seenMentions.add(key);
                mentionEntries.push({label, jid});
              }
              let chatType = 'unknown';
              if (messageId.includes('@g.us')) chatType = 'group';
              else if (messageId.includes('@c.us')) chatType = 'direct';
              const documentCandidates = Array.from(
                root.querySelectorAll('[data-testid="document-thumb"], button, [role="button"], [aria-label], [title]')
              );
              const documentEntries = [];
              const seenDocuments = new Set();
              for (const candidate of documentCandidates) {
                const text = documentText(candidate);
                if (!looksLikeDocument(text) || seenDocuments.has(text)) continue;
                seenDocuments.add(text);
                let control = candidate.matches('[data-testid="document-thumb"], [data-icon="document"]')
                  ? candidate
                  : candidate.closest('[data-testid="document-thumb"], [data-icon="document"]');
                let globalIndex = control ? allDocumentControls.indexOf(control) : -1;
                documentEntries.push({text, globalIndex});
              }
              return {
                index,
                source,
                data_id: messageId,
                message_id: messageId,
                direction,
                sender_display_name: senderDisplayName,
                sender_id: senderId,
                timestamp,
                chat_type: chatType,
                mentions: mentionEntries,
                provenance_confidence: messageId && direction !== 'unknown' ? 'high' : 'low',
                meta,
                text: (root.innerText || root.textContent || '').replace(/\\u200e/g, '').trim(),
                documents: documentEntries.map((item) => item.text),
                document_global_indices: documentEntries.map((item) => item.globalIndex),
              };
            })
            .filter((item) => item.text || item.documents.length);
        }
        """
    )


def record_message_id(record: dict) -> str:
    return str(record.get("message_id") or record.get("data_id") or "").strip()
