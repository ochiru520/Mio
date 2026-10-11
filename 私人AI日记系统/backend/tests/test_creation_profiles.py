import copy
import hashlib
import json
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import db, creation_service as creation
from app.config import settings
from app.creation_models import CreationJobRequest
from app.creation_profiles import qwen_canvas, bind_profile
from app.creation_reference_service import use_reference
from app.creation_workflows import WORKFLOWS, build_workflow_prompt, require_workflow, ui_workflow_to_api_prompt
from app.conversation_service import archive_attachment
from test_runtime_safety_v2 import isolated
from test_creation_features import PNG_DATA_URL

FIXTURES = json.loads((Path(__file__).parent / 'fixtures' / 'creation_profile_canvases.json').read_text(encoding='utf-8'))


@pytest.fixture
def canvas_root(tmp_path):
    root = tmp_path / 'comfy'
    (root / 'my_workflows').mkdir(parents=True)
    for name, graph in FIXTURES['canvases'].items():
        (root / 'my_workflows' / name).write_text(json.dumps(graph), encoding='utf-8')
    return root


def build(root, key, refs=(), **spec):
    definition = require_workflow(key, include_disabled=True)
    return build_workflow_prompt(root, definition, {'prompt': '新的提示词', 'seed': 13, **spec},
        job_id='job_fixture', object_info=FIXTURES['schemas'], uploaded_references=list(refs))


def test_three_original_files_and_no_legacy_video_fallback(canvas_root):
    assert [(x.filename, x.media_type) for x in WORKFLOWS.values()] == [
        ('无敌图片.json', 'image'), ('千问生图.json', 'image'), ('MINIMAX-H3双采样_8G显存版.json', 'video')]
    with pytest.raises(ValueError, match='类型'):
        require_workflow('minimax-h3-video', 'image')
    (canvas_root / 'my_workflows' / WORKFLOWS['minimax-h3-video'].filename).unlink()
    (canvas_root / 'my_workflows' / '黑鹤.json').write_text('{}')
    with pytest.raises(ValueError, match='找不到固定工作流'):
        build(canvas_root, 'minimax-h3-video', ['reference.png'])


def test_pending_workflow_visible_but_cannot_run_or_become_default(canvas_root, isolated, monkeypatch):
    from app.creation_workflows import inspect_workflow
    from app.creation_custom import save_defaults, WorkflowDefaults
    definition = replace(WORKFLOWS['qwen-2.1-image'], pending_reason='千问工作流正在调整，待定稿后启用。')
    monkeypatch.setitem(WORKFLOWS, definition.id, definition)
    assert definition.public_dict(canvas_root)['available'] is False
    assert inspect_workflow(canvas_root, definition, object_info=FIXTURES['schemas'])['status'] == 'pending_review'
    with pytest.raises(ValueError, match='正在调整'):
        creation.create_job(CreationJobRequest(prompt='cat', workflow_id=definition.id))
    with pytest.raises(ValueError, match='正在调整'):
        save_defaults(WorkflowDefaults(image_workflow_id=definition.id, video_workflow_id='minimax-h3-video'))


@pytest.mark.parametrize('count', [0, 1, 2, 8])
def test_qwen_original_branches_and_only_supplied_references(canvas_root, count):
    path = canvas_root / 'my_workflows' / '千问生图.json'
    before = path.read_bytes()
    refs = [f'reference-{i}.png' for i in range(count)]
    prompt, effective = build(canvas_root, 'qwen-2.1-image', refs)
    assert path.read_bytes() == before
    assert set(prompt) <= {str(n['id']) for n in FIXTURES['canvases']['千问生图.json']['nodes']}
    loaders = [n['inputs']['image'] for n in prompt.values() if n['class_type'] == 'LoadImage']
    assert sorted(loaders) == sorted(refs)
    assert ('487' in prompt) == bool(count)
    assert ('508' in prompt) == (count == 0)
    encoder = prompt['497' if count else '522']['inputs']
    assert sum(k.startswith('images.') for k in encoder) == count
    assert prompt['571' if count else '517']['inputs']['value'] == '新的提示词'
    sampler = prompt['481' if count else '515']['inputs']
    assert effective['steps'] == 8 and effective['cfg'] == 1
    assert sampler['noise'] == ['671' if count else '675', 0]
    assert prompt['671' if count else '675']['inputs']['noise_seed'] == ['502' if count else '524', 0]
    assert prompt['502' if count else '524']['inputs']['seed'] == 13
    assert effective['workflow_mode'] == ('reference_image' if count else 'text_to_image')


