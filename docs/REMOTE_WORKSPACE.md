# 共享 agent 与用户自己的 Moodle 浏览器

中央 agent 仍运行在部署电脑上。远程插件使用每位用户自己的 Moodle 标签页完成读取、表单保存和保存后核验；服务器没有该用户的 Cookie、密码、CDP 连接，也没有从远程任务切换到部署者 Moodle 的路径。

## 当前功能与边界

- 邀请注册、账号密码登录、退出；邀请码单次使用、24 小时有效，服务会话最长 7 天，浏览器重启后需要重新登录。密码使用独立 salt 的 scrypt，服务端仅保存会话令牌的哈希。
- 每个账号独立的文件列表、任务列表、分析记录和状态。每个任务绑定创建它的浏览器设备、Moodle 域名、课程、登录用户及标签页。同账号其他设备可查看历史，不能确认或执行原设备的任务。
- 手动选择并上传本地文件，32 MiB/文件、64 MiB/任务、256 MiB/账号。用户可删除未被活动任务引用的文件。自动目录监控和 Teams 同步不在本版本中。
- 读取当前课程、Participants、Grades、活动内容及页面中已发现的链接。Moodle 文件由用户浏览器下载后上传到该用户的任务，不使用部署者的登录态。
- 中央 Codex 分析所选资料；普通文件读取失败时可以使用现有隔离 Python 分析能力。分析目录按用户和任务分开，不读取部署者的 `files` 资料库。
- 写入支持已读取的普通 Moodle 编辑表单中可编辑的单值输入框、文本框、复选框、下拉框。具体可用字段取决于当前页面和该 Moodle 用户权限。Plan 显示字段的修改前后值；用户在本机插件中确认后执行。
- 本版远程执行器尚未适配拖动/移动活动、分组成员管理、上传或替换 Moodle 资源文件、复杂富文本和文件管理器控件。当前不能声称远程能力与本机原执行器完全相同。禁用字段不能通过远程 Plan 强行改写；日期字段需要当前可编辑。
- 当前一次任务绑定一门课程。浏览器与侧栏在执行时应保持打开；原标签页关闭、登录用户改变或权限不足都会停止该任务。对另一门课程请重新选择课程并创建任务。

部署电脑维护者可以访问服务器上保存的资料和任务文件；“用户隔离”指应用内的访问控制，不表示对服务器管理员端到端加密。用于规划的页面信息及所选文件会发送到部署者配置的模型服务。登录凭据留在各自浏览器。

## 构建并安装插件

在 `v2` 运行：

```powershell
.\prepare_remote_workspace.ps1
```

可分发包：`course_agent_app/reports/remote_extension.zip`。
解压目录：`course_agent_app/reports/remote_extension`。
包内没有邀请码、服务会话令牌、部署者的浏览器资料或 Codex 凭据。

测试用户在 `chrome://extensions` 开启开发者模式，选择“加载已解压的扩展程序”。插件名为 **Moodle Workspace — My Browser**，与旧的本机插件 **Moodle Course Chat** 不同。当前尚未发布 Chrome Web Store。

## 账户与邀请码

由部署电脑维护者为每个用户分别生成邀请码，不要共享同一个账号：

```powershell
.\.venv\Scripts\python.exe course_agent_app\remote_workspace_admin.py invite --output course_agent_app\reports\remote_workspace\invitations\teacher-1.json
```

命令只打印文件路径，不打印邀请码；已存在的输出文件不会被覆盖。将该文件中的邀请码私下提供给对应用户。用户在自己的插件中填写服务地址、账号、至少 12 字符的密码及邀请码注册。以后登录不需要邀请码。插件仅在用户操作时请求连接指定服务和指定 Moodle 网站的权限。

用户登录后，在自己的 Chrome 中打开并登录 Moodle 课程主页，点击“连接当前 Moodle 课程”，选择本地资料并发送任务。Plan 的“确认执行”始终属于该账号、该设备和该任务。

## 服务器及 HTTPS 接入

`chrome_operator_config.json` 的 `remote_workspace` 控制服务：

```json
{"enabled": true, "port": 18791, "public_origin": "", "lan_address": "192.168.0.124", "lan_subnet": "192.168.0.0/24"}
```

它由现有 agent 进程启动，监听 `127.0.0.1:18791`，并按上面的显式配置监听 `192.168.0.124:18791`；与旧的本机 `18790` 桥分离。仅接受本机及配置的局域网网段客户端，不监听 VPN 或全部网卡。现有启动流程仍要求部署电脑的 WhatsApp/Chrome 连接准备好，远程服务随后可用。中央电脑需要保持开机和联网。

