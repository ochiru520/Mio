"""User-selected creation workflow constraints shared by planning and execution."""
from __future__ import annotations

from .creation_workflows import require_workflow

GENERATION_TOOLS = frozenset({"comfyui_generate_image", "comfyui_generate_video", "remote_generate_image"})


def selected_generation_tool(workflow_id: str) -> str:
    workflow = require_workflow(workflow_id)
    return "comfyui_generate_video" if workflow.media_type == "video" else "comfyui_generate_image"


def selection_instructions(workflow_id: str) -> str:
    workflow = require_workflow(workflow_id)
    return (
        f"用户在本次任务的工具切换器中选择了 {workflow.label}，workflow_id={workflow.id}。"
        f"需要生成时只可使用 {selected_generation_tool(workflow.id)}，明确传入该 workflow_id。"
        "这是用户明确选择，优先于历史记录、自动判断及默认工作流。不要切换到其他本地或远程生成器。"
        "如果消息要求另一个生成器，应请用户切换工具或选择自动调用，不得擅自替换。"
        "文件读取、素材转换、工作流检查等辅助工具仍可使用。选择工具本身不表示立即生成；"
        "结合用户消息判断操作，缺少必要参考图时询问或使用用户指定的当前会话图片。"
        f"工作流说明：{workflow.selection_hint or workflow.description}"
    )


def bind_selected_workflow(tool_name: str, arguments: object, workflow_id: str) -> object:
    if not workflow_id:
        return arguments
    if tool_name in GENERATION_TOOLS:
        if tool_name != selected_generation_tool(workflow_id):
            raise ValueError("当前已手动选择生成工具，请使用选定工作流；如需组合生图和视频，请切换为自动调用。")
        if not isinstance(arguments, dict):
            return arguments  # The tool schema reports malformed arguments.
        requested = arguments.get("workflow_id")
        if requested and requested != workflow_id:
            raise ValueError(f"当前已选择工作流 {workflow_id}，不能改用 {requested}。")
        return {**arguments, "workflow_id": workflow_id}
    if tool_name in {"creation_check_workflow", "creation_list_loras"} and isinstance(arguments, dict) and not arguments.get("workflow_id"):
        return {**arguments, "workflow_id": workflow_id}
    return arguments
