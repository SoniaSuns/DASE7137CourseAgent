"""Generate a local, read-only diagnostic report from durable evidence."""
import csv
import io
import html
import json
import re
from datetime import datetime
from pathlib import Path
from agent_status import snapshot
from run_trace import RunTrace
from trace_report import inspect_trace
from report_io import atomic_text,exclusive

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'reports/trace_eval_20261009'
STATE=ROOT/'reports/shared_chrome/state'
CLEAN=RunTrace(OUT).clean


def read(path):
    try:return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return {}


def save(name,value):
    atomic_text(OUT/name,json.dumps(CLEAN(value),ensure_ascii=False,indent=2))


WORKFLOW=[
    ('1 接收请求','固定规则','两个群的新请求必须真实 @ 当前账号；同一请求人的有效追问/范围选择可免再次 @。','模型不决定哪些群消息可以执行。','chrome_mentions.py'),
    ('2 查任务与资料','固定规则 + 模型选择','最多保留 10 个可调用任务编码；刷新 files 索引；纯继续指令恢复原任务未完成范围。','新任务中，模型选择与请求相关的文件及读取页。','chrome_task_memory.py / chrome_file_library.py'),
    ('3 查课程及表单','固定工具 + 模型选择','按课程 ID 读取目录、实际表单；字段和目标必须来自真实证据。','模型选择需要检查的活动、设置、依赖项。','chrome_moodle.py / chrome_planner.py'),
    ('4 理解目标与影响','模型决策 + 固定校验','工具类型、字段、目标、证据、日期约束由代码校验；缺失证据可补读并有限重规划。','模型解释 syllabus、锚定时间、排列文件、提出相关修改及澄清问题。','chrome_planner.py / run_shared_chrome_agent.py'),
    ('5 选范围与审批','固定状态机','范围选择不等于写入批准；当前聊天、请求人、运行和准确审批码须匹配；过期不能保存。','模型可解释范围和提议修改；不能自行批准。','chrome_approval.py'),
    ('6 执行','固定类型工具','执行前读回检查；只执行批准清单；用原生表单、文件选择器、精确活动 ID 移动和章节显示/隐藏工具。','模型不能生成任意浏览器 JS，也不能绕过保存前检查。','chrome_moodle.py / chrome_structure.py / chrome_section_visibility.py / chrome_resources.py / chrome_group_changes.py'),
    ('7 验证与恢复','固定流程 + 有界重规划','保存后读回，保留成功回执；可能保存但未确认时先核实，不能直接重放。章节可见性前置操作完成后，自动重新检查并规划原任务剩余目标。','确需修订目标或计划时模型再规划，并重新进入当前计划审批。','chrome_approval.py / run_shared_chrome_agent.py'),
    ('8 回复与观测','固定流程','原聊天回复；分段发送并查验；记录状态、步骤、输入输出和异常；回放只读。','模型给出任务说明，发送是否成功由工具证据判断。','chrome_delivery.py / task_observability.py / run_trace.py'),
]


def classify(reason):
    s=reason.lower()
    if 'whatsapp' in s or 'outgoing message' in s:return '消息投递 / 发送读回'
    if 'planner' in s:return '模型调用 / CLI'
    if 'login' in s and 'requires login' in s:return '浏览器 / 登录页面'
    if 'approved form' in s or 'settings changed' in s:return '执行前状态校验'
    if 'saved values' in s or 'save' in s or 'readback' in s:return '保存 / 读回验证'
    if 'scope' in s or 'impact review' in s or 'inspected' in s:return '规划 / 证据及范围校验'
    if 'browser' in s:return '浏览器动作（根因未确认）'
    if 'expir' in s:return '审批有效期'
    return '需要进一步证据'


