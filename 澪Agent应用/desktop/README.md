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


## 应用更新与发布

应用设置入口为“基础与启动 → 应用更新”。更新检查独立于 AI 联网搜索，不调用聊天模型；自动下载为单独选项，安装需要用户确认。源代码预览不允许安装程序。

更新代码在 `desktop/updates/`，发布脚本为 `desktop/publish_update.py`。安装器和助手必须使用同一构建的更新公钥。新开发构建默认 `development` 通道，不与公开的 `stable` 混合比较。

### 依赖与本地测试

```powershell
python -m pip install -r .\desktopequirements-updater.txt
python -m unittest discover -s desktop -p "test_*.py"
npm test
```

`requirements-desktop.txt` 同时包含该更新依赖。公开源码 CI 和导出验证会先安装更新依赖再运行桌面测试。

### 正式版本准备

只在脱敏后的公开候选源码中执行正式构建。先统一 `package.json`、`desktop/version_info.txt` 和 `desktop/installer.iss` 的版本号，再设置通道：

```powershell
$env:MIO_UPDATE_CHANNEL = "stable"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\构建Windows应用.ps1
```

构建流程会检查三个版本是否一致，生成更新通道配置、独立升级助手、主程序和安装器。不一致时停止，不自动猜测或修改版本。

发布者首次配置签名密钥使用 `python .\desktop\publish_update.py init-key`。已有密钥不得随意重建；私人密钥使用当前 Windows 用户的 DPAPI 加密保存在源码工作区之外。源码仅包含验证公钥。更换电脑/Windows 用户前必须专门处理发布密钥迁移，不能以生成另一把密钥代替。

验收完成后，使用 `publish_update.py sign` 的 `--installer`、`--manifest`、`--notes`、`--output` 参数生成签名 `latest.json`。`--manifest` 指向同一构建的 `release/Mio/构建清单.json`。脚本会检查实际制品，不上传、不推送、不发布。

将安装器与匹配的 `latest.json` 放到同一 `v版本号` 草稿 Release，核对远端文件后再发布稳定版本。仅上传源码 ZIP 或仅上传 EXE 不能启用客户端更新。旧版用户需要先手动安装一次包含更新器的过渡版本。

### 验证与恢复边界

升级助手只复制/替换托管程序文件，保留原数据目录；维护备份使用 SQLite 安全备份。安装后由新冻结程序执行独立验证；失败时按事务记录恢复旧程序与兼容数据，恢复失败则保留锁和诊断，不能假装成功。

本轮本地已验证清单/下载边界、模拟安装事务、真实跨盘文件快照、冻结程序启动和原生升级验证/恢复模式。线上 Release 到实际安装的完整跨版本流程、干净 Windows 和断电故障注入仍属于发布前验收，不应把模拟测试描述为这些现场测试已完成。

`构建Windows应用.ps1 -FastPackage` 可为本地验收使用 ZIP 压缩；默认正式构建仍为 LZMA2。更换压缩方式会改变安装器摘要，必须对最终文件重新生成更新签名。
