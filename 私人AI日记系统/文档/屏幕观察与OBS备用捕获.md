# 屏幕观察与 OBS 备用捕获

## 默认捕获

澪默认使用以下顺序：

- 游戏窗口优先使用 DXGI。
- 整个屏幕优先使用 MSS。
- 捕获失败时使用 ImageGrab 等兼容路径降级。
- 截图只在内存中短暂存在，不写入磁盘。

普通窗口和无边框窗口不需要安装或配置 OBS。

## 什么时候使用 OBS

遇到以下情况时再启用 OBS：

- 独占全屏游戏只能得到黑屏。
- 游戏使用特殊渲染或反作弊机制，DXGI 无法读取。
- 已经在 OBS 中配置了稳定的“游戏捕获”来源。

在 OBS 中创建一个“游戏捕获”或“显示器捕获”来源，并记住来源名称。然后在运行数据的 `.env` 中配置：

```env
MIO_CAPTURE_BACKEND=obs
OBS_WEBSOCKET_URL=ws://127.0.0.1:4455
OBS_WEBSOCKET_PASSWORD=
OBS_SOURCE_NAME=游戏捕获
OBS_WEBSOCKET_TIMEOUT_SECONDS=8
```

重新启动澪 Agent 后生效。OBS 连接、鉴权或源名称错误时，澪会自动降级到原有捕获方式，并在屏幕观察状态中显示具体原因。

恢复默认捕获：

```env
MIO_CAPTURE_BACKEND=auto
```

## 四小时压力测试

先打开准备同时使用的游戏、QQ、浏览器、桌宠和澪 Agent，然后运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\工具\屏幕观察压力测试.ps1"
```

默认运行 240 分钟，每 10 秒检查一次：

- Agent 健康状态
- 观察器独立进程状态
- 帧编号是否持续增长
- 自动恢复次数
- Agent 和观察器最大内存占用

压力测试使用“仅捕获模式”，不会调用视觉模型，也不会产生模型费用。测试结束后会自动停止观察，并将 JSON 报告写入：

```text
D:\澪Agent数据\压力测试
```

快速测试示例：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\工具\屏幕观察压力测试.ps1" -DurationMinutes 10 -SampleSeconds 5
```
