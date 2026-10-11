<p align="center">
  <img src="docs/image/mio-logo.png" alt="Mio" width="160">
</p>

<h1 align="center">Mio · 澪</h1>

<p align="center">
  A personal AI companion and Agent for Windows
</p>

<p align="center">
  <a href="README.md">中文</a> · <strong>English</strong> ·
  <a href="https://github.com/ochiru520/Mio/releases/tag/v0.4.0">Download 0.4.0</a> ·
  <a href="https://github.com/ochiru520/Mio/issues">Report an issue</a>
</p>

<p align="center"><strong>0.4.0 · Windows x64 · MIT</strong></p>

Mio brings conversations, long-term memory, diaries, voice and a Live2D desktop companion together with an Agent workspace for documents, files and code. Chat, reflect on daily life, or authorize Mio to help with work, learning and creative tasks. Chat and Agent share local data, model providers, themes and persona settings.

## ✨ At a glance

- **Conversation and companionship**: multiple conversations and model providers, attachments, reasoning controls, and optional Live2D and voice interaction.
- **Memory and diaries**: inspect sources, edit and confirm memories, handle conflicts and restore earlier versions; organize daily reviews, weekly and monthly entries, and export Markdown.
- **Work, Learning and Image Generation**: start tasks in natural language, follow progress, add requirements, stop or recover; creative tools connect to your configured generation services or ComfyUI workflows.
- **Projects and results**: bind project folders, archive conversations, sources, tasks and results with provenance and versions, and preview images and Office content in the sidebar.
- **Code and computer tools**: read and edit authorized code, inspect diffs, run checks and terminal commands, and observe or control authorized Windows application windows.
- **Capabilities and local changes**: versioned skills and restricted data-processing tools and panels; with matching source and development dependencies, build, preview and confirm local changes to Mio.
- **Local data and optional integrations**: SQLite, complete backup/restore and component management, with configurable WeChat, QQ, screen and system-audio observation.

## 🚀 Get started

### Install 0.4.0

