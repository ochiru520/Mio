"""Owner workspace API for the desktop application."""
import asyncio
import hashlib
from contextlib import closing
from pathlib import Path
from typing import Literal
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from .. import db, workspace_service as service, knowledge_service as knowledge
from .. import routine_service as routines, browser_service as browser, creative_batch_service as batches
from .. import media_compose_service as media, agent_task_service as tasks, agent_file_service as files
from .. import workspace_pending_service as pending

router=APIRouter(prefix='/api/workspace',tags=['workspace'])


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')


class Project(Input):
    name: str=Field(min_length=1,max_length=160)
    description: str=Field(default='',max_length=6000)
    instructions: str=Field(default='',max_length=20000)
    archived: bool=False
    directory: str=Field(default='',max_length=1200)
    create_directory: bool=False


class Goal(Input):
    goal: str=Field(default='',max_length=12000)
    project_id: str=''
    template_id: str=''
    variables: dict=Field(default_factory=dict)
    request_key: str=Field(min_length=1,max_length=120)


class Feedback(Input):
    message: str=Field(min_length=1,max_length=12000)
    request_key: str=Field(min_length=1,max_length=120)


class Template(Input):
    name: str=Field(min_length=1,max_length=160)
    goal: str=Field(min_length=1,max_length=12000)
    instructions: str=Field(default='',max_length=20000)
    project_id: str=''


class Note(Input):
    title: str=Field(min_length=1,max_length=160)
    content: str=Field(min_length=1,max_length=20000)
    note_id: str=Field(default='',max_length=120)
    expected_revision: int | None=Field(default=None,ge=1)


class Selection(Input):
    job_id: str=''
    output_index: int=Field(default=0,ge=0)
    artifact_id: str=''
    task_id: str=''
    note: str=Field(default='',max_length=2000)


class Index(Input):
    path: str=Field(min_length=1,max_length=1200)


class ExternalRoot(Input):
    path: str = Field(min_length=1, max_length=1200)
    label: str = Field(default='', max_length=160)


class ExternalNoteLink(Input):
    root_id: str = Field(min_length=1, max_length=120)
    relative_path: str = Field(min_length=1, max_length=1200)


class ExternalNoteWrite(Input):
    content: str = Field(min_length=1, max_length=200000)
    expected_revision: int = Field(ge=1)
    expected_sha256: str = Field(min_length=64, max_length=64)


class Routine(Input):
    name: str=Field(min_length=1,max_length=160)
    project_id: str
    kind: Literal['schedule','files','task_completed','generation_completed']
    config: dict=Field(default_factory=dict)
    goal: str=Field(default='',max_length=12000)
    template_id: str=''
    enabled: bool=False


class Toggle(Input):
    enabled: bool


class LearningSession(Input):
    title: str = Field(min_length=1, max_length=160)
    goal: str = Field(default='', max_length=2000)
    source_document_id: str = ''


class LearningItem(Input):
    prompt: str = Field(min_length=1, max_length=5000)
    answer_key: str = Field(default='', max_length=2000)
    source_ref: str = Field(default='', max_length=500)


class LearningAnswer(Input):
    answer: str = Field(min_length=1, max_length=10000)


class BrowserConfig(Input):
    hosts: list[str]=Field(default_factory=list,max_length=100)
    allow_public: bool=False


class BrowserLogin(Input):
    url: str=Field(min_length=1,max_length=2000)


class Approval(Input):
    approve: bool


class BatchAction(Input):
    action: Literal['pause','resume','cancel','retry']
    index: int | None=None


async def call(fn,*args,**kwargs):
    try:
        if asyncio.iscoroutinefunction(fn): return await fn(*args,**kwargs)
        def blocking():
            from ..maintenance_service import mutation_scope
            with mutation_scope():return fn(*args,**kwargs)
        return await asyncio.to_thread(blocking)
    except (ValueError,OSError) as exc:
        raise HTTPException(400,str(exc)) from exc


