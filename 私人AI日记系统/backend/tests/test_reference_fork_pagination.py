"""Synthetic coverage for branch reference ownership and complete candidate paging."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import agent_file_service as files
from app import conversation_state_service as state
from app import db
from app import reference_service as references
from app import workspace_service as workspace
from app.routes.workspace import router
from test_runtime_safety_v2 import isolated


def reference_branch(tmp_path, kind):
    root = tmp_path / "granted"
    root.mkdir()
    source = root / "reference.md"
    source.write_text("unique reference content", encoding="utf-8")
    files.roots([str(root)])
    conversation = "desktop_agent_reference_branch"
    target = "desktop_agent_reference_source"
    db.create_agent_conversation(conversation, "引用分支")
    db.create_agent_conversation(target, "引用来源")
    project = workspace.save_project("引用项目")
    workspace.bind_conversation(project["id"], conversation)
    workspace.bind_conversation(project["id"], target)
    db.save_message("user", "unique conversation reference", conversation_id=target)
    selected_target = {"file": str(source), "directory": str(root), "conversation": target}[kind]
    selected = references.resolve([{"kind": kind, "target": selected_target}], project["id"])["resolved"][0]
    metadata = {"kind": "reference", "reference_kind": kind, "target": selected["target"],
                "version": selected["version"], "name": selected["name"]}
    original_id = db.save_message("user", "查看引用", conversation_id=conversation,
                                 attachments_json=json.dumps([metadata]))
    branch = state.fork(conversation)
    branch_id = db.get_recent_messages(conversation_id=branch["id"])[0]["id"]
    return root, source, target, original_id, branch, branch_id, metadata


@pytest.mark.parametrize("kind", ["file", "directory", "conversation"])
def test_fork_preserves_reference_version_and_replay_rechecks_changes(isolated, tmp_path, kind):
    root, source, target, original_id, branch, branch_id, metadata = reference_branch(tmp_path, kind)
    original = state.read_replay_attachments("desktop_agent_reference_branch", original_id)
    replay = state.read_replay_attachments(branch["id"], branch_id)
    assert replay["metadata"] == [metadata]
    assert replay["text_attachments"][0].content == original["text_attachments"][0].content
    assert workspace.project_for_conversation(branch["id"])["id"] == workspace.project_for_conversation(target)["id"]
    if kind == "file":
        source.write_text("changed reference content", encoding="utf-8")
    elif kind == "directory":
        (root / "new.md").write_text("new file", encoding="utf-8")
    else:
        db.save_message("user", "changed conversation reference", conversation_id=target)
    with pytest.raises(ValueError, match="来源已变化"):
        state.read_replay_attachments(branch["id"], branch_id)


@pytest.mark.parametrize("kind", ["file", "directory", "conversation"])
def test_fork_reference_replay_rechecks_permission_and_project(isolated, tmp_path, kind):
    _, _, target, _, branch, branch_id, _ = reference_branch(tmp_path, kind)
    if kind == "conversation":
        other = workspace.save_project("另一个项目")
        workspace.bind_conversation(other["id"], target)
    else:
        files.roots([])
    with pytest.raises(ValueError, match="不属于当前项目|尚未授权"):
        state.read_replay_attachments(branch["id"], branch_id)


def test_candidate_search_finds_authorized_files_after_the_old_500_limit(isolated, tmp_path):
    root = tmp_path / "large"
    root.mkdir()
    for index in range(505):
        (root / f"a_{index:04d}.md").write_text("synthetic", encoding="utf-8")
    source = root / "z_target.md"
    source.write_text("the source", encoding="utf-8")
    files.roots([str(root)])
    page = references.candidates(kind="file", path=str(root), query="z_target", limit=2)
    assert [item["target"] for item in page["candidates"]] == [str(source.resolve())]
    assert page["offset"] == 0 and page["limit"] == 2
    assert not page["has_more"] and page["next_offset"] is None
    files.roots([])
    with pytest.raises(ValueError, match="尚未授权"):
        references.candidates(kind="file", path=str(root), query="z_target")


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_candidate_http_pages_after_name_kind_and_authorization_filters(isolated, tmp_path, kind):
    root = tmp_path / "paging"
    root.mkdir()
    expected = []
    for index in range(7):
        target = root / (f"match_{index}" + (".md" if kind == "file" else ""))
        if kind == "file":
            target.write_text("synthetic", encoding="utf-8")
        else:
            target.mkdir()
        expected.append(str(target.resolve()))
        (root / f"unmatched_{index}.md").write_text("synthetic", encoding="utf-8")
    (root / ".match_hidden.md").write_text("hidden", encoding="utf-8")
    (root / "match_unsupported.exe").write_text("unsupported", encoding="utf-8")
    if kind == "directory":
        (root / "match_file.md").write_text("must not become directory candidate", encoding="utf-8")
    files.roots([str(root)])
    app = FastAPI()
    app.include_router(router)
    found = []
    with TestClient(app) as client:
        base = {"kind": kind, "path": str(root), "query": "match_", "limit": 3}
        offset = 0
        while True:
            response = client.get("/api/workspace/references/candidates", params={**base, "offset": offset})
            assert response.status_code == 200, response.text
            page = response.json()
            found.extend(item["target"] for item in page["candidates"])
            assert page["offset"] == offset and page["limit"] == 3
            if not page["has_more"]:
                assert page["next_offset"] is None
                break
            assert page["next_offset"] == offset + len(page["candidates"])
            offset = page["next_offset"]
        assert client.get("/api/workspace/references/candidates", params={**base, "offset": -1}).status_code == 422
    assert found == expected


def test_conversation_and_authorized_root_candidates_use_the_same_paging_contract(isolated, tmp_path):
    granted = [tmp_path / f"grant_{index}" for index in range(3)]
    for root in granted:
        root.mkdir()
    files.roots([str(root) for root in granted])
    page = references.candidates(query="grant_", limit=2)
    assert len(page["candidates"]) == 2 and page["has_more"] and page["next_offset"] == 2
    final = references.candidates(query="grant_", limit=2, offset=2)
    assert len(final["candidates"]) == 1 and not final["has_more"]
    project = workspace.save_project("候选分页")
    for index in range(3):
        identifier = f"desktop_agent_candidate_{index}"
        db.create_agent_conversation(identifier, f"分页来源 {index}")
        workspace.bind_conversation(project["id"], identifier)
    first = references.candidates(kind="conversation", query="分页来源", project_id=project["id"], limit=2)
    second = references.candidates(kind="conversation", query="分页来源", project_id=project["id"], limit=2, offset=first["next_offset"])
    assert first["has_more"] and len(second["candidates"]) == 1 and not second["has_more"]
    assert len({item["target"] for item in first["candidates"] + second["candidates"]}) == 3
