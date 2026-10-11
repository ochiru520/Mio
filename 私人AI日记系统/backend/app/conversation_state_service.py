"""Sidebar state and forks. Forks own their attachment files independently."""
import json
import uuid
from contextlib import closing
from pathlib import Path
from urllib.parse import unquote
from . import db, workspace_service as workspace


def initialize():
    workspace.initialize()
    with closing(db.get_conn()) as conn, conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS conversation_sidebar_state(
          conversation_id TEXT PRIMARY KEY,pinned INTEGER NOT NULL DEFAULT 0,
          unread INTEGER NOT NULL DEFAULT 0,archived INTEGER NOT NULL DEFAULT 0,
          parent_id TEXT NOT NULL DEFAULT '')''')


def states():
    initialize()
    with closing(db.get_conn()) as conn:
        return {r['conversation_id']: {**dict(r), **{k:bool(r[k]) for k in ('pinned','unread','archived')}}
                for r in conn.execute('SELECT * FROM conversation_sidebar_state')}


def require(conversation_id):
    if not conversation_id.startswith('desktop_agent_') or db.get_agent_conversation(conversation_id) is None:
        raise ValueError('Agent 对话不存在。')
    db.assert_conversation_writable(conversation_id)


def update(conversation_id, **values):
    initialize(); require(conversation_id)
    with closing(db.get_conn()) as conn, conn:
        conn.execute('INSERT OR IGNORE INTO conversation_sidebar_state(conversation_id) VALUES(?)',(conversation_id,))
        for key,value in values.items():
            if key not in {'pinned','unread','archived'}: raise ValueError('无效侧栏状态。')
            conn.execute(f'UPDATE conversation_sidebar_state SET {key}=? WHERE conversation_id=?',(int(value),conversation_id))
    return states()[conversation_id]


def markdown(conversation_id):
    require(conversation_id)
    with closing(db.get_conn()) as conn:
        rows=conn.execute('SELECT role,content FROM messages WHERE conversation_id=? ORDER BY id',(conversation_id,)).fetchall()
    return '\n\n'.join(('### 你' if r['role']=='user' else '### Mio')+'\n\n'+r['content'] for r in rows)


def fork(conversation_id):
    initialize(); require(conversation_id)
    from .config import settings
    from .conversation_service import archive_attachment
    original = db.get_agent_conversation(conversation_id)
    target='desktop_agent_'+uuid.uuid4().hex
    copies=[]
    with closing(db.get_conn()) as conn:
        rows=conn.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY id',(conversation_id,)).fetchall()
    try:
        db.create_agent_conversation(target,(original['title'][:54]+' · 分支')[:60])
        for row in rows:
            attachments=[]
            for item in json.loads(row['attachments_json'] or '[]'):
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
        with closing(db.get_conn()) as conn, conn:
            conn.execute('INSERT INTO conversation_sidebar_state(conversation_id,parent_id) VALUES(?,?)',(target,conversation_id))
        return {**dict(db.get_agent_conversation(target)), 'kind':'agent','parent_id':conversation_id}
    except Exception:
        db.delete_agent_conversation(target)
        for path in copies: path.unlink(missing_ok=True)
        raise
