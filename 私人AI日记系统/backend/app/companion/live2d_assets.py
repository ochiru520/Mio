"""Companion live2d assets; dependencies are explicitly injected."""
from __future__ import annotations

from typing import Any, Callable
from io import BytesIO
from PIL import Image
from pathlib import Path
from datetime import datetime
import json
import os
import re
import shutil
import time


class Live2DAssetsService:
    """Domain implementation; constructor callbacks preserve facade test seams."""

    def __init__(self, *,
                 dep_LIVE2D_MODELS: Callable[..., Any],
                 dep__decode_image_data_url: Callable[..., Any],
                 dep_load_config: Callable[..., Any],
                 dep_save_config: Callable[..., Any],
                 ) -> None:
        self._dep_LIVE2D_MODELS = dep_LIVE2D_MODELS
        self._dep__decode_image_data_url = dep__decode_image_data_url
        self._dep_load_config = dep_load_config
        self._dep_save_config = dep_save_config

    def live2d_state_dir(self) -> Path:
        configured_state_root = os.getenv("MIO_DESKTOP_STATE_DIR", "").strip()
        state_root = Path(configured_state_root) if configured_state_root else (
            Path("D:/Mio数据")
            if Path("D:/").exists()
            else Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "MioAgent"
        )
        return (state_root / "Live2D桌宠").resolve()


    def _live2d_models_dir(self) -> Path:
        path = self.live2d_state_dir() / "models"
        path.mkdir(parents=True, exist_ok=True)
        return path


    def _live2d_runtime_path(self) -> Path:
        return self.live2d_state_dir() / "runtime.json"


    def _read_json_object(self, path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法读取 {path.name}。") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{path.name} 必须是 JSON 对象。")
        return value


    def _safe_child(self, root: Path, relative: object) -> Path:
        text = str(relative or "").replace("\\", "/").strip()
        if not text:
            raise ValueError("Live2D 模型引用了空文件路径。")
        candidate = (root / text).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError("Live2D 模型包含越界文件路径。") from exc
        return candidate


    def _live2d_capabilities(self, document: dict[str, Any]) -> dict[str, Any]:
        references = document.get("FileReferences")
        references = references if isinstance(references, dict) else {}
        raw_motions = references.get("Motions")
        raw_motions = raw_motions if isinstance(raw_motions, dict) else {}
        motions = [
            {"name": str(name), "count": len(entries) if isinstance(entries, list) else 0}
            for name, entries in raw_motions.items()
        ]
        names = [str(item["name"]) for item in motions]

        def match(*patterns: str) -> str:
            return next((name for name in names if any(re.search(pattern, name, re.I) for pattern in patterns)), "")

        idle = match(r"^idle$", r"idle|wait|stand|breath") or (names[0] if names else "")
        touch = match(r"^tapbody$", r"tap|touch|click|body") or next((name for name in names if name != idle), idle)
        slots = {
            "idle": idle,
            "touch": touch,
            "think": match(r"think|ponder|question|wonder|confus") or idle,
            "speak": match(r"speak|talk|voice|mouth|chat") or idle,
            "observe": match(r"observe|watch|look|search|scan") or idle,
            "cheerful": match(r"happy|joy|cheer|smile|laugh|win|victory|success") or touch or idle,
            "concerned": match(r"sad|worry|concern|trouble|down|lose|defeat") or idle,
            "alert": match(r"alert|angry|serious|surprise|shock|danger") or touch or idle,
            "attention": match(r"attention|curious|notice|question|look") or idle,
            "shy": match(r"shy|blush|embarrass") or idle,
        }
        slots = {key: value for key, value in slots.items() if value}
        expressions = references.get("Expressions")
        return {
            "format": str(document.get("Version") or "Cubism 4"),
            "motions": motions,
            "expressions": expressions if isinstance(expressions, list) else [],
            "physics": bool(references.get("Physics")),
            "pose": bool(references.get("Pose")),
            "idleGroup": idle,
            "tapGroup": touch,
            "motionSlots": slots,
            "unassignedMotions": [name for name in names if name not in set(slots.values())],
        }


    def _register_unlisted_live2d_expressions(self,
        document: dict[str, Any],
        model_parent: Path,
        files: list[Path],
    ) -> bool:
        references = document.get("FileReferences")
        if not isinstance(references, dict):
            return False
        raw_expressions = references.get("Expressions")
        expressions = list(raw_expressions) if isinstance(raw_expressions, list) else []
        known_files = {
            str(item.get("File") or "").replace("\\", "/").lower()
            for item in expressions
            if isinstance(item, dict)
        }
        known_names = {
            str(item.get("Name") or "").strip().lower()
            for item in expressions
            if isinstance(item, dict)
        }
        changed = False
        for expression_path in sorted(
            (item for item in files if item.name.lower().endswith(".exp3.json")),
            key=lambda item: str(item).lower(),
        ):
            try:
                relative = str(expression_path.relative_to(model_parent)).replace("\\", "/")
            except ValueError:
                continue
            name = expression_path.name[:-len(".exp3.json")].strip() or "Expression"
            if relative.lower() in known_files:
                continue
            unique_name = name
            suffix = 2
            while unique_name.lower() in known_names:
                unique_name = f"{name}-{suffix}"
                suffix += 1
            expressions.append({"Name": unique_name, "File": relative})
            known_files.add(relative.lower())
            known_names.add(unique_name.lower())
            changed = True
        if changed or (expressions and not isinstance(raw_expressions, list)):
            references["Expressions"] = expressions
        return changed


    def _live2d_preview_candidate(self, source_root: Path, files: list[Path]) -> Path | None:
        image_suffixes = {".png", ".jpg", ".jpeg", ".webp"}
        candidates = []
        for path in files:
            if path.suffix.lower() not in image_suffixes:
                continue
            relative_parts = [part.lower() for part in path.relative_to(source_root).parts[:-1]]
            if any("texture" in part or "贴图" in part for part in relative_parts):
                continue
            name = path.stem.lower()
            exact_preview = path.name.lower() in {"preview.png", "preview.jpg", "preview.jpeg", "preview.webp"}
            keyword = any(word in name for word in ("preview", "cover", "icon", "avatar", "thumbnail", "立绘", "头像", "封面"))
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            candidates.append((0 if exact_preview else 1 if keyword else 2, -size, len(path.parts), path))
        return min(candidates, default=(0, 0, 0, None))[-1]


    def _custom_live2d_models(self) -> list[dict[str, Any]]:
        models: list[dict[str, Any]] = []
        for model_root in sorted(self._live2d_models_dir().iterdir(), key=lambda item: item.name.lower()):
            if not model_root.is_dir():
                continue
            metadata_path = model_root / "mio-model.json"
            if not metadata_path.is_file():
                continue
            try:
                metadata = self._read_json_object(metadata_path)
                model_path = self._safe_child(model_root, metadata.get("modelPath"))
                if not model_path.is_file():
                    continue
                document = self._read_json_object(model_path)
                model_files = [item for item in model_root.rglob("*") if item.is_file()]
                expressions_changed = self._register_unlisted_live2d_expressions(
                    document,
                    model_path.parent,
                    model_files,
                )
                refreshed_capabilities = self._live2d_capabilities(document)
                metadata_changed = expressions_changed or metadata.get("capabilities") != refreshed_capabilities
                if expressions_changed:
                    model_path.write_text(
                        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                metadata["capabilities"] = refreshed_capabilities
                if not str(metadata.get("previewPath") or "").strip():
                    preview_candidate = self._live2d_preview_candidate(model_root, model_files)
                    if preview_candidate is not None:
                        metadata["previewPath"] = str(
                            preview_candidate.relative_to(model_root)
                        ).replace("\\", "/")
                        metadata_changed = True
                if metadata_changed:
                    metadata_path.write_text(
                        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
            except ValueError:
                continue
            preview_relative = str(metadata.get("previewPath") or "").replace("\\", "/").strip()
            preview_path = None
            if preview_relative:
                try:
                    candidate = self._safe_child(model_root, preview_relative)
                    preview_path = candidate if candidate.is_file() else None
                except ValueError:
                    preview_path = None
            models.append({
                "id": model_root.name,
                "name": str(metadata.get("name") or model_root.name),
                "description": str(metadata.get("sourceLabel") or "本地导入的 Live2D 模型"),
                "model_path": str(model_path.relative_to(model_root)).replace("\\", "/"),
                "preview_path": preview_relative if preview_path else "",
                "preview_url": f"/api/companion/live2d/models/{model_root.name}/preview" if preview_path else "",
                "license": str((metadata.get("authorization") or {}).get("notice") or "授权状态未确认"),
                "source": "imported",
                "motion_count": sum(int(item.get("count") or 0) for item in (metadata.get("capabilities") or {}).get("motions", [])),
                "capabilities": metadata.get("capabilities") if isinstance(metadata.get("capabilities"), dict) else {},
                "imported": True,
            })
        return models


    def available_live2d_models(self) -> list[dict[str, Any]]:
        return [dict(model, imported=False) for model in self._dep_LIVE2D_MODELS()] + self._custom_live2d_models()


    def _live2d_model_ids(self) -> set[str]:
        return {str(model["id"]) for model in self.available_live2d_models()}


    def _write_live2d_runtime_selected(self, model_id: str) -> None:
        runtime_path = self._live2d_runtime_path()
        runtime_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            current = json.loads(runtime_path.read_text(encoding="utf-8")) if runtime_path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            current = {}
        if not isinstance(current, dict):
            current = {}
        current["selectedModelId"] = model_id if model_id in {str(model["id"]) for model in self._custom_live2d_models()} else ""
        temporary = runtime_path.with_suffix(".tmp.json")
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(runtime_path)


    def select_live2d_model(self, model_id: str) -> str:
        clean = str(model_id or "").strip()
        if clean not in self._live2d_model_ids():
            raise ValueError("没有找到这个 Live2D 模型。")
        self._write_live2d_runtime_selected(clean)
        return clean


    def import_live2d_model_directory(self, source: str | Path, display_name: str = "") -> dict[str, Any]:
        source_root = Path(source).expanduser().resolve()
        if not source_root.is_dir():
            raise ValueError("请选择一个有效的 Live2D 模型目录。")
        files = [item for item in source_root.rglob("*") if item.is_file()]
        if len(files) > 5000:
            raise ValueError("模型文件超过 5000 个，无法导入。")
        total_bytes = sum(item.stat().st_size for item in files)
        if total_bytes > 500 * 1024 * 1024:
            raise ValueError("模型目录不能超过 500MB。")
        model_files = sorted((item for item in files if item.name.lower().endswith(".model3.json")), key=str)
        if not model_files:
            raise ValueError("所选目录中没有 .model3.json 文件。")
        source_model = model_files[0]
        document = self._read_json_object(source_model)
        references = document.get("FileReferences")
        if not isinstance(references, dict):
            raise ValueError("model3.json 缺少 FileReferences。")
        model_parent = source_model.parent
        moc_path = self._safe_child(model_parent, references.get("Moc"))
        if not moc_path.is_file() or moc_path.suffix.lower() != ".moc3":
            raise ValueError("模型缺少可用的 .moc3 文件。")
        textures = references.get("Textures")
        if not isinstance(textures, list) or not textures:
            raise ValueError("模型没有纹理文件。")
        for texture in textures:
            if not self._safe_child(model_parent, texture).is_file():
                raise ValueError(f"模型纹理不存在：{texture}")

        clean_base = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff_-]+", "-", source_root.name).strip("-")[:48] or "model"
        model_id = f"{clean_base}-{int(time.time() * 1000):x}"
        target_root = self._live2d_models_dir() / model_id
        staging = self._live2d_models_dir() / f".{model_id}.importing"
        expressions_changed = self._register_unlisted_live2d_expressions(document, model_parent, files)
        preview = self._live2d_preview_candidate(source_root, files)
        license_files = [
            str(item.relative_to(source_root)).replace("\\", "/")
            for item in files
            if re.search(r"(^|[\\/])(licen[sc]e|copying|notice)(\.|$|[-_])", str(item.relative_to(source_root)), re.I)
        ][:20]
        try:
            shutil.copytree(source_root, staging)
            if expressions_changed:
                staged_model = staging / source_model.relative_to(source_root)
                staged_model.write_text(
                    json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            metadata = {
                "name": (display_name.strip() or source_root.name)[:100],
                "modelPath": str(source_model.relative_to(source_root)).replace("\\", "/"),
                "previewPath": str(preview.relative_to(source_root)).replace("\\", "/") if preview else "",
                "sourceLabel": str(source_root),
                "importedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
                "capabilities": self._live2d_capabilities(document),
                "authorization": {
                    "status": "files_found" if license_files else "unverified",
                    "licenseFiles": license_files,
                    "notice": "已发现授权文件，使用和分发前请核对。" if license_files else "未发现授权文件；只允许本机使用，禁止随安装包分发。",
                    "distributionAllowed": False,
                },
            }
            (staging / "mio-model.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            staging.replace(target_root)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        self._write_live2d_runtime_selected(model_id)
        return next(model for model in self.available_live2d_models() if model["id"] == model_id)


    def delete_live2d_model(self, model_id: str) -> bool:
        clean = str(model_id or "").strip()
        if clean == "hiyori":
            raise ValueError("内置 Live2D 模型不能删除。")
        target = (self._live2d_models_dir() / clean).resolve()
        try:
            target.relative_to(self._live2d_models_dir())
        except ValueError as exc:
            raise ValueError("模型编号无效。") from exc
        if not target.is_dir() or not (target / "mio-model.json").is_file():
            return False
        shutil.rmtree(target)
        config = self._dep_load_config()
        if str(config.get("live2d_model_id") or "") == clean:
            self._dep_save_config({"live2d_model_id": "hiyori"})
        self._write_live2d_runtime_selected("")
        return True


    def live2d_model_preview_path(self, model_id: str) -> Path | None:
        clean = str(model_id or "").strip()
        model = next((item for item in self._custom_live2d_models() if item["id"] == clean), None)
        if model is None or not model.get("preview_path"):
            return None
        path = self._safe_child(self._live2d_models_dir() / clean, model["preview_path"])
        return path if path.is_file() else None


    def save_live2d_model_preview_data_url(self, model_id: str, data_url: str) -> Path:
        clean = str(model_id or "").strip()
        model_root = (self._live2d_models_dir() / clean).resolve()
        try:
            model_root.relative_to(self._live2d_models_dir())
        except ValueError as exc:
            raise ValueError("模型编号无效。") from exc
        metadata_path = model_root / "mio-model.json"
        if not metadata_path.is_file():
            raise ValueError("只有本地导入的 Live2D 模型可以更换封面。")
        raw = self._dep__decode_image_data_url(data_url, max_bytes=12 * 1024 * 1024, label="Live2D 封面")
        target = model_root / "mio-preview.png"
        temporary = model_root / "mio-preview.tmp.png"
        try:
            with Image.open(BytesIO(raw)) as source:
                source.load()
                image = source.convert("RGBA")
                image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                image.save(temporary, format="PNG", optimize=True)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise ValueError("这个文件不是可用的图片。") from exc
        temporary.replace(target)
        metadata = self._read_json_object(metadata_path)
        metadata["previewPath"] = target.name
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return target
