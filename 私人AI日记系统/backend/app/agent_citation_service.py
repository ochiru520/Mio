"""Durable citations for Agent replies.

The citation manifest is stored with the assistant message so a later source
change cannot silently rewrite what the user saw at reply time.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import knowledge_service as knowledge


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[。！？!?；;])\s*|\n+", text or "") if part.strip()]


def _source(raw: dict[str, Any]) -> dict[str, Any] | None:
    source = raw.get("source", raw)
    if not isinstance(source, dict) or not source.get("document_id"):
        return None
    document_id = str(source["document_id"])
    digest = str(source.get("sha256") or "")
    path: Path | None = None
    document: dict[str, Any] | None = None
    try:
        path, document = knowledge.source_file(document_id)
        current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    except (ValueError, OSError):
        current_hash = ""
    status = "current" if digest and current_hash == digest else ("unavailable" if not current_hash else "stale")
    key = ":".join((document_id, digest, str(source.get("chunk_id") or raw.get("id") or "")))
    return {
        "key": key,
        "document_id": document_id,
        "name": str(source.get("name") or raw.get("name") or "资料"),
        "sha256": digest,
        "page": source.get("page", 0),
        "line": source.get("line", 0),
        "chunk_id": source.get("chunk_id", raw.get("id")),
        "status": status,
        "url": f"/api/workspace/knowledge/{document_id}/source" + (f"#page={source.get('page')}" if source.get("page") else ""),
        "excerpt": str(raw.get("content") or source.get("content") or "")[:1200],
        "_evidence": " ".join(str(value or "") for value in (raw.get("content"), source.get("content"), source.get("name"), raw.get("name"))),
    }


def _sentence_source_keys(sentence: str, sources: list[dict[str, Any]]) -> list[str]:
    """Attach only sources whose indexed evidence can explain this sentence."""
    normalized = re.sub(r"\s+", "", sentence).casefold()
    matches: list[str] = []
    for source in sources:
        evidence = re.sub(r"\s+", "", str(source.get("_evidence") or "")).casefold()
        name = re.sub(r"\s+", "", str(source.get("name") or "")).casefold()
        if name and name in normalized:
            matches.append(source["key"])
            continue
        if evidence and (evidence in normalized or normalized in evidence):
            matches.append(source["key"])
            continue
        fragments = {evidence[index:index + 4] for index in range(max(0, len(evidence) - 3)) if len(evidence[index:index + 4]) >= 4}
        if any(fragment in normalized for fragment in fragments):
            matches.append(source["key"])
    return matches


def manifest(reply: str, observations: Any) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in observations or ():
        tool_name = getattr(item, "tool_name", None) if not isinstance(item, dict) else item.get("tool_name") or item.get("tool")
        if tool_name != "knowledge_search":
            continue
        payload = getattr(item, "result", {}) if not isinstance(item, dict) else item.get("result", {})
        if not isinstance(payload, dict):
            continue
        from .knowledge_service import validate_agent_result
        payload = validate_agent_result(payload)
        for raw in payload.get("results", []) or []:
            if not isinstance(raw, dict):
                continue
            value = _source(raw)
            if value and value["key"] not in seen:
                seen.add(value["key"]); sources.append(value)
    sentences = _sentences(reply)
    current_sources = [source for source in sources if source["status"] == "current"]
    snapshot = [{key: value for key, value in source.items() if key != "_evidence"} for source in sources]
    return {"version": 1, "source_snapshot": snapshot,
            "sentences": [{"index": index, "text": sentence,
                           "source_keys": _sentence_source_keys(sentence, current_sources)}
                          for index, sentence in enumerate(sentences)]}


def attachment(reply: str, observations: Any) -> dict[str, Any]:
    return {"kind": "citation_manifest", **manifest(reply, observations)}


def decode_attachments(value: str) -> list[dict[str, Any]]:
    try:
        result = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return result if isinstance(result, list) else []
