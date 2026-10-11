import asyncio
from contextlib import closing
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
import wave

from openpyxl import Workbook
from PIL import Image
import pytest

from app import db, agent_file_service as files, knowledge_service as knowledge, workspace_service as workspace
from app import media_compose_service as media, artifact_service as artifacts
from test_runtime_safety_v2 import isolated


def make_task():
    workspace.initialize()
    with patch('app.model_runtime.model_selection', return_value=('test-model', 'low')):
        project = workspace.save_project('合成媒体文档')
        return project, workspace.enqueue_goal('整理合成资料', project['id'])


def write_pdf(path, count):
    objects = []
    def add(value): objects.append(value); return len(objects)
    add(b'<< /Type /Catalog /Pages 2 0 R >>')
    kids = ' '.join(f'{4+i*2} 0 R' for i in range(count))
    add(f'<< /Type /Pages /Count {count} /Kids [{kids}] >>'.encode())
    add(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    for index in range(count):
        content_id = 5+index*2
        add(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>'.encode())
        stream = f'BT /F1 12 Tf 30 200 Td (synthetic page {index+1} final evidence) Tj ET'.encode()
        add(f'<< /Length {len(stream)} >>\nstream\n'.encode()+stream+b'\nendstream')
    data = bytearray(b'%PDF-1.4\n'); positions=[0]
    for index, value in enumerate(objects, 1):
        positions.append(len(data)); data.extend(f'{index} 0 obj\n'.encode()+value+b'\nendobj\n')
    start=len(data); data.extend(f'xref\n0 {len(positions)}\n0000000000 65535 f \n'.encode())
    for position in positions[1:]: data.extend(f'{position:010d} 00000 n \n'.encode())
    data.extend(f'trailer << /Size {len(positions)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF'.encode())
    path.write_bytes(data)


def test_pdf_after_page_200_is_readable_and_xlsx_retains_sheets_rows_and_formulas(isolated, tmp_path):
    root=tmp_path/'authorized'; root.mkdir(); files.roots([str(root)])
    pdf=root/'long.pdf'; write_pdf(pdf, 205)
    result=files.read_document(str(pdf), max_chars=200000)
    assert 'synthetic page 205 final evidence' in result['text'] and not result['has_more']
    assert result['sha256'] == hashlib.sha256(pdf.read_bytes()).hexdigest()
    book=Workbook(); book.active.title='证据'; book.active.append(['名称','数值']); book.active.append(['合成',42]); book.active.append(['公式','=B2*2'])
    book.create_sheet('第二张').append(['末尾证据']); path=root/'synthetic.xlsx'; book.save(path); book.close()
    workbook=files.read_document(str(path))
    assert '[工作表：证据]' in workbook['text'] and '[行 3] 公式\t=B2*2' in workbook['text']
    assert '第二张' in workbook['text'] and '末尾证据' in workbook['text']


def test_unchanged_index_preserves_chunk_ids_and_changed_index_replaces_evidence(isolated, tmp_path):
    project, _=make_task()
    root=tmp_path/'authorized'; root.mkdir(); files.roots([str(root)])
    source=root/'synthetic.md'; source.write_text('星空项目合成资料', encoding='utf-8')
    first=knowledge.index_document(project['id'],str(source))
    with closing(db.get_conn()) as conn:
        rows=[row['id'] for row in conn.execute('SELECT id FROM knowledge_chunks')]
    with patch.object(files,'read_document',side_effect=AssertionError('unchanged must not reparse')):
        second=knowledge.index_document(project['id'],str(source))
    assert second['unchanged'] and second['id']==first['id']
    with closing(db.get_conn()) as conn:
        assert rows==[row['id'] for row in conn.execute('SELECT id FROM knowledge_chunks')]
    source.write_text('海洋项目更正资料',encoding='utf-8')
    third=knowledge.index_document(project['id'],str(source))
    assert not third['unchanged'] and third['id']==first['id'] and third['sha256']!=first['sha256']
    assert not knowledge.search(project['id'],'星空')['results']
    assert knowledge.search(project['id'],'海洋')['results']


def test_real_video_preserves_audio_and_silent_images_and_can_explicitly_mute(isolated, tmp_path):
    _, task=make_task()
    root=tmp_path/'authorized'; root.mkdir(); files.roots([str(root)])
    binary=media.binary()
    subprocess.run([binary,'-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=c=blue:s=128x128:r=24:d=0.5',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=0.5','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(root/'tone.mp4')], check=True, capture_output=True)
    Image.new('RGB',(128,128),'white').save(root/'silent.png')
    async def run():
        spec={'name':'保留音轨.mp4','clips':[{'path':str(root/'tone.mp4'),'kind':'video','duration':0.4},
              {'path':str(root/'silent.png'),'kind':'image','duration':0.3}], 'width':128, 'height':128}
        result=media.create(spec,SimpleNamespace(task_id=task['id']))
        await asyncio.gather(*list(media._running.values()))
        assembly=media.get(result['assembly']['id']); assert assembly['status']=='completed',assembly['error']
        path,_=artifacts.file(assembly['artifact_id'])
        assert await media.has_audio(path)
        decoded=root/'decoded.wav'
        subprocess.run([binary,'-hide_banner','-loglevel','error','-y','-i',str(path),'-vn','-ac','1','-ar','48000','-c:a','pcm_s16le',str(decoded)], check=True,capture_output=True)
        with wave.open(str(decoded),'rb') as audio:
            import struct
            samples=struct.unpack('<'+'h'*audio.getnframes(),audio.readframes(audio.getnframes()))
        assert len(samples)>30000 and max(abs(value) for value in samples[:12000])>1000
        assert max(abs(value) for value in samples[25000:30000])<100
        muted=media.create({**spec,'name':'静音.mp4','audio_mode':'mute'},SimpleNamespace(task_id=task['id']))
        await asyncio.gather(*list(media._running.values()))
        assembly=media.get(muted['assembly']['id']); assert assembly['status']=='completed',assembly['error']
        path,_=artifacts.file(assembly['artifact_id'])
        assert not await media.has_audio(path)
    asyncio.run(run())
