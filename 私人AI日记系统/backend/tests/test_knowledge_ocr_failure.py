"""Failed scan indexing stays visible and retries cannot reuse old evidence."""
from unittest.mock import patch

import pytest
from PIL import Image
from reportlab.pdfgen import canvas

from app import agent_file_service as files, knowledge_service as knowledge, workspace_service as workspace
from test_runtime_safety_v2 import isolated


def scan_project(tmp_path):
    folder = tmp_path / 'authorized'
    folder.mkdir()
    source = folder / 'scan.pdf'
    Image.new('RGB', (80, 80), 'white').save(source, 'PDF')
    files.roots([str(folder)])
    return workspace.save_project('扫描资料失败重试'), source


def text_pdf(source, text):
    pdf = canvas.Canvas(str(source))
    pdf.drawString(40, 700, text)
    pdf.save()


def test_missing_ocr_is_persisted_and_successful_retry_keeps_document_id(isolated, tmp_path):
    project, source = scan_project(tmp_path)
    with patch('app.document_service.ocr_pdf_pages', side_effect=RuntimeError('OCR unavailable: missing engine')):
        with pytest.raises(ValueError, match='missing engine'):
            knowledge.index_document(project['id'], str(source))
    documents = knowledge.list_documents(project['id'])
    assert len(documents) == 1
    failed = documents[0]
    assert failed['status'] == 'failed' and failed['ocr_status'] == 'failed'
    assert 'missing engine' in failed['ocr_error']
    assert workspace.collection_page(project['id'], 'documents')['documents'] == documents
    assert not knowledge.search_for_agent(project['id'], 'scan')['results']
    text_pdf(source, 'orbit recovered evidence')
    recovered = knowledge.index_document(project['id'], str(source))
    assert recovered['id'] == failed['id']
    assert recovered['ocr_status'] == 'not_needed'
    document = knowledge.list_documents(project['id'])[0]
    assert document['status'] == 'ready' and not document['error'] and not document['ocr_error']
    assert knowledge.search_for_agent(project['id'], 'orbit')['results']


def test_empty_ocr_is_visible_without_searchable_chunks(isolated, tmp_path):
    project, source = scan_project(tmp_path)
    with patch('app.document_service.ocr_pdf_pages', return_value=[]):
        with pytest.raises(ValueError, match='未识别到文字'):
            knowledge.index_document(project['id'], str(source))
    document = knowledge.list_documents(project['id'])[0]
    assert document['ocr_status'] == 'empty' and not document['ocr_used']
    assert document['status'] == 'failed'
    assert not knowledge.search_for_agent(project['id'], 'scan')['results']


def test_failed_reindex_preserves_old_hash_as_stale_and_shows_latest_failure(isolated, tmp_path):
    project, source = scan_project(tmp_path)
    text_pdf(source, 'old orbit evidence')
    initial = knowledge.index_document(project['id'], str(source))
    Image.new('RGB', (80, 80), 'white').save(source, 'PDF')
    with patch('app.document_service.ocr_pdf_pages', side_effect=RuntimeError('latest scan unavailable')):
        with pytest.raises(ValueError, match='latest scan unavailable'):
            knowledge.reindex_document(initial['id'])
    document = knowledge.list_documents(project['id'])[0]
    assert document['id'] == initial['id'] and document['sha256'] == initial['sha256']
    assert document['ocr_status'] == 'failed' and 'latest scan' in document['ocr_error']
    assert workspace.collection_page(project['id'], 'documents')['documents'] == [document]
    result = knowledge.search_for_agent(project['id'], 'orbit')
    assert result['blocked_sources'] and not result['results']
