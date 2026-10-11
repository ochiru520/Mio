"""Explicitly enabled HTTPS owner portal; never exposes the localhost control API."""
from __future__ import annotations
import asyncio
import base64
import hashlib
import io
import ipaddress
import secrets
import socket
import time
from contextlib import closing, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, APIRouter, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field, ConfigDict
from . import db, agent_task_service as tasks

_pair = {}
_attempts = {}
_server = None
_state = {'running':False,'error':''}


def initialize():
    with closing(db.get_conn()) as conn, conn:
        conn.executescript('''CREATE TABLE IF NOT EXISTS mobile_portal_settings(id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL,port INTEGER NOT NULL);
          INSERT OR IGNORE INTO mobile_portal_settings VALUES(1,0,8443);
          CREATE TABLE IF NOT EXISTS mobile_portal_devices(id TEXT PRIMARY KEY,name TEXT NOT NULL,token_hash TEXT UNIQUE NOT NULL,expires_at REAL NOT NULL,created_at TEXT NOT NULL,last_seen TEXT NOT NULL);''')


def configuration():
    initialize()
    with closing(db.get_conn()) as conn:
        return dict(conn.execute('SELECT enabled,port FROM mobile_portal_settings WHERE id=1').fetchone())


def addresses():
    values={'127.0.0.1'}
    try:
        for item in socket.getaddrinfo(socket.gethostname(),None,socket.AF_INET):
            address=item[4][0]
            if not ipaddress.ip_address(address).is_unspecified: values.add(address)
    except OSError: pass
    return sorted(values)


def certificate():
    from cryptography import x509
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    directory=db.settings.db_path.parent/'手机入口';directory.mkdir(parents=True,exist_ok=True)
    certfile,keyfile=directory/'portal.crt',directory/'portal.key'
    cafile,cakeyfile=directory/'Mio-local-CA.crt',directory/'Mio-local-CA.key'
    now=datetime.now(timezone.utc)
    if not cafile.exists() or not cakeyfile.exists():
        cakey=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        caname=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Mio personal local CA')])
        ca=(x509.CertificateBuilder().subject_name(caname).issuer_name(caname).public_key(cakey.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True,key_encipherment=False,key_cert_sign=True,crl_sign=True,content_commitment=False,data_encipherment=False,key_agreement=False,encipher_only=False,decipher_only=False),critical=True)
            .sign(cakey,hashes.SHA256()))
        cakeyfile.write_bytes(cakey.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        cafile.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    ca=x509.load_pem_x509_certificate(cafile.read_bytes())
    cakey=serialization.load_pem_private_key(cakeyfile.read_bytes(),password=None)
    ips=addresses()
    if certfile.exists() and keyfile.exists():
        try:
            cert=x509.load_pem_x509_certificate(certfile.read_bytes())
            existing={str(ip) for ip in cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress)}
            if cert.issuer==ca.subject and set(ips)<=existing and cert.not_valid_after_utc>datetime.now(timezone.utc)+timedelta(days=1):return certfile,keyfile
        except (ValueError,x509.ExtensionNotFound):pass
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Mio private mobile portal')])
    now=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(ca.subject).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=365))
          .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),*[x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]]),critical=False)
          .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
          .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),critical=False)
          .sign(cakey,hashes.SHA256()))
    keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return certfile,keyfile


def status():
    value=configuration()
    with closing(db.get_conn()) as conn:
        devices=[dict(r) for r in conn.execute('SELECT id,name,created_at,last_seen,expires_at FROM mobile_portal_devices WHERE expires_at>? ORDER BY created_at DESC',(time.time(),))]
    return {**value,**_state,'addresses':[f'https://{ip}:{value["port"]}/mobile/' for ip in addresses() if ip!='127.0.0.1'],
            'devices':devices,'certificate_url':'/api/mobile-portal/certificate',
            'help':'首次使用需在手机信任本机证书；电脑保持运行。外出访问需你已配置的私人 VPN 或可信 HTTPS 网络入口。不会自动开放公网端口。'}


