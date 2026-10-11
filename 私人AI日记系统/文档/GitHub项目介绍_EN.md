# Mio

[中文](GitHub项目介绍.md) · [0.4.0 preview — pending release](https://github.com/ochiru520/Mio/releases/tag/v0.4.0) · [Current stable 0.3.1](https://github.com/ochiru520/Mio/releases/tag/v0.3.1) · [Report an issue](https://github.com/ochiru520/Mio/issues)

Mio is a personal AI companion and Agent for Windows. Conversations, memory, diaries, voice and the Live2D companion share local data. The Agent's Work, Learning and Image Generation entry points handle ongoing document, file, code and configured creative tasks.

**Version prepared here: 0.4.0 · Preview · Windows x64 · Pending release.** Online Latest remains 0.3.1. Version 0.4.0 has not been uploaded; its link is the intended release page, not an available download. The main application includes its runtime; users do not need to install Python or Node.js themselves. Bring your own model API; configure optional capabilities as needed.

## Get started

1. You can install [stable 0.3.1](https://github.com/ochiru520/Mio/releases/tag/v0.3.1) now. Once 0.4.0 is published, download `Mio-0.4.0-Windows-x64-Setup.exe` and its SHA-256 file from that version's page, verify and install it.
2. Complete the environment and naming steps. Add a model provider now or later under Model & API settings.
3. Try a conversation, then use the Agent's Work, Learning or Image Generation entry point. Bind an existing folder or create one under Projects; configure file, terminal and window access under Agent Settings > Execution Permissions (执行权限). Check provider pricing before cloud use.
4. Find components under chat Settings > Environment & Models (环境与模型), and backups under Data & Privacy (数据与隐私). Model providers, themes and persona settings are shared between chat and Agent; their specific settings remain separate.

Windows 11 x64 is the primary validation environment. The desktop window requires [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/). Model weights, Genie runtime data, the Mio voice package, QQ/NapCat, custom character assets and reference audio are not included in the main installer.

## Features

| Area | Available capabilities |
|---|---|
| Chat | Multiple conversations/providers, Responses and Chat Completions, attachments, reasoning controls, token and cost records |
| Memory | Source evidence, manual edits and confirmation, conflict handling, deactivation and version restoration |
| Records | Daily diaries/reviews, weekly and monthly summaries, growth statistics and Markdown export; complete month/source listings |
| Agent Work / Learning / Image Generation | Natural-language tasks, progress, additional requests, stop/recovery, study materials and exercises; generation needs configured services or ComfyUI workflows |
| Projects and results | Folders, conversations, sources, tasks and archived results with provenance and versions; sidebar content previews for images and Office results |
| Code, terminal and Windows control | Authorized code reading/editing, diffs, checks and commands; observation and control of authorized windows, with required dependencies |
| Capabilities and Modify Mio | Versioned skills, restricted data-processing tools and panels; validation, activation and rollback; local modification builds, previews and application with matching source and development dependencies |
| Models | Inspect, install, verify and uninstall managed optional components with a removal preview; externally reused resources stay externally managed |
| Companion and voice | Live2D actions/expressions, Genie local voice, faster-whisper transcription and voice interaction |
| WeChat, QQ and observation | Configured messaging integrations; QQ uses NapCat/OneBot; optional screen/window/system-audio observation |
| Data and updates | Local SQLite, backup/restore, migration checks, signed update manifests and confirmed installation; online 0.4.0 updates await publication |

Installed, configured, connected and successfully tested are separate states. Check the application's actual diagnostics before relying on a feature.

View Only, Help Me Do It and Autonomous Execution obey existing authorization; autonomy grants no additional permissions. The local terminal has no OS sandbox. Optional isolation requires Docker and refuses execution when unavailable. Windows automation depends on application interfaces, focus and environment, and cannot guarantee control of every application.

Office previews show supported text, tables and images, not complete layouts, charts or animations, and do not recalculate Excel formulas. Oversized, damaged and unsupported files receive an explanation.

## Main changes in 0.4.0 (preview, pending release)

- Work, Learning and Image Generation connect with project folders and archived results; files and images open in the sidebar.
- Code, terminal and authorized window tools; persistent permission modes, global defaults and project access.
- Persistent task summaries retain full-history retrieval; context estimates and original-request reconciliation after disconnection. Unknown write results are not automatically repeated. Summaries may omit details.
- Validated, versioned skills and restricted extensions with rollback. Changes to Mio are checked, built and previewed in a separate development copy; local application requires confirmation by default, with installation failure/interruption rollback. Returning to an older version depends on version and data compatibility.
- Shared models, themes and persona; simpler presets/input controls; clearer execution results, public analysis, image previews, contrast and narrow-window layouts.
- FFmpeg installation/checks, the missing Whisper system-audio dependency, download verification, source-specific resume data and installer process recovery fixes.

See [CHANGELOG](../../CHANGELOG_EN.md), [release notes](版本说明.md) and the [usage and upgrade guide](使用与升级指南.md) (Chinese). Version 0.3.0 was withdrawn. Users on 0.2.1 or earlier can first install current stable 0.3.1 manually. The new version will not appear in online updates before publication.

## Obtaining Genie and the Mio voice package

These are separate optional data packages, not bundled with the main application. Environment settings provide acquisition/installation guidance, real HTTPS source configuration and offline package selection, with verification before installation. **An acquisition entry point or source setting does not mean an official default download URL exists.** Online acquisition needs a real matching source; offline installation requires obtaining the package first. Actual published attachments and applicable licenses determine availability and redistribution rights. A local package is not proof of redistribution permission.

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

Related features have automated checks, isolated samples and UI validation; the repository provides Windows CI checks. See [CONTRIBUTING](../../CONTRIBUTING.md) for commands. This does not establish success in every real scenario or compatibility with every GPU, audio device or provider.

QQ, WeChat and creative/image/video generation remain user-tested. Voice and the companion were exempt this round and have not undergone renewed real-device validation. A full first installation on clean Windows without development-machine caches/tools has not been completed.

This is preview software. Installers do not have Windows code-signing certificates; use the official release and verify checksums.

## Documentation and license

[Usage and upgrades](使用与升级指南.md) · [Release notes](版本说明.md) · [Privacy](隐私说明.md) · [Security](../../SECURITY.md) · [Third-party assets](资产与第三方许可.md)

Original code and documentation use the [MIT License](../../LICENSE). Live2D, model weights, voices, images and imported assets retain their own licenses. Include only sanitized diagnostics in issues, never databases, credentials or private conversations.
