import hashlib
import json

from app import agent_file_service as files, artifact_service as artifacts, db
from test_runtime_safety_v2 import isolated  # noqa: F401 - registers the project-wide autouse fixture
def test_diary_revisions_conflict_diff_and_restore(isolated):
    date = "2026-10-05"
    first = db.upsert_diary(date, "第一版", "# 第一版\n原文", expected_revision=0, change_reason="初稿")
    second = db.upsert_diary(date, "第二版", "# 第二版\n修订", expected_revision=first, change_reason="手动编辑")
    assert (first, second) == (1, 2)
    assert [row["revision"] for row in db.list_diary_versions(date)] == [2, 1]
    try:
        db.upsert_diary(date, "过期", "覆盖", expected_revision=1)
    except ValueError as exc:
        assert "版本已变化" in str(exc)
    else:
        raise AssertionError("过期 revision 未被阻止")
    db.upsert_diary(date, "恢复版", "# 第一版\n原文", expected_revision=2, change_reason="从 revision 1 恢复")
    assert db.get_diary(date)["markdown_content"] == "# 第一版\n原文"
    assert db.list_diary_versions(date)[0]["change_reason"] == "从 revision 1 恢复"


def test_xlsx_metadata_is_persisted_in_artifact_library(isolated):
    result = files.write_document(
        "核算.xlsx",
        json.dumps({"sheets": [{"name": "数据", "rows": [["值"], [1], ["=A2*2"]]}]}, ensure_ascii=False),
        "task_upgrade",
    )
    item = artifacts.get(result["artifact_id"])
    assert item["metadata"]["formulas"] == 1
    assert item["metadata"]["formula_values_recalculated"] is False
    assert item["metadata"]["recalculation_engine"] == "none"
