from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from . import db


@dataclass(frozen=True)
class ChatRequestClaim:
    client_request_id: str
    created: bool
    status: str
    response: dict[str, object]
    error: dict[str, object]
    http_status: int


def request_fingerprint(payload: object) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def content_fingerprint(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def claim_request(
    client_request_id: str,
    request_hash: str,
    *,
    conversation_id: str,
    source: str,
) -> ChatRequestClaim:
    request_id = str(client_request_id or "").strip()[:80] or uuid.uuid4().hex
    created, row = db.claim_chat_request(
        request_id,
        request_hash,
        conversation_id=conversation_id,
        source=source,
    )
    return ChatRequestClaim(
        client_request_id=request_id,
        created=created,
        status=str(row["status"] or "pending"),
        response=db.chat_request_payload(row, "response_json"),
        error=db.chat_request_payload(row, "error_json"),
        http_status=int(row["http_status"] or 0),
    )


def pending_error(claim: ChatRequestClaim) -> dict[str, object]:
    return {
        "code": "chat_request_in_progress",
        "message": "这条消息仍在处理中；Mio 不会重复调用模型，请稍后刷新对话。",
        "request_id": claim.client_request_id,
    }


def normalize_error_detail(
    detail: object,
    *,
    code: str,
    request_id: str,
) -> dict[str, object]:
    if isinstance(detail, dict):
        normalized = dict(detail)
        normalized.setdefault("code", code)
        normalized.setdefault("message", str(detail.get("message") or code))
        normalized.setdefault("request_id", request_id)
        return normalized
    return {
        "code": code,
        "message": str(detail or code),
        "request_id": request_id,
    }


__all__ = [
    "ChatRequestClaim",
    "claim_request",
    "content_fingerprint",
    "normalize_error_detail",
    "pending_error",
    "request_fingerprint",
]
