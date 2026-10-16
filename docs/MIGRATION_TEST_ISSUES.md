# Migration Test Issues

This document records issues found while testing the migrated Windows runtime. Fixes should be made in the source-computer project and verified in a newly generated migration bundle.

## MIG-001: Bootstrap Python version check fails in Windows PowerShell 5.1

- Status: Fixed and verified on destination
- Severity: High
- Environment: Windows PowerShell 5.1.26100.8655; Python 3.11.9
- Date found: 2026-07-27

### Reproduction

From the extracted project root, run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\migration\bootstrap_runtime.ps1
```

### Actual result

The bootstrap stops at its first Python version check before creating the virtual environment or installing dependencies:

```text
File "<string>", line 3
  raise SystemExit(Python
                  ^
SyntaxError: '(' was never closed
```

### Expected result

The script should detect the installed Python 3.11.9, print the selected version, and continue creating the runtime environment.

### Likely cause

`bootstrap_runtime.ps1` passes a double-quoted Python string inside a PowerShell here-string to a native `python -c` invocation. Windows PowerShell 5.1 does not preserve this nested quoting as intended, so Python receives truncated invalid source code.

### Suggested source-computer change

Avoid string literals in the native `-c` compatibility check. For example, use a compact exit-code-only check and obtain the display version separately:

```powershell
& $PythonCommand @PythonPrefix --version
& $PythonCommand @PythonPrefix -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.11 or newer is required."
}
```

Test the generated Windows bundle specifically under the built-in Windows PowerShell 5.1, not only PowerShell 7.

### Verification

Verified on 2026-07-27: the PowerShell bootstrap selected Python 3.11.9 and completed with `Preflight: PASS`.

## MIG-004: WhatsApp navigation race terminates the long-running agent

- Status: Fixed and regression-tested locally
- Severity: High
- Environment: Windows; authenticated WhatsApp Web session
- Date found: 2026-07-27

### Actual result

After the agent processed a staff prompt and returned to the long-running wait loop, the entire application exited:

```text
playwright._impl._errors.Error: Page.evaluate: Execution context was destroyed, most likely because of a navigation
```

The exception originated from `message_records(page)` inside `wait_for_staff_task_context`. No Agent Python process or local status service remained afterward.

### Expected result

A transient WhatsApp navigation/reload should be treated as lifecycle recovery: wait for the page to become ready, reopen the configured chat, rebuild the message baseline safely, and continue listening without consuming or duplicating staff prompts.

### Suggested source-computer change

- Catch Playwright execution-context/navigation errors around each polling read.
- Reuse the existing bounded WhatsApp readiness and exact-chat recovery flow.
- Preserve the current prompt boundary and consumed-message keys across recovery.
- Add a test that navigates or reloads WhatsApp between poll-position maintenance and `message_records`.

### Verification

Verified with a focused recovery test that injects the observed navigation exception, waits for WhatsApp readiness, restores the exact chat, and retries the message read without losing the baseline. The live Agent also entered the long-running staff-prompt wait state after restart.

## MIG-005: Planner invocation exceeds the Windows command-line limit

- Status: Fixed and regression-tested locally
- Severity: High
- Environment: Windows; full migrated planner task context
- Date found: 2026-07-27

### Actual result

For the staff prompt `报告moodle状态`, planning failed before the response/approval cycle completed:

```text
[WinError 206] The filename or extension is too long.
```

The generated planner task artifact is large, and the runtime passes planner input through a subprocess command invocation. Windows rejects the oversized command line.

### Expected result

Planner context size should not depend on the operating system command-line length limit.

### Suggested source-computer change

- Pass the planner prompt through standard input or a bounded task file instead of a command-line argument.
- Keep secrets out of command arguments, process listings, and diagnostic output.
- Add a Windows test using the full production planner context and verify both intent and manifest calls complete.

### Verification

Verified with a 20,000-line planner prompt: the command contains only the stdin marker and remains below 1,000 characters; the full prompt is supplied through standard input. A live Codex CLI stdin probe also exited successfully with the expected `STDIN_OK` result.

## MIG-006: Windows WhatsApp multiline output is not reliably preserved or verified

- Status: Fixed and verified on destination
- Severity: Medium
- Environment: Windows; WhatsApp Web
- Date found: 2026-07-27

### Observation

Received WhatsApp text retains newline characters in runtime logs, and message builders also join visible fields with `\n`. The Windows outbound path, however, inserts the entire string into WhatsApp's content-editable composer with DOM `execCommand('insertText')`. There is no Windows-specific multiline test and the normal send path returns success without verifying the displayed outgoing text or its line structure. During this run, the startup send returned without an exception but its outgoing message was not visible to the verifier.

### Expected result

Structured approval and result messages should appear with their intended line breaks, and successful delivery verification should compare normalized lines rather than only reporting that the send control was clicked.

### Suggested source-computer change

- Add a Windows WhatsApp test message containing blank lines and several labeled lines.
- Prefer a composition method known to preserve WhatsApp line breaks, such as inserting lines with explicit `Shift+Enter` operations where appropriate.
- Read back the outgoing message and verify the expected ordered lines before reporting send success.
- Keep the test confined to the configured WhatsApp test chat.

### Verification

Verified with real Chromium content-editable tests for normal and blank lines plus WhatsApp-specific DOM tests covering both doubled breaks and the observed five-newline serialization of one intentional blank line. Live acknowledgement and final status-report messages were composed, sent, and read back successfully on the first attempt for `RUN-091540-E77D`.

## MIG-002: Start launcher rejects the Chromium installed by bootstrap

- Status: Fixed and verified on destination
- Severity: High
- Environment: Windows PowerShell 5.1; fresh destination without separately installed Google Chrome
- Date found: 2026-07-27

### Reproduction

After installing the Python requirements and successfully running `python -m playwright install chromium`, run:

```powershell
.\migration\start_agent.ps1
```

### Actual result

Startup stops immediately:

```text
Google Chrome was not found in the standard Windows installation paths.
```

The bootstrap-installed browser is present under the Playwright browser cache. The ordinary preflight passes and explicitly reports that Playwright Chromium will be used, but `start_agent.ps1` independently requires a system Google Chrome executable and never reaches strict preflight or application startup.

### Expected result

The browser selected by bootstrap, preflight, and startup should be consistent. A fresh Windows destination should either:

1. start with the Chromium installed by `python -m playwright install chromium`; or
2. declare Google Chrome as an explicit prerequisite before bootstrap and avoid installing an unused Chromium copy.

### Likely cause

`bootstrap_runtime.ps1` installs Playwright Chromium and `preflight.py` supports that fallback, while `start_agent.ps1` searches only standard Google Chrome installation paths and throws when none is found.

### Suggested source-computer change

- Use one shared browser-resolution policy across bootstrap, preflight, and startup.
- Prefer an explicitly configured valid browser path, then system Chrome, then the Playwright-managed Chromium executable.
- Do not overwrite a valid configured browser path merely because system Chrome is absent.
- Add a clean-Windows test where Chrome is absent but bootstrap-installed Playwright Chromium is present.

### Verification

Verified on 2026-07-27: the formal `start_agent.ps1` launcher resolved the Playwright-managed Chromium, passed strict preflight, and started both Agent processes without system Chrome.

## MIG-003: Windows runtime is missing the `tzdata` dependency

- Status: Fixed and verified on destination
- Severity: High
- Environment: Windows; Python 3.11.9 virtual environment
- Date found: 2026-07-27

### Reproduction

After dependencies are installed from `course_agent_app/requirements.txt`, start `course_agent_app/run_course_agent_app.py`.

### Actual result

The process exits before the status server or browser starts:

```text
ModuleNotFoundError: No module named 'tzdata'
zoneinfo._common.ZoneInfoNotFoundError: 'No time zone found with key Asia/Hong_Kong'
```

### Expected result

The application should resolve `ZoneInfo("Asia/Hong_Kong")` and continue startup on a clean Windows installation.

### Likely cause

Linux and macOS commonly provide an operating-system IANA time-zone database. A clean Windows Python environment does not, so Python's `zoneinfo` falls back to the PyPI `tzdata` package. That package is not installed by the migrated requirements.

### Suggested source-computer change

Add a Windows-specific runtime dependency to the canonical requirements, for example:

```text
tzdata; sys_platform == "win32"
```

Add a Windows migration smoke test that imports the regular app entry point or explicitly resolves `ZoneInfo("Asia/Hong_Kong")` inside the newly created virtual environment.

### Verification

Verified on 2026-07-27: bootstrap installed the Windows-specific pinned `tzdata` dependency and the application resolved `Asia/Hong_Kong` during successful startup.

## MIG-007: Windows launcher selects an inaccessible Codex binary and the bundle has no Git metadata

- Status: Fixed and verified on destination
- Severity: High
- Environment: Windows Codex desktop installation; extracted migration bundle without `.git`
- Date found: 2026-07-27

### Actual result

The launcher selected the Codex executable exposed inside `C:\Program Files\WindowsApps`. A normal planner subprocess could discover that path but could not execute it:

```text
PermissionError: [WinError 5] Access is denied
```

After selecting the runnable per-user Codex CLI, `codex exec` then correctly reported that the extracted runtime was not inside a trusted Git directory. Migration bundles intentionally exclude `.git` metadata.

### Fix

- The Windows launcher now probes candidate executables with `codex --version` and accepts only a runnable candidate.
- It searches the per-user Codex CLI directory before the protected WindowsApps command result.
- Codex command construction adds `--skip-git-repo-check` only when the selected working directory is not inside a Git worktree.

### Verification

The resolved per-user CLI reported its version successfully. A real non-Git-bundle stdin execution exited with code 0 and returned the expected `STDIN_OK` result.

## MIG-008: Windows default subprocess encoding corrupts Codex planner stdin

- Status: Fixed and verified on destination
- Severity: High
- Environment: Windows PowerShell 5.1; Python 3.11.9; planner prompt containing non-ASCII text
- Date found: 2026-07-27

### Actual result

After moving the large planner prompt from the command line to standard input, the Codex CLI rejected the production prompt:

```text
Failed to read prompt from stdin: input is not valid UTF-8 (invalid byte at offset 600)
```

### Likely cause

`subprocess.run(..., text=True)` used the Windows process default encoding when converting the Unicode planner prompt to stdin bytes. Codex CLI requires UTF-8 input.

### Fix

The planner subprocess now sets `encoding="utf-8"` explicitly and uses replacement decoding only for diagnostic stdout/stderr, while still passing the complete prompt through stdin.

### Verification

A real Codex stdin probe containing repeated Chinese and Unicode text exited with code 0 and produced the expected UTF-8 result. The recovered production request then completed planning, recorded a read-only intent, and delivered the Moodle status report to WhatsApp as `RUN-091540-E77D`.

## MIG-009: Windows console encoding crashes the long-running Agent on Unicode prompt text

- Status: Fixed and verified on destination
- Severity: High
- Environment: Windows PowerShell 5.1; Python 3.11.9; redirected Agent stdout
- Date found: 2026-07-27

### Actual result

After returning to the long-running prompt wait loop, the Agent received a staff message containing the Unicode bullet character `•`. Logging the raw prompt terminated the entire process:

```text
UnicodeEncodeError: 'gbk' codec can't encode character '\u2022'
```

The failure occurred in the ordinary `print(f"Received staff prompt ...")` path. No WhatsApp stop command or intentional shutdown event was recorded.

### Expected result

Any valid Unicode WhatsApp prompt should be loggable without terminating the Agent, regardless of the Windows console code page or output redirection.

### Fix

- Reconfigure `sys.stdout` and `sys.stderr` to UTF-8 with a non-fatal diagnostic error policy at application startup.
- Set UTF-8 explicitly for launcher-managed redirected logs where applicable.
- Add a Windows long-running intake test containing Chinese text, emoji, en/em dashes, and bullet characters.
- Keep user-visible text unchanged; only diagnostic stream encoding should use replacement fallback.

### Verification

The application-level UTF-8 stream regression covers Chinese text and the Unicode bullet. A formal PowerShell 5.1 launch then wrote the non-ASCII workspace path `OneDrive\文档` correctly to redirected logs, completed Moodle and WhatsApp startup, and remained in `agent_waiting_for_staff_prompt` as `RUN-095436-B44D` with an empty stderr log.

## MIG-010: Windows capability failures were detected only after a real staff task started

- Status: Fixed and verified on destination
- Severity: High
- Environment: Windows PowerShell 5.1; Python 3.11.9; Microsoft 365 desktop apps
- Date found: 2026-07-27

### Actual result

Windows support was fragmented across individual fixes. Several specialist Codex planners still placed their full prompt on the command line, Office review generation required LibreOffice even when Microsoft Office was installed, PowerShell redirected UTF-8 child output through the active legacy code page, and a second launcher could start against the same profiles. These failures would appear only when a matching staff task exercised that path.

The Office renderer also retained a same-name PDF from a previous attempt, so a failed conversion could leave ambiguous evidence. PowerPoint `ExportAsFixedFormat` additionally failed through late-bound COM because its optional print-range parameter expected a COM object.

### Fix

- All course, date, schedule, composition, timeline, WhatsApp-listener, and Moodle-listener Codex prompts now default to UTF-8 stdin. Non-Git migration bundles add `--skip-git-repo-check` consistently.
- Windows installs pinned `pywin32==312` and uses isolated Word, Excel, or PowerPoint COM workers when LibreOffice is absent. Macros are disabled, files open read-only, conversion uses a short temporary staging directory, and the worker has a bounded timeout.
- PowerPoint uses the reliable PDF `SaveAs` format. Every conversion removes its own expected output before starting, so stale PDFs cannot prove success.
- Strict preflight now verifies a runnable Codex binary, Chrome or Playwright Chromium, Windows COM modules, and complete Office PDF support before accepting the runtime.
- PowerShell 5.1 bootstrap/start/build scripts set UTF-8 input/output explicitly. The launcher adds localhost to `NO_PROXY`/`no_proxy` and holds a project-specific named mutex to block duplicate Agent instances.
- Windows attachment names preserve their extension, avoid reserved device names, include a collision-resistant digest when shortened, and use a smaller bound for long-path compatibility.
- Poppler is no longer reported as a missing runtime dependency because the supported PDF paths use the pinned Python PDF libraries.

### Verification

- Strict preflight: PASS, with Microsoft Office COM reported as the active DOCX/XLSX/PPTX renderer.
- Windows regression suite: 12 tests passed.
- Real Office rendering: the migrated DOCX, a generated XLSX, and a generated PPTX each produced a non-empty, one-page PDF through Microsoft Office COM.
- Duplicate startup: a second `start_agent.ps1` exited with code 1 before opening another Agent or browser.
- Rebuilt Windows ZIP: 96 entries; verified inclusion of the Office worker, requirements, bootstrap, and launcher.
- Formal runtime: Moodle and WhatsApp reached `agent_waiting_for_staff_prompt`; redirected stderr remained empty.

## MIG-011: Bundled Windows bootstrap could not recover a missing Python or broken virtual environment

- Status: Fixed and verified by parser, dependency, and preflight tests
- Severity: High
- Environment: Windows PowerShell 5.1 migration bundle
- Date found: 2026-07-27

### Actual result

The private Windows bundle could contain `python-3.11.9-amd64.exe`, but bootstrap still stopped with a manual-install error when no suitable Python was discoverable. It also trusted any existing `.venv\Scripts\python.exe` path without checking whether the copied environment could actually start on the destination machine.

### Fix

Bootstrap now prefers Python 3.11 explicitly, accepts any working Python 3.11+, and otherwise silently installs the bundled Python 3.11 runtime for the current user. An unusable virtual environment is moved to a timestamped `.venv.broken-*` backup and rebuilt; it is not deleted.

The Windows migration guide now contains only PowerShell commands, documents automatic Python recovery, and uses Windows verification/checksum examples.

### Verification

All three PowerShell scripts parse successfully under Windows PowerShell 5.1. The existing Python 3.11.9 environment passed the new health check, dependency installation, strict preflight, and complete regression suite. The bundled-installer branch is deterministic but was not executed because this destination already has a healthy Python 3.11.9 installation.

## MIG-012: WhatsApp Web automatic list formatting blocked a valid result send

- Status: Fixed and verified on destination
- Severity: High
- Environment: Windows PowerShell 5.1; WhatsApp Web contenteditable composer
- Date found: 2026-07-27

### Actual result

A read-only Moodle result containing lines beginning with `1.`, `2.`, and so on was converted by WhatsApp Web into an editable ordered list. DOM readback then contained duplicated markers such as `2. 2.` and numbered the trailing Course/Run/Link lines. The pre-send integrity check correctly refused to click Send, but the orchestration path incorrectly recorded the result as posted even though both delivery attempts failed.

### Fix

- Convert leading plain numeric-list markers to stable `Item N:` labels before composing text or attachment captions.
- Permit compose verification to ignore WhatsApp-generated list and newline serialization while still requiring every substantive line in the same order.
- Record `approval_report_posted` only after the send function returns success; record `approval_report_send_failed` otherwise.

### Verification

The Windows regression suite now has 15 passing tests, including safe numeric-list preparation, semantic comparison of WhatsApp-generated list markers, and failed-send ledger accuracy. Python compilation checks also pass for the WhatsApp driver, Agent application, and approval server.
