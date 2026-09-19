# Mio

[English](GitHub项目介绍_EN.md) · [下载 Windows 版](https://github.com/ochiru520/Mio/releases/latest) · [反馈问题](https://github.com/ochiru520/Mio/issues)

Mio 是 Windows 上的个人 AI 伙伴与 Agent。它把对话、长期记忆、日记、语音、QQ 和桌宠放在同一套本地数据里，也提供处理文件、生成图片与视频的任务工作台。

**公开版本：0.3.1 · 预览版 · Windows x64。** 普通用户下载安装程序即可，不需要自行安装 Python 或 Node.js。云端模型需要你自己的 API；本地模型和其他扩展能力按需配置。

## 从哪里开始

1. 从 [Releases](https://github.com/ochiru520/Mio/releases/latest) 下载 `Mio-0.3.1-Windows-x64-Setup.exe`，校验旁边的 SHA-256 文件后运行。
2. 在首次向导中确认环境、名字与称呼，添加模型供应商。可以先跳过模型配置，稍后到“设置 > 模型与 API”完成。
3. 发起一次对话，再按需要开启记忆、日记、QQ、语音或桌宠。启用云端能力前确认供应商价格。
4. 在“设置 > 数据与隐私”建立完整备份。

安装要求：Windows 11 x64 是主要验证环境；桌面主窗口需要 [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)。模型权重、QQ/NapCat、自定义角色资产和参考音频不随主安装包提供。

## 可以做什么

| 入口 | 当前能力 |
|---|---|
| 对话 | 多会话，多个模型供应商，Responses / Chat Completions，附件，思考档位，Token 与费用记录 |
| 记忆 | 查看来源、人工编辑与确认、处理冲突、停用和恢复历史版本 |
| 日记 | 每日日记与回顾、周记、月记、成长统计、Markdown 导出；月记显示完整月份和来源日记 |
| Agent 工作台 | 任务执行与状态追踪，授权范围内的文件工具，ComfyUI 工作流和已配置生成服务，失败恢复与回执核对 |
| 环境与模型 | 检查、安装、验证和卸载受管可选模型；卸载前显示范围，外部复用资源在原位置管理 |
| 桌宠与语音 | 独立 Live2D 窗口，动作和表情，Genie 本地音色，faster-whisper 转写及语音互动 |
| QQ 与观察 | NapCat / OneBot 私聊和群聊；按需启用屏幕、窗口及系统声音观察 |
| 数据与更新 | 本地 SQLite、完整备份恢复、迁移检查、签名更新清单、下载进度和确认安装 |

QQ、主动联系、自动记录、联网和观察等能力需要分别配置。已安装文件、已配置地址、已连接服务和通过真实调用是不同状态；请以界面实际检测结果为准。

## 0.3.1 的变化

- 新增可选模型卸载，保护共用环境、个人资料和自定义资源。
- 修复中文 Windows 上的 Genie / Whisper 验证编码及 Genie 资源路径问题。
- 修复任务重试关联、人工记忆保护、沉睡记忆继续进入提示、验证缓存和诊断脱敏问题。
- 整理 Agent 首页、模型选择、更新页面、周记摘要及记录空态。
- 修复月记受首页最近日记窗口影响而漏显示月份、篇数不准的问题。

0.3.0 曾下架；请使用本次修复版。0.2.1 及更早版本需先手动安装 0.3.1，随后可使用应用内更新。详情见[使用与升级指南](使用与升级指南.md)和[版本说明](版本说明.md)。

## 数据、隐私与费用

数据保存在本机选择的数据目录，升级时沿用原目录。模型密钥通过 Windows DPAPI 加密，只对当前 Windows 用户有效；备份不等于跨机器自动恢复密钥。

使用云端模型、搜索或云端视觉时，完成请求所需的内容会发送给你选择的服务。本地优先不代表全部离线。更新检查只读取发布服务；屏幕、系统声音、QQ 和主动行为可单独关闭，也可使用隐私总控暂停。

公开仓库不包含作者的聊天、日记、私人角色设定、API Key、QQ 登录态、训练音频或私人模型。费用展示依赖供应商可提供的信息，不能替代供应商账单。

## 从源码运行

开发环境：Windows x64、Python 3.10（发布构建使用 3.10.11）、Node.js 22.12+。源码 ZIP 不需要 Git；构建安装器另需 Inno Setup 6。

先构建界面：

```powershell
cd .\澪Agent应用
npm ci
npm run build
```

再启动后端：

```powershell
cd ..\私人AI日记系统\backend
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/agent-app/`。开发时可在桌面目录运行 `npm run dev`；完整 Windows 构建使用 `构建Windows应用.ps1`。后端只应监听本机，不要直接暴露到公网。

```text
Mio/
├─ 私人AI日记系统/   FastAPI、SQLite、记忆、记录与服务
├─ 澪Agent应用/      Vue、Windows 启动器、更新器与 Live2D
├─ README.md
└─ README_EN.md
```

中文子目录名称保留用于兼容构建和旧路径。公开版本号与私人开发版本号属于不同通道，不按数字大小互相升级。

## 验证与限制

后端、桌面启动器／更新器、前端与 Live2D 都有自动化测试，CI 在 Windows 执行。测试命令和贡献要求见 [CONTRIBUTING](../CONTRIBUTING.md)。本机安装、旧数据保留和隔离场景验收不能覆盖所有显卡、声卡、供应商或长期使用情况。

当前仍是预览版。另一台干净 Windows 首装、真人 QQ／麦克风、第三方服务的长任务和跨机器使用需要持续反馈。安装包未做 Windows 代码签名；请从官方 Release 下载并核对校验文件。

## 文档与许可

- [使用与升级指南](使用与升级指南.md)
- [版本说明](版本说明.md)
- [隐私说明](隐私说明.md) · [安全说明](../SECURITY.md)
- [资产与第三方许可](资产与第三方许可.md)

原创代码和文档采用 [MIT](../LICENSE)。Live2D、模型权重、音色、角色图片及用户导入资产遵循各自许可。提交 Issue 时请只附脱敏诊断和复现步骤，不要上传数据库、密钥或私人对话。
