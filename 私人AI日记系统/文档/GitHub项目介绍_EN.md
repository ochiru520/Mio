# Mio

[中文](GitHub项目介绍.md) · [Download for Windows](https://github.com/ochiru520/Mio/releases/latest) · [Report an issue](https://github.com/ochiru520/Mio/issues)

Mio is a personal AI companion and Agent for Windows. Conversations, long-term memory, diaries, voice, QQ and a Live2D companion share local data. Its Agent workspace handles file tasks and configured image/video workflows.

**Public version: 0.3.1 · Preview · Windows x64.** The installer includes the application runtime; users do not need Python or Node.js. Bring your own model API or configure optional local models.

## Get started

1. Download `Mio-0.3.1-Windows-x64-Setup.exe` from [Releases](https://github.com/ochiru520/Mio/releases/latest), check its SHA-256 file and run it.
2. Complete the environment and naming steps. Add a model provider now or later under Model & API settings.
3. Try a conversation, then configure memory, diaries, QQ, voice or the companion as needed. Check provider pricing before enabling cloud features.
4. Create a complete backup under Data & Privacy settings.

Windows 11 x64 is the primary tested environment. The desktop window requires [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/). Model weights, QQ/NapCat, custom character assets and reference audio are optional and are not included in the main installer.

## Features

| Area | Available capabilities |
|---|---|
| Chat | Multiple conversations/providers, Responses and Chat Completions, attachments, reasoning controls, token and cost records |
| Memory | Source evidence, manual edits and confirmation, conflict handling, deactivation and version restoration |
| Records | Daily diaries/reviews, weekly and monthly summaries, growth statistics and Markdown export; complete month/source listings |
| Agent | Task tracking, authorized file tools, configured generation services and ComfyUI workflows, recovery and external receipt reconciliation |
| Models | Inspect, install, verify and uninstall managed optional components with a removal preview; externally reused resources stay externally managed |
| Companion and voice | Live2D actions/expressions, Genie local voice, faster-whisper transcription and voice interaction |
| QQ and observation | NapCat/OneBot private and group chats, optional screen/window/system-audio observation |
| Data and updates | Local SQLite, complete backup/restore, migration checks, signed update manifests, download progress and explicit installation confirmation |

Installed, configured, connected and successfully tested are separate states. Check the application's actual diagnostics before relying on a feature.

## Changes in 0.3.1

- Optional model removal with protection for shared environments, personal data and custom resources.
- Genie/Whisper Unicode validation fixes on Windows and corrected Genie resource discovery.
- Task retry association, manual memory protection, sleeping-memory prompt exclusion, cache invalidation and sanitized diagnostics fixes.
- Revised Agent home, model menus, update panel, weekly excerpts and record empty states.
- Complete monthly listings and counts independent of the dashboard's recent-diary window.

0.3.0 was withdrawn. Use 0.3.1. Users on 0.2.1 or earlier must install 0.3.1 manually before using the in-app updater. See the [usage and upgrade guide](使用与升级指南.md) and [release notes](版本说明.md) (Chinese).

## Privacy and cost

Data stays in the selected local directory and is preserved during upgrades. Windows DPAPI protects stored model keys for the current Windows user; backups do not make those keys portable between users or machines.

Cloud model, search and cloud vision requests send necessary content to the configured service. Local-first does not mean fully offline. Update checks read the release service. QQ, observation, system audio and proactive behavior can be disabled individually or paused through privacy controls.

Public source excludes the author's chats, diaries, private persona, credentials, QQ sessions, training audio and private models. Displayed cost information depends on the provider and does not replace provider billing.

## Run from source

Use Windows x64, Python 3.10 (release builds use 3.10.11) and Node.js 22.12+. Inno Setup 6 is only needed to produce an installer.

Build the frontend first:

```powershell
cd .\澪Agent应用
npm ci
npm run build
```

Start the backend:

```powershell
cd ..\私人AI日记系统\backend
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/agent-app/`. Use `npm run dev` for frontend development and `构建Windows应用.ps1` for a Windows build. Keep the backend bound to localhost.

The Chinese backend/frontend directory names are retained for build and path compatibility. Private development builds and public releases use separate version channels and do not upgrade across channels based on version numbers.

## Validation and limitations

Windows CI runs backend, desktop/updater, frontend and Live2D checks. See [CONTRIBUTING](../CONTRIBUTING.md) for commands. Local installation and isolated upgrade tests do not establish compatibility with every GPU, audio device or provider. Clean-machine installs, real QQ/microphone use and long-running provider tasks still need broader field testing.

This is preview software. Installers do not have Windows code-signing certificates; use the official release and verify checksums.

## Documentation and license

[Usage and upgrades](使用与升级指南.md) · [Release notes](版本说明.md) · [Privacy](隐私说明.md) · [Security](../SECURITY.md) · [Third-party assets](资产与第三方许可.md)

Original code and documentation use the [MIT License](../LICENSE). Live2D, model weights, voices, images and imported assets retain their own licenses. Include only sanitized diagnostics in issues, never databases, credentials or private conversations.
