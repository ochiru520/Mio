"""Companion voice packages; dependencies are explicitly injected."""
from __future__ import annotations

from typing import Any, Callable
from io import BytesIO
from pathlib import Path
import base64
import binascii
from datetime import datetime
import hashlib
import json
import re
import shutil
import time
import uuid
import yaml
import zipfile


class VoicePackagesService:
    """Domain implementation; constructor callbacks preserve facade test seams."""

    def __init__(self, *,
                 dep_DEFAULT_VOICE_PROFILE_ID: Callable[..., Any],
                 dep_MIO_VOICE_ENGINE: Callable[..., Any],
                 dep_SO_VITS_SVC_ENGINE: Callable[..., Any],
                 dep_SPEECH_EMOTION_LABELS: Callable[..., Any],
                 dep_VOICE_PACKAGE_DISK_RESERVE_BYTES: Callable[..., Any],
                 dep_VOICE_PACKAGE_FORMAT: Callable[..., Any],
                 dep_VOICE_PACKAGE_MAX_COMPRESSION_RATIO: Callable[..., Any],
                 dep_VOICE_PACKAGE_MAX_ENTRIES: Callable[..., Any],
                 dep_VOICE_PACKAGE_MAX_MANIFEST_BYTES: Callable[..., Any],
                 dep_VOICE_REFERENCE_ALLOWED_EXTENSIONS: Callable[..., Any],
                 dep__voice_weight_path: Callable[..., Any],
                 dep_load_config: Callable[..., Any],
                 dep_save_config: Callable[..., Any],
                 dep_settings: Callable[..., Any],
                 ) -> None:
        self._dep_DEFAULT_VOICE_PROFILE_ID = dep_DEFAULT_VOICE_PROFILE_ID
        self._dep_MIO_VOICE_ENGINE = dep_MIO_VOICE_ENGINE
        self._dep_SO_VITS_SVC_ENGINE = dep_SO_VITS_SVC_ENGINE
        self._dep_SPEECH_EMOTION_LABELS = dep_SPEECH_EMOTION_LABELS
        self._dep_VOICE_PACKAGE_DISK_RESERVE_BYTES = dep_VOICE_PACKAGE_DISK_RESERVE_BYTES
        self._dep_VOICE_PACKAGE_FORMAT = dep_VOICE_PACKAGE_FORMAT
        self._dep_VOICE_PACKAGE_MAX_COMPRESSION_RATIO = dep_VOICE_PACKAGE_MAX_COMPRESSION_RATIO
        self._dep_VOICE_PACKAGE_MAX_ENTRIES = dep_VOICE_PACKAGE_MAX_ENTRIES
        self._dep_VOICE_PACKAGE_MAX_MANIFEST_BYTES = dep_VOICE_PACKAGE_MAX_MANIFEST_BYTES
        self._dep_VOICE_REFERENCE_ALLOWED_EXTENSIONS = dep_VOICE_REFERENCE_ALLOWED_EXTENSIONS
        self._dep__voice_weight_path = dep__voice_weight_path
        self._dep_load_config = dep_load_config
        self._dep_save_config = dep_save_config
        self._dep_settings = dep_settings

    def save_voice_reference_data_url(self, data_url: str, filename: str, *, profile_id: str = "") -> Path:
        if "," not in data_url:
            raise ValueError("参考音频数据格式不正确。")
        header, encoded = data_url.split(",", 1)
        if ";base64" not in header or not header.startswith(("data:audio/", "data:video/", "data:application/")):
            raise ValueError("请选择音频文件作为参考音色。")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError, binascii.Error) as exc:
            raise ValueError("参考音频无法读取。") from exc
        if not raw:
            raise ValueError("参考音频不能为空。")
        if len(raw) > 40 * 1024 * 1024:
            raise ValueError("参考音频不能超过 40MB。")
        extension = Path(filename).suffix.lower()
        allowed = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac", ".wma"}
        if extension not in allowed:
            extension = ".wav" if "wav" in header.lower() else ".mp3"
        config = self._dep_load_config()
        selected_profile_id = str(profile_id or config.get("default_voice_profile_id") or self._dep_DEFAULT_VOICE_PROFILE_ID())
        if selected_profile_id not in config["voice_profiles"]:
            raise ValueError("要更新的音色配置不存在。")
        safe_profile_id = re.sub(r"[^A-Za-z0-9._-]+", "-", selected_profile_id).strip("-.") or "voice"
        self._dep_settings().companion_dir.mkdir(parents=True, exist_ok=True)
        target = self._dep_settings().companion_dir / f"音色参考-{safe_profile_id}{extension}"
        temporary = self._dep_settings().companion_dir / f"音色参考-{safe_profile_id}.tmp{extension}"
        temporary.write_bytes(raw)
        temporary.replace(target)
        for existing in self._dep_settings().companion_dir.glob(f"音色参考-{safe_profile_id}.*"):
            if existing != target:
                existing.unlink(missing_ok=True)
        profiles = dict(config.get("voice_profiles") or {})
        profile = dict(profiles[selected_profile_id])
        profile["gpt_sovits_ref_audio"] = str(target.resolve())
        profiles[selected_profile_id] = profile
        changes: dict[str, Any] = {"voice_profiles": profiles}
        if selected_profile_id == config.get("default_voice_profile_id"):
            changes["gpt_sovits_ref_audio"] = str(target.resolve())
        self._dep_save_config(changes)
        return target


    def _report_voice_import_progress(self,
        progress: VoiceImportProgress | None,
        *,
        phase: str,
        message: str,
        processed_bytes: int = 0,
        total_bytes: int = 0,
    ) -> None:
        if progress is None:
            return
        percent = int(processed_bytes * 100 / total_bytes) if total_bytes > 0 else 0
        progress({
            "phase": phase,
            "message": message,
            "processed_bytes": max(0, int(processed_bytes)),
            "total_bytes": max(0, int(total_bytes)),
            "percent": max(0, min(99, percent)),
        })


    def export_voice_package(self, profile_id: str) -> bytes:
        """把单个音色导出为只含数据的 ZIP 音色包（manifest + 可选参考音频）。"""
        config = self._dep_load_config()
        profiles = config.get("voice_profiles") or {}
        if profile_id not in profiles:
            raise ValueError("要导出的音色不存在。")
        profile = dict(profiles[profile_id])
        manifest: dict[str, Any] = {
            "format": self._dep_VOICE_PACKAGE_FORMAT(),
            "format_version": 1,
            "name": str(profile.get("name") or profile_id).strip()[:80],
            "engine": str(profile.get("engine") or self._dep_MIO_VOICE_ENGINE()),
            "prompt_text": str(profile.get("gpt_sovits_prompt_text") or ""),
            "prompt_language": str(profile.get("gpt_sovits_prompt_language") or "zh"),
            "text_language": str(profile.get("gpt_sovits_text_language") or "auto"),
            "translate_to_japanese": bool(profile.get("gpt_sovits_translate_to_japanese", False)),
            "use_emotion_references": bool(profile.get("use_emotion_references", True)),
            "gpt_weights_name": Path(str(profile.get("gpt_sovits_gpt_weights") or "")).name,
            "sovits_weights_name": Path(str(profile.get("gpt_sovits_sovits_weights") or "")).name,
            "reference_audio": "",
            "license": "",
            "creator": "",
            "description": "",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "sha256": "",
        }
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            ref_audio = str(profile.get("gpt_sovits_ref_audio") or "")
            if ref_audio:
                path = Path(ref_audio)
                if path.is_file():
                    digest = hashlib.sha256()
                    with path.open("rb") as source:
                        while True:
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            digest.update(chunk)
                    manifest["reference_audio"] = path.name
                    manifest["sha256"] = digest.hexdigest()
                    archive.write(path, f"reference_audio/{path.name}")
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        return buffer.getvalue()


    def _import_so_vits_svc_archive(self,
        archive: zipfile.ZipFile,
        normalized_infos: dict[str, zipfile.ZipInfo],
        *,
        filename: str,
        progress: VoiceImportProgress | None = None,
    ) -> dict[str, Any]:
        from .. import so_vits_svc_service

        config_candidates = [name for name in normalized_infos if Path(name).name.lower() == "config.json"]
        model_candidates = [
            name for name in normalized_infos
            if re.fullmatch(r"G_[^/\\]+\.pth", Path(name).name, flags=re.IGNORECASE)
        ]
        if not config_candidates or not model_candidates:
            raise ValueError("这不是 Mio 交换音色包，也没有识别到可用的第三方音色模型（当前支持 So-VITS-SVC 4.1）。")
        config_entry = min(config_candidates, key=lambda name: len(Path(name).parts))
        config_parent = str(Path(config_entry).parent).replace("\\", "/").strip(".")
        same_parent_models = [
            name for name in model_candidates
            if str(Path(name).parent).replace("\\", "/").strip(".") == config_parent
        ]
        model_entry = max(same_parent_models or model_candidates, key=lambda name: normalized_infos[name].file_size)
        config_info = normalized_infos[config_entry]
        model_info = normalized_infos[model_entry]
        if config_info.file_size > self._dep_VOICE_PACKAGE_MAX_MANIFEST_BYTES():
            raise ValueError("第三方音色 config.json 异常过大，已拦截。")
        if model_info.file_size < 1024 * 1024:
            raise ValueError("第三方音色主模型异常过小，文件可能不完整。")
        ratio = model_info.file_size / max(1, model_info.compress_size)
        if ratio > self._dep_VOICE_PACKAGE_MAX_COMPRESSION_RATIO():
            raise ValueError("第三方音色主模型压缩比异常，疑似 ZIP 炸弹。")
        try:
            model_config = json.loads(archive.read(config_info).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("第三方音色 config.json 无法读取。") from None
        if not isinstance(model_config, dict):
            raise ValueError("第三方音色 config.json 格式不正确。")
        model_section = model_config.get("model") if isinstance(model_config.get("model"), dict) else {}
        speakers = model_config.get("spk") if isinstance(model_config.get("spk"), dict) else {}
        speech_encoder = str(model_section.get("speech_encoder") or "").strip().lower()
        if speech_encoder not in {"vec768l12", "vec256l9"} or not speakers:
            raise ValueError("第三方音色不是当前可兼容的 So-VITS-SVC 4.1 模型。")
        speaker = str(next(iter(speakers))).strip()[:80]
        if not speaker:
            raise ValueError("第三方音色没有有效的说话人名称。")

        config = self._dep_load_config()
        profiles = dict(config.get("voice_profiles") or {})
        if len(profiles) >= 20:
            raise ValueError("音色数量已达上限（20 个），请先删除一个再导入。")
        base_profile_id = ""
        current_default = str(config.get("default_voice_profile_id") or "")
        if isinstance(profiles.get(current_default), dict) and profiles[current_default].get("engine") == self._dep_MIO_VOICE_ENGINE():
            base_profile_id = current_default
        if not base_profile_id:
            base_profile_id = next(
                (profile_id for profile_id, profile in profiles.items() if isinstance(profile, dict) and profile.get("engine") == self._dep_MIO_VOICE_ENGINE()),
                "",
            )
        if not base_profile_id:
            raise ValueError("导入 So-VITS-SVC 前请先保留一个 GPT-SoVITS 基础音色，转换文字时需要它先生成声源。")

        new_id = self._new_voice_profile_id(profiles)
        package_stem = Path(filename or "").stem.strip()
        display_name = (speaker if speaker else package_stem or "第三方音色")[:80]
        third_party_root = self._dep_settings().companion_dir / "第三方音色"
        target_dir = third_party_root / new_id
        staging_dir = third_party_root / f".{new_id}.{uuid.uuid4().hex}.importing"
        self._dep_settings().companion_dir.mkdir(parents=True, exist_ok=True)
        free_bytes = shutil.disk_usage(self._dep_settings().companion_dir).free
        required_bytes = int(model_info.file_size + config_info.file_size)
        if required_bytes > max(0, free_bytes - self._dep_VOICE_PACKAGE_DISK_RESERVE_BYTES()):
            raise ValueError("磁盘空间不足，无法导入第三方音色主模型。")
        third_party_root.mkdir(parents=True, exist_ok=True)
        staging_dir.mkdir(parents=False, exist_ok=False)
        total_copy_bytes = int(config_info.file_size + model_info.file_size)
        copied_bytes = 0
        try:
            target_config = staging_dir / "config.json"
            target_model = staging_dir / Path(model_entry).name
            with archive.open(config_info, "r") as source_config, target_config.open("wb") as output:
                while True:
                    chunk = source_config.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    copied_bytes += len(chunk)
                    self._report_voice_import_progress(
                        progress,
                        phase="extracting",
                        message="正在复制音色模型",
                        processed_bytes=copied_bytes,
                        total_bytes=total_copy_bytes,
                    )
            with archive.open(model_info, "r") as source_model, target_model.open("wb") as output:
                while True:
                    chunk = source_model.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    copied_bytes += len(chunk)
                    self._report_voice_import_progress(
                        progress,
                        phase="extracting",
                        message="正在复制音色模型",
                        processed_bytes=copied_bytes,
                        total_bytes=total_copy_bytes,
                    )
            staging_dir.replace(target_dir)
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

        profile: dict[str, Any] = {
            "name": display_name,
            "engine": self._dep_SO_VITS_SVC_ENGINE(),
            "gpt_sovits_ref_audio": "",
            "gpt_sovits_prompt_text": "",
            "gpt_sovits_prompt_language": "zh",
            "gpt_sovits_text_language": "auto",
            "gpt_sovits_translate_to_japanese": False,
            "gpt_sovits_gpt_weights": "",
            "gpt_sovits_sovits_weights": "",
            "use_emotion_references": False,
            "so_vits_svc_model_path": str((target_dir / Path(model_entry).name).resolve()),
            "so_vits_svc_config_path": str((target_dir / "config.json").resolve()),
            "so_vits_svc_speaker": speaker,
            "so_vits_svc_pitch": 0,
            "so_vits_svc_auto_predict_f0": bool(model_section.get("use_automatic_f0_prediction", True)),
            "so_vits_svc_noise_scale": 0.4,
            "so_vits_svc_base_profile_id": base_profile_id,
            "source_package_name": Path(filename or "第三方音色.zip").name[:260],
            "source_license": "未声明（仅导入可信且有权使用的模型）",
        }
        runtime = so_vits_svc_service.runtime_status()
        activated = bool(runtime["ready"])
        if activated:
            try:
                self._report_voice_import_progress(
                    progress,
                    phase="validating",
                    message="正在独立进程中用 CPU 加载验证音色模型",
                    processed_bytes=99,
                    total_bytes=100,
                )
                so_vits_svc_service.probe_profile(profile, device="cpu")
            except (ValueError, OSError, TimeoutError) as exc:
                shutil.rmtree(target_dir, ignore_errors=True)
                raise ValueError(f"第三方音色模型加载验证失败：{exc}") from exc
        profiles[new_id] = profile
        try:
            self._report_voice_import_progress(
                progress,
                phase="saving",
                message="正在保存音色设置",
                processed_bytes=99,
                total_bytes=100,
            )
            self._dep_save_config({
                "voice_profiles": profiles,
                "default_voice_profile_id": new_id if activated else base_profile_id,
                "voice_engine": self._dep_MIO_VOICE_ENGINE(),
            })
        except Exception:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise
        return {
            "id": new_id,
            "name": display_name,
            "engine": self._dep_SO_VITS_SVC_ENGINE(),
            "speaker": speaker,
            "base_profile_id": base_profile_id,
            "model_size": model_info.file_size,
            "runtime": "ready" if activated else "missing",
            "activated": activated,
            "runtime_missing": list(runtime.get("missing") or []),
        }


    def _import_voice_package_source(self,
        source: BytesIO | Path,
        *,
        filename: str = "",
        progress: VoiceImportProgress | None = None,
    ) -> dict[str, Any]:
        """校验并流式导入 ZIP；不按包体积设业务上限，也不把参考音频整体读入内存。"""
        target: Path | None = None
        temporary: Path | None = None
        try:
            self._report_voice_import_progress(progress, phase="checking", message="正在检查音色包")
            with zipfile.ZipFile(source) as archive:
                infos = archive.infolist()
                if len(infos) > self._dep_VOICE_PACKAGE_MAX_ENTRIES():
                    raise ValueError("音色包条目过多，疑似异常压缩包。")
                normalized_infos = {info.filename.replace("\\", "/"): info for info in infos}
                if len(normalized_infos) != len(infos):
                    raise ValueError("音色包包含重复路径，已拦截。")
                for name in normalized_infos:
                    if name.startswith("/") or name.startswith("\\") or ".." in name.split("/"):
                        raise ValueError("音色包包含非法路径，已拦截。")
                if "manifest.json" not in normalized_infos:
                    return self._import_so_vits_svc_archive(
                        archive,
                        normalized_infos,
                        filename=filename,
                        progress=progress,
                    )
                manifest_info = normalized_infos["manifest.json"]
                if manifest_info.file_size > self._dep_VOICE_PACKAGE_MAX_MANIFEST_BYTES():
                    raise ValueError("manifest.json 异常过大，已拦截。")
                try:
                    manifest_raw = archive.read(manifest_info)
                    manifest = json.loads(manifest_raw.decode("utf-8"))
                except (KeyError, UnicodeDecodeError):
                    raise ValueError("manifest.json 无法读取。") from None
                if not isinstance(manifest, dict):
                    raise ValueError("manifest.json 格式不正确。")
                if str(manifest.get("format") or "") != self._dep_VOICE_PACKAGE_FORMAT():
                    raise ValueError("这不是 Mio 音色包格式，已拦截。")
                name = str(manifest.get("name") or "").strip()[:80]
                if not name:
                    raise ValueError("音色包缺少名称。")
                audio_name = str(manifest.get("reference_audio") or "").strip()
                audio_info: zipfile.ZipInfo | None = None
                if audio_name:
                    if Path(audio_name).name != audio_name or "/" in audio_name or "\\" in audio_name:
                        raise ValueError("参考音频名称包含非法路径。")
                    entry = "reference_audio/" + audio_name.replace("\\", "/")
                    if entry not in normalized_infos:
                        raise ValueError(f"音色包里找不到参考音频：{audio_name}")
                    audio_info = normalized_infos[entry]
                    extension = Path(audio_name).suffix.lower()
                    if extension not in self._dep_VOICE_REFERENCE_ALLOWED_EXTENSIONS():
                        raise ValueError("参考音频格式不受支持。")

                    ratio = audio_info.file_size / max(1, audio_info.compress_size)
                    if ratio > self._dep_VOICE_PACKAGE_MAX_COMPRESSION_RATIO():
                        raise ValueError("参考音频压缩比异常，疑似 ZIP 炸弹。")

                config = self._dep_load_config()
                profiles = dict(config.get("voice_profiles") or {})
                if len(profiles) >= 20:
                    raise ValueError("音色数量已达上限（20 个），请先删除一个再导入。")
                new_id = self._new_voice_profile_id(profiles)
                profile: dict[str, Any] = {
                    "name": name,
                    "engine": str(manifest.get("engine") or self._dep_MIO_VOICE_ENGINE()),
                    "gpt_sovits_ref_audio": "",
                    "gpt_sovits_prompt_text": str(manifest.get("prompt_text") or ""),
                    "gpt_sovits_prompt_language": str(manifest.get("prompt_language") or "zh"),
                    "gpt_sovits_text_language": str(manifest.get("text_language") or "auto"),
                    "gpt_sovits_translate_to_japanese": bool(manifest.get("translate_to_japanese", False)),
                    "gpt_sovits_gpt_weights": "",
                    "gpt_sovits_sovits_weights": "",
                    "use_emotion_references": bool(manifest.get("use_emotion_references", True)),
                }
                if audio_info is not None:
                    self._dep_settings().companion_dir.mkdir(parents=True, exist_ok=True)
                    free_bytes = shutil.disk_usage(self._dep_settings().companion_dir).free
                    if audio_info.file_size > max(0, free_bytes - self._dep_VOICE_PACKAGE_DISK_RESERVE_BYTES()):
                        raise ValueError("磁盘空间不足，无法导入参考音频。")
                    safe_profile_id = re.sub(r"[^A-Za-z0-9._-]+", "-", new_id).strip("-.") or "voice"
                    target = self._dep_settings().companion_dir / f"音色参考-{safe_profile_id}{Path(audio_name).suffix.lower()}"
                    temporary = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
                    digest = hashlib.sha256()
                    copied_bytes = 0
                    with archive.open(audio_info, "r") as source_audio, temporary.open("wb") as output:
                        while True:
                            chunk = source_audio.read(1024 * 1024)
                            if not chunk:
                                break
                            digest.update(chunk)
                            output.write(chunk)
                            copied_bytes += len(chunk)
                            self._report_voice_import_progress(
                                progress,
                                phase="extracting",
                                message="正在复制参考音频",
                                processed_bytes=copied_bytes,
                                total_bytes=audio_info.file_size,
                            )
                    expected = str(manifest.get("sha256") or "").strip().lower()
                    if expected and digest.hexdigest() != expected:
                        raise ValueError("参考音频校验和不一致，文件可能已损坏。")
                    temporary.replace(target)
                    temporary = None
                    profile["gpt_sovits_ref_audio"] = str(target.resolve())
        except zipfile.BadZipFile:
            raise ValueError("音色包不是有效的 ZIP 文件。") from None
        except json.JSONDecodeError:
            raise ValueError("manifest.json 不是有效 JSON。") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

        profiles[new_id] = profile
        changes: dict[str, Any] = {"voice_profiles": profiles}
        if not config.get("default_voice_profile_id"):
            changes["default_voice_profile_id"] = new_id
        try:
            self._report_voice_import_progress(
                progress,
                phase="saving",
                message="正在保存音色设置",
                processed_bytes=99,
                total_bytes=100,
            )
            self._dep_save_config(changes)
        except Exception:
            if target is not None:
                target.unlink(missing_ok=True)
            raise
        return {"id": new_id, "name": name, "reference_audio": audio_name}


    def import_voice_package(self, raw: bytes, _filename: str = "") -> dict[str, Any]:
        """兼容内存调用；HTTP 路由使用文件版，避免大包占满内存。"""
        if not raw:
            raise ValueError("音色包为空。")
        return self._import_voice_package_source(BytesIO(raw), filename=_filename)


    def import_voice_package_file(self,
        path: Path,
        *,
        progress: VoiceImportProgress | None = None,
    ) -> dict[str, Any]:
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError("音色包为空。")
        return self._import_voice_package_source(path, filename=path.name, progress=progress)


    def _new_voice_profile_id(self, profiles: dict[str, Any]) -> str:
        base = f"voice-{int(time.time() * 1000)}"
        candidate = base
        counter = 1
        while candidate in profiles:
            candidate = f"{base}-{counter}"
            counter += 1
        return candidate


    def _voice_weight_options(self) -> dict[str, list[dict[str, str]]]:
        roots = [
            self._dep_settings().voice_training_dir,
            self._dep_settings().workspace_root,
            self._dep_settings().project_root,
            self._dep_settings().source_workspace_root,
            Path("D:/GPT-SoVITS"),
            Path("D:/GPT-SoVITS-v2"),
            Path("D:/GPT-SoVITS-v2proplus"),
        ]
        groups: dict[str, tuple[str, ...]] = {
            "gpt": ("GPT_weights", "GPT_weights_v2", "GPT_weights_v2Pro", "GPT_weights_v2ProPlus"),
            "sovits": (
                "SoVITS_weights",
                "SoVITS_weights_v2",
                "SoVITS_weights_v2Pro",
                "SoVITS_weights_v2ProPlus",
            ),
        }
        result: dict[str, list[dict[str, str]]] = {"gpt": [], "sovits": []}
        for kind, folder_names in groups.items():
            suffix = ".ckpt" if kind == "gpt" else ".pth"
            seen: set[str] = set()
            for root in roots:
                for source in (root, root / "GPT-SoVITS"):
                    for folder_name in folder_names:
                        folder = source / folder_name
                        if not folder.is_dir():
                            continue
                        for path in sorted(folder.glob(f"*{suffix}"), key=lambda item: item.stat().st_mtime, reverse=True):
                            resolved = str(path.resolve())
                            if resolved in seen or not self._dep__voice_weight_path(path, kind):
                                continue
                            seen.add(resolved)
                            result[kind].append({"name": path.name, "path": resolved})
        return result


    def _runtime_voice_weights(self) -> dict[str, str]:
        source = self._dep_settings().voice_training_dir / "GPT-SoVITS"
        config_path = source / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, yaml.YAMLError):
            return {"gpt": "", "sovits": ""}
        custom = payload.get("custom") if isinstance(payload, dict) else None
        if not isinstance(custom, dict):
            return {"gpt": "", "sovits": ""}

        result: dict[str, str] = {}
        for kind, key in (("gpt", "t2s_weights_path"), ("sovits", "vits_weights_path")):
            raw = str(custom.get(key) or "").strip()
            path = Path(raw).expanduser()
            if raw and not path.is_absolute():
                path = source / path
            result[kind] = str(path.resolve()) if raw and path.is_file() else ""
        return result


    def _emotion_reference_status(self) -> dict[str, Any]:
        mapping_path = self._dep_settings().voice_training_dir / "emotion-references.json"
        try:
            payload = json.loads(mapping_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        available: list[str] = []
        if isinstance(payload, dict):
            for emotion in self._dep_SPEECH_EMOTION_LABELS():
                item = payload.get(emotion)
                if not isinstance(item, dict):
                    continue
                audio = Path(str(item.get("audio") or "")).expanduser()
                if not audio.is_absolute():
                    audio = mapping_path.parent / audio
                if audio.is_file() and str(item.get("text") or "").strip():
                    available.append(emotion)
        return {
            "ready": bool(available),
            "count": len(available),
            "emotions": available,
        }
