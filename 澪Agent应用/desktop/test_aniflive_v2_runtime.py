"""CPU-only checks: fail closed on Pro bundles, wrong weights and wrong engines."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "aniflive_v2_runtime_tests", Path(__file__).resolve().parents[1] / "scripts/aniflive_v2_runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        sources, stages = {}, {}
        for name in ("gpt", "sovits"):
            path = self.root / (name + ".weight")
            path.write_bytes(("test-original-" + name).encode())
            sources[name] = {"path": path.name, "sha256": runtime.file_sha256(path)}
        for name in runtime.STAGES:
            path = self.root / (name + ".engine")
            path.write_bytes(("v2-stage-" + name).encode())
            stages[name] = {"path": path.name, "sha256": runtime.file_sha256(path)}
        self.manifest = {"kind": runtime.KIND, "model_family": runtime.MODEL_FAMILY,
                         "source_weights": sources, "stages": stages}
        self.config = {"model": {"version": "v2", "semantic_frame_rate": "25hz"},
                       "data": {"sampling_rate": 32000, "hop_length": 640}}
        self.expected = {name: item["sha256"] for name, item in sources.items()}
        self.save()

    def save(self):
        (self.root / "v2-manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        (self.root / "config.json").write_text(json.dumps(self.config), encoding="utf-8")

    def test_original_v2_eight_hashed_stages_accept_shared_asset_paths(self):
        self.manifest["stages"]["bert"]["path"] = str(self.root / "bert.engine")
        self.save()
        value = runtime.validate_bundle(self.root, self.expected)
        self.assertEqual(set(value["paths"]), set(runtime.STAGES))
        self.assertEqual(value["config"]["model"]["version"], "v2")

    def test_wrong_original_weights_rejected_before_torch_import(self):
        expected = dict(self.expected, gpt="0" * 64)
        with patch.object(runtime.importlib, "import_module") as importing:
            with self.assertRaisesRegex(ValueError, "原Mio权重"):
                runtime.create_engine(engine_dir=self.root, bert_path=self.root,
                                      expected_weight_sha256=expected)
            importing.assert_not_called()

    def test_changed_engine_and_changed_source_rejected(self):
        for name in ("gpt.weight", "sovits_stream.engine"):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b"changed")
                with self.assertRaisesRegex(ValueError, "SHA256"):
                    runtime.validate_bundle(self.root, self.expected)
                path.write_bytes(original)

    def test_pro_model_or_sv_stage_cannot_be_mislabelled_v2(self):
        self.config["model"]["version"] = "v2ProPlus"
        self.save()
        with self.assertRaises(ValueError):
            runtime.validate_bundle(self.root, self.expected)
        self.config["model"]["version"] = "v2"
        self.manifest["stages"]["sv_embedding"] = self.manifest["stages"]["ssl"]
        self.save()
        with self.assertRaisesRegex(ValueError, "八个阶段"):
            runtime.validate_bundle(self.root, self.expected)

    def test_loader_uses_eight_engines_and_inherits_frontend_without_pro_init(self):
        loaded = []
        class Upstream:
            def __init__(self, *_args, **_kwargs):
                raise AssertionError("Pro-only constructor must not run")
            def get_phones_and_bert(self, *_args):
                return [5], "bert", "text"
        def trt_module(path, _device, _stream):
            stage = Path(path).stem
            loaded.append(stage)
            inputs, outputs = runtime._IO[stage]
            return SimpleNamespace(input_names=list(inputs), output_names=list(outputs),
                tensor_dtype={"logits": "float32", "bert_feature": "float16"},
                input_max_shapes={"pred_semantic": (1, 1, 250), "text_seq": (1, 100),
                                  "refer_spec": (1, 1025, 512)},
                engine=SimpleNamespace(get_tensor_shape=lambda name:
                    (1, 1025) if name == "logits" else (1, 192, 4)))
        torch = SimpleNamespace(float32="float32", device=lambda value: value,
            cuda=SimpleNamespace(is_available=lambda: True, Stream=lambda **_: object()))
        source = SimpleNamespace(GPTSoVITS_TRT_Inference=Upstream, ShapeProfiler=lambda: object(),
                                 TRTModule=trt_module)
        transformer = SimpleNamespace(AutoTokenizer=SimpleNamespace(from_pretrained=lambda *_a, **_k: "tokenizer"))
        modules = {"run_trt_inference": source, "torch": torch, "transformers": transformer}
        with patch.object(runtime.importlib, "import_module", side_effect=modules.__getitem__):
            engine = runtime.create_engine(engine_dir=self.root, bert_path=self.root,
                                           expected_weight_sha256=self.expected)
        self.assertEqual(loaded, list(runtime.STAGES))
        self.assertEqual(engine.get_phones_and_bert("text", "ja", "v2"), ([5], "bert", "text"))
        self.assertFalse(engine.v2_metadata["uses_speaker_embedding"])
        self.assertFalse(engine.v2_metadata["original_mio_weights_loaded"])
        self.assertEqual(engine.v2_metadata["weight_sha256"], self.expected)
        self.assertFalse(engine.v2_metadata["numerical_verified"])
        self.assertEqual(engine.sovits_max_text_len, 100)


class ContractTests(unittest.TestCase):
    def test_only_actual_original_checkpoint_pair_is_recognized(self):
        actual = dict(runtime.ORIGINAL_MIO_WEIGHT_SHA256)
        self.assertTrue(runtime.is_original_mio_weights(actual))
        self.assertTrue(runtime.is_original_mio_weights({key: value.upper() for key, value in actual.items()}))
        self.assertFalse(runtime.is_original_mio_weights(dict(actual, sovits="0" * 64)))
        self.assertFalse(runtime.is_original_mio_weights({"profile": "mio", "name": "mio"}))
        self.assertFalse(runtime.is_original_mio_weights({"gpt": actual["gpt"]}))

    def module(self, stage):
        inputs, outputs = runtime._IO[stage]
        return SimpleNamespace(input_names=list(inputs), output_names=list(outputs),
            tensor_dtype={"logits": "float32"},
            engine=SimpleNamespace(get_tensor_shape=lambda name:
                (1, 1025) if name == "logits" else (1, 192, 4)))

    def test_decoder_contract_rejects_pro_sv_input(self):
        module = self.module("sovits_stream")
        runtime._validate_stage("sovits_stream", module, SimpleNamespace(float32="float32"))
        module.input_names.append("sv_emb")
        with self.assertRaises(ValueError):
            runtime._validate_stage("sovits_stream", module, SimpleNamespace(float32="float32"))

    def test_legacy_top50_logits_are_rejected(self):
        for stage in ("gpt_encoder", "gpt_step"):
            with self.subTest(stage=stage):
                module = self.module(stage)
                module.output_names.remove("logits")
                with self.assertRaises(ValueError):
                    runtime._validate_stage(stage, module, SimpleNamespace(float32="float32"))

    def test_logits_wrong_dtype_shape_and_odd_overlap_rejected(self):
        module = self.module("gpt_step")
        module.tensor_dtype["logits"] = "float16"
        with self.assertRaises(ValueError):
            runtime._validate_stage("gpt_step", module, SimpleNamespace(float32="float32"))
        module = self.module("gpt_step")
        module.engine.get_tensor_shape = lambda _: (1, 50)
        with self.assertRaises(ValueError):
            runtime._validate_stage("gpt_step", module, SimpleNamespace(float32="float32"))
        module = self.module("sovits_stream")
        module.engine.get_tensor_shape = lambda _: (1, 192, 3)
        with self.assertRaises(ValueError):
            runtime._validate_stage("sovits_stream", module, SimpleNamespace(float32="float32"))

    def test_dummy_sv_is_only_zero_placeholder_not_an_engine(self):
        calls = []
        torch = SimpleNamespace(zeros=lambda shape, **kwargs: calls.append((shape, kwargs)) or "zeros")
        dummy = runtime._UnusedSpeakerEmbedding(torch, "cuda", "fp16")
        self.assertEqual(dummy({"audio": object()}), {"sv_embedding": "zeros"})
        self.assertEqual(calls, [((1, 512), {"device": "cuda", "dtype": "fp16"})])

    def test_native_sampling_is_selected_then_environment_restored(self):
        with patch.dict(os.environ, {"ANIFLIVE_TTS_SEMANTIC_SAMPLING": "legacy-topk-v1",
                                     "ANIFLIVE_TTS_SEMANTIC_BACKEND": "short-step"}):
            with self.assertRaises(RuntimeError):
                with runtime._construction_environment():
                    self.assertEqual(os.environ["ANIFLIVE_TTS_SEMANTIC_SAMPLING"], "native-v2proplus-v1")
                    self.assertEqual(os.environ["ANIFLIVE_TTS_SEMANTIC_BACKEND"], "transformer")
                    raise RuntimeError("construction failed")
            self.assertEqual(os.environ["ANIFLIVE_TTS_SEMANTIC_SAMPLING"], "legacy-topk-v1")
            self.assertEqual(os.environ["ANIFLIVE_TTS_SEMANTIC_BACKEND"], "short-step")


if __name__ == "__main__":
    unittest.main()
