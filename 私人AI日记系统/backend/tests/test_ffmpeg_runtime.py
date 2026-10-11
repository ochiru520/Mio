from types import SimpleNamespace

from app import document_service, media_compose_service, ffmpeg_runtime


def test_parsing_and_assembly_share_managed_voice_binary(tmp_path, monkeypatch):
    monkeypatch.delenv('MIO_FFMPEG_BINARY', raising=False)
    monkeypatch.setattr(ffmpeg_runtime, 'settings', SimpleNamespace(voice_training_dir=tmp_path))
    managed = tmp_path / 'cache' / 'bin' / 'ffmpeg.exe'
    managed.parent.mkdir(parents=True)
    managed.write_bytes(b'fixture-binary')
    assert document_service._ffmpeg_path() == managed.resolve()
    assert media_compose_service.binary() == str(managed.resolve())


def test_explicit_binary_overrides_managed_installation(tmp_path, monkeypatch):
    configured = tmp_path / 'custom-ffmpeg.exe'
    configured.write_bytes(b'configured-binary')
    monkeypatch.setenv('MIO_FFMPEG_BINARY', str(configured))
    assert document_service._ffmpeg_path() == configured.resolve()
    assert media_compose_service.binary() == str(configured.resolve())


def test_missing_ffmpeg_is_installable_and_never_uses_author_tools(tmp_path, monkeypatch):
    monkeypatch.delenv('MIO_FFMPEG_BINARY', raising=False)
    monkeypatch.setattr(ffmpeg_runtime, 'settings', SimpleNamespace(voice_training_dir=tmp_path))
    monkeypatch.setattr(ffmpeg_runtime.shutil, 'which', lambda name: None)
    assert ffmpeg_runtime.installation_status()['status'] == 'missing'
    from app import dependency_installer
    definition = dependency_installer._dependency_def('ffmpeg')
    assert definition['kind'] == 'script'
    assert definition['script'] == 'install-ffmpeg.ps1'
