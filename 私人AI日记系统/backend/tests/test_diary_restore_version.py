from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import db
from app.routes.diary import router
from test_runtime_safety_v2 import isolated


def test_restore_preserves_recorded_title_and_rejects_a_stale_current_revision(isolated):
    date = '2026-10-01'
    db.upsert_diary(date, '原始自定义标题', '# 正文标题\n\n原始记录。')
    db.upsert_diary(date, '新标题', '# 更新后的正文\n\n保留新记录。')
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        stale = client.post(f'/api/diaries/{date}/versions/1/restore', json={'expected_revision': 1})
        assert stale.status_code == 409
        assert db.get_diary(date)['title'] == '新标题'
        restored = client.post(f'/api/diaries/{date}/versions/1/restore', json={'expected_revision': 2})
        assert restored.status_code == 200, restored.text
        assert restored.json()['title'] == '原始自定义标题'
        assert db.get_diary(date)['markdown_content'] == '# 正文标题\n\n原始记录。'
        assert len(db.list_diary_versions(date)) == 3
        assert db.settings.diary_dir.joinpath(date + '.md').read_text(encoding='utf-8') == '# 正文标题\n\n原始记录。'
