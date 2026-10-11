"""Bounded, passive Office content previews for already-authorized artifact bytes."""
from __future__ import annotations

import base64
import io
import posixpath
import re
import zipfile
from pathlib import Path

from lxml import etree
from PIL import Image, UnidentifiedImageError

OFFICE = {'.docx', '.xlsx', '.pptx'}
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_PART_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 60000
MAX_IMAGE_BYTES = 1024 * 1024
MAX_IMAGES_BYTES = 3 * 1024 * 1024
NS = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
    'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
}


class OfficePreviewError(ValueError):
    """A verified file exists, but its Office content cannot be previewed."""


class _Package:
    def __init__(self, archive):
        self.archive = archive
        self.characters = 0
        self.image_bytes = 0
        self.image_count = 0
        self.truncated = False
        entries = archive.infolist()
        if len(entries) > 10000 or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
            raise ValueError('Office 文档解压体积过大，无法预览。')
        if len({entry.filename for entry in entries}) != len(entries):
            raise ValueError('Office 文档包含重复内容部件，无法预览。')

    def xml(self, name):
        entry = self.archive.getinfo(name)
        if entry.file_size > MAX_PART_BYTES:
            raise ValueError('Office 文档内容部件过大，无法预览。')
        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
        root = etree.fromstring(self.archive.read(entry), parser=parser)
        if root.getroottree().docinfo.doctype or any(isinstance(node, etree._Entity) for node in root.iter()):
            raise ValueError('Office 文档包含不安全 XML 声明，无法预览。')
        return root

    def text(self, value):
        value = str(value or '')
        remaining = MAX_TEXT_CHARS - self.characters
        if len(value) > remaining:
            self.truncated = True
        value = value[:max(0, remaining)]
        self.characters += len(value)
        return value

    def relationships(self, part):
        directory, filename = posixpath.split(part)
        name = posixpath.join(directory, '_rels', filename + '.rels')
        if name not in self.archive.namelist():
            return {}
        result = {}
        for relation in self.xml(name):
            if relation.get('TargetMode') == 'External':
                continue
            target = relation.get('Target', '')
            target = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(directory, target))
            if target.startswith('../') or ':' in target or '\\' in target:
                continue
            result[relation.get('Id')] = target
        return result

    def image(self, target, alt=''):
        if not target or target not in self.archive.namelist():
            return None
        entry = self.archive.getinfo(target)
        if entry.file_size > MAX_IMAGE_BYTES or self.image_bytes + entry.file_size > MAX_IMAGES_BYTES or self.image_count >= 12:
            self.truncated = True
            return None
        payload = self.archive.read(entry)
        try:
            with Image.open(io.BytesIO(payload)) as image:
                mime = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp', 'GIF': 'image/gif'}.get(image.format)
                if not mime or image.width * image.height > 16000000:
                    self.truncated = True
                    return None
                image.verify()
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            self.truncated = True
            return None
        self.image_bytes += len(payload)
        self.image_count += 1
        return {'type': 'image', 'url': f'data:{mime};base64,' + base64.b64encode(payload).decode('ascii'), 'alt': self.text(alt)}


def _word_paragraph(package, paragraph, relationships):
    text = ''.join(node.text or '' if node.tag == '{' + NS['w'] + '}t' else '\t' if node.tag == '{' + NS['w'] + '}tab' else '\n'
                   for node in paragraph.iter() if node.tag in {'{' + NS['w'] + '}t', '{' + NS['w'] + '}tab', '{' + NS['w'] + '}br'})
    blocks = []
    if text:
        style = paragraph.find('w:pPr/w:pStyle', NS)
        match = re.fullmatch(r'Heading([1-6])', style.get('{' + NS['w'] + '}val', ''), re.I) if style is not None else None
        blocks.append({'type': 'heading' if match else 'paragraph', 'text': package.text(text), **({'level': int(match[1])} if match else {})})
    for blip in paragraph.findall('.//a:blip', NS):
        block = package.image(relationships.get(blip.get('{' + NS['r'] + '}embed')), '文档图片')
        if block:
            blocks.append(block)
    return blocks


