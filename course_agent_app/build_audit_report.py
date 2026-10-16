"""Render local audit results without connecting to production services."""
import hashlib
import html
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

root = Path(__file__).resolve().parent
out = root / 'reports/audit_20261008'
summary = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
rows = json.loads((out / 'planner_requests.json').read_text(encoding='utf-8'))
expected = {'allowsubmissionsfromdate': '2026-10-26 18:59', 'duedate': '2026-11-02 18:59', 'gradingduedate': '2026-11-09 18:59'}
assert next(r for r in rows if r['case'] == 'date_shift')['proposed_dates'] == expected
assert len(rows) == 7 and not any(r.get('error_type') for r in rows)
unchanged = all(hashlib.sha256((root / n).read_bytes()).hexdigest() == d for n,d in summary['source_sha256'].items())
assert unchanged, 'Production source changed; review before publishing.'
health = json.loads((root / 'reports/shared_chrome/health.json').read_text(encoding='utf-8'))
summary.update({'completed': datetime.now().astimezone().isoformat(),
    'production_source_hashes_unchanged': unchanged,
    'planner_audit': {'scenarios': 7, 'decisions': dict(Counter(r['result']['decision'] for r in rows)),
                      'date_intervals_correct': True, 'evidence': 'synthetic; no production writes',
                      'ambiguity_limit': 'Only one later section; multiple-destination ambiguity not tested.'},
    'listener_observation': {'updated': health['updated'], 'status': health['status'],
         'consecutive_cycle_failures': health['consecutive_cycle_failures'],
         'chats': {k: {'healthy': v['healthy'], 'consecutive_failures': v['consecutive_failures'],
                       'last_success_age_seconds': v['last_success_age_seconds']} for k,v in health['chats'].items()}}})
