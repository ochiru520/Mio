# 私人 AI 日记系统第一阶段 MVP

## 边界

第一版只做本地网页、聊天、SQLite 记录、读取两份说明书、生成当天 Markdown 日记和本地查看日记。

暂时不做 QQ / 微信接入、主动推送、云服务器部署、登录、多用户和向量数据库。

## 命名规则

- 用户直接看到和维护的目录、页面、文案、日记内容优先使用中文。
- Python 模块、API 路径、数据库表名和字段名、环境变量使用英文。

## 本地启动

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
```

填写 `backend\.env` 后启动：

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

访问：

```text
http://127.0.0.1:8000/chat
http://127.0.0.1:8000/diaries
```