def failures():
    groups={}
    for path in STATE.glob('*.json'):
        doc=read(path)
        if not isinstance(doc,dict) or not re.fullmatch('[a-f0-9]{12}',str(doc.get('run',''))):continue
        groups.setdefault(doc['run'],[]).append((path,doc))
    traces={}
    for path in (STATE/'traces').glob('*/trace.jsonl'):
        if not re.fullmatch('[a-f0-9]{12}',path.parent.name):continue
        try:
            events,summary=inspect_trace(path.parent)
        except (OSError,ValueError,KeyError):continue
        if summary['first_observed_error']:
            traces[path.parent.name]=(events,summary);groups.setdefault(path.parent.name,[])
    catalog=OUT/'failed_requests.json'
    # Never replace the durable catalog with an empty fallback on a transient
    # read error or malformed file; leave it intact and let the watcher retry.
    previous=json.loads(catalog.read_text(encoding='utf-8-sig')).get('cases',[]) if catalog.exists() else []
    merged={r['run']:r for r in previous if isinstance(r,dict) and re.fullmatch('[a-f0-9]{12}',str(r.get('run','')))}
    annotations=read(OUT/'failure_annotations.json')
    rows=[]
    for run,items in groups.items():
        bad=[(p,d) for p,d in items if d.get('phase') in {'FAILED','EXPIRED','INTERRUPTED','NEEDS_READBACK'} or p.name.startswith('execution-failure-')]
        if not bad and run not in traces and run not in merged and not annotations.get(run,{}).get('reported_issue'):continue
        states=[(p,d) for p,d in items if d.get('task')]
        states.sort(key=lambda x:(bool(re.fullmatch('[a-f0-9]{12}',x[0].stem)),x[0].stat().st_mtime),reverse=True)
        state=states[0][1] if states else {}
        failed=next((d for p,d in bad if d.get('reason') and not p.name.startswith('reconciled-')),state)
        reason=failed.get('reason') or ('Approval expired.' if failed.get('phase')=='EXPIRED' else '历史记录没有保留完整异常文本。')
        execution=next((d for p,d in items if p.name.startswith('execution-failure-')), {})
        frames=execution.get('frames',[])
        row={'run':run,'task_id':state.get('task_id','历史记录未保存任务编码'),'request':state.get('task','历史记录未保存请求正文'),
             'phase':state.get('phase','EXECUTION_FAILURE'),'reason':reason,'layer':classify(reason),
             'first_observed_step':frames[-1] if frames else '历史日志不足，无法定位最早步骤',
             'possible_save':execution.get('possible_save','unknown'),'verified_before_failure':execution.get('verified_changes','unknown'),
             'confidence':'历史部分证据；最终错误提示不是已确认根因',
             'evidence':[str(p.relative_to(ROOT)).replace('\\','/') for p,d in items],
             'reconciliation':next((d.get('reason','') for p,d in items if p.name.startswith('reconciled-')),''),
             'trace_available':False}
        trace=STATE/'traces'/run/'trace.jsonl'
        if trace.exists():
            try:events,summary=inspect_trace(trace.parent)
            except (OSError,ValueError,KeyError):events=[];summary={'first_observed_error':None}
            row.update(trace_available=True,trace_summary=summary)
            if summary['first_observed_error']:
                event=summary['first_observed_error'];row.update(layer=event['layer'],first_observed_step=event['operation'],confidence='已记录最早异常；需结合后续恢复判断根因')
                errors=[]
                for e in events:
                    if e.get('event')!='error' or not re.fullmatch(r'[a-f0-9]{32}-error\.json',e.get('artifact','')):continue
                    error=read(trace.parent/e['artifact'])
                    if error.get('message') and error['message'] not in errors:errors.append(str(error['message'])[:2000])
                row['observed_errors']=errors
                if errors:row['reason']=errors[0]
                row['evidence'].append(trace.relative_to(ROOT).as_posix())
                row['last_error_at_ns']=max(e.get('at_ns',0) for e in events if e.get('event')=='error')
        old=merged.get(run,{})
        row={**old,**row}
        row['evidence']=list(dict.fromkeys(old.get('evidence',[])+row.get('evidence',[])))
        row['observed_errors']=list(dict.fromkeys(old.get('observed_errors',[])+row.get('observed_errors',[])))
        row['first_cataloged_at']=old.get('first_cataloged_at',datetime.now().astimezone().isoformat())
        merged[run]=CLEAN(row)
    for run,row in merged.items():
        note=annotations.get(run,{})
        row.update({k:v for k,v in note.items() if k!='evidence'})
        row['evidence']=list(dict.fromkeys(row.get('evidence',[])+note.get('evidence',[])))
        row.setdefault('diagnosis','审批有效期已过，旧批准不能继续执行。' if row.get('phase')=='EXPIRED' else
                       '尚未确认根因；请结合原始错误和证据查看，不能仅凭 Failed 文案判断。')
        row.setdefault('handling','保留历史结果；续办时重新读取状态并获取当前计划审批。' if row.get('phase')=='EXPIRED' else '尚无已确认的修复结论。')
        row['outcome']=('该运行已完成，保留中途异常' if row.get('phase')=='DONE' else
                        '审批过期（不等于代码故障）' if row.get('phase')=='EXPIRED' else
                        '中断或等待核实，见当前任务状态' if row.get('phase') in {'INTERRUPTED','NEEDS_READBACK'} else
                        '用户报告的流程问题（不等于执行失败）' if row.get('reported_issue') else
                        '该运行曾失败或记录异常')
    return sorted((CLEAN(r) for r in merged.values()),key=lambda r:r.get('last_error_at_ns',0),reverse=True)


