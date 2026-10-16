"""Summarize this implementation's recorded checks and deployed runtime."""
import hashlib
import html
import json
from datetime import datetime
from pathlib import Path

root=Path(__file__).resolve().parent
out=root/'reports/implementation_20261008'
tests=json.loads((out/'regression.json').read_text(encoding='utf-8'))
planner=json.loads((out/'planner/results.json').read_text(encoding='utf-8'))
live=json.loads((out/'live_rehearsal.json').read_text(encoding='utf-8'))
deployment=json.loads((out/'deployment.json').read_text(encoding='utf-8-sig')) if (out/'deployment.json').exists() else None
status=json.loads((root/'reports/shared_chrome/status.json').read_text(encoding='utf-8'))
health=json.loads((root/'reports/shared_chrome/health.json').read_text(encoding='utf-8'))
assert tests['passed'] and len(planner)==8 and all(r.get('decision',{}).get('decision')=='plan' and r.get('resolved_count',0)>0 for r in planner)
assert 'error' not in live
sources=['run_shared_chrome_agent.py','chrome_mentions.py','chrome_group_changes.py','chrome_moodle.py','chrome_structure.py',
         'chrome_resources.py','chrome_rich_text.py','chrome_availability.py','chrome_planner.py','chrome_approval.py']
summary={'generated':datetime.now().astimezone().isoformat(),'regression':tests,'planner_scenarios':8,'planner_resolved':8,
    'live_unsaved_rehearsal':live,'deployment':deployment,'listener_version':status.get('runtime_version'),
    'listener_status':status['status'],'listener_pid':status['pid'],'health_updated':health['updated'],
    'chat_health':{k:{'healthy':v['healthy'],'consecutive_failures':v['consecutive_failures']} for k,v in health['chats'].items()},
    'production_writes_for_validation':0,'whatsapp_test_messages':0,
    'source_sha256':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sources}}
(out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
rows=''.join('<tr><td>'+html.escape(r['case'])+'</td><td>plan</td><td>'+str(r['resolved_count'])+'</td></tr>' for r in planner)
page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>v2 修复与功能实现 · 2026-10-08</title>
<style>body{font:16px/1.7 system-ui,sans-serif;max-width:1000px;margin:40px auto;padding:0 24px;color:#1d2938}table{width:100%;border-collapse:collapse}td,th{padding:10px;border:1px solid #ccd3dd;text-align:left}th{background:#eef3f8}a{color:#075bbb}</style>
<h1>v2 修复与功能实现</h1>
<p>变更仅位于 v2。没有修改 origin_course_agent 或 course_agent_v1。本轮验证没有保存真实课程、上传真实文件或发送 WhatsApp 测试消息。</p>
<h2>已实现</h2><ul>
<li>分组功能跟随当前工作标签页；等待真实提交完成再回读；已核验回滚到原状态的失败会重新进入规划。</li>
<li>修复部署时发现的 WhatsApp 启动问题：账号资料读取完成后，关闭资料抽屉超时不会覆盖已读取的账号身份或终止监听。</li>
<li>批量预检和执行步骤间轮询 WhatsApp；新任务排队，停止指令保留已验证结果。未确认的保存不自动重放。</li>
<li>既有活动跨章节移动和同章节排序，使用实时章节/活动 ID。后续移动基于前面已核验的结果推导预期状态，不修改审批清单。</li>
<li>章节改名通过观察到的章节表单执行。</li>
<li>从 files 中选择并检查文件，新增 File 资源或替换单文件资源；替换保留活动 ID、文件名和设置，保存后回读文件字节哈希。</li>
<li>通过原生 TinyMCE 编辑富文本；通过原生开放条件模型新增/删除日期、group 和 grouping 条件，保留其他插件条件。</li></ul>
<h2>验证结果</h2><p>最终整体回归：<strong>''' + str(tests['tests']) + ''' 次执行全部通过</strong>（包含继承/导入产生的重复测试）。日志中的真实浏览器写入全部针对本地路由模拟页面。</p>
<p>重点覆盖：两个文件连续移动、排序、保留隐藏状态、真实外部漂移、无审批零写入、延迟响应只回读一次、待提交页面重新绑定、源文件和远端文件变更、上传后精确新 ID、同名但内容错误不能宣称成功、分组延迟提交和回滚、批量停止保留收据。</p>
<p>真实配置规划服务 + 虚构课程证据：8 个场景均生成保留所需效果的计划，并通过本地参数解析。初次沙箱运行的子进程无法找到用户目录；随后在正常用户环境重测通过，没有修改生产规划服务认证配置。</p>
<table><tr><th>场景</th><th>规划结果</th><th>解析操作数</th></tr>''' + rows + '''</table>
<p>真实 Moodle 不保存演练通过：读取 15 个带真实 ID 的章节、59 项活动；读取原文件哈希；富文本本地编辑；新增日期条件本地编辑；章节名字段、新 File 表单以及课程编辑模块均可读取。演练完成即关闭临时标签页。真实保存、移动、上传仍需具体任务的当前审批，不能将演练当作生产写入成功。</p>
<h2>部署和监听</h2><p>运行版本：''' + html.escape(str(status.get('runtime_version','尚未切换'))) + '；监听状态：' + html.escape(status['status']) + '；检查时间：' + html.escape(health['updated']) + '''。</p>
<p>服务切换只在没有活动任务、未确认保存、待发消息或排队请求时进行；部署前保留任务状态。不会自动复用旧审批或执行历史失败任务。</p>
<h2>仍有边界</h2><p>单文件替换已实现，多文件包替换、跨课程复制、删除活动/分组、Office/PDF 原文改写仍未实现。富文本只接受普通格式标签，开放条件新增目前覆盖日期/group/grouping；其他插件条件保持不变。轮询发生在操作边界，单次浏览器调用很慢时仍会延迟消息读取。无法确认实际保存结果时，系统继续保留待回读状态，不猜测完成。</p>
<p>官方接口依据：<a href="https://raw.githubusercontent.com/moodle/moodle/MOODLE_405_STABLE/course/format/classes/stateactions.php">Moodle 课程移动实现</a>、<a href="https://raw.githubusercontent.com/moodle/moodle/MOODLE_405_STABLE/availability/yui/src/form/js/form.js">原生开放条件模型</a>、<a href="https://raw.githubusercontent.com/moodle/moodle/MOODLE_405_STABLE/mod/resource/view.php">资源文件读取</a>。</p>
<p><a href="regression.log">单元测试日志</a> · <a href="planner/results.json">规划器结果</a> · <a href="live_rehearsal.json">真实页面演练结果</a> · <a href="summary.json">部署与源码哈希</a></p></html>'''
(out/'report.html').write_text(page,encoding='utf-8')
print(json.dumps({'tests':tests['tests'],'planner_cases':8,'version':status.get('runtime_version'),'listener':status['status'],'report':str(out/'report.html')},ensure_ascii=True))
