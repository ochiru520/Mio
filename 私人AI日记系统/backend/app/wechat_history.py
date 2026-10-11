"""Local WeChat transcript, including transport receipts and old conversations."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import re

from . import db


ID_PATTERN = re.compile(r'wechat_private_[a-f0-9]{24}')


def conversations() -> list[dict]:
    from . import wechat_service, mobile_task_service
    with closing(db.get_conn()) as conn:
        ids = [row[0] for row in conn.execute("SELECT DISTINCT conversation_id FROM messages WHERE conversation_id LIKE 'wechat_private_%'")
               if ID_PATTERN.fullmatch(row[0])]
    current = ''
    try:
        config = wechat_service.config()
        if config.get('bot_id') and config.get('user_id'):
            current = mobile_task_service.conversation_id(wechat_service.origin_for(config))
    except (ValueError, OSError, RuntimeError):
        pass
    if current and current not in ids:
        ids.append(current)
    result = []
    for identifier in ids:
        last = db.get_last_message(identifier)
        result.append({'id': identifier, 'kind': 'wechat',
                       'title': '微信对话' if identifier == current else '微信历史对话',
                       'preview': str(last['content'] or '') if last else '与微信共享聊天记录',
                       'updated_at': str(last['created_at'] or '') if last else '',
                       'bound': identifier == current})
    result.sort(key=lambda x: (x['bound'], x['updated_at']), reverse=True)
    return result


def archive_outbox(conn, row) -> None:
    from .mobile_task_service import conversation_id
    if not str(row['self_id']).startswith('wechat:'):
        return
    payload = json.loads(row['payload_json'])
    # Ordinary model replies already exist in messages before transport begins.
    if payload.get('record_history') is False or str(row['dedupe_key']).endswith(':reply'):
        return
    attachments = []
    content = str(payload.get('text') or '')
    if payload.get('artifact_id'):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='artifacts'").fetchone():
            return
        artifact = conn.execute('SELECT * FROM artifacts WHERE id=?', (payload['artifact_id'],)).fetchone()
        if not artifact:
            return
        kind = 'image' if artifact['mime_type'].startswith('image/') else 'video' if artifact['mime_type'].startswith('video/') else 'file'
        attachments = [{'kind': kind, 'name': artifact['name'], 'mime_type': artifact['mime_type'],
                        'size': artifact['size'], 'url': f"/api/artifacts/{artifact['id']}/download"}]
        content = '任务成果：' + artifact['name']
    if not content:
        return
    key = f'wechat-outbox:{row["id"]}'
    conn.execute('''INSERT OR IGNORE INTO messages
        (role,content,source,conversation_id,created_at,request_id,delivery_key,attachments_json)
        VALUES('assistant',?,'wechat',?,?,?,?,?)''',
        (content, conversation_id(dict(row)), row['created_at'], key, key, json.dumps(attachments, ensure_ascii=False)))


def archive_document(conn, row) -> None:
    from .mobile_task_service import conversation_id
    if not str(row['self_id']).startswith('wechat:'):
        return
    attachment = {'kind': 'document', 'name': row['name'], 'size': row['size'],
                  'url': f"/api/wechat/attachments/{row['id']}"}
    key = 'wechat-file:' + row['id']
    conn.execute('''INSERT OR IGNORE INTO messages
        (role,content,source,conversation_id,created_at,request_id,delivery_key,attachments_json)
        VALUES('user',?,'wechat',?,?,?,?,?)''',
        ('微信文件：' + row['name'], conversation_id(dict(row)), row['created_at'], key, key,
         json.dumps([attachment], ensure_ascii=False)))


def backfill() -> None:
    """Recover display entries from durable records; never resend messages."""
    with closing(db.get_conn()) as conn, conn:
        for table, callback in [('mobile_task_outbox', archive_outbox), ('mobile_task_attachments', archive_document)]:
            if conn.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
                for row in conn.execute(f"SELECT * FROM {table} WHERE self_id LIKE 'wechat:%' ORDER BY created_at").fetchall():
                    callback(conn, row)


def inbound(origin: dict, text: str, event_key: str, attachments=None) -> int:
    from .mobile_task_service import conversation_id
    return db.save_message('user', text, source='wechat', conversation_id=conversation_id(origin),
                           request_id=event_key, delivery_key='wechat-in:' + hashlib.sha256(event_key.encode()).hexdigest(),
                           attachments_json=json.dumps(attachments or [], ensure_ascii=False))


def add_delivery_states(messages: list[dict]) -> None:
    # Public conversation queries intentionally omit the internal delivery key.
    # Resolve it through persisted message IDs instead of relying on SELECT *.
    entries = {int(x['id']): x for x in messages if str(x.get('id') or '').isdigit()}
    if not entries:
        return
    with closing(db.get_conn()) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='mobile_task_outbox'").fetchone():
            return
        for row in conn.execute('''SELECT m.id,o.status,o.error FROM messages m
            JOIN mobile_task_outbox o ON m.delivery_key = 'wechat-outbox:' || o.id
            WHERE m.id IN (''' + ','.join('?' for _ in entries) + ')', list(entries)):
            message = entries[int(row['id'])]
            message['delivery_status'] = row['status']
            message['delivery_error'] = row['error']
