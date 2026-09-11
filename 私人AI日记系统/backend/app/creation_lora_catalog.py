"""Human-readable, runtime-visible LoRA guidance for Agent image planning.

The catalog is intentionally keyed by stable IDs.  Workflow filenames remain
the execution source of truth; this module only explains when a registered
LoRA is appropriate and which entries are locked by the user's workflow.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


IMAGE_LORA_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "highres_boost",
        "name": "细节增强",
        "filename": "功能_Anima_美学增强_HighresBoost.safetensors",
        "purpose": "增强细节、纹理和高分辨率观感；不负责改变主体身份或主要画风。",
        "use_when": ["高清", "高细节", "材质纹理清楚", "壁纸", "海报"],
        "avoid_when": ["极简平涂", "刻意模糊", "柔焦低细节"],
        "selection_terms": ["高清", "高细节", "壁纸", "海报", "high detail", "wallpaper"],
        "default_strength": 0.8,
        "selectable": True,
        "locked": False,
        "notes": "实际效果以当前无敌图片工作流的 A/B 对比为准。",
    },
    {
        "id": "sensual_style",
        "name": "成熟性感画风",
        "filename": "画风_色色_Anima_v5.safetensors",
        "purpose": "把画面推向成熟、性感、暧昧的视觉气质；不应在用户没有明确要求时主动使用。",
        "use_when": ["成熟性感", "暧昧", "泳装", "时尚写真"],
        "avoid_when": ["全年龄", "儿童或未成年角色", "清纯日常", "用户未表达成人化意图"],
        "selection_terms": ["性感", "成熟", "暧昧", "泳装", "丝袜", "sensual", "sexy", "lingerie"],
        "default_strength": 0.8,
        "selectable": True,
        "locked": False,
        "notes": "仅描述风格倾向，不放宽内容安全边界；实际效果以 A/B 对比为准。",
    },
    {
        "id": "clear_lineart",
        "name": "清晰线稿",
        "filename": "画风_清晰线稿_Anima_v1.0.safetensors",
        "purpose": "强化轮廓线、线条边缘和插画稿的清晰度，适合角色立绘与漫画感画面。",
        "use_when": ["线条清楚", "清晰线稿", "角色立绘", "漫画感", "赛璐璐插画"],
        "avoid_when": ["厚涂", "油画笔触", "写实摄影", "柔焦"],
        "selection_terms": ["线稿", "漫画线", "清晰线条", "lineart", "line art"],
        "default_strength": 0.8,
        "selectable": True,
        "locked": False,
        "notes": "不等于自动提高分辨率；它主要影响线条表现。",
    },
    {
        "id": "soft_cel_pastel",
        "name": "柔和赛璐璐粉彩",
        "filename": "画风_柔和赛璐璐粉彩_Anima_v1.1.safetensors",
        "purpose": "增加柔和赛璐璐上色、粉彩色调和轻柔阴影的观感。",
        "use_when": ["柔和", "粉彩", "pastel", "赛璐璐", "轻柔色彩"],
        "avoid_when": ["强烈高对比", "硬朗写实", "暗黑厚重", "金属工业质感"],
        "selection_terms": ["柔和", "赛璐璐", "粉彩", "pastel", "soft cel"],
        "default_strength": 0.8,
        "selectable": True,
        "locked": False,
        "notes": "与清晰线稿可以同时使用，但不要为了凑数强行叠加。",
    },
    {
        "id": "turbo_v01",
        "name": "Anima Turbo v0.1",
        "filename": "功能_Anima_加速_Turbo_v0.1.safetensors",
        "purpose": "工作流固定的加速 LoRA，与 4 steps / CFG 1.0 采样参数配套。",
        "use_when": ["所有无敌图片工作流生图"],
        "avoid_when": ["手动关闭", "替换为其他加速 LoRA"],
        "default_strength": 1.0,
        "selectable": False,
        "locked": True,
        "notes": "由后端按工作流强制开启，Agent 不得在 lora_choices 中填写。",
    },
    {
        "id": "turbo_v02",
        "name": "Anima Turbo v0.2",
        "filename": "功能_Anima_加速_Turbo_v0.2.safetensors",
        "purpose": "工作流固定的加速 LoRA，与 4 steps / CFG 1.0 采样参数配套。",
        "use_when": ["所有无敌图片工作流生图"],
        "avoid_when": ["手动关闭", "替换为其他加速 LoRA"],
        "default_strength": 1.0,
        "selectable": False,
        "locked": True,
        "notes": "由后端按工作流强制开启，Agent 不得在 lora_choices 中填写。",
    },
)

IMAGE_OPTIONAL_LORAS: dict[str, str] = {
    item["id"]: item["filename"]
    for item in IMAGE_LORA_CATALOG
    if item["selectable"] and not item["locked"]
}
IMAGE_REQUIRED_LORAS: set[str] = {
    item["filename"]
    for item in IMAGE_LORA_CATALOG
    if item["locked"]
}


def list_image_loras(workflow_id: str = "anima-2.9b-image") -> dict[str, Any]:
    """Return a copy safe to expose to the planner and UI receipts."""

    return {
        "workflow_id": str(workflow_id or "anima-2.9b-image"),
        "loras": deepcopy([item for item in IMAGE_LORA_CATALOG if item["selectable"]]),
        "locked_loras": deepcopy([item for item in IMAGE_LORA_CATALOG if item["locked"]]),
        "selection_rule": "只提交可选 LoRA 的稳定 ID；未明确匹配时保持可选 LoRA 关闭；固定 Turbo 始终开启。",
    }


__all__ = [
    "IMAGE_LORA_CATALOG",
    "IMAGE_OPTIONAL_LORAS",
    "IMAGE_REQUIRED_LORAS",
    "list_image_loras",
]
