# Mio 桌面应用

本目录提供 Vue 主界面、Windows 启动器、安全更新器与独立 Electron Live2D 桌宠。后端位于相邻 `私人AI日记系统`。用户运行一个 Mio 应用，启动器负责本机服务和窗口，数据沿用安装时选择的位置。

## 开发与构建

要求 Node.js 22.12+，后端 Python 3.10 环境已准备好。开发预览连接 `127.0.0.1:8000`。

```powershell
npm ci
npm test
npm run build
npm run dev
```

完整 Windows 构建：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\构建Windows应用.ps1
```

安装器需 Inno Setup 6；只构建程序目录可加 `-SkipInstaller`。制品位于 `release/`，包含构建身份与哈希清单。数据目录与程序目录分开，升级必须沿用既有数据且预先备份。

## 验证

```powershell
python -m unittest discover -s desktop -p "test_*.py"
python -m unittest discover -s scripts -p "test_*.py"
python -m unittest discover -s scripts/deps -p "test_*.py"
cd live2d-desktop
npm ci
npm run test:model
```

前端组件通过显式注入访问功能；更新操作只调用固定原生桥接口，安装需确认。环境中心卸载模型先展示受管范围。周期记录使用后端完整月份目录，不能从首页最近日记推算月份和篇数。

## 文档

[使用与升级指南](../私人AI日记系统/文档/使用与升级指南.md) · [版本说明](../私人AI日记系统/文档/版本说明.md) · [隐私说明](../私人AI日记系统/文档/隐私说明.md)

公开发行使用中性占位图，不附带私人角色资料、模型权重和参考音频。原创代码采用 [MIT](LICENSE)，Live2D 和其他第三方资产保留各自许可。
