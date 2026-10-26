"""SQLite persistence and transaction scopes for autonomy."""
from __future__ import annotations

from collections import namedtuple
from contextlib import closing, contextmanager
from .. import db

WriteResult = namedtuple("WriteResult", "rowcount lastrowid")


class AutonomyRepository:
    """A transaction-bound domain store; callers never supply SQL statements."""

    def __init__(self, connection):
        self._conn = connection

    def collect_scheduled_proactive_events_find_agent_events(self, parameters):
        """collect scheduled proactive events persistence: find agent_events."""
        return self._conn.execute("SELECT 1 FROM agent_events WHERE event_type='proactive_checkin_due' AND conversation_id=? AND status IN ('pending','claimed') LIMIT 1", parameters).fetchone()


    def process_claimed_event_update_agent_events(self, parameters):
        """process claimed event persistence: update agent_events."""
        return self._conn.execute("UPDATE agent_events SET status='pending',available_at=?,payload_json=?,error=?,decision_reason=?,claim_token='',claimed_at='',updated_at=? WHERE id=? AND status='claimed'", parameters).rowcount


@contextmanager
def read():
    with closing(db.get_conn()) as connection:
        yield AutonomyRepository(connection)


@contextmanager
def transaction():
    with closing(db.get_conn()) as connection, connection:
        yield AutonomyRepository(connection)


def bind(connection):
    """Join an existing transaction without committing or closing its connection."""
    return connection if isinstance(connection, AutonomyRepository) else AutonomyRepository(connection)
