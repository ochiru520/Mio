"""Isolated phone document storage, plus QQ transport intake."""
from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit, urljoin
import uuid

import httpx

from .repositories import mobile_file_service_repository as repository
from . import db, agent_file_service as files
from .config import settings
from . import mobile_task_service as mobile

MAX_BYTES = 32 * 1024 * 1024
DOCUMENTS = files.TEXT | {'.docx', '.pdf'}
_intake_locks: dict[tuple[str, str], asyncio.Lock] = {}


def normalize_event(event: dict) -> dict:
    if event.get('post_type') == 'notice' and event.get('notice_type') == 'offline_file' and not event.get('group_id'):
        item = dict(event.get('file') or {})
        return {**event, 'post_type': 'message', 'message_type': 'private',
                'message_id': 'file:' + str(item.get('id') or item.get('file_id') or ''),
                'message': [{'type': 'file', 'data': item}]}
    return event


def segments(event: dict) -> list[dict]:
    message = event.get('message')
    if isinstance(message, list):
        return [item['data'] for item in message if isinstance(item, dict) and item.get('type') == 'file' and isinstance(item.get('data'), dict)]
    if isinstance(message, str):
        # Some OneBot adapters use CQ strings. Decode escapes with the existing parser.
        from .onebot_protocol import parse_cq_params
        return [parse_cq_params(match[1]) for match in re.finditer(r'\[CQ:file,([^\]]+)\]', message)]
    return []


def clean_name(value: str) -> str:
    name = value.strip()
    if (not name or len(name) > 160 or name.startswith('.') or name.endswith(('.', ' '))
            or re.search(r'[\\/:*?"<>|\x00-\x1f]', name)
            or re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM\d|LPT\d)(?:\..*)?', name, re.I)):
        raise ValueError('附件文件名无效，请改成普通文档文件名后重发。')
    if Path(name).suffix.lower() not in DOCUMENTS:
        raise ValueError('任务附件支持 TXT、Markdown、JSON、CSV、YAML、DOCX 和 PDF；图片请作为图片消息发送。')
    return name


async def download(url: str) -> bytes:
    from .web_search_service import _validate_public_url, _read_limited_response
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
        for _ in range(4):
            host = (urlsplit(url).hostname or '').lower().rstrip('.')
            if not any(host == suffix or host.endswith('.' + suffix) for suffix in ('qq.com', 'qq.com.cn', 'qpic.cn', 'gtimg.com')):
                raise ValueError('QQ 返回了非受信任的附件地址，未下载；请重新发送原始文件。')
            await _validate_public_url(url)
            async with client.stream('GET', url) as response:
                if response.is_redirect:
                    location = response.headers.get('location')
                    if not location:
                        raise ValueError('附件下载重定向无效。')
                    url = urljoin(url, location)
                    continue
                response.raise_for_status()
                payload = await _read_limited_response(response, MAX_BYTES)
                if not payload:
                    raise ValueError('附件为空，请检查后重发。')
                return payload
    raise ValueError('附件下载重定向过多。')


def root() -> Path:
    path = settings.data_dir.resolve() / '手机任务附件'
    path.mkdir(parents=True, exist_ok=True)
    if path.resolve() != path:
        raise ValueError('手机附件目录不能指向其他目录。')
    return path


def attachment_authorized(path: Path, task_id: str) -> bool:
    if not task_id or not db.settings.db_path.is_file():
        return False
    with repository.read() as conn:
        if not conn.attachment_authorized_find_sqlite_master():
            return False
        row = conn.attachment_authorized_find_mobile_task_attachments((task_id, str(path)))
    if not row or not mobile.valid_origin(dict(row)):
        return False
    directory = root()
    if not path.is_relative_to(directory) or not path.is_file() or path.stat().st_size != row['size'] or row['size'] > MAX_BYTES:
        return False
    return hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']


