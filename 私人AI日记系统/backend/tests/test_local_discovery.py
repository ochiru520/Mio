import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from app import agent_file_service as files, local_discovery as discovery
from app.config import settings
from app.agent_tool_service import _dispatch_read_tool, _dispatch_write_tool, ToolExecutionContext
from app.tool_registry import tool_registry
from test_runtime_safety_v2 import isolated


def graph():
    return {'1': {'class_type': 'EmptyImage', 'inputs': {'width':64,'height':64,'batch_size':1,'color':0}},
            '2': {'class_type': 'SaveImage','inputs': {'images':['1',0],'filename_prefix':'test'}}}


def setup_workflow(tmp_path):
    comfy=tmp_path/'ComfyUI'
    folder=comfy/'my_workflows'; folder.mkdir(parents=True)
    object.__setattr__(settings,'comfyui_root',comfy)
    target=folder/'人物抠图.json'; target.write_text(json.dumps(graph()),encoding='utf-8')
    return target


def test_discover_configured_workflow_and_read_without_broad_file_grant(tmp_path):
    target=setup_workflow(tmp_path)
    result=discovery.discover_workflows('人物')
    assert result['files'][0]['path']==str(target.resolve())
    assert discovery.read_workflow(str(target))['prompt']==graph()
    with pytest.raises(ValueError): files.resolve_read(str(target))


def test_search_authorized_nested_documents_and_refuse_outside(tmp_path):
    root=tmp_path/'docs'; (root/'nested').mkdir(parents=True)
    target=root/'nested'/'设计说明.md'; target.write_text('synthetic',encoding='utf-8')
    files.roots([str(root)])
    result=asyncio.run(_dispatch_read_tool('agent_search_files', {'query':'说明','limit':10}))
    assert result['files'][0]['path']==str(target)
    assert files.read_document(result['files'][0]['path'])['text']=='synthetic'
    with pytest.raises(ValueError): discovery.search_files(path=str(tmp_path))


def test_skip_hidden_and_runtime_trees_and_mark_partial_results(tmp_path):
    root=tmp_path/'docs'; root.mkdir()
    for name in ['.hidden','models']:
        (root/name).mkdir(); (root/name/'secret.json').write_text('{}')
    for i in range(3): (root/f'{i}.json').write_text('{}')
    files.roots([str(root)])
    result=discovery.search_files('*.json',limit=1)
    assert result['truncated'] and len(result['files'])==1
    assert 'secret' not in str(result['files'])


def test_private_and_ungranted_workflow_reads_rejected(tmp_path):
    setup_workflow(tmp_path)
    target=tmp_path/'private.json'; target.write_text(json.dumps(graph()))
    with pytest.raises(ValueError): discovery.read_workflow(str(target))
    target=settings.data_dir/'secret.json'; target.write_text(json.dumps(graph()))
    with pytest.raises(ValueError): discovery.read_workflow(str(target))


def test_non_workflow_and_oversize_json_not_listed(tmp_path):
    target=setup_workflow(tmp_path)
    (target.parent/'settings.json').write_text('{}')
    large=target.parent/'large.json'; large.write_bytes(b' '*2_000_001)
    assert [f['name'] for f in discovery.discover_workflows()['files']]==[target.name]
    with pytest.raises(ValueError,match='2 MB'): discovery.read_workflow(str(large))


def test_model_import_reads_source_and_preserves_original(tmp_path):
    target=setup_workflow(tmp_path)
    before=target.read_bytes()
    prepared={'media_type':'image','prompt':graph(),'bindings':{},'notes':[]}
    context=ToolExecutionContext(run_id='r',request_id='r',trace_id='r',conversation_id='desktop_agent_test',source_message_id=0,user_message='导入工作流',step_index=0)
    with patch('app.routes.creation.creation_preview_workflow',return_value=prepared), \
         patch('app.creation_custom.import_workflow',return_value={'id':'custom_test'}) as importer:
        result=asyncio.run(_dispatch_write_tool('creation_import_local_workflow',{'path':str(target),'label':''},context))
    assert result['workflow']['id']=='custom_test'
    assert importer.call_args.args[0].label=='人物抠图'
    assert target.read_bytes()==before


def test_new_tools_are_discoverable_and_validate_limits():
    for name in ['agent_search_files','creation_find_local_workflows','creation_read_local_workflow','creation_import_local_workflow']:
        assert tool_registry.require(name).native_schema()['function']['name']==name
    with pytest.raises(ValueError): tool_registry.require('agent_search_files').validate_arguments({'limit':101})


def test_import_api_graph_runs_real_parser_and_registry_without_model(tmp_path):
    from app import creation_custom
    target=setup_workflow(tmp_path)
    before=target.read_bytes()
    result=asyncio.run(discovery.import_local_workflow(str(target)))
    definition=next(d for d in creation_custom.definitions() if d.id==result['workflow']['id'])
    assert creation_custom.read_prompt(definition)==graph()
    assert target.read_bytes()==before


def test_junction_escape_is_rejected(tmp_path):
    target=setup_workflow(tmp_path)
    outside=tmp_path/'outside'; outside.mkdir()
    (outside/'secret.json').write_text(json.dumps(graph()))
    link=target.parent/'linked'
    if __import__('os').name != 'nt':
        link.symlink_to(outside,target_is_directory=True)
    else:
        # Use the Python Windows link API when permitted, otherwise the test needs a junction.
        try: link.symlink_to(outside,target_is_directory=True)
        except OSError:
            import _winapi
            _winapi.CreateJunction(str(outside),str(link))
    try:
        assert not any('secret' in item['name'] for item in discovery.discover_workflows()['files'])
        with pytest.raises(ValueError): discovery.read_workflow(str(link/'secret.json'))
    finally:
        if link.is_symlink(): link.unlink()
        else: link.rmdir()
