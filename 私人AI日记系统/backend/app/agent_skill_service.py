"""Versioned task guidance. Skills never grant permissions or execute scripts."""
import hashlib
from pathlib import Path
import uuid

from . import db, maintenance_service
from .repositories import agent_skills_repository as repository

from .agent_skill_defaults import DEFAULTS


def initialize():
    """Explicit compatibility bootstrap; ordinary service calls never migrate."""
    db.init_db()


def listing():
    return [{**dict(row), 'source_status': source_status(row)} for row in repository.listing()]


def source_status(skill):
    if not skill['source_path']:
        return 'local'
    from .agent_file_service import resolve_read
    try:
        source=resolve_read(skill['source_path'])
        return 'current' if hashlib.sha256(source.read_bytes()).hexdigest()==skill['source_hash'] else 'stale'
    except (ValueError,OSError):
        return 'unavailable'


def read(skill_id, *, revision=0, offset=0, max_chars=12000, active_only=False):
    offset,max_chars=max(0,int(offset)),max(1,min(20000,int(max_chars)))
    skill=repository.get(skill_id)
    if not skill or active_only and not skill['enabled']:
        raise ValueError('技能不存在或已停用。')
    version=int(revision) or skill['revision']
    if active_only and version != skill['revision']:
        raise ValueError('技能版本已变化，请重新读取当前版本。')
    row=repository.version(skill_id,version)
    if not row: raise ValueError('技能版本不存在。')
    status=source_status(skill)
    if active_only and status not in {'local','current'}:
        raise ValueError('导入技能的来源已变化或授权已撤销，请在技能页核对并更新。')
    content=row['content']
    return {**dict(skill),'revision':version,'current_revision':skill['revision'],'content_hash':row['content_hash'],
            'source_status':status,'content':content[offset:offset+max_chars],'offset':offset,
            'next_offset':min(len(content),offset+max_chars),'has_more':offset+max_chars<len(content),
            'guidance_only':True,'instruction':'技能是可调整指导，不覆盖用户当前要求，不授予文件/账号/外发权限，也不自动执行代码。'}


@maintenance_service.mutation_scope()
def save(name,description,content,*,skill_id='',expected_revision=0,enabled=True,source_path='',source_hash=''):
    if not name.strip() or len(name)>100 or len(description)>500 or not content.strip() or len(content)>100000:
        raise ValueError('技能需要名称、简介和正文；正文最多 10 万字符。')
    identifier=skill_id or 'skill_'+uuid.uuid4().hex[:24]
    repository.save(identifier,name.strip(),description.strip(),content,expected_revision,enabled,source_path,source_hash)
    return read(identifier,max_chars=20000)


@maintenance_service.mutation_scope()
def enabled(skill_id,enabled,expected_revision):
    repository.set_enabled(skill_id,enabled,expected_revision)
    return read(skill_id)


def import_file(path,*,skill_id='',expected_revision=0):
    from .agent_file_service import resolve_read
    source=resolve_read(path)
    if source.suffix.lower() not in {'.md','.txt'} or source.stat().st_size>400000:
        raise ValueError('请导入已授权目录中不超过 400 KB 的 Markdown 或文本技能。')
    raw=source.read_bytes(); content=raw.decode('utf-8-sig')
    title=next((line.lstrip('# ').strip() for line in content.splitlines() if line.startswith('# ')),source.stem)
    return save(title,'从本机授权文件导入；使用前核对来源版本',content,skill_id=skill_id,expected_revision=expected_revision,
                source_path=str(source),source_hash=hashlib.sha256(raw).hexdigest())


def _drafts_initialize():
    initialize()


@maintenance_service.mutation_scope()
def save_draft(name, description, content, *, skill_id='', expected_revision=0):
    if not name.strip() or len(name)>100 or len(description)>500 or not content.strip() or len(content)>100000:
        raise ValueError('技能需要名称、简介和正文；正文最多 10 万字符。')
    if skill_id:
        if read(skill_id)['current_revision'] != expected_revision:
            raise ValueError('技能已变化，请刷新后保存草稿。')
    elif expected_revision:
        raise ValueError('新技能的预期版本必须为 0。')
    identifier = 'draft_'+uuid.uuid4().hex[:24]
    repository.save_draft(identifier,skill_id,expected_revision,name.strip(),description.strip(),content)
    return read_draft(identifier)


def read_draft(draft_id):
    row=repository.draft(draft_id)
    if not row: raise ValueError('技能草稿不存在。')
    return {**dict(row),'guidance_only':True,'validation':{'valid':True,'errors':[]},
            'instruction':'技能只是指导，不授予权限；激活后在下一轮使用。'}


@maintenance_service.mutation_scope()
def activate_draft(draft_id):
    return read(repository.activate_draft(draft_id))


@maintenance_service.mutation_scope()
def rollback(skill_id, revision, expected_revision):
    previous=read(skill_id,revision=revision,max_chars=20000)
    # Read the immutable full version; the public read API intentionally pages.
    row=repository.version(skill_id,revision)
    return save(previous['name'],previous['description'],row['content'],skill_id=skill_id,
                expected_revision=expected_revision,enabled=bool(previous['enabled']))
