from pathlib import Path

import pytest

from app import db, external_notes_service as external, workspace_service as workspace
from app.routes.diary import save_diary_markdown
from test_runtime_safety_v2 import isolated  # noqa: F401 - installs the shared isolated fixture


def test_external_markdown_round_trip_and_conflict_protection(tmp_path):
    project = workspace.save_project("笔记项目")
    notes = tmp_path / "notes"
    notes.mkdir()
    source = notes / "daily.md"
    source.write_text("# 初稿\n\n第一版", encoding="utf-8")
    root = external.authorize(str(notes), "我的笔记")
    linked = external.link(project["id"], root["id"], "daily.md")
    assert linked["revision"] == 1 and linked["content"].startswith("# 初稿")
    saved = external.write(project["id"], linked["id"], "# 更新\n\n第二版", linked["revision"], linked["current_sha256"])
    assert saved["revision"] == 2
    source.write_text("# 外部修改", encoding="utf-8")
    current = external.read(project["id"], linked["id"])
    assert current["conflict"] is True
    with pytest.raises(ValueError, match="外部笔记原文已被"):
        external.write(project["id"], linked["id"], "# 覆盖", 2, saved["current_sha256"])


def test_diary_revision_marks_linked_followup_stale():
    project = workspace.save_project("日记联动")
    thread = db.remember_pending_thread("default", "核对今天的计划")
    db.attach_pending_thread_source(thread, "diary", "2026-10-05", 1)
    save_diary_markdown("2026-10-05", "# 第一次\n\n计划", expected_revision=None)
    save_diary_markdown("2026-10-05", "# 第二次\n\n计划已更正", expected_revision=1)
    sources = [dict(row) for row in db.list_pending_thread_sources(thread)]
    assert sources and sources[0]["state"] == "stale" and sources[0]["source_revision"] == 1
