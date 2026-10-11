"""Authenticated local worker. No HTTP endpoint and no remote control entry point."""
from __future__ import annotations
import base64
import ctypes
import io
import os
import time
import uuid
from multiprocessing.connection import Listener

_observations = {}
_owned_keys = []


class InputDeliveryError(ValueError):
    def __init__(self, message, input_sent):
        super().__init__(message)
        self.input_sent = input_sent


def _process_created(pid):
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = w.HANDLE
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.GetProcessTimes.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    created, exited, system, user = (ctypes.c_uint64() for _ in range(4))
    try:
        if not handle or not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(system), ctypes.byref(user)):
            raise ValueError('无法读取窗口进程身份。')
        return str(created.value)
    finally:
        if handle: kernel.CloseHandle(handle)


def _identity(window):
    pid = window.process_id()
    return {'hwnd': int(window.handle), 'pid': pid, 'created': _process_created(pid),
            'title': _uia_text(window.window_text())[:300]}


def _uia_text(text):
    # Some UIA providers return UTF-16 surrogate pairs as Python code points.
    # Normalize before truncation/JSON/SQLite; retain valid non-BMP characters.
    return str(text).encode('utf-16-le', errors='surrogatepass').decode('utf-16-le', errors='replace')


def _desktop():
    from pywinauto import Desktop
    return Desktop(backend='uia')


def windows():
    # Enumerating every application's UIA tree can block on an unrelated app.
    # Only resolve UIA after selecting a particular authorized window.
    return {'windows': _launched_windows(None)[:100]}


def _window(identity):
    window = _desktop().window(handle=int(identity['hwnd'])).wrapper_object()
    current = _identity(window)
    if current['pid'] != identity['pid'] or current['created'] != identity['created']:
        raise ValueError('窗口已被替换，请重新授权窗口。')
    if not window.is_visible(): raise ValueError('窗口已隐藏，请先显示窗口。')
    return window


def observe(identity, task_key):
    from PIL import ImageGrab
    window = _window(identity)
    _focus_window(window, identity)
    rect = window.rectangle()
    controls, wrappers, masks = [], {}, []
    # UIA may fail on elevated/custom surfaces; screenshot is still available.
    error = ''
    try:
        for item in [window, *window.descendants()][:300]:
            try:
                if not item.is_visible(): continue
                bounds = item.rectangle()
                try: password = bool(item.element_info.element.CurrentIsPassword)
                except Exception: password = item.element_info.control_type == 'Edit'
                if password:
                    masks.append((max(0,bounds.left-rect.left), max(0,bounds.top-rect.top), min(rect.width(),bounds.right-rect.left), min(rect.height(),bounds.bottom-rect.top)))
                    continue
                key = 'c' + str(len(controls) + 1)
                controls.append({'id': key, 'name': _uia_text(item.window_text())[:300], 'type': item.element_info.control_type,
                    'enabled': item.is_enabled(), 'bounds': [bounds.left - rect.left, bounds.top - rect.top,
                                                           bounds.right - rect.left, bounds.bottom - rect.top]})
                wrappers[key] = item
            except Exception: continue
    except Exception as exc: error = str(exc)[:200]
    image = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True)
    _assert_foreground(identity)
    from PIL import ImageDraw
    draw = ImageDraw.Draw(image)
    for box in masks:
        if box[2] > box[0] and box[3] > box[1]: draw.rectangle(box, fill='#555555')
    stream = io.BytesIO(); image.save(stream, format='PNG')
    observation_id = 'observation_' + uuid.uuid4().hex
    _observations[task_key] = {'id': observation_id, 'identity': _identity(window), 'time': time.monotonic(),
                              'wrappers': wrappers, 'rect': (rect.left, rect.top, rect.right, rect.bottom)}
    return {'observation_id': observation_id, 'window': _identity(window), 'controls': controls,
            'width': rect.width(), 'height': rect.height(), 'uia_error': error,
            'screenshot_base64': base64.b64encode(stream.getvalue()).decode()}


