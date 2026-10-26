"""User-authorized document reads and versioned outputs for Agent tasks."""

from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import os
import re
import zipfile
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

from docx import Document
import pypdfium2

from .repositories import agent_file_repository as repository

from .config import settings

TEXT = {".txt", ".md", ".json", ".csv", ".tsv", ".yaml", ".yml"}
TEXT |= {'.py', '.js', '.ts', '.jsx', '.tsx', '.vue', '.css', '.html', '.xml', '.toml', '.ini', '.log'}
READABLE = TEXT | {".docx", ".xlsx", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".mov", ".wav", ".mp3", ".m4a", ".ogg", ".flac"}


def workspace() -> Path:
    root = settings.data_dir.resolve() / "Agent文件"
    root.mkdir(parents=True, exist_ok=True)
    if root.resolve() != root:
        raise ValueError("Agent 成果目录不能是指向其他位置的联接或符号链接。")
    return root.resolve()


def _private_path(path: Path, *, grant: bool = False) -> bool:
    data = settings.data_dir.resolve()
    if path == data or path.is_relative_to(data) or (grant and data.is_relative_to(path)):
        return True
    # Some installations keep their settings outside the main data directory.
    sensitive = [settings.db_path, settings.runtime_config_path, settings.model_profiles_path,
                 settings.companion_config_path, settings.mio_profile_path]
    return any(path == item.resolve() or (grant and item.resolve().is_relative_to(path)) for item in sensitive)


def roots(changes: list[str] | None = None) -> list[str]:
    from .agent_task_service import initialize
    initialize()
    with repository.unit_of_work() as conn:
        conn.ensure_roots_table()
        if changes is not None:
            resolved = []
            for item in changes:
                path = Path(item).resolve(strict=True)
                if not path.is_dir() or path.parent == path or _private_path(path, grant=True):
                    raise ValueError("请选择具体资料目录，不能授权整个磁盘或 Mio 私人数据根目录。")
                resolved.append(str(path))
            conn.clear_roots()
            conn.insert_roots([(p,) for p in resolved])
        values = [row[0] for row in conn.list_roots()]
    values = [value for value in values if not _private_path(Path(value).resolve(), grant=True)]
    return list(dict.fromkeys([str(workspace()), *values]))


def resolve_read(value: str, *, task_id: str = "") -> Path:
    path = Path(value)
    path = (path if path.is_absolute() else workspace() / path).resolve(strict=True)
    if task_id:
        from .mobile_file_service import attachment_authorized
        if attachment_authorized(path, task_id):
            return path
    if _private_path(path) and not path.is_relative_to(workspace()):
        raise ValueError("私人数据不能通过文件工具读取，请使用专用的记忆或日记工具。")
    from .execution_policy_service import document_roots, protected, configuration
    if not path.is_relative_to(workspace()) and protected(path,broad=configuration()['read_scope']=='computer'):
        raise ValueError('系统目录、凭据或私人配置不能通过资料工具读取。')
    if not any(path.is_relative_to(Path(root)) for root in document_roots(roots())):
        raise ValueError("该路径尚未授权，请在 Agent 设置的文件权限中添加资料目录。")
    if any(part.startswith('.') for part in path.parts) or ':' in path.name:
        raise ValueError("不允许读取隐藏配置或命名数据流。")
    if path.is_file() and path.suffix.lower() not in READABLE:
        raise ValueError("不支持该文件类型。")
    return path


def list_files(path: str = "", limit: int = 100, *, offset: int = 0, query: str = "",
               directories_only: bool = False) -> dict:
    if not path:
        from .execution_policy_service import document_roots
        return {"roots": document_roots(roots()), "output_root": str(workspace())}
    directory = resolve_read(path)
    if not directory.is_dir():
        raise ValueError("需要目录路径。")
    if offset < 0 or limit < 1:
        raise ValueError("文件列表分页参数无效。")
    query = query.casefold()
    items = []
    matched = 0
    for item in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())):
        if item.name.startswith('.') or (item.is_file() and item.suffix.lower() not in READABLE):
            continue
        if (directories_only and not item.is_dir()) or (query and query not in item.name.casefold()):
            continue
        try:
            target = resolve_read(str(item))
            entry = {"path": str(target), "name": item.name, "directory": target.is_dir(),
                     "size": target.stat().st_size if target.is_file() else 0}
        except (ValueError, OSError):
            continue
        matched += 1
        if matched <= offset:
            continue
        items.append(entry)
        if len(items) > limit:
            break
    has_more = len(items) > limit
    page = items[:limit]
    return {"files": page, "root": str(directory), "offset": offset, "limit": limit,
            "has_more": has_more, "next_offset": offset + len(page) if has_more else None}


def read_document(path: str, offset: int = 0, max_chars: int = 16000, *, task_id: str = "") -> dict:
    return _read_source(resolve_read(path, task_id=task_id), offset=offset, max_chars=max_chars)