def _word_table(package, table):
    rows = []
    elements = table.findall('w:tr', NS)
    if len(elements) > 200:
        package.truncated = True
    for row in elements[:200]:
        cells = row.findall('w:tc', NS)
        if len(cells) > 60:
            package.truncated = True
        rows.append([package.text('\n'.join(''.join(p.itertext()) for p in cell.findall('w:p', NS))) for cell in cells[:60]])
        if package.characters >= MAX_TEXT_CHARS:
            package.truncated = True
            break
    return {'type': 'table', 'rows': rows}


def _docx(package):
    root = package.xml('word/document.xml')
    body = root.find('w:body', NS)
    if body is None:
        raise ValueError('DOCX 缺少正文，无法预览。')
    relationships = package.relationships('word/document.xml')
    blocks = []
    for element in body:
        if element.tag == '{' + NS['w'] + '}p':
            blocks.extend(_word_paragraph(package, element, relationships))
        elif element.tag == '{' + NS['w'] + '}tbl':
            blocks.append(_word_table(package, element))
        if package.characters >= MAX_TEXT_CHARS or len(blocks) >= 2000:
            package.truncated = True
            break
    return {'blocks': blocks}


def _column(value):
    match = re.fullmatch(r'([A-Z]+)[1-9][0-9]*', value or '')
    if not match or len(match[1]) > 3:
        return 0
    number = 0
    for letter in match[1]:
        number = number * 26 + ord(letter) - 64
    return number


def _xlsx(package):
    root = package.xml('xl/workbook.xml')
    relationships = package.relationships('xl/workbook.xml')
    shared = []
    if 'xl/sharedStrings.xml' in package.archive.namelist():
        shared = [''.join(item.itertext()) for item in package.xml('xl/sharedStrings.xml').findall('x:si', NS)]
    sheets = []
    elements = root.findall('x:sheets/x:sheet', NS)
    if len(elements) > 30:
        package.truncated = True
    for sheet in elements[:30]:
        target = relationships.get(sheet.get('{' + NS['r'] + '}id'))
        if not target:
            raise ValueError('XLSX 工作表引用无效，无法预览。')
        rows, numbers, trimmed = [], [], False
        data = package.xml(target).findall('x:sheetData/x:row', NS)
        if len(data) > 200:
            trimmed = True
        for row in data[:200]:
            cells = {}
            for cell in row.findall('x:c', NS):
                column = _column(cell.get('r'))
                if column > 60:
                    trimmed = True
                    continue
                if not column:
                    continue
                formula = cell.find('x:f', NS)
                value = cell.findtext('x:v', '', NS)
                kind = cell.get('t')
                if formula is not None:
                    value = '=' + (formula.text or '[共享公式]')
                elif kind == 's':
                    if not re.fullmatch(r'[0-9]{1,8}', value):
                        raise ValueError('XLSX 共享字符串引用无效，无法预览。')
                    index = int(value)
                    if index < 0 or index >= len(shared):
                        raise ValueError('XLSX 共享字符串引用无效，无法预览。')
                    value = shared[index]
                elif kind == 'inlineStr':
                    value = ''.join(node.text or '' for node in cell.findall('.//x:t', NS))
                elif kind == 'b':
                    value = 'TRUE' if value == '1' else 'FALSE'
                cells[column] = package.text(value)
            rows.append([cells.get(column, '') for column in range(1, max(cells, default=0) + 1)])
            row_number = str(row.get('r', len(rows)))
            if not re.fullmatch(r'[1-9][0-9]{0,7}', row_number):
                raise ValueError('XLSX 行号无效，无法预览。')
            numbers.append(int(row_number))
            if package.characters >= MAX_TEXT_CHARS:
                trimmed = True
                break
        sheets.append({'name': package.text(sheet.get('name', '工作表')), 'rows': rows, 'row_numbers': numbers, 'truncated': trimmed})
        package.truncated |= trimmed
        if package.characters >= MAX_TEXT_CHARS:
            package.truncated = True
            break
    return {'sheets': sheets}


