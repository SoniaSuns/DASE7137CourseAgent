# DASE7137 Course Agent

**[下载 Chrome 插件 ZIP](00_Download/Moodle-Workspace-Chrome.zip?raw=true)** · [详细安装说明](docs/REMOTE_WORKSPACE.md)

Agent 已运行在管理员电脑上。普通用户只需连接现有服务，不需要安装 Python 或 Codex。

## WhatsApp 调用

1. 在已接入的 `DASE7137 - 2026` 或 `test approval` 群中，使用 WhatsApp 的真正 @ 提及选择 agent 账号，再写要求。
2. 例如：`@agent 请检查课程 A 的文件顺序与 syllabus 是否一致，先报告确定的不一致项。`
3. 涉及 Moodle 修改时，检查 Plan，再按消息原样回复 `OK <验证码>`；取消回复 `NO <验证码>`。回复当前问题或确认时无需再次 @。

WhatsApp 使用管理员配置的课程和浏览器。需要操作自己的 Moodle，请使用下面的 Chrome 插件。

## Chrome 插件与现有 agent

1. 下载上方 ZIP 并解压；打开 `chrome://extensions`，开启“开发者模式”，点击“加载已解压的扩展程序”，选择解压目录。
2. 打开 **Moodle Workspace — My Browser** 侧栏。与管理员电脑连接同一局域网，服务地址填写 **`http://192.168.0.124:18791`**。
3. 向管理员领取个人邀请码，填写账号、至少 12 位密码和邀请码注册；以后只需账号密码登录。
4. 在自己的 Chrome 登录 Moodle，打开课程主页，再点“连接当前 Moodle 课程”。可选择上传本地资料，然后在聊天框输入要求。
5. 检查 Plan，点击“确认执行”。执行期间保持 Chrome、侧栏和课程标签页打开。

每个账号拥有独立的任务和资料空间；Moodle 读取和修改发生在该用户自己的浏览器。管理员本机也可使用 `http://127.0.0.1:18791`。局域网 HTTP 不加密，请仅在可信网络使用；公网连接需要另外配置 HTTPS。

当前支持资料读取、分析和普通 Moodle 表单修改；远程排序移动、分组管理及 Moodle 资源上传尚未适配。

## 管理员

生成个人邀请码：

```powershell
.\.venv\Scripts\python.exe course_agent_app\remote_workspace_admin.py invite --output course_agent_app\reports\remote_workspace\invitations\teacher-1.json
```

[部署、网络及账号说明](docs/REMOTE_WORKSPACE.md) · [项目技术说明](docs/PROJECT_DETAILS.md)

仓库包含程序、配置、测试和插件安装包，不包含密钥、浏览器登录数据、用户上传资料和运行日志。
