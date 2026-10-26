"""Sidebar state and forks. Forks own their attachment files independently."""
import json
import re
import uuid
from pathlib import Path
from urllib.parse import unquote
from .repositories import conversation_state_service_repository as repository
from . import db, workspace_service as workspace


def initialize():
    workspace.initialize()
    with repository.transaction() as conn:
        conn.initialize_create_conversation_sidebar_state()
        conn.initialize_rewinds()


def states():
    initialize()
    with repository.read() as conn:
        return {r['conversation_id']: {**dict(r), **{k:bool(r[k]) for k in ('pinned','unread','archived')}}
                for r in conn.states_find_conversation_sidebar_state()}


def require(conversation_id):
    if not conversation_id.startswith('desktop_agent_') or db.get_agent_conversation(conversation_id) is None:
        raise ValueError('Agent 对话不存在。')
    db.assert_conversation_writable(conversation_id)


def update(conversation_id, **values):
    initialize(); require(conversation_id)
    with repository.transaction() as conn:
        conn.update_insert_conversation_sidebar_state((conversation_id,))
        for key,value in values.items():
            if key not in {'pinned','unread','archived'}: raise ValueError('无效侧栏状态。')
            conn.update_f_conversation_sidebar_state(key, (int(value),conversation_id))
    return states()[conversation_id]


def markdown(conversation_id):
    require(conversation_id)
    with repository.read() as conn:
        rows=conn.markdown_find_messages((conversation_id,))
    return '\n\n'.join(('### 你' if r['role']=='user' else '### Mio')+'\n\n'+r['content'] for r in rows)


def fork(conversation_id):
    initialize(); require(conversation_id)
    from .config import settings
    from .conversation_service import archive_attachment
    original = db.get_agent_conversation(conversation_id)
    target='desktop_agent_'+uuid.uuid4().hex
    copies=[]
    with repository.read() as conn:
        rows=conn.fork_find_messages((conversation_id,))
    try:
        db.create_agent_conversation(target,(original['title'][:54]+' · 分支')[:60])
        for row in rows:
            attachments=[]
            for item in json.loads(row['attachments_json'] or '[]'):
                if item.get('kind') == 'reference':
                    # Keep the selected source/version. Replay resolves it again
                    # under the branch's project and current file permissions.
                    attachments.append(dict(item))
                    continue
                if item.get('kind') not in {'image','video','audio','document','text','file'}: continue
                value=dict(item); url=value.get('url','')
                if url.startswith('/agent-files/'):
                    relative=Path(unquote(url[len('/agent-files/'):]))
                    source=(settings.agent_attachment_dir/relative).resolve(strict=True)
                    if not source.is_relative_to(settings.agent_attachment_dir.resolve()): raise ValueError('附件路径无效。')
                    value=archive_attachment(value.get('name') or source.name,source.read_bytes(),value.get('mime_type','application/octet-stream'),value['kind'])
                    copies.append(settings.agent_attachment_dir/Path(value['url'][len('/agent-files/'):]))
                attachments.append(value)
            db.save_message(row['role'],row['content'],source='agent_branch',conversation_id=target,
                            attachments_json=json.dumps(attachments,ensure_ascii=False),model_id=row['model_id'],provider_model=row['provider_model'])
        project=workspace.project_for_conversation(conversation_id)
        if project: workspace.bind_conversation(project['id'],target)
        mode=workspace.conversation_index()['modes'].get(conversation_id,'work')
        workspace.save_conversation_mode(target,mode)
        with repository.transaction() as conn:
            conn.fork_insert_conversation_sidebar_state((target,conversation_id))
        return {**dict(db.get_agent_conversation(target)), 'kind':'agent','parent_id':conversation_id}
    except Exception:
        db.delete_agent_conversation(target)
        for path in copies: path.unlink(missing_ok=True)
        raise


def rewind(conversation_id, anchor_message_id, mode='regenerate', content='', expected_latest_message_id=None):
    """Archive an obsolete tail and atomically make the visible/model history agree."""
    validate_rewind(conversation_id, anchor_message_id, mode, content, expected_latest_message_id)

    with repository.transaction() as conn:
        user, removed, revision = conn.rewind(conversation_id, int(anchor_message_id), mode,
            str(content).strip()[:200000], expected_latest_message_id, db.now_iso(), uuid.uuid4().hex)
    from .conversation_service import public_message
    return {'conversation_id': conversation_id, 'revision': revision, 'user_message': public_message(user),
            'removed_message_ids': removed, 'external_actions_preserved': True,
            'notice': '有效历史已回退；已执行的文件、网络或其他外部动作仍保留。'}


