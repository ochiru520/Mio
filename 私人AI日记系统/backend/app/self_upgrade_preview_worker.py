"""One read-only candidate preview, synthetic state, no application lifespan jobs."""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--program-root', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--parent-pid', type=int, required=True)
    args = parser.parse_args()
    def parent_alive():
        if os.name == 'nt':
            import ctypes
            kernel = ctypes.windll.kernel32
            kernel.OpenProcess.restype = ctypes.c_void_p
            handle = kernel.OpenProcess(0x1000, False, args.parent_pid)
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                return bool(kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) and code.value == 259
            finally:
                kernel.CloseHandle(ctypes.c_void_p(handle))
        try:
            os.kill(args.parent_pid, 0); return True
        except OSError:
            return False
    def watchdog():
        deadline = time.monotonic() + 3600
        while parent_alive() and time.monotonic() < deadline:
            time.sleep(1)
        os._exit(0)
    threading.Thread(target=watchdog, daemon=True).start()
    program = args.program_root.resolve(strict=True)
    # Import the built modules, not the development working tree.
    sys.path.insert(0, str(program / '_internal'))
    os.environ['MIO_AGENT_FRONTEND_DIR'] = str(program / '_internal/agent_frontend')
    os.environ['MIO_BUILD_MANIFEST'] = str(program / '构建清单.json')
    os.environ['MIO_DISABLE_DOTENV'] = '1'
    if not os.environ.get('MIO_RUNTIME_ROOT'):
        raise RuntimeError('候选预览缺少隔离数据目录。')
    build = json.loads((program / '构建清单.json').read_text('utf-8'))
    from app import db, migration_service, onboarding_service, workspace_service
    db.init_db(); migration_service.run_migrations()
    # Seed only synthetic state, so real App bootstrap doesn't try to create a
    # conversation or enter a first-run wizard whose writes are disabled.
    workspace_service.initialize()
    if db.get_agent_conversation('desktop_agent_candidate_preview') is None:
        db.create_agent_conversation('desktop_agent_candidate_preview', '候选界面预览（合成数据）')
    onboarding_service._write_state({'version': onboarding_service.ONBOARDING_VERSION,
        'completed': True, 'mode': 'synthetic_candidate_preview', 'completed_at': db.now_iso(), 'existing_data': []})
    with db.get_conn() as conn:
        if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('候选合成数据库未通过完整性核对。')
    from app.main import app
    from app.routes import agent as agent_routes
    async def offline_qq_status():
        return {'enabled': False, 'connected': False, 'logged_in': False, 'diagnostic_code': 'candidate_preview',
                'diagnostic_message': '候选预览关闭QQ连接。', 'group_chat': {'enabled': False, 'group_ids': []}}
    # Real bootstrap otherwise probes a local NapCat endpoint. Use a clearly
    # synthetic offline status, while exercising all other built bootstrap code.
    agent_routes._qq_status = offline_qq_status
    from starlette.responses import JSONResponse

    @app.middleware('http')
    async def preview_boundary(request, call_next):
        path = request.url.path
        if path == '/health':
            return JSONResponse({'status': 'ok', 'build_id': build['build_id'], 'preview': True})
        # API reads use synthetic data only. Provider probes, file/terminal/desktop,
        # extension execution and every write endpoint are unavailable in preview.
        allowed = ('/agent-app', '/static', '/api/onboarding/status', '/api/agent/bootstrap', '/api/agent/settings',
                   '/api/agent/conversations', '/api/agent/messages', '/api/agent/context-usage', '/api/agent/projects',
                   '/api/agent/tasks', '/api/agent/work', '/api/agent/models', '/api/agent/tools', '/api/agent/skills', '/api/self-upgrade/status',
                   '/api/settings/runtime', '/api/settings/profile', '/api/settings/avatar', '/api/settings/user-avatar',
                   '/api/settings/chat-background', '/api/companion/chat-settings', '/api/companion/status', '/api/companion/avatar',
                   '/api/workspace', '/api/extensions', '/api/execution/policy', '/api/agent/surface',
                   '/api/agent/work', '/api/artifacts')
        if request.method not in {'GET', 'HEAD'} or not any(path == value or path.startswith(value + '/') for value in allowed):
            return JSONResponse({'detail': '候选预览使用合成数据，外部调用、电脑控制及写操作已关闭。'}, status_code=403)
        return await call_next(request)

    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=args.port, lifespan='off', log_level='warning')


if __name__ == '__main__':
    main()
