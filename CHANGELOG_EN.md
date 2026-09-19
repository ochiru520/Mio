# Changelog

## Mio 0.3.0 — September 19, 2026

- Signed application updates with verified downloads, installation restart and failure recovery. Users on 0.2.1 or earlier must install this version manually once.
- Memory evidence, user corrections and revision restoration. Conflicting model extractions become candidates instead of replacing confirmed facts; private chat and record prompts include correction rules.
- Explicit supplier-receipt reconciliation for unknown generation results, confirmation before manual retry, and guards against late results after cancellation or privacy pause.
- Sanitized diagnostic export and timestamps for dependency model-load checks. Loading a model is distinct from completing inference.
- Improved web lookup triggers and failure context; split frontend, database and companion modules while preserving their interfaces.

Validation includes automated tests and isolated use of the actual Windows executables and installers on the development computer. Another clean Windows installation, live QQ/phone use and long-running cross-hardware behavior still require field validation. Update-manifest signing is separate from Windows code signing.

## Mio 0.2.1 — September 14, 2026

- Improved optional environment installation, offline Genie and voice-package import, installation logs, alternate download sources and model verification states.


## Mio 0.2 — September 12, 2026

Compared with **v0.1.0**, this release adds a dedicated workspace for model-driven tasks while preserving the everyday chat experience.

### Added

- Persistent multi-step Agent tasks: the model selects available tools, observes results, and continues. Tasks support pause, resume, cancellation, approvals, background-job waiting, and state recovery. Chat handoffs preserve relevant text and attachments.
- ComfyUI image, video, and matting tools. Import API JSON and supported canvas JSON, discover common inputs, choose default workflows, and check required nodes, models, and files.
- Workflow research: inspect graph structure, node definitions, permitted local documentation and plugin source, and public references when web access is enabled. Understanding notes include sources and version checks and are refreshed when stale.
- Native file/folder selection for ComfyUI, workflows, and authorized document folders; local workflow discovery, scoped file search, document reading, and versioned output files.
- Per-mode model policies, task execution timelines, and failure details.
- An independent diary model, missed-date checks, visible failures, and retries. Dates without supporting records are not filled with invented events.

### Improved

- Separate chat and Agent experiences; redundant standalone creation forms removed.
- Unified Agent settings grouped by models, task execution, file permissions, creation environment, workflows, and appearance.
- Compact task status above the composer, with details on demand.
- Full-image previews with dimmed, blurred surroundings, zoom/reset, download, Escape handling, and narrow-window support.
- Optional task budget limits, disabled by default, and clearer local-vision installation/activation states.

### Fixed

- Proactive messages, startup greetings, and nightly wrap-ups ignoring the selected model; bounded retries and duplicate/expiry checks added.
- Hidden diary-generation errors, missed dates, and races between automatic generation and manual edits.
- Local vision conflicting with an existing Ollama process, port, or model directory.
- Residual task execution after cancellation, conversation deletion, or privacy pause; associated generation cancellation, duplicate submissions, restart receipts, and long tool results.
- Model-policy priority, missing handoff attachments, orphan conversations, and handoff banners leaking across conversations.
- File boundaries, output validation, concurrent settings updates, maintenance/restore protection, CI coverage, and production-only image-blur CSS behavior.

- Fixed NapCat/QQ status detection for Windows short paths and alternate separators. Workflow regression tests now use independent samples instead of files from the developer's ComfyUI installation.

### Upgrade and limits

Download `Mio-0.2.0-Windows-x64-Setup.exe` for Windows or `Mio-source-0.2.0.zip` for source. Both have SHA-256 files. Create a full backup before upgrading. To keep your history, select **reuse existing data** in the installer and choose your previous data directory. A new data directory starts fresh. Database structures are upgraded as needed; use a pre-upgrade backup when returning to an older version.

Public downloads exclude personal records, secrets, private persona material, voice-training assets, and ComfyUI model weights. Optional services, plugins, and models require separate configuration. Agent behavior depends on the selected model and integrated tools; no general terminal or arbitrary project-code editing is provided. Workflow understanding is not proof of successful execution or visual quality. Remote requests may not be cancellable or refundable. The installer is unsigned, and cross-hardware and long-running use need more field feedback.
