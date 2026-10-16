"""Attach to the operator's Chrome; never copy cookies or own its lifecycle."""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse


class ChromeConnectionRequired(RuntimeError):
    pass


def activate_whatsapp_page(page):
    """Keep the dedicated WhatsApp renderer active without raising its window."""
    if urlparse(page.url).hostname != 'web.whatsapp.com':
        raise ValueError('Active-page recovery is limited to WhatsApp Web.')
    session=page.context.new_cdp_session(page)
    try:session.send('Emulation.setFocusEmulationEnabled',{'enabled':True})
    except Exception:
        session.detach()
        raise
    return session


def connection_endpoint(env: dict) -> str:
    explicit = env.get("COURSE_AGENT_CDP_URL", "").strip()
    if explicit:
        parsed = urlparse(explicit)
        if (parsed.scheme not in {"http", "ws"} or parsed.hostname not in
                {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password):
            raise ValueError("Chrome endpoint must be a local HTTP/WebSocket address.")
        return explicit
    root = Path(env.get("COURSE_AGENT_CHROME_USER_DATA_DIR") or
                Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) /
                "Google/Chrome/User Data")
    port_file = root / "DevToolsActivePort"
    if port_file.is_file():
        lines = port_file.read_text(encoding="utf-8").splitlines()
        if (len(lines) >= 2 and lines[0].isdigit() and 0 < int(lines[0]) < 65536
                and re.fullmatch(r"/devtools/browser/[A-Za-z0-9-]+", lines[1])):
            return f"ws://127.0.0.1:{lines[0]}{lines[1]}"
    raise ChromeConnectionRequired(
        "In your signed-in Chrome, open chrome://inspect/#remote-debugging, "
        "enable remote debugging, then allow the connection dialog. "
        "Alternatively configure COURSE_AGENT_CDP_URL for an existing local endpoint."
    )


class SharedChrome:
    def __init__(self, playwright, env):
        self.playwright=playwright;self.env=dict(env)
        self.browser = playwright.chromium.connect_over_cdp(
            connection_endpoint(env), timeout=int(env.get('COURSE_AGENT_CHROME_CONNECT_TIMEOUT_MS','180000')), no_defaults=True)
        self.owned_pages = []
        self.active_page_sessions = []

    def focus_whatsapp(self,page):
        for session in self.active_page_sessions:
            try:session.detach()
            except Exception:pass
        self.active_page_sessions=[activate_whatsapp_page(page)]

    def whatsapp_page(self):
        matches = [p for ctx in self.browser.contexts for p in ctx.pages
                   if urlparse(p.url).hostname == "web.whatsapp.com"]
        if len(matches) != 1:
            raise ChromeConnectionRequired(
                "Keep exactly one WhatsApp Web tab open in the intended signed-in Chrome profile.")
        self.focus_whatsapp(matches[0])
        return matches[0]

    def recover_whatsapp(self,page,action):
        """Recover transport only; caller retains task, queue, dedup and approval state."""
        if action not in {'focus','reload','reconnect'}:raise ValueError('Unknown browser recovery action.')
        reconnected=False
        if action=='reconnect' and not self.browser.is_connected():
            self.browser=self.playwright.chromium.connect_over_cdp(connection_endpoint(self.env),
                timeout=15000,no_defaults=True)
            reconnected=True
        if action=='reconnect':
            page=self.whatsapp_page()  # Exact existing tab only; ambiguity/login is never bypassed.
        else:self.focus_whatsapp(page)
        if action=='reload':
            if page.evaluate("""() => [...document.querySelectorAll('#main footer [contenteditable="true"]')]
                .some(e=>(e.innerText || e.textContent || '').trim())"""):
                raise ChromeConnectionRequired('WhatsApp has an unsent draft; preserve it before reloading.')
            page.reload(wait_until='domcontentloaded',timeout=30000)
            self.focus_whatsapp(page)
        return page,reconnected

    def moodle_page(self, whatsapp_page):
        # New work tab in the *same* profile; leave the operator's tabs intact.
        page = whatsapp_page.context.new_page()
        self.owned_pages.append(page)
        return page

    def recover_moodle_page(self,page):
        """Replace only an agent-owned work tab; caller repeats an exact GET.

        This must be called only by read/unsaved-preparation recovery, never by
        the Save action. Closing a failed work tab cannot resubmit a POST.
        """
        reconnected=False
        if not self.browser.is_connected():
            self.browser=self.playwright.chromium.connect_over_cdp(connection_endpoint(self.env),
                timeout=15000,no_defaults=True)
            reconnected=True
        whatsapp=self.whatsapp_page()
        replacement=self.moodle_page(whatsapp)
        if page in self.owned_pages:
            try:
                if not page.is_closed():page.close(run_before_unload=False)
            except Exception:pass
            self.owned_pages.remove(page)
        return replacement,whatsapp,reconnected

    def close(self):
        for session in getattr(self,'active_page_sessions',[]):
            try:session.detach()
            except Exception:pass
        self.active_page_sessions=[]
        for page in self.owned_pages:
            try:
                if not page.is_closed():page.close()
            except Exception:pass
        self.owned_pages.clear()
        # playwright.stop disconnects. browser/context.close would close user tabs.
