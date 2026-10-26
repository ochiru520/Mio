from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .repositories import creation_repository as repository

from . import db
from .config import settings


def use_reference(arguments: dict, context) -> dict:
    from .creation_service import get_asset, get_job, save_asset, save_job_output_as_asset
    job_id = str(arguments.get('job_id') or '')
    message_id = int(arguments.get('message_id') or 0)
    index = int(arguments.get('index') or 0)
    if job_id and message_id:
        raise ValueError('请在消息图片和任务输出之间选择一个来源。')
    if job_id:
        job = get_job(job_id)
        if not job:
            raise ValueError('找不到参考图片任务。')
        if job.get('conversation_id') != context.conversation_id:
            from .workspace_service import project_for_conversation
            current = project_for_conversation(context.conversation_id)
            owner = project_for_conversation(job.get('conversation_id',''))
            if not current or not owner or current['id'] != owner['id']:
                raise ValueError('参考图片任务不属于当前会话或项目。')
        asset = save_job_output_as_asset(job_id, index)
    else:
        message_id = message_id or context.source_message_id
        with repository.transaction() as conn:
            row = repository.message_attachment(conn, (message_id, context.conversation_id)).fetchone()
        if row is None:
            raise ValueError('没有找到当前会话的参考图片消息。')
        attachments = [x for x in json.loads(row['attachments_json'] or '[]') if x.get('kind') == 'image']
        if index < 0 or index >= len(attachments):
            raise ValueError('这条消息没有所选图片；请先上传图片或选择已完成的图片任务。')
        attachment = attachments[index]
        url = urlsplit(str(attachment.get('url') or ''))
        prefix = '/agent-files/'
        if url.scheme or url.netloc or not url.path.startswith(prefix):
            raise ValueError('图片不是本机保存的会话附件。')
        relative = Path(unquote(url.path[len(prefix):]))
        root = settings.agent_attachment_dir.resolve()
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('参考图片文件不存在或超出附件目录。')
        if path.stat().st_size > settings.creation_max_asset_bytes:
            raise ValueError('参考图片超过允许的大小。')
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        with repository.transaction() as conn:
            existing = repository.asset_id_by_hash(conn, (digest,)).fetchone()
        asset = get_asset(existing['id']) if existing else None
        if not asset:
            mime = str(attachment.get('mime_type') or 'image/png')
            asset = save_asset(str(attachment.get('name') or path.name), mime,
                               f'data:{mime};base64,' + base64.b64encode(content).decode('ascii'))
    return {'asset': {key: value for key, value in asset.items() if key not in {'path', 'sha256'}},
            'message': '参考图片已登记，可将 asset.id 传给生图或视频工具。'}
