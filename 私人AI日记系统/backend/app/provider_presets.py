from __future__ import annotations


PROVIDER_PRESETS: tuple[dict[str, object], ...] = (
    {"id": "openai", "name": "OpenAI", "kind": "official", "protocol": "openai", "base_url": "https://api.openai.com/v1"},
    {"id": "deepseek", "name": "DeepSeek", "kind": "official", "protocol": "deepseek", "base_url": "https://api.deepseek.com/v1"},
    {"id": "opencode_go", "name": "OpenCode Go", "kind": "official", "protocol": "opencode_go", "base_url": "https://opencode.ai/zen/go/v1", "note": "GPT/Luna/Grok 走 Responses；GLM/Kimi/DeepSeek/MiMo/Hy3 走 Chat Completions；Claude /messages 暂不支持。"},
    {"id": "bailian", "name": "阿里云百炼", "kind": "official", "protocol": "openai", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    {"id": "volcengine_ark", "name": "火山方舟", "kind": "official", "protocol": "openai", "base_url": "https://ark.cn-beijing.volces.com/api/v3"},
    {"id": "zhipu", "name": "智谱开放平台", "kind": "official", "protocol": "openai", "base_url": "https://open.bigmodel.cn/api/paas/v4"},
    {"id": "moonshot", "name": "月之暗面 Moonshot", "kind": "official", "protocol": "openai", "base_url": "https://api.moonshot.cn/v1"},
    {"id": "siliconflow", "name": "硅基流动", "kind": "official", "protocol": "openai", "base_url": "https://api.siliconflow.cn/v1"},
    {"id": "openrouter", "name": "OpenRouter", "kind": "official", "protocol": "openai", "base_url": "https://openrouter.ai/api/v1"},
    {"id": "aihub", "name": "AIHub", "kind": "relay", "protocol": "openai", "base_url": "https://aihub.top", "default_api_mode": "responses", "note": "按 Codex 配置使用 Responses API；模型 ID 以 AIHub 控制台为准。"},
    {"id": "ekti", "name": "Ekti AI CHAT", "kind": "relay", "protocol": "openai", "base_url": "https://chat.ekti.cc/v1", "default_api_mode": "responses", "note": "Codex 模型按官方文档使用 Responses API。"},
)


def public_provider_presets() -> list[dict[str, object]]:
    return [dict(item) for item in PROVIDER_PRESETS]


def provider_preset(preset_id: str) -> dict[str, object] | None:
    clean = str(preset_id or "").strip().lower()
    return next((dict(item) for item in PROVIDER_PRESETS if item["id"] == clean), None)
