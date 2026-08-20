# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


desktop = Path(SPECPATH)
frontend = desktop.parent
backend = frontend.parent / "私人AI日记系统" / "backend"
live2d_desktop = frontend / "live2d-desktop" / "release" / "win-unpacked"
build_identity = desktop / "generated" / "build_identity.json"
default_voice_reference = frontend.parent / "音色训练" / "materials" / "prepared" / "wav32k_v2" / "mio_v2_00.wav"

if not build_identity.is_file():
    raise RuntimeError("缺少构建身份文件；请通过 构建Windows应用.ps1 执行发布构建。")

datas = [
    (str(backend / "app" / "templates"), "app/templates"),
    (str(backend / "app" / "static"), "app/static"),
    (str(frontend / "dist"), "agent_frontend"),
    (str(frontend / "scripts"), "agent_scripts"),
    (str(desktop / "mio.ico"), "desktop"),
    (str(build_identity), "."),
]
if default_voice_reference.is_file():
    datas.append((str(default_voice_reference), "default_voice"))
datas += collect_data_files("tzdata")
datas += collect_data_files("webview")
if live2d_desktop.is_dir():
    datas.append((str(live2d_desktop), "live2d_desktop"))

hiddenimports = collect_submodules("webview")
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("dxcam")
hiddenimports += collect_submodules("mss")
hiddenimports += collect_submodules("websockets.sync")
hiddenimports += [
    "app.main",
    "app.desktop_pet",
    "app.desktop_pet_live2d",
    "app.companion_service",
    "app.genie_tts_service",
    "app.local_vision_service",
    "app.screen_capture",
    "app.screen_behavior_service",
    "app.screen_frame_processor",
    "app.screen_observer_process",
    "app.system_audio_service",
    "app.routes.agent",
    "app.routes.companion",
    "app.routes.onebot",
    "screen_preview",
    "PIL.ImageTk",
    "pystray",
    "pystray._win32",
    "tkinter",
]

a = Analysis(
    [str(desktop / "launcher.py")],
    pathex=[str(backend)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "PyQt6", "PySide2", "PySide6", "cefpython3", "gtk"],
    noarchive=True,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Mio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(desktop / "mio.ico"),
    version=str(desktop / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Mio",
)
