from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


SCRUB_PREFIXES = (
    "MIO_",
    "OPENAI_",
    "QQ_",
    "NAPCAT_",
    "PERSONA_",
    "RUNTIME_",
    "TALENT_",
    "VOICE_",
)
SCRUB_NAMES = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}


def build_clean_environment(runtime_root: Path, backend_root: Path) -> dict[str, str]:
    environment = dict(os.environ)
    for name in list(environment):
        upper = name.upper().lstrip("\ufeff")
        if upper in SCRUB_NAMES or any(upper.startswith(prefix) for prefix in SCRUB_PREFIXES):
            environment.pop(name, None)
    environment.update(
        {
            "MIO_RUNTIME_ROOT": str(runtime_root),
            "MIO_DESKTOP_STATE_DIR": str(runtime_root.parent / "桌面状态"),
            "MIO_DISABLE_DOTENV": "1",
            "PYTHONPATH": str(backend_root),
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return environment


def _sha256_if_present(path: Path) -> str:
    if not path.is_file():
        return "missing"
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _protected_source_state(project_root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(project_root)): _sha256_if_present(path)
        for path in (
            project_root / "backend" / ".env",
            project_root / "数据" / "personal_ai.db",
            project_root / "数据" / "模型供应商.json",
        )
    }


def _clean_preflight(python: Path, backend_root: Path, environment: dict[str, str]) -> dict[str, object]:
    code = (
        "import json; "
        "from app.config import settings; "
        "from app.model_registry import list_model_profiles; "
        "print(json.dumps({"
        "'runtime_root': str(settings.project_root), "
        "'database_exists': settings.db_path.exists(), "
        "'dotenv_exists': (settings.project_root / '.env').exists() or (settings.project_root / 'backend' / '.env').exists(), "
        "'provider_count': len(list_model_profiles())"
        "}, ensure_ascii=False))"
    )
    completed = subprocess.run(
        [str(python), "-c", code],
        cwd=backend_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout.strip())
    if payload["database_exists"] or payload["dotenv_exists"] or payload["provider_count"]:
        raise RuntimeError(f"干净环境预检失败：{json.dumps(payload, ensure_ascii=False)}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="在无 .env、无个人数据和无供应商的临时运行根执行后端回归。")
    parser.add_argument("--pattern", default="test_*.py")
    args = parser.parse_args()

    backend_root = Path(__file__).resolve().parents[1]
    project_root = backend_root.parent
    python = Path(sys.executable).resolve()
    before = _protected_source_state(project_root)
    with tempfile.TemporaryDirectory(prefix="mio-clean-regression-") as temporary_directory:
        temporary_root = Path(temporary_directory)
        runtime_root = temporary_root / "业务运行根"
        runtime_root.mkdir(parents=True)
        environment = build_clean_environment(runtime_root, backend_root)
        preflight = _clean_preflight(python, backend_root, environment)
        print("CLEAN_PREFLIGHT=" + json.dumps(preflight, ensure_ascii=False), flush=True)
        completed = subprocess.run(
            [str(python), "-m", "unittest", "discover", "-s", "tests", "-p", args.pattern],
            cwd=backend_root,
            env=environment,
            check=False,
        )
    after = _protected_source_state(project_root)
    if before != after:
        print("CLEAN_SOURCE_GUARD=failed", file=sys.stderr)
        return 2
    print("CLEAN_SOURCE_GUARD=ok", flush=True)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
