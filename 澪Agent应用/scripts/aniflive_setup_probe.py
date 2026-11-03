"""Real audio acceptance in the freshly prepared managed runtime."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import threading
import time
import types
import wave


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', type=Path, required=True)
    data = json.loads(parser.parse_args().job.read_text('utf-8-sig'))
    from aniflive_v2_server import AnifV2Voice
    profile = data['profile']
    args = types.SimpleNamespace(source=Path(data['source']), engines=Path(data['engines']),
        bert=Path(data['assets']) / 'chinese-roberta-wwm-ext-large',
        gpt=Path(profile['gpt']), sovits=Path(profile['sovits']), reference=Path(profile['reference']),
        reference_text=profile['reference_text'], reference_language=profile['reference_language'],
        voice_profile=profile['id'])
    voice = AnifV2Voice(args)
    output = Path(data['job_dir'])
    result = {'schema': 1, 'job_id': data['job_id'], 'weight_sha256': data['weight_sha256'],
              'ok': False, 'audio': {}, 'runtime': voice.metadata(),
              'experimental': True, 'numerical_verified': False}
    try:
        for language, text in (('zh', '你好，这是我的声音试听。安装完成以后，你可以随时切换自己喜欢的音色。'),
                               ('ja', 'こんにちは。これは声のテストです。準備ができたら、好きな声でお話ししましょう。')):
            chunks = []; started = time.perf_counter(); first = None
            for chunk in voice.generate((text,), language, threading.Event()):
                if first is None:
                    first = time.perf_counter() - started
                chunks.append(chunk)
            pcm = b''.join(chunks)
            if len(pcm) < 3200 or len(pcm) % 2:
                raise RuntimeError('试听音频不完整。')
            import numpy as np
            values = np.frombuffer(pcm, dtype='<i2')
            if abs(values.astype('int32')).max() < 8:
                raise RuntimeError('试听结果没有有效声音。')
            path = output / f'preview-{language}.wav'
            with wave.open(str(path), 'wb') as target:
                target.setnchannels(1); target.setsampwidth(2); target.setframerate(32000)
                target.writeframes(pcm)
            result['audio'][language] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'seconds': len(pcm) / 64000, 'first_pcm_seconds': first,
                'generation_seconds': time.perf_counter() - started}
        result['ok'] = True
        (output / 'acceptance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    finally:
        voice.close()


if __name__ == '__main__':
    main()
