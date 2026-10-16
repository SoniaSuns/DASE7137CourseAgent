"""Verify complete outgoing messages, including folded and off-screen bodies."""
import time
import re

from whatsapp_web import (WhatsAppSendUnconfirmedError, whatsapp_safe_compose_text,
    compose_box, focus_compose_box, set_action_message_text, scroll_chat_to_bottom,
    multiline_message_matches, first_visible)
from playwright.sync_api import Error as BrowserError


class WhatsAppSendDeferredError(WhatsAppSendUnconfirmedError):
    send_attempted=False


def click_send(page,*,coordinate_fallback=False):
    control=first_visible(page,["button[aria-label='Send']","button[aria-label='Send message']",
        "[data-testid='send']","span[data-icon='send']","span[data-icon='send-filled']",
        "span[data-icon='wds-ic-send-filled']"],timeout_ms=1000)
    if control is None:raise WhatsAppSendDeferredError('The send control is unavailable; no send was attempted.')
    # Never click a second time after a browser timeout: the first click may have sent.
    control.click(timeout=5000,force=True)


def outgoing_snapshots(page):
    return page.evaluate(r"""() => {
      const quoted='[data-testid*="quoted"], blockquote';
      const rendered = element => {
        if (getComputedStyle(element).visibility !== 'visible') return false;
        for (let node=element; node; node=node.parentElement)
          if (getComputedStyle(node).display === 'none') return false;
        return true; // Off-screen rendered bubbles remain valid readback.
      };
      // Recover inline-code delimiters from observed DOM structure, never by
      // stripping punctuation from the expected approval or message text.
      const formattedText = node => {
        if (node.nodeType === Node.TEXT_NODE) return node.textContent;
        if (node.nodeType !== Node.ELEMENT_NODE || node.matches(quoted)) return '';
        const style=getComputedStyle(node);
        if (style.display === 'none' || style.visibility !== 'visible' ||
            /^(SCRIPT|STYLE|TEMPLATE)$/.test(node.tagName)) return '';
        if (node.tagName === 'BR') return '\n';
        let text=[...node.childNodes].map(formattedText).join('');
        if (node.tagName === 'CODE' && node.getAttribute('data-app-text-template') === '`${appText}`')
          text='`'+text+'`';
        return /^(DIV|P|LI)$/.test(node.tagName) ? text+'\n' : text;
      };
      const result=new Map();
      for (const root of document.querySelectorAll('#main [data-id]')) {
        if (root.closest(quoted)) continue;
        const meta=root.querySelector('[data-testid="msg-meta"]');
        const outgoing=!!root.closest('.message-out') ||
          !!root.querySelector('[aria-label="You:"]') ||
          !!meta?.querySelector('[aria-label*="Delivered"], [aria-label*="Read"]') ||
          [...(meta?.querySelectorAll('title') || [])].some(e=>/wds-ic-(?:read|sent|delivered)/i.test(e.textContent));
        if (!outgoing) continue;
        const bodies=[...root.querySelectorAll('[data-testid="selectable-text"], .selectable-text')]
          .filter(e=>rendered(e) && !e.closest(quoted) && !e.parentElement.closest('[data-testid="selectable-text"], .selectable-text'));
        result.set(root.getAttribute('data-id'), {id:root.getAttribute('data-id'),
          text:bodies.map(e=>e.innerText || e.textContent || '').join('\n'),
          formatted_text:bodies.map(formattedText).join('\n'),
          collapsed:!!root.querySelector('[data-testid="caption-read-more-button"], .read-more-button')});
      }
      return [...result.values()];
    }""")


def matching_outgoing_ids(page, expected, *, expand=False, exclude_ids=()):
    records=outgoing_snapshots(page)
    excluded=set(exclude_ids)
    if expand:
        ids=[r['id'] for r in records if r['id'] not in excluded and r['collapsed']]
        if ids:
            for mid in ids:
                selector=page.evaluate("id=>'#main [data-id='+CSS.escape(id)+']'",mid)
                control=page.locator(selector).locator('[data-testid="caption-read-more-button"], .read-more-button').first
                if control.count():control.click(force=True,timeout=2000)
            page.wait_for_timeout(150)
            records=outgoing_snapshots(page)
    # WhatsApp renders consecutive inline spaces as one. Keep newline, words,
    # punctuation and approval digits intact; never accept missing message parts.
    rendered=lambda text:re.sub(r'[ \t\u00a0]+',' ',text)
    return {r['id'] for r in records if r['id'] not in excluded and
            not r['collapsed'] and any(multiline_message_matches(rendered(text),rendered(expected))
                for text in (r['text'],r.get('formatted_text','')))}


def message_parts(message,limit=3000):
    """Bounded review parts; preserve leading commands and legacy reply footers."""
    message=whatsapp_safe_compose_text(message)
    if len(message)<=limit:return [message]
    budget=limit-110
    # Keep the approval response instructions together at the very end.
    head,separator,footer=message.rpartition('\nReply:\n')
    blocks=((head+'\n').splitlines(keepends=True)+['Reply:\n'+footer]) if separator else message.splitlines(keepends=True)
    bodies=[];current=''
    for block in blocks:
        while len(block)>budget:
            if current:bodies.append(current.rstrip());current=''
            bodies.append(block[:budget]);block=block[budget:]
        if len(current)+len(block)>budget:
            bodies.append(current.rstrip());current=''
        current+=block
    if current:bodies.append(current.rstrip())
    return [f'[Part {i+1}/{len(bodies)} — review all parts before replying]\n'+body for i,body in enumerate(bodies)]


def send_message(page,message,*,mention_name='',chat_type='',resume=None):
    parts=message_parts(message)
    start=0;receipts=[]
    if resume:
        if resume.get('parts')!=parts:raise ValueError('Pending message parts no longer match the original review.')
        start=resume['next_part'];receipts=list(resume.get('part_receipts',[]))
        if not 0<=start<=len(parts) or len(receipts)!=start:
            raise ValueError('Pending multipart delivery evidence is inconsistent.')
    for index in range(start,len(parts)):
        try:
            result=send_single_message(page,parts[index],mention_name=mention_name if index==0 else '',chat_type=chat_type)
            receipts.append(result['verified_message_ids'])
        except WhatsAppSendUnconfirmedError as exc:
            exc.delivery_parts=parts;exc.next_part=index;exc.part_receipts=receipts
            raise
    return {'verified_message_ids':[mid for part in receipts for mid in part],'part_receipts':receipts,'parts':parts}


def send_single_message(page,message,*,mention_name='',chat_type=''):
    message=whatsapp_safe_compose_text(message)
    baseline=set()
    try:
        baseline={r['id'] for r in outgoing_snapshots(page)}
        box=compose_box(page)
        focus_compose_box(page,box)
        delivery=set_action_message_text(page,box,message,mention_name,chat_type)
    except BrowserError:
        error=WhatsAppSendDeferredError('WhatsApp is unavailable before sending; delivery is deferred.')
        error.delivery_baseline=sorted(baseline)
        raise error from None
    try:
        click_send(page)
        scroll_chat_to_bottom(page)
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            matched=matching_outgoing_ids(page,message,expand=True,exclude_ids=baseline)
            if matched:return {**delivery,'verified_message_ids':sorted(matched)}
            page.wait_for_timeout(500)
    except BrowserError:pass
    error=WhatsAppSendUnconfirmedError(
        'WhatsApp send was attempted, but the complete new outgoing message could not be confirmed. No resend was attempted.')
    error.delivery_baseline=sorted(baseline)
    raise error