(out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
plan = '''v2 功能与冲突测试后的实施计划（2026-10-08）

范围：本轮完成测试与评估，以下修复尚未实施或部署。新增文件均位于 v2；没有测试性修改真实课程或发送 WhatsApp 消息。
术语：session 按 Moodle 课程章节 section 理解。浏览器会话间文件传输未单独测试。

阶段 1：修复三个已复现的可靠性缺陷
1. 分组操作动态读取 browser.page；重连或换工作标签页后，读写均使用当前页。
   验收：换页、关闭旧页后，group/grouping 读取与修改不会误报身份变化。
2. 分组和 grouping 保存接入提交完成检查，然后回读。
   验收：延迟提交、拒绝、响应丢失、成员移动部分失败与回滚；结果不确定时先检查实际状态，不重复写入。
3. 批量预检和执行步骤之间轮询 WhatsApp，报告适量进度并处理停止命令。
   验收：长任务仍收取两个聊天的消息；新任务排队，不并发写 Moodle；审批绑定原聊天、请求者和具体任务。

阶段 2：实现 L4/L5 跨章节移动、排序和章节改名
1. 新增明确的移动、排序、章节改名能力；实时读取活动 ID、源/目标章节、前后活动。
2. 移动已有资源，保留活动 ID、文件内容、可见性、时间限制和分组设置，不修改隐藏身份字段来伪造移动。
3. 根据 syllabus 提出直接改动和相关排序，让用户选择范围；明确未支持效果。
4. 批量操作维护预期中间状态，避免把前一步已批准的改动误判为外部冲突。
验收：两个文件移至 Week 5 且顺序正确；同名资源、多候选章节先消歧；隐藏状态保留；真实外部变更触发重新检查；部分失败保留已验证结果；无有效审批时零写入。

阶段 3：完善混合请求和其他能力
1. 结构化区分可执行效果和能力缺口；移动加隐藏、改时间或改章节名，不能只做一部分后宣称完成。
2. 增加上传/替换文件、富文本编辑、开放条件新增/删除；保留无关设置。
3. 跨课程复制/移动单独设计；删除资源或分组检查目标与依赖。
4. 增加中英混合、语义改写、缺文件、同名、多候选、重复消息、重启和断线测试。

阶段 4：分层验收
本地模拟页面及单元测试 → 真实规划器使用虚构证据 → 真实 Moodle 只读检查 → 按具体有效审批进行真实写入 → 回读章节、顺序、文件内容及相关设置。

证据限制
198 次现有测试执行通过，包括继承/导入的重复项，不等于 198 个独立业务功能。
新增 7 项审计测试中 4 项通过，3 项复现缺陷，尚未修复。
真实规划器的 7 个场景使用虚构证据，不能证明真实课程操作成功。
“later week”场景仅一个后续章节，不能判断多候选歧义处理是否正确。
未在生产测试移动、上传、替换或删除；未覆盖全部插件字段、浏览器会话间文件传输或长期跨日运行。
'''
(out / 'implementation_plan.txt').write_text(plan, encoding='utf-8')
labels = {'move_en':'跨章节移动（英文）','move_mixed':'跨章节移动（中英混合）','reorder':'同章节排序',
          'mixed_move_hide':'移动并隐藏（两项都要求）','date_shift':'修改截止日期并保留时间间隔',
          'upload_replace':'替换已有文件内容','ambiguous_section':'移动至后续周次（仅一个候选）'}
tr = ''.join('<tr><td>'+html.escape(labels[r['case']])+'</td><td>'+html.escape(r['result']['decision'])+'</td><td>'+html.escape(r['result']['message'])+'</td></tr>' for r in rows)
page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>v2 功能与冲突测试报告 · 2026-10-08</title>
<style>body{font:16px/1.65 system-ui,sans-serif;max-width:1120px;margin:40px auto;padding:0 24px;color:#18212c}h1,h2{line-height:1.3}table{border-collapse:collapse;width:100%;margin:18px 0}th,td{border:1px solid #d0d7de;padding:12px;text-align:left;vertical-align:top}th{background:#edf3f8}.notice{border-left:5px solid #b86b00;background:#fff5df;padding:16px}a{color:#075bbb}pre{white-space:pre-wrap}</style>
<h1>v2 功能与冲突测试报告</h1><p>2026-10-08 · session 按 Moodle section（课程章节）理解</p>
<p class="notice"><strong>仍有功能缺口和 3 个可复现缺陷。</strong>本轮仅测试与出计划，未实施这些修复；新增文件均在 v2，没有测试性修改真实课程或发送 WhatsApp 消息。</p>
<h2>测试结果</h2><table><tr><th>层次</th><th>结果</th><th>含义</th></tr>
<tr><td>现有单元测试</td><td>198 次执行通过；0 失败/错误；85.882 秒</td><td>包括继承/导入的重复项：171 个唯一测试 ID、144 个唯一测试方法，不等于 198 个业务功能。</td></tr>
<tr><td>新增审计</td><td>7 项：4 通过、1 断言失败、2 错误</td><td>3 个失败用例复现缺陷，没有跳过或标为已修复。</td></tr>
<tr><td>真实规划器 + 虚构证据</td><td>7 场景：6 stop、1 plan；无调用异常</td><td>stop 表示功能不足，不能当作用户功能验收通过。日期计划正确保留前后各 7 天与 18:59。</td></tr></table>
<h2>仍会被拦截或产生冲突的请求</h2><table><tr><th>请求</th><th>当前情况</th><th>影响</th></tr>
<tr><td>跨章节移动已有文件、章节内排序、章节改名</td><td>缺少执行能力</td><td>L4/L5 移至 Week 5 的任务无法完成；刷新和重新登录不会补齐功能。</td></tr>
<tr><td>上传/替换、跨课程复制、删除资源或分组</td><td>缺少专用执行能力</td><td>files 目录有材料不代表具备上传/替换能力。</td></tr>
<tr><td>富文本；新增/删除开放条件</td><td>明确拒绝</td><td>富文本需要编辑器支持；既有条件编辑器只修改日期时间戳。</td></tr>
<tr><td>普通已观察表单字段、现有日期、group/grouping 成员</td><td>有能力，分组有下述缺陷</td><td>有执行能力不等于所有异常路径可靠。</td></tr>
<tr><td>移动 + 隐藏</td><td>本次整体 stop</td><td>没有只隐藏后就宣称完成；仍需实现移动能力。</td></tr></table>
<h2>已复现缺陷（未修复）</h2><ol>
<li><strong>D1：换工作标签页后分组仍引用旧页。</strong>GroupChanges 缓存 browser.page；恢复后会误报页面身份变化。模拟换页测试触发 ValueError。</li>
<li><strong>D2：分组保存可能提前回读。</strong>等待已加载页面的 domcontentloaded 不等于新提交完成。真实 Playwright 本地页面延迟提交 600ms 时，后续 GET 打断提交，导致成员回读不匹配。验证的是时序缺陷，并不表示生产 Moodle 每次都有此延迟。</li>
<li><strong>D3：批量执行不轮询 WhatsApp。</strong>模拟 3 项操作执行完成，轮询调用为 0；长任务中用户可能感觉 agent 不响应。应在操作边界收消息并保持任务串行。</li></ol>
<h2>真实规划器逐场景结果</h2><p>仅虚构课程、表单和文件证据，未调用 Moodle 执行器。每次首次 stop 最多增加一次替代方案审查。</p>
<table><tr><th>场景</th><th>决策</th><th>原始回答</th></tr>''' + tr + '''</table>
<p>日期结果已逐项验证：开放 2026-10-26 18:59，截止 2026-11-02 18:59，评分提醒 2026-11-09 18:59。</p>
<p>后续周次用例只有一个后续章节，规划器推断 Week 5；不能据此判断多候选歧义处理是否正确。</p>
<h2>实施计划</h2><pre>''' + html.escape(plan) + '''</pre>
<h2>证据与复现</h2><p><a href="implementation_plan.txt">计划文本</a> · <a href="summary.json">结构化结果及源码哈希</a> · <a href="unit_audit_20261008.log">单元测试日志</a> · <a href="capability_conflicts_20261008.log">缺陷审计日志</a> · <a href="planner_requests.json">规划器原始结果</a></p>
<p>在 v2/course_agent_app 中执行：</p><pre>..\\.venv\\Scripts\\python.exe -m unittest discover -s . -p "test*.py" -v
..\\.venv\\Scripts\\python.exe -m unittest audit_capability_conflicts -v
..\\.venv\\Scripts\\python.exe -u audit_planner_requests.py</pre>
<p>第二条目前预期复现 3 个失败；审计文件未加入默认 test*.py 发现范围。第三条会调用已配置的规划服务，模型结果可能变化。检查的 6 个生产源码文件哈希保持一致。</p>
<p>结束时监听状态：''' + html.escape(health['status']) + '；记录时间：' + html.escape(health['updated']) + '''。两个聊天均正常读取，连续失败为 0。业务任务失败与监听服务停止是不同状态。</p></html>'''
(out / 'report.html').write_text(page, encoding='utf-8')
assert '\ufffd' not in page and '?' not in plan
print(json.dumps({'report':str(out / 'report.html'),'planner_decisions':summary['planner_audit']['decisions'],
                  'source_unchanged':unchanged,'listener':health['status']},ensure_ascii=True))