def write_failure_csv(rows):
    columns=[('Run','run'),('任务编号','task_id'),('请求','request'),('记录类型/结果','outcome'),
             ('状态','phase'),('出错层面','layer'),('原始错误','reason'),('确认原因/诊断','diagnosis'),
             ('已做处理/后续','handling'),('最早异常步骤','first_observed_step'),('证据可信度','confidence'),
             ('可能已保存','possible_save'),('失败前已核实修改数','verified_before_failure'),('证据路径','evidence')]
    stream=io.StringIO(newline='');writer=csv.writer(stream);writer.writerow([c[0] for c in columns])
    for row in rows:
        values=[]
        for _,key in columns:
            v=row.get(key,'');v='; '.join(v) if isinstance(v,list) else str(v)
            # Keep request/error text inert when the CSV is opened in Excel.
            if v.lstrip().startswith(('=','+','-','@')):v="'"+v
            values.append(v)
        writer.writerow(values)
    atomic_text(OUT/'failed_requests.csv',stream.getvalue(),encoding='utf-8-sig')


def esc(value):return html.escape(str(value))


def trace_tool_stats():
    stats={}
    for path in (STATE/'traces').glob('*/trace.jsonl'):
        try:events,_=inspect_trace(path.parent)
        except (OSError,ValueError,KeyError):continue
        for event in events:
            if event['event'] not in {'input','output','error'}:continue
            key=(event['layer'],event['operation'])
            row=stats.setdefault(key,{'layer':key[0],'operation':key[1],'input':0,'output':0,'error':0,'latest_ns':0})
            row[event['event']]+=1
            if event['at_ns']>row['latest_ns']:
                row.update(latest_ns=event['at_ns'],last_event=event['event'],last_run=event['run'],last_artifact=event['artifact'])
    for row in stats.values():row['unfinished']=max(0,row['input']-row['output']-row['error'])
    return sorted(stats.values(),key=lambda r:(r['layer'],r['operation']))


def table(headers,rows):
    return '<table><thead><tr>'+''.join('<th>'+esc(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+esc(c)+'</td>' for c in row)+'</tr>' for row in rows)+'</tbody></table>'


