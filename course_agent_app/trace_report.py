"""Offline inspection of a trace: never import or invoke a browser/executor."""
import argparse
import html
import json
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parent/'reports/shared_chrome/state/traces'


def inspect_trace(folder):
    folder=Path(folder);events=[];invalid=0
    for line in (folder/'trace.jsonl').read_text(encoding='utf-8').splitlines():
        try:events.append(json.loads(line))
        except ValueError:invalid+=1
    events.sort(key=lambda e:e['at_ns'])
    first=next((e for e in events if e['event']=='error'),None)
    started={e['span'] for e in events if e['event']=='input'}
    ended={e['span'] for e in events if e['event'] in {'output','error'}}
    summary={'events':len(events),'first_observed_error':first,'incomplete_spans':sorted(started-ended),
             'unreadable_lines':invalid,'interpretation':'First recorded exception is an investigation starting point, not proof of the root cause.'}
    return events,summary


def build(folder):
    folder=Path(folder);events,summary=inspect_trace(folder)
    first=summary['first_observed_error']
    heading=('最早记录的异常：'+first['layer']+' / '+first['operation']) if first else '未记录异常；仍需检查未完成步骤及实际结果。'
    rows=[]
    for index,event in enumerate(events,1):
        name=event['artifact']
        if not re.fullmatch(r'[a-f0-9]{32}-(input|output|error|note)\.json',name):continue
        body=(folder/name).read_text(encoding='utf-8') if (folder/name).exists() else '[artifact missing]'
        rows.append('<details'+(' open' if event['event']=='error' else '')+'><summary>'+html.escape(f"{index}. {event['layer']} / {event['operation']} · {event['event']} · {event.get('duration_seconds','—')} s")+'</summary><pre>'+html.escape(body)+'</pre></details>')
    page='<!doctype html><meta charset="utf-8"><title>Agent trace 回放</title><style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px;color:#182332}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f7;padding:16px}summary{padding:12px;cursor:pointer}details{border:1px solid #ccd5dd;margin:10px 0}</style>'
    page+='<h1>运行轨迹：'+html.escape(folder.name)+'</h1><p>'+html.escape(heading)+'</p><p>这是只读回放，不会发送消息或再次保存 Moodle。首个异常可能被后续重试恢复，不能直接等同于最终根因。</p>'
    page+='<p>未完成调用：'+str(len(summary['incomplete_spans']))+'；损坏日志行：'+str(summary['unreadable_lines'])+'</p>'+''.join(rows)
    (folder/'replay.html').write_text(page,encoding='utf-8')
    (folder/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    return folder/'replay.html'


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('run');args=parser.parse_args()
    if not re.fullmatch('[a-f0-9]{12}|startup',args.run):parser.error('Use the exact run ID.')
    print(build(ROOT/args.run))