def configure(enabled,port):
    initialize()
    with closing(db.get_conn()) as conn,conn:
        conn.execute('UPDATE mobile_portal_settings SET enabled=?,port=? WHERE id=1',(int(enabled),port))
    return status()


def new_pairing():
    if not configuration()['enabled']:raise ValueError('请先启用手机入口。')
    code=f'{secrets.randbelow(100000000):08d}'
    _pair.clear();_pair.update(digest=hashlib.sha256(code.encode()).hexdigest(),expires=time.time()+600)
    return {'code':code,'expires_in':600}


def session(request):
    token=request.cookies.get('mio_owner','')
    if not token:return None
    initialize()
    with closing(db.get_conn()) as conn:
        row=conn.execute('SELECT * FROM mobile_portal_devices WHERE token_hash=? AND expires_at>?',(hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
    return dict(row) if row else None


def allowed(method,path):
    import re
    if path in {'/api/workspace/skills/import', '/api/workspace/music/scan'}:
        return False
    workspace_reads = [r'/api/workspace/stickers(?:/[^/]+/file)?', r'/api/workspace/projects/[^/]+/moments']
    workspace_writes = [r'/api/workspace/stickers/[^/]+/favorite', r'/api/workspace/projects/[^/]+/moments',
                        r'/api/workspace/moments/[^/]+/(comments|archive)', r'/api/artifacts/[^/]+/favorite']
    if method == 'GET' and any(re.fullmatch(rule, path) for rule in [*workspace_reads, r'/api/artifacts/[^/]+/(preview|content)']):
        return True
    if method == 'POST' and any(re.fullmatch(rule, path) for rule in workspace_writes):
        return True
    rules={
      'GET':[r'/api/workspace',r'/api/workspace/skills(?:/[^/]+)?',r'/api/workspace/(pending|tasks)',r'/api/workspace/music',r'/api/workspace/music/[^/]+/file',r'/api/workspace/projects/[^/]+',r'/api/workspace/projects/[^/]+/(search|jobs|learning)',r'/api/workspace/projects/[^/]+/learning/[^/]+',r'/api/workspace/projects/[^/]+/history/(notes|selections|documents|batches|assemblies)',r'/api/workspace/knowledge/[^/]+/source',
             r'/api/agent/work',r'/api/agent/work/task_[^/]+',r'/api/agent/work/task_[^/]+/(?:events|messages|runs|citations|stream|inputs)',r'/api/artifacts(?:/[^/]+(?:/download)?)?',r'/api/creation/jobs/[^/]+/outputs/\d+'],
      'POST':[r'/api/workspace/projects',r'/api/workspace/goals',r'/api/workspace/templates',r'/api/workspace/music/scan',r'/api/workspace/projects/[^/]+/(notes|selections|upload|learning)',r'/api/workspace/projects/[^/]+/learning/[^/]+/items',r'/api/workspace/projects/[^/]+/learning/items/[^/]+/answer',
              r'/api/artifacts/[^/]+/(review|verify)',r'/api/workspace/skills',r'/api/workspace/skills/[^/]+/enabled',
              r'/api/workspace/tasks/[^/]+/follow-up',r'/api/workspace/knowledge/[^/]+/reindex',r'/api/workspace/routines(?:/[^/]+/enabled)?',r'/api/workspace/batches/[^/]+/control',r'/api/workspace/browser/[^/]+/confirm',
              r'/api/agent/work/task_[^/]+/(pause|resume|cancel)',r'/api/agent/work/task_[^/]+/actions/\d+/approve',r'/api/creation/jobs/[^/]+/(confirm|cancel)'],
      'PUT':[r'/api/workspace/skills/[^/]+',r'/api/workspace/projects/[^/]+',r'/api/workspace/templates/[^/]+',r'/api/workspace/routines/[^/]+'],
    }
    if method == 'GET' and re.fullmatch(r'/api/workspace/projects/[^/]+/learning/review', path):
        return True
    return any(re.fullmatch(rule,path) for rule in rules.get(method,[]))


class Pair(BaseModel):
    code:str=Field(min_length=8,max_length=8,pattern=r'^\d{8}$')
    name:str=Field(default='我的手机',min_length=1,max_length=80)


def create_app():
    from .routes import workspace, agent_tasks, artifacts, creation
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    app.include_router(workspace.router)
    app.include_router(agent_tasks.router,prefix='/api/agent')
    app.include_router(artifacts.router)
    app.include_router(creation.router)

    @app.middleware('http')
    async def protect(request,call_next):
        if not configuration()['enabled'] or tasks.automatic_work_paused():return JSONResponse({'detail':'手机入口已关闭或暂停。'},503)
        host=urlsplit('https://'+request.headers.get('host','')).hostname
        if host not in {*addresses(),'localhost'}:return JSONResponse({'detail':'入口地址无效。'},403)
        path=request.url.path
        if request.method not in {'GET','HEAD'}:
            expected=f'https://{request.headers.get("host","")}'
            if request.headers.get('origin')!=expected or request.headers.get('x-mio-mobile')!='1':
                return JSONResponse({'detail':'请从已配对的 Mio 手机页面操作。'},403)
        if path.startswith('/api/') and path!='/api/mobile/pair':
            device=session(request)
            if not device:return JSONResponse({'detail':'请在电脑获取配对码后连接。'},401)
            request.state.mobile_portal = True
            if path not in {'/api/mobile/session','/api/mobile/logout'} and not allowed(request.method,path):
                return JSONResponse({'detail':'手机入口不提供此接口。'},403)
            with closing(db.get_conn()) as conn,conn:
                conn.execute('UPDATE mobile_portal_devices SET last_seen=? WHERE id=?',(db.now_iso(),device['id']))
        from . import maintenance_service
        try:
            with maintenance_service.mutation_scope() if request.method not in {'GET','HEAD'} else nullcontext():
                response=await call_next(request)
        except maintenance_service.MaintenanceModeError as exc:
            return JSONResponse({'detail':str(exc)},503)
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
                                 'Content-Security-Policy':"frame-ancestors 'none'"})
        return response

    @app.post('/api/mobile/pair')
    async def pair_device(payload:Pair,request:Request):
        now=time.time();ip=request.client.host if request.client else 'unknown'
        for key in list(_attempts):
            _attempts[key]=[stamp for stamp in _attempts[key] if now-stamp<600]
            if not _attempts[key]:_attempts.pop(key,None)
        for key,limit in ((ip,8),('*',40)):
            if len(_attempts.get(key,[]))>=limit:raise HTTPException(429,'配对尝试过多，请十分钟后再试。')
            _attempts.setdefault(key,[]).append(now)
        if now>_pair.get('expires',0) or not secrets.compare_digest(hashlib.sha256(payload.code.encode()).hexdigest(),_pair.get('digest','')):
            raise HTTPException(401,'配对码无效或已过期。')
        _pair.clear()
        token=secrets.token_urlsafe(40);device_id='device_'+secrets.token_hex(12)
        with closing(db.get_conn()) as conn,conn:
            conn.execute('INSERT INTO mobile_portal_devices VALUES(?,?,?,?,?,?)',(device_id,payload.name,hashlib.sha256(token.encode()).hexdigest(),now+30*86400,db.now_iso(),db.now_iso()))
        response=JSONResponse({'paired':True,'name':payload.name})
        response.set_cookie('mio_owner',token,max_age=30*86400,httponly=True,secure=True,samesite='strict',path='/')
        return response

    @app.get('/api/mobile/session')
    async def current_session(request:Request):
        device=session(request)
        return {'paired':True,'name':device['name']}

    @app.post('/api/mobile/logout')
    async def logout(request:Request):
        device=session(request)
        with closing(db.get_conn()) as conn,conn:conn.execute('DELETE FROM mobile_portal_devices WHERE id=?',(device['id'],))
        response=JSONResponse({'paired':False});response.delete_cookie('mio_owner',path='/');return response

    @app.get('/mobile/')
    @app.get('/mobile/{path:path}')
    async def mobile_file(path:str=''):
        root=db.settings.agent_frontend_dir.resolve()
        relative='mobile.html' if not path else path
        candidate=(root/relative).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():raise HTTPException(404,'手机页面尚未打包。')
        if candidate.suffix not in {'.html','.js','.css','.svg','.png','.webmanifest','.woff2','.ico'}:raise HTTPException(404)
        return FileResponse(candidate)
    return app


