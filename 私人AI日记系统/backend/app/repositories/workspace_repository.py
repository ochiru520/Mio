"""Project-space persistence and atomic units of work; no filesystem or task execution."""
from contextlib import closing, contextmanager
from .. import db


@contextmanager
def read():
    with closing(db.get_conn()) as conn:
        yield WorkspaceUnit(conn)


@contextmanager
def transaction():
    with closing(db.get_conn()) as conn, conn:
        yield WorkspaceUnit(conn)


def initialize():

    with closing(db.get_conn()) as conn, conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS workspace_projects(
          id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',
          instructions TEXT NOT NULL DEFAULT '',archived INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workspace_conversations(
          conversation_id TEXT PRIMARY KEY,project_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workspace_conversation_modes(conversation_id TEXT PRIMARY KEY,mode TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workspace_project_directories(
          project_id TEXT PRIMARY KEY,path TEXT NOT NULL,created_by_mio INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS workspace_deleted_projects(project_id TEXT PRIMARY KEY,deleted_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workspace_notes(
          id TEXT PRIMARY KEY,project_id TEXT NOT NULL,title TEXT NOT NULL,content TEXT NOT NULL,
          source TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workspace_note_state(
          note_id TEXT PRIMARY KEY,revision INTEGER NOT NULL DEFAULT 1,content_sha256 TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS workspace_templates(
          id TEXT PRIMARY KEY,project_id TEXT NOT NULL DEFAULT '',name TEXT NOT NULL,
          goal TEXT NOT NULL,instructions TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workspace_requests(
          request_key TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,task_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workspace_selections(
          id TEXT PRIMARY KEY,project_id TEXT NOT NULL,task_id TEXT NOT NULL DEFAULT '',
          artifact_id TEXT NOT NULL DEFAULT '',job_id TEXT NOT NULL DEFAULT '',output_index INTEGER NOT NULL DEFAULT 0,
          note TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS workspace_conversations_project ON workspace_conversations(project_id);
        ''')
        # Existing installations predate provenance tracking.  Treat their
        # directories as user-owned until Mio can prove it created them.
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(workspace_project_directories)')}
        if 'created_by_mio' not in columns:
            conn.execute('ALTER TABLE workspace_project_directories ADD COLUMN created_by_mio INTEGER NOT NULL DEFAULT 0')


class WorkspaceUnit:
    def __init__(self, conn):
        self._conn = conn

    def begin_immediate(self):
        self._conn.execute("BEGIN IMMEDIATE")

    def clear_directory(self, parameters):
        return self._conn.execute('DELETE FROM workspace_project_directories WHERE project_id=?', parameters)

    def mark_deleted(self, parameters):
        return self._conn.execute('INSERT INTO workspace_deleted_projects VALUES(?,?)', parameters)

    def archive_project(self, parameters):
        return self._conn.execute('UPDATE workspace_projects SET archived=1,updated_at=? WHERE id=?', parameters)

    def save_conversation_mode(self, parameters):
        return self._conn.execute('INSERT OR REPLACE INTO workspace_conversation_modes VALUES(?,?)', parameters)

    def unbind_conversation(self, parameters):
        return self._conn.execute('DELETE FROM workspace_conversations WHERE conversation_id=?', parameters)

    def bind_conversation(self, parameters):
        return self._conn.execute('INSERT INTO workspace_conversations VALUES(?,?) ON CONFLICT(conversation_id) DO UPDATE SET project_id=excluded.project_id', parameters)

    def update_note(self, parameters):
        return self._conn.execute('UPDATE workspace_notes SET title=?,content=?,source=?,updated_at=? WHERE id=?', parameters)

    def save_note_state(self, parameters):
        return self._conn.execute('INSERT OR REPLACE INTO workspace_note_state VALUES(?,?,?)', parameters)

    def save_template(self, parameters):
        return self._conn.execute('INSERT INTO workspace_templates VALUES(?,?,?,?,?,?,?) ON CONFLICT(id)\n          DO UPDATE SET project_id=excluded.project_id,name=excluded.name,goal=excluded.goal,\n          instructions=excluded.instructions,updated_at=excluded.updated_at', parameters)

    def select_result(self, parameters):
        return self._conn.execute('INSERT OR IGNORE INTO workspace_selections VALUES(?,?,?,?,?,?,?,?)', parameters)

    def save_project(self, parameters):
        return self._conn.execute('INSERT INTO workspace_projects VALUES(?,?,?,?,?,?,?)\n              ON CONFLICT(id) DO UPDATE SET name=excluded.name,description=excluded.description,\n              instructions=excluded.instructions,archived=excluded.archived,updated_at=excluded.updated_at', parameters)

    def clear_code_workspace(self, parameters):
        return self._conn.execute('DELETE FROM code_workspaces WHERE project_id=?', parameters)

    def unbind_project_conversations(self, parameters):
        return self._conn.execute('DELETE FROM workspace_conversations WHERE project_id=?', parameters)

    def disable_project_routines(self, parameters):
        return self._conn.execute("UPDATE workspace_routines SET enabled=0,next_run_at='',updated_at=? WHERE project_id=? AND enabled=1", parameters)

    def insert_note(self, parameters):
        return self._conn.execute('INSERT INTO workspace_notes VALUES(?,?,?,?,?,?,?)', parameters)

    def insert_conversation(self, parameters):
        return self._conn.execute('INSERT INTO agent_conversations(id,title,created_at,updated_at) VALUES(?,?,?,?)', parameters)

    def insert_task(self, parameters):
        return self._conn.execute("INSERT INTO agent_tasks(id,conversation_id,source,status,original_goal,snapshot_json,model_id,reasoning_level,\n                allowed_tools_json,context_json,budget_yuan,created_at,updated_at) VALUES(?,?,?,'ready',?,?,?,?,?,?,?,?,?)", parameters)

    def save_goal_request(self, parameters):
        return self._conn.execute('INSERT INTO workspace_requests VALUES(?,?,?)', parameters)

    def list_projects(self, parameters):
        return self._conn.execute("SELECT p.*,COALESCE(d.path,'') AS directory,COALESCE(d.created_by_mio,0) AS directory_created FROM workspace_projects p LEFT JOIN workspace_project_directories d ON d.project_id=p.id WHERE (p.archived=0 OR ?) AND NOT EXISTS(SELECT 1 FROM workspace_deleted_projects x WHERE x.project_id=p.id) ORDER BY p.updated_at DESC,p.id", parameters)

    def project(self, parameters):
        return self._conn.execute("SELECT p.*,COALESCE(d.path,'') AS directory,COALESCE(d.created_by_mio,0) AS directory_created FROM workspace_projects p LEFT JOIN workspace_project_directories d ON d.project_id=p.id WHERE p.id=?", parameters)

    def save_directory(self, parameters):
        return self._conn.execute('INSERT INTO workspace_project_directories(project_id,path,created_by_mio) VALUES(?,?,?)\n                    ON CONFLICT(project_id) DO UPDATE SET path=excluded.path,created_by_mio=excluded.created_by_mio', parameters)

    def directory_for_deletion(self, parameters):
        return self._conn.execute('SELECT d.path,d.created_by_mio,p.archived\n            FROM workspace_project_directories d JOIN workspace_projects p ON p.id=d.project_id\n            WHERE d.project_id=?', parameters)

    def project_tasks(self, parameters):
        return self._conn.execute("SELECT t.id,t.status FROM agent_tasks t\n            WHERE t.conversation_id IN (SELECT conversation_id FROM workspace_conversations WHERE project_id=?)\n               OR json_extract(t.snapshot_json,'$.project_id')=?", parameters)

    def table_names(self):
        return self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")

    def project_metadata(self, parameters):
        return self._conn.execute('SELECT * FROM workspace_projects WHERE id=?', parameters)

    def is_deleted(self, parameters):
        return self._conn.execute('SELECT 1 FROM workspace_deleted_projects WHERE project_id=?', parameters)

    def conversation_project(self, parameters):
        return self._conn.execute('SELECT project_id FROM workspace_conversations WHERE conversation_id=?', parameters)

    def note(self, parameters):
        return self._conn.execute('SELECT * FROM workspace_notes WHERE id=?', parameters)

    def note_state(self, parameters):
        return self._conn.execute('SELECT * FROM workspace_note_state WHERE note_id=?', parameters)

    def recent_notes(self, parameters):
        return self._conn.execute("SELECT n.*,COALESCE(s.revision,1) AS revision,COALESCE(s.content_sha256,'') AS content_sha256\n          FROM workspace_notes n LEFT JOIN workspace_note_state s ON s.note_id=n.id\n          WHERE n.project_id=? ORDER BY n.updated_at DESC LIMIT 20", parameters)

    def recent_selections(self, parameters):
        return self._conn.execute('SELECT * FROM workspace_selections WHERE project_id=? ORDER BY created_at DESC LIMIT 12', parameters)

    def templates(self, parameters):
        return self._conn.execute("SELECT * FROM workspace_templates WHERE (?='' OR project_id='' OR project_id=?) ORDER BY updated_at DESC", parameters)

    def goal_request(self, parameters):
        return self._conn.execute('SELECT * FROM workspace_requests WHERE request_key=?', parameters)

    def insert_conversation_binding(self, parameters):
        return self._conn.execute('INSERT INTO workspace_conversations VALUES(?,?)', parameters)

    def task_page(self, parameters):
        return self._conn.execute("SELECT t.* FROM agent_tasks t\n            LEFT JOIN workspace_conversations c ON c.conversation_id=t.conversation_id\n            WHERE (?='' OR c.project_id=?) ORDER BY t.updated_at DESC,t.id LIMIT ? OFFSET ?", parameters)

    def job_page(self, parameters):
        return self._conn.execute('SELECT j.* FROM creation_jobs j JOIN workspace_conversations c\n            ON c.conversation_id=j.conversation_id WHERE c.project_id=?\n            ORDER BY j.created_at DESC,j.id LIMIT ? OFFSET ?', parameters)

    def active_terminal(self, parameters):
        return self._conn.execute("SELECT 1 FROM terminal_jobs WHERE project_id=? AND status IN ('running','unknown') LIMIT 1", parameters)

    def conversation_bindings(self):
        return self._conn.execute('SELECT * FROM workspace_conversations')

    def conversation_modes(self):
        return self._conn.execute('SELECT * FROM workspace_conversation_modes')

    def conversation_exists(self, parameters):
        return self._conn.execute('SELECT 1 FROM agent_conversations WHERE id=?', parameters)

    def active_project(self, parameters):
        return self._conn.execute('SELECT 1 FROM workspace_projects WHERE id=? AND archived=0', parameters)

    def project_directory(self, parameters):
        return self._conn.execute('SELECT path,created_by_mio FROM workspace_project_directories WHERE project_id=?', parameters)

    def has_active_producer(self, project_id, tables):
        for table, condition, states in (
            ('terminal_jobs', 'project_id=?', ('running', 'unknown')),
            ('creation_jobs', 'conversation_id IN (SELECT conversation_id FROM workspace_conversations WHERE project_id=?)',
             ('created', 'submitted', 'queued', 'running', 'cancel_requested', 'unknown')),
            ('creative_batches', 'conversation_id IN (SELECT conversation_id FROM workspace_conversations WHERE project_id=?)',
             ('queued', 'running')),
            ('media_assemblies', "task_id IN (SELECT id FROM agent_tasks WHERE json_extract(snapshot_json,'$.project_id')=? OR conversation_id IN (SELECT conversation_id FROM workspace_conversations WHERE project_id=?))",
             ('queued', 'running')),
        ):
            if table not in tables:
                continue
            parameters = (project_id, project_id) if table == 'media_assemblies' else (project_id,)
            marks = ','.join('?' for _ in states)
            if self._conn.execute(f'SELECT 1 FROM {table} WHERE {condition} AND status IN ({marks}) LIMIT 1',
                                  (*parameters, *states)).fetchone():
                return True
        return False

    def collection_page(self, kind, parameters):
        if kind in {'notes', 'selections', 'documents'}:
            table, sort = {'notes': ('workspace_notes', 'updated_at'),
                'selections': ('workspace_selections', 'created_at'), 'documents': ('knowledge_documents', 'indexed_at')}[kind]
            sql = ('''SELECT n.*,COALESCE(s.revision,1) AS revision,COALESCE(s.content_sha256,'') AS content_sha256
                      FROM workspace_notes n LEFT JOIN workspace_note_state s ON s.note_id=n.id
                      WHERE n.project_id=? ORDER BY n.updated_at DESC,n.id LIMIT ? OFFSET ?'''
                   if kind == 'notes' else
                   '''SELECT d.*,COALESCE(o.status,'not_needed') AS ocr_status,
                      COALESCE(o.ocr_used,0) AS ocr_used,COALESCE(o.error,'') AS ocr_error
                      FROM knowledge_documents d LEFT JOIN knowledge_ocr_state o
                      ON o.project_id=d.project_id AND o.path=d.path
                      WHERE d.project_id=? ORDER BY d.indexed_at DESC,d.id LIMIT ? OFFSET ?'''
                   if kind == 'documents' else
                   f'SELECT * FROM {table} WHERE project_id=? ORDER BY {sort} DESC,id LIMIT ? OFFSET ?')
        elif kind == 'batches':
            sql = '''SELECT b.id FROM creative_batches b JOIN workspace_conversations c ON c.conversation_id=b.conversation_id
            WHERE c.project_id=? ORDER BY b.created_at DESC,b.id LIMIT ? OFFSET ?'''
        elif kind == 'assemblies':
            sql = '''SELECT m.id FROM media_assemblies m JOIN agent_tasks t ON t.id=m.task_id
            JOIN workspace_conversations c ON c.conversation_id=t.conversation_id
            WHERE c.project_id=? ORDER BY m.created_at DESC,m.id LIMIT ? OFFSET ?'''
        else:
            raise ValueError('Unsupported project collection')
        return self._conn.execute(sql, parameters).fetchall()
