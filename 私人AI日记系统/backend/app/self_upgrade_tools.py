"""Schemas and dispatch for version-bound local self-development tasks."""
from pydantic import Field
from .tool_types import ToolInput, ToolDefinition, ToolPermission as Permission


class Start(ToolInput):
    goal: str = Field(min_length=1, max_length=3000)
    acceptance: list[str] = Field(min_length=1, max_length=30)
    expected_revision: int = Field(ge=1)


class Job(ToolInput):
    job_id: str = Field(pattern=r'^upgrade_[0-9a-f]{32}$')


class Validate(Job):
    backend_tests: list[str] = Field(default_factory=lambda: ['tests/test_self_upgrade.py'], max_length=30)


DEFINITIONS = [
    ToolDefinition('self_upgrade_status', Permission.READ_ONLY, '读取自身源码绑定、安装身份、开发任务和真实检查/预览回执；开发副本不是OS沙箱。'),
    ToolDefinition('self_upgrade_start', Permission.HIGH_RISK_WRITE, '以当前完整源码和现有未提交内容创建双仓开发副本，绑定当前桌面对话的代码项目；不覆盖原源码。', arguments_model=Start, timeout_seconds=120),
    ToolDefinition('self_upgrade_validate', Permission.HIGH_RISK_WRITE, '启动当前开发源码的实际检查，指定相关后端tests/*.py并绑定源码hash；异步结果必须用status回读。', arguments_model=Validate),
    ToolDefinition('self_upgrade_build', Permission.HIGH_RISK_WRITE, '在开发副本构建完整前后端核心候选；缺编译环境或构建失败如实返回。', arguments_model=Job),
    ToolDefinition('self_upgrade_preview', Permission.HIGH_RISK_WRITE, '启动唯一合成数据候选预览，不启动付费模型或后台任务；验证身份后返回真实URL。', arguments_model=Job),
    ToolDefinition('self_upgrade_stop', Permission.LOW_RISK_WRITE, '终止所属构建/预览进程树并释放开发锁，保留代码与真实失败记录。', arguments_model=Job, idempotent=False),
    ToolDefinition('self_upgrade_request_apply', Permission.HIGH_RISK_WRITE, '请求应用已验证本地核心候选；默认等待最终用户确认，只有用户提前配置自动本地应用才交给独立更新器。工具不能设确认或扩大授权。', arguments_model=Job, timeout_seconds=60),
]
NAMES = {item.name for item in DEFINITIONS}


async def dispatch(name, arguments, context):
    from . import self_upgrade_service as service, agent_task_service as tasks
    if not context or not context.conversation_id.startswith('desktop_agent_') or context.source not in {'desktop', 'agent', 'agent_task', 'agent_background'}:
        raise ValueError('自身改造仅用于本机桌面 Agent，不接受手机或远程渠道。')
    if tasks.automatic_work_paused():
        raise ValueError('执行已暂停。')
    if name == 'self_upgrade_status':
        return service.status()
    if name == 'self_upgrade_start':
        return service.create(**arguments, conversation_id=context.conversation_id, task_id=context.task_id)
    job = service.get(arguments['job_id'])
    if job.get('conversation_id') != context.conversation_id or (job.get('task_id') and job['task_id'] != context.task_id):
        raise ValueError('改造任务不属于当前桌面对话。')
    handlers = {'self_upgrade_validate': service.validate, 'self_upgrade_build': service.build,
                'self_upgrade_preview': service.preview, 'self_upgrade_stop': service.stop,
                'self_upgrade_request_apply': service.apply}
    if name not in handlers:
        raise ValueError('未知自身改造工具。')
    return handlers[name](**arguments)
