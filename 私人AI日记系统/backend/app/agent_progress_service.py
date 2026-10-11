"""Persisted text chunks and cursor-based task streams; reconnects do not execute work."""
import asyncio
import json
import time
import uuid

from . import db, agent_task_service as tasks
from .agent_result_context import AgentCheckpointError


class FinalTextRecorder:
    def __init__(self,run_id,*,stream_kind='response',round_index=None):
        self.run_id=run_id; self.stream_id=uuid.uuid4().hex; self.ordinal=0
        self.stream_kind=stream_kind; self.round_index=round_index
        self.pending=''; self.total=0; self.last_flush=0
        self.event(f'{stream_kind}_stream_started',{'draft':True})

    def event(self,kind,payload):
        try:
            db.append_agent_run_event(self.run_id,f'{self.run_id}:text:{self.stream_id}:{self.ordinal}',kind,
                json.dumps({'stream_id':self.stream_id,**({'round':self.round_index} if self.round_index is not None else {}),**payload},ensure_ascii=False))
            self.ordinal+=1
        except Exception as exc:
            raise AgentCheckpointError('回复分块未保存，生成已停止，已有工具结果保留。') from exc

    async def delta(self,piece,cumulative):
        self.pending+=piece; self.total+=len(piece)
        if self.ordinal==1 or len(self.pending)>=800 or time.monotonic()-self.last_flush>=.25:
            self.flush()

    def flush(self):
        if self.pending:
            self.event(f'{self.stream_kind}_text_delta',{'text':self.pending,'draft':True})
            self.pending=''; self.last_flush=time.monotonic()

    def finish(self,status):
        self.flush(); self.event(f'{self.stream_kind}_stream_finished',{'status':status,'total_chars':self.total,'draft':True})


def frame(kind,value,event_id=None):
    identifier=f'id: {event_id}\n' if event_id is not None else ''
    return identifier+f'event: {kind}\ndata: '+json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n\n'


def state(task):
    return {'id':task['id'],'status':task['status'],'snapshot':{key:task['snapshot'].get(key) for key in ('summary','goal','plan','blocker','next_step','input_version')},
            'updated_at':task['updated_at']}


async def stream(task_id,request,after_id=0,*,max_seconds=55):
    cursor=max(0,int(after_id)); started=time.monotonic(); last_state=''; last_heartbeat=0
    yield 'retry: 2000\n\n'
    while time.monotonic()-started<max_seconds:
        if await request.is_disconnected(): return
        task=await asyncio.to_thread(tasks.get,task_id)
        if task is None:
            yield frame('task_removed',{'id':task_id}); return
        rows=await asyncio.to_thread(db.list_agent_task_history,task_id,kind='events',after_id=cursor,limit=100)
        for row in rows:
            try: payload=json.loads(row['payload_json'] or '{}')
            except (TypeError,ValueError): payload={'record_error':'事件详情无法读取。'}
            item={'id':int(row['id']),'run_id':row['run_id'],'event_type':row['event_type'],
                  'payload':payload,'created_at':row['created_at']}
            cursor=item['id']; yield frame('progress',item,cursor)
        current=state(task); signature=json.dumps(current,ensure_ascii=False)
        if signature!=last_state:
            yield frame('task_state',current); last_state=signature
        if not rows and task['status'] in {'completed','cancelled','paused','failed','budget_exhausted','waiting_user','waiting_confirmation'}:
            yield frame('idle',{'status':task['status'],'next_id':cursor}); return
        if time.monotonic()-last_heartbeat>=10:
            yield ': heartbeat\n\n'; last_heartbeat=time.monotonic()
        if len(rows)<100: await asyncio.sleep(.3)