async def service_loop():
    global _server
    import uvicorn
    runner=None;active_port=None
    try:
        while True:
            config=configuration();wanted=config['enabled'] and not tasks.automatic_work_paused()
            if runner and (not wanted or active_port!=config['port']):
                _server.should_exit=True
                await runner;runner=None;_state['running']=False
            if wanted and runner is None:
                try:
                    cert,key=await asyncio.to_thread(certificate)
                    _server=uvicorn.Server(uvicorn.Config(create_app(),host='0.0.0.0',port=config['port'],ssl_certfile=str(cert),ssl_keyfile=str(key),access_log=False,log_level='warning',timeout_graceful_shutdown=5))
                    # Bind here so a port conflict cannot SystemExit the desktop process.
                    sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
                    try:sock.bind(('0.0.0.0',config['port']));sock.listen(128);sock.setblocking(False)
                    except BaseException:sock.close();raise
                    async def serve(server,sock):
                        try:await server.serve(sockets=[sock])
                        finally:sock.close()
                    runner=asyncio.create_task(serve(_server,sock));active_port=config['port'];_state.update(running=True,error='')
                except Exception as exc:
                    _state.update(running=False,error=str(exc)[:500]);await asyncio.sleep(5)
            if runner and runner.done():
                try:runner.result()
                except Exception as exc:_state['error']=str(exc)[:500]
                runner=None;_state['running']=False
            await asyncio.sleep(1)
    finally:
        if _server:_server.should_exit=True
        if runner:
            try:await asyncio.wait_for(asyncio.shield(runner),8)
            except (asyncio.TimeoutError,asyncio.CancelledError):runner.cancel();await asyncio.gather(runner,return_exceptions=True)
        _state['running']=False;_server=None


router=APIRouter(prefix='/api/mobile-portal',tags=['mobile-portal'])
class Settings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled:bool=False
    port:int=Field(default=8443,ge=1024,le=65535)

@router.get('')
async def portal_status():return status()

@router.put('')
async def portal_config(payload:Settings):return configure(payload.enabled,payload.port)

@router.post('/pairing')
async def portal_pairing():
    try:return new_pairing()
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc

@router.delete('/devices/{device_id}')
async def revoke(device_id:str):
    initialize()
    with closing(db.get_conn()) as conn,conn:conn.execute('DELETE FROM mobile_portal_devices WHERE id=?',(device_id,))
    return {'revoked':True}

@router.get('/certificate')
async def download_certificate():
    cert,_=await asyncio.to_thread(certificate)
    return FileResponse(cert.parent/'Mio-local-CA.crt',filename='Mio手机入口.crt',media_type='application/x-x509-ca-cert')

@router.get('/qr')
async def qr():
    import qrcode
    urls=status()['addresses']
    if not urls:raise HTTPException(400,'未检测到局域网地址。')
    output=io.BytesIO();qrcode.make(urls[0]).save(output,format='PNG')
    return Response(output.getvalue(),media_type='image/png',headers={'Cache-Control':'no-store'})
