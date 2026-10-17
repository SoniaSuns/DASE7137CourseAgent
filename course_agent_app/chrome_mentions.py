"""DOM-only addressing evidence. Account identifiers stay inside the browser."""

MESSAGE_BODY_SCRIPT = r"""input => {
  const ids = Array.isArray(input) ? input : input.ids;
  const accountName = Array.isArray(input) ? '' : input.account_name || '';
  const normalize = value => String(value || '').replace(/[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]/g, '').trim();
  const jid = value => {
    const match = /^(\d+)(?::\d+)?@(c\.us|s\.whatsapp\.net|lid)$/.exec(normalize(value));
    return match ? match[1] + '@' + (match[2] === 's.whatsapp.net' ? 'c.us' : match[2]) : '';
  };
  const own = new Set();
  for (const key of ['last-wid-md', 'last-wid', 'last-lid']) {
    let value = localStorage.getItem(key);
    try { value = JSON.parse(value); } catch {}
    if (typeof value === 'string' && jid(value)) own.add(jid(value));
  }
  const quoted = '[data-testid*="quoted"], blockquote';
  const marker = '[data-mention-jid], .mention, .matched-mention, [data-testid="mention"], '
    + '[data-testid="message-mention"], [data-app-text-template][data-plain-text^="@"], span[role="button"][tabindex="0"]';
  const isOwnMention = node => {
    if (node.closest(quoted)) return false;
    let explicitIdentity = false;
    for (const attr of ['data-mention-jid', 'data-jid', 'data-app-text-template']) {
      const identity = jid(node.getAttribute(attr));
      if (identity) explicitIdentity = true;
      if (own.has(identity)) return true;
    }
    if (explicitIdentity) return false;
    // Numeric mention labels can identify the current account even when the UI
    // omits the JID attribute. A display-name match alone is never sufficient.
    for (const value of [node.getAttribute('data-plain-text'), node.textContent]) {
      const label = normalize(value);
      if (/^@\+?[\d\s()-]+$/.test(label)) {
        const digits = label.replace(/\D/g, '');
        if (own.has(digits + '@c.us')) return true;
      }
    }
    // v1 uses an actual structured mention plus the observed account profile name.
    // Never accept plain message text or override an explicit different identity.
    const rawLabel = normalize(node.getAttribute('data-plain-text') || node.textContent);
    if (!rawLabel.startsWith('@')) return false;
    const label = rawLabel.slice(1).toLocaleLowerCase();
    if (accountName && label === normalize(accountName).toLocaleLowerCase()) return true;
    return false;
  };
  return Object.fromEntries(ids.map(id => {
    const root = document.querySelector('[data-id="' + CSS.escape(id) + '"]');
    if (!root) return [id, null];
    const candidates = [...root.querySelectorAll('[data-pre-plain-text]')].filter(e => !e.closest(quoted));
    // Rich-text list items also carry data-pre-plain-text ("- "). They are
    // descendants of the message body, not separate timestamped messages.
    const body = candidates.find(e => !candidates.some(other => other !== e && other.contains(e)));
    // Document captions in current WhatsApp have no data-pre-plain-text wrapper.
    const nodes = (body ? [...body.querySelectorAll('.selectable-text')] :
      [...root.querySelectorAll('[data-testid~="document-caption"],[data-testid~="image-caption"]')]).filter(e =>
      !e.parentElement.closest('.selectable-text') && !e.closest(quoted));
    const unquotedTail = direction => [...root.querySelectorAll('[data-testid="tail-'+direction+'"],[data-icon="tail-'+direction+'"]')]
      .some(e => !e.closest(quoted));
    const documentDirection = root.querySelector('[data-testid="document-thumb"],[data-testid="image-thumb"]') ?
      (unquotedTail('out') ? 'outgoing' : unquotedTail('in') ? 'incoming' : '') : '';
    let documentDateLabel = '', documentClock = '';
    if (!body && root.querySelector('[data-testid="document-thumb"],[data-testid="image-thumb"]')) {
      const clocks = [...root.querySelectorAll('[data-testid="msg-meta"]')].filter(e => !e.closest(quoted));
      documentClock = clocks.length === 1 ? normalize(clocks[0].textContent) : '';
      // Read a structural timeline separator, never date-like words in a message.
      const timeline = root.closest('[data-tab="8"]');
      const dateLabel = /^(今天|昨天|昨日|Today|Yesterday|\d{4}年\d{1,2}月\d{1,2}日|\d{4}[/-]\d{1,2}[/-]\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{4})$/i;
      for (let branch = root; timeline && branch && branch !== timeline && !documentDateLabel; branch = branch.parentElement) {
        for (let sibling = branch.previousElementSibling; sibling; sibling = sibling.previousElementSibling) {
          if (sibling.getAttribute('tabindex') !== '-1' || sibling.matches('[data-id],[role="row"]') ||
              sibling.querySelector('[data-id],[data-pre-plain-text],[data-testid="document-thumb"]')) continue;
          const label = normalize(sibling.innerText || sibling.textContent);
          if (dateLabel.test(label)) {documentDateLabel = label; break;}
        }
      }
    }
    let selfMentioned = false;
    const raw = [], addressed = [];
    for (const node of nodes) {
      raw.push(node.innerText || node.textContent || '');
      const clone = node.cloneNode(true);
      clone.querySelectorAll(quoted).forEach(e => e.remove());
      for (const mention of clone.querySelectorAll(marker)) {
        if (isOwnMention(mention)) {
          selfMentioned = true;
          // WhatsApp edits can leave an adjacent auto-linked copy of our phone
          // after a real mention. Only remove that exact own-account artifact.
          let next = mention.nextSibling;
          while (next && next.nodeType === Node.TEXT_NODE && !normalize(next.textContent)) next = next.nextSibling;
          while (next && next.nodeType === Node.ELEMENT_NODE && next.matches('a')) {
            const label = normalize(next.textContent);
            const digits = label.replace(/\D/g, '');
            if (!/^@?\+?[\d\s()-]+$/.test(label) || !own.has(digits + '@c.us')) break;
            const remove = next;
            next = next.nextSibling;
            remove.remove();
            while (next && next.nodeType === Node.TEXT_NODE && !normalize(next.textContent)) next = next.nextSibling;
          }
          mention.replaceWith(document.createTextNode(' '));
        }
      }
      addressed.push(clone.textContent || '');
    }
    return [id, {text:raw.join('\n').trim(), addressed_text:normalize(addressed.join('\n')),
      self_mentioned:selfMentioned, account_identity_available:own.size > 0,
      document_date_label:documentDateLabel, document_clock:documentClock,
      document_direction:documentDirection,
      meta:body?.getAttribute('data-pre-plain-text') || ''}];
  }));
}"""