def act(identity, task_key, observation_id, operation, control_id='', x=0, y=0, end_x=0, end_y=0,
        text='', keys='', amount=3):
    from pywinauto import mouse, keyboard
    previous = _observations.get(task_key)
    if not previous or previous['id'] != observation_id or time.monotonic() - previous['time'] > 90:
        raise ValueError('观察已过期，请重新观察；不能重放旧坐标。')
    if any(previous['identity'][key] != identity[key] for key in ('hwnd','pid','created')): raise ValueError('动作窗口与观察窗口不一致。')
    window = _window(identity)
    if _identity(window)['title'] != previous['identity']['title']:
        raise ValueError('窗口内容或标签已改变，请重新观察。')
    rect = window.rectangle()
    if (rect.left, rect.top, rect.right, rect.bottom) != previous['rect']:
        raise ValueError('窗口位置改变，请重新观察。')
    item = previous['wrappers'].get(control_id) if control_id else None
    if control_id and item is None: raise ValueError('控件不属于当前观察。')
    _focus_window(window, identity)
    if _identity(window)['title'] != previous['identity']['title']:
        raise ValueError('窗口内容或标签已改变，请重新观察。')
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    if any(user32.GetAsyncKeyState(key) & 0x8000 for key in (0x10,0x11,0x12,0x5b,0x5c,1,2)):
        raise ValueError('检测到用户正在按键或拖动，请停止自动输入并交还用户。')
    if control_id:
        if not item.is_visible() or not item.is_enabled(): raise ValueError('控件已不可用，请重新观察。')
        bounds = item.rectangle()
        if not (rect.left <= bounds.left < bounds.right <= rect.right and rect.top <= bounds.top < bounds.bottom <= rect.bottom): raise ValueError('控件已移出授权窗口，请重新观察。')
    def point(px, py):
        if not (0 <= px < rect.width() and 0 <= py < rect.height()):
            raise ValueError('坐标不在已观察窗口内。')
        return (rect.left + px, rect.top + py)
    try:
        if operation in {'click', 'double_click'}:
            if item:
                item.click_input(double=operation == 'double_click')
            else:
                (mouse.double_click if operation == 'double_click' else mouse.click)(coords=point(x, y))
        elif operation == 'type':
            # ValuePattern sets literal text without interpreting braces or shortcuts.
            if item:
                item.set_focus()
                try: item.set_edit_text(text)
                except (AttributeError, NotImplementedError): _send_keys(text, literal=True, identity=identity)
            else: _send_keys(text, literal=True, identity=identity)
        elif operation == 'keys': _send_keys(keys, identity=identity)
        elif operation == 'scroll': mouse.scroll(coords=point(x, y), wheel_dist=amount)
        elif operation == 'drag':
            _drag(point(x,y),point(end_x,end_y))
        else: raise ValueError('不支持此桌面动作。')
    except InputDeliveryError as exc:
        if not exc.input_sent:
            raise
        return {'input_sent': True, 'operation': operation, 'observation_pending': True,
                'automatic_retry_safe': False,
                'message': '部分输入已发送，后续输入已停止：' + str(exc)[:300] + ' 请核对文档，不要重复整段输入。'}
    finally:
        release_inputs()
        _observations.pop(task_key, None)
    # A fresh observation is evidence, not a declaration of business success.
    try:
        result = observe(identity, task_key)
    except Exception as exc:
        return {'input_sent': True, 'operation': operation, 'observation_pending': True,
                'automatic_retry_safe': False,
                'message': '输入已发送，但后续观察失败：' + str(exc)[:300] + ' 请重新观察结果，勿重复输入。'}
    result['input_sent'] = True; result['operation'] = operation
    return result


def _assert_foreground(identity):
    user32 = ctypes.WinDLL('user32')
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    if int(user32.GetForegroundWindow() or 0) != identity['hwnd']:
        raise ValueError('目标窗口未获得焦点，停止输入。')


def _focus_window(window, identity):
    # UIA SetFocus selects a control; it does not always activate its top-level window.
    # Use the normal Windows foreground request, then verify instead of bypassing
    # the OS foreground restrictions with synthetic keys or unrelated windows.
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user32.SetForegroundWindow(ctypes.c_void_p(identity['hwnd']))
    window.set_focus()
    deadline = time.monotonic() + .5
    while True:
        try:
            _assert_foreground(identity)
            return
        except ValueError:
            if time.monotonic() >= deadline:
                raise ValueError('目标窗口未获得焦点，请先切到授权窗口后重试。')
            time.sleep(.025)


