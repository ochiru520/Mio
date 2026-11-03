"""The worker-only reclaimer must preserve model calls and leave hot Tensor.to alone."""
import importlib.util
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.config import settings


def helper():
    spec = importlib.util.spec_from_file_location("test_tts_memory_helper", settings.agent_control_scripts_dir / "local_tts_memory.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reclaimer_preserves_load_and_module_to_and_restores_hooks():
    memory = helper()
    class Module:
        def to(self, *args, **kwargs):
            self.received = args, kwargs
            return self
    tensor_to = Mock(return_value="unchanged-tensor")
    load = Mock(return_value={"weights": "same-object"})
    torch = SimpleNamespace(load=load, nn=SimpleNamespace(Module=Module), Tensor=SimpleNamespace(to=tensor_to))
    old_to = Module.to
    kernel = SimpleNamespace(GetCurrentProcess=Mock(return_value=42))
    psapi = SimpleNamespace(EmptyWorkingSet=Mock(return_value=True))
    with patch.object(memory.gc, "collect") as collect, patch.object(memory.ctypes, "WinDLL", side_effect=lambda name, **kwargs: kernel if name == "kernel32" else psapi):
        reclaimer = memory.install(torch)
        try:
            model = Module()
            assert model.to("cuda:0", dtype="existing-dtype") is model
            assert model.received == (("cuda:0",), {"dtype": "existing-dtype"})
            assert torch.load("existing-weights", mmap=True) is load.return_value
            load.assert_called_once_with("existing-weights", mmap=True)
            assert torch.Tensor.to is tensor_to
            assert torch.Tensor.to() == "unchanged-tensor"
            assert reclaimer.status()["boundary_calls"] == 2
            assert collect.call_count == 2
            for call in psapi.EmptyWorkingSet.call_args_list:
                assert call.args == (42,)  # Never trim another PID/process handle.
        finally:
            reclaimer.close()
        assert torch.load is load and Module.to is old_to
        reclaimer.close()


def test_model_failure_propagates_and_cleanup_preserves_later_hooks():
    memory = helper()
    class Module:
        def to(self, *args, **kwargs):
            raise ValueError("original model error")
    torch = SimpleNamespace(load=Mock(), nn=SimpleNamespace(Module=Module))
    old_to = Module.to
    reclaimer = memory.install(torch)
    try:
        try:
            Module().to("cuda")
        except ValueError as error:
            assert str(error) == "original model error"
        else:
            raise AssertionError("Original exception was swallowed")
        later_load_hook = Mock()
        torch.load = later_load_hook
    finally:
        reclaimer.close()
    assert torch.load is later_load_hook and Module.to is old_to
