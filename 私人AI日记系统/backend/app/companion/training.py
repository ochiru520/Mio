"""Companion training; dependencies are explicitly injected."""
from __future__ import annotations

from typing import Any, Callable
from pathlib import Path
import json
import os
import subprocess


class TrainingService:
    """Domain implementation; constructor callbacks preserve facade test seams."""

    def __init__(self, *,
                 dep__runtime_voice_weights: Callable[..., Any],
                 dep_settings: Callable[..., Any],
                 ) -> None:
        self._dep__runtime_voice_weights = dep__runtime_voice_weights
        self._dep_settings = dep_settings

    def voice_training_status(self) -> dict[str, Any]:
        root = self._dep_settings().voice_training_dir
        source = root / "GPT-SoVITS"
        environment_python = root / ".voice-env" / "Scripts" / "python.exe"
        environment_marker = root / ".voice-env" / ".setup-complete"
        pretrained_dir = source / "GPT_SoVITS" / "pretrained_models"
        pretrained_marker = root / ".pretrained-v2-complete"
        required_models = (
            "chinese-hubert-base/config.json",
            "chinese-hubert-base/preprocessor_config.json",
            "chinese-hubert-base/pytorch_model.bin",
            "chinese-roberta-wwm-ext-large/config.json",
            "chinese-roberta-wwm-ext-large/pytorch_model.bin",
            "chinese-roberta-wwm-ext-large/tokenizer.json",
            "gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt",
            "gsv-v2final-pretrained/s2D2333k.pth",
            "gsv-v2final-pretrained/s2G2333k.pth",
        )
        present_models = [name for name in required_models if (pretrained_dir / name).is_file()]
        status_data: dict[str, Any] = {}
        status_kind = "setup"
        training_status: dict[str, Any] = {}
        for kind, filename in (("setup", "setup-status.json"), ("training", "training-status.json")):
            try:
                candidate = json.loads((root / filename).read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(candidate, dict):
                continue
            if kind == "training":
                training_status = candidate
            if str(candidate.get("updated_at") or "") >= str(status_data.get("updated_at") or ""):
                status_data = candidate
                status_kind = kind

        training_result: dict[str, Any] = {}
        try:
            candidate = json.loads((root / "training-result.json").read_text(encoding="utf-8-sig"))
            if isinstance(candidate, dict):
                training_result = candidate
        except (OSError, json.JSONDecodeError):
            pass
        gpt_model = Path(str(training_result.get("gpt_model") or ""))
        sovits_model = Path(str(training_result.get("sovits_model") or ""))
        trained_ready = bool(
            training_status.get("success")
            and training_status.get("stage") == "complete"
            and gpt_model.is_file()
            and sovits_model.is_file()
        )
        if not trained_ready:
            runtime_weights = self._dep__runtime_voice_weights()
            runtime_gpt = Path(runtime_weights["gpt"])
            runtime_sovits = Path(runtime_weights["sovits"])
            if runtime_gpt.is_file() and runtime_sovits.is_file():
                gpt_model = runtime_gpt
                sovits_model = runtime_sovits
                trained_ready = True
        stage = str(status_data.get("stage") or "")
        raw_message = str(status_data.get("message") or "")
        stage_messages = {
            "1/5": "正在 D 盘创建独立 Python 环境",
            "2/5": "正在准备安装工具和编译依赖",
            "3/5": "正在确认 RTX 4060 的 CUDA 版 PyTorch",
            "4/5": "正在安装 GPT-SoVITS 依赖",
            "5/5": "正在检查 CUDA 和训练程序依赖",
            "complete": "训练环境已安装，可以继续下载基础模型",
            "models-complete": "v2 基础模型已准备完成",
        }
        display_message = stage_messages.get(stage, raw_message)
        if status_kind == "training" and stage == "complete":
            display_message = "Mio 的第一版专属音色已训练完成"
        if stage == "models-error":
            display_message = f"基础模型下载失败：{raw_message}"
        elif stage == "error":
            display_message = f"训练环境初始化失败：{raw_message}"
        elif raw_message.startswith("Downloading: "):
            display_message = f"正在下载：{raw_message.removeprefix('Downloading: ')}"
        elif raw_message.startswith("Already present: "):
            display_message = f"已存在：{raw_message.removeprefix('Already present: ')}"
        material_dir = root / "materials" / "raw-japanese"
        material_count = sum(1 for path in material_dir.iterdir() if path.is_file()) if material_dir.is_dir() else 0
        return {
            "root": str(root),
            "source_ready": (source / "webui.py").is_file(),
            "environment_ready": environment_python.is_file() and environment_marker.is_file(),
            "pretrained_ready": pretrained_marker.is_file() and len(present_models) == len(required_models),
            "model_count": len(present_models),
            "expected_model_count": len(required_models),
            "material_count": material_count,
            "trained_ready": trained_ready,
            "gpt_model": str(gpt_model) if trained_ready else "",
            "sovits_model": str(sovits_model) if trained_ready else "",
            "stage": stage,
            "message": display_message,
            "updated_at": str(status_data.get("updated_at") or ""),
        }


    def launch_voice_training(self, action: str) -> dict[str, Any]:
        root = self._dep_settings().voice_training_dir
        if action == "folder":
            root.mkdir(parents=True, exist_ok=True)
            os.startfile(str(root))
            return self.voice_training_status()

        if action == "check":
            script = root / "检查训练环境.ps1"
            if not script.is_file():
                raise FileNotFoundError(f"找不到音色训练脚本：{script}")
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    "-Json",
                ],
                cwd=str(root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=45,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode != 0:
                raise OSError(completed.stderr.strip() or "音色训练环境检查失败。")
            return self.voice_training_status()

        scripts = {
            "setup": root / "安装训练环境.ps1",
            "models": root / "下载基础模型.ps1",
            "open": root / "启动音色训练.ps1",
        }
        script = scripts.get(action)
        if script is None:
            raise ValueError("不支持的音色训练操作。")
        if not script.is_file():
            raise FileNotFoundError(f"找不到音色训练脚本：{script}")
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            cwd=str(root),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        status = self.voice_training_status()
        status["launched_action"] = action
        status["message"] = {
            "setup": "初始化已启动，请查看弹出的进度窗口",
            "models": "基础模型下载已启动，请查看弹出的进度窗口",
            "open": "训练工具正在启动",
        }.get(action, status.get("message", ""))
        return status
