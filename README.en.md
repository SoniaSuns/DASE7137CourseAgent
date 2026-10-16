# DASE7137 Course Agent

[中文](README.md) | [English](README.en.md)

**[Download the Chrome extension ZIP](00_Download/Moodle-Workspace-Chrome.zip?raw=true)** · [Detailed setup guide (Chinese)](docs/REMOTE_WORKSPACE.md)

The agent already runs on the administrator's computer. Users connect to that service; they do not need Python or Codex installed.

## Use through WhatsApp

1. In the connected `DASE7137 - 2026` or `test approval` group, select the agent account using a real WhatsApp @ mention, then describe your request.
2. Example: `@agent Check whether Course A's file order matches its syllabus. Report the confirmed differences first.`
3. For Moodle changes, review the Plan and reply exactly `OK <code>` to confirm or `NO <code>` to cancel, using the code in the message. Replies to the current question or approval do not need another mention.

WhatsApp uses the administrator's configured courses and browser. To operate your own Moodle account, use the Chrome extension below.

## Chrome extension and the existing agent

1. Download and unzip the extension above. Open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**, and select the extracted folder.
2. Open the **Moodle Workspace — My Browser** side panel. Join the same local network as the administrator's computer and enter **`http://192.168.0.124:18791`** as the service address.
3. Click **连接 agent** (Connect agent) to open this browser's workspace.
4. Sign in to Moodle in your own Chrome, open the course homepage, and click **连接当前 Moodle 课程** (Connect current Moodle course). Select any local files you want to upload, then enter your request.
5. Review the Plan and click **确认执行** (Confirm execution). Keep Chrome, the side panel, and the course tab open while it runs.

Each browser has its own tasks and files. Moodle reads and changes run in that user's browser. Reopening the extension reconnects to the same workspace; clearing extension data or using another browser creates a new workspace. On the administrator's computer, `http://127.0.0.1:18791` also works. Local HTTP is unencrypted; use a trusted network. Internet access requires separate HTTPS configuration.

The remote extension supports reading and analysing materials and editing ordinary Moodle forms. Activity movement, group management, and Moodle resource uploads are not yet supported remotely.

[Deployment and networking (Chinese)](docs/REMOTE_WORKSPACE.md) · [Technical project details](docs/PROJECT_DETAILS.md)

This repository includes code, configuration, tests, and the extension ZIP. It excludes credentials, browser login data, uploaded user materials, and runtime logs.
