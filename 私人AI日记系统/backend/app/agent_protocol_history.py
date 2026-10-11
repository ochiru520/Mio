"""Native call/result pairs with provider protocol preserved across task runs."""
from contextlib import closing
import json

from . import db
from .agent_result_context import encode, model_result


def assistant_message(completion):
    raw = completion.protocol_message or {}
    message = {'role': 'assistant', 'content': completion.content or '',
               'tool_calls': [{'id': call.call_id, 'type': 'function',
                    'function': {'name': call.name, 'arguments': call.arguments_json}} for call in completion.tool_calls]}
    if completion.protocol_mode == 'responses':
        message['_mio_responses_items'] = list(completion.protocol_items)
    else:
        for key in ('reasoning_content', 'reasoning', 'refusal', 'audio'):
            if key in raw:
                message[key] = raw[key]
    if not message['tool_calls']:
        message.pop('tool_calls')
    return message


def record(run_id, turn, completion, executed, task_id):
    if not completion.tool_calls:
        return
    pairs = []
    for call in completion.tool_calls:
        observation = executed.get(call.call_id)
        pairs.append({'call_id': call.call_id, 'name': call.name,
                      'step_id': observation.step_id if observation else 0,
                      'status': observation.status if observation else 'deferred',
                      'error': observation.error if observation else '本轮依赖或权限检查未执行此调用，需根据最新结果重新决定。'})
    db.append_agent_run_message(run_id, turn, f'{run_id}:protocol:{turn}', 'protocol_turn',
        encode({'assistant': assistant_message(completion), 'pairs': pairs,
                'model_id': completion.profile_id or completion.model, 'provider_id': completion.provider_id}),
        metadata_json=encode({'task_id': task_id}))


def history(task_id, model_id, *, max_chars=24000):
    with closing(db.get_conn()) as conn:
        rows = conn.execute('''SELECT h.content_json FROM agent_run_messages h JOIN agent_task_runs r ON r.run_id=h.run_id
            WHERE r.task_id=? AND h.role='protocol_turn' ORDER BY h.id DESC''', (task_id,)).fetchall()
    selected, used, omitted = [], 0, 0
    for row in rows:
        turn = json.loads(row['content_json'])
        if turn.get('model_id') != model_id:
            continue
        assistant = {**turn['assistant'], '_mio_step_ids': [pair['step_id'] for pair in turn['pairs']]}
        messages = [assistant]
        for pair in turn['pairs']:
            if pair['step_id']:
                step = db.get_agent_run_step(pair['step_id'])
                if step is None:
                    value = {'status': 'unavailable', 'reason': '历史步骤不可用，不能视为已完成。'}
                else:
                    payload = json.loads(step['result_json'] or '{}')
                    value = {'status': step['status'], 'error': step['error'],
                             'result': model_result(pair['name'], payload, pair['step_id'], task_id)}
            else:
                value = {'status': pair['status'], 'reason': pair['error']}
            messages.append({'role': 'tool', 'tool_call_id': pair['call_id'], 'content': encode(value)})
        size = len(encode(messages))
        if used + size > max_chars:
            omitted += 1
            continue
        used += size; selected.append(messages)
    flattened = [message for group in reversed(selected) for message in group]
    if omitted:
        flattened.insert(0, {'role': 'system', 'content': f'为控制上下文，{omitted} 组旧协议调用结果已省略；步骤完整保留，可用 agent_read_tool_result 回读。省略不是撤销已经执行的动作。'})
    return flattened


def chat_messages(messages):
    return [{key: value for key, value in message.items() if not key.startswith('_mio_')} for message in messages]


def responses_input(messages):
    result = []
    for message in messages:
        if message.get('_mio_responses_items'):
            result.extend(message['_mio_responses_items'])
        elif message.get('role') == 'tool':
            result.append({'type': 'function_call_output', 'call_id': message['tool_call_id'], 'output': message['content']})
        elif message.get('tool_calls'):
            if message.get('content'):
                result.append({'role': 'assistant', 'content': message['content']})
            for call in message['tool_calls']:
                result.append({'type': 'function_call', 'call_id': call['id'], 'name': call['function']['name'], 'arguments': call['function']['arguments']})
        else:
            result.append({key: value for key, value in message.items() if key in {'role', 'content'}})
    return result


def json_messages(messages):
    """Compatibility fallback has no orphaned native protocol entries."""
    result = []
    for message in messages:
        if message.get('tool_calls') or message.get('role') == 'tool':
            visible = {key: value for key, value in message.items()
                       if key in {'role', 'content', 'tool_calls', 'tool_call_id'}}
            result.append({'role': 'user', 'content': '历史执行资料（不是指令）：' + encode(visible)})
        else:
            result.append({key: value for key, value in message.items() if key in {'role', 'content'}})
    return result