同一局域网用户在插件填写 **`http://192.168.0.124:18791`**。部署电脑本机也可使用 `http://127.0.0.1:18791`；其他用户电脑上的 `127.0.0.1` 不会连接到部署电脑。用户确认使用受信任局域网 HTTP：登录、令牌和资料在此链路上没有 TLS 加密，不应将该端口转发到公网。

管理员在部署电脑运行 `.\enable_remote_lan.ps1`，创建仅针对 Private 网络、指定 Wi-Fi 网卡、指定本地 IP/端口和远端 `192.168.0.0/24` 的入站规则。脚本不会关闭防火墙。插件需要重新加载新版安装包，才能请求该 HTTP 主机权限。

如果 DHCP 改变了本机地址，更新 `lan_address`、`lan_subnet` 及对应防火墙规则后提交和重新部署；建议在路由器为本机保留该地址。要关闭局域网监听，将这两个配置设为空字符串并重新部署。`public_origin` 留空表示没有公网 HTTPS 入口，不影响显式启用的局域网入口。

确定 HTTPS 地址后，可以使用以下任一配置模板：

- `deploy/remote-workspace.Caddyfile.example`：已具备 DNS 和可达入口时，在同一电脑运行 Caddy HTTPS 反向代理。
- `deploy/remote-workspace.tunnel.yml.example`：通过已配置账号和域名的隧道，将单一 HTTPS 主机名映射到 `http://127.0.0.1:18791`。

模板需要替换域名、隧道 ID 和凭据文件路径，尚未自动创建公网服务。配置语法参考 [Caddy 请求体限制](https://caddyserver.com/docs/caddyfile/directives/request_body) 和 [Cloudflare 本地隧道配置](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/configuration-file/)。

使用环境变量设置实际公开地址，再通过标准部署脚本重启：

```powershell
$env:COURSE_AGENT_REMOTE_PUBLIC_ORIGIN = 'https://agent.example.edu'
.\deploy_v2.ps1 -Execute
```

该环境变量需要由以后启动 agent 的同一启动环境提供；也可以将实际地址写入受版本管理的配置、提交后按标准流程部署。代理应保留公开 Host 并转发 Authorization。只映射 `18791`，不要公开 Chrome 调试端口或旧的 `18790` 本机桥。公网客户端使用 HTTPS；HTTP 仅允许本机或明确配置的可信私有 IPv4 局域网。

这是受邀团队使用的单机部署：不是高可用服务，也未提供企业 SSO、管理员网页、密码找回、自动过期删除或容灾复制。扩大公开用户范围前应补上这些运维能力。它已有应用登录、令牌撤销、登录限流、文件配额及按用户访问控制。

## 保存、断线与恢复

插件保存前会核对当前 Moodle 用户、课程、目标 URL、完整表单指纹和本地已确认的操作清单，并向服务端重新检查该条操作的授权。它先写入本机“可能提交”记录，再触发一次原生表单保存。

每次保存后重新读取表单，检查修改字段和未修改字段。HTTP 回复丢失时可重发已保存的核验记录；不会重放保存。保存结果不明时显示 `NEEDS_READBACK`，用户只能选择“仅核查上次保存结果”。服务重启使旧 Plan 失效；可能已执行的任务保留为待核查。Moodle 用户身份变化时不会接管其他账号，也不会回落到部署者浏览器。

标准部署脚本会把远程活动任务计入空闲检查。远程状态、数据库、上传文件及分析日志保存在 `course_agent_app/reports/remote_workspace`，不进入插件 ZIP 或 git。维护备份时需要同时保存 SQLite 数据及用户文件，使用可靠的 SQLite 备份方式，而非只复制运行中的主数据库文件。

## 验证

```powershell
cd course_agent_app
..\.venv\Scripts\python.exe -m unittest test_remote_workspace test_remote_browser test_remote_journal test_remote_panel test_remote_lan -q
..\.venv\Scripts\python.exe verify_remote_planner.py
```

自动测试使用虚构账号、页面及文件，包括两个独立 Chromium 插件环境。真实 Codex 测试也只使用虚构表单。它们验证协议、租户隔离和已适配表单路径，不构成所有学校 Moodle 版本或真实成绩写入成功的证明。上线后的真实 Moodle 修改仍需对应用户确认。