def _pptx(package):
    root = package.xml('ppt/presentation.xml')
    relationships = package.relationships('ppt/presentation.xml')
    slides = []
    elements = root.findall('p:sldIdLst/p:sldId', NS)
    if len(elements) > 100:
        package.truncated = True
    for number, element in enumerate(elements[:100], 1):
        target = relationships.get(element.get('{' + NS['r'] + '}id'))
        if not target:
            raise ValueError('PPTX 幻灯片引用无效，无法预览。')
        slide = package.xml(target)
        slide_relationships = package.relationships(target)
        blocks = []
        # Preserve shape order. Text is read passively; media and external links are never executed.
        shapes = slide.find('p:cSld/p:spTree', NS)
        if shapes is None:
            raise ValueError('PPTX 缺少幻灯片内容，无法预览。')
        for shape in shapes:
            tables = shape.findall('.//a:tbl', NS)
            for table in tables:
                table_rows = table.findall('a:tr', NS)
                if len(table_rows) > 200:
                    package.truncated = True
                rows = []
                for row in table_rows[:200]:
                    cells = row.findall('a:tc', NS)
                    if len(cells) > 60:
                        package.truncated = True
                    rows.append([package.text('\n'.join(''.join(n.text or '' for n in p.findall('.//a:t', NS)) for p in cell.findall('.//a:p', NS))) for cell in cells[:60]])
                blocks.append({'type': 'table', 'rows': rows})
            if not tables:
                for paragraph in shape.findall('.//a:p', NS):
                    text = ''.join(node.text or '' for node in paragraph.findall('.//a:t', NS))
                    if text:
                        blocks.append({'type': 'paragraph', 'text': package.text(text)})
            for blip in shape.findall('.//a:blip', NS):
                image = package.image(slide_relationships.get(blip.get('{' + NS['r'] + '}embed')), '幻灯片图片')
                if image:
                    blocks.append(image)
            if package.characters >= MAX_TEXT_CHARS or len(blocks) >= 2000:
                package.truncated = True
                break
        slides.append({'number': number, 'blocks': blocks})
        if package.characters >= MAX_TEXT_CHARS:
            package.truncated = True
            break
    return {'slides': slides}


def office_preview(path: Path, name: str) -> dict:
    """The caller must authorize and verify the stored artifact before parsing."""
    suffix = Path(name).suffix.lower()
    if suffix not in OFFICE:
        raise OfficePreviewError('不支持该 Office 预览格式。')
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise OfficePreviewError('Office 文档超过 32MB，无法预览，请下载后打开。')
    try:
        with zipfile.ZipFile(path) as archive:
            package = _Package(archive)
            content = {'.docx': _docx, '.xlsx': _xlsx, '.pptx': _pptx}[suffix](package)
    except ValueError as exc:
        raise OfficePreviewError(str(exc)) from exc
    except (zipfile.BadZipFile, KeyError, etree.XMLSyntaxError, TypeError, IndexError, OverflowError, RuntimeError) as exc:
        raise OfficePreviewError('Office 文档损坏或结构不受支持，无法预览，请下载后检查。') from exc
    notice = '内容预览：显示正文、表格和部分内嵌图片，原始版式、图表及批注请下载查看。'
    if suffix == '.xlsx':
        notice = '内容预览：最多 30 张工作表，每表 200 行、60 列；公式仅显示表达式，不执行重算，图表和原始格式请下载查看。'
    elif suffix == '.pptx':
        notice = '内容预览：最多 100 张幻灯片，显示文字、表格和部分图片；动画、图表及原始版式请下载查看。'
    if package.truncated:
        notice += ' 内容较多，预览已截断，请下载查看完整文件。'
    return {'kind': 'document', 'format': suffix[1:], 'name': name, 'supported': True,
            'truncated': package.truncated, 'notice': notice, **content}
