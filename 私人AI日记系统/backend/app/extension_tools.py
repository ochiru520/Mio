"""Agent-facing persistent extension and skill lifecycle registration entry point."""
import asyncio
from pydantic import Field
from .tool_types import ToolInput, ToolDefinition, ToolPermission
from .extension_service import Manifest, TestCase


class SkillDraft(ToolInput):
    name: str = Field(min_length=1,max_length=100)
    description: str = Field(default='',max_length=500)
    content: str = Field(min_length=1,max_length=100000)
    skill_id: str = Field(default='',max_length=80)
    expected_revision: int = Field(default=0,ge=0)


class DraftId(ToolInput):
    draft_id: str = Field(min_length=1,max_length=80)


class ExtensionId(ToolInput):
    extension_id: str = Field(min_length=1,max_length=80)
    version: str = Field(default='',max_length=40)


class ExtensionSave(ToolInput):
    manifest: Manifest
    source: str = Field(default='',max_length=100000)
    tests: list[TestCase] = Field(default_factory=list,max_length=30)
    expected_revision: int = Field(default=0,ge=0)


class ExtensionValidate(ExtensionId):
    expected_revision: int = Field(ge=1)


class ExtensionEnabled(ExtensionValidate):
    enabled: bool


class ExtensionRollback(ToolInput):
    extension_id: str = Field(min_length=1,max_length=80)
    expected_revision: int = Field(ge=1)


class ExtensionCall(ExtensionId):
    arguments: dict = Field(default_factory=dict)


DEFINITIONS = [
    ToolDefinition('skill_save_draft',ToolPermission.LOW_RISK_WRITE,'根据用户目标保存持久技能草稿；不改变当前启用版本、不执行代码、不授予权限。',arguments_model=SkillDraft),
    ToolDefinition('skill_activate',ToolPermission.LOW_RISK_WRITE,'激活已保存的技能草稿，核对来源版本；在下一轮按需读取，指导不覆盖用户要求。',arguments_model=DraftId),
    ToolDefinition('extension_list',ToolPermission.READ_ONLY,'发现持久扩展的名称、已启用版本、参数结构、所需能力和验证状态；schema 按需用 extension_read 读取。'),
    ToolDefinition('extension_read',ToolPermission.READ_ONLY,'读取扩展精确版本、参数schema、受限Python正文或声明式面板；运行只允许已验证且当前启用版本。',arguments_model=ExtensionId),
    ToolDefinition('extension_save_draft',ToolPermission.LOW_RISK_WRITE,'保存新版本的持久扩展草稿。Python入口 def handle(arguments)，无import/open/反射/系统访问，仅纯JSON数据；宿主按file_inputs读取授权文本到arguments["files"]，outputs:[{name,content}]经artifact.write生成独立成果。测试含arguments、expected和合成files。面板仅text/metric/table，无脚本HTML。扩展不授予权限。',arguments_model=ExtensionSave),
    ToolDefinition('extension_validate',ToolPermission.LOW_RISK_WRITE,'在独立进程、临时目录和合成输入中验证扩展契约与所有测试；不读取真实数据、不写成果，失败版本不能启用。独立进程不是OS沙箱。',arguments_model=ExtensionValidate,timeout_seconds=120,idempotent=False),
    ToolDefinition('extension_set_enabled',ToolPermission.LOW_RISK_WRITE,'启用已验证版本或停用扩展；停止其运行进程，不扩大权限。版本变化后下轮重新发现。',arguments_model=ExtensionEnabled),
    ToolDefinition('extension_rollback',ToolPermission.LOW_RISK_WRITE,'恢复上一已启用且已验证扩展版本，停止旧运行；不改授权。',arguments_model=ExtensionRollback),
    ToolDefinition('extension_call',ToolPermission.LOW_RISK_WRITE,'调用本轮发现的扩展精确启用版本（请填写version和extension_read给出的arguments）；宿主复核参数/文件授权/任务停止。扩展只能处理受控输入和写独立文本成果，无凭据、网络或系统对象。',arguments_model=ExtensionCall,timeout_seconds=40,idempotent=False),
]
NAMES = frozenset(item.name for item in DEFINITIONS)


async def dispatch(name, arguments, context):
    from . import agent_skill_service as skills, extension_service as extensions
    args=dict(arguments)
    if name=='skill_save_draft':
        return await asyncio.to_thread(skills.save_draft,**args)
    if name=='skill_activate':
        return await asyncio.to_thread(skills.activate_draft,**args)
    if name=='extension_list':
        return {'extensions':await asyncio.to_thread(extensions.listing)}
    if name=='extension_save_draft':
        return await asyncio.to_thread(extensions.save,**args)
    identifier=args.pop('extension_id')
    if name=='extension_read':
        return await asyncio.to_thread(extensions.read,identifier,**args)
    if name=='extension_validate':
        return await extensions.validate(identifier,**args)
    if name=='extension_set_enabled':
        return await asyncio.to_thread(extensions.enabled,identifier,**args)
    if name=='extension_rollback':
        return await asyncio.to_thread(extensions.rollback,identifier,**args)
    if name=='extension_call':
        if not args.get('version'):
            raise ValueError('请先发现扩展并填写精确版本，新增能力在下一轮加载。')
        return await extensions.invoke(identifier,context=context,**args)
    raise ValueError('未知扩展工具。')
