from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PresetKind = Literal["character", "style", "project"]
MediaType = Literal["image", "video"]
CreationBackend = Literal["auto", "comfyui", "remote_api"]
RemoteApiMode = Literal["auto", "images", "responses"]
ImageLoraChoice = Literal[
    "highres_boost",
    "sensual_style",
    "clear_lineart",
    "soft_cel_pastel",
]


class CreationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreationPresetRequest(CreationModel):
    kind: PresetKind
    name: str = Field(min_length=1, max_length=80)
    content: str = Field(default="", max_length=8000)
    negative_prompt: str = Field(default="", max_length=4000)
    params: dict[str, Any] = Field(default_factory=dict)
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=8)
    approved: bool = True

    @model_validator(mode="after")
    def require_confirmed_content(self) -> "CreationPresetRequest":
        if not self.approved:
            raise ValueError("长期创作预设只保存已经确认的内容。")
        if not self.content and not self.reference_asset_ids:
            raise ValueError("预设需要填写规则或添加参考素材。")
        return self


class CreationAssetRequest(CreationModel):
    name: str = Field(min_length=1, max_length=160)
    mime_type: str = Field(default="", max_length=100)
    data_url: str = Field(min_length=16)


class CreationJobRequest(CreationModel):
    media_type: MediaType = "image"
    backend: CreationBackend = "auto"
    workflow_id: str = Field(default="", max_length=100)
    prompt: str = Field(min_length=1, max_length=12000)
    negative_prompt: str = Field(default="", max_length=6000)
    character_preset_id: str = Field(default="", max_length=80)
    style_preset_id: str = Field(default="", max_length=80)
    project_preset_id: str = Field(default="", max_length=80)
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=8)
    width: int | None = Field(default=None, ge=256, le=4096)
    height: int | None = Field(default=None, ge=256, le=4096)
    steps: int | None = Field(default=None, ge=1, le=150)
    cfg: float | None = Field(default=None, ge=0, le=30)
    lora_choices: list[ImageLoraChoice] | None = Field(default=None, max_length=4)
    seed: int = Field(default=-1, ge=-1, le=0x7FFFFFFFFFFFFFFF)
    batch_size: int = Field(default=1, ge=1, le=4)
    duration_seconds: float | None = Field(default=None, ge=1, le=30)
    fps: int | None = Field(default=None, ge=1, le=60)
    max_frames: int | None = Field(default=None, ge=1, le=1800)
    provider_id: str = Field(default="", max_length=160)
    model_id: str = Field(default="", max_length=240)
    remote_api_mode: RemoteApiMode = "auto"
    idempotency_key: str = Field(default="", max_length=160, pattern=r"^[A-Za-z0-9._:-]*$")

    @field_validator("reference_asset_ids")
    @classmethod
    def unique_asset_ids(cls, value: list[str]) -> list[str]:
        cleaned = [str(item).strip()[:80] for item in value if str(item).strip()]
        return list(dict.fromkeys(cleaned))

    @model_validator(mode="after")
    def validate_backend_and_media(self) -> "CreationJobRequest":
        if self.media_type == "video" and self.backend == "remote_api":
            raise ValueError("当前远程中转站只接入图片生成；视频请使用本地 ComfyUI。")
        if self.backend == "remote_api" and (not self.provider_id or not self.model_id):
            raise ValueError("远程图片生成需要选择供应商并填写图片模型 ID。")
        return self


class CreationToolImageRequest(CreationModel):
    prompt: str = Field(min_length=1, max_length=12000)
    negative_prompt: str = Field(default="", max_length=6000)
    character_preset_id: str = Field(default="", max_length=80)
    style_preset_id: str = Field(default="", max_length=80)
    project_preset_id: str = Field(default="", max_length=80)
    reference_asset_id: str = Field(default="", max_length=80)
    width: int | None = Field(default=None, ge=256, le=4096)
    height: int | None = Field(default=None, ge=256, le=4096)
    steps: int | None = Field(default=None, ge=1, le=150)
    cfg: float | None = Field(default=None, ge=0, le=30)
    seed: int = Field(default=-1, ge=-1, le=0x7FFFFFFFFFFFFFFF)


class CreationToolVideoRequest(CreationModel):
    prompt: str = Field(min_length=1, max_length=12000)
    reference_asset_id: str = Field(min_length=1, max_length=80)
    character_preset_id: str = Field(default="", max_length=80)
    style_preset_id: str = Field(default="", max_length=80)
    project_preset_id: str = Field(default="", max_length=80)
    duration_seconds: float | None = Field(default=None, ge=1, le=30)
    fps: int | None = Field(default=None, ge=1, le=60)
    seed: int = Field(default=-1, ge=-1, le=0x7FFFFFFFFFFFFFFF)


class CreationToolRemoteImageRequest(CreationToolImageRequest):
    provider_id: str = Field(min_length=1, max_length=160)
    model_id: str = Field(min_length=1, max_length=240)
    remote_api_mode: RemoteApiMode = "auto"


class CreationJobIdRequest(CreationModel):
    job_id: str = Field(min_length=1, max_length=80)


__all__ = [
    "CreationAssetRequest",
    "CreationJobIdRequest",
    "CreationJobRequest",
    "CreationPresetRequest",
    "CreationToolImageRequest",
    "CreationToolRemoteImageRequest",
    "CreationToolVideoRequest",
    "ImageLoraChoice",
]