def discover_account_name(page):
    """Read the current UI profile; no credentials, profile edits or guessed names."""
    from playwright.sync_api import Error as BrowserError
    reader = r"""() => {
      // Current WhatsApp exposes the observed own-account name in the Me drawer
      // title. This is account UI provenance, never a name guessed from chat text.
      const ownTitles=[...document.querySelectorAll('[data-testid="me-tab-drawer"] [data-testid="drawer-title-body"]')];
      if (ownTitles.length===1) {
        const names=[...ownTitles[0].querySelectorAll('[dir="auto"]')].filter(e=>e.textContent.trim());
        if (names.length===1) return names[0].textContent.trim();
      }
      const labels = ['Edit name', 'Edit \u201cName\u201d', '\u7f16\u8f91\u201c\u59d3\u540d\u201d'];
      const buttons = [...document.querySelectorAll('button')].filter(e => labels.includes(e.getAttribute('aria-label')));
      if (buttons.length !== 1) return '';
      let row = buttons[0];
      for (let i=0; row && i<7; i++, row=row.parentElement) {
        const names = [...row.querySelectorAll('[dir="auto"]')].filter(e => e.textContent.trim());
        if (names.length===1) return names[0].textContent.trim();
        if (names.length>1) return '';
      }
      return '';
    }"""
    try:
        name=page.evaluate(reader)
        if name:return name
        control=page.locator('header button[aria-label="自己"], header button[aria-label="Profile"], header button[aria-label="You"]')
        if control.count()!=1:return ''
        control.click(force=True,timeout=3000)
        # Let the observed account drawer settle before falling back to the older
        # editable profile layout. Never click the Edit-name control.
        from playwright.sync_api import TimeoutError as BrowserTimeout
        try:return page.wait_for_function(reader,timeout=2000).json_value()
        except BrowserTimeout:pass
        profile=page.locator('[data-testid="li-profile"]')
        profile.wait_for(state='visible',timeout=3000)
        profile.click(force=True,timeout=3000)
        page.wait_for_timeout(500)
        return page.evaluate(reader)
    except BrowserError:
        return ''  # No guessed identity; exact account-ID mention checks remain available.
    finally:
        # A cosmetic drawer transition must not discard a successfully read
        # identity or terminate the listener. Chat opening has its own recovery.
        try:
            page.keyboard.press('Escape')
            chats=page.locator('header button[aria-label="对话"], header button[aria-label="Chats"]')
            if chats.count()==1 and chats.is_visible():
                chats.click(force=True,timeout=5000)
                page.locator('#pane-side').wait_for(state='visible',timeout=5000)
        except BrowserError:pass


def observed_chat_type(page):
    return page.evaluate(r"""() => {
      const ids=[...document.querySelectorAll('#main [data-id]')].map(e=>e.getAttribute('data-id'));
      if(ids.some(id=>id.includes('@g.us')))return 'group';
      const labels=[...document.querySelectorAll('#main header [aria-label]')].map(e=>e.getAttribute('aria-label'));
      if(labels.some(s=>/group (video )?call|群组视频通话|群組視訊通話/i.test(s)))return 'group';
      if(ids.some(id=>id.includes('@c.us')))return 'direct';
      return 'unknown';
    }""")
