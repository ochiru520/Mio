"""OCR must work in the frozen app without an unshipped Python wrapper."""
import io
import os
import subprocess
from unittest.mock import patch

import pytest
from PIL import Image

from app import document_service


def test_durable_user_engine_works_without_path(tmp_path):
    binary = tmp_path / 'Mio/Dependencies/tesseract/tesseract.exe'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'fixture-binary')
    with patch.dict(os.environ, {'LOCALAPPDATA': str(tmp_path)}, clear=True), \
            patch('app.document_service.shutil.which', return_value=None):
        assert document_service._resolve_tesseract() == str(binary.resolve())


def test_invalid_explicit_engine_does_not_silently_use_another_install(tmp_path):
    with patch.dict(os.environ, {'MIO_TESSERACT_BINARY': str(tmp_path / 'missing.exe')}, clear=True), \
            patch('app.document_service.shutil.which', return_value='other-tesseract.exe'):
        with pytest.raises(RuntimeError, match='MIO_TESSERACT_BINARY'):
            document_service._resolve_tesseract()


def test_adjacent_language_data_ignores_unrelated_global_prefix(tmp_path):
    binary = tmp_path / 'tesseract.exe'
    binary.write_bytes(b'fixture-binary')
    (tmp_path / 'tessdata').mkdir()
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        assert command[command.index('--tessdata-dir') + 1] == str(tmp_path / 'tessdata')
        return subprocess.CompletedProcess(command, 0, b'eng\nchi_sim\n' if '--list-langs' in command else b'valid scan', b'')

    with patch.dict(os.environ, {'MIO_TESSERACT_BINARY': str(binary), 'TESSDATA_PREFIX': str(tmp_path / 'unrelated')}, clear=True), \
            patch('app.document_service.subprocess.run', side_effect=run):
        assert document_service.ocr_pdf_pages(scan_pdf(), 'scan.pdf') == [(1, 'valid scan')]
    assert len(commands) == 2


def scan_pdf():
    stream = io.BytesIO()
    Image.new('RGB', (120, 120), 'white').save(stream, 'PDF')
    return stream.getvalue()


def test_local_engine_reads_png_without_pytesseract():
    sent = []

    def run(command, **kwargs):
        if '--list-langs' in command:
            return subprocess.CompletedProcess(command, 0, b'Languages:\neng\nchi_sim\n', b'')
        with Image.open(io.BytesIO(kwargs['input'])) as image:
            assert image.width > 100 and image.height > 100
        sent.append(command)
        return subprocess.CompletedProcess(command, 0, '扫描资料 0 False\x00\n'.encode(), b'')

    with patch('app.document_service.shutil.which', return_value='tesseract.exe'), \
            patch('app.document_service.subprocess.run', side_effect=run):
        pages = document_service.ocr_pdf_pages(scan_pdf(), '中文 scan.pdf')
    assert pages == [(1, '扫描资料 0 False')]
    assert len(sent) == 1


def test_missing_chinese_data_is_not_silent_english_fallback():
    result = subprocess.CompletedProcess([], 0, b'List:\neng\n', b'')
    with patch('app.document_service.shutil.which', return_value='tesseract.exe'), \
            patch('app.document_service.subprocess.run', return_value=result) as run:
        with pytest.raises(RuntimeError, match='chi_sim'):
            document_service.ocr_pdf_pages(scan_pdf(), 'scan.pdf')
    assert run.call_count == 1


@pytest.mark.parametrize('failure', ['exit', 'timeout', 'launch'])
def test_engine_failures_become_persistable_ocr_errors(failure):
    def run(command, **kwargs):
        if '--list-langs' in command:
            return subprocess.CompletedProcess(command, 0, b'eng\nchi_sim\n', b'')
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        if failure == 'launch':
            raise OSError('process launch failed')
        return subprocess.CompletedProcess(command, 2, b'partial misleading text', b'image decode failed')

    with patch('app.document_service.shutil.which', return_value='tesseract.exe'), \
            patch('app.document_service.subprocess.run', side_effect=run):
        with pytest.raises(RuntimeError, match='OCR'):
            document_service.ocr_pdf_pages(scan_pdf(), 'scan.pdf')
