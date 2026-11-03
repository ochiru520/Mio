"""Loopback official v5 Turbo API using an existing local checkout and weights."""
from pathlib import Path
import argparse
import os
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--port", type=int, default=9885)
    args = parser.parse_args()
    repo = args.repo.resolve(strict=True)
    os.chdir(repo)
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    sys.path[:0] = [str(repo), str(repo / "GPT_SoVITS")]
    import torch
    from local_tts_memory import install
    reclaimer = install(torch)
    try:
        reclaimer.reclaim()
        serve(args, repo, torch, reclaimer)
    finally:
        reclaimer.close()


def serve(args, repo, torch, reclaimer):
    import yaml
    original_load, original_dtype = torch.load, torch.get_default_dtype()

    def mapped_load(file, *values, **kwargs):
        if isinstance(file, (str, Path)):
            with open(file, "rb") as check:
                if check.read(2) == b"PK":
                    kwargs.setdefault("mmap", True)
        return original_load(file, *values, **kwargs)

    pretrained = repo / "GPT_SoVITS/pretrained_models"
    config = {"custom": {"version": "v5turbo", "device": "cuda", "is_half": True,
        "t2s_weights_path": str(pretrained / "s1v3.ckpt"), "vits_weights_path": str(pretrained / "gsv-v5-pretrained/s2Gv5turbo.pth"),
        "bert_base_path": str(pretrained / "chinese-roberta-wwm-ext-large"), "cnhuhbert_base_path": str(pretrained / "chinese-hubert-base")}}
    with tempfile.TemporaryDirectory(prefix="mio-v5-config-") as folder:
        config_path = Path(folder) / "tts.yaml"
        config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
        sys.argv = ["api_v2.py", "-a", "127.0.0.1", "-p", str(args.port), "-c", str(config_path)]
        torch.load = mapped_load
        torch.set_default_dtype(torch.float16)
        try:
            import api_v2
        finally:
            torch.load = original_load
            torch.set_default_dtype(original_dtype)

        @api_v2.APP.get("/health")
        def health():
            return {"ready": True, "model_version": api_v2.tts_pipeline.configs.version,
                    "streaming": "text_segments", "source": "official-v5-with-mio-local-loader",
                    "cpu_memory_reclamation": reclaimer.status()}

        import uvicorn
        uvicorn.run(api_v2.APP, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
