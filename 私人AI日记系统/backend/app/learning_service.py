"""Small, durable learning loop built on project knowledge and notes."""
from __future__ import annotations

import re
import json
import uuid
from datetime import datetime, timedelta, timezone

from .repositories import learning_service_repository as repository
from . import db, workspace_service as workspace


def initialize() -> None:
    with repository.transaction() as conn:
        conn.initialize_initialize_learning_sessions()
        conn.initialize_drafts()
        columns = {row["name"] for row in conn.initialize_inspect_schema()}
        if "review_at" not in columns:
            conn.initialize_alter_learning_items()
        if "review_streak" not in columns:
            conn.initialize_alter_learning_items_2()


def _session(session_id: str, project_id: str = ""):
    initialize()
    with repository.read() as conn:
        row = conn.session_find_learning_sessions((session_id, project_id, project_id))
    if row is None:
        raise ValueError("学习会话不存在或不属于当前项目。")
    return row


def _public(session_id: str, project_id: str = "") -> dict:
    row = _session(session_id, project_id)
    with repository.read() as conn:
        items = conn.public_find_learning_items((session_id,))
        attempts = conn.public_find_learning_attempts((session_id,))
    attempt_map = {}
    for attempt in attempts:
        attempt_map.setdefault(attempt["item_id"], dict(attempt))
    data = dict(row)
    data["items"] = [{**dict(item), "latest_attempt": attempt_map.get(item["id"])} for item in items]
    scores = [float(item["latest_attempt"]["score"]) for item in data["items"] if item["latest_attempt"]]
    data["progress"] = {"total": len(items), "answered": len(scores), "average_score": round(sum(scores) / len(scores), 3) if scores else 0}
    data['draft'] = draft(project_id, session_id)
    return data


def sessions(project_id: str) -> list[dict]:
    workspace.require_project(project_id)
    initialize()
    with repository.read() as conn:
        ids = [row["id"] for row in conn.sessions_find_learning_sessions((project_id,))]
    return [_public(identifier, project_id) for identifier in ids]


def create_session(project_id: str, title: str, goal: str = "", source_document_id: str = "") -> dict:
    workspace.require_project(project_id, active=True)
    initialize()
    if not str(title or "").strip():
        raise ValueError("学习会话需要标题。")
    if source_document_id:
        with repository.read() as conn:
            if not conn.create_session_find_knowledge_documents((source_document_id, project_id)):
                raise ValueError("资料不属于当前项目。")
    identifier = "learn_" + uuid.uuid4().hex[:24]
    now = db.now_iso()
    with repository.transaction() as conn:
        conn.create_session_insert_learning_sessions((identifier, project_id, title.strip()[:160], str(goal or "").strip()[:2000], source_document_id, "active", now, now))
    return _public(identifier, project_id)


def add_item(project_id: str, session_id: str, prompt: str, answer_key: str = "", source_ref: str = "") -> dict:
    _session(session_id, project_id)
    if not str(prompt or "").strip():
        raise ValueError("练习题目不能为空。")
    identifier = "learn_item_" + uuid.uuid4().hex[:24]
    with repository.transaction() as conn:
        conn.add_item_insert_learning_items((identifier, session_id, prompt.strip()[:5000], str(answer_key or "").strip()[:2000],
                      str(source_ref or "").strip()[:500], db.now_iso(), '', 0))
        conn.add_item_update_learning_sessions((db.now_iso(), session_id))
    return next(item for item in _public(session_id, project_id)["items"] if item["id"] == identifier)


def answer(project_id: str, item_id: str, value: str) -> dict:
    if not str(value or "").strip():
        raise ValueError("作答内容不能为空。")
    initialize()
    with repository.read() as conn:
        item = conn.answer_find_learning_items((item_id, project_id))
    if item is None:
        raise ValueError("练习不存在或不属于当前项目。")
    expected = [part.strip().casefold() for part in re.split(r"[,，、;；|]", str(item["answer_key"] or "")) if part.strip()]
    actual = str(value).strip().casefold()
    if not expected:
        score, feedback = 0.0, "已保存作答；这道题没有设置可自动核对的答案，请结合资料人工复盘。"
    else:
        matched = sum(1 for term in expected if term in actual)
        score = round(matched / len(expected), 3)
        feedback = "要点已覆盖。" if score == 1 else f"已覆盖 {matched}/{len(expected)} 个要点，回看资料后再补充。"
    with repository.transaction() as conn:
        conn.clear_answered_draft(item['session_id'], item_id)
        cursor = conn.answer_insert_learning_attempts((item_id, value.strip()[:10000], score, feedback, db.now_iso()))
        previous = int(item["review_streak"] or 0)
        streak = previous + 1 if score >= 0.8 else 0
        intervals = (1, 3, 7, 14, 30)
        days = intervals[min(streak, len(intervals)) - 1] if streak else 1
        review_at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        conn.answer_update_learning_items((review_at, streak, item_id))
        conn.answer_update_learning_sessions((db.now_iso(), item["session_id"]))
        attempt_id = cursor.lastrowid
    return {"id": attempt_id, "item_id": item_id, "answer": value.strip(), "score": score, "feedback": feedback, "review_at": review_at, "review_streak": streak}


def review(project_id: str, limit: int = 50) -> dict:
    """Return unanswered, low-scoring, or due items without changing their schedule."""
    workspace.require_project(project_id)
    initialize()
    now = datetime.now(timezone.utc).isoformat()
    with repository.read() as conn:
        rows = conn.review_n_learning_items((project_id, now, max(1, min(int(limit), 200))))
    return {"items": [dict(row) for row in rows], "total": len(rows), "as_of": now}


def session(project_id: str, session_id: str) -> dict:
    return _public(session_id, project_id)


def draft(project_id: str, session_id: str) -> dict:
    _session(session_id, project_id)
    with repository.read() as conn:
        row = conn.draft(session_id)
    return {'answers': json.loads(row['answers_json']) if row else {},
            'flagged_item_ids': json.loads(row['flagged_json']) if row else [],
            'active_item_id': row['active_item_id'] if row else '',
            'revision': row['revision'] if row else 0, 'updated_at': row['updated_at'] if row else ''}


def save_draft(project_id, session_id, answers, flagged_item_ids, active_item_id='', expected_revision=None):
    workspace.require_project(project_id, active=True)
    _session(session_id, project_id)
    with repository.read() as conn:
        valid = {row['id'] for row in conn.public_find_learning_items((session_id,))}
    if (set(answers) | set(flagged_item_ids) | ({active_item_id} if active_item_id else set())) - valid:
        raise ValueError('草稿包含不属于当前学习会话的题目。')
    if any(not isinstance(value, str) or len(value) > 10000 for value in answers.values()):
        raise ValueError('草稿内容无效或超过长度限制。')
    with repository.transaction() as conn:
        conn.save_draft(session_id, answers, list(dict.fromkeys(flagged_item_ids)), active_item_id, expected_revision, db.now_iso())
    return draft(project_id, session_id)
