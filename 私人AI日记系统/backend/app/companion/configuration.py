"""Companion configuration; dependencies are explicitly injected."""
from __future__ import annotations

from typing import Any, Callable
from pathlib import Path
import json
import re


class ConfigurationService:
    """Domain implementation; constructor callbacks preserve facade test seams."""

    def __init__(self, *,
                 dep_DEFAULT_CONFIG: Callable[..., Any],
                 dep_DEFAULT_VOICE_PROFILE_ID: Callable[..., Any],
                 dep_GENIE_VOICE_RUNTIME: Callable[..., Any],
                 dep_LEGACY_GPT_SOVITS_RUNTIME: Callable[..., Any],
                 dep_LIVE2D_EXPRESSION_SLOT_IDS: Callable[..., Any],
                 dep_LIVE2D_MOTION_SLOT_IDS: Callable[..., Any],
                 dep_MIO_VOICE_ENGINE: Callable[..., Any],
                 dep_SO_VITS_SVC_ENGINE: Callable[..., Any],
                 dep_VOICE_PROFILE_FIELDS: Callable[..., Any],
                 dep__live2d_model_ids: Callable[..., Any],
                 dep_settings: Callable[..., Any],
                 ) -> None:
        self._dep_DEFAULT_CONFIG = dep_DEFAULT_CONFIG
        self._dep_DEFAULT_VOICE_PROFILE_ID = dep_DEFAULT_VOICE_PROFILE_ID
        self._dep_GENIE_VOICE_RUNTIME = dep_GENIE_VOICE_RUNTIME
        self._dep_LEGACY_GPT_SOVITS_RUNTIME = dep_LEGACY_GPT_SOVITS_RUNTIME
        self._dep_LIVE2D_EXPRESSION_SLOT_IDS = dep_LIVE2D_EXPRESSION_SLOT_IDS
        self._dep_LIVE2D_MOTION_SLOT_IDS = dep_LIVE2D_MOTION_SLOT_IDS
        self._dep_MIO_VOICE_ENGINE = dep_MIO_VOICE_ENGINE
        self._dep_SO_VITS_SVC_ENGINE = dep_SO_VITS_SVC_ENGINE
        self._dep_VOICE_PROFILE_FIELDS = dep_VOICE_PROFILE_FIELDS
        self._dep__live2d_model_ids = dep__live2d_model_ids
        self._dep_settings = dep_settings

    def _voice_weight_path(self, value: object, kind: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        path = Path(raw).expanduser()
        expected_suffix = ".ckpt" if kind == "gpt" else ".pth"
        if path.suffix.lower() != expected_suffix:
            return ""
        return raw


    def _migrate_config(self, data: dict[str, Any]) -> dict[str, Any]:
        migrated = dict(data)
        try:
            schema_version = int(migrated.get("config_schema_version") or 1)
        except (TypeError, ValueError):
            schema_version = 1
        if schema_version < 2:
            try:
                legacy_screen_timeout = int(migrated.get("screen_request_timeout_seconds", 12))
            except (TypeError, ValueError):
                legacy_screen_timeout = 12
            if legacy_screen_timeout == 12:
                migrated["screen_request_timeout_seconds"] = 25
        migrated["config_schema_version"] = 2
        return migrated


    def _normalize_live2d_motion_slots(self, value: object) -> dict[str, dict[str, str]]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, dict[str, str]] = {}
        for raw_model_id, raw_slots in list(value.items())[:30]:
            model_id = str(raw_model_id or "").strip()[:100]
            if not model_id or not isinstance(raw_slots, dict):
                continue
            slots: dict[str, str] = {}
            for raw_slot, raw_group in raw_slots.items():
                slot = str(raw_slot or "").strip().lower()
                group = str(raw_group or "").strip()[:120]
                if slot in self._dep_LIVE2D_MOTION_SLOT_IDS() and group:
                    slots[slot] = group
            if slots:
                normalized[model_id] = slots
        return normalized


    def _normalize_live2d_expression_slots(self, value: object) -> dict[str, dict[str, str]]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, dict[str, str]] = {}
        for raw_model_id, raw_slots in list(value.items())[:30]:
            model_id = str(raw_model_id or "").strip()[:100]
            if not model_id or not isinstance(raw_slots, dict):
                continue
            slots: dict[str, str] = {}
            for raw_slot, raw_expression in raw_slots.items():
                slot = str(raw_slot or "").strip().lower()
                expression = str(raw_expression or "").strip()[:120]
                if slot in self._dep_LIVE2D_EXPRESSION_SLOT_IDS() and expression:
                    slots[slot] = expression
            if slots:
                normalized[model_id] = slots
        return normalized


    def _legacy_voice_profile(self, config: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": "默认角色音色",
            "engine": self._dep_MIO_VOICE_ENGINE(),
            "gpt_sovits_ref_audio": str(config.get("gpt_sovits_ref_audio") or "").strip(),
            "gpt_sovits_prompt_text": str(config.get("gpt_sovits_prompt_text") or "").strip()[:1000],
            "gpt_sovits_prompt_language": str(config.get("gpt_sovits_prompt_language") or "ja").strip().lower(),
            "gpt_sovits_text_language": str(config.get("gpt_sovits_text_language") or "auto").strip().lower(),
            "gpt_sovits_translate_to_japanese": bool(config.get("gpt_sovits_translate_to_japanese", False)),
            "gpt_sovits_gpt_weights": self._voice_weight_path(config.get("gpt_sovits_gpt_weights"), "gpt"),
            "gpt_sovits_sovits_weights": self._voice_weight_path(config.get("gpt_sovits_sovits_weights"), "sovits"),
            "use_emotion_references": True,
            "so_vits_svc_model_path": "",
            "so_vits_svc_config_path": "",
            "so_vits_svc_speaker": "",
            "so_vits_svc_pitch": 0,
            "so_vits_svc_auto_predict_f0": True,
            "so_vits_svc_noise_scale": 0.4,
            "so_vits_svc_base_profile_id": "",
            "source_package_name": "",
            "source_license": "",
        }


    def _uses_genie_runtime(self, config: dict[str, Any] | None = None) -> bool:
        selected = config or self.load_config()
        return str(selected.get("local_voice_runtime") or self._dep_GENIE_VOICE_RUNTIME()).strip().lower() == self._dep_GENIE_VOICE_RUNTIME()


    def _normalize_voice_profiles(self, value: object, legacy: dict[str, Any]) -> dict[str, dict[str, Any]]:
        languages = {"zh", "ja", "en", "yue", "ko", "all_zh", "all_ja", "all_en", "auto"}
        source = value if isinstance(value, dict) else {}
        normalized: dict[str, dict[str, Any]] = {}
        for raw_profile_id, candidate in list(source.items())[:20]:
            profile_id = str(raw_profile_id or "").strip()[:80]
            if not re.fullmatch(r"[A-Za-z0-9._-]+", profile_id) or not isinstance(candidate, dict):
                continue
            prompt_language = str(candidate.get("gpt_sovits_prompt_language") or "ja").strip().lower()
            text_language = str(candidate.get("gpt_sovits_text_language") or "auto").strip().lower()
            engine = str(candidate.get("engine") or self._dep_MIO_VOICE_ENGINE()).strip().lower()
            if engine not in {self._dep_MIO_VOICE_ENGINE(), self._dep_SO_VITS_SVC_ENGINE()}:
                engine = self._dep_MIO_VOICE_ENGINE()
            normalized[profile_id] = {
                "name": str(candidate.get("name") or profile_id).strip()[:80] or profile_id,
                "engine": engine,
                "gpt_sovits_ref_audio": str(candidate.get("gpt_sovits_ref_audio") or "").strip(),
                "gpt_sovits_prompt_text": str(candidate.get("gpt_sovits_prompt_text") or "").strip()[:1000],
                "gpt_sovits_prompt_language": prompt_language if prompt_language in languages else "ja",
                "gpt_sovits_text_language": text_language if text_language in languages else "auto",
                "gpt_sovits_translate_to_japanese": bool(candidate.get("gpt_sovits_translate_to_japanese", False)),
                "gpt_sovits_gpt_weights": self._voice_weight_path(candidate.get("gpt_sovits_gpt_weights"), "gpt"),
                "gpt_sovits_sovits_weights": self._voice_weight_path(candidate.get("gpt_sovits_sovits_weights"), "sovits"),
                "use_emotion_references": bool(candidate.get("use_emotion_references", True)),
                "so_vits_svc_model_path": str(candidate.get("so_vits_svc_model_path") or "").strip(),
                "so_vits_svc_config_path": str(candidate.get("so_vits_svc_config_path") or "").strip(),
                "so_vits_svc_speaker": str(candidate.get("so_vits_svc_speaker") or "").strip()[:80],
                "so_vits_svc_pitch": max(-24, min(24, int(candidate.get("so_vits_svc_pitch") or 0))),
                "so_vits_svc_auto_predict_f0": bool(candidate.get("so_vits_svc_auto_predict_f0", True)),
                "so_vits_svc_noise_scale": max(0.0, min(1.0, float(candidate.get("so_vits_svc_noise_scale") or 0.4))),
                "so_vits_svc_base_profile_id": str(candidate.get("so_vits_svc_base_profile_id") or "").strip()[:80],
                "source_package_name": str(candidate.get("source_package_name") or "").strip()[:260],
                "source_license": str(candidate.get("source_license") or "").strip()[:200],
            }
        if not normalized:
            fallback = dict(legacy)
            fallback["name"] = str(fallback.get("name") or "默认角色音色")
            normalized[self._dep_DEFAULT_VOICE_PROFILE_ID()] = fallback
        return normalized


    def resolve_voice_profile(self,
        model_id: str = "",
        config: dict[str, Any] | None = None,
        speech_language: str = "",
    ) -> tuple[str, dict[str, Any]]:
        base = config or self.load_config()
        profiles = base.get("voice_profiles") if isinstance(base.get("voice_profiles"), dict) else {}
        default_profile_id = str(base.get("default_voice_profile_id") or self._dep_DEFAULT_VOICE_PROFILE_ID())
        # 音色属于角色，不再随聊天模型变化；所有入口统一使用当前默认音色。
        profile_id = default_profile_id
        if profile_id not in profiles:
            profile_id = next(iter(profiles), self._dep_DEFAULT_VOICE_PROFILE_ID())
        profile = profiles.get(profile_id) or self._legacy_voice_profile(base)
        selected = dict(base)
        selected.update(profile)
        selected["voice_profile_id"] = profile_id
        selected["voice_profile_name"] = str(profile.get("name") or profile_id)
        language = str(speech_language or base.get("pet_speech_language") or "zh").strip().lower()
        if language == "zh":
            selected["gpt_sovits_text_language"] = "zh"
            selected["gpt_sovits_translate_to_japanese"] = False
            selected["gpt_sovits_translate_to_chinese"] = True
        elif language == "ja":
            selected["gpt_sovits_text_language"] = "ja"
            selected["gpt_sovits_translate_to_japanese"] = True
            selected["gpt_sovits_translate_to_chinese"] = False
        return profile_id, selected


    def _resolve_base_voice_profile(self, config: dict[str, Any], selected_profile_id: str) -> tuple[str, dict[str, Any]]:
        profiles = config.get("voice_profiles") if isinstance(config.get("voice_profiles"), dict) else {}
        selected = profiles.get(selected_profile_id) if isinstance(profiles.get(selected_profile_id), dict) else {}
        requested_id = str(selected.get("so_vits_svc_base_profile_id") or "").strip()
        candidates = [requested_id]
        candidates.extend(
            profile_id
            for profile_id, profile in profiles.items()
            if profile_id != selected_profile_id and isinstance(profile, dict) and profile.get("engine") == self._dep_MIO_VOICE_ENGINE()
        )
        for profile_id in candidates:
            profile = profiles.get(profile_id)
            if not profile_id or not isinstance(profile, dict) or profile.get("engine") != self._dep_MIO_VOICE_ENGINE():
                continue
            base = dict(config)
            base.update(profile)
            for key in ("gpt_sovits_text_language", "gpt_sovits_translate_to_japanese", "gpt_sovits_translate_to_chinese"):
                if key in config:
                    base[key] = config[key]
            base["voice_profile_id"] = profile_id
            base["voice_profile_name"] = str(profile.get("name") or profile_id)
            return profile_id, base
        raise ValueError("这个第三方音色缺少基础 TTS 音色，请先保留或导入一个 GPT-SoVITS 音色。")


    def _apply_voice_profile_config(self, data: dict[str, Any]) -> None:
        profiles = self._normalize_voice_profiles(data.get("voice_profiles"), self._legacy_voice_profile(data))
        requested_default = str(data.get("default_voice_profile_id") or self._dep_DEFAULT_VOICE_PROFILE_ID()).strip()[:80]
        default_profile_id = requested_default if requested_default in profiles else next(iter(profiles))
        data["default_voice_profile_id"] = default_profile_id
        data["voice_profiles"] = profiles
        data.pop("model_voice_bindings", None)
        default_profile = profiles[default_profile_id]
        for key in self._dep_VOICE_PROFILE_FIELDS():
            data[key] = default_profile.get(key, "")


    def load_config(self) -> dict[str, Any]:
        data = dict(self._dep_DEFAULT_CONFIG())
        try:
            saved = json.loads(self._dep_settings().companion_config_path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                data.update(self._migrate_config(saved))
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        data["voice_enabled"] = bool(data.get("voice_enabled", True))
        data["voice_startup_enabled"] = bool(data.get("voice_startup_enabled", False))
        data["voice_idle_timeout_seconds"] = max(
            0,
            min(1800, int(data.get("voice_idle_timeout_seconds", 180))),
        )
        voice_engine = str(data.get("voice_engine") or self._dep_MIO_VOICE_ENGINE()).strip().lower()
        data["voice_engine"] = voice_engine if voice_engine in {"gpt_sovits", "cloud"} else self._dep_MIO_VOICE_ENGINE()
        local_runtime = str(data.get("local_voice_runtime") or self._dep_GENIE_VOICE_RUNTIME()).strip().lower()
        data["local_voice_runtime"] = local_runtime if local_runtime in {self._dep_GENIE_VOICE_RUNTIME(), self._dep_LEGACY_GPT_SOVITS_RUNTIME()} else self._dep_GENIE_VOICE_RUNTIME()
        data["cloud_tts_api_key"] = str(data.get("cloud_tts_api_key") or "").strip()
        data["cloud_tts_app_id"] = str(data.get("cloud_tts_app_id") or "").strip()[:200]
        cloud_speaker = str(data.get("cloud_tts_speaker") or self._dep_DEFAULT_CONFIG()["cloud_tts_speaker"]).strip()
        data["cloud_tts_speaker"] = cloud_speaker[:200] or self._dep_DEFAULT_CONFIG()["cloud_tts_speaker"]
        data["cloud_tts_speech_rate"] = max(
            -50,
            min(100, int(data.get("cloud_tts_speech_rate") or 0)),
        )
        data["chat_model_id"] = str(data.get("chat_model_id") or "auto").strip()[:200] or "auto"
        data["chat_reasoning_level"] = str(data.get("chat_reasoning_level") or "auto").strip()[:50] or "auto"
        data["pet_chat_model_id"] = str(data.get("pet_chat_model_id") or "auto").strip()[:200] or "auto"
        data["pet_chat_reasoning_level"] = str(
            data.get("pet_chat_reasoning_level") or "auto"
        ).strip()[:50] or "auto"
        call_asr_engine = str(data.get("pet_call_asr_engine") or "auto").strip().lower()
        data["pet_call_asr_engine"] = (
            call_asr_engine
            if call_asr_engine in {"auto", "whisper", "sensevoice", "paraformer"}
            else "auto"
        )
        call_language = str(data.get("pet_call_input_language") or "zh").strip().lower()
        data["pet_call_input_language"] = "ja" if call_language == "ja" else "zh"
        data["speech_translation_model_id"] = (
            str(data.get("speech_translation_model_id") or "deepseek-v4-flash").strip()[:200]
            or "deepseek-v4-flash"
        )
        data["pet_call_silence_ms"] = max(350, min(1800, int(data.get("pet_call_silence_ms", 650))))
        data["pet_call_voice_threshold"] = max(0.004, min(0.12, float(data.get("pet_call_voice_threshold", 0.018))))
        data["pet_call_min_speech_ms"] = max(150, min(1500, int(data.get("pet_call_min_speech_ms", 280))))
        data["pet_call_max_turn_seconds"] = max(5, min(45, int(data.get("pet_call_max_turn_seconds", 18))))
        data["startup_greeting_enabled"] = bool(data.get("startup_greeting_enabled", True))
        data["qq_startup_enabled"] = bool(data.get("qq_startup_enabled", False))
        data["speak_proactive"] = bool(data.get("speak_proactive", False))
        data["speak_screen_observations"] = bool(data.get("speak_screen_observations", True))
        data["speak_game_observations"] = bool(data.get("speak_game_observations", True))
        data["screen_ai_enabled"] = bool(data.get("screen_ai_enabled", True))
        data["screen_audio_enabled"] = bool(data.get("screen_audio_enabled", True))
        screen_audio_model = str(data.get("screen_audio_model") or "base").strip().lower()
        data["screen_audio_model"] = screen_audio_model if screen_audio_model in {"tiny", "base", "small"} else "base"
        screen_audio_language = str(data.get("screen_audio_language") or "auto").strip().lower()
        data["screen_audio_language"] = screen_audio_language if screen_audio_language in {"auto", "zh", "ja", "en"} else "auto"
        data["screen_audio_chunk_seconds"] = max(4, min(15, int(data.get("screen_audio_chunk_seconds", 5))))
        screen_vision_route = str(data.get("screen_vision_route") or "local").strip().lower()
        data["screen_vision_route"] = screen_vision_route if screen_vision_route in {"local", "cloud"} else "local"
        data["qq_voice_mode"] = str(data.get("qq_voice_mode") or "adaptive")
        data["gpt_sovits_url"] = str(data.get("gpt_sovits_url") or self._dep_DEFAULT_CONFIG()["gpt_sovits_url"]).rstrip("/")
        data["gpt_sovits_ref_audio"] = str(data.get("gpt_sovits_ref_audio") or "")
        data["gpt_sovits_prompt_text"] = str(data.get("gpt_sovits_prompt_text") or "")[:1000]
        data["gpt_sovits_prompt_language"] = str(data.get("gpt_sovits_prompt_language") or "ja")
        data["gpt_sovits_text_language"] = str(data.get("gpt_sovits_text_language") or "auto")
        data["gpt_sovits_translate_to_japanese"] = bool(
            data.get("gpt_sovits_translate_to_japanese", False)
        )
        data["gpt_sovits_gpt_weights"] = str(data.get("gpt_sovits_gpt_weights") or "")
        data["gpt_sovits_sovits_weights"] = str(data.get("gpt_sovits_sovits_weights") or "")
        self._apply_voice_profile_config(data)
        data["voice_volume"] = max(0, min(100, int(data.get("voice_volume", 85))))
        data["voice_streaming_enabled"] = bool(data.get("voice_streaming_enabled", True))
        pet_speech_language = str(data.get("pet_speech_language") or "zh").strip().lower()
        data["pet_speech_language"] = pet_speech_language if pet_speech_language in {"zh", "ja"} else "zh"
        data["screen_direct_voice_enabled"] = bool(data.get("screen_direct_voice_enabled", True))
        data["screen_vision_model_id"] = (
            str(data.get("screen_vision_model_id") or "auto-fast").strip()[:200] or "auto-fast"
        )
        data["bubble_seconds"] = max(3, min(30, int(data.get("bubble_seconds", 9))))
        data["pet_size_percent"] = max(80, min(240, int(data.get("pet_size_percent", 150))))
        # 公开版只保留 Live2D 桌宠形象：历史数据里的静态立绘模式统一迁移。
        pet_renderer = str(data.get("pet_renderer") or "live2d").strip().lower()
        data["pet_renderer"] = "live2d" if pet_renderer in {"classic", "live2d"} else "live2d"
        live2d_model_id = str(data.get("live2d_model_id") or "hiyori").strip().lower()
        available_model_ids = self._dep__live2d_model_ids()
        data["live2d_model_id"] = live2d_model_id if live2d_model_id in available_model_ids else "hiyori"
        data["live2d_scale"] = max(0.65, min(1.55, float(data.get("live2d_scale", 1.0))))
        data["live2d_vertical_offset"] = max(
            -0.35,
            min(0.35, float(data.get("live2d_vertical_offset", 0.0))),
        )
        data["live2d_follow_cursor"] = bool(data.get("live2d_follow_cursor", True))
        data["live2d_idle_motion"] = bool(data.get("live2d_idle_motion", True))
        data["live2d_click_motion"] = bool(data.get("live2d_click_motion", True))
        data["live2d_smart_passthrough"] = bool(data.get("live2d_smart_passthrough", True))
        data["live2d_click_through_locked"] = bool(data.get("live2d_click_through_locked", False))
        data["live2d_speech_bubble_enabled"] = bool(data.get("live2d_speech_bubble_enabled", True))
        data["live2d_keep_visible"] = bool(data.get("live2d_keep_visible", False))
        data["live2d_always_on_top"] = bool(data.get("live2d_always_on_top", True))
        data["live2d_disable_gpu"] = bool(data.get("live2d_disable_gpu", False))
        data["live2d_motion_slots"] = self._normalize_live2d_motion_slots(
            data.get("live2d_motion_slots")
        )
        data["live2d_expression_slots"] = self._normalize_live2d_expression_slots(
            data.get("live2d_expression_slots")
        )
        data["screen_change_threshold"] = max(1.0, min(50.0, float(data.get("screen_change_threshold", 4.0))))
        data["screen_analysis_interval_seconds"] = max(
            5,
            min(600, int(data.get("screen_analysis_interval_seconds", 5))),
        )
        data["screen_request_timeout_seconds"] = max(
            5,
            min(60, int(data.get("screen_request_timeout_seconds", 25))),
        )
        data["screen_voice_cooldown_seconds"] = max(
            5,
            min(600, int(data.get("screen_voice_cooldown_seconds", 5))),
        )
        data["screen_minimum_importance"] = max(
            0.1,
            min(1.0, float(data.get("screen_minimum_importance", 0.62))),
        )
        data["screen_daily_cost_limit_yuan"] = max(
            0.1,
            min(1000.0, float(data.get("screen_daily_cost_limit_yuan", 5.0))),
        )
        return data


    def save_config(self, changes: dict[str, Any]) -> dict[str, Any]:
        data = self.load_config()
        previous_key = str(data.get("cloud_tts_api_key") or "")
        for key in self._dep_DEFAULT_CONFIG():
            if key in changes:
                data[key] = changes[key]
        if "cloud_tts_api_key" in changes:
            key_value = str(changes.get("cloud_tts_api_key") or "").strip()
            if key_value == "__clear__":
                data["cloud_tts_api_key"] = ""
            elif key_value:
                from ..secret_store import protect_secret

                data["cloud_tts_api_key"] = protect_secret(key_value)
            else:
                # 留空表示保持原值不变（前端不回显 Key，无法填"原样"）
                data["cloud_tts_api_key"] = previous_key
        if any(key in changes for key in self._dep_VOICE_PROFILE_FIELDS()):
            profiles = dict(data.get("voice_profiles") or {})
            default_id = str(data.get("default_voice_profile_id") or self._dep_DEFAULT_VOICE_PROFILE_ID())
            profile = dict(profiles.get(default_id) or self._legacy_voice_profile(data))
            for key in self._dep_VOICE_PROFILE_FIELDS():
                if key in changes:
                    profile[key] = changes[key]
            profiles[default_id] = profile
            data["voice_profiles"] = profiles
        data = {key: value for key, value in self.load_normalized_config(data).items() if key in self._dep_DEFAULT_CONFIG()}
        self._dep_settings().companion_dir.mkdir(parents=True, exist_ok=True)
        temporary = self._dep_settings().companion_config_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self._dep_settings().companion_config_path)
        from .. import pet_event_service

        pet_event_service.publish("settings_changed", data)
        return data


    def load_normalized_config(self, data: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(self._dep_DEFAULT_CONFIG())
        normalized.update(self._migrate_config(data))
        normalized["voice_enabled"] = bool(normalized.get("voice_enabled", True))
        normalized["voice_startup_enabled"] = bool(normalized.get("voice_startup_enabled", False))
        normalized["voice_idle_timeout_seconds"] = max(
            0,
            min(1800, int(normalized.get("voice_idle_timeout_seconds", 180))),
        )
        voice_engine = str(normalized.get("voice_engine") or self._dep_MIO_VOICE_ENGINE()).strip().lower()
        normalized["voice_engine"] = voice_engine if voice_engine in {"gpt_sovits", "cloud"} else self._dep_MIO_VOICE_ENGINE()
        local_runtime = str(normalized.get("local_voice_runtime") or self._dep_GENIE_VOICE_RUNTIME()).strip().lower()
        normalized["local_voice_runtime"] = local_runtime if local_runtime in {self._dep_GENIE_VOICE_RUNTIME(), self._dep_LEGACY_GPT_SOVITS_RUNTIME()} else self._dep_GENIE_VOICE_RUNTIME()
        normalized["cloud_tts_api_key"] = str(normalized.get("cloud_tts_api_key") or "").strip()
        normalized["cloud_tts_app_id"] = str(normalized.get("cloud_tts_app_id") or "").strip()[:200]
        cloud_speaker = str(normalized.get("cloud_tts_speaker") or self._dep_DEFAULT_CONFIG()["cloud_tts_speaker"]).strip()
        normalized["cloud_tts_speaker"] = cloud_speaker[:200] or self._dep_DEFAULT_CONFIG()["cloud_tts_speaker"]
        normalized["cloud_tts_speech_rate"] = max(
            -50,
            min(100, int(normalized.get("cloud_tts_speech_rate") or 0)),
        )
        normalized["chat_model_id"] = str(normalized.get("chat_model_id") or "auto").strip()[:200] or "auto"
        normalized["chat_reasoning_level"] = str(
            normalized.get("chat_reasoning_level") or "auto"
        ).strip()[:50] or "auto"
        normalized["pet_chat_model_id"] = str(
            normalized.get("pet_chat_model_id") or "auto"
        ).strip()[:200] or "auto"
        normalized["pet_chat_reasoning_level"] = str(
            normalized.get("pet_chat_reasoning_level") or "auto"
        ).strip()[:50] or "auto"
        call_asr_engine = str(normalized.get("pet_call_asr_engine") or "auto").strip().lower()
        normalized["pet_call_asr_engine"] = (
            call_asr_engine
            if call_asr_engine in {"auto", "whisper", "sensevoice", "paraformer"}
            else "auto"
        )
        call_language = str(normalized.get("pet_call_input_language") or "zh").strip().lower()
        normalized["pet_call_input_language"] = "ja" if call_language == "ja" else "zh"
        normalized["speech_translation_model_id"] = (
            str(normalized.get("speech_translation_model_id") or "deepseek-v4-flash").strip()[:200]
            or "deepseek-v4-flash"
        )
        normalized["pet_call_silence_ms"] = max(350, min(1800, int(normalized.get("pet_call_silence_ms", 650))))
        normalized["pet_call_voice_threshold"] = max(0.004, min(0.12, float(normalized.get("pet_call_voice_threshold", 0.018))))
        normalized["pet_call_min_speech_ms"] = max(150, min(1500, int(normalized.get("pet_call_min_speech_ms", 280))))
        normalized["pet_call_max_turn_seconds"] = max(5, min(45, int(normalized.get("pet_call_max_turn_seconds", 18))))
        normalized["startup_greeting_enabled"] = bool(normalized.get("startup_greeting_enabled", True))
        normalized["qq_startup_enabled"] = bool(normalized.get("qq_startup_enabled", False))
        normalized["speak_proactive"] = bool(normalized.get("speak_proactive", False))
        normalized["speak_screen_observations"] = bool(normalized.get("speak_screen_observations", True))
        normalized["speak_game_observations"] = bool(normalized.get("speak_game_observations", True))
        normalized["screen_ai_enabled"] = bool(normalized.get("screen_ai_enabled", True))
        normalized["screen_audio_enabled"] = bool(normalized.get("screen_audio_enabled", True))
        screen_audio_model = str(normalized.get("screen_audio_model") or "base").strip().lower()
        normalized["screen_audio_model"] = screen_audio_model if screen_audio_model in {"tiny", "base", "small"} else "base"
        screen_audio_language = str(normalized.get("screen_audio_language") or "auto").strip().lower()
        normalized["screen_audio_language"] = screen_audio_language if screen_audio_language in {"auto", "zh", "ja", "en"} else "auto"
        normalized["screen_audio_chunk_seconds"] = max(
            4,
            min(15, int(normalized.get("screen_audio_chunk_seconds", 5))),
        )
        screen_vision_route = str(normalized.get("screen_vision_route") or "local").strip().lower()
        normalized["screen_vision_route"] = (
            screen_vision_route if screen_vision_route in {"local", "cloud"} else "local"
        )
        voice_mode = str(normalized.get("qq_voice_mode") or "adaptive").strip().lower()
        normalized["qq_voice_mode"] = voice_mode if voice_mode in {"explicit", "adaptive", "always"} else "adaptive"
        normalized["gpt_sovits_url"] = str(
            normalized.get("gpt_sovits_url") or self._dep_DEFAULT_CONFIG()["gpt_sovits_url"]
        ).strip().rstrip("/")
        normalized["gpt_sovits_ref_audio"] = str(normalized.get("gpt_sovits_ref_audio") or "").strip()
        normalized["gpt_sovits_prompt_text"] = str(normalized.get("gpt_sovits_prompt_text") or "").strip()[:1000]
        languages = {"zh", "ja", "en", "yue", "ko", "all_zh", "all_ja", "all_en", "auto"}
        prompt_language = str(normalized.get("gpt_sovits_prompt_language") or "ja").strip().lower()
        text_language = str(normalized.get("gpt_sovits_text_language") or "auto").strip().lower()
        normalized["gpt_sovits_prompt_language"] = prompt_language if prompt_language in languages else "ja"
        normalized["gpt_sovits_text_language"] = text_language if text_language in languages else "auto"
        normalized["gpt_sovits_translate_to_japanese"] = bool(
            normalized.get("gpt_sovits_translate_to_japanese", False)
        )
        normalized["gpt_sovits_gpt_weights"] = str(normalized.get("gpt_sovits_gpt_weights") or "").strip()
        normalized["gpt_sovits_sovits_weights"] = str(normalized.get("gpt_sovits_sovits_weights") or "").strip()
        self._apply_voice_profile_config(normalized)
        normalized["voice_volume"] = max(0, min(100, int(normalized.get("voice_volume", 85))))
        normalized["voice_streaming_enabled"] = bool(normalized.get("voice_streaming_enabled", True))
        pet_speech_language = str(normalized.get("pet_speech_language") or "zh").strip().lower()
        normalized["pet_speech_language"] = pet_speech_language if pet_speech_language in {"zh", "ja"} else "zh"
        normalized["screen_direct_voice_enabled"] = bool(normalized.get("screen_direct_voice_enabled", True))
        normalized["screen_vision_model_id"] = (
            str(normalized.get("screen_vision_model_id") or "auto-fast").strip()[:200] or "auto-fast"
        )
        normalized["bubble_seconds"] = max(3, min(30, int(normalized.get("bubble_seconds", 9))))
        normalized["pet_size_percent"] = max(80, min(240, int(normalized.get("pet_size_percent", 150))))
        pet_renderer = str(normalized.get("pet_renderer") or "live2d").strip().lower()
        normalized["pet_renderer"] = "live2d" if pet_renderer in {"classic", "live2d"} else "live2d"
        live2d_model_id = str(normalized.get("live2d_model_id") or "hiyori").strip().lower()
        available_model_ids = self._dep__live2d_model_ids()
        normalized["live2d_model_id"] = live2d_model_id if live2d_model_id in available_model_ids else "hiyori"
        normalized["live2d_scale"] = max(
            0.65,
            min(1.55, float(normalized.get("live2d_scale", 1.0))),
        )
        normalized["live2d_vertical_offset"] = max(
            -0.35,
            min(0.35, float(normalized.get("live2d_vertical_offset", 0.0))),
        )
        normalized["live2d_follow_cursor"] = bool(normalized.get("live2d_follow_cursor", True))
        normalized["live2d_idle_motion"] = bool(normalized.get("live2d_idle_motion", True))
        normalized["live2d_click_motion"] = bool(normalized.get("live2d_click_motion", True))
        normalized["live2d_smart_passthrough"] = bool(
            normalized.get("live2d_smart_passthrough", True)
        )
        normalized["live2d_click_through_locked"] = bool(
            normalized.get("live2d_click_through_locked", False)
        )
        normalized["live2d_speech_bubble_enabled"] = bool(
            normalized.get("live2d_speech_bubble_enabled", True)
        )
        normalized["live2d_keep_visible"] = bool(normalized.get("live2d_keep_visible", False))
        normalized["live2d_always_on_top"] = bool(normalized.get("live2d_always_on_top", True))
        normalized["live2d_disable_gpu"] = bool(normalized.get("live2d_disable_gpu", False))
        normalized["live2d_motion_slots"] = self._normalize_live2d_motion_slots(
            normalized.get("live2d_motion_slots")
        )
        normalized["live2d_expression_slots"] = self._normalize_live2d_expression_slots(
            normalized.get("live2d_expression_slots")
        )
        normalized["screen_change_threshold"] = max(
            1.0,
            min(50.0, float(normalized.get("screen_change_threshold", 4.0))),
        )
        normalized["screen_analysis_interval_seconds"] = max(
            5,
            min(600, int(normalized.get("screen_analysis_interval_seconds", 5))),
        )
        normalized["screen_request_timeout_seconds"] = max(
            5,
            min(60, int(normalized.get("screen_request_timeout_seconds", 25))),
        )
        normalized["screen_voice_cooldown_seconds"] = max(
            5,
            min(600, int(normalized.get("screen_voice_cooldown_seconds", 5))),
        )
        normalized["screen_minimum_importance"] = max(
            0.1,
            min(1.0, float(normalized.get("screen_minimum_importance", 0.62))),
        )
        normalized["screen_daily_cost_limit_yuan"] = max(
            0.1,
            min(1000.0, float(normalized.get("screen_daily_cost_limit_yuan", 5.0))),
        )
        normalized["position_x"] = max(-10000, min(10000, int(normalized.get("position_x", 80))))
        normalized["position_y"] = max(-10000, min(10000, int(normalized.get("position_y", 420))))
        return normalized


    def save_pet_position(self, x: int, y: int) -> dict[str, Any]:
        return self.save_config({"position_x": x, "position_y": y})


    def save_pet_size(self, percent: int) -> dict[str, Any]:
        return self.save_config({"pet_size_percent": percent})
