# 参与贡献

## 开发流程

1. 从独立分支开始，保持改动聚焦。
2. 不提交 `.env`、API Key、QQ Token、数据库、聊天原文、日记、附件、日志、音色素材或模型文件。
3. 用户可见文案优先使用中文；URL、API 字段和必要代码标识使用英文。
4. 修改共享行为时补充对应单元测试。
5. 提交前运行：

```powershell
cd backend
.\.venv\Scripts\python.exe -m compileall app
.\.venv\Scripts\python.exe -m unittest discover -s tests
git diff --check
```

桌面界面修改还需在相邻的 `澪Agent应用` 目录执行 `npm run build` 和 Electron 测试。公开仓库使用单仓库结构，但两个子目录仍分别维护 Python 与 Node.js 依赖。

## 行为边界

- 不默认开启云端屏幕上传、QQ 群回复或主动消息。
- 不新增系统音色兜底；语音失败时退回文字。
- 不把模型输出直接当成可执行代码。
- 不改变第三方资产许可证，也不提交没有分发权的角色或音色素材。