@router.get('')
async def overview():
    service.initialize();routines.initialize();batches.initialize();media.initialize()
    return {'projects':service.projects(),'templates':service.templates(),'routines':routines.listing(),
            'tasks':tasks.list_tasks(limit=100),'browser_pending':browser.pending()}


class ConversationMode(Input):
    mode: Literal['chat', 'work', 'learn', 'image']


@router.get('/conversations')
async def conversation_index():
    return await call(service.conversation_index)


@router.put('/conversations/{conversation_id}/mode')
async def conversation_mode(conversation_id: str, payload: ConversationMode):
    return await call(service.save_conversation_mode, conversation_id, payload.mode)


@router.delete('/conversations/{conversation_id}/project')
async def unlink_conversation(conversation_id: str):
    return await call(service.unbind_conversation, conversation_id)


@router.get('/pending')
async def pending_items(project_id: str = '', limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return await call(pending.listing, project_id, limit=limit, offset=offset)


@router.get('/tasks')
async def project_tasks(project_id: str = '', limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return await call(service.task_page, project_id, limit=limit, offset=offset)


class SkillSave(Input):
    name: str = Field(min_length=1,max_length=100)
    description: str = Field(default='',max_length=500)
    content: str = Field(min_length=1,max_length=100000)
    expected_revision: int = Field(default=0,ge=0)
    enabled: bool = True


class SkillEnabled(Input):
    enabled: bool
    expected_revision: int = Field(ge=1)


class SkillImport(Input):
    path: str = Field(min_length=1,max_length=1200)
    skill_id: str = ''
    expected_revision: int = Field(default=0,ge=0)


@router.get('/skills')
async def skill_list():
    from ..agent_skill_service import listing
    return {'skills':await call(listing)}


@router.get('/skills/{skill_id}')
async def skill_read(skill_id: str, revision: int=Query(0,ge=0), offset: int=Query(0,ge=0), max_chars: int=Query(20000,ge=1,le=20000)):
    from ..agent_skill_service import read
    return await call(read,skill_id,revision=revision,offset=offset,max_chars=max_chars)


@router.post('/skills')
async def skill_create(payload: SkillSave):
    from ..agent_skill_service import save
    return await call(save,**payload.model_dump())


@router.put('/skills/{skill_id}')
async def skill_update(skill_id: str,payload: SkillSave):
    from ..agent_skill_service import save
    return await call(save,skill_id=skill_id,**payload.model_dump())


@router.post('/skills/{skill_id}/enabled')
async def skill_enabled(skill_id: str,payload: SkillEnabled):
    from ..agent_skill_service import enabled
    return await call(enabled,skill_id,**payload.model_dump())


@router.post('/skills/import')
async def skill_import(payload: SkillImport):
    from ..agent_skill_service import import_file
    return await call(import_file,**payload.model_dump())


@router.get('/projects/{project_id}/jobs')
async def project_jobs(project_id: str, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    return await call(service.job_page, project_id, limit=limit, offset=offset)


@router.get('/projects/{project_id}/learning')
async def learning_sessions(project_id: str):
    from .. import learning_service
    return {'sessions': await call(learning_service.sessions, project_id)}


@router.get('/external-roots')
async def external_roots():
    from .. import external_notes_service as external
    return {'roots': await call(external.roots)}


@router.post('/external-roots')
async def external_root(payload: ExternalRoot):
    from .. import external_notes_service as external
    return await call(external.authorize, payload.path, payload.label)


@router.get('/projects/{project_id}/external-notes')
async def external_notes(project_id: str):
    from .. import external_notes_service as external
    return {'notes': await call(external.list_notes, project_id)}


@router.post('/projects/{project_id}/external-notes')
async def link_external_note(project_id: str, payload: ExternalNoteLink):
    from .. import external_notes_service as external
    return await call(external.link, project_id, payload.root_id, payload.relative_path)


@router.get('/projects/{project_id}/external-notes/{note_id}')
async def read_external_note(project_id: str, note_id: str):
    from .. import external_notes_service as external
    return await call(external.read, project_id, note_id)


@router.put('/projects/{project_id}/external-notes/{note_id}')
async def write_external_note(project_id: str, note_id: str, payload: ExternalNoteWrite):
    from .. import external_notes_service as external
    return await call(external.write, project_id, note_id, **payload.model_dump())


@router.post('/projects/{project_id}/external-notes/{note_id}/refresh')
async def refresh_external_note(project_id: str, note_id: str):
    from .. import external_notes_service as external
    return await call(external.refresh, project_id, note_id)


@router.post('/projects/{project_id}/learning')
async def learning_create(project_id: str, payload: LearningSession):
    from .. import learning_service
    return await call(learning_service.create_session, project_id, **payload.model_dump())


@router.get('/projects/{project_id}/learning/review')
async def learning_review(project_id: str, limit: int = Query(50, ge=1, le=200)):
    from .. import learning_service
    return await call(learning_service.review, project_id, limit=limit)


@router.get('/projects/{project_id}/learning/{session_id}')
async def learning_detail(project_id: str, session_id: str):
    from .. import learning_service
    return await call(learning_service.session, project_id, session_id)


@router.post('/projects/{project_id}/learning/{session_id}/items')
async def learning_item(project_id: str, session_id: str, payload: LearningItem):
    from .. import learning_service
    return await call(learning_service.add_item, project_id, session_id, **payload.model_dump())


@router.post('/projects/{project_id}/learning/items/{item_id}/answer')
async def learning_answer(project_id: str, item_id: str, payload: LearningAnswer):
    from .. import learning_service
    return await call(learning_service.answer, project_id, item_id, payload.answer)


@router.get('/projects/{project_id}/history/{kind}')
async def project_history(project_id: str, kind: Literal['notes', 'selections', 'documents', 'batches', 'assemblies'],
                          limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    return await call(service.collection_page, project_id, kind, limit=limit, offset=offset)


@router.post('/projects')
async def create_project(payload:Project):
    return await call(service.save_project,**payload.model_dump())


@router.put('/projects/{project_id}')
async def edit_project(project_id:str,payload:Project):
    return await call(service.save_project,project_id=project_id,**payload.model_dump())


@router.delete('/projects/{project_id}')
async def delete_project(project_id: str):
    try:
        return await call(service.delete_project, project_id)
    except HTTPException as exc:
        if isinstance(exc.__cause__, service.ProjectBusyError):
            raise HTTPException(409, exc.detail) from exc
        raise


@router.delete('/projects/{project_id}/directory')
async def delete_project_directory(project_id: str):
    try:
        return await call(service.delete_project_directory, project_id)
    except HTTPException as exc:
        if isinstance(exc.__cause__, service.ProjectBusyError):
            raise HTTPException(409, exc.detail) from exc
        raise


@router.get('/projects/{project_id}')
async def project_detail(project_id:str):
    data=await call(service.detail,project_id)
    data['browser']=browser.config(project_id)
    for kind in ('documents', 'batches', 'assemblies'):
        page = await call(service.collection_page, project_id, kind)
        data[kind] = page[kind]
        data[kind + '_has_more'] = page['has_more']
    return data


@router.post('/projects/{project_id}/conversations/{conversation_id}')
async def link_conversation(project_id:str,conversation_id:str):
    return await call(service.bind_conversation,project_id,conversation_id)


@router.post('/projects/{project_id}/notes')
async def add_note(project_id:str,payload:Note):
    return await call(service.add_note,project_id,**payload.model_dump())


@router.post('/goals')
async def enqueue(payload:Goal):
    return await call(service.enqueue_goal,**payload.model_dump())


@router.post('/tasks/{task_id}/follow-up')
async def follow_up(task_id:str,payload:Feedback):
    return await call(service.follow_up,task_id,**payload.model_dump())


@router.post('/templates')
async def save_template(payload:Template):
    return await call(service.save_template,**payload.model_dump())


@router.put('/templates/{template_id}')
async def update_template(template_id:str,payload:Template):
    if not any(t['id']==template_id for t in service.templates()): raise HTTPException(404,'模板不存在。')
    return await call(service.save_template,template_id=template_id,**payload.model_dump())


@router.post('/projects/{project_id}/selections')
async def select(project_id:str,payload:Selection):
    return await call(service.select_result,project_id,**payload.model_dump())


@router.post('/projects/{project_id}/knowledge')
async def index(project_id:str,payload:Index):
    return await call(knowledge.index_document,project_id,payload.path)


@router.get('/projects/{project_id}/search')
async def search(project_id:str,q:str=Query(min_length=1,max_length=500)):
    return await call(knowledge.search,project_id,q)


@router.get('/knowledge/{document_id}/source')
async def source(document_id:str):
    path,item=await call(knowledge.source_file,document_id)
    return FileResponse(path,filename=item['name'],headers={'X-Content-Type-Options':'nosniff'})


@router.post('/knowledge/{document_id}/reindex')
async def reindex(document_id: str):
    return await call(knowledge.reindex_document, document_id)


@router.post('/projects/{project_id}/upload')
async def upload(project_id:str,request:Request,name:str=Query(min_length=1,max_length=160)):
    await call(service.require_project,project_id,active=True)
    if name!=Path(name).name or any(c in name for c in ':\\/\x00') or name.endswith(('.', ' ')):
        raise HTTPException(400,'文件名无效。')
    if Path(name).suffix.lower() not in {'.txt','.md','.pdf','.docx','.csv','.tsv','.xlsx','.json','.yaml','.yml'}:
        raise HTTPException(400,'支持 PDF、DOCX、XLSX、CSV/TSV 和常用文本资料。')
    data=bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data)>32*1024*1024: raise HTTPException(413,'单文件上限 32 MB。')
    # Keep the original filename visible while the digest isolates versions.
    directory=files.workspace()/'项目资料'/project_id/hashlib.sha256(data).hexdigest()[:16]
    directory.mkdir(parents=True,exist_ok=True)
    destination=directory/name
    if not destination.exists(): destination.write_bytes(data)
    return await call(knowledge.index_document,project_id,str(destination))


@router.post('/routines')
async def save_routine(payload:Routine):
    return await call(routines.save,**payload.model_dump())


@router.put('/routines/{routine_id}')
async def update_routine(routine_id:str,payload:Routine):
    if not any(r['id']==routine_id for r in routines.listing()):raise HTTPException(404,'自动任务不存在。')
    return await call(routines.save,routine_id=routine_id,**payload.model_dump())


@router.post('/routines/{routine_id}/enabled')
async def enable_routine(routine_id:str,payload:Toggle):
    return await call(routines.set_enabled,routine_id,payload.enabled)


@router.post('/batches/{batch_id}/control')
async def control_batch(batch_id:str,payload:BatchAction):
    return await call(batches.control,batch_id,**payload.model_dump())


@router.put('/projects/{project_id}/browser')
async def configure_browser(project_id:str,payload:BrowserConfig):
    await browser.close_project(project_id)
    return await call(browser.config,project_id,**payload.model_dump())


@router.post('/projects/{project_id}/browser/login')
async def browser_login(project_id:str,payload:BrowserLogin):
    # User explicitly opens a dedicated visible session; model never gets this endpoint.
    await browser.close_project(project_id)
    existing=service.detail(project_id)['tasks']
    if not existing: raise HTTPException(400,'请先在项目创建一个任务，再打开专用浏览器登录。')
    context=SimpleNamespace(task_id=existing[0]['id'],conversation_id=existing[0]['conversation_id'])
    result=await call(browser.open_page,payload.url,context,visible=True)
    browser.require_page(result['page_id'])['manual']=True
    return result


@router.post('/browser/{pending_id}/confirm')
async def confirm_browser(pending_id:str,payload:Approval):
    return await call(browser.confirm,pending_id,payload.approve)
