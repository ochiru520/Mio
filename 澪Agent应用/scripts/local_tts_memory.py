"""Collect temporary Python objects and trim this worker's resident CPU pages at model-loading boundaries.

No dtype/device/value changes, Tensor.to hooks, periodic trimming, or working-set limits.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import gc
import os
import threading


class ModelLoadReclaimer:
    def __init__(self, torch):
        self.torch = torch
        self.lock = threading.RLock()
        self.closed = False
        self.calls = self.windows_trims = self.last_windows_error = 0
        self.original_load, self.original_to = torch.load, torch.nn.Module.to

        def load(*args, **kwargs):
            self.reclaim()
            return self.original_load(*args, **kwargs)

        def to(module, *args, **kwargs):
            result = self.original_to(module, *args, **kwargs)
            self.reclaim()
            return result

        self.load_hook, self.to_hook = load, to
        torch.load, torch.nn.Module.to = load, to

    def reclaim(self):
        with self.lock:
            gc.collect()
            self.calls += 1
            if os.name == "nt":
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                psapi = ctypes.WinDLL("psapi", use_last_error=True)
                kernel.GetCurrentProcess.restype = wintypes.HANDLE
                psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
                psapi.EmptyWorkingSet.restype = wintypes.BOOL
                if psapi.EmptyWorkingSet(kernel.GetCurrentProcess()):
                    self.windows_trims += 1
                    self.last_windows_error = 0
                else:
                    self.last_windows_error = ctypes.get_last_error()

    def status(self):
        with self.lock:
            return {"boundary_calls": self.calls, "windows_self_trims": self.windows_trims,
                    "last_windows_error": self.last_windows_error,
                    "policy": "gc + current-process EmptyWorkingSet at torch.load/Module.to only; no hard cap, no Tensor.to hook"}

    def close(self):
        with self.lock:
            if self.closed:
                return
            # Do not overwrite a hook installed by another component later.
            if self.torch.load is self.load_hook:
                self.torch.load = self.original_load
            if self.torch.nn.Module.to is self.to_hook:
                self.torch.nn.Module.to = self.original_to
            self.closed = True


def install(torch):
    return ModelLoadReclaimer(torch)
