"""Collect a bounded, structurally identified incoming document group."""
import time

from chrome_whatsapp_attachments import MAX_FILES

BATCH_QUIET_SECONDS = 1.0
BATCH_MAX_SECONDS = 4.0


def message_id(row):
    return row.get('message_id') or row.get('dom_id') or row.get('id')


def validate_batch_source(source, anchor, rows):
    matches=[r for r in rows if message_id(r)==source['attachment_message_id']]
    if not matches or any(
        r.get('direction')!='incoming' or
        (r.get('sender_id') or r.get('sender_display_name'))!=anchor['sender'] or
        r.get('attachment_batch_id')!=anchor['attachment_message_id'] or
        r.get('attachment_fingerprint')!=source['attachment_fingerprint'] or
        r.get('attachments')!=source['attachments'] for r in matches):
        raise ValueError('An attachment in this request changed or is unavailable; the file batch was not completed.')


def batch_sources(anchor, rows):
    sources={anchor['attachment_message_id']:anchor}
    for row in rows:
        mid=message_id(row)
        if mid==anchor['attachment_message_id'] or row.get('attachment_batch_id')!=anchor['attachment_message_id']:
            continue
        if (not mid or row.get('direction')!='incoming' or row.get('self_mentioned') or
            str(row.get('message_text') or '').strip() or not row.get('attachments') or
            (row.get('sender_id') or row.get('sender_display_name'))!=anchor['sender']):
            raise ValueError('The attachment group includes an unrelated message; no complete batch was accepted.')
        source={'sender':anchor['sender'],'attachment_message_id':mid,
                'attachment_fingerprint':row.get('attachment_fingerprint'),
                'attachments':row['attachments']}
        if mid in sources and sources[mid]!=source:
            raise ValueError('Conflicting copies of an attachment message are visible.')
        sources[mid]=source
    if sum(len(r['attachments']) for r in sources.values())>MAX_FILES:
        raise ValueError('This request exceeds the limit of 12 WhatsApp attachments.')
    for source in sources.values():validate_batch_source(source,anchor,rows)
    positions={message_id(r):r.get('attachment_batch_position',0) for r in rows}
    anchor_id=anchor['attachment_message_id']
    return [sources[anchor_id]]+[sources[mid] for mid in sorted(
        (mid for mid in sources if mid!=anchor_id),key=lambda mid:(positions.get(mid,0),mid))]


def collect_batch(page, anchor, rows, read_records):
    """Allow later-mounted siblings to arrive, without adding older messages."""
    started=time.monotonic();changed=started
    sources=batch_sources(anchor,rows)
    while time.monotonic()-started<BATCH_MAX_SECONDS:
        page.wait_for_timeout(100)
        rows=read_records()
        # Previously observed sources may never silently disappear or change.
        for source in sources:validate_batch_source(source,anchor,rows)
        fresh=batch_sources(anchor,rows)
        if fresh!=sources:
            changed=time.monotonic();sources=fresh
        if time.monotonic()-changed>=BATCH_QUIET_SECONDS:return sources
    raise ValueError('The WhatsApp attachment group is still changing; no complete batch was accepted.')
