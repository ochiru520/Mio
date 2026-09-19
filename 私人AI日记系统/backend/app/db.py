from __future__ import annotations

import json
import re
import sqlite3
from datetime import date as date_value
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import settings
from .repositories.conversation_repository import ConversationRepository


def _local_timezone():
    try:
        return ZoneInfo(settings.timezone)
    except Exception:
        return timezone(timedelta(hours=8), name="Asia/Shanghai")


def now_iso() -> str:
    return datetime.now(_local_timezone()).isoformat(timespec="seconds")


def logical_date_for_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        local = value.replace(tzinfo=_local_timezone())
    else:
        local = value.astimezone(_local_timezone())
    return (local - timedelta(hours=settings.day_boundary_hour)).date().isoformat()


def today_string(current: datetime | None = None) -> str:
    local = current or datetime.fromisoformat(now_iso())
    return logical_date_for_datetime(local)


def logical_day_bounds(date: str) -> tuple[str, str]:
    day = date_value.fromisoformat(date)
    start = datetime.combine(
        day,
        time(hour=settings.day_boundary_hour),
        tzinfo=_local_timezone(),
    )
    end = start + timedelta(days=1)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


def get_conn() -> sqlite3.Connection:
    settings.ensure_directories()
    conn = sqlite3.connect(settings.db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_conversation_repository = ConversationRepository(
    get_conn,
    lambda: now_iso(),
    lambda target_date: logical_day_bounds(target_date),
)




# Domain composition. Legacy imports remain supported; new code belongs in the domains.
from .repositories.schema_repository import SchemaRepository
_schema_service = SchemaRepository(
    dep__ensure_structured_memory_fts=lambda *args, **kwargs: _ensure_structured_memory_fts(*args, **kwargs),
    dep_get_conn=lambda *args, **kwargs: get_conn(*args, **kwargs),
    dep_now_iso=lambda *args, **kwargs: now_iso(*args, **kwargs),
)
init_db = _schema_service.init_db
_ensure_column = _schema_service._ensure_column

from .repositories.memory_repository import MemoryRepository
_memory_service = MemoryRepository(
    dep_get_conn=lambda *args, **kwargs: get_conn(*args, **kwargs),
    dep_now_iso=lambda *args, **kwargs: now_iso(*args, **kwargs),
)
_memory_search_text = _memory_service._memory_search_text
_ensure_structured_memory_fts = _memory_service._ensure_structured_memory_fts
_upsert_structured_memory_fts = _memory_service._upsert_structured_memory_fts
refresh_manual_memories = _memory_service.refresh_manual_memories
get_latest_memory = _memory_service.get_latest_memory
replace_memory = _memory_service.replace_memory
delete_memory = _memory_service.delete_memory
save_structured_memory = _memory_service.save_structured_memory
save_structured_memory_candidate = _memory_service.save_structured_memory_candidate
list_structured_memories = _memory_service.list_structured_memories
get_structured_memory = _memory_service.get_structured_memory
restore_structured_memory = _memory_service.restore_structured_memory
search_structured_memories = _memory_service.search_structured_memories
set_structured_memory_status = _memory_service.set_structured_memory_status
sleep_stale_structured_memories = _memory_service.sleep_stale_structured_memories
mark_structured_memories_seen = _memory_service.mark_structured_memories_seen
confirm_structured_memory_candidate = _memory_service.confirm_structured_memory_candidate
archive_structured_memory = _memory_service.archive_structured_memory
list_memories_by_type = _memory_service.list_memories_by_type

from .repositories.messages_repository import MessagesRepository
_messages_service = MessagesRepository(
    dep__conversation_repository=lambda: _conversation_repository,
    dep_get_conn=lambda *args, **kwargs: get_conn(*args, **kwargs),
    dep_logical_date_for_datetime=lambda *args, **kwargs: logical_date_for_datetime(*args, **kwargs),
    dep_logical_day_bounds=lambda *args, **kwargs: logical_day_bounds(*args, **kwargs),
    dep_now_iso=lambda *args, **kwargs: now_iso(*args, **kwargs),
    dep_settings=lambda: settings,
    dep_today_string=lambda *args, **kwargs: today_string(*args, **kwargs),
)
conversation_deleted = _messages_service.conversation_deleted
assert_conversation_writable = _messages_service.assert_conversation_writable
save_message = _messages_service.save_message
claim_chat_request = _messages_service.claim_chat_request
complete_chat_request = _messages_service.complete_chat_request
fail_chat_request = _messages_service.fail_chat_request
chat_request_payload = _messages_service.chat_request_payload
get_recent_messages = _messages_service.get_recent_messages
get_total_message_token_usage = _messages_service.get_total_message_token_usage
get_token_usage_summary = _messages_service.get_token_usage_summary
list_recent_private_user_messages = _messages_service.list_recent_private_user_messages
get_message_by_id = _messages_service.get_message_by_id
get_latest_message_id = _messages_service.get_latest_message_id
get_messages_after_id = _messages_service.get_messages_after_id
create_agent_conversation = _messages_service.create_agent_conversation
get_agent_conversation = _messages_service.get_agent_conversation
list_agent_conversations = _messages_service.list_agent_conversations
touch_agent_conversation = _messages_service.touch_agent_conversation
rename_agent_conversation = _messages_service.rename_agent_conversation
list_conversation_attachment_records = _messages_service.list_conversation_attachment_records
list_message_attachment_records = _messages_service.list_message_attachment_records
delete_agent_conversation = _messages_service.delete_agent_conversation
delete_conversation_messages = _messages_service.delete_conversation_messages
get_messages_since = _messages_service.get_messages_since
get_last_message = _messages_service.get_last_message
get_today_messages = _messages_service.get_today_messages

from .repositories.costs_repository import CostsRepository
_costs_service = CostsRepository(
    dep_get_conn=lambda *args, **kwargs: get_conn(*args, **kwargs),
    dep_now_iso=lambda *args, **kwargs: now_iso(*args, **kwargs),
    dep_today_string=lambda *args, **kwargs: today_string(*args, **kwargs),
)
enqueue_cost_reconciliation = _costs_service.enqueue_cost_reconciliation
list_due_cost_reconciliation_jobs = _costs_service.list_due_cost_reconciliation_jobs
resolve_cost_reconciliation_job = _costs_service.resolve_cost_reconciliation_job
retry_cost_reconciliation_job = _costs_service.retry_cost_reconciliation_job
refresh_reconciled_message_cost = _costs_service.refresh_reconciled_message_cost
record_screen_analysis_usage = _costs_service.record_screen_analysis_usage
get_screen_analysis_usage = _costs_service.get_screen_analysis_usage
get_screen_analysis_costs_since = _costs_service.get_screen_analysis_costs_since

from .repositories.observations_repository import ObservationsRepository
_observations_service = ObservationsRepository(
    dep__local_timezone=lambda *args, **kwargs: _local_timezone(*args, **kwargs),
    dep_get_conn=lambda *args, **kwargs: get_conn(*args, **kwargs),
    dep_now_iso=lambda *args, **kwargs: now_iso(*args, **kwargs),
)
_screen_session_is_recent = _observations_service._screen_session_is_recent
start_screen_session = _observations_service.start_screen_session
end_screen_sessions = _observations_service.end_screen_sessions
cleanup_screen_observation_history = _observations_service.cleanup_screen_observation_history
save_screen_event = _observations_service.save_screen_event
save_observation = _observations_service.save_observation
recent_observations = _observations_service.recent_observations
get_game_session_state = _observations_service.get_game_session_state
upsert_game_session_state = _observations_service.upsert_game_session_state
recent_screen_event_summaries = _observations_service.recent_screen_event_summaries
save_companion_reaction = _observations_service.save_companion_reaction

from .repositories.daily_state_repository import DailyStateRepository
_daily_state_service = DailyStateRepository(
    dep_get_conn=lambda *args, **kwargs: get_conn(*args, **kwargs),
    dep_now_iso=lambda *args, **kwargs: now_iso(*args, **kwargs),
    dep_today_string=lambda *args, **kwargs: today_string(*args, **kwargs),
)
add_diary_material = _daily_state_service.add_diary_material
add_diary_material_once = _daily_state_service.add_diary_material_once
list_diary_materials = _daily_state_service.list_diary_materials
get_daily_state = _daily_state_service.get_daily_state
ensure_daily_state_today = _daily_state_service.ensure_daily_state_today
list_daily_states_since = _daily_state_service.list_daily_states_since
upsert_daily_state = _daily_state_service.upsert_daily_state
update_daily_thirty = _daily_state_service.update_daily_thirty
update_daily_mood = _daily_state_service.update_daily_mood
update_daily_state_summary = _daily_state_service.update_daily_state_summary

from .repositories.followups_repository import FollowupsRepository
_followups_service = FollowupsRepository(
    dep_get_conn=lambda *args, **kwargs: get_conn(*args, **kwargs),
    dep_now_iso=lambda *args, **kwargs: now_iso(*args, **kwargs),
)
remember_pending_thread = _followups_service.remember_pending_thread
list_open_pending_threads = _followups_service.list_open_pending_threads
list_due_pending_threads = _followups_service.list_due_pending_threads
resolve_pending_thread = _followups_service.resolve_pending_thread
mark_pending_thread_mentioned = _followups_service.mark_pending_thread_mentioned
get_night_close_prompted_date = _followups_service.get_night_close_prompted_date
set_night_close_prompted_date = _followups_service.set_night_close_prompted_date
list_all_open_pending_threads = _followups_service.list_all_open_pending_threads
get_pending_thread = _followups_service.get_pending_thread
find_open_pending_thread = _followups_service.find_open_pending_thread
record_follow_up_result = _followups_service.record_follow_up_result
list_follow_up_results = _followups_service.list_follow_up_results
resolve_pending_thread_by_id = _followups_service.resolve_pending_thread_by_id
update_pending_thread_by_id = _followups_service.update_pending_thread_by_id
delete_pending_thread_by_id = _followups_service.delete_pending_thread_by_id

from .repositories.agent_runs_repository import AgentRunsRepository
_agent_runs_service = AgentRunsRepository(
    dep_get_conn=lambda *args, **kwargs: get_conn(*args, **kwargs),
    dep_now_iso=lambda *args, **kwargs: now_iso(*args, **kwargs),
    dep_today_string=lambda *args, **kwargs: today_string(*args, **kwargs),
)
create_agent_run = _agent_runs_service.create_agent_run
get_agent_run = _agent_runs_service.get_agent_run
get_agent_run_by_request = _agent_runs_service.get_agent_run_by_request
get_agent_runs_by_requests = _agent_runs_service.get_agent_runs_by_requests
save_model_route_observation = _agent_runs_service.save_model_route_observation
list_model_route_observations = _agent_runs_service.list_model_route_observations
relink_agent_run_source_message = _agent_runs_service.relink_agent_run_source_message
update_agent_run = _agent_runs_service.update_agent_run
list_agent_runs = _agent_runs_service.list_agent_runs
claim_agent_run_step = _agent_runs_service.claim_agent_run_step
update_agent_run_step = _agent_runs_service.update_agent_run_step
list_agent_run_steps = _agent_runs_service.list_agent_run_steps
list_agent_run_steps_many = _agent_runs_service.list_agent_run_steps_many
get_agent_run_step = _agent_runs_service.get_agent_run_step
start_tool_execution_receipt = _agent_runs_service.start_tool_execution_receipt
finish_tool_execution_receipt = _agent_runs_service.finish_tool_execution_receipt
list_tool_execution_receipts = _agent_runs_service.list_tool_execution_receipts
log_companion_action = _agent_runs_service.log_companion_action
update_companion_action = _agent_runs_service.update_companion_action
list_companion_actions = _agent_runs_service.list_companion_actions
get_companion_action = _agent_runs_service.get_companion_action

from .repositories.diaries_repository import DiariesRepository
_diaries_service = DiariesRepository(
    dep_get_conn=lambda *args, **kwargs: get_conn(*args, **kwargs),
    dep_logical_day_bounds=lambda *args, **kwargs: logical_day_bounds(*args, **kwargs),
    dep_now_iso=lambda *args, **kwargs: now_iso(*args, **kwargs),
    dep_today_string=lambda *args, **kwargs: today_string(*args, **kwargs),
)
get_day_summary = _diaries_service.get_day_summary
upsert_diary = _diaries_service.upsert_diary
list_diaries = _diaries_service.list_diaries
search_diaries = _diaries_service.search_diaries
get_diary = _diaries_service.get_diary
list_diaries_since = _diaries_service.list_diaries_since
list_diary_exports = _diaries_service.list_diary_exports
get_daily_review = _diaries_service.get_daily_review
list_daily_reviews_since = _diaries_service.list_daily_reviews_since
upsert_daily_review = _diaries_service.upsert_daily_review
set_diary_confirmed = _diaries_service.set_diary_confirmed
list_reviews = _diaries_service.list_reviews
get_diary_stats = _diaries_service.get_diary_stats
get_calendar_data = _diaries_service.get_calendar_data
delete_diary = _diaries_service.delete_diary
search_all = _diaries_service.search_all
mark_materials_used = _diaries_service.mark_materials_used

from .repositories.autonomy_repository import AutonomyRepository
_autonomy_service = AutonomyRepository(
    dep_get_conn=lambda *args, **kwargs: get_conn(*args, **kwargs),
    dep_now_iso=lambda *args, **kwargs: now_iso(*args, **kwargs),
)
get_qq_proactive_state = _autonomy_service.get_qq_proactive_state
upsert_qq_proactive_state = _autonomy_service.upsert_qq_proactive_state
record_proactive_topic = _autonomy_service.record_proactive_topic
list_recent_proactive_topics = _autonomy_service.list_recent_proactive_topics
get_autonomy_policy = _autonomy_service.get_autonomy_policy
update_autonomy_policy = _autonomy_service.update_autonomy_policy
create_agent_goal = _autonomy_service.create_agent_goal
get_agent_goal = _autonomy_service.get_agent_goal
list_agent_goals = _autonomy_service.list_agent_goals
update_agent_goal_status = _autonomy_service.update_agent_goal_status
record_agent_event = _autonomy_service.record_agent_event
claim_next_agent_event = _autonomy_service.claim_next_agent_event
finish_agent_event = _autonomy_service.finish_agent_event
reschedule_agent_event = _autonomy_service.reschedule_agent_event
list_agent_events = _autonomy_service.list_agent_events
create_autonomy_behavior = _autonomy_service.create_autonomy_behavior
get_autonomy_behavior = _autonomy_service.get_autonomy_behavior
get_autonomy_behavior_by_key = _autonomy_service.get_autonomy_behavior_by_key
update_autonomy_behavior = _autonomy_service.update_autonomy_behavior
list_autonomy_behaviors = _autonomy_service.list_autonomy_behaviors
autonomy_usage_between = _autonomy_service.autonomy_usage_between

from .repositories.reviews_repository import ReviewsRepository
_reviews_service = ReviewsRepository(
    dep_get_conn=lambda *args, **kwargs: get_conn(*args, **kwargs),
    dep_now_iso=lambda *args, **kwargs: now_iso(*args, **kwargs),
)
get_weekly_review = _reviews_service.get_weekly_review
list_weekly_reviews = _reviews_service.list_weekly_reviews
upsert_weekly_review = _reviews_service.upsert_weekly_review
get_monthly_review = _reviews_service.get_monthly_review
list_monthly_reviews = _reviews_service.list_monthly_reviews
upsert_monthly_review = _reviews_service.upsert_monthly_review
