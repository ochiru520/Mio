"""Read projections for runtime calls and operation history."""
from contextlib import closing
from .. import db


def operations(*, mode='', conversation_id='', task_id='', request_id='', limit=50, offset=0):
    clauses, parameters = [], []
    for key, value in {"mode": mode, "conversation_id": conversation_id, "task_id": task_id, "request_id": request_id}.items():
        if value:
            clauses.append(f"{key}=?"); parameters.append(value)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT * FROM runtime_operations" + where + " ORDER BY started_at DESC,id DESC LIMIT ? OFFSET ?", (*parameters, limit+1, offset)).fetchall()
        result = []
        for row in rows[:limit]:
            item = dict(row)
            item["calls"] = [dict(call) for call in conn.execute("SELECT * FROM runtime_model_calls WHERE operation_id=? ORDER BY started_at,id", (row["id"],))]
            result.append(item)
    return result, len(rows)>limit


def usage_since(start):
    with closing(db.get_conn()) as conn:
        return conn.execute("SELECT mode,status,prompt_tokens,completion_tokens,cost_yuan,cost_source,duration_ms FROM runtime_model_calls WHERE started_at>=?", (start,)).fetchall()
