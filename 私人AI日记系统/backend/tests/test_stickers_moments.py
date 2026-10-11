from app import agent_file_service as files, moments_service as moments, sticker_service as stickers, workspace_service as workspace
from test_runtime_safety_v2 import isolated
import hashlib
from contextlib import closing

import pytest

from app import artifact_service, db


def test_authorized_sticker_scan_favorite_and_changed_file_is_blocked(tmp_path):
    root = tmp_path / 'stickers'; root.mkdir()
    files.roots([str(root)])
    source = root / 'happy.png'; source.write_bytes(b'fake-png')
    result = stickers.scan(str(root), context='开心', tags=['安慰', '收藏'])
    assert result['scanned'] == 1 and result['stickers'][0]['context'] == '开心'
    item = result['stickers'][0]
    assert stickers.set_favorite(item['id'], True)['favorite'] is True
    source.write_bytes(b'changed')
    try:
        stickers.file(item['id'])
    except ValueError as exc:
        assert '变化' in str(exc)
    else:
        raise AssertionError('changed sticker must be blocked')


def test_moments_keep_sources_comments_search_and_archive():
    project = workspace.save_project('生活动态')
    item = moments.create(project['id'], '今天完成了一个阶段。')
    assert item['source'] is None and item['archived'] is False
    comment = moments.comment(item['id'], '记下来。')
    assert comment['moment_id'] == item['id']
    archived = moments.archive(item['id'])
    assert archived['archived'] is True and archived['comments'][0]['content'] == '记下来。'
    assert moments.listing(project['id'])['total'] == 0
    assert moments.listing(project['id'], include_archived=True, q='阶段')['total'] == 1


def test_moment_diary_source_requires_existing_record():
    project = workspace.save_project('来源动态')
    try:
        moments.create(project['id'], '引用不存在的日记', source_type='diary', source_id='2099-01-01')
    except ValueError as exc:
        assert '日记不存在' in str(exc)
    else:
        raise AssertionError('missing diary source must be rejected')


@pytest.mark.parametrize('task_only', [False, True])
def test_moment_can_reference_older_project_artifact_beyond_first_page(tmp_path, task_only):
    project = workspace.save_project('分页来源项目')
    task = workspace.enqueue_goal('合成来源任务', project['id'])
    other = workspace.save_project('不相干项目')
    foreign_task = workspace.enqueue_goal('合成其他任务', other['id'])
    folder = tmp_path / 'source'; folder.mkdir()

    def archive(name, owner, *, omit_conversation=False):
        path = folder / name; path.write_text('synthetic ' + name, encoding='utf-8')
        return artifact_service.import_file(path, source_root=folder, producer='synthetic_test',
            expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), task_id=owner['id'],
            conversation_id='' if omit_conversation else owner['conversation_id'])

    old = archive('旧成果.md', task, omit_conversation=task_only)
    with closing(db.get_conn()) as conn, conn:
        conn.execute('UPDATE artifacts SET created_at=? WHERE id=?', ('2000-01-01', old['id']))
    for index in range(102):
        archive(f'新成果-{index}.md', task)
    assert old['id'] not in {item['id'] for item in artifact_service.list_artifacts(project_id=project['id'], limit=2000)}
    moment = moments.create(project['id'], '引用旧成果', source_type='artifact', source_id=old['id'])
    assert moment['source'] == {'type': 'artifact', 'id': old['id'], 'label': '旧成果.md'}
    foreign = archive('他人项目.md', foreign_task, omit_conversation=task_only)
    with pytest.raises(ValueError, match='不属于当前项目'):
        moments.create(project['id'], '不应引用其他项目', source_type='artifact', source_id=foreign['id'])
    with pytest.raises(ValueError, match='不存在'):
        moments.create(project['id'], '不应引用缺失成果', source_type='artifact', source_id='artifact_missing')