async def receive(event: dict, origin: dict, event_key: str) -> bool:
    items = segments(event)
    if not items:
        return False
    from .onebot_transport_service import request_task_api
    mobile.initialize()
    accepted, errors = [], []
    async with _intake_locks.setdefault((origin['self_id'], origin['user_id']), asyncio.Lock()):
        for item in items[:8]:
            try:
                name = clean_name(str(item.get('name') or item.get('file') or ''))
                file_id = str(item.get('file_id') or item.get('id') or '')
                if not file_id or len(file_id) > 512 or re.search(r'[\x00-\x20:/\\]', file_id):
                    raise ValueError('附件标识无效，请直接发送原始文件。')
                size = int(item.get('file_size') or item.get('size') or 0)
                if size < 0 or size > MAX_BYTES:
                    raise ValueError('单个附件不能超过 32MB。')
                with repository.read() as conn:
                    previous = conn.receive_find_mobile_task_attachments((origin['self_id'], origin['user_id'], file_id))
                    pending = conn.receive_find_mobile_task_attachments_2((origin['self_id'], origin['user_id']))[0]
                if previous:
                    accepted.append(previous['name'])
                    continue
                if pending >= 8:
                    raise ValueError('待处理附件已达 8 个，请先创建任务或发送“/任务 清空附件”。')
                # Never fetch an event-supplied URL or read a supplied local path.
                response = await request_task_api(origin, 'get_private_file_url', {'file_id': file_id})
                url = str((response.get('data') or {}).get('url') or '')
                payload = await download(url)
                digest = hashlib.sha256(payload).hexdigest()
                identifier = 'qqfile_' + uuid.uuid4().hex
                directory = root() / identifier
                directory.mkdir()
                path = directory / name
                try:
                    path.write_bytes(payload)
                    # Parse once now so corrupt/oversized archives fail before reaching the model.
                    await asyncio.to_thread(files._read_source, path, max_chars=300)
                    with repository.transaction() as conn:
                        conn.receive_insert_mobile_task_attachments((identifier, origin['self_id'], origin['user_id'], file_id, name, str(path), len(payload), digest, '', db.now_iso()))
                except BaseException:
                    path.unlink(missing_ok=True)
                    directory.rmdir()
                    raise
                accepted.append(name)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # QQ file URLs may contain temporary credentials; never echo transport exceptions.
                errors.append(str(exc) if isinstance(exc, ValueError) else '附件接收失败，请检查 QQ 连接并重新发送原始文件。')
    reply = ('已收到附件：' + '、'.join(accepted) + '。\n请告诉我处理要求；补充到已有任务可发送“/任务 补充 要求”。') if accepted else ''
    if errors:
        reply += '\n' + '\n'.join(errors)
    mobile.queue(origin, reply.strip(), key=event_key + ':attachments')
    return True


async def store_document(origin: dict, file_id: str, name: str, payload: bytes) -> str:
    """Store verified bytes from a channel's authenticated download path."""
    mobile.initialize()
    if not mobile.valid_origin(origin):
        raise ValueError('附件来源未授权。')
    name = clean_name(name)
    if not payload or len(payload) > MAX_BYTES:
        raise ValueError('附件为空或超过 32MB。')
    async with _intake_locks.setdefault((origin['self_id'], origin['user_id']), asyncio.Lock()):
        with repository.read() as conn:
            previous = conn.store_document_find_mobile_task_attachments((origin['self_id'], origin['user_id'], file_id))
            if previous:
                return previous['id']
            count = conn.store_document_find_mobile_task_attachments_2((origin['self_id'], origin['user_id']))[0]
        if count >= 8:
            raise ValueError('待处理附件已达 8 个，请先创建任务或发送“/任务 清空附件”。')
        identifier = 'phonefile_' + uuid.uuid4().hex
        directory = root() / identifier
        directory.mkdir()
        path = directory / name
        try:
            path.write_bytes(payload)
            await asyncio.to_thread(files._read_source, path, max_chars=300)
            with repository.transaction() as conn:
                conn.store_document_insert_mobile_task_attachments((identifier, origin['self_id'], origin['user_id'], file_id, name, str(path), len(payload),
                     hashlib.sha256(payload).hexdigest(), '', db.now_iso()))
        except BaseException:
            path.unlink(missing_ok=True)
            directory.rmdir()
            raise
        return identifier
