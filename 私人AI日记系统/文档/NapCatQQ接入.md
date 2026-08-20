# NapCat 本地 QQ 接入

## 当前方案

- 接入方式：NapCat 本地个人号 + OneBot v11 反向 WebSocket。
- 本地服务地址：`ws://127.0.0.1:8000/onebot/ws`
- 默认只允许白名单 QQ 号触发澪回复。
- QQ 消息会以 `source="qq"` 写入现有聊天记录，日终整理和日记生成可以读到。
- 群聊默认不启用；如果后续启用群聊，默认需要 @ 机器人后才回复。

## backend/.env 配置

在项目的 `backend\.env` 里追加：

```env
QQ_BOT_ENABLED=true
QQ_ONEBOT_TOKEN=
QQ_ALLOWED_USER_IDS=你的QQ号
QQ_ALLOWED_GROUP_IDS=
QQ_GROUP_MENTION_REQUIRED=true
QQ_IMAGE_ENABLED=true
QQ_IMAGE_MAX_COUNT=3
QQ_IMAGE_MAX_BYTES=8388608
QQ_IMAGE_DETAIL=auto
QQ_REPLY_DELAY_SECONDS=1.2
QQ_PROACTIVE_ENABLED=true
QQ_PROACTIVE_MIN_IDLE_MINUTES=120
QQ_PROACTIVE_MAX_IDLE_MINUTES=120
QQ_PROACTIVE_DAY_START_HOUR=9
QQ_PROACTIVE_DAY_END_HOUR=22
QQ_PROACTIVE_CHECK_SECONDS=300
WEB_SEARCH_ENABLED=true
WEB_SEARCH_MAX_RESULTS=5
WEB_SEARCH_TIMEOUT_SECONDS=12
```

注意：

- `QQ_ONEBOT_TOKEN` 不要提交到 Git。
- `QQ_ALLOWED_USER_IDS` 可以写多个 QQ 号，用英文逗号分隔。
- 第一版建议只填你的 QQ 号，不开群聊。
- `QQ_IMAGE_DETAIL` 可选 `low` / `auto` / `high`。`low` 更省，`high` 更适合看截图小字。
- QQ 图片只会临时下载并发送给中转站模型，不会长期保存到数据库或日记文件。
- `QQ_REPLY_DELAY_SECONDS` 控制多条短回复之间的间隔。
- 主动消息默认只在 9:00-22:00 生效。Agent 应用开着时，用户闲置约 2 小时后，澪会按当前上下文决定是否主动发短消息。
- `WEB_SEARCH_ENABLED=true` 时，澪只会在问题需要外部/实时信息，或你明确说“查一下、搜一下、上网看看、最新”等词时联网查询。

## NapCat WebUI 配置

在 NapCat 的网络配置里新增一个 OneBot 11 反向 WebSocket / WebSocket Client 连接：

- URL：`ws://127.0.0.1:8000/onebot/ws`
- Access Token：和 `QQ_ONEBOT_TOKEN` 一致
- 上报格式：OneBot v11
- 消息格式：默认即可；本系统会优先读取 text 消息段，读不到时使用 `raw_message`

配置后重启本地 FastAPI 服务和 NapCat 连接。

## 验证步骤

1. 启动“澪 Agent”桌面应用，由桌面启动器拉起后端。
2. 在 Agent 设置的“QQ通道”中查看六项诊断状态。
3. 用白名单 QQ 号私聊 NapCat 登录的 QQ。
4. 澪应该以 1-3 条短消息回复，中间会有轻微间隔。
5. 打开 Agent 的 QQ 共享对话，确认消息已同步。
6. 如果“消息通道”未通过，依次检查控制脚本、NapCat 程序、WebUI 配置、WebUI 连接和 QQ 登录状态。

## 主动消息

主动消息以 Agent 应用为主通道，并使用 `QQ_ALLOWED_USER_IDS` 中第一个私聊账号对应的共享上下文。

默认行为：

- 只在 9:00-22:00 发送。
- 从你最后一次发言或上一次主动消息开始算，默认等待约 2 小时。
- Agent 应用开着时，消息一定先写入应用；NapCat WebSocket 在线时，再把同一条消息同步到 QQ。
- Agent 应用关闭时不生成主动消息，也不调用模型。重新打开后会立即检查；如果已经超过 2 小时，会马上生成并写入应用。
- QQ 离线不会阻止应用内主动消息；QQ 登录失效时需要重新扫码，离线期间的旧消息不会在恢复后补发到 QQ。
- 如果你一直不回复，后续仍受白天时段和主动消息间隔限制，不会按分钟刷屏。
- 主动消息会写入聊天记录，但不会伪造用户消息。

## QQ 图片

支持私聊里直接发图片，或发图片时附带一句说明。

第一版限制：

- 默认每次最多处理 3 张图。
- 单张图默认最大 8 MB。
- 支持 JPEG、PNG、GIF、WebP。
- 支持 NapCat 传来的图片 URL、本地图片文件路径、`file://` 路径和 `base64://` 图片内容。
- 图片正文不长期保存，只在当次请求里临时转成模型可读的 data URL。
- 如果当前模型或中转站不支持视觉输入，澪会明确提示“模型这边没看成”，不会假装看到了。

图片会比纯文字消耗更多模型额度。截图、小字、长图通常更贵；普通照片或简单图会轻一些。

## 联网查询

默认不会每句话都上网。普通聊天、日记上下文、行动建议和她已经能回答的常识问题，不会触发联网。

触发方式：

- “查一下今天的……”
- “搜一下……”
- “上网看看……”
- “最新的……是什么”
- 直接发一个网页链接让澪看
- “现在……是谁 / 多少钱 / 什么情况”

默认回复里不附来源链接，避免 QQ 聊天变得像搜索报告。联网结果只作为澪回答时的参考资料。

## 安全边界

- 这是本地个人号桥接，不是 QQ 官方机器人。
- 不建议第一版接群聊，也不建议私聊全开。
- 如果 QQ 端异常刷屏，先把 `QQ_BOT_ENABLED=false`，重启服务。
