"""Collect a bounded, structurally identified incoming document group."""
import time

from chrome_whatsapp_attachments import MAX_FILES

BATCH_QUIET_SECONDS = 1.0
BATCH_MAX_SECONDS = 4.0
BATCH_RECHECK_SECONDS = 3.0


class BatchObservationUnavailable(ValueError):
    """The current DOM cannot establish identity; not evidence of a change."""
    def __init__(self,source,detail):
        self.source=source
        self.message_id=source['attachment_message_id']
        self.detail=detail
        name=', '.join(d['name'] for d in source['attachments'])
        super().__init__(name+': '+detail+'. The file batch could not be verified.')


class BatchSourceChanged(ValueError):
    def __init__(self,source,detail):
        self.message_id=source['attachment_message_id']
        self.detail=detail
        name=', '.join(d['name'] for d in source['attachments'])
        super().__init__(name+': '+detail+'. The file batch was not completed.')


def message_id(row):
    return row.get('message_id') or row.get('dom_id') or row.get('id')


def validate_batch_source(source, anchor, rows):
    matches=[r for r in rows if message_id(r)==source['attachment_message_id']]
    if not matches:raise BatchObservationUnavailable(source,'WhatsApp message is temporarily unavailable')
    incomplete=[]
    for row in matches:
        fields=[(row.get('direction'),'incoming','message direction'),
                (row.get('sender_id') or row.get('sender_display_name'),anchor['sender'],'sender'),
                (row.get('attachment_batch_id'),anchor['attachment_message_id'],'attachment group'),
                (row.get('attachments'),source['attachments'],'attachment list')]
        missing=[]
        for observed,expected,label in fields:
            if not observed or (label=='message direction' and observed=='unknown'):
                missing.append(label)
            elif observed!=expected:
                raise BatchSourceChanged(source,label+' changed')
        if not row.get('attachment_fingerprint'):missing.append('message identity')
        # Do not treat complete, conflicting evidence as a rendering delay.
        if not missing and row['attachment_fingerprint']!=source['attachment_fingerprint']:
            raise BatchSourceChanged(source,'message content or mention changed')
        incomplete.extend(missing)
    if incomplete:
        raise BatchObservationUnavailable(source,'WhatsApp has not exposed '+', '.join(dict.fromkeys(incomplete)))


def verify_downloaded_batch(page,anchor,expected,read_records):
    """Retry incomplete observations only, never a transfer or conflicting source.

    Two complete matching samples establish that a transient remount has settled.
    Saved bytes alone cannot satisfy a missing or changed source identity.
    """
    deadline=time.monotonic()+BATCH_RECHECK_SECONDS
    matching=0;last_error=None;attempts=0
    while time.monotonic()<deadline:
        attempts+=1
        try:
            rows=read_records()
            for source in expected:validate_batch_source(source,anchor,rows)
            if batch_sources(anchor,rows)!=expected:
                raise ValueError('The attachment group grew during download; the complete request has not been read.')
            matching+=1
            if matching==2:return {'status':'verified','observations':attempts}
        except BatchObservationUnavailable as exc:
            last_error=exc;matching=0
        page.wait_for_timeout(100)
    detail=last_error.detail if last_error else 'The attachment group did not settle.'
    source=last_error.source if last_error else anchor
    raise BatchObservationUnavailable(source,
        'Downloaded bytes are retained, but post-download verification did not finish: '+detail)


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
