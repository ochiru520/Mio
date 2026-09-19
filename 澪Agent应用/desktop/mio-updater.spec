# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
root = Path(SPECPATH)
config = root / "generated" / "update_channel.json"
if not config.is_file():
    raise RuntimeError("Run publish_update.py prepare before packaging the updater.")
a = Analysis([str(root / "updater_entry.py")], pathex=[str(root)],
    binaries=[], datas=[(str(config), "desktop")], hiddenimports=[], hookspath=[],
    hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="MioUpdater", debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    disable_windowed_traceback=False, icon=str(root / "mio.ico"))
