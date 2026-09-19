# 参与贡献

## 开发流程

1. 从独立分支开始，保持改动聚焦。
2. 不提交 `.env`、API Key、QQ Token、数据库、聊天原文、日记、附件、日志、音色素材或模型文件。
3. 用户可见文案优先使用中文；URL、API 字段和必要代码标识使用英文。
4. 修改共享行为时补充对应单元测试。
5. 提交前运行：

```powershell
cd 私人AI日记系统/backend
.\.venv\Scripts\python.exe -m compileall app
.\.venv\Scripts\python.exe -m pytest -q tests
git diff --check
```

桌面界面修改还需在仓库根目录下的 `澪Agent应用` 执行 `npm ci`、`npm test` 和 `npm run build`，并在 `澪Agent应用/live2d-desktop` 执行 `npm ci` 与 `npm run test:model`。完整检查步骤见 `.github/workflows/ci.yml`。公开仓库使用单仓库结构，但两个子目录仍分别维护 Python 与 Node.js 依赖。

## 版本与发布

当前公开版本为 0.3.0。修改版本时同步桌面 package.json、锁文件、Windows 版本、安装器和更新通道；更新中英文说明。旧提交说明中的预览编号属于历史记录，不代表当前发行版本。

发布安装器前必须核对构建清单、SHA-256 和签名更新清单，并验证保留数据的安装与失败恢复。0.2.1 及更早版本需要手动安装带更新器的版本一次。签名私钥与私人数据不得提交。

## 行为边界

- 不默认开启云端屏幕上传、QQ 群回复或主动消息。
- 不新增系统音色兜底；语音失败时退回文字。
- 不把模型输出直接当成可执行代码。
- 不改变第三方资产许可证，也不提交没有分发权的角色或音色素材。
