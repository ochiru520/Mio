"""Temporal orientation and source-aware history text for chat context.

Use the shared configured clock and logical-day boundary. History labels remain
absolute so prompt-cache bytes stay stable when time advances; current relative
dates and conversation gaps belong in the per-request runtime context.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from . import db
from .config import settings


USER_CONTINUING_ACTIVITY_RE = re.compile(
    r"(?:还在|一直在|仍在|继续|接着|没停).{0,12}(?:做|写|弄|搞|改|画|整理|制作|开发|搭建|配置|测试|验收|剪|练|学|跑|运动|锻炼|健身|阅读|复习)"
    r"|(?:做|写|弄|搞|改|画|整理|制作|开发|搭建|配置|测试|验收|剪|练|学|跑|运动|锻炼|健身|阅读|复习).{0,8}(?:到现在|还没停)"
)



USER_PRODUCTIVE_ACTIVITY_RE = re.compile(
    r"(在|正|开始|继续|接着|一直).{0,8}(做|写|弄|搞|改|画|整理|制作|开发|搭建|配置|测试|验收|剪|练|学|跑|运动|锻炼|健身|阅读|复习)"
    r"|(?:demo|代码|设计|文档|笔记|作品|项目|游戏|网站|模型|视频|作业|东西|流水线|跑步|跑了|运动|锻炼|健身|力量训练|阅读|看书|课程|学习|复习|面试准备)",
    re.IGNORECASE,
)



def _time_period(hour: int) -> tuple[str, str]:
    if 0 <= hour < 5:
        return "凌晨", "更适合关心为什么还没睡、身体是否难受、要不要先收尾休息"
    if 5 <= hour < 8:
        return "清晨", "可以自然聊睡得怎么样、刚醒的状态、早餐或今天最先要做的事"
    if 8 <= hour < 12:
        return "上午", "可以自然聊今天的安排、正在推进的事、上午的精力和进度"
    if 12 <= hour < 14:
        return "中午", "可以自然聊午饭、上午过得怎样、要不要稍微休息一下"
    if 14 <= hour < 18:
        return "下午", "可以自然聊手头事情的进展、下午的疲惫感、接下来最小的一步"
    if 18 <= hour < 20:
        return "傍晚", "可以自然聊晚饭、回家或收工、今天到目前为止发生了什么"
    if 20 <= hour < 23:
        return "晚上", "可以自然聊今天的感受、晚上的安排、还想完成什么或怎样放松"
    return "深夜", "更适合安静聊天、简单回顾今天、关心困不困和准备什么时候休息"



def _build_current_time_context(current: datetime | None = None) -> str:
    local_now = current or datetime.fromisoformat(db.now_iso())
    period, _ = _time_period(local_now.hour)
    weekday = "一二三四五六日"[local_now.weekday()]
    local_time = local_now.isoformat(sep=" ", timespec="minutes")
    logical_date = db.logical_date_for_datetime(local_now)
    if logical_date == local_now.date().isoformat():
        logical_note = f"当前记录日：{logical_date}。"
    else:
        logical_note = f"当前记录日仍是 {logical_date}。"
    return (
        f"当前时间（本地）：{local_time}，时区：{settings.timezone}，星期{weekday}，{period}。\n"
        f"{logical_note}记录日边界：{settings.day_boundary_hour:02d}:00。"
    )



def _parse_message_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None



def _relative_message_time(created_at: object, current: datetime) -> str:
    message_time = _parse_message_datetime(created_at)
    if message_time is None:
        return "时间未知"
    current_logical_date = date.fromisoformat(db.logical_date_for_datetime(current))
    message_logical_date = date.fromisoformat(db.logical_date_for_datetime(message_time))
    day_delta = (current_logical_date - message_logical_date).days
    clock = message_time.strftime("%H:%M")
    if day_delta == 0:
        if current.date() != message_time.date():
            return f"今天（昨晚）{clock}"
        return f"今天 {clock}"
    if day_delta == 1:
        return f"昨天 {clock}"
    if day_delta > 1:
        return f"{message_time.strftime('%Y-%m-%d %H:%M')}（{day_delta} 天前）"
    return f"{message_time.strftime('%Y-%m-%d %H:%M')}（未来时间记录，谨慎使用）"



def _build_conversation_orientation_context(
    history_rows: list[object],
    current_message_id: int | None = None,
    current: datetime | None = None,
) -> str:
    local_now = current or datetime.fromisoformat(db.now_iso())
    previous_rows = [
        row
        for row in history_rows
        if current_message_id is None or int(row["id"]) != current_message_id
    ]
    if not previous_rows:
        return "当前会话可见记录：第一次对话。"

    previous = previous_rows[-1]
    previous_time = _parse_message_datetime(previous["created_at"])
    relative_time = _relative_message_time(previous["created_at"], local_now)
    if previous_time is None:
        relation = "上一条消息时间无法确认。"
    else:
        current_logical_date = date.fromisoformat(db.logical_date_for_datetime(local_now))
        previous_logical_date = date.fromisoformat(db.logical_date_for_datetime(previous_time))
        day_delta = (current_logical_date - previous_logical_date).days
        gap = max(timedelta(0), local_now - previous_time)
        gap_minutes = int(gap.total_seconds() // 60)
        if day_delta == 0 and local_now.date() != previous_time.date():
            relation = (
                f"这是跨过零点但仍属于同一个记录日的继续，实际间隔约 {max(1, round(gap_minutes / 60))} 小时。"
            )
        elif day_delta == 0 and gap_minutes <= 120:
            relation = f"这是今天同一段对话的继续，实际间隔约 {max(1, gap_minutes)} 分钟。"
        elif day_delta == 0:
            relation = f"这是今天间隔约 {max(1, round(gap_minutes / 60))} 小时后重新开始的一段对话。"
        elif day_delta == 1:
            relation = "这是跨天后的新一轮对话。上一轮属于昨天；昨天消息里的“今天”现在必须理解为昨天。"
        else:
            relation = f"这是间隔 {day_delta} 天后重新开始的对话。"

    return f"上一条可见消息：{relative_time}。\n会话关系：{relation}"



def _format_message_gap(minutes: int) -> str:
    hours, remainder = divmod(max(0, minutes), 60)
    if hours and remainder:
        return f"{hours} 小时 {remainder} 分钟"
    if hours:
        return f"{hours} 小时"
    return f"{minutes} 分钟"



def _build_user_message_gap_context(
    history_rows: list[object],
    current_message_id: int,
) -> str:
    current_row = next(
        (
            row
            for row in history_rows
            if int(row["id"]) == current_message_id and row["role"] == "user"
        ),
        None,
    )
    if current_row is None:
        return "本轮用户消息未定位，发言间隔未知。"

    previous_users = [
        row
        for row in history_rows
        if row["role"] == "user" and int(row["id"]) < current_message_id
    ]
    if not previous_users:
        return "这是当前可见记录中的第一条用户消息，没有可计算的用户发言间隔。"

    previous = previous_users[-1]
    previous_time = _parse_message_datetime(previous["created_at"])
    current_time = _parse_message_datetime(current_row["created_at"])
    if previous_time is None or current_time is None:
        return "最近两条用户消息的时间不完整，发言间隔未知。"

    gap_minutes = max(0, int((current_time - previous_time).total_seconds() // 60))
    same_logical_day = (
        db.logical_date_for_datetime(previous_time)
        == db.logical_date_for_datetime(current_time)
    )
    previous_content = str(previous["content"] or "").strip()[:240]
    current_content = str(current_row["content"] or "").strip()[:240]
    day_note = "属于同一个记录日" if same_logical_day else "已经属于不同记录日"
    continuity_note = ""
    if USER_CONTINUING_ACTIVITY_RE.search(current_content):
        for candidate in reversed(previous_users):
            candidate_time = _parse_message_datetime(candidate["created_at"])
            if candidate_time is None:
                continue
            if db.logical_date_for_datetime(candidate_time) != db.logical_date_for_datetime(current_time):
                break
            candidate_gap = int((current_time - candidate_time).total_seconds() // 60)
            if candidate_gap > 12 * 60:
                break
            candidate_content = str(candidate["content"] or "").strip()[:240]
            if USER_PRODUCTIVE_ACTIVITY_RE.search(candidate_content):
                continuity_note = (
                    f"\n持续活动措辞：有；最近的活动起点线索："
                    f"{candidate_time.strftime('%H:%M')} 的“{candidate_content}”，"
                    f"距本轮约 {_format_message_gap(candidate_gap)}。"
                )
                break
    return f"""上一条用户消息：{previous_time.strftime('%Y-%m-%d %H:%M')}，“{previous_content}”。
本轮用户消息：{current_time.strftime('%Y-%m-%d %H:%M')}，“{current_content}”。
两次用户发言实际间隔：约 {_format_message_gap(gap_minutes)}，{day_note}。{continuity_note}"""



def _annotate_history_content(
    row: object,
    content: object,
    current: datetime,
    current_message_id: int | None = None,
) -> object:
    # History must keep the same bytes when today's message becomes yesterday's.
    # Relative dates and the current-turn marker belong to the runtime context.
    created = _parse_message_datetime(row["created_at"])
    label = created.isoformat(timespec="seconds") if created else "时间未知"
    note = f"[内部消息时间：{label}]"
    if isinstance(content, str):
        if current_message_id is None or int(row["id"]) != current_message_id:
            from .memory_revision_service import sanitize_history
            content = sanitize_history(content, source_message_id=int(row['id']))
        return f"{note}\n{content}"
    if isinstance(content, list):
        return [{"type": "text", "text": note}, *content]
    return content
