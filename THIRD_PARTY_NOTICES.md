# 第三方组件与致谢

Mio 使用或支持接入下列开源项目。感谢这些项目的作者与贡献者。

本页用于提供清晰的项目入口和许可边界，不替代各依赖自身的 `LICENSE`、模型许可证或服务条款。实际分发版本、传递依赖和精确版本以 `requirements.txt`、`package-lock.json`、安装包内许可文件及对应上游发布物为准。

## 核心应用

| 项目 | Mio 中的用途 | 上游与许可 |
|---|---|---|
| FastAPI | 本地 HTTP / WebSocket 后端 | [fastapi/fastapi](https://github.com/fastapi/fastapi)，MIT |
| Vue | 主应用界面 | [vuejs/core](https://github.com/vuejs/core)，MIT |
| Vite | 前端构建 | [vitejs/vite](https://github.com/vitejs/vite)，MIT |
| Electron | 独立 Live2D 桌宠进程 | [electron/electron](https://github.com/electron/electron)，MIT |
| pywebview | Windows 主窗口与 WebView 桥接 | [r0x0r/pywebview](https://github.com/r0x0r/pywebview)，BSD 3-Clause |
| PyInstaller | Windows 可执行文件构建 | [pyinstaller/pyinstaller](https://github.com/pyinstaller/pyinstaller)，GPL 及 Bootloader Exception |

## 语音、视觉与外部能力

| 项目 | Mio 中的用途 | 上游与许可边界 |
|---|---|---|
| faster-whisper / CTranslate2 | 本地电话与系统声音转写 | [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) / [OpenNMT/CTranslate2](https://github.com/OpenNMT/CTranslate2)，遵循各自上游许可证 |
| Genie-TTS | GPT-SoVITS 权重的本地 ONNX 推理适配 | [High-Logic/Genie-TTS](https://github.com/High-Logic/Genie-TTS)，引擎与模型分别遵循上游许可证 |
| GPT-SoVITS | 可选角色音色与用户权重来源 | [RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)，代码、模型和训练数据分别遵循上游及数据授权 |
| Ollama | 可选本地模型运行器 | [ollama/ollama](https://github.com/ollama/ollama)，MIT；具体模型另有各自许可证 |
| NapCatQQ | 可选 QQ / OneBot 接入 | [NapNeko/NapCatQQ](https://github.com/NapNeko/NapCatQQ)，独立安装并遵循上游许可证与平台规则 |

Mio 的公开仓库和默认安装包不包含用户的模型权重、角色音色、训练参考音频、QQ 登录态或 API Key。

## Live2D 与渲染

| 项目或资产 | Mio 中的用途 | 上游与许可 |
|---|---|---|
| PixiJS | 2D 渲染 | [pixijs/pixijs](https://github.com/pixijs/pixijs)，MIT |
| pixi-live2d-display | Live2D 模型渲染桥接 | [guansss/pixi-live2d-display](https://github.com/guansss/pixi-live2d-display)，MIT |
| Live2DPet | 桌宠交互实现参考 | [x380kkm/Live2DPet](https://github.com/x380kkm/Live2DPet)，MIT |
| Live2D Cubism Core | Live2D 运行库 | Live2D Proprietary Software License |
| Hiyori Momose | 默认示例模型 | Live2D Free Material License 与 Sample Model Terms；不是 Mio 原创角色 |

Live2D 许可原文和声明位于 [`澪Agent应用/public/live2d-pet/licenses/`](澪Agent应用/public/live2d-pet/licenses/)；可视化汇总入口为 [`THIRD_PARTY_NOTICES.html`](澪Agent应用/public/live2d-pet/THIRD_PARTY_NOTICES.html)。

## 其他依赖

后端还使用 Uvicorn、HTTPX、Jinja2、Pillow、python-docx、OpenPyXL、pypdfium2、MSS、DXCam、PyYAML、websockets 等开源库；前端还使用 Lucide、DOMPurify、Marked 等开源库。完整版本清单见：

- [`私人AI日记系统/backend/requirements.txt`](私人AI日记系统/backend/requirements.txt)
- [`澪Agent应用/package-lock.json`](澪Agent应用/package-lock.json)
- [`澪Agent应用/live2d-desktop/package-lock.json`](澪Agent应用/live2d-desktop/package-lock.json)

如果发现遗漏、错误归属或许可文本需要补充，请通过 Issue 报告，并附上对应上游项目和版本。