def _read_source(source: Path, offset: int = 0, max_chars: int = 16000) -> dict:
    """Parse already-authorized bytes; callers must authorize the source first."""
    if not source.is_file() or source.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("文档必须存在且小于 32MB。")
    if offset < 0 or max_chars < 1:
        raise ValueError('文档分页参数无效。')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    suffix = source.suffix.lower()
    if suffix in TEXT:
        content = source.read_text(encoding="utf-8-sig")
    elif suffix in {'.docx', '.xlsx'}:
        with zipfile.ZipFile(source) as archive:
            if sum(item.file_size for item in archive.infolist()) > 64 * 1024 * 1024:
                raise ValueError("文档解压体积过大。")
        if suffix == '.docx':
            doc = Document(source)
            paragraphs = [p.text for p in doc.paragraphs]
            paragraphs.extend("\t".join(cell.text for cell in row.cells) for table in doc.tables for row in table.rows)
            content = "\n".join(paragraphs)
        else:
            from openpyxl import load_workbook
            book = load_workbook(source, read_only=True, data_only=False, keep_links=False)
            try:
                lines, characters = [], 0
                for sheet in book:
                    lines.append(f'[工作表：{sheet.title}]')
                    for number, row in enumerate(sheet.iter_rows(values_only=True), 1):
                        line = f'[行 {number}] ' + '\t'.join('' if cell is None else str(cell) for cell in row)
                        characters += len(line)
                        if characters > 2000000 or number > 200000:
                            raise ValueError('工作簿内容过大，请拆分后读取；未返回部分内容作为全文。')
                        lines.append(line)
                content = '\n'.join(lines)
            finally:
                book.close()
    elif suffix == ".pdf":
        with closing(pypdfium2.PdfDocument(source)) as pdf:
            if len(pdf) > 2000:
                raise ValueError('PDF 超过 2000 页，请拆分后读取；未返回前 200 页作为全文。')
            parts = []
            for index in range(len(pdf)):
                with closing(pdf[index]) as page:
                    with closing(page.get_textpage()) as textpage:
                        parts.append(f"[第 {index + 1} 页]\n{textpage.get_text_range()}")
            content = "\n".join(parts)
    else:
        raise ValueError("文档读取支持 TXT、Markdown、JSON、CSV、YAML、DOCX、XLSX 和有文本层的 PDF。")
    if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
        raise ValueError('读取期间文件发生变化，请重新读取。')
    return {"path": str(source), "text": content[offset:offset + max_chars],
            "offset": offset, "total_chars": len(content), "has_more": offset + max_chars < len(content),
            "sha256": digest}


def write_document(name: str, content: str, task_id: str) -> dict:
    filename = Path(name).name
    if filename != name or ':' in name or name.startswith('.') or not re.fullmatch(r"task_[a-zA-Z0-9_-]+", task_id):
        raise ValueError("输出需要普通文件名与有效任务。")
    suffix = Path(name).suffix.lower()
    if suffix not in TEXT | {".docx", '.xlsx', '.pdf'}:
        raise ValueError("输出支持文本、DOCX、XLSX 和 PDF。")
    directory = workspace() / task_id
    directory.mkdir(exist_ok=True)
    if directory.resolve() != directory:
        raise ValueError("任务成果目录不能是联接或符号链接。")
    if suffix == ".json":
        json.loads(content)
    output_metadata = {}
    if suffix in {'.docx', '.xlsx', '.pdf'}:
        from . import office_document_service as office
        if suffix == '.docx': payload = office.docx_bytes(content)
        elif suffix == '.pdf': payload = office.pdf_bytes(content)
        else: payload, output_metadata = office.xlsx_bytes(content)
    else:
        payload = content.encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    output = directory / f"{Path(name).stem}-{digest[:10]}{suffix}"
    if not output.exists():
        with output.open('xb') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    verified = read_document(str(output), max_chars=300)
    if verified["sha256"] != digest:
        raise ValueError("输出文件内容校验失败。")
    from . import artifact_service
    artifact = artifact_service.import_file(output, source_root=directory, producer="agent_document",
        expected_sha256=digest, task_id=task_id, name=name, metadata=output_metadata)
    return {"path": str(output), "name": output.name, "size": output.stat().st_size,
            "sha256": digest, "verified": True, "preview": verified["text"], **output_metadata,
            'verification_scope': 'file_integrity_and_structure', 'quality_status': 'not_reviewed',
            "artifact_id": artifact["id"], "artifact_url": artifact["url"], "version": artifact["version"],
            "url": f"/api/agent/work/files/{task_id}/{quote(output.name, safe='')}"}


def output_file(task_id: str, name: str) -> Path:
    if not re.fullmatch(r"task_[a-zA-Z0-9_-]+", task_id) or Path(name).name != name:
        raise ValueError("无效输出路径。")
    candidate = (workspace() / task_id / name).resolve(strict=True)
    if not candidate.is_relative_to(workspace() / task_id) or not candidate.is_file() or candidate.suffix.lower() not in READABLE:
        raise ValueError("找不到输出文件。")
    return candidate