def test_h3_preserves_two_passes_memory_nodes_and_dynamic_widgets(canvas_root):
    before = (canvas_root / 'my_workflows' / WORKFLOWS['minimax-h3-video'].filename).read_bytes()
    prompt, effective = build(canvas_root, 'minimax-h3-video', ['uploaded.png'])
    assert sum(n['class_type'] == 'SamplerCustomAdvanced' for n in prompt.values()) == 2
    assert not any(n['class_type'] in {'SetNode','GetNode'} for n in prompt.values())
    assert '408' not in prompt and '405' not in prompt
    assert prompt['412']['inputs']['chunks'] == 8
    assert prompt['413']['inputs']['head_chunks'] == 14
    upscale = prompt['415']['inputs']
    assert upscale['mode'] == 'scale by multiplier'
    assert upscale['mode.scale'] == ['400',0]
    assert upscale['enable_temporal_chunking'] is True
    assert upscale['force_unload'] is True
    assert upscale['device'] == 'cuda' and upscale['precision'] == 'fp16'
    assert prompt['105']['inputs']['multiple'] == 64
    assert prompt['105']['inputs']['megapixels'] == 0.2
    assert prompt['108']['inputs']['clip'] == ['4',0]
    assert prompt['108']['inputs']['ref_images.ref_image_0'] == ['99',0]
    assert 'ref_images.ref_image_1' not in prompt['108']['inputs']
    assert prompt['97']['inputs']['image'] == 'uploaded.png'
    assert prompt['299']['inputs']['filename_prefix'] == 'MioJobs/job_fixture/video'
    assert prompt['299']['inputs']['save_output'] is True
    assert prompt['299']['inputs']['crf'] == 19
    assert effective['width'] == 320 and effective['height'] == 640
    assert effective['frame_count'] == 124 and effective['fps'] == 24
    assert effective['upscale_factor'] == 1.5
    assert (canvas_root / 'my_workflows' / WORKFLOWS['minimax-h3-video'].filename).read_bytes() == before


def first_last_frame_prompt(canvas_root):
    prompt, _ = build(canvas_root, 'minimax-h3-video', ['initial.png'])
    old = prompt.pop('108')
    old['class_type'] = 'MiniMaxH3ImageToVideo'
    inputs = old['inputs']
    inputs.pop('ref_images.ref_image_0')
    inputs['first_frame'] = ['97', 0]
    inputs['last_frame'] = ['97', 0]
    prompt['417'] = old
    prompt['419'] = copy.deepcopy(old)
    prompt['419']['inputs']['width'] = ['second-width', 0]
    prompt['419']['inputs']['height'] = ['second-height', 0]
    prompt['299']['inputs']['audio'] = ['audio-output', 0]
    return prompt


def test_h3_first_last_frame_two_conditions_preserve_audio_and_sampling(canvas_root):
    prompt = first_last_frame_prompt(canvas_root)
    before = copy.deepcopy(prompt)
    path = canvas_root / 'my_workflows' / WORKFLOWS['minimax-h3-video'].filename
    source = path.read_bytes()
    effective = bind_profile(prompt, WORKFLOWS['minimax-h3-video'],
                             {'prompt': '新的首尾帧视频', 'seed': 42, 'duration_seconds': 2, 'fps': 12},
                             'job_dual', ['actual-upload.png'])
    assert prompt['97']['inputs']['image'] == 'actual-upload.png'
    assert prompt['299']['inputs']['audio'] == before['299']['inputs']['audio']
    assert set(prompt) == set(before)
    for key in ('417', '419'):
        for name in ('width', 'height', 'length', 'first_frame', 'last_frame', 'clip', 'vae'):
            assert prompt[key]['inputs'].get(name) == before[key]['inputs'].get(name)
    for key, node in before.items():
        if node['class_type'] in {'SamplerCustomAdvanced', 'SplitSigmas', 'H3SigmaRefiner',
                                  'MinimaxH3LatentUpscaler3D', 'MiniMaxLowVRAMAttention'}:
            assert prompt[key] == node
    assert effective['frame_count'] % 17 == 5
    assert prompt['299']['inputs']['frame_rate'] == 12
    assert path.read_bytes() == source


@pytest.mark.parametrize('problem', ['mixed_model', 'different_length'])
def test_h3_rejects_incompatible_dual_conditions(canvas_root, problem):
    prompt = first_last_frame_prompt(canvas_root)
    if problem == 'mixed_model':
        prompt['419']['class_type'] = 'MiniMaxH3ReferenceToVideo'
    else:
        prompt['419']['inputs']['length'] = ['unrelated-length', 1]
    with pytest.raises(ValueError, match='混用了|来源不一致'):
        bind_profile(prompt, WORKFLOWS['minimax-h3-video'], {'prompt': 'test', 'seed': 1},
                     'job_reject', ['actual-upload.png'])


