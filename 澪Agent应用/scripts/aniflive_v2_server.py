"""Mio's original V2 checkpoints accelerated by the actual AnifLive streamer."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from http.server import ThreadingHTTPServer
import logging
import os
from pathlib import Path
import re
import sys
import threading
import time
import types

from aniflive_v2_runtime import create_engine, create_streamer, file_sha256
from aniflive_experimental_server import (LANGUAGES, POLICY, handler_for,
    release_startup_pages, speech_segments, warm_text_frontends)

MODEL_ID = 'mio-v2-aniflive'
GENERATION = dict(top_k=10, top_p=.88, temperature=.68, noise_scale=.5,
                  speed=1., pause_length=0., request_seed=6101, chunk_length=32)
FIRST_CHUNK_TOKENS = 17
FIRST_LOOKAHEAD_TOKENS = 8


class PhonemePlanner:
    """Use Anif's exact phoneme frontend without running BERT twice.

    Segment planning only consumes phone counts, never BERT features. The real
    frontend and trained BERT still run when the streamer reaches each segment.
    """
    def __init__(self, engine):
        self.precision = engine.precision
        self.get_phones_and_bert = types.MethodType(engine.get_phones_and_bert.__func__, self)
        self.model_gpt_enc = engine.model_gpt_enc
        self.sovits_max_text_len = engine.sovits_max_text_len
        self.version = engine.version

    def get_bert_inf(self, phones, word2ph, norm_text, language):
        import torch
        return torch.empty((0, len(phones)), dtype=self.precision)


class AnifV2Voice:
    def __init__(self, args):
        started = time.perf_counter()
        self.reference = args.reference.resolve(strict=True)
        self.reference_language = args.reference_language
        self.reference_text = args.reference_text
        self.profile = args.voice_profile
        os.environ.update(POLICY)
        os.environ['ANIFLIVE_TTS_REPETITION_PENALTY'] = '1.35'
        # A 25-token first context supplies the following decoder call without
        # starving the existing 80 ms player buffer on this 4060 laptop.
        os.environ['ANIFLIVE_TTS_FIRST_CHUNK_TOKENS'] = str(FIRST_CHUNK_TOKENS)
        os.environ['ANIFLIVE_TTS_FIRST_LOOKAHEAD_TOKENS'] = str(FIRST_LOOKAHEAD_TOKENS)
        os.environ['ANIFLIVE_TTS_FIRST_SEGMENT_PREVIEW_ONLY'] = '1'
        with open(os.devnull, 'w', encoding='utf-8') as quiet, redirect_stdout(quiet), redirect_stderr(quiet):
            self.engine = create_engine(engine_dir=args.engines, bert_path=args.bert,
                expected_weight_sha256={'gpt': file_sha256(args.gpt), 'sovits': file_sha256(args.sovits)},
                source_dir=args.source)
            logger = logging.getLogger('mio.aniflive.v2.private')
            logger.handlers = [logging.NullHandler()]; logger.propagate = False
            self.streamer = create_streamer(self.engine, reference_wav=self.reference,
                reference_text=self.reference_text, reference_language=self.reference_language, logger=logger)
            self.planner = PhonemePlanner(self.engine)
            self.streamer.prepare_reference()
            warm_text_frontends(self.engine)
            release_startup_pages()
            self.load_seconds = time.perf_counter() - started
            started = time.perf_counter()
            for text, language in (('你好。', 'zh'), ('こんにちは。', 'ja')):
                for _ in self.generate((text,), language, threading.Event()):
                    pass
            self.warmup_seconds = time.perf_counter() - started

    def metadata(self):
        return {**self.engine.v2_metadata, 'model': MODEL_ID,
                'voice_profile': self.profile, 'reference_name': self.profile,
                'reference_path': str(self.reference), 'reference_language': self.reference_language,
                'generation': {**GENERATION, 'first_chunk_tokens': FIRST_CHUNK_TOKENS,
                               'first_lookahead_tokens': FIRST_LOOKAHEAD_TOKENS,
                               'minimum_preview_context_tokens': FIRST_CHUNK_TOKENS + FIRST_LOOKAHEAD_TOKENS,
                               'configured_first_context_tokens': 17,
                               'preview_target_dynamic': True,
                               'first_segment_preview_only': True,
                               'semantic_sampling': self.streamer.semantic_runtime.sampling_contract},
                'load_seconds': self.load_seconds,
                'warmup_seconds': self.warmup_seconds,
                'formal_numerical_gate': 'V2 adapter pending end-to-end acceptance; no V2ProPlus engines'}

    def generate(self, segments, language, cancelled):
        if cancelled.is_set():
            return
        if (language not in LANGUAGES or not isinstance(segments, (tuple, list))
                or not 1 <= len(segments) <= 8
                or any(not isinstance(text, str) or not text.strip() for text in segments)
                or sum(len(text) for text in segments) > 600):
            raise ValueError('语言、文字长度或分段数量无效。')
        import numpy as np
        with open(os.devnull, 'w', encoding='utf-8') as quiet, redirect_stdout(quiet), redirect_stderr(quiet):
            fitted = speech_segments(segments, language, self.planner, self.streamer, cancelled)
        pieces = self.streamer.iter_audio(segments=fitted, text_language=LANGUAGES[language],
                                          cancelled=cancelled, **GENERATION)
        try:
            while not cancelled.is_set():
                with open(os.devnull, 'w', encoding='utf-8') as quiet, redirect_stdout(quiet), redirect_stderr(quiet):
                    chunk = next(pieces, None)
                if chunk is None:
                    break
                if cancelled.is_set():
                    return
                values = np.asarray(chunk, dtype=np.float32).reshape(-1)
                if not np.isfinite(values).all():
                    raise RuntimeError('Invalid V2 audio output')
                if values.size:
                    yield (np.clip(values, -1, 1) * 32767).astype('<i2').tobytes()
        finally:
            with open(os.devnull, 'w', encoding='utf-8') as quiet, redirect_stdout(quiet), redirect_stderr(quiet):
                pieces.close()

    def close(self):
        self.streamer.close()


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'engines', 'bert', 'gpt', 'sovits', 'reference'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--reference-text', required=True)
    parser.add_argument('--reference-language', choices=['zh', 'ja', 'en'], default='ja')
    parser.add_argument('--voice-profile', default='mio')
    parser.add_argument('--port', type=int, default=9882)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', args.voice_profile):
        parser.error('端口或音色标识无效。')
    if not args.reference_text.strip() or len(args.reference_text) > 1000:
        parser.error('需要参考音频的准确原文。')
    return args


def main():
    args = arguments()
    voice = AnifV2Voice(args)
    server = None
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(voice.generate,
            voice_profile=args.voice_profile, reference_language=args.reference_language,
            reference_kind='user-provided', model_id=MODEL_ID, runtime_metadata=voice.metadata()))
        server.daemon_threads = True
        server.serve_forever()
    finally:
        if server is not None:
            server.server_close()
        voice.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Anif V2 worker failed: ' + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
