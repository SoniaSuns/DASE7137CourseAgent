"""Compare editor draft links by verified bytes, never by temporary draft IDs."""
import html
import re
from urllib.parse import urlsplit,urlunsplit

ATTR=re.compile(r'''(?i)\b(?:src|href|poster)\s*=\s*(["'])(.*?)\1''',re.S)
DRAFT=re.compile(r'(/draftfile\.php/\d+/user/draft/)\d+(/.+)')


def draft_urls(value):
    urls=[]
    for match in ATTR.finditer(value):
        url=html.unescape(match[2]);parts=urlsplit(url)
        if parts.scheme=='https' and parts.netloc=='moodle.hku.hk' and DRAFT.fullmatch(parts.path):
            if url not in urls:urls.append(url)
    return urls


def comparable(field):
    value=field.get('value');evidence=field.get('draft_content',{})
    if field.get('type')!='textarea' or not isinstance(value,str):return value
    def replace(match):
        url=html.unescape(match[2]);row=evidence.get(url)
        if url not in draft_urls(match[0]) or not isinstance(row,dict):return match[0]
        sha=row.get('sha256','');size=row.get('size')
        if not re.fullmatch('[a-f0-9]{64}',sha) or type(size) is not int or size<0:return match[0]
        parts=urlsplit(url);path=DRAFT.fullmatch(parts.path)
        stable=urlunsplit((parts.scheme,parts.netloc,path[1]+'content-'+sha+'-'+str(size)+path[2],parts.query,parts.fragment))
        # Replace only the actual attribute value, keeping all surrounding HTML exact.
        return match[0][:match.start(2)-match.start()]+stable+match[0][match.end(2)-match.start():]
    return ATTR.sub(replace,value)


def inspect_draft_content(page,fields):
    wanted={f['key']:draft_urls(f['value']) for f in fields if f.get('type')=='textarea' and isinstance(f.get('value'),str)}
    urls=list(dict.fromkeys(url for values in wanted.values() for url in values))
    if not urls:return
    if len(urls)>25:raise ValueError('Too many embedded draft files to verify in one form; no save attempted.')
    records=page.evaluate("""async urls=>{
      const result={}, limit=16*1024*1024;let total=0;
      for(const url of urls){
        const u=new URL(url);
        if(u.origin!==location.origin)throw Error('Draft file origin differs from the current form.');
        const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
        try{
          const response=await fetch(url,{credentials:'same-origin',redirect:'error',cache:'no-store',signal:controller.signal});
          if(response.status!==200||!response.body)throw Error('Draft content is unavailable.');
          const reader=response.body.getReader(),chunks=[];let size=0;
          while(true){const {done,value}=await reader.read();if(done)break;
            size+=value.length;total+=value.length;
            if(size>limit||total>32*1024*1024){await reader.cancel();throw Error('Draft content size limit.');}
            chunks.push(value);
          }
          const bytes=new Uint8Array(size);let offset=0;
          for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
          const hash=await crypto.subtle.digest('SHA-256',bytes);
          result[url]={sha256:[...new Uint8Array(hash)].map(v=>v.toString(16).padStart(2,'0')).join(''),size};
        }finally{clearTimeout(timer);}
      }
      return result;
    }""",urls)
    for field in fields:
        if wanted.get(field['key']):field['draft_content']={url:records[url] for url in wanted[field['key']]}