@pytest.mark.parametrize('count', [1, 2, 8])
def test_qwen_named_widgets_cannot_restore_saved_example_images(canvas_root, count):
    path = canvas_root / 'my_workflows' / '千问生图.json'
    graph = json.loads(path.read_text(encoding='utf-8'))
    for node in graph['nodes']:
        if node['type'] == 'LoadImage':
            node['widgets_values_named'] = {'image': 'example-must-not-run.png', 'upload': 'image'}
    path.write_text(json.dumps(graph), encoding='utf-8')
    before = path.read_bytes()
    refs = [f'actual-reference-{i}.png' for i in range(count)]
    prompt, _ = build(canvas_root, 'qwen-2.1-image', refs)
    assert sorted(n['inputs']['image'] for n in prompt.values() if n['class_type'] == 'LoadImage') == sorted(refs)
    assert path.read_bytes() == before


def test_video_duration_fps_alignment_and_image_parameter_rejection(canvas_root):
    prompt, effective = build(canvas_root, 'minimax-h3-video', ['uploaded.png'], fps=12, duration_seconds=2)
    assert effective['frame_count'] % 17 == 5
    assert effective['frame_count'] >= 24
    assert prompt['299']['inputs']['frame_rate'] == 12
    assert 'a * 12' in prompt['92']['inputs']['expression']
    for key in ('steps','cfg'):
        with pytest.raises(ValueError, match='生图采样参数'):
            build(canvas_root, 'minimax-h3-video', ['uploaded.png'], **{key:6})


def test_anima_preserves_original_sampler_values_unless_requested(canvas_root):
    prompt, effective = build(canvas_root, 'anima-2.9b-image')
    assert prompt['8']['inputs']['steps'] == 6
    assert prompt['28']['inputs']['denoise'] == 0
    assert prompt['16']['inputs']['width'] == 1296
    assert len(effective['workflow_snapshot']['samplers']) == 2


def test_reference_capability_is_enforced_before_scheduling(tmp_path):
    object.__setattr__(settings, 'creation_asset_dir', tmp_path/'assets')
    asset = creation.save_asset('reference.png','image/png',PNG_DATA_URL)
    with patch.object(creation, 'schedule_job') as schedule:
        with pytest.raises(ValueError, match='支持 0 张'):
            creation.create_job(CreationJobRequest(prompt='cat',workflow_id='anima-2.9b-image',reference_asset_ids=[asset['id']]))
        with pytest.raises(ValueError, match='参考图'):
            creation.create_job(CreationJobRequest(prompt='cat',media_type='video',workflow_id='minimax-h3-video'))
        schedule.assert_not_called()


def test_reference_conversion_uses_current_conversation_and_reuses_bytes(tmp_path):
    import base64
    object.__setattr__(settings, 'creation_asset_dir', tmp_path/'assets')
    data = base64.b64decode(PNG_DATA_URL.split(',')[1])
    attachment = archive_attachment('uploaded.png', data, 'image/png', 'image')
    mid = db.save_message('user','use this',conversation_id='desktop_agent_ref',attachments_json=json.dumps([attachment]))
    context = SimpleNamespace(conversation_id='desktop_agent_ref',source_message_id=mid)
    first = use_reference({},context)['asset']
    assert use_reference({},context)['asset']['id'] == first['id']
    assert 'path' not in first
    with pytest.raises(ValueError,match='当前会话'):
        use_reference({'message_id':mid},SimpleNamespace(conversation_id='another',source_message_id=mid))


def test_reference_rejects_traversal_in_message_metadata(tmp_path):
    mid = db.save_message('user','bad',conversation_id='test',attachments_json=json.dumps([
        {'kind':'image','url':'/agent-files/../../outside.png'}]))
    with pytest.raises(ValueError,match='附件目录'):
        use_reference({},SimpleNamespace(conversation_id='test',source_message_id=mid))


def test_missing_virtual_setter_fails_instead_of_inventing_nodes():
    graph = copy.deepcopy(FIXTURES['canvases']['MINIMAX-H3双采样_8G显存版.json'])
    graph['nodes'] = [n for n in graph['nodes'] if n['id'] != 12]
    with pytest.raises(ValueError,match='SetNode'):
        ui_workflow_to_api_prompt(graph,object_info=FIXTURES['schemas'])
