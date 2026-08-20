from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

from . import db
from .config import settings


DESKTOP_PET_CONVERSATION_ID = "desktop_pet"


def _non_negative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def primary_conversation_id() -> str:
    if settings.qq_allowed_user_ids:
        return f"qq_private_{settings.qq_allowed_user_ids[0]}"
    return "default"


def resolve_conversation_id(requested: str = "") -> str:
    clean = requested.strip()
    if not clean or clean == primary_conversation_id():
        return primary_conversation_id()
    if clean == DESKTOP_PET_CONVERSATION_ID:
        return clean
    if clean.startswith("desktop_") and db.get_agent_conversation(clean) is not None:
        return clean
    raise ValueError("没有找到这个对话窗口。")


def list_conversations() -> list[dict[str, object]]:
    primary_id = primary_conversation_id()
    primary_last = db.get_last_message(primary_id)
    pet_last = db.get_last_message(DESKTOP_PET_CONVERSATION_ID)
    conversations = [{
        "id": primary_id,
        "title": "QQ共享对话",
        "kind": "qq",
        "preview": str(primary_last["content"] or "") if primary_last else "QQ与桌面应用共享的主对话",
        "updated_at": str(primary_last["created_at"] or "") if primary_last else "",
    }, {
        "id": DESKTOP_PET_CONVERSATION_ID,
        "title": "桌宠Mio",
        "kind": "pet",
        "preview": str(pet_last["content"] or "") if pet_last else "桌宠独立上下文与语音回复",
        "updated_at": str(pet_last["created_at"] or "") if pet_last else "",
    }]
    conversations.extend(
        {**dict(row), "kind": "desktop"}
        for row in db.list_agent_conversations()
        if str(row["id"] or "") not in {primary_id, DESKTOP_PET_CONVERSATION_ID}
    )
    return conversations


def _agent_tool_receipts(run, steps: list[object] | None = None) -> list[dict[str, object]]:
    try:
        saved = json.loads(str(run["observation_json"] or "[]"))
    except (TypeError, json.JSONDecodeError):
        saved = []
    saved_by_step: dict[int, dict[str, object]] = {}
    if isinstance(saved, list):
        for item in saved:
            if not isinstance(item, dict):
                continue
            step_id = _non_negative_int(item.get("step_id"))
            if step_id > 0:
                saved_by_step[step_id] = item

    receipts: list[dict[str, object]] = []
    step_rows = steps if steps is not None else db.list_agent_run_steps(str(run["run_id"] or ""))
    for row in step_rows:
        if str(row["step_kind"] or "") != "tool_call":
            continue
        step_id = _non_negative_int(row["id"])
        previous = saved_by_step.get(step_id, {})
        try:
            result = json.loads(str(row["result_json"] or "{}"))
        except json.JSONDecodeError:
            result = {}
        if not isinstance(result, dict):
            result = {}
        if not result and isinstance(previous.get("result"), dict):
            result = dict(previous["result"])
        receipts.append({
            "tool_name": str(row["tool_name"] or previous.get("tool_name") or ""),
            "status": str(row["status"] or previous.get("status") or "failed"),
            "result": result,
            "step_id": step_id,
            "action_id": _non_negative_int(row["action_id"] or previous.get("action_id")),
            "receipt_id": _non_negative_int(row["receipt_id"] or previous.get("receipt_id")),
            "replayed": bool(previous.get("replayed")),
            "error": str(row["error"] or previous.get("error") or ""),
        })
    return receipts


def _public_message_base(row) -> dict[str, object]:
    result = dict(row)
    try:
        result["attachments"] = json.loads(result.pop("attachments_json", "[]") or "[]")
    except (TypeError, json.JSONDecodeError):
        result["attachments"] = []
    if result.get("role") == "user":
        result["request_cost_yuan"] = 0.0
    return result


def public_messages(rows: list[object]) -> list[dict[str, object]]:
    results = [_public_message_base(row) for row in rows]
    request_ids = [
        str(item.get("request_id") or "")
        for item in results
        if item.get("role") != "user" and item.get("request_id")
    ]
    runs = db.get_agent_runs_by_requests(request_ids)
    runs_by_request = {str(run["request_id"] or ""): run for run in runs}
    steps_by_run: dict[str, list[object]] = {}
    for step in db.list_agent_run_steps_many([str(run["run_id"] or "") for run in runs]):
        steps_by_run.setdefault(str(step["run_id"] or ""), []).append(step)
    for result in results:
        run = runs_by_request.get(str(result.get("request_id") or ""))
        if run is None:
            continue
        run_id = str(run["run_id"] or "")
        result["agent_run_id"] = run_id
        result["agent_run_status"] = str(run["status"] or "")
        result["tool_receipts"] = _agent_tool_receipts(run, steps_by_run.get(run_id, []))
    return results


