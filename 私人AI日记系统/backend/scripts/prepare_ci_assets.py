"""Provision verified media tools and a CJK font for disposable Windows CI runners.

These assets are test prerequisites, not files added to the application bundle.
LXGW WenKai is distributed upstream under SIL OFL 1.1.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request
from zipfile import ZipFile


ASSETS = (
    ('ffmpeg.zip', 'https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip',
     '60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba'),
    ('LXGWWenKai-Regular.ttf', 'https://github.com/lxgw/LxgwWenKai/releases/download/v1.520/LXGWWenKai-Regular.ttf',
     '8d6ba638ac9553413354cfaab97637c1cd778444e259441ea1e5f8fb2c697fba'),
)


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def download(root, name, url, expected):
    target = root / name
    if target.is_file() and file_hash(target) == expected:
        return target
    partial = target.with_suffix(target.suffix + '.part')
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'Mio-CI'})
        with urllib.request.urlopen(request, timeout=90) as response, partial.open('wb') as output:
            shutil.copyfileobj(response, output, 1024 * 1024)
        if file_hash(partial) != expected:
            raise ValueError(f'CI asset SHA-256 mismatch: {name}')
        partial.replace(target)
        return target
    finally:
        partial.unlink(missing_ok=True)


def prepare(root):
    root.mkdir(parents=True, exist_ok=True)
    archive, font = [download(root, *asset) for asset in ASSETS]
    with ZipFile(archive) as package:
        for name in ('ffmpeg.exe', 'ffprobe.exe'):
            matches = [item for item in package.infolist() if Path(item.filename).name == name and not item.is_dir()]
            if len(matches) != 1:
                raise ValueError(f'CI media archive must contain exactly one {name}')
            # Fixed destination names; never extract archive-controlled paths.
            with package.open(matches[0]) as source, (root / name).open('wb') as output:
                shutil.copyfileobj(source, output)
            subprocess.run([str(root / name), '-version'], check=True, stdout=subprocess.DEVNULL,
                           creationflags=0x08000000 if os.name == 'nt' else 0)
    return {'MIO_FFMPEG_BINARY': str((root / 'ffmpeg.exe').resolve()),
            'MIO_FFPROBE_PATH': str((root / 'ffprobe.exe').resolve()),
            'MIO_PDF_FONT_PATH': str(font.resolve())}


if __name__ == '__main__':
    root = Path(os.environ['RUNNER_TEMP']) / 'mio-ci-assets'
    values = prepare(root)
    with Path(os.environ['GITHUB_ENV']).open('a', encoding='utf-8') as output:
        for key, value in values.items():
            output.write(f'{key}={value}\n')
    print('Verified FFmpeg, FFprobe and CJK font ready for CI tests.')
