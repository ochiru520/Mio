import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app import mio_profile, agent_loop_service


def test_public_analysis_style_roundtrip_and_planner_context():
    style = '用中文短段落解释正在核对什么，沿用角色称呼。'
    with tempfile.TemporaryDirectory() as directory, patch.object(mio_profile, '_profile_path', return_value=Path(directory) / 'profile.json'):
        original = mio_profile.load_mio_profile()
        saved = mio_profile.save_mio_profile_from_settings({'speaking_style': {**original['speaking_style'], 'analysis_style': style}})
        reloaded = mio_profile.load_mio_profile()
        assert reloaded['speaking_style']['analysis_style'] == style
        assert reloaded['identity'] == original['identity']
        assert reloaded['speaking_style']['tone'] == original['speaking_style']['tone']
        with patch.object(agent_loop_service, '_planner_history', return_value='无'):
            messages = agent_loop_service._planner_messages('synthetic', '检查文件', 1)
        assert style in messages[0]['content']
        assert 'speaking_style.analysis_style' in messages[0]['content']
        assert '不改变实际判断' in messages[0]['content']
        before = (Path(directory) / 'profile.json').read_bytes()
        with pytest.raises(ValueError, match='2000'):
            mio_profile.save_mio_profile_from_settings({'speaking_style': {'analysis_style': '字' * 2001}})
        assert (Path(directory) / 'profile.json').read_bytes() == before
