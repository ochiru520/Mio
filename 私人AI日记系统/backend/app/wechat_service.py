"""Personal WeChat transport using Tencent's documented iLink Bot protocol.

Protocol reference: Tencent/openclaw-weixin/docs/protocol_zh_CN.md (2.4.8).
Only the scanning owner can interact. Credentials and message context are DPAPI
protected at rest; public status never returns them. No external gateway required.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import secrets
import time
from urllib.parse import urlencode, urlsplit
import uuid

import httpx
import qrcode
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from .repositories import wechat_service_repository as repository
from . import db, agent_task_service as tasks, maintenance_service
from .config import settings
from .secret_store import protect_secret, unprotect_secret
from .model_runtime import operation, OperationStopped

API = 'https://ilinkai.weixin.qq.com'
CDN = 'https://novac2c.cdn.weixin.qq.com/c2c'
VERSION = '2.4.8'
MAX_BYTES = 32 * 1024 * 1024
_login: dict = {}
_state: dict = {'connected': False, 'message': '尚未连接微信'}
_inflight: set[asyncio.Task] = set()
_stopping: set[asyncio.Task] = set()


class ApiRejected(ValueError):
    pass


class ConnectionStopped(RuntimeError):
    """A local transport pause; the long-lived scheduler must survive it."""


def initialize() -> None:
    with repository.transaction() as conn:
        conn.initialize_initialize_wechat_state()


def config() -> dict:
    initialize()
    with repository.read() as conn:
        row = conn.config_find_wechat_state()
    return json.loads(unprotect_secret(row[0])) if row else {}


def save_config(value: dict) -> None:
    initialize()
    encrypted = protect_secret(json.dumps(value, ensure_ascii=False))
    with repository.transaction() as conn:
        conn.save_config_insert_wechat_state((encrypted,))


def origin_for(value: dict) -> dict:
    return {'self_id': 'wechat:' + value.get('bot_id', ''), 'user_id': value.get('user_id', '')}


def authorized(origin: dict) -> bool:
    value = config()
    return bool(value.get('token') and value.get('bot_id') and value.get('user_id')
                and origin_for(value) == {key: origin.get(key) for key in ('self_id', 'user_id')})


def connected(origin: dict) -> bool:
    value = config()
    return bool(value.get('enabled') and authorized(origin) and _state.get('bot_id') == value.get('bot_id')
                and _state.get('connected') and time.time() - _state.get('last_poll', 0) < 90)


def public_status() -> dict:
    try:
        value = config()
    except (ValueError, OSError, RuntimeError):
        return {'configured': False, 'enabled': False, 'connected': False, 'message': '微信凭据无法解密，请重新扫码连接。'}
    paused = tasks.automatic_work_paused() or maintenance_service.status().get('blocked', False)
    is_connected = connected(origin_for(value)) if value else False
    return {'configured': bool(value.get('token')), 'enabled': bool(value.get('enabled')),
            'connected': is_connected and not paused, 'paused': bool(paused),
            'message': '隐私总控或维护已暂停微信' if paused else _state['message'],
            'owner_bound': bool(value.get('user_id')), 'protocol_version': VERSION}


def trusted_url(url: str, *, cdn: bool = False) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    suffix = '.cdn.weixin.qq.com' if cdn else '.weixin.qq.com'
    if (parsed.scheme != 'https' or not host.endswith(suffix) or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.fragment
            or (not cdn and (parsed.path not in ('', '/') or parsed.query))):
        raise ValueError('微信返回的服务地址无效。')
    return url.rstrip('/') if not cdn else url


def headers(token: str = '', *, post: bool = True) -> dict:
    result = {'iLink-App-Id': 'bot', 'iLink-App-ClientVersion': str((2 << 16) | (4 << 8) | 8)}
    if post:
        result.update({'Content-Type': 'application/json', 'AuthorizationType': 'ilink_bot_token',
                       'X-WECHAT-UIN': base64.b64encode(str(secrets.randbits(32)).encode()).decode()})
        if token:
            result['Authorization'] = 'Bearer ' + token
    return result


async def request(endpoint: str, *, value: dict | None = None, body: dict | None = None,
                  params: dict | None = None, get: bool = False, base: str = API) -> dict:
    base = trusted_url(value.get('base_url', API) if value else base)
    payload = dict(body or {})
    if value:
        payload['base_info'] = {'channel_version': VERSION, 'bot_agent': 'Mio/0.11'}
    async with httpx.AsyncClient(timeout=40 if endpoint in {'getupdates', 'get_qrcode_status'} else 20,
                                 follow_redirects=False, trust_env=False) as client:
        try:
            response = await client.request('GET' if get else 'POST', base + '/ilink/bot/' + endpoint,
                params=params, json=None if get else payload, headers=headers((value or {}).get('token', ''), post=not get))
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Never propagate URLs, query strings, or credentials into logs/UI.
            if exc.response.status_code >= 500:
                raise RuntimeError('微信服务错误，请核对发送结果。') from None
            raise ApiRejected(f'微信接口返回 HTTP {exc.response.status_code}。') from None
        if len(response.content) > 4 * 1024 * 1024:
            raise ApiRejected('微信接口响应过大。')
        data = response.json()
    if not isinstance(data, dict):
        raise ApiRejected('微信接口响应格式无效。')
    codes = [int(data.get(key) or 0) for key in ('ret', 'errcode')]
    if -14 in codes:
        if value:
            current = config()
            if current.get('token') == value.get('token'):
                save_config({**current, 'enabled': False})
        _state.update(connected=False, message='微信登录已失效，请重新扫码。')
        raise ApiRejected('微信登录已失效，请重新扫码。')
    if any(codes):
        raise ApiRejected('微信接口拒绝请求（错误码 ' + '/'.join(map(str, codes)) + '）。')
    return data


async def start_login() -> dict:
    global _login
    if tasks.automatic_work_paused() or maintenance_service.status().get('blocked'):
        raise ValueError('请先恢复隐私总控，再连接微信。')
    response = await request('get_bot_qrcode', params={'bot_type': '3'}, body={'local_token_list': []})
    content = str(response.get('qrcode_img_content') or '')
    if not response.get('qrcode') or not content or len(content) > 20000:
        raise ValueError('微信没有返回有效二维码。')
    if content.startswith('data:image/png;base64,'):
        picture = content
    else:
        buffer = io.BytesIO()
        qrcode.make(content).save(buffer, format='PNG')
        picture = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()
    _login = {'id': uuid.uuid4().hex, 'qrcode': response['qrcode'], 'base': API, 'expires': time.time() + 300}
    return {'login_id': _login['id'], 'qr_image': picture, 'expires_in': 300, 'status': 'wait'}


async def login_status(login_id: str, verify_code: str = '') -> dict:
    login = _login
    if login.get('id') != login_id or time.time() >= login.get('expires', 0):
        return {'status': 'expired'}
    params = {'qrcode': login['qrcode']}
    if verify_code:
        if not verify_code.isdigit() or len(verify_code) > 12:
            raise ValueError('验证码格式无效。')
        params['verify_code'] = verify_code
    response = await request('get_qrcode_status', get=True, base=login['base'], params=params)
    if _login is not login or login.get('id') != login_id:
        return {'status': 'expired'}
    state = str(response.get('status') or 'wait')
    if state == 'scaned_but_redirect':
        login['base'] = trusted_url('https://' + str(response.get('redirect_host') or ''))
        return {'status': 'scaned'}
    if state == 'confirmed':
        if tasks.automatic_work_paused() or maintenance_service.status().get('blocked'):
            raise ValueError('隐私总控或维护已暂停，请恢复后重新扫码。')
        if not all(isinstance(response.get(k), str) and response[k] for k in ('bot_token', 'ilink_bot_id', 'ilink_user_id')):
            raise ValueError('微信授权缺少账号信息，请重新扫码。')
        await stop_requests()
        save_config({'enabled': True, 'token': response['bot_token'], 'bot_id': response['ilink_bot_id'],
                     'user_id': response['ilink_user_id'], 'base_url': trusted_url(response.get('baseurl') or login['base'])})
        _state.update(connected=False, bot_id=response['ilink_bot_id'], message='已绑定，正在连接微信')
        _login.clear()
    return {'status': state}


async def stop_requests() -> None:
    pending = [item for item in _inflight if item is not asyncio.current_task() and not item.done()]
    for item in pending:
        _stopping.add(item)
        item.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def set_enabled(enabled: bool) -> dict:
    value = config()
    if enabled and not value.get('token'):
        raise ValueError('请先扫码绑定微信。')
    save_config({**value, 'enabled': enabled})
    if not enabled:
        await stop_requests()
    _state.update(connected=False, message='正在重新连接微信' if enabled else '微信已暂停')
    return public_status()


async def forget() -> dict:
    _login.clear()
    await set_enabled(False)
    save_config({})
    with repository.transaction() as conn:
        conn.forget_delete_wechat_state()
        conn.forget_delete_wechat_inbox()
    _state.update(connected=False, message='已解除本机微信绑定')
    return public_status()


def _same_session(value: dict) -> bool:
    current = config()
    return bool(current.get('enabled') and current.get('token') == value.get('token')
                and not tasks.automatic_work_paused() and not maintenance_service.status().get('blocked'))


def _accept_message(message: dict, value: dict) -> bool:
    return bool(message.get('from_user_id') == value.get('user_id') and not message.get('group_id')
                and message.get('message_type') == 1 and message.get('message_state', 2) == 2
                and (message.get('message_id') is not None or message.get('client_id'))
                and message.get('to_user_id') in (None, '', value.get('bot_id')))


@operation('chat', automatic=True, persist=False)
async def poll_once(*, conversation_id: str = 'wechat_transport') -> None:
    value = config()
    if not value.get('enabled'):
        return
    bot = value['bot_id']
    with repository.read() as conn:
        cursor = conn.poll_once_find_wechat_state(('cursor:' + bot,))
    process = asyncio.current_task(); _inflight.add(process)
    try:
        response = await request('getupdates', value=value, body={'get_updates_buf': cursor[0] if cursor else ''})
        if not _same_session(value):
            return
        with repository.transaction() as conn:
            for message in response.get('msgs') or []:
                if not isinstance(message, dict) or not _accept_message(message, value):
                    continue
                key = 'wechat-' + hashlib.sha256(json.dumps([bot, message['from_user_id'], message.get('message_id', message.get('client_id'))]).encode()).hexdigest()[:40]
                conn.poll_once_insert_wechat_inbox((key, bot, protect_secret(json.dumps(message, ensure_ascii=False)), db.now_iso()))
                if message.get('context_token'):
                    conn.poll_once_insert_wechat_state_2(('context:' + bot, protect_secret(str(message['context_token']))))
            if response.get('get_updates_buf'):
                conn.poll_once_insert_wechat_state(('cursor:' + bot, str(response['get_updates_buf'])))
        _state.update(connected=True, bot_id=bot, last_poll=time.time(), message='微信已连接，仅接收扫码账号的私聊')
    except asyncio.CancelledError:
        if process in _stopping:
            raise ConnectionStopped('微信连接已停止') from None
        raise
    finally:
        _inflight.discard(process)
        _stopping.discard(process)


def aes_key(value: str) -> bytes:
    key = base64.b64decode(value, validate=True)
    if len(key) == 32:
        key = bytes.fromhex(key.decode('ascii'))
    if len(key) != 16:
        raise ValueError('微信附件加密密钥无效。')
    return key


def crypt(payload: bytes, key: bytes, *, decrypt: bool = False) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    if decrypt:
        worker = cipher.decryptor(); unpad = PKCS7(128).unpadder()
        return unpad.update(worker.update(payload) + worker.finalize()) + unpad.finalize()
    pad = PKCS7(128).padder(); worker = cipher.encryptor()
    return worker.update(pad.update(payload) + pad.finalize()) + worker.finalize()


async def download_media(item: dict) -> bytes:
    from .web_search_service import _validate_public_url, _read_limited_response
    media = item.get('media') or {}
    key = bytes.fromhex(item['aeskey']) if item.get('aeskey') else aes_key(media.get('aes_key', ''))
    if len(key) != 16:
        raise ValueError('微信附件密钥无效。')
    url = media.get('full_url') or CDN + '/download?' + urlencode({'encrypted_query_param': media.get('encrypt_query_param', '')})
    trusted_url(url, cdn=True)
    await _validate_public_url(url)
    async with httpx.AsyncClient(timeout=35, follow_redirects=False, trust_env=False) as client:
        async with client.stream('GET', url) as response:
            response.raise_for_status()
            encrypted = await _read_limited_response(response, MAX_BYTES + 16)
    payload = crypt(encrypted, key, decrypt=True)
    if not payload or len(payload) > MAX_BYTES:
        raise ValueError('微信附件为空或超过 32MB。')
    return payload


async def upload_file(value: dict, path, name: str) -> dict:
    from .web_search_service import _validate_public_url
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
        raise ValueError('回传文件为空或超过 32MB。')
    payload = path.read_bytes(); key = secrets.token_bytes(16); filekey = secrets.token_hex(16)
    encrypted = crypt(payload, key)
    response = await request('getuploadurl', value=value, body={'filekey': filekey, 'media_type': 3,
        'to_user_id': value['user_id'], 'rawsize': len(payload), 'rawfilemd5': hashlib.md5(payload).hexdigest(),
        'filesize': len(encrypted), 'no_need_thumb': True, 'aeskey': key.hex()})
    if not response.get('upload_full_url') and not response.get('upload_param'):
        raise ApiRejected('微信未提供文件上传地址。')
    url = response.get('upload_full_url') or CDN + '/upload?' + urlencode({'encrypted_query_param': response['upload_param'], 'filekey': filekey})
    trusted_url(url, cdn=True); await _validate_public_url(url)
    if not _same_session(value):
        raise ApiRejected('微信连接已改变，未发送文件。')
    async with httpx.AsyncClient(timeout=60, follow_redirects=False, trust_env=False) as client:
        result = await client.post(url, content=encrypted, headers={'Content-Type': 'application/octet-stream'})
    parameter = result.headers.get('x-encrypted-param')
    if result.status_code != 200 or not parameter:
        raise ApiRejected('微信文件上传未取得确认。')
    return {'type': 4, 'file_item': {'file_name': name, 'len': str(len(payload)), 'media': {
        'encrypt_query_param': parameter, 'aes_key': base64.b64encode(key.hex().encode()).decode(), 'encrypt_type': 1}}}


async def deliver(origin: dict, payload: dict, *, client_id: str) -> dict:
    from . import artifact_service
    value = config()
    if not authorized(origin) or not _same_session(value):
        raise ApiRejected('微信收件账号已改变或通道已暂停。')
    with repository.read() as conn:
        context = conn.deliver_find_wechat_state(('context:' + value['bot_id'],))
    if not context:
        raise ApiRejected('请先从微信向澪发一条消息，再重发结果。')
    process = asyncio.current_task(); _inflight.add(process)
    try:
        if payload.get('artifact_id'):
            path, artifact = artifact_service.file(payload['artifact_id'])
            item = await upload_file(value, path, artifact['name'])
        else:
            item = {'type': 1, 'text_item': {'text': payload['text']}}
        if not _same_session(value):
            raise ApiRejected('微信连接已改变，未发送消息。')
        response = await request('sendmessage', value=value, body={'msg': {'from_user_id': '',
            'to_user_id': value['user_id'], 'client_id': client_id, 'message_type': 2, 'message_state': 2,
            'context_token': unprotect_secret(context[0]), 'item_list': [item]}})
        return {'status': 'ok', 'retcode': int(response.get('ret') or 0)}
    except asyncio.CancelledError:
        if process in _stopping:
            raise ConnectionStopped('微信连接已停止') from None
        raise
    finally:
        _inflight.discard(process)
        _stopping.discard(process)


@operation('chat', automatic=True, persist=False)
async def consume_once(*, conversation_id: str = 'wechat_transport', controls: bool = False) -> bool:
    from . import mobile_task_service as mobile, mobile_file_service as files
    from .chat_service import chat_with_ai
    from .model_runtime import model_selection
    value = config()
    if not value.get('enabled'):
        return False
    with repository.read() as conn:
        candidates = conn.consume_once_find_wechat_inbox((value['bot_id'],))
    row = None
    for candidate in candidates:
        preview = json.loads(unprotect_secret(candidate['payload']))
        parts = preview.get('item_list') or []
        plain_text = '\n'.join(str((part.get('text_item') or {}).get('text') or '') for part in parts if part.get('type') == 1)
        urgent = all(part.get('type') == 1 for part in parts) and mobile.urgent_command(plain_text)
        if urgent == controls:
            row = candidate
            break
    if not row:
        return False
    message = json.loads(unprotect_secret(row['payload']))
    origin = origin_for(value); key = row['event_key']
    if not _accept_message(message, value):
        with repository.transaction() as conn:
            conn.consume_once_update_wechat_inbox_2((key,))
        return True
    mobile.initialize()
    if not mobile.claim_event(key, origin):
        with repository.transaction() as conn:
            conn.consume_once_update_wechat_inbox_3((key,))
        return True
    process = asyncio.current_task(); _inflight.add(process)
    try:
        text_parts, unsupported, images, attachment_metadata = [], [], [], []
        for index, item in enumerate(message.get('item_list') or []):
            kind = item.get('type')
            if kind == 1:
                text_parts.append(str((item.get('text_item') or {}).get('text') or ''))
            elif kind == 3 and (item.get('voice_item') or {}).get('text'):
                text_parts.append(item['voice_item']['text'])
            elif kind == 2:
                from .image_service import ImageAttachment, _normalise_raster_image, _to_data_url
                try:
                    if len(images) >= 4:
                        raise ValueError('一次最多处理 4 张图片。')
                    content = await download_media(item.get('image_item') or {})
                    content, mime = await asyncio.to_thread(_normalise_raster_image, content, '微信图片')
                    images.append(ImageAttachment(_to_data_url(content, mime), mime, 'wechat', content))
                    from .conversation_service import archive_attachment
                    suffix = '.jpg' if mime == 'image/jpeg' else '.png'
                    attachment_metadata.append(archive_attachment('微信图片' + suffix, content, mime, 'image'))
                except (ValueError, RuntimeError, httpx.HTTPError):
                    unsupported.append('这张图片未能识别，请以普通图片重新发送；一次最多处理 4 张图片。')
            elif kind == 4:
                document = item.get('file_item') or {}
                try:
                    name = files.clean_name(str(document.get('file_name') or ''))
                    if int(document.get('len') or 0) > MAX_BYTES:
                        raise ValueError('单个文件不能超过 32MB。')
                    content = await download_media(document)
                    if not _same_session(value):
                        raise asyncio.CancelledError()
                    attachment_id = await files.store_document(origin, key + ':' + str(index), name, content)
                    from .wechat_history import archive_document
                    with repository.transaction() as conn:
                        archive_document(conn.history(), conn.consume_once_find_mobile_task_attachments((attachment_id,)))
                    mobile.queue(origin, f'已收到附件：{name}。请告诉我处理要求，或发送“/任务 补充 要求”。', key=key + ':file:' + str(index))
                except (ValueError, httpx.HTTPError):
                    unsupported.append('这个附件未能接收；支持 32MB 以内的 DOCX、PDF 和文本文件，请检查文件后重发。')
            else:
                unsupported.append('这类消息暂不支持。可以发文字、图片、带转写的语音，或 DOCX、PDF、文本文件。')
        text = '\n'.join(part for part in text_parts if part).strip()
        if unsupported:
            mobile.queue(origin, '\n'.join(dict.fromkeys(unsupported)), key=key + ':unsupported')
        handled = await mobile.handle_command(message, origin, text, key) if text else False
        if handled and attachment_metadata:
            from .wechat_history import inbound
            inbound(origin, '微信图片', key + ':images', attachment_metadata)
        if (text or images) and not handled:
            target, hint = mobile.input_context(origin)
            model, reasoning = model_selection('chat')
            result = await chat_with_ai(text, conversation_id=mobile.conversation_id(origin), source='wechat',
                model_id=model, reasoning_level=reasoning, request_id=key, task_origin=target,
                image_attachments=images,
                attachment_metadata=attachment_metadata,
                extra_system_context=hint, agent_tools_enabled=False, handoff_enabled=True)
            if not result.task_handoff:
                mobile.queue(origin, '\n'.join(result.replies), key=key + ':reply', record_history=False)
        mobile.finish_event(key)
    except asyncio.CancelledError:
        mobile.finish_event(key, interrupted=True)
        if process in _stopping:
            raise ConnectionStopped('微信连接已停止') from None
        raise
    except Exception:
        mobile.queue(origin, '这条消息处理失败，请检查澪中的模型与连接状态后重新发送。', key=key + ':error')
        mobile.finish_event(key)
    finally:
        _inflight.discard(process)
        _stopping.discard(process)
    with repository.transaction() as conn:
        conn.consume_once_update_wechat_inbox((key,))
    return True


async def _worker(*, receive: bool, controls: bool = False) -> None:
    while True:
        delay = 1
        try:
            if not tasks.automatic_work_paused() and not maintenance_service.status().get('blocked'):
                value = config()
                if value.get('enabled'):
                    if receive:
                        await poll_once(conversation_id='wechat_transport')
                    else:
                        await consume_once(conversation_id='wechat_transport', controls=controls)
        except (OperationStopped, ConnectionStopped):
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            _state.update(connected=False, message='微信暂时断开，正在重连；可在设置中重新扫码。')
            delay = 8
        await asyncio.sleep(delay)


async def service_loop() -> None:
    initialize()
    from .wechat_history import backfill
    await asyncio.to_thread(backfill)
    workers = [asyncio.create_task(_worker(receive=True)), asyncio.create_task(_worker(receive=False)),
               asyncio.create_task(_worker(receive=False, controls=True))]
    try:
        await asyncio.gather(*workers)
    finally:
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        _state.update(connected=False, message='微信服务已停止')