def _send_keys(text, literal=False, identity=None):
    from pywinauto import keyboard, win32structures, win32functions
    if literal:
        # One Unicode stream avoids mixing virtual-key punctuation with packets:
        # rich editors can process those two paths in a different order.
        normalized = text.replace('\r\n', '\n').replace('\r', '\n')
        if len(normalized) > 1000:
            raise ValueError('单次电脑输入最多1000字符，请分段输入并逐段核对。')
        batches = []
        for line_index, line in enumerate(normalized.split('\n')):
            if line_index:
                batches.append(list(keyboard.VirtualKeyAction(13).GetInput()))
            for char in line:
                raw = char.encode('utf-16-le')
                events = []
                for offset in range(0, len(raw), 2):
                    unit = int.from_bytes(raw[offset:offset + 2], 'little')
                    for flags in (4, 6):
                        event = win32structures.INPUT()
                        event.type = 1
                        event.ki.wVk = 0
                        event.ki.wScan = unit
                        event.ki.dwFlags = flags
                        events.append(event)
                batches.append(events)
        _deliver_batches(batches, identity, 0.02)
        return
    else:
        actions = keyboard.parse_keys(text, vk_packet=True)
        events, held, batches = [], set(), []
        for action in actions:
            if isinstance(action, keyboard.PauseAction): raise ValueError('快捷键不允许等待或悬置输入。')
            for event in action.GetInput():
                key = (event.ki.wVk,event.ki.wScan,event.ki.dwFlags & ~2)
                if event.ki.dwFlags & 2:
                    if key not in held: raise ValueError('快捷键不得释放非执行器按下的键。')
                    held.remove(key)
                else: held.add(key)
                events.append(event)
            if not held and events:
                batches.append(events)
                events = []
        if held: raise ValueError('快捷键须完整释放按键，不允许单独 DOWN。')
    _deliver_batches(batches, identity, 0.05)


def _deliver_batches(batches, identity, pause):
    sent = False
    for events in batches:
        try:
            if identity is not None:
                _assert_foreground(identity)
            _send_events(events)
            sent = True
            # Let a completed Unicode character or balanced shortcut settle.
            time.sleep(pause)
        except Exception as exc:
            raise InputDeliveryError(str(exc), sent or isinstance(exc, InputDeliveryError) and exc.input_sent) from exc


def _send_events(events):
    from pywinauto import win32structures, win32functions
    if not events: return
    array = (win32structures.INPUT * len(events))(*events)
    # Queue the complete balanced gesture in one call, without holding keys across RPCs.
    count = win32functions.SendInput(len(array),ctypes.byref(array),ctypes.sizeof(win32structures.INPUT))
    held = set()
    for event in events[:count]:
        key = (event.ki.wVk,event.ki.wScan,event.ki.dwFlags & ~2)
        if event.ki.dwFlags & 2: held.discard(key)
        else: held.add(key)
    _owned_keys.extend(held)
    if count != len(events): raise InputDeliveryError('部分输入未发送，已停止并释放执行器按键。', count > 0)


def _drag(start,end):
    from pywinauto import win32structures as s, win32functions as f
    user32 = ctypes.WinDLL('user32')
    left,top,width,height = (user32.GetSystemMetrics(i) for i in (76,77,78,79))
    events = []
    for point,flag in ((start,0),(start,2),(end,0),(end,4)):
        event = s.INPUT(); event.type = 0
        event.mi.dx = round((point[0]-left)*65535/max(1,width-1))
        event.mi.dy = round((point[1]-top)*65535/max(1,height-1))
        event.mi.dwFlags = 0x8000 | 0x4000 | 1 | flag
        events.append(event)
    array = (s.INPUT*len(events))(*events)
    count = f.SendInput(len(array),ctypes.byref(array),ctypes.sizeof(s.INPUT))
    if count != len(events):
        if count >= 2: user32.mouse_event(4,0,0,0,0)
        raise ValueError('拖动输入未完整发送。')


def release_inputs():
    if os.name != 'nt' or not _owned_keys: return
    from pywinauto import win32structures as s, win32functions as f
    events=[]
    for vk,scan,flags in _owned_keys:
        event=s.INPUT();event.type=1;event.ki.wVk=vk;event.ki.wScan=scan;event.ki.dwFlags=flags|2;events.append(event)
    _owned_keys.clear()
    array=(s.INPUT*len(events))(*events)
    f.SendInput(len(array),ctypes.byref(array),ctypes.sizeof(s.INPUT))