Download `Mio-0.4.0-Windows-x64-Setup.exe` and its SHA-256 file from the [0.4.0 release page](https://github.com/ochiru520/Mio/releases/tag/v0.4.0). Verify the checksum, then install. The main application includes its runtime; users do not need to install Python or Node.js separately.

Windows 11 x64 is the primary validation environment. The desktop window requires [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/). Installers do not have Windows code-signing certificates; download from the release page above and verify checksums.

### First use

1. Complete the environment and naming steps in the first-run wizard. Add a model provider now, or later under Settings > Model & API (模型与 API), using your API key, service URL and model name.
2. Try a conversation, then open the Agent's Work, Learning or Image Generation entry point. Bind an existing folder or create one under Projects.
3. Configure file, terminal and window access under Agent Settings > Execution Permissions (执行权限), choosing a permission mode for your task.
4. Manage optional components under chat Settings > Environment & Models (环境与模型), and create a complete backup under Data & Privacy (数据与隐私). Cloud services require your own API access; check provider pricing before use.

Model weights, Genie runtime data, the Mio voice package, QQ/NapCat, custom character assets and reference audio are not included in the main installer.

## 🖼️ Features and interface

These screenshots use an isolated example environment and a custom avatar. You can change the avatar, persona and theme in settings.

### Agent: tasks and results

<p align="center">
  <img src="docs/image/agent-workspace.png" alt="Mio 0.4.0 Agent workspace" width="1000">
</p>

Work, Learning and Image Generation connect to project folders. Keep conversations, sources, tasks and results together, and view generated files and images in the sidebar. Add requirements, stop or recover while a task is running.

Version 0.4.0 saves persistent summaries for long tasks, retains full-history retrieval and shows context estimates. After a disconnection, it reconciles the original request; unknown write results are not automatically repeated. Summaries can still omit details.

Office previews show supported text, tables and images. They do not fully reproduce layouts, charts or animations, or recalculate Excel formulas. Oversized, damaged or unsupported files receive an explanation.

### Conversation, memory and daily records

Chat supports Responses / Chat Completions, multiple providers, attachments, and token and cost records. Inspect memory sources, edit and confirm entries, handle conflicts, deactivate memories or restore earlier versions. Diaries include daily reviews, weekly and monthly entries, growth statistics and Markdown export; monthly entries list the full month and source diaries.

The optional Live2D companion runs in a separate window with actions and expressions. Voice uses Genie local voices, faster-whisper transcription and voice interaction. WeChat and QQ need their respective integrations configured; QQ uses NapCat / OneBot. Proactive contact, automatic recording, internet access and observation are configured separately.

### Settings: models, environment and permissions

<p align="center">
  <img src="docs/image/settings.png" alt="Mio 0.4.0 settings" width="1000">
</p>

Model providers, themes and persona settings are shared between chat and Agent; their specific settings remain separate. The environment center checks, installs, verifies and uninstalls managed optional components, showing the removal scope first. Externally reused resources remain managed at their original location. Updates support signed manifests, migration checks and confirmed installation.

Installed, configured, connected and successfully tested are different states. Check the application's actual diagnostics before relying on a feature.

### Extend capabilities and modify Mio

Skills and restricted extensions support validation, versioning, activation and rollback. Modifying Mio requires matching source and development dependencies. Candidates are checked, built and previewed in a separate copy, and require your confirmation by default before application. Installation failure or interruption has rollback handling; returning to an earlier version still depends on version and data compatibility.

## 🔐 Permissions, privacy and cost

View Only, Help Me Do It and Autonomous Execution obey existing authorization; autonomy grants no additional permissions. The local terminal has no OS sandbox. Optional isolated execution requires Docker and refuses to run when unavailable. Windows automation depends on application interfaces, focus, authorization and component setup; it cannot guarantee control of every application.

Data stays in your selected local directory, which is preserved during upgrades. Windows DPAPI protects stored model keys for the current Windows user. Complete backups do not make those keys automatically portable to another machine.

Cloud model, search and cloud vision requests send the necessary content to your chosen service. Local-first does not mean fully offline. Screen, system audio, QQ and proactive behavior can be disabled individually or paused through privacy controls. Update checks read the release service. Cost information depends on what providers supply and does not replace provider billing.

Public source excludes the author's chats, diaries, private persona, API keys, QQ sessions, training audio and private models.

## 📦 Optional components and compatibility

**Genie runtime data and the Mio voice are separate optional downloads**, now available as [Genie runtime](https://github.com/ochiru520/Mio/releases/download/v0.4.0/Mio-Genie-Runtime-v1.zip) and [Mio voice](https://github.com/ochiru520/Mio/releases/download/v0.4.0/Mio-Native-Voice-mio-v1.zip) in the 0.4.0 release, each with a SHA-256 file. For mainland China, use [Baidu Netdisk](https://pan.baidu.com/s/1mbT21ul6g1MQEY346F_7HA?pwd=hm75) (code: `hm75`). Select each file separately; there is no combined bundle. In Settings → Environment and Models, install the Genie ZIP first, then the Mio voice ZIP. Do not unpack the data ZIPs yourself.

The original 0.4.0 installer and source ZIP are unchanged; their built-in download lists may still be empty. Select the ZIP offline or save the actual GitHub HTTPS package URL in acquisition settings. GitHub filenames are English, while Netdisk retains Chinese names; their bytes and hashes match. Initial Genie setup still downloads Python and runtime dependencies. Netdisk may require login or a client and is not an automatic update URL. Voice/model/reference assets follow their own terms; the source MIT license does not automatically cover them.

Related features have automated checks, isolated samples and UI validation, but compatibility still depends on your GPU, audio devices, model providers and local environment. QQ, WeChat and creative/image/video generation require testing in your own environment. Voice and the companion have not undergone renewed real-device validation for this version. A complete first installation on clean Windows has not yet been verified.

## 🧱 Technology and project structure

| Area | Technology |
|---|---|
| Interface | Vue 3, Vite, JavaScript |
| Backend and data | Python 3.10, FastAPI, SQLite |
| Windows desktop | Windows launcher, WebView2, separate Live2D window |
| Optional voice and creation | Genie, faster-whisper, generation services / ComfyUI workflows |

```text
Mio/
├─ 私人AI日记系统/   Backend, memory, records and services
├─ 澪Agent应用/      Interface, Windows launcher, updater and Live2D
├─ docs/image/       Project icon and application screenshots
├─ README.md
└─ README_EN.md
```

Chinese directory names are retained for build and path compatibility. Public releases and private development builds use separate channels; version numbers do not trigger upgrades across channels.

## 🛠️ Run from source

Use Windows x64, Python 3.10 (release builds use 3.10.11) and Node.js 22.12+. Source ZIPs do not require Git. Inno Setup 6 is also needed to produce an installer.

Build the frontend:

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

Open `http://127.0.0.1:8000/agent-app/`. Use `npm run dev` in the frontend directory for development and `构建Windows应用.ps1` for a Windows build. Keep the backend bound to localhost; do not expose it directly to the internet. See [CONTRIBUTING](CONTRIBUTING.md) for Windows CI and check commands.

## 📖 Help and license

- [Usage and upgrades](私人AI日记系统/文档/使用与升级指南.md) (Chinese) · [0.4.0 changelog](CHANGELOG_EN.md)
- [Privacy](私人AI日记系统/文档/隐私说明.md) (Chinese) · [Security](SECURITY.md)
- [Third-party assets](私人AI日记系统/文档/资产与第三方许可.md) (Chinese) · [Contributing](CONTRIBUTING.md)
- [Report an issue](https://github.com/ochiru520/Mio/issues): include sanitized diagnostics and reproduction steps, never databases, credentials or private conversations.

Original code and documentation use the [MIT License](LICENSE). Live2D, model weights, voices, character images and imported assets retain their own licenses.