def validate_rewind(conversation_id, anchor_message_id, mode='regenerate', content='', expected_latest_message_id=None):
    """Check before cancelling anything; the transaction repeats these checks at commit."""
    initialize()
    if not conversation_id.startswith('desktop_') or db.get_agent_conversation(conversation_id) is None:
        raise ValueError('只能编辑桌面对话历史。')
    db.assert_conversation_writable(conversation_id)
    if mode not in {'edit', 'regenerate'}:
        raise ValueError('无效回退方式。')
    if mode == 'edit' and not str(content).strip():
        raise ValueError('编辑内容不能为空。')
    anchor = db.get_message_by_id(int(anchor_message_id))
    if anchor is None or anchor['conversation_id'] != conversation_id or anchor['role'] != 'user':
        raise ValueError('只能从当前有效的用户消息重新回答。')
    if expected_latest_message_id is not None and db.get_latest_message_id(conversation_id=conversation_id) != expected_latest_message_id:
        raise ValueError('对话已更新，请刷新后再编辑。')
    summary = db.get_latest_memory('conversation_summary', tags=conversation_id)
    if summary:
        marker = re.search(r'<!--\s*last_message_id:(\d+)\s*-->', summary['content'])
        if marker is None or int(anchor_message_id) <= int(marker[1]):
            raise ValueError('该消息已进入压缩摘要，请先清除会话摘要或创建分支后编辑。')


def claim_replay(conversation_id: str, message_id: int, request_id: str):
    """Call inside the normal conversation coordinator instead of saving a second user row."""
    initialize()
    db.assert_conversation_writable(conversation_id)
    if not request_id:
        raise ValueError('重新回答需要请求标识。')
    with repository.transaction() as conn:
        return conn.claim_replay(conversation_id, int(message_id), request_id)


def capture_source(conversation_id: str):
    initialize()
    with repository.read() as conn:
        return {'conversation_id': conversation_id, 'revision': conn.revision(conversation_id)}


def source_is_current(snapshot):
    return capture_source(snapshot['conversation_id']) == snapshot


def turn_index(conversation_id, before_id=0, limit=1000):
    from .conversation_service import resolve_conversation_id
    identifier = resolve_conversation_id(conversation_id)
    limit = max(1, min(int(limit), 1000))
    with repository.read() as conn:
        rows = conn.turn_index(identifier, int(before_id), limit + 1)
    page = rows[:limit]
    return {'turns': [dict(row) for row in reversed(page)], 'has_more': len(rows) > limit,
            'next_before_id': int(page[-1]['id']) if page and len(rows) > limit else 0}


def read_replay_attachments(conversation_id, message_id):
    """Reload only this user's archived attachments; never accept arbitrary file URLs."""
    import base64
    from .config import settings
    from .chat_service import TextAttachment
    from .image_service import image_attachment_from_data_url
    from .document_service import parse_document_data_url
    from . import reference_service
    db.assert_conversation_writable(conversation_id)
    with repository.read() as conn:
        row = conn.replay_message(conversation_id, int(message_id))
    if row is None:
        raise ValueError('重新回答的附件来源已失效。')
    metadata = json.loads(row['attachments_json'] or '[]')
    images, texts, explicit = [], [], []
    root = settings.agent_attachment_dir.resolve()
    for item in metadata:
        if item.get('kind') == 'reference':
            explicit.append({'kind': item.get('reference_kind'), 'target': item.get('target', ''), 'version': item.get('version', '')})
            continue
        kind = item.get('kind')
        if kind not in {'image', 'document', 'text', 'file', 'video', 'audio'}:
            continue
        url = str(item.get('url', ''))
        if not url.startswith('/agent-files/'):
            raise ValueError('历史附件来源不受支持，请重新添加附件。')
        path = (root / unquote(url[len('/agent-files/'):])).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('历史附件路径已失效。')
        if path.stat().st_size > settings.agent_document_attachment_max_bytes:
            raise ValueError('历史附件超过当前大小限制，请重新添加。')
        raw = path.read_bytes()
        mime = item.get('mime_type') or 'application/octet-stream'
        name = Path(str(item.get('name') or path.name)).name
        data_url = 'data:' + mime + ';base64,' + base64.b64encode(raw).decode('ascii')
        if kind == 'image':
            images.append(image_attachment_from_data_url(data_url, source=name))
        elif kind == 'text':
            content = raw.decode('utf-8-sig')
            if len(content) > settings.agent_text_attachment_max_chars:
                raise ValueError('历史文本附件超过当前长度限制。')
            texts.append(TextAttachment(name=name, content=content, mime_type=mime))
        elif kind == 'document':
            parsed = parse_document_data_url(data_url, name)
            if len(parsed.text_attachment.content) > settings.agent_text_attachment_max_chars:
                raise ValueError('历史文档附件超过当前长度限制。')
            images.extend(parsed.images)
            texts.append(parsed.text_attachment)
        else:
            texts.append(TextAttachment(name=name, content=f'[附件信息] 文件名：{name}；类型：{mime}；大小：{len(raw)}字节。原始附件已保留，但未读取正文。'))
    if explicit:
        project = workspace.project_for_conversation(conversation_id)
        result = reference_service.resolve(explicit, project_id=project['id'] if project else '')
        if result['blocked']:
            raise ValueError('历史引用不可用：' + '；'.join(item['reason'] for item in result['blocked']))
        if result['context']:
            texts.append(TextAttachment(name='显式引用', content=result['context']))
    return {'message': str(row['content']), 'images': images, 'text_attachments': texts, 'metadata': metadata}
