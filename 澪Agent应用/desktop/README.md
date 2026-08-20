# Mio Windows 桌面版

## 构建

在 PowerShell 中运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\构建Windows应用.ps1
```

免安装版输出到 `release\Mio\Mio.exe`。

如果电脑安装了 Inno Setup 6，还会在 `release` 中生成安装程序。构建脚本会从 Windows 安装登记读取工具位置，也支持安装在 D 盘。

当前公开安装程序发布者显示为 `Mio Project`，但尚未进行代码签名。Windows 可能显示 SmartScreen 提示；公开分发时应同时提供 SHA-256，用户只应从项目正式 Release 下载。

## 数据

桌面启动器优先沿用当前 `私人AI日记系统` 目录，因此 Agent 应用、QQ 和日记功能使用同一个 SQLite 数据库。

桌面运行配置、日志和 WebView 数据位于：

```text
Mio.exe 同目录下的 Data
```

安装程序默认安装到当前用户的 `Mio` 目录，数据默认位于程序目录内的 `Data`。向导仍可选择旧数据或其他目录；安装后所选目录写入程序旁的 `数据目录.txt`。便携版没有该文件时也使用 `Mio.exe` 同目录的 `Data`。旧数据只兼容迁移，不会被删除；环境变量 `MIO_DESKTOP_STATE_DIR` 的优先级更高，可用于测试隔离配置。

API Key 不会打包进 EXE。启动器只会在本机读取现有 `.env` 或本地运行目录中的配置；应用内新增的供应商 Key 使用当前 Windows 用户的 DPAPI 加密保存，不会以明文写入供应商 JSON。

`desktop.log` 单文件最多 2 MB，并保留 3 份历史日志，避免后台长期运行持续占用磁盘。