def _main():
    OUT.mkdir(parents=True,exist_ok=True)
    cases=read(ROOT/'reports/evaluations/latest.json');status=CLEAN(snapshot());rows=failures()
    save('failed_requests.json',{'generated':datetime.now().astimezone().isoformat(),'count':len(rows),'cases':rows})
    write_failure_csv(rows)
    save('workflow_map.json',[dict(zip(['stage','control','fixed','model','source'],r)) for r in WORKFLOW])
    tools=[
        ('WhatsApp 读取','监听心跳：'+str(status['listener'])+'；阶段：'+str(status['listener_step']),'心跳只证明监听活性；不能证明每条消息都被接收。'),
        ('WhatsApp 发送','本次验证：本地分段发送/发送不确定性回归','未发送诊断消息到群；当前任务送达情况见实际任务状态。'),
        ('Codex 规划','本次未发起线上模型评测','正在运行的旧版本进度文件可能缺少 Run/Attempt；不能直接归到某个当前任务。'),
        ('Moodle 表单/移动','本次验证：本地浏览器夹具及工作流回归','真实只读复查证据：reports/resume_content_20261009/live_preflight.json；不是本次生产保存验证。'),
        ('文件读取/上传/替换','本次验证：索引、哈希、替换、审批边界回归','没有为评测向生产课程上传文件；Office/PDF 内容编辑仍不属于此运行入口能力。'),
        ('分组/分组集合','已有类型工具；完整单元回归覆盖','不据单元测试宣称生产账户权限或登录当前有效。'),
        ('Trace 记录','磁盘代码版本：2026-10-09-move-visibility；运行版本：'+str(status['runtime_version']),'新增保存按钮就绪检查、点击/提交事件、写请求计数和脱敏点击异常。不能补回历史缺失轨迹。'),
    ]
    stats=trace_tool_stats()
    save('tool_status.json',{'captured_at':datetime.now().astimezone().isoformat(),'status':status,'tools':tools,'recorded_calls':stats})
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Course Agent 诊断总览</title><style>body{font:16px/1.65 system-ui;max-width:1250px;margin:32px auto;padding:0 22px;color:#192b3d;background:#f8fafc}h1,h2{line-height:1.3}table{border-collapse:collapse;width:100%;background:white;margin:20px 0}td,th{border:1px solid #d4dde6;padding:10px;text-align:left;vertical-align:top}th{background:#e9eff7}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#edf2f7;padding:16px}details{background:white;border:1px solid #ccd7e2;margin:12px 0;padding:14px}summary{cursor:pointer;font-weight:600}.note{border-left:4px solid #3d72b4;padding:12px 18px;background:#eaf2fc}a{color:#175ba7}</style>'''
    page+='<h1>Course Agent：从哪一步开始出错</h1><p>生成时间：'+esc(datetime.now().astimezone().isoformat())+'。后台汇总程序运行时每 30 秒刷新；也可手动重新运行生成器。失败清单保留历史记录，不随任务编号淘汰。</p>'
    page+='<div class="note">可调用任务记忆最多 10 个；失败审计档案独立保留。最新修复支持原请求人的受阻任务修复指令；经只读确认已移动而仅可见性不同后，保留回执并重新规划审批，禁止重复移动。</div>'
    page+='<h2>现在是否在运行</h2>'+table(['监听','版本','队列'],[[status['listener'],status['runtime_version'],status['queue_size']]])
    page+=table(['聊天','任务','Run','状态','当前可观察步骤'],[[chat,row.get('task_id'),row.get('run'),row.get('assessment'),row.get('step')] for chat,row in status['tasks'].items()])
    page+='<p>旧版本没有每步计时，不能仅凭 PLANNING 认定仍在推进，也不能凭 APPROVAL 认定卡死。有活动任务时延期部署，避免重启使审批失效。</p>'
    page+='<h2>固定流程与模型自由决策</h2>'+table(['阶段','由谁控制','代码固定的部分','模型可以决定的部分','主要源码'],WORKFLOW)
    page+='<h2>工具调用状态与证据边界</h2>'+table(['工具层','可证实状态','不能据此推断的内容'],tools)
    page+=table(['WhatsApp 聊天','距成功读取秒数','读取次数','连续失败'],[[chat,round(r['last_success_age_seconds'],1) if r.get('last_success_age_seconds') is not None else '未知',r.get('reads'),r.get('consecutive_failures')] for chat,r in status['chats'].items()])
    page+='<p>以下调用统计来自实际保留下来的完整 trace；历史日志缺失的调用不计入。</p>'
    page+=table(['层','调用','发起','返回','异常','未返回','最近 Run'],[[r['layer'],r['operation'],r['input'],r['output'],r['error'],r['unfinished'],r['last_run']] for r in stats]) if stats else '<p>尚无新版本的真实调用轨迹；不能将空统计解释为工具正常或没有发生失败。</p>'
    page+='<h2>失败/过期/中断请求目录</h2><p>共 '+str(len(rows))+' 个去重 Run；包含暂停、过期和中断，不全部代表代码 bug。同一个任务的多次重试保留独立 Run。'
    page+='<a href="failed_requests.csv">Excel 可打开的 CSV</a> · <a href="failed_requests.json">完整结构化目录</a></p>'
    for row in rows:
        page+='<details><summary>'+esc(row['run']+' · '+row['layer']+' · '+row['phase'])+'</summary>'
        for label,key in [('请求','request'),('原始原因','reason'),('最早可定位步骤','first_observed_step'),('证据置信度','confidence'),('确认诊断','diagnosis'),('处理情况','handling'),('记录类型','outcome'),('相关问题','related_issue'),('后续只读核实','reconciliation')]:
            if row.get(key):page+='<p><b>'+label+'：</b>'+esc(row[key])+'</p>'
        page+='<p>完整 trace：'+('有' if row['trace_available'] else '无（历史未记录，不补造）')+'</p><p>证据：'
        page+=' · '.join('<a href="../../'+esc(p)+'">'+esc(Path(p).name)+'</a>' for p in row['evidence'] if (ROOT/p).exists())+'</p></details>'
    page+='<h2>'+esc(cases.get('total',0))+' 个代表性评测</h2><p>'+esc(cases.get('passed',0))+' / '+esc(cases.get('total',0))+' 通过。'+esc(cases.get('limitation',''))+'</p>'
    regression_path=ROOT/'reports/move_visibility_20261009/regression.json'
    if not regression_path.exists():regression_path=ROOT/'reports/recent_results_20261009/regression.json'
    if not regression_path.exists():regression_path=ROOT/'reports/renderer_dispatch_20261009/regression.json'
    if not regression_path.exists():regression_path=ROOT/'reports/submit_dispatch_20261009/regression.json'
    if not regression_path.exists():regression_path=ROOT/'reports/blocked_repair_20261009/regression.json'
    if not regression_path.exists():regression_path=OUT/'regression.json'
    regression=read(regression_path)
    page+='<p>完整单元回归：'+esc(regression.get('tests','待运行'))+' 项；失败 '+esc(regression.get('failures','未知'))+'；错误 '+esc(regression.get('errors','未知'))+'。<a href="../../'+esc(regression_path.relative_to(ROOT).as_posix())+'">完整回归结果</a></p>'
    page+='<p>与上次通过率对比：'+esc(cases.get('previous_pass_rate') if cases.get('previous_pass_rate') is not None else '首次建立基线，无历史通过率可比较')+'；新增失败：'+esc(cases.get('new_regressions',[]))+'。<a href="../../eval_cases.json">请求及期望行为</a> · <a href="../evaluations/latest.json">评测结果</a></p>'
    page+=table(['编号','典型请求','期望行为','结果'],[[c['id'],c.get('request',''),c.get('expected',''), '通过' if c['passed'] else '失败'] for c in cases.get('cases',[])])
    page+='<h2>下次如何定位</h2><ol><li>从 WhatsApp 结果取得 Run，先查看监听和任务状态，区分等审批、等提问、正在执行、等待读回。</li><li>生成该 Run 的只读回放，展开最早异常的输入、输出和堆栈，再看后续是否已恢复。未结束的 input 表示调用尚未返回或进程中断，不能直接判定根因。</li><li>按模型、校验、浏览器、投递层修复，重新跑小评测集，对比新增失败；需要时再跑完整单元测试。</li></ol>'
    page+='<pre>在 C:\\Users\\Sonia\\DASE_Agent 执行：\n.\\v2\\status_v2.ps1\n.\\v2\\evaluate_v2.ps1\n.\\v2\\.venv\\Scripts\\python.exe .\\v2\\course_agent_app\\build_diagnostic_report.py\n.\\v2\\.venv\\Scripts\\python.exe .\\v2\\course_agent_app\\trace_report.py &lt;Run ID&gt;</pre>'
    page+='<p>轨迹目录：reports/shared_chrome/state/traces/&lt;Run&gt;/。记录每次规划的实际 prompt、schema、结构化模型结果、类型工具输入输出、错误堆栈及耗时。记录的是公开输入输出，不包含模型内部隐藏推理，也不逐条记录 DOM 点击或全部网络流量。二进制仅记录长度和哈希。密码、cookie、sesskey、审批码脱敏；选中文件正文可留在本机轨迹中。未配置外部日志服务；目前轨迹无自动清理策略。</p>'
    page+='<p><a href="fixtures/aaaaaaaaaaaa/replay.html">查看明确标记的合成失败回放示例</a>（演示格式，不是实际 Moodle 故障）。日志写入异常不会阻断原工具，会显示 trace_write_error；磁盘故障可能导致轨迹缺段。</p></html>'
    atomic_text(OUT/'diagnostic-overview.html',page)
    print(json.dumps({'report':str(OUT/'diagnostic-overview.html'),'failure_runs':len(rows),'evaluation_passed':cases.get('passed'),'runtime_version':status['runtime_version']},ensure_ascii=True))


def main():
    with exclusive(OUT/'report-write.lock'):_main()


if __name__=='__main__':main()
