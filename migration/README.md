# Course Agent Runtime Migration

This directory builds and starts a minimal runtime copy of the long-running
WhatsApp-to-Moodle course agent.

## Build on the source computer

```bash
python3 migration/build_runtime_bundle.py
```

The builder creates:

```text
migration_output/course-agent-runtime-<UTC timestamp>.tar.gz
migration_output/course-agent-runtime-<UTC timestamp>.tar.gz.sha256
```

For a destination repository whose private visibility and authenticated access
can be confirmed, build a dedicated bundle that already contains the current
local `.env`:

```bash
python3 migration/build_runtime_bundle.py \
  --include-env \
  --private-github-repo Richard598/Course_Agent
```

The copied `.env` is stored with mode `0600`. The destination bootstrap keeps
an existing `.env`, so this bundle does not require editing it before startup.

Build the private Windows variant with:

```bash
python3 migration/build_runtime_bundle.py \
  --target windows \
  --include-env \
  --private-github-repo Richard598/Course_Agent
```

The Windows target is emitted as a `.zip`; the macOS target is emitted as a
`.tar.gz`.

From an already-created private Windows runtime copy, rebuild a verified ZIP
with PowerShell 5.1 or newer:

```powershell
.\migration\build_windows_bundle.ps1
```

This preserves the manifest-approved runtime closure, refreshes every file
hash, includes a bundled Python installer when present, and still excludes
browser profiles/cookies, logs, caches, approval state, and historical output.

The archive is assembled from a static local-import closure rooted at:

- `course_agent_app/run_course_agent_app.py`
- `course_agent_app/approval_server.py`
- `course_automation_probe/run_dase7137_stale_cleanup_rebuild.py`

It also includes schemas, operation templates, the current DASE7137 reference
pack, and only the source files named by the current reference manifest.

The regular archive does not include `.env`. The confirmed-private-repository
variant includes `.env`. Neither variant includes browser profiles/cookies,
approval state, reports, caches, historical plans, test fixtures, Moodle backup
payloads, or virtual environments.

## Install on the destination computer

```bash
tar -xzf course-agent-runtime-<UTC timestamp>.tar.gz
cd course-agent-runtime-<UTC timestamp>
./migration/bootstrap_runtime.sh
```

For a regular bundle, edit `course_agent_app/.env`. At minimum, check:

- `COURSE_AGENT_WHATSAPP_CHAT_NAME`
- `CODEX_MODEL`
- `MOODLE_API_URL`
- `MOODLE_COURSE_URL`
- `MOODLE_COURSE_ID`
- `DASE7137_KEY`
- Moodle login configuration or the matching macOS Keychain entries

The private-repository bundle already contains the source computer's `.env` and
does not need this edit.

## Windows destination

Open PowerShell in the extracted bundle and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\migration\bootstrap_runtime.ps1
.\migration\start_agent.ps1
```

The PowerShell launcher detects Python, Codex, and Chrome from PATH and standard
Windows installation paths. It replaces the source computer's macOS browser
paths with Windows paths while retaining the remaining `.env` configuration.
If Python 3.11+ is missing and the private runtime contains
`python-3.11.9-amd64.exe`, the bootstrap installs that bundled runtime for the
current user. An unusable existing `.venv` is preserved with a timestamped
`.broken-*` suffix before being rebuilt.

The Windows launcher validates a runnable Codex CLI, uses Chrome or the
Playwright-managed Chromium fallback, enables UTF-8 diagnostics, prevents
duplicate Agent instances, and keeps local approval-server traffic outside
system/VPN proxies. Microsoft Office is used as the Windows PDF-rendering
fallback when LibreOffice is not installed.

On the first destination launch, authenticate WhatsApp Web by QR code. Moodle
can restore a session through configured local credentials/Keychain and then
reuses the bundle-local persistent profile.

## Verify

```powershell
.\course_agent_app\.venv\Scripts\python.exe .\migration\preflight.py --strict
Invoke-RestMethod http://localhost:8787/status | ConvertTo-Json -Depth 10
```

The archive checksum can be verified with:

```powershell
Get-FileHash -Algorithm SHA256 .\course-agent-runtime-windows-<UTC timestamp>.zip
```
