"""Managed, cancellable Anif V2 installation. No application settings are changed.

Only pinned public prerequisites are downloaded. User checkpoints are copied,
never altered. All eight engines are built on the destination GPU; a successful
build alone never produces a ready installation: real Chinese/Japanese synthesis
must finish first. GPU numerical qualification remains experimental.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.parse import unquote
from urllib.request import Request, urlopen
import zipfile

STAGES = ('ssl', 'bert', 'vq_encoder', 'gpt_encoder', 'gpt_step',
          'spectrogram', 'sovits', 'sovits_stream')
SCRIPTS = ('aniflive_v2_convert.py', 'aniflive_v2_runtime.py', 'aniflive_v2_server.py',
           'aniflive_experimental_server.py', 'mio_v2_stream_server.py',
           'aniflive_setup_probe.py')
MIN_DISK = 8 * 1024**3
MIN_RAM = 512 * 1024**2
MIN_VRAM = 384 * 1024**2


class Cancelled(Exception):
    pass


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(4 * 1024**2), b''):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    os.replace(temporary, path)


def contained(root, path):
    path = Path(path).resolve()
    if path == root or root not in path.parents:
        raise ValueError('安装文件位置超出 Mio 管理的目录。')
    return path


def unpack(archive, target, *, strip_first=False):
    """Reject traversal, Windows drive paths, symlinks and archive bombs."""
    target = Path(target).resolve()
    seen = set()
    with zipfile.ZipFile(archive) as bundle:
        if sum(x.file_size for x in bundle.infolist()) > 600 * 1024**2:
            raise ValueError('安装压缩包展开后过大。')
        for item in bundle.infolist():
            raw = item.filename.replace('\\', '/')
            rel = PurePosixPath(raw)
            if rel.is_absolute() or '..' in rel.parts or any(':' in p for p in rel.parts):
                raise ValueError('安装包包含无效的文件位置。')
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('安装包不允许包含链接。')
            parts = rel.parts[1:] if strip_first else rel.parts
            if not parts:
                continue
            key = '/'.join(parts).casefold()
            if key in seen:
                raise ValueError('安装包包含重复文件。')
            seen.add(key)
            destination = contained(target, target.joinpath(*parts))
            if item.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(item) as incoming, destination.open('wb') as output:
                    shutil.copyfileobj(incoming, output)


def tree_digest(root):
    root = Path(root)
    h = hashlib.sha256()
    for path in sorted((p for p in root.rglob('*') if p.is_file()),
                       key=lambda p: p.relative_to(root).as_posix()):
        h.update(path.relative_to(root).as_posix().encode('utf-8'))
        h.update(b'\0' + digest(path).encode('ascii') + b'\n')
    return h.hexdigest()


def stop_process(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        process.kill()
    process.wait(timeout=20)


class Installer:
    def __init__(self, job_path):
        self.job = json.loads(Path(job_path).read_text('utf-8-sig'))
        self.root = Path(self.job['root']).resolve(strict=True)
        self.job_dir = contained(self.root, self.job['job_dir'])
        self.state_path = contained(self.job_dir, self.job['state_path'])
        self.cancel_path = contained(self.job_dir, self.job['cancel_path'])
        self.scripts = Path(self.job['scripts_dir']).resolve(strict=True)
        self.lock = json.loads((self.scripts / 'deps/aniflive-sources.json').read_text('utf-8-sig'))
        self.state = {'schema': 1, 'job_id': self.job['job_id'], 'ready': False, 'percent': 1}
        self.last_guard = 0
        self.environment = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
                                TOKENIZERS_PARALLELISM='false', PIP_DISABLE_PIP_VERSION_CHECK='1')
        for inherited in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'):
            self.environment.pop(inherited, None)
        # Keep all installer caches under the same managed root.
        self.environment.update(PIP_CACHE_DIR=str(self.root / 'pip-cache'),
                                HF_HOME=str(self.root / 'hf-cache'))
        temporary = self.root / 'temp'
        temporary.mkdir(exist_ok=True)
        self.environment.update(TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary))
        self.flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

    def status(self, stage, message, percent=None, **fields):
        if stage != self.state.get('stage'):
            self.state.update(downloaded_bytes=0, total_bytes=0)
        self.state.update(stage=stage, message=message, updated_at=time.time(), **fields)
        if percent is not None:
            self.state['percent'] = percent
        atomic_json(self.state_path, self.state)

    def guard(self, gpu=False):
        if self.cancel_path.exists():
            raise Cancelled()
        if time.monotonic() - self.last_guard < 1:
            return
        self.last_guard = time.monotonic()
        if shutil.disk_usage(self.root).free < MIN_DISK:
            raise RuntimeError('磁盘可用空间不足，已停止；请保留至少 8 GB 空间后重试。')
        if os.name == 'nt':
            class Memory(ctypes.Structure):
                _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
                    (name, ctypes.c_ulonglong) for name in
                    ('total', 'free', 'total_page', 'free_page', 'total_virtual', 'free_virtual', 'free_ext')]
            memory = Memory(); memory.length = ctypes.sizeof(memory)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
                raise RuntimeError('无法检查内存，已停止本次安装。')
            if memory.free < MIN_RAM:
                raise RuntimeError('可用内存过低，已停止；请关闭占用较大的程序后重试。')
        if gpu:
            result = subprocess.run(['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits', '-i', '0'],
                                    capture_output=True, text=True, timeout=8, creationflags=self.flags)
            if result.returncode or int(result.stdout.strip()) * 1024**2 < MIN_VRAM:
                raise RuntimeError('显存余量不足，已停止编译；请先停止其他本地模型。')

    def run(self, command, name, *, env=None, cwd=None, gpu=False):
        self.last_guard = 0
        self.guard(gpu=gpu)
        with (self.job_dir / (name + '.log')).open('ab') as log:
            process = subprocess.Popen([str(x) for x in command], stdout=log, stderr=log,
                                       env=env or self.environment, cwd=cwd,
                                       creationflags=self.flags)
            try:
                while process.poll() is None:
                    self.guard(gpu=gpu)
                    time.sleep(.5)
                if process.returncode:
                    raise RuntimeError(f'步骤「{name}」没有完成，可重试；详细记录保存在本机安装目录。')
            finally:
                stop_process(process)

    def download(self, record, target):
        target = contained(self.root, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        expected = record['sha256']
        size = record.get('bytes')
        if target.is_file() and digest(target) == expected and (size is None or target.stat().st_size == size):
            return target
        partial = target.with_name(target.name + '.part')
        url = record['url']
        if urlparse(url).scheme != 'https':
            raise ValueError('安装清单仅允许 HTTPS 下载。')
        for attempt in range(3):
            self.guard()
            offset = partial.stat().st_size if partial.is_file() else 0
            if size is not None and offset >= size:
                if offset == size and digest(partial) == expected:
                    os.replace(partial, target)
                    return target
                partial.unlink(); offset = 0
            try:
                request = Request(url, headers={'User-Agent': 'Mio-Anif-Installer/1',
                                               **({'Range': f'bytes={offset}-'} if offset else {})})
                with urlopen(request, timeout=25) as response:
                    resumed = offset and response.status == 206
                    if resumed and not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
                        raise RuntimeError('下载服务器返回的续传位置不符。')
                    if not resumed:
                        offset = 0
                    with partial.open('ab' if resumed else 'wb') as output:
                        last_report = 0
                        while True:
                            self.guard()
                            chunk = response.read(1024**2)
                            if not chunk:
                                break
                            output.write(chunk); offset += len(chunk)
                            if size is not None and offset > size:
                                raise ValueError('下载文件大小超过固定清单。')
                            if time.monotonic() - last_report > 1:
                                self.status(self.state['stage'], self.state['message'],
                                            downloaded_bytes=offset, total_bytes=size or 0)
                                last_report = time.monotonic()
                if (size is not None and offset != size) or digest(partial) != expected:
                    partial.unlink(missing_ok=True)
                    raise OSError('下载文件校验不符，需要重新下载。')
                os.replace(partial, target)
                return target
            except (OSError, HTTPError):
                if attempt == 2:
                    raise RuntimeError('下载未完成，请检查网络后重试；已下载部分会保留。') from None

    def prepare_source(self):
        record = self.lock['source']
        archive = self.download(record, self.root / 'downloads/anif-source.zip')
        source = self.root / 'source' / record['revision']
        marker = source / '.mio-source.json'
        if not marker.is_file():
            self.guard()
            unpack(archive, source, strip_first=True)
            # Record immutable shipped files, not Python's generated caches.
            files = {p.relative_to(source).as_posix(): digest(p) for p in source.rglob('*') if p.is_file()}
            atomic_json(marker, {'archive_sha256': record['sha256'], 'files': files})
        metadata = json.loads(marker.read_text('utf-8'))
        if metadata['archive_sha256'] != record['sha256']:
            raise ValueError('Anif 源码版本不符，请卸载受管组件后重新安装。')
        for name, sha in metadata['files'].items():
            self.guard()
            if digest(contained(source.resolve(), source / name)) != sha:
                raise ValueError('Anif 源码校验失败，请重新安装受管组件。')
        return source

    def prepare_assets(self):
        assets = self.root / 'assets'
        for index, record in enumerate(self.lock['assets']):
            self.status('downloading', f'正在准备多语言资源（{index + 1}/{len(self.lock["assets"])}）',
                        10 + int(index * 15 / len(self.lock['assets'])))
            target = self.download(record, assets / record['path'])
            if record.get('extract_to'):
                destination = assets / record['extract_to']
                tree = destination / target.stem
                if not tree.is_dir() or tree_digest(tree) != record['tree_sha256']:
                    # Only overwrite this known, managed resource subtree.
                    if tree.exists():
                        shutil.rmtree(contained(self.root, tree))
                    unpack(target, destination)
                if tree_digest(tree) != record['tree_sha256']:
                    raise ValueError('语言资源展开后校验失败。')
        return assets

    def prepare_environment(self):
        python = self.root / 'environment/Scripts/python.exe'
        if not python.is_file():
            self.run([sys.executable, '-m', 'venv', self.root / 'environment'], 'create-environment')
        requirements = self.scripts / 'deps/aniflive-runtime-requirements.txt'
        signature = hashlib.sha256((digest(requirements) + json.dumps(self.lock, sort_keys=True)).encode()).hexdigest()
        marker = python.parents[1] / '.mio-environment.json'
        installed = json.loads(marker.read_text('utf-8')) if marker.is_file() else {}
        if installed.get('signature') != signature:
            self.status('installing', '正在安装 GPU 语音运行库，首次下载较大，可稍后返回。', 27)
            # The Python 3.10 installer bundles an old resolver that rejects
            # normalized dependency names on the PyTorch wheel index.
            self.run([python, '-m', 'pip', 'install', 'pip==25.3', 'setuptools==80.9.0',
                      'wheel==0.45.1', '--index-url', 'https://pypi.org/simple'], 'prepare-pip')
            torch_wheels = []
            for record in self.lock['torch_wheels']:
                self.status('downloading', '正在下载 GPU 运行库，已下载部分可在重试时继续。', 28)
                torch_wheels.append(self.download(record, self.root / 'downloads' / unquote(Path(urlparse(record['url']).path).name)))
            self.status('installing', '正在安装 GPU 运行库。', 29)
            self.run([python, '-m', 'pip', 'install', *torch_wheels,
                      '--index-url', 'https://pypi.org/simple', '--timeout', '30', '--retries', '2'], 'install-torch')
            wheels = []
            for record in self.lock['tensorrt_wheels']:
                self.status('downloading', '正在下载 TensorRT 加速库，重试可继续下载。', 30)
                wheels.append(self.download(record, self.root / 'downloads' / Path(urlparse(record['url']).path).name))
            for record in self.lock['bundled_wheels']:
                path = self.scripts / 'deps' / record['path']
                if not path.is_file() or path.stat().st_size != record['bytes'] or digest(path) != record['sha256']:
                    raise RuntimeError('程序自带的分词组件校验失败，请更新完整的 Mio 安装包。')
                wheels.append(path)
            self.status('installing', '正在安装语音加速与多语言组件。', 32)
            self.run([python, '-m', 'pip', 'install', *wheels, 'tensorrt-cu12==11.2.1.2',
                      '-r', requirements, '--timeout', '30', '--retries', '2'], 'install-runtime')
            atomic_json(marker, {'signature': signature, 'python': '3.10', 'tensorrt': '11.2.1.2'})
        self.run([python, '-c',
                  'import torch,tensorrt,transformers,numpy,pyopenjtalk,onnxruntime; '
                  'assert torch.cuda.is_available(); assert torch.__version__=="2.7.1+cu128"; '
                  'assert tensorrt.__version__=="11.2.1.2"; assert transformers.__version__=="4.50.0"; '
                  'assert numpy.__version__=="1.26.4"; assert onnxruntime.__version__=="1.23.2"'], 'check-environment', gpu=True)
        # Multi-gigabyte wheel archives are useful while downloading, but are
        # unnecessary once this independent environment has passed its check.
        # Keep partial files on cancellation; remove only known successful
        # downloads, never installed files or imported voice originals.
        for record in self.lock['torch_wheels'] + self.lock['tensorrt_wheels']:
            archive = contained(self.root, self.root / 'downloads' / unquote(Path(urlparse(record['url']).path).name))
            if archive.is_file() and digest(archive) == record['sha256']:
                archive.unlink()
        return python

    def fingerprint(self, python):
        path = self.job_dir / 'hardware.json'
        code = ('import torch,tensorrt,json,subprocess,pathlib; '
                'p=torch.cuda.get_device_properties(0); '
                'd={"gpu":p.name,"capability":[p.major,p.minor],"vram":p.total_memory,'
                '"torch":torch.__version__,"cuda":torch.version.cuda,"tensorrt":tensorrt.__version__}; '
                'd["driver"]=subprocess.check_output(["nvidia-smi","--query-gpu=driver_version",'
                '"--format=csv,noheader","-i","0"],text=True).strip(); '
                'pathlib.Path(__import__("sys").argv[1]).write_text(json.dumps(d),encoding="utf-8")')
        self.run([python, '-c', code, path], 'identify-gpu', gpu=True)
        return json.loads(path.read_text('utf-8'))

    def install(self):
        self.guard(gpu=True)
        profile = self.job['profile']
        expected = self.job['weight_sha256']
        for kind in ('gpt', 'sovits'):
            if digest(profile[kind]) != expected[kind]:
                raise ValueError('所选音色权重已改变，请重新选择后安装。')
        if digest(profile['reference']) != self.job['reference_sha256']:
            raise ValueError('所选参考音频已改变，请重新选择后安装。')
        self.status('downloading', '正在下载并校验固定版本的 Anif 程序。', 5)
        source = self.prepare_source()
        assets = self.prepare_assets()
        python = self.prepare_environment()
        hardware = self.fingerprint(python)
        scripts_id = hashlib.sha256(''.join(digest(self.scripts / name) for name in SCRIPTS).encode()).hexdigest()[:20]
        runtime_scripts = self.root / 'runtime-scripts' / scripts_id
        runtime_scripts.mkdir(parents=True, exist_ok=True)
        for name in SCRIPTS:
            destination = runtime_scripts / name
            if not destination.is_file() or digest(destination) != digest(self.scripts / name):
                shutil.copyfile(self.scripts / name, destination)
        identity = {'weights': expected, 'reference_sha256': self.job['reference_sha256'],
                    'reference_text': profile['reference_text'], 'language': profile['reference_language'],
                    'profile': profile['id'], 'hardware': hardware, 'scripts': scripts_id,
                    'source': self.lock['source']['sha256'],
                    'asset_lock': hashlib.sha256(json.dumps(self.lock, sort_keys=True).encode()).hexdigest()}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]
        engines = self.root / 'bundles' / key
        weights = engines / 'weights'; weights.mkdir(parents=True, exist_ok=True)
        for kind, name in (('gpt', 'gpt.ckpt'), ('sovits', 'sovits.pth'), ('reference', 'reference.wav')):
            destination = weights / name
            if not destination.exists():
                shutil.copyfile(profile[kind], destination)
            if digest(destination) != (expected[kind] if kind != 'reference' else self.job['reference_sha256']):
                raise ValueError('音色副本校验失败，原文件仍保留。')
        converter = runtime_scripts / 'aniflive_v2_convert.py'
        env = dict(self.environment, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                   NLTK_DATA=str(assets / 'nltk_data'),
                   ANIFLIVE_TTS_FAST_LANGDETECT_CACHE=str(assets / 'fast_langdetect'),
                   ANIFLIVE_TTS_FAST_LANGDETECT_MODEL=str(assets / 'fast_langdetect/lid.176.bin'))
        prior_manifest = engines / 'v2-manifest.json'
        config_path = engines / 'config.json'
        config_valid = config_path.is_file()
        if prior_manifest.is_file():
            try:
                config_valid = config_valid and json.loads(prior_manifest.read_text('utf-8')).get('config_sha256') == digest(config_path)
            except (OSError, ValueError):
                config_valid = False
        for index, stage in enumerate(STAGES):
            self.status('compiling', f'正在为这台显卡编译（{index + 1}/8），请保持 Mio 运行。', 35 + index * 6)
            metadata = engines / f'{stage}-build.json'
            engine = engines / f'{stage}.engine'
            try:
                cached = json.loads(metadata.read_text('utf-8')) if metadata.is_file() else {}
                if not isinstance(cached, dict):
                    cached = {}
            except (OSError, ValueError):
                # The converter may have been cancelled while writing this
                # small receipt. A partial receipt is never a usable cache.
                cached = {}
            if (engine.is_file() and cached.get('engine_sha256') == digest(engine)
                    and (stage != 'vq_encoder' or config_valid)):
                continue
            engine.unlink(missing_ok=True)
            # A cancelled export/build can leave a graph. Export refuses to
            # overwrite graphs, so clear only this owned bundle's known files.
            for suffix in ('.onnx', '-inputs.npz', '-native.npz', '-export.json'):
                (engines / (stage + suffix)).unlink(missing_ok=True)
            args = ['--stage', stage, '--source', source, '--assets', assets,
                    '--gpt', weights / 'gpt.ckpt', '--sovits', weights / 'sovits.pth', '--output', engines]
            self.run([python, converter, 'export', *args], f'export-{stage}', env=env, cwd=source / 'minimal_inference')
            self.run([python, converter, 'build', *args], f'build-{stage}', env=env,
                     cwd=source / 'minimal_inference', gpu=True)
            # Conversion graphs/fixtures are reproducible; free them on completion.
            for suffix in ('.onnx', '-inputs.npz', '-native.npz'):
                (engines / (stage + suffix)).unlink(missing_ok=True)
        manifest = {'kind': 'mio-aniflive-v2-experimental', 'model_family': 'GPT-SoVITS V2',
                    'source_weights': {k: {'path': f'weights/{k}.{"ckpt" if k == "gpt" else "pth"}',
                                          'sha256': expected[k]} for k in ('gpt', 'sovits')},
                    'stages': {s: {'path': s + '.engine', 'sha256': digest(engines / (s + '.engine'))}
                               for s in STAGES}, 'config_sha256': digest(engines / 'config.json'),
                    'uses_speaker_embedding': False, 'experimental': True, 'numerical_verified': False,
                    'target_hardware': hardware, 'source_revision': self.lock['source']['revision']}
        atomic_json(engines / 'v2-manifest.json', manifest)
        self.status('testing', '编译已完成，正在实际生成中文和日语试听。', 90)
        probe_job = dict(self.job, source=str(source), assets=str(assets), engines=str(engines),
                         profile=dict(profile, gpt=str(weights / 'gpt.ckpt'), sovits=str(weights / 'sovits.pth'),
                                      reference=str(weights / 'reference.wav')))
        probe_path = self.job_dir / 'probe.json'; atomic_json(probe_path, probe_job)
        self.run([python, runtime_scripts / 'aniflive_setup_probe.py', '--job', probe_path],
                 'synthesis-check', env=env, cwd=source / 'minimal_inference', gpu=True)
        acceptance = json.loads((self.job_dir / 'acceptance.json').read_text('utf-8'))
        if acceptance.get('ok') is not True or set(acceptance.get('audio', {})) != {'zh', 'ja'}:
            raise RuntimeError('中日语音检查未完成，保留当前声音设置。')
        for language, record in acceptance['audio'].items():
            if digest(self.job_dir / f'preview-{language}.wav') != record['sha256']:
                raise RuntimeError('试听文件校验失败，不能启用本次安装。')
        registration = {'python': str(python), 'script': str(runtime_scripts / 'aniflive_v2_server.py'),
                        'cwd': str(source / 'minimal_inference'), 'url': 'http://127.0.0.1:9882',
                        'args': ['--source', str(source), '--engines', str(engines), '--bert',
                                 str(assets / 'chinese-roberta-wwm-ext-large'), '--gpt', str(weights / 'gpt.ckpt'),
                                 '--sovits', str(weights / 'sovits.pth'), '--reference', str(weights / 'reference.wav'),
                                 '--reference-text', profile['reference_text'], '--reference-language',
                                 profile['reference_language'], '--voice-profile', profile['id'], '--port', '9882'],
                        'env': {k: env[k] for k in ('NLTK_DATA', 'ANIFLIVE_TTS_FAST_LANGDETECT_CACHE',
                                'ANIFLIVE_TTS_FAST_LANGDETECT_MODEL', 'HF_HUB_OFFLINE',
                                'TRANSFORMERS_OFFLINE', 'PYTHONUTF8', 'PYTHONIOENCODING')},
                        'managed_install_id': self.job['job_id'], 'model_version': 'v2',
                        'experimental': True, 'numerical_verified': False, 'startup_timeout': 240}
        self.guard()
        self.status('ready', '中日语音生成检查通过，请试听；满意后点击使用。', 100,
                    ready=True, registration=registration, acceptance=acceptance,
                    target_hardware=hardware, experimental=True, numerical_verified=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', type=Path, required=True)
    args = parser.parse_args()
    installer = Installer(args.job)
    try:
        installer.install()
    except Cancelled:
        installer.status('cancelled', '已取消，保留可复用下载；当前声音设置不受影响。', ready=False)
        return 2
    except Exception as error:
        installer.status('failed', str(error) or '安装未完成，请重试。', ready=False)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
