"""A probe owns its subprocess tree, including external Python/TTS workers."""
import ctypes
from ctypes import wintypes
import os
import signal
import subprocess
import time


def creation_flags():
    # Contain before executing Python: even a short-lived launcher can spawn workers.
    return (getattr(subprocess, 'CREATE_NO_WINDOW', 0) | 0x00000004) if os.name == 'nt' else 0


class ProbeProcess:
    def __init__(self, command, *, cwd):
        self.job = None
        self.process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
            creationflags=creation_flags(), start_new_session=os.name != 'nt')
        if os.name == 'nt':
            try:
                self._assign_job()
            except BaseException:
                self.close()
                raise

    def _assign_job(self):
        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                        ('flags', wintypes.DWORD), ('min_ws', ctypes.c_size_t),
                        ('max_ws', ctypes.c_size_t), ('active', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
        class Io(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', Io), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
        kernel = self._job_kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.job = kernel.CreateJobObjectW(None, None)
        info = Extended()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.job or not kernel.SetInformationJobObject(self.job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError('Unable to contain probe process')
        if not kernel.AssignProcessToJobObject(self.job, wintypes.HANDLE(int(self.process._handle))):
            raise OSError('Unable to assign probe process')
        ntdll = ctypes.WinDLL('ntdll')
        ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        ntdll.NtResumeProcess.restype = ctypes.c_long
        if ntdll.NtResumeProcess(wintypes.HANDLE(int(self.process._handle))) != 0:
            raise OSError('Unable to resume contained probe process')

    def _job_active_processes(self):
        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in ('user_time', 'kernel_time', 'period_user_time', 'period_kernel_time')] + [
                (name, wintypes.DWORD) for name in ('page_faults', 'total_processes', 'active_processes', 'terminated_processes')]
        info = Accounting()
        if not self._job_kernel.QueryInformationJobObject(self.job, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            raise OSError(ctypes.get_last_error(), 'Unable to confirm probe process tree exit')
        return info.active_processes

    def poll(self):
        return self.process.poll()

    def close(self, timeout=15):
        deadline = time.monotonic() + timeout
        if self.job:
            # Termination is asynchronous. Keep ownership until descendants have
            # exited, even when the launcher process has already returned.
            if not self._job_kernel.TerminateJobObject(self.job, 1):
                raise OSError(ctypes.get_last_error(), 'Unable to stop probe process tree')
        if self.process.poll() is None:
            if os.name == 'nt':
                self.process.kill()
            else:
                os.killpg(self.process.pid, signal.SIGKILL)
        elif os.name != 'nt':
            # The session leader may have exited while its workers are alive.
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.process.wait(timeout=max(.01, deadline - time.monotonic()))
        if self.job:
            while self._job_active_processes():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Probe process tree did not exit before the shutdown deadline')
                time.sleep(min(.02, remaining))
            if not self._job_kernel.CloseHandle(self.job):
                raise OSError(ctypes.get_last_error(), 'Unable to close probe process tree handle')
            self.job = None
