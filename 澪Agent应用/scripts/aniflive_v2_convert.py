"""Convert original GPT-SoVITS V2 weights for the local AnifLive adapter.

Runs one stage per process so unused training and frontend models stay unloaded.
Never modifies the input checkpoints or the vendored AnifLive source.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
from io import BytesIO
import json
import inspect
import os
from pathlib import Path
import sys
import textwrap
import types

STAGES = ('ssl', 'bert', 'vq_encoder', 'gpt_encoder', 'gpt_step',
          'spectrogram', 'sovits', 'sovits_stream')


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def definitions(path, names, scope):
    source = Path(path).read_text(encoding='utf-8')
    nodes = [n for n in ast.parse(source).body
             if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name in names]
    if {n.name for n in nodes} != set(names):
        raise ValueError('The pinned AnifLive definitions have changed.')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)
    return scope


class CheckpointParams:
    """Data-only legacy HParams replacement for weights-only deserialization."""
    pass


def mapping(value):
    if isinstance(value, dict):
        return {key: mapping(item) for key, item in value.items()}
    if isinstance(value, CheckpointParams):
        return mapping(vars(value))
    return value


def checkpoint(torch, path):
    with Path(path).open('rb') as stream:
        prefix = stream.read(2)
    if prefix not in (b'PK', b'01'):
        raise ValueError('Unsupported V2 checkpoint header.')
    source, mmap = path, True
    if prefix == b'01':
        data = bytearray(Path(path).read_bytes()); data[:2] = b'PK'
        source, mmap = BytesIO(data), False
    with torch.serialization.safe_globals([(CheckpointParams, 'utils.HParams')]):
        return mapping(torch.load(source, map_location='cpu', weights_only=True, mmap=mmap))


def load_gpt(args, torch):
    from typing import Dict
    from GPT_SoVITS.AR.models.t2s_model import Text2SemanticDecoder
    scope = definitions(args.source / 'minimal_inference/GPT_SoVITS/AR/models/t2s_lightning_module.py',
                        {'Text2SemanticLightningModule'},
                        dict(torch=torch, Dict=Dict, Text2SemanticDecoder=Text2SemanticDecoder))
    data = checkpoint(torch, args.gpt)
    model = scope['Text2SemanticLightningModule'](data['config'], 'output', is_train=False)
    model.load_state_dict(data['weight'], strict=True)
    return model.eval()


def load_sovits(args, torch):
    from GPT_SoVITS.module.models import SynthesizerTrn
    from mio_v2_stream_server import validate_v2_metadata
    data = checkpoint(torch, args.sovits)
    # Use the existing shape/version gate; GPT metadata is checked independently.
    validate_v2_metadata(data, {'weight': {'model.ar_text_embedding.word_embeddings.weight': True}})
    hps = data['config']
    hps['model'].update(version='v2', semantic_frame_rate='25hz')
    model = SynthesizerTrn(hps['data']['filter_length'] // 2 + 1,
                          hps['train']['segment_size'] // hps['data']['hop_length'],
                          n_speakers=hps['data']['n_speakers'], **hps['model'])
    # The posterior encoder is training-only and absent in exported checkpoints.
    if hasattr(model, 'enc_q'):
        del model.enc_q
    weights = {key: value for key, value in data['weight'].items() if not key.startswith('enc_q.')}
    model.load_state_dict(weights, strict=True)
    # The upstream LongTensor([refer.size(2)]) traces a constant reference
    # length. Keep the same mask math, deriving its length from the ONNX shape.
    method = inspect.unwrap(model.decode_streaming.__func__)
    content = textwrap.dedent(inspect.getsource(method))
    old = 'torch.LongTensor([refer.size(2)]).to(refer.device)'
    if content.count(old) != 1:
        raise ValueError('The V2 reference-mask implementation has changed.')
    scope = dict(method.__globals__)
    exec(compile(content.replace(old, 'torch._shape_as_tensor(refer)[2].reshape(1).to(refer.device)'),
                 inspect.getsourcefile(method), 'exec'), scope)
    model.decode_streaming = types.MethodType(scope['decode_streaming'], model)
    for module in model.modules():
        if module.__class__.__name__ == 'EuclideanCodebook':
            module.init_embed_ = types.MethodType(lambda self, data: None, module)
    from GPT_SoVITS.text import _symbol_to_id_v2
    config = v2_config(hps, _symbol_to_id_v2)
    write_json(args.output / 'config.json', config)
    return model.eval()


def v2_config(hps, symbols):
    data = {key: hps['data'][key] for key in
            ('filter_length', 'hop_length', 'win_length', 'sampling_rate')}
    if (data['sampling_rate'] != 32000 or data['hop_length'] != 640
            or data['filter_length'] != 2048 or data['win_length'] != 2048):
        raise ValueError('Only the validated V2 32kHz/25Hz STFT configuration is supported.')
    return {'schema': 1, 'model': {'version': 'v2', 'semantic_frame_rate': '25hz'},
            'data': dict(data, max_len=1000), 'symbol_to_id': symbols}


def asset_directory(args, kind):
    """Accept explicit local HF directories or the managed shared asset root."""
    supplied = getattr(args, kind, None)
    if supplied is None:
        assets = getattr(args, 'assets', None)
        if assets is None:
            raise ValueError(f'--{kind} or --assets is required for this stage.')
        supplied = assets / {'hubert': 'chinese-hubert-base',
                             'bert': 'chinese-roberta-wwm-ext-large'}[kind]
    path = Path(supplied).resolve(strict=True)
    if not path.is_dir() or not (path / 'config.json').is_file():
        raise ValueError(f'{kind}: expected a local Hugging Face model directory.')
    if not any((path / name).is_file() for name in
               ('model.safetensors', 'pytorch_model.bin', 'model.safetensors.index.json',
                'pytorch_model.bin.index.json')):
        raise ValueError(f'{kind}: local model weights are missing.')
    return path


def spectrogram_config(args, torch):
    if getattr(args, 'config', None) is not None:
        config = json.loads(args.config.read_text(encoding='utf-8-sig'))
        if config.get('model', {}).get('version') != 'v2':
            raise ValueError('Spectrogram config must belong to GPT-SoVITS V2.')
        return v2_config(config, config.get('symbol_to_id', {}))
    if args.sovits is None:
        raise ValueError('--sovits or --config is required for the spectrogram stage.')
    from mio_v2_stream_server import validate_v2_metadata
    from GPT_SoVITS.text import _symbol_to_id_v2
    data = checkpoint(torch, args.sovits)
    validate_v2_metadata(data, {'weight': {'model.ar_text_embedding.word_embeddings.weight': True}})
    return v2_config(data['config'], _symbol_to_id_v2)


def shared_wrapper(args, torch, nn, F):
    """Export the three frontend stages without loading any voice checkpoints."""
    if args.stage == 'ssl':
        from transformers import HubertModel
        model = HubertModel.from_pretrained(str(asset_directory(args, 'hubert')),
            local_files_only=True, trust_remote_code=False, torch_dtype=torch.float32,
            attn_implementation='eager').eval()

        class SSLWrapper(nn.Module):
            def __init__(self, value):
                super().__init__(); self.model = value
            def forward(self, audio):
                return self.model(audio).last_hidden_state.transpose(1, 2)

        return (SSLWrapper(model).eval(), (torch.randn(1, 32000),), ['audio'],
                ['last_hidden_state'],
                {'audio': {1: 'audio_len'}, 'last_hidden_state': {2: 'ssl_len'}})
    if args.stage == 'bert':
        from transformers import AutoModelForMaskedLM
        model = AutoModelForMaskedLM.from_pretrained(str(asset_directory(args, 'bert')),
            local_files_only=True, trust_remote_code=False, torch_dtype=torch.float32,
            attn_implementation='eager').eval()

        class BERTWrapper(nn.Module):
            def __init__(self, value):
                super().__init__(); self.model = value
            def forward(self, input_ids, attention_mask, token_type_ids):
                value = self.model(input_ids=input_ids, attention_mask=attention_mask,
                    token_type_ids=token_type_ids, output_hidden_states=True)
                return torch.cat(value.hidden_states[-3:-2], dim=-1)

        names = ['input_ids', 'attention_mask', 'token_type_ids']
        inputs = (torch.randint(0, 100, (1, 20), dtype=torch.long),
                  torch.ones(1, 20, dtype=torch.long), torch.zeros(1, 20, dtype=torch.long))
        axes = {name: {1: 'text_len'} for name in (*names, 'hidden_states')}
        return BERTWrapper(model).eval(), inputs, names, ['hidden_states'], axes
    config = spectrogram_config(args, torch)
    scope = definitions(args.source / 'minimal_inference/export_onnx.py',
                        {'SpectrogramWrapper'}, dict(torch=torch, nn=nn, F=F))
    model = scope['SpectrogramWrapper'](**{key: config['data'][key] for key in
        ('filter_length', 'hop_length', 'win_length', 'sampling_rate')}).eval()
    write_json(args.output / 'config.json', config)
    return (model, (torch.randn(1, 48000),), ['audio'], ['spectrogram'],
            {'audio': {1: 'audio_len'}, 'spectrogram': {2: 'spec_len'}})


def export(args):
    import torch
    from torch import nn
    from torch.nn import functional as F
    torch.set_num_threads(4); torch.manual_seed(1234); torch.set_grad_enabled(False)
    scope = definitions(args.source / 'minimal_inference/export_onnx.py',
                        {'GPTEncoder', 'GPTStep', 'VQEncoder', 'SoVITSStreaming',
                         'stabilize_gpt_layer_norm', 'gpt_validation_inputs'},
                        dict(torch=torch, nn=nn, F=F, os=os, json=json))
    stage = args.stage
    axes = {}; names = []; output_names = []; inputs = ()
    if stage in {'ssl', 'bert', 'spectrogram'}:
        wrapper, inputs, names, output_names, axes = shared_wrapper(args, torch, nn, F)
    elif stage.startswith('gpt_'):
        model = load_gpt(args, torch)
        encoder = scope['GPTEncoder'](model, max_len=1000, full_logits=True).eval()
        probe = scope['gpt_validation_inputs'](1234)
        if stage == 'gpt_encoder':
            wrapper = encoder; inputs = tuple(probe.values()); names = list(probe)
            output_names = ['topk_values', 'topk_indices', 'k_cache', 'v_cache', 'x_len', 'y_len', 'logits']
            axes = {'phoneme_ids': {1: 'text_len'}, 'prompts': {1: 'prompt_len'},
                    'bert_feature': {2: 'text_len'}}
        else:
            _, _, k, v, x, y = encoder(**probe)[:6]
            wrapper = scope['GPTStep'](model, full_logits=True).eval()
            inputs = (torch.tensor([[100]]), k, v, x, y, torch.tensor([0]))
            names = ['samples', 'k_cache', 'v_cache', 'x_len', 'y_len', 'idx']
            output_names = ['topk_values', 'topk_indices', 'k_cache_new', 'v_cache_new', 'logits']
    else:
        model = load_sovits(args, torch)
        if stage == 'vq_encoder':
            wrapper = scope['VQEncoder'](model).eval()
            inputs = (torch.randn(1, 768, 200),); names = ['ssl_content']; output_names = ['codes']
            axes = {'ssl_content': {2: 'ssl_len'}}
        else:
            class CompleteV2(nn.Module):
                def __init__(self, value):
                    super().__init__(); self.model = value
                def forward(self, pred_semantic, text_seq, refer_spec, noise_scale, speed):
                    return self.model.decode(pred_semantic, text_seq, [refer_spec],
                                             noise_scale=noise_scale, speed=speed, sv_emb=None)

            class StreamV2(nn.Module):
                def __init__(self, value):
                    super().__init__(); self.model = value
                def forward(self, pred_semantic, text_seq, refer_spec, noise_scale, speed,
                            result_length, overlap_frames, overlap_enabled, acoustic_noise):
                    return self.model.decode_streaming(pred_semantic, text_seq, [refer_spec],
                        noise_scale=noise_scale, speed=speed, sv_emb=None,
                        result_length=result_length[0], overlap_frames=overlap_frames,
                        padding_length=None, overlap_enabled=overlap_enabled,
                        acoustic_noise=acoustic_noise)

            inputs = (torch.randint(0, 1024, (1, 1, 50)), torch.randint(0, 732, (1, 25)),
                      torch.randn(1, 1025, 200), torch.tensor([.5]), torch.tensor([1.]))
            names = ['pred_semantic', 'text_seq', 'refer_spec', 'noise_scale', 'speed']
            axes = {'pred_semantic': {2: 'sem_len'}, 'text_seq': {1: 'text_len'},
                    'refer_spec': {2: 'ref_len'}}
            if stage == 'sovits':
                # Explicitly zero noise for reproducible complete-decoder probe.
                inputs = (*inputs[:3], torch.tensor([0.]), inputs[4])
                wrapper = CompleteV2(model).eval(); output_names = ['audio']
            elif stage == 'sovits_stream':
                wrapper = StreamV2(model).eval()
                inputs += (torch.tensor([18]), torch.zeros(1, 192, 12), torch.ones(1),
                           torch.randn(1, 192, 36))
                names += ['result_length', 'overlap_frames', 'overlap_enabled', 'acoustic_noise']
                output_names = ['audio', 'latent', 'latent_mask']
                axes['acoustic_noise'] = {2: 'result_frames'}
            else:
                raise ValueError(stage)
    destination = args.output / f'{stage}.onnx'
    if destination.exists():
        raise FileExistsError(destination)
    torch.onnx.export(wrapper, inputs, str(destination), input_names=names, output_names=output_names,
                      dynamic_axes=axes, opset_version=20, dynamo=False)
    if stage.startswith('gpt_'):
        scope['stabilize_gpt_layer_norm'](destination)
    # Deterministic native reference for later TRT checks (explicit acoustic noise).
    import numpy as np
    result = wrapper(*inputs)
    if not isinstance(result, tuple): result = (result,)
    np.savez(args.output / f'{stage}-inputs.npz', **{key: v.numpy().copy() for key, v in zip(names, inputs)})
    np.savez(args.output / f'{stage}-native.npz', **{key: v.numpy().copy() for key, v in zip(output_names, result)})
    weights = {name: sha256(getattr(args, name)) for name in ('gpt', 'sovits')
               if getattr(args, name, None) is not None}
    write_json(args.output / f'{stage}-export.json', {
        'stage': stage, 'model_family': 'gsv-v2', 'torch': torch.__version__,
        'weight_sha256': weights,
        'onnx_sha256': sha256(destination), 'uses_speaker_embedding': False,
        'validation_status': 'pending', 'experimental': True, 'numerical_verified': False,
        'native_reference_inputs': names})
    print(f'Exported original V2: {stage}', flush=True)


def build(args):
    # Pure TRT process: keep Torch and all neural frontend models unloaded.
    import tensorrt as trt
    from aniflive_tts.backend.profiles import profiles_for
    from aniflive_tts.backend.trt_builder import ShapeRange
    stage = args.stage
    logger = trt.Logger(trt.Logger.WARNING)
    builder = trt.Builder(logger); builder.max_threads = 1
    network = builder.create_network(1 << int(trt.NetworkDefinitionCreationFlag.STRONGLY_TYPED))
    parser = trt.OnnxParser(network, logger)
    path = args.output / f'{stage}.onnx'
    if not parser.parse_from_file(str(path)):
        raise RuntimeError('\n'.join(str(parser.get_error(i)) for i in range(parser.num_errors)))
    config = builder.create_builder_config(); config.builder_optimization_level = 0
    config.max_aux_streams = 0
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 256 * 1024**2)
    config.clear_flag(trt.BuilderFlag.TF32)
    profile = builder.create_optimization_profile()
    ranges = dict(profiles_for(stage)[0])
    if stage == 'sovits_stream':
        ranges['pred_semantic'] = ShapeRange((1, 1, 6), (1, 1, 120), (1, 1, 250))
        ranges['acoustic_noise'] = ShapeRange((1, 192, 12), (1, 192, 240), (1, 192, 500))
    for i in range(network.num_inputs):
        tensor = network.get_input(i)
        if tensor.is_shape_tensor:
            bounds = {'x_len': ((1,), (100,), (256,)),
                      'y_len': ((1,), (100,), (300,)),
                      'idx': ((0,), (50,), (999,)),
                      'result_length': ((6,), (120,), (250,))}
            low, optimum, high = bounds[tensor.name]
            profile.set_shape_input(tensor.name, low, optimum, high)
        elif any(d < 0 for d in tensor.shape):
            value = ranges[tensor.name]
            if profile.set_shape(tensor.name, value.min_shape, value.opt_shape, value.max_shape) is False:
                raise RuntimeError('Invalid V2 profile ' + tensor.name)
    config.add_optimization_profile(profile)
    target = args.output / f'{stage}.engine'
    if target.exists(): raise FileExistsError(target)
    serialized = builder.build_serialized_network(network, config)
    if serialized is None: raise RuntimeError('V2 TRT engine build failed')
    with target.open('wb') as stream:
        stream.write(memoryview(serialized))
    write_json(args.output / f'{stage}-build.json', {'stage': stage, 'tensorrt': trt.__version__,
        'engine_sha256': sha256(target), 'tf32': False, 'optimization_level': 0,
        'validation_status': 'pending', 'experimental': True, 'numerical_verified': False,
        'onnx_sha256': sha256(path)})
    print('Built V2 engine ' + stage, flush=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['export', 'build'])
    parser.add_argument('--stage', choices=STAGES, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--gpt', type=Path)
    parser.add_argument('--sovits', type=Path)
    parser.add_argument('--assets', type=Path, help='Shared model root containing the local HF model folders')
    parser.add_argument('--bert', type=Path, help='Explicit local BERT Hugging Face model folder')
    parser.add_argument('--hubert', type=Path, help='Explicit local HuBERT Hugging Face model folder')
    parser.add_argument('--config', type=Path, help='Existing V2 config for spectrogram-only export')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    for name in ('source', 'gpt', 'sovits', 'assets', 'bert', 'hubert', 'config'):
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, value.resolve(strict=True))
    if args.operation == 'export':
        if args.stage in {'gpt_encoder', 'gpt_step'} and args.gpt is None:
            parser.error('--gpt is required for GPT export.')
        if args.stage in {'vq_encoder', 'sovits', 'sovits_stream'} and args.sovits is None:
            parser.error('--sovits is required for V2 acoustic export.')
        if args.stage == 'spectrogram' and args.sovits is None and args.config is None:
            parser.error('--sovits or --config is required for spectrogram export.')
        if args.stage in {'ssl', 'bert'}:
            asset_directory(args, 'hubert' if args.stage == 'ssl' else 'bert')
    return args


def main():
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    sys.path[:0] = [str(args.source / 'src'), str(args.source / 'minimal_inference'),
                   str(args.source / 'minimal_inference/GPT_SoVITS')]
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
    (export if args.operation == 'export' else build)(args)


if __name__ == '__main__':
    main()
