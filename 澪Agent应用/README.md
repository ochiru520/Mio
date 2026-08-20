# Mio 桌面应用

这是 Mio 的 Vue 桌面界面、Windows 启动器和独立 Electron Live2D 桌宠。它与相邻的 `私人AI日记系统` 后端共享同一个 SQLite 数据库、人格、记忆、日记和模型配置。

最终用户只会看到并运行一个“Mio”应用。Vue 是应用窗口内部的界面技术，不代表还要打开另一个网站；Windows 启动器会自动启动仅监听 `127.0.0.1` 的本地 FastAPI 服务，再把 `/agent-app/` 嵌入桌面窗口。

## 开发预览

```powershell
npm ci
npm run build
```

前端开发需要后端运行在 `http://127.0.0.1:8000`。执行 `启动预览.ps1` 可启动本地后端与 Vite 预览；脚本会从相邻目录发现后端项目。

## 桌面构建

先在相邻后端仓库创建 `backend/.venv` 并安装依赖，然后执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\构建Windows应用.ps1
```

构建产物位于 `release/`。当前正式安装目录为 `D:\Mio`，数据默认位于 `D:\Mio\Data`；安装界面可修改程序位置并选择沿用或创建独立数据目录。没有可用 D 盘时回落到当前用户的 `%LOCALAPPDATA%\MioAgent`。也可通过 `MIO_DESKTOP_STATE_DIR` 自定义并覆盖安装配置。

## 验证

```powershell
npm run build
npm audit
cd live2d-desktop
npm run test:model
npm audit
```

后端测试、隐私边界和完整配置说明见相邻后端仓库的 `README.md`。

首次使用时在“设置”中添加模型供应商，并按需配置人格、QQ、主动消息、附件和屏幕观察。开源源码包使用中性占位头像与工作区背景；替换图片时只使用自己拥有分发权的素材。

全新安装会进入首次启动向导，先检查核心运行环境和可选模型/服务状态；检查不会下载模型或启动 QQ、语音和观察服务。已有数据的升级安装继续使用原 SQLite、人格和设置。完整备份、导入恢复、数据库迁移状态和隐私总控位于“设置 > 数据与隐私”。不要直接复制正在运行的数据库，也不要把 `D:\Mio\Data` 提交到 Git。

## 许可证

源代码使用 [MIT License](LICENSE)。`public/live2d-pet/licenses/` 中的 Live2D 运行库和模型遵循各自许可。澪角色图片和音色素材不自动包含在 MIT 授权中。
