"""Fail-closed Docker execution against a credential-free project copy."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

_cache={}
_lock=threading.RLock()
_active={}
_IMAGE=re.compile(r'^[a-z0-9][a-z0-9._/-]*(?::[A-Za-z0-9._-]+|@sha256:[a-f0-9]{64})?$')


class SandboxCleanupError(RuntimeError):
    pass


def validate_image(image):
    if not isinstance(image,str) or len(image)>150 or not _IMAGE.fullmatch(image):
        raise ValueError('隔离镜像名称无效。')


def environment():
    # Model/API/IPC credentials are never inherited by terminal subprocesses.
    allowed={'PATH','PATHEXT','SYSTEMROOT','WINDIR','COMSPEC','TEMP','TMP','USERPROFILE',
             'HOME','LOCALAPPDATA','APPDATA','PROGRAMFILES','PROGRAMFILES(X86)','LANG','LC_ALL'}
    result={key:value for key,value in os.environ.items() if key.upper() in allowed}
    result.update(PYTHONIOENCODING='utf-8',PYTHONUTF8='1')
    return result


def _run(argv,timeout=3):
    return subprocess.run(argv,stdin=subprocess.DEVNULL,capture_output=True,text=True,
        encoding='utf-8',errors='replace',timeout=timeout,env=environment(),
        creationflags=0x08000000 if os.name=='nt' else 0)


def status(image='python:3.12-alpine',*,refresh=False):
    validate_image(image)
    with _lock:
        cached=_cache.get(image)
        if not refresh and cached and time.monotonic()-cached[0]<5:
            return dict(cached[1])
    result=dict(available=False,backend='docker',image=image,
                message='需要启动本机 Docker，并准备隔离镜像。',shells=['bash'],
                filesystem='仅脱敏项目副本',network='由联网开关控制')
    executable=shutil.which('docker')
    if executable:
        try:
            endpoint=_run([executable,'context','inspect','--format','{{.Endpoints.docker.Host}}'])
            if endpoint.returncode or not endpoint.stdout.strip().startswith(('npipe://','unix://')):
                result['message']='隔离执行只使用本机 Docker，不能连接远程上下文。'
            else:
                engine=_run([executable,'info','--format','{{.OSType}}'])
                if engine.returncode or engine.stdout.strip()!='linux':
                    result['message']='Docker Linux 引擎未启动，隔离执行不可用。'
                else:
                    exists=_run([executable,'image','inspect',image,'--format','{{.Id}}'])
                    if exists.returncode:
                        result['message']=f'隔离镜像尚未准备：{image}'
                    else:
                        result.update(available=True,image_id=exists.stdout.strip(),
                                      message='本机容器隔离可用，不会挂载其他磁盘或私人配置。')
        except (OSError,subprocess.TimeoutExpired):
            result['message']='无法连接本机 Docker，隔离执行不可用。'
    with _lock:
        _cache[image]=(time.monotonic(),result)
    return dict(result)


def _allowed(relative):
    from . import code_workspace_service as code
    parts=relative.parts
    return not any(p.startswith('.') or p.casefold() in {s.casefold() for s in code.EXCLUDED|code.PRIVATE}
                   or p.casefold() in {'dist','build','release','数据','data','运行数据','终端回执'} for p in parts) \
        and not relative.name.casefold().endswith(('.key','.pem','.pfx','.p12','.db','.sqlite')) \
        and relative.name.casefold() not in {'credentials.yaml','secrets.yaml','credentials.yml','secrets.yml'}


def _files(root):
    from .code_workspace_service import is_link
    count,size=0,0
    for current,dirs,names in os.walk(root,followlinks=False):
        directory=Path(current)
        dirs[:]=[name for name in dirs if _allowed((directory/name).relative_to(root))
                  and not is_link(directory/name)]
        for name in sorted(names):
            path=directory/name
            relative=path.relative_to(root)
            if not _allowed(relative) or is_link(path):
                continue
            length=path.stat().st_size
            count+=1;size+=length
            if length>8*1024*1024 or size>128*1024*1024 or count>10000:
                raise ValueError('项目副本超过隔离执行限额，请选择更小的项目或明确授权本机执行。')
            yield relative,path


def prepare(job_id,root,folder,config,shell,command):
    if shell!='bash':
        raise ValueError('隔离终端使用 Linux shell，请选择 bash；Windows 命令请使用本机确认执行。')
    info=status(config['sandbox_image'])
    if not info['available']:
        raise ValueError(info['message']+' 不会降级到本机执行。')
    from .code_workspace_service import is_link
    original=Path(root)
    if is_link(original):
        raise ValueError('隔离项目不能是链接。')
    root=original.resolve(strict=True)
    folder=Path(folder).resolve(strict=True)
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',job_id):
        raise ValueError('隔离作业标识无效。')
    stage=folder/'project';stage.mkdir()
    baseline={}
    try:
        for relative,path in _files(root):
            raw=path.read_bytes()
            if len(raw)>8*1024*1024:
                raise ValueError('复制期间文件增长超过限额。')
            target=stage/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
            baseline[relative.as_posix()]=hashlib.sha256(raw).hexdigest()
    except BaseException:
        # This newly-created stage is owned by this preparation, never the project.
        shutil.rmtree(stage)
        raise
    name='mio-'+job_id.replace('_','-')
    # --mount is passed as one argv item: paths/commands never enter the host shell.
    argv=[shutil.which('docker'),'run','--name',name,'--read-only','--init',
          '--cap-drop','ALL','--security-opt','no-new-privileges',
          '--pids-limit','128','--memory','768m','--cpus','2',
          '--network','bridge' if config['network_enabled'] else 'none',
          '--tmpfs','/tmp:rw,noexec,nosuid,size=64m',
          '--mount',f'type=bind,source={stage},target=/workspace',
          '--workdir','/workspace',info['image_id'],'/bin/sh','-c',command]
    record=dict(name=name,root=str(root),stage=str(stage),baseline=baseline,
                policy_revision=config['revision'],network=config['network_enabled'])
    with _lock:
        _active[job_id]=record
    return argv,record


def remove(job_id):
    with _lock:
        record=_active.get(job_id)
    if record:
        try:
            executable=shutil.which('docker')
            if not executable:
                raise SandboxCleanupError('Docker 不可用，无法确认隔离容器已停止和删除。')
            result=_run([executable,'rm','-f',record['name']],timeout=10)
            if result.returncode:
                missing='no such container' in result.stderr.casefold() or 'no such object' in result.stderr.casefold()
                if not missing:
                    raise SandboxCleanupError('隔离容器删除失败，无法确认已停止：'+result.stderr.strip()[:400])
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise SandboxCleanupError('隔离容器删除未完成，无法确认已停止。') from exc
        with _lock:
            if _active.get(job_id) is record:
                _active.pop(job_id,None)
    return {'removed':True,'name':record['name'] if record else ''}


def publish(job_id,context,revision):
    """Publish textual changes through the existing CAS/backup code tool."""
    with _lock:
        record=_active.get(job_id)
    if not record:
        return {}
    from . import code_workspace_service as code
    config=code.project_context(context,'execute',revision)
    if not config['write_enabled']:
        return {'sandbox_outputs':'retained_in_receipt','published':False}
    stage=Path(record['stage']);root=Path(record['root'])
    changes=[]
    output=dict(_files(stage))
    # Shell deletions stay in the copy; destructive deletes require an explicit operation.
    for relative,path in output.items():
        raw=path.read_bytes();name=relative.as_posix()
        digest=hashlib.sha256(raw).hexdigest()
        before=record['baseline'].get(name)
        if digest==before:
            continue
        if len(raw)>code.MAX_FILE:
            raise ValueError('隔离输出过大，未写回项目。')
        content=code._decode(raw)[0]
        if before:
            target=code.resolve(config,name)
            previous=target.read_bytes()
            if hashlib.sha256(previous).hexdigest()!=before:
                raise ValueError('运行期间项目被修改，隔离结果未覆盖用户的新内容。')
            old=code._decode(previous)[0]
            if not old:
                raise ValueError('隔离结果修改了空文件，请通过代码补丁工具明确写入。')
            changes.append(dict(path=name,expected_sha256=before,edits=[dict(old_text=old,new_text=content)]))
        else:
            code.resolve(config,name,allow_missing=True)
            changes.append(dict(path=name,create=True,content=content))
    if len(changes)>30:
        raise ValueError('隔离结果修改超过30个文件，未自动写回；请拆分任务。')
    result=code.apply_patch(context,changes,revision) if changes else {}
    return {'published':True,'changed_files':len(changes),'change_id':result.get('change_id','')}