def _launched_processes(parent_pid, not_before=0, root_created=None):
    """Follow the launched process tree, including packaged-app child windows."""
    from ctypes import wintypes as w
    class Entry(ctypes.Structure):
        _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('pid', w.DWORD),
                    ('heap', ctypes.c_size_t), ('module', w.DWORD), ('threads', w.DWORD),
                    ('parent', w.DWORD), ('priority', w.LONG), ('flags', w.DWORD),
                    ('name', w.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    kernel.Process32FirstW.argtypes = [w.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), '无法读取启动进程树。')
    try:
        entries, entry = [], Entry()
        entry.size = ctypes.sizeof(entry)
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            entries.append((int(entry.pid), int(entry.parent)))
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    if root_created is None:
        return set()
    if any(pid == parent_pid for pid, _ in entries):
        try:
            if _process_created(parent_pid) != root_created:
                return set()
        except (ValueError, OSError):
            return set()
    owned = {parent_pid}
    while True:
        added = set()
        for pid, parent in entries:
            if parent in owned and pid not in owned:
                try:
                    if int(_process_created(pid)) >= not_before:
                        added.add(pid)
                except (ValueError, OSError):
                    continue
        if not added:
            return owned
        owned.update(added)


def _launched_windows(process_ids):
    """Discover native identities without a potentially blocking UIA lookup."""
    from ctypes import wintypes as w
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, w.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    user32.IsWindowVisible.argtypes = [w.HWND]
    user32.GetWindowTextLengthW.argtypes = [w.HWND]
    user32.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    result = []
    @callback_type
    def collect(hwnd, unused):
        pid = w.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if (process_ids is None or pid.value in process_ids) and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd):
            try:
                title = ctypes.create_unicode_buffer(301)
                user32.GetWindowTextW(hwnd, title, len(title))
                result.append({'hwnd': int(hwnd), 'pid': pid.value,
                               'created': _process_created(pid.value), 'title': title.value})
            except (ValueError, OSError):
                pass
        return True
    if not user32.EnumWindows(collect, 0):
        raise OSError(ctypes.get_last_error(), '无法读取启动窗口。')
    return result


def launch(executable, arguments, timeout=15):
    """Launch once; never attach an unrelated pre-existing app by its title."""
    import subprocess
    # Modern Notepad starts its editor in a child process. Waiting for the
    # launcher's idle state/top_window misses that editor and can exceed IPC.
    # Track the earliest possible creation time to reject recycled/old HWNDs.
    # Process-tree ownership is required in addition to this timestamp.
    started = ctypes.c_uint64()
    ctypes.WinDLL('kernel32').GetSystemTimeAsFileTime(ctypes.byref(started))
    application = subprocess.Popen([executable, *arguments])
    try:
        root_created = _process_created(application.pid)
    except (ValueError, OSError):
        root_created = None
    deadline = time.monotonic() + timeout
    while True:
        try:
            candidates = _launched_windows(_launched_processes(application.pid, started.value, root_created))
        except (ValueError, OSError):
            candidates = []
        for identity in candidates:
            if int(identity['created']) < started.value:
                continue
            if identity['pid'] == application.pid and identity['created'] != root_created:
                continue
            return {'window': identity, 'launched': True, 'process_pid': application.pid}
        if time.monotonic() >= deadline:
            return {'window': None, 'launched': True, 'process_pid': application.pid,
                    'launch_state': 'window_authorization_required', 'automatic_retry_safe': False,
                    'message': '软件已启动，但未取得属于启动进程的可授权窗口。请先选择窗口，勿重复启动。'}
        time.sleep(.1)


def dispatch(message):
    operation = message['method']; arguments = message.get('arguments', {})
    if operation == 'windows': return windows()
    if operation == 'observe': return observe(**arguments)
    if operation == 'act': return act(**arguments)
    if operation == 'launch':
        return launch(arguments['executable'], arguments.get('arguments', []))
    if operation == 'stop':
        release_inputs(); _observations.pop(arguments.get('task_key', ''), None)
        return {'stopped': True}
    raise ValueError('未知桌面 RPC 方法。')


def worker_main():
    if os.name != 'nt': return 2
    auth = bytes.fromhex(os.environ.pop('MIO_DESKTOP_RPC_KEY'))
    pipe = os.environ.pop('MIO_DESKTOP_RPC_PIPE')
    try:
        ctypes.WinDLL('user32').SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception: pass
    with Listener(pipe, family='AF_PIPE', authkey=auth) as server:
        while True:
            with server.accept() as connection:
                try:
                    message = connection.recv()
                    if message.get('method') == 'shutdown': return 0
                    connection.send({'ok': True, 'result': dispatch(message)})
                except Exception as exc:
                    release_inputs()
                    connection.send({'ok': False, 'error': str(exc)[:500]})
    return 0


if __name__ == '__main__':
    raise SystemExit(worker_main())