def public_message(row) -> dict[str, object]:
    return public_messages([row])[0]


class AttachmentCleanupError(OSError):
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        names = "、".join(item["path"] for item in result["errors"][:3])
        super().__init__(f"有 {len(result['errors'])} 个附件无法删除：{names}")


def _unlink_attachment(path: Path) -> None:
    path.unlink()


def _move_attachment(source: Path, target: Path) -> None:
    shutil.move(str(source), str(target))


class ArchivedAttachmentTransaction:
    def __init__(self, staging: Path, entries: list[tuple[Path, Path]], result: dict[str, Any]) -> None:
        self.staging = staging
        self.entries = entries
        self.result = result
        self.closed = False

    def commit(self) -> dict[str, Any]:
        if not self.closed:
            shutil.rmtree(self.staging, ignore_errors=False)
            self.closed = True
        return self.result

    def rollback(self) -> None:
        if self.closed:
            return
        restore_errors: list[str] = []
        for original, staged in reversed(self.entries):
            if not staged.exists():
                continue
            try:
                original.parent.mkdir(parents=True, exist_ok=True)
                _move_attachment(staged, original)
            except OSError as exc:
                restore_errors.append(f"{original.name}: {exc}")
        if restore_errors:
            self.closed = True
            raise OSError(
                "附件回滚失败，暂存文件已保留在 "
                f"{self.staging}：{'；'.join(restore_errors[:3])}"
            )
        try:
            shutil.rmtree(self.staging, ignore_errors=False)
        finally:
            self.closed = True


def stage_archived_attachments(records: list[str], *, strict: bool = False) -> ArchivedAttachmentTransaction:
    root = settings.agent_attachment_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".mio-delete-", dir=str(root)))
    result: dict[str, Any] = {"deleted": 0, "missing": 0, "rejected": 0, "errors": []}
    entries: list[tuple[Path, Path]] = []
    seen: set[Path] = set()
    try:
        for record in records:
            try:
                attachments = json.loads(record)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(attachments, list):
                continue
            for attachment in attachments:
                if not isinstance(attachment, dict):
                    continue
                url = str(attachment.get("url") or "")
                prefix = "/agent-files/"
                if not url.startswith(prefix):
                    continue
                target = (root / url.removeprefix(prefix)).resolve()
                if not target.is_relative_to(root):
                    result["rejected"] += 1
                    continue
                if target in seen:
                    continue
                seen.add(target)
                if not target.exists():
                    result["missing"] += 1
                    continue
                if not target.is_file():
                    result["rejected"] += 1
                    continue
                staged = staging / str(len(entries))
                try:
                    _move_attachment(target, staged)
                except OSError as exc:
                    result["errors"].append({"path": target.name, "error": str(exc)[:500]})
                    continue
                entries.append((target, staged))
                result["deleted"] += 1
        if strict and result["errors"]:
            raise AttachmentCleanupError(result)
        return ArchivedAttachmentTransaction(staging, entries, result)
    except Exception:
        transaction = ArchivedAttachmentTransaction(staging, entries, result)
        try:
            transaction.rollback()
        except OSError as rollback_error:
            raise OSError(f"附件暂存失败，回滚也失败：{rollback_error}") from rollback_error
        raise


def delete_archived_attachments(records: list[str], *, strict: bool = False) -> dict[str, Any]:
    root = settings.agent_attachment_dir.resolve()
    result: dict[str, Any] = {
        "deleted": 0,
        "missing": 0,
        "rejected": 0,
        "errors": [],
    }
    seen: set[Path] = set()
    for record in records:
        try:
            attachments = json.loads(record)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(attachments, list):
            continue
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            url = str(attachment.get("url") or "")
            prefix = "/agent-files/"
            if not url.startswith(prefix):
                continue
            target = (root / url.removeprefix(prefix)).resolve()
            if not target.is_relative_to(root):
                result["rejected"] += 1
                continue
            if target in seen:
                continue
            seen.add(target)
            if not target.exists():
                result["missing"] += 1
                continue
            if not target.is_file():
                result["rejected"] += 1
                continue
            try:
                _unlink_attachment(target)
                result["deleted"] += 1
            except OSError as exc:
                result["errors"].append({"path": target.name, "error": str(exc)[:500]})
    if strict and result["errors"]:
        raise AttachmentCleanupError(result)
    return result
