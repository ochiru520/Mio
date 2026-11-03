"""Portable conversion contract and small offline shared-frontend checks."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'mio_v2_converter_test', Path(__file__).resolve().parents[1] / 'scripts/aniflive_v2_convert.py')
converter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(converter)


class ConversionArgumentsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'
        self.source.mkdir()
        self.assets = self.root / 'assets'
        self.assets.mkdir()
        for name in ('chinese-hubert-base', 'chinese-roberta-wwm-ext-large'):
            folder = self.assets / name
            folder.mkdir()
            (folder / 'config.json').write_text('{}')
            (folder / 'model.safetensors').write_bytes(b'test-weights')

    def argv(self, stage, operation='export'):
        return [operation, '--stage', stage, '--source', str(self.source),
                '--output', str(self.root / 'engines')]

    def test_shared_exports_do_not_require_developer_checkpoint_paths(self):
        for stage, kind in (('ssl', 'hubert'), ('bert', 'bert')):
            with self.subTest(stage=stage):
                args = converter.parse_args(self.argv(stage) + ['--assets', str(self.assets)])
                self.assertIsNone(args.gpt)
                self.assertIsNone(args.sovits)
                self.assertTrue(converter.asset_directory(args, kind).is_dir())

    def test_explicit_local_hf_folder_overrides_shared_root(self):
        target = self.assets / 'chinese-hubert-base'
        args = converter.parse_args(self.argv('ssl') + ['--hubert', str(target)])
        self.assertEqual(converter.asset_directory(args, 'hubert'), target)

    def test_missing_weights_fail_before_transformer_or_cuda_import(self):
        target = self.assets / 'chinese-hubert-base'
        (target / 'model.safetensors').unlink()
        with self.assertRaisesRegex(ValueError, 'weights are missing'):
            converter.parse_args(self.argv('ssl') + ['--assets', str(self.assets)])

    def test_sharded_hf_checkpoint_accepted(self):
        target = self.assets / 'chinese-hubert-base'
        (target / 'model.safetensors').unlink()
        (target / 'model.safetensors.index.json').write_text('{}')
        self.assertEqual(converter.asset_directory(SimpleNamespace(hubert=target), 'hubert'), target)

    def test_voice_stages_require_their_own_checkpoint_only(self):
        with self.assertRaises(SystemExit):
            converter.parse_args(self.argv('gpt_step'))
        with self.assertRaises(SystemExit):
            converter.parse_args(self.argv('sovits_stream'))
        with self.assertRaises(SystemExit):
            converter.parse_args(self.argv('spectrogram'))

    def test_build_needs_no_pytorch_checkpoints(self):
        for stage in converter.STAGES:
            args = converter.parse_args(self.argv(stage, 'build'))
            self.assertIsNone(args.gpt)
            self.assertIsNone(args.sovits)

    def test_config_only_spectrogram_preserves_stft_contract(self):
        config = {'model': {'version': 'v2', 'semantic_frame_rate': '25hz'},
                  'data': {'filter_length': 2048, 'hop_length': 640,
                           'win_length': 2048, 'sampling_rate': 32000},
                  'symbol_to_id': {'test': 1}}
        target = self.root / 'config.json'
        target.write_text(json.dumps(config))
        args = converter.parse_args(self.argv('spectrogram') + ['--config', str(target)])
        result = converter.spectrogram_config(args, None)
        self.assertEqual(result['data']['sampling_rate'], 32000)
        self.assertEqual(result['symbol_to_id'], config['symbol_to_id'])
        config['data']['hop_length'] = 320
        target.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, 'STFT'):
            converter.spectrogram_config(args, None)
        config['model']['version'] = 'v2ProPlus'
        target.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, 'V2'):
            converter.spectrogram_config(args, None)


try:
    import torch
    from transformers import BertConfig, BertForMaskedLM, HubertConfig, HubertModel
except ImportError:
    torch = None


@unittest.skipUnless(torch is not None, 'Optional neural frontend libraries are not installed')
class SharedNeuralContractTests(unittest.TestCase):
    setUp = ConversionArgumentsTests.setUp
    argv = ConversionArgumentsTests.argv
    def test_hubert_output_axes_match_runtime(self):
        torch.set_num_threads(2)
        model = HubertModel(HubertConfig(hidden_size=8, num_hidden_layers=1,
            num_attention_heads=2, intermediate_size=16, conv_dim=(8, 8, 8),
            conv_stride=(5, 2, 2), conv_kernel=(10, 3, 3),
            num_conv_pos_embedding_groups=2, mask_time_prob=0)).eval()
        args = converter.parse_args(self.argv('ssl') + ['--assets', str(self.assets)])
        with patch('transformers.HubertModel.from_pretrained', return_value=model) as loading:
            wrapper, inputs, names, outputs, axes = converter.shared_wrapper(
                args, torch, torch.nn, torch.nn.functional)
        self.assertTrue(loading.call_args.kwargs['local_files_only'])
        self.assertFalse(loading.call_args.kwargs['trust_remote_code'])
        self.assertEqual(names, ['audio'])
        self.assertEqual(outputs, ['last_hidden_state'])
        self.assertEqual(axes['last_hidden_state'], {2: 'ssl_len'})
        with torch.no_grad():
            result = wrapper(*inputs)
        self.assertEqual(tuple(result.shape[:2]), (1, 8))
        self.assertFalse(wrapper.training)

    def test_bert_returns_third_from_last_hidden_layer_without_logits(self):
        torch.set_num_threads(2)
        model = BertForMaskedLM(BertConfig(vocab_size=100, hidden_size=8,
            num_hidden_layers=4, num_attention_heads=2, intermediate_size=16)).eval()
        args = converter.parse_args(self.argv('bert') + ['--assets', str(self.assets)])
        with patch('transformers.AutoModelForMaskedLM.from_pretrained', return_value=model) as loading:
            wrapper, inputs, names, outputs, axes = converter.shared_wrapper(
                args, torch, torch.nn, torch.nn.functional)
        self.assertTrue(loading.call_args.kwargs['local_files_only'])
        self.assertFalse(loading.call_args.kwargs['trust_remote_code'])
        with torch.no_grad():
            actual = wrapper(*inputs)
            expected = model(input_ids=inputs[0], attention_mask=inputs[1],
                token_type_ids=inputs[2], output_hidden_states=True).hidden_states[-3]
        torch.testing.assert_close(actual, expected, rtol=0, atol=0)
        self.assertEqual(names, ['input_ids', 'attention_mask', 'token_type_ids'])
        self.assertEqual(outputs, ['hidden_states'])
        self.assertEqual(set(axes), {*names, 'hidden_states'})


if __name__ == '__main__':
    unittest.main()
