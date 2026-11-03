"""Windows tree shutdown must wait for descendants, not just the launcher."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from app.probe_process import ProbeProcess


@pytest.mark.skipif(os.name != 'nt', reason='Windows Job Object accounting')
def test_close_retains_job_until_all_descendants_exit():
    proc = ProbeProcess.__new__(ProbeProcess)
    proc.job = 123
    proc.process = Mock()
    proc.process.poll.return_value = 0  # Launcher already exited.
    terminating, descendants_exited = threading.Event(), threading.Event()
    proc._job_kernel = Mock()
    proc._job_kernel.TerminateJobObject.side_effect = lambda *_: terminating.set() or 1
    proc._job_kernel.CloseHandle.return_value = 1
    proc._job_active_processes = lambda: 0 if descendants_exited.is_set() else 1
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(proc.close)
        try:
            assert terminating.wait(2)
            assert not result.done()
            assert proc.job == 123
            proc._job_kernel.CloseHandle.assert_not_called()
        finally:
            descendants_exited.set()
        result.result(timeout=2)
    assert proc.job is None
    proc._job_kernel.CloseHandle.assert_called_once_with(123)
    proc.process.kill.assert_not_called()


@pytest.mark.skipif(os.name != 'nt', reason='Windows Job Object accounting')
def test_shutdown_timeout_keeps_owned_job_for_retry():
    proc = ProbeProcess.__new__(ProbeProcess)
    proc.job = 123
    proc.process = Mock()
    proc.process.poll.return_value = 0
    proc._job_kernel = Mock()
    proc._job_kernel.TerminateJobObject.return_value = 1
    proc._job_kernel.CloseHandle.return_value = 1
    proc._job_active_processes = lambda: 1
    with pytest.raises(TimeoutError):
        proc.close(timeout=.03)
    assert proc.job == 123
    proc._job_kernel.CloseHandle.assert_not_called()
    proc._job_active_processes = lambda: 0
    proc.close()
    assert proc.job is None
