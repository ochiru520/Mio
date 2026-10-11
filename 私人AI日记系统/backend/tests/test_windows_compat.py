import pytest
from app.windows_compat import evaluate


@pytest.mark.parametrize('major,build,arch,bits,expected', [
    (10,19045,'AMD64',64,True), (10,22631,'AMD64',64,True),
    (10,19044,'AMD64',64,False), (6,7601,'AMD64',64,False),
    (10,19045,'x86',32,False), (10,19045,'ARM64',64,False),
    (10,22631,'ARM64',64,True),
])
def test_windows_support_matrix(major, build, arch, bits, expected):
    value = evaluate(system='Windows', major=major, build=build, architecture=arch, bits=bits)
    assert value['supported'] is expected
    assert bool(value['reason']) is not expected


def test_old_windows_is_not_labeled_windows_10():
    assert evaluate(system='Windows',major=6,build=7601,architecture='AMD64',bits=64)['os'] == 'Windows'


@pytest.mark.parametrize('native,build,supported', [
    ('amd64', 19045, True), ('arm64', 19045, False),
    ('arm64', 22631, True), ('', 19045, False),
])
def test_embedded_snapshot_uses_native_architecture(monkeypatch, native, build, supported):
    from types import SimpleNamespace
    from app import windows_compat as compat
    monkeypatch.setattr(compat.sys, 'platform', 'win32')
    monkeypatch.setattr(compat.sys, 'getwindowsversion', lambda: SimpleNamespace(major=10, build=build), raising=False)
    monkeypatch.setattr(compat, '_native_architecture', lambda: native)
    monkeypatch.setattr(compat.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(compat.platform, 'machine', lambda: '')
    monkeypatch.delenv('PROCESSOR_ARCHITEW6432', raising=False)
    monkeypatch.delenv('PROCESSOR_ARCHITECTURE', raising=False)
    result = compat.snapshot()
    assert result['architecture'] == native
    assert result['supported'] is supported
