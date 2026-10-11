"""Local, versioned video assembly from verified outputs or authorized files."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from contextlib import closing
from pathlib import Path

from . import db, artifact_service, agent_file_service, creation_service
from .workspace_service import encode
from .ffmpeg_runtime import resolve_ffmpeg

_running = {}


def initialize():
    with closing(db.get_conn()) as conn, conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS media_assemblies(
          id TEXT PRIMARY KEY,task_id TEXT NOT NULL,spec_json TEXT NOT NULL,status TEXT NOT NULL,
          artifact_id TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')


def binary():
    return str(resolve_ffmpeg())


def get(assembly_id):
    initialize()
    with closing(db.get_conn()) as conn:
        row = conn.execute('SELECT * FROM media_assemblies WHERE id=?',(assembly_id,)).fetchone()
    if not row:
        raise ValueError('找不到合成任务。')
    item = dict(row)
    item['spec'] = json.loads(item.pop('spec_json'))
    item['artifact'] = artifact_service.get(item['artifact_id']) if item['artifact_id'] else None
    return item


def input_path(item, task_id):
    from . import agent_task_service as tasks, workspace_service as workspace
    task = tasks.get(task_id)
    project = workspace.project_for_conversation(task['conversation_id']) if task else None
    if item.get('job_id'):
        job = creation_service.get_job(item['job_id'])
        job_project = workspace.project_for_conversation(job['conversation_id']) if job else None
        if not job or not task or (job['conversation_id'] != task['conversation_id'] and (not project or not job_project or project['id'] != job_project['id'])):
            raise ValueError('生成素材不属于当前任务或项目。')
        path, _, _ = creation_service.output_file(item['job_id'],int(item.get('index',0)))
        return path
    if item.get('artifact_id'):
        path, artifact = artifact_service.file(item['artifact_id'])
        owner = tasks.get(artifact['task_id']) if artifact.get('task_id') else None
        owner_project = workspace.project_for_conversation(owner['conversation_id']) if owner else None
        if artifact.get('task_id') != task_id and (not project or not owner_project or project['id'] != owner_project['id']):
            raise ValueError('素材成果不属于当前项目。')
        return path
    return agent_file_service.resolve_read(item.get('path',''),task_id=task_id)


def create(spec, context):
    initialize()
    binary()
    if not spec.get('clips') or len(spec['clips']) > 40:
        raise ValueError('请选择 1–40 个片段。')
    for clip in spec['clips']:
        input_path(clip,context.task_id)
    if spec.get('audio'):
        input_path(spec['audio'],context.task_id)
    assembly_id = 'assembly_' + hashlib.sha256(encode([context.task_id,spec]).encode()).hexdigest()[:24]
    with closing(db.get_conn()) as conn, conn:
        conn.execute('INSERT OR IGNORE INTO media_assemblies VALUES(?,?,?,\'queued\',\'\',\'\',?,?)',(assembly_id,context.task_id,encode(spec),db.now_iso(),db.now_iso()))
    schedule(assembly_id)
    return {'assembly':get(assembly_id)}


def schedule(assembly_id):
    if assembly_id not in _running and get(assembly_id)['status'] in {'queued','running'}:
        task = asyncio.create_task(run(assembly_id))
        _running[assembly_id] = task
        task.add_done_callback(lambda _: _running.pop(assembly_id,None))


async def command(args, cwd):
    process = await asyncio.create_subprocess_exec(binary(),'-hide_banner','-loglevel','error','-y',*args,cwd=str(cwd),
        stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.PIPE,creationflags=0x08000000 if os.name=='nt' else 0)
    try:
        _,err = await asyncio.wait_for(process.communicate(),timeout=900)
        if process.returncode:
            raise ValueError('FFmpeg 合成失败：'+err.decode('utf-8',errors='replace')[-1600:])
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise


async def has_audio(path):
    probe = Path(binary()).with_name('ffprobe.exe' if os.name == 'nt' else 'ffprobe')
    executable = str(probe) if probe.is_file() else shutil.which('ffprobe')
    if not executable:
        raise ValueError('保留输入音轨需要 FFprobe，请配置同目录的 FFmpeg 工具。')
    process = await asyncio.create_subprocess_exec(executable, '-v', 'error', '-select_streams', 'a:0',
        '-show_entries', 'stream=index', '-of', 'json', str(path), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, creationflags=0x08000000 if os.name=='nt' else 0)
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout=30)
        if process.returncode:
            raise ValueError('音轨检查失败：'+err.decode('utf-8', errors='replace')[-500:])
        return bool(json.loads(out).get('streams'))
    except BaseException:
        if process.returncode is None:
            process.kill(); await process.wait()
        raise


def stamp(seconds):
    ms = round(max(0,float(seconds))*1000)
    return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'


async def run(assembly_id):
    item = get(assembly_id)
    spec = item['spec']
    folder = agent_file_service.workspace() / item['task_id'] / assembly_id
    folder.mkdir(parents=True,exist_ok=True)
    try:
        with closing(db.get_conn()) as conn, conn:
            conn.execute("UPDATE media_assemblies SET status='running',updated_at=? WHERE id=?",(db.now_iso(),assembly_id))
        width,height = int(spec.get('width',720)),int(spec.get('height',1280))
        if width<128 or height<128 or width>3840 or height>3840 or width%2 or height%2:
            raise ValueError('合成尺寸需要是 128–3840 的偶数。')
        audio_mode = spec.get('audio_mode', 'preserve')
        if audio_mode not in {'preserve', 'replace', 'mix', 'mute'}:
            raise ValueError('不支持的合成音轨模式。')
        if audio_mode in {'replace', 'mix'} and not spec.get('audio'):
            raise ValueError('替换或混合音轨需要提供音频素材。')
        for index,clip in enumerate(spec['clips']):
            path = input_path(clip,item['task_id'])
            duration = float(clip.get('duration',5))
            if not 0.1<=duration<=600:
                raise ValueError('单片段时长需要在 0.1–600 秒之间。')
            kind = clip.get('kind','video')
            prefix = ['-loop','1'] if kind=='image' else ['-ss',str(max(0,float(clip.get('start',0))))]
            native_audio = kind != 'image' and await has_audio(path)
            inputs = [*prefix, '-i', str(path)]
            if not native_audio:
                inputs += ['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
            await command([*inputs,'-t',str(duration),'-map','0:v:0','-map','0:a:0' if native_audio else '1:a:0',
                '-af','apad,aresample=48000','-ac','2','-c:a','aac', '-vf',f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24',
                '-c:v','libx264','-pix_fmt','yuv420p',f'clip-{index}.mp4'],folder)
        (folder/'clips.txt').write_text('\n'.join(f"file 'clip-{i}.mp4'" for i in range(len(spec['clips']))),encoding='utf-8')
        await command(['-f','concat','-safe','1','-i','clips.txt','-c','copy','joined.mp4'],folder)
        args = ['-i','joined.mp4']
        if audio_mode == 'mute':
            args += ['-map','0:v:0','-an']
        elif spec.get('audio'):
            args += ['-i',str(input_path(spec['audio'],item['task_id']))]
            if audio_mode == 'mix':
                args += ['-filter_complex','[0:a:0][1:a:0]amix=inputs=2:duration=first:dropout_transition=0[a]', '-map','0:v:0','-map','[a]','-c:a','aac']
            else:
                args += ['-map','0:v:0','-map','1:a:0','-af','apad','-shortest','-c:a','aac']
        else:
            args += ['-map','0:v:0','-map','0:a:0','-c:a','copy']
        subtitles = spec.get('subtitles') or []
        if subtitles:
            rows = []
            for index,entry in enumerate(subtitles):
                if float(entry['end'])<=float(entry['start']):
                    raise ValueError('字幕结束时间必须晚于开始时间。')
                text = str(entry['text']).replace('\r','').replace('\x00','').replace('\n\n','\n')
                rows.append(f"{index+1}\n{stamp(entry['start'])} --> {stamp(entry['end'])}\n{text}\n")
            (folder/'captions.srt').write_text('\n'.join(rows),encoding='utf-8')
            args += ['-vf',"subtitles=captions.srt:force_style='FontName=Microsoft YaHei,FontSize=22'",'-c:v','libx264']
        else:
            args += ['-c:v','copy']
        args += ['-movflags','+faststart','result.mp4']
        await command(args,folder)
        output = folder/'result.mp4'
        if not output.is_file() or output.stat().st_size<100:
            raise ValueError('未生成有效视频文件。')
        artifact = artifact_service.import_file(output,expected_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),source_root=folder,producer='media_assembly',task_id=item['task_id'],name=Path(spec.get('name') or '成片.mp4').stem+'.mp4')
        with closing(db.get_conn()) as conn, conn:
            conn.execute("UPDATE media_assemblies SET status='completed',artifact_id=?,updated_at=? WHERE id=?",(artifact['id'],db.now_iso(),assembly_id))
    except asyncio.CancelledError:
        with closing(db.get_conn()) as conn, conn:
            conn.execute("UPDATE media_assemblies SET status='queued',updated_at=? WHERE id=?",(db.now_iso(),assembly_id))
        raise
    except Exception as exc:
        with closing(db.get_conn()) as conn, conn:
            conn.execute("UPDATE media_assemblies SET status='failed',error=?,updated_at=? WHERE id=?",(str(exc)[:1800],db.now_iso(),assembly_id))


async def stop_task(task_id, cancelled=False):
    initialize()
    with closing(db.get_conn()) as conn:
        ids=[row[0] for row in conn.execute("SELECT id FROM media_assemblies WHERE task_id=? AND status IN ('queued','running')",(task_id,))]
    running=[_running[key] for key in ids if key in _running]
    for job in running: job.cancel()
    if running: await asyncio.gather(*running,return_exceptions=True)
    if cancelled:
        with closing(db.get_conn()) as conn,conn:
            conn.execute("UPDATE media_assemblies SET status='cancelled' WHERE task_id=? AND status='queued'",(task_id,))


async def service_loop():
    from . import agent_task_service as tasks
    initialize()
    try:
        while True:
            for key,job in list(_running.items()):
                parent=tasks.get(get(key)['task_id'])
                if tasks.automatic_work_paused() or not parent or parent['status'] in {'paused','cancelled'}:
                    job.cancel()
                    await asyncio.gather(job,return_exceptions=True)
            if not tasks.automatic_work_paused():
                with closing(db.get_conn()) as conn:
                    rows = conn.execute("SELECT id,task_id FROM media_assemblies WHERE status IN ('queued','running')").fetchall()
                for row in rows:
                    parent = tasks.get(row['task_id'])
                    if parent and parent['status'] not in {'paused','cancelled'}:
                        schedule(row['id'])
            await asyncio.sleep(3)
    finally:
        for task in list(_running.values()): task.cancel()
        if _running: await asyncio.gather(*list(_running.values()),return_exceptions=True)
