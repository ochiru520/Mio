# Mio 后端

本目录包含 FastAPI 本地后端：对话、模型供应商、结构化记忆、日记／周记／月记、任务执行、QQ、语音和观察服务。桌面界面位于相邻 `澪Agent应用`；应用运行时各入口共享所选本地数据目录。

产品功能、环境要求、构建步骤与边界见[项目介绍](文档/GitHub项目介绍.md)，日常使用见[使用与升级指南](文档/使用与升级指南.md)，本次变更见[版本说明](文档/版本说明.md)。

## 开发启动

先在相邻前端执行 `npm ci` 和 `npm run build`，再在本目录：

```powershell
cd backend
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

主界面地址为 `http://127.0.0.1:8000/agent-app/`。模型可在应用中配置，密钥通过 Windows DPAPI 保存。不要提交 `.env`、运行数据或私人人格。新安装的自动行为默认关闭，按用户选择开启。

## 验证

```powershell
cd backend
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest -q tests
```

涉及模型、权限、任务或数据变更时，同时验证失败状态与恢复行为。不要把隔离测试当作真人 QQ、麦克风或云端服务已验收。

## 导航

- [代码结构与模块边界](文档/代码结构与模块边界.md)
- [首次启动与数据迁移](文档/首次启动与数据迁移.md)
- [隐私说明](文档/隐私说明.md)
- [安全说明](SECURITY.md)
- [资产与第三方许可](文档/资产与第三方许可.md)

原创代码与文档采用 [MIT](LICENSE)，第三方资源保留原许可。
