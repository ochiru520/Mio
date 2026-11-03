"""Stream user-selected voice weights into owned storage without loading model code."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import threading
import uuid

MAX_WEIGHT_BYTES = 2 * 1024**3
DISK_RESERVE_BYTES = 8 * 1024**3
_COMMIT_LOCK = threading.RLock()
_FIELDS = {"gpt": (".ckpt", "gpt_sovits_gpt_weights"), "sovits": (".pth", "gpt_sovits_sovits_weights")}
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class WeightTooLarge(ValueError):
    pass


def _plain_chain(path: Path) -> None:
    """Check each existing ancestor before resolving, including Windows junctions."""
    for current in (*reversed(path.parents), path):
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("音色权重目录不能使用符号链接或目录联接。")


def _profile(config: dict, profile_id: str) -> dict:
    selected = (config.get("voice_profiles") or {}).get(profile_id)
    if not isinstance(selected, dict) or selected.get("engine", "gpt_sovits") != "gpt_sovits":
        raise ValueError("请选择已有的 GPT-SoVITS 音色后再导入权重。")
    return selected


class WeightUpload:
    """One bounded upload; a new unique file never replaces an existing model."""

    def __init__(self, *, profile_id: str, kind: str, filename: str,
                 root: Path, load_config, save_config, content_length: int | None = None):
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", profile_id)
                or profile_id.endswith(".") or profile_id.split(".", 1)[0].upper() in _RESERVED_NAMES):
            raise ValueError("音色 ID 无效。")
        if kind not in _FIELDS:
            raise ValueError("权重类型必须为 GPT 或 SoVITS。")
        extension, self.field = _FIELDS[kind]
        if (not filename or len(filename) > 200 or filename != Path(filename).name
                or any(character in filename for character in '/\\:*?"<>|')
                or any(ord(character) < 32 for character in filename)
                or filename.endswith((".", " ")) or Path(filename).suffix.lower() != extension):
            raise ValueError(f"请选择不含路径的 {extension} 权重文件。")
        if content_length is not None and (content_length <= 0 or content_length > MAX_WEIGHT_BYTES):
            raise WeightTooLarge("权重文件不能为空或超过 2 GiB。")
        self.profile_id, self.kind, self.filename = profile_id, kind, filename
        self.load_config, self.save_config = load_config, save_config
        self.original_field = _profile(load_config(), profile_id).get(self.field, "")
        self.root = Path(root).absolute()
        _plain_chain(self.root)
        self.folder = self.root / "音色权重" / profile_id
        _plain_chain(self.folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        _plain_chain(self.folder)
        if not self.folder.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("音色权重目录超出数据目录。")
        if shutil.disk_usage(self.folder).free - (content_length or 0) < DISK_RESERVE_BYTES:
            raise OSError("磁盘空间不足，导入后必须保留 8 GiB 空间。")
        key = uuid.uuid4().hex
        self.temporary = self.folder / f".{kind}-{key}.uploading"
        self.target = self.folder / f"{kind}-{key}{extension}"
        self.size, self.digest, self.saved = 0, hashlib.sha256(), False
        descriptor = os.open(self.temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        self.stream = os.fdopen(descriptor, "wb")
        self.file_identity = os.fstat(self.stream.fileno()).st_ino

    def write(self, chunk: bytes) -> None:
        if not chunk:
            return
        if self.size + len(chunk) > MAX_WEIGHT_BYTES:
            raise WeightTooLarge("权重文件超过 2 GiB，已停止导入。")
        if shutil.disk_usage(self.folder).free - len(chunk) < DISK_RESERVE_BYTES:
            raise OSError("磁盘剩余空间不足 8 GiB，已停止导入。")
        self.stream.write(chunk)
        self.size += len(chunk)
        self.digest.update(chunk)

    def commit(self) -> dict:
        if not self.size:
            raise ValueError("权重文件不能为空。")
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.stream.close()
        with _COMMIT_LOCK:
            _plain_chain(self.folder)
            current = self.load_config()
            selected = dict(_profile(current, self.profile_id))
            if selected.get(self.field, "") != self.original_field:
                raise ValueError("导入期间此音色权重已被修改，请刷新后重试。")
            profiles = dict(current["voice_profiles"])
            selected[self.field] = str(self.target.resolve())
            profiles[self.profile_id] = selected
            self.temporary.replace(self.target)
            try:
                self.save_config({"voice_profiles": profiles})
            except BaseException:
                self.target.unlink(missing_ok=True)
                raise
            self.saved = True
        return {"profile_id": self.profile_id, "field": self.field, "name": self.filename,
                "size_bytes": self.size, "sha256": self.digest.hexdigest(), "path": str(self.target.resolve()),
                "validation": "file_saved_not_model_loaded"}

    def close(self) -> None:
        self.stream.close()
        try:
            _plain_chain(self.folder)
            info = self.temporary.lstat()
            if stat.S_ISREG(info.st_mode) and info.st_ino == self.file_identity:
                self.temporary.unlink()
        except (FileNotFoundError, ValueError):
            pass
