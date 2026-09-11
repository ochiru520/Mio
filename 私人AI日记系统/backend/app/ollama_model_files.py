"""Validate Ollama model files without consulting a running server."""

import json
import re
from pathlib import Path


def manifest_complete(manifest: Path, models_root: Path) -> bool:
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict) or not isinstance(payload.get("layers"), list) or not payload["layers"]:
            return False
        for item in [payload.get("config"), *payload["layers"]]:
            if not isinstance(item, dict):
                return False
            digest = str(item.get("digest") or "")
            if not re.fullmatch(r"sha256:[a-zA-Z0-9]+", digest):
                return False
            expected_size = int(item.get("size") or 0)
            blob = models_root / "blobs" / digest.replace(":", "-")
            if expected_size <= 0 or not blob.is_file() or blob.stat().st_size != expected_size:
                return False
        return True
    except (OSError, ValueError, TypeError):
        return False
