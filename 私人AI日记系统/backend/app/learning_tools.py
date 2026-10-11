"""Learning tools backed by the same sessions the desktop and mobile display."""
from pydantic import Field
from .tool_types import ToolInput, ToolDefinition, ToolPermission as Permission


class SessionCreate(ToolInput):
    title: str = Field(min_length=1, max_length=160)
    goal: str = Field(default='', max_length=2000)
    source_document_id: str = Field(default='', max_length=80)


class SessionId(ToolInput):
    session_id: str = Field(min_length=1, max_length=80)


class Question(SessionId):
    prompt: str = Field(min_length=1, max_length=5000)
    answer_key: str = Field(default='', max_length=2000)
    source_ref: str = Field(default='', max_length=500)


class Answer(ToolInput):
    item_id: str = Field(min_length=1, max_length=80)
    answer: str = Field(min_length=1, max_length=10000)


DEFINITIONS = [
    ToolDefinition('learning_sessions', Permission.READ_ONLY, '读取当前项目学习会话、真实练习和作答记录。'),
    ToolDefinition('learning_create_session', Permission.LOW_RISK_WRITE, '按用户学习目标创建持久学习会话，资料 ID 只能来自本项目已读资料。', arguments_model=SessionCreate),
    ToolDefinition('learning_read_session', Permission.READ_ONLY, '读取当前项目指定学习会话与练习；不虚构成绩。', arguments_model=SessionId),
    ToolDefinition('learning_add_question', Permission.LOW_RISK_WRITE, '按已读内容创建练习及参考答案，使用真实来源位置；先建立学习会话。', arguments_model=Question),
    ToolDefinition('learning_submit_answer', Permission.LOW_RISK_WRITE, '记录用户实际答案与反馈、复习日期；不替用户作答或编造成绩。', arguments_model=Answer),
    ToolDefinition('learning_review', Permission.READ_ONLY, '读取当前项目到期、低分和未作答的复习题。'),
]
NAMES = {item.name for item in DEFINITIONS}


async def dispatch(name, arguments, context):
    from . import workspace_service as workspace, learning_service as learning
    project = workspace.project_for_conversation(context.conversation_id)
    if not project or project['archived']: raise ValueError('请先把学习对话放入有效项目。')
    project_id = project['id']
    if name == 'learning_sessions': return {'sessions': learning.sessions(project_id)}
    if name == 'learning_create_session': return learning.create_session(project_id, **arguments)
    if name == 'learning_read_session': return learning.session(project_id, **arguments)
    if name == 'learning_add_question': return learning.add_item(project_id, **arguments)
    if name == 'learning_submit_answer': return learning.answer(project_id, arguments['item_id'], arguments['answer'])
    if name == 'learning_review': return learning.review(project_id)
    raise ValueError('未知学习工具。')
