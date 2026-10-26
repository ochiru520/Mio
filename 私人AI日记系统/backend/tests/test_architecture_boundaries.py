"""Layer checks against small independent examples, without loading Mio."""
from pathlib import Path
import tempfile
import unittest

from scripts.check_architecture import inspect_source, inspect_tree, violations


class ArchitectureBoundariesTests(unittest.TestCase):
    def test_nested_endpoint_import_is_rejected(self):
        findings = inspect_source('def action():\n from .chat import run\n return run()\n', 'routes/demo.py')
        self.assertEqual([f.rule for f in violations(findings, {})], ['route-layer'])

    def test_registration_does_not_allow_endpoint_calls(self):
        source = 'from . import tasks\nrouter.include_router(tasks.router)\n'
        self.assertFalse(inspect_source(source, 'routes/demo.py'))
        self.assertEqual(inspect_source(source + 'tasks.run()\n', 'routes/demo.py')[0].rule, 'route-layer')
        self.assertFalse(inspect_source('from .tasks import router as tasks_router\nrouter.include_router(tasks_router)\n', 'routes/demo.py'))
        for source in ('from .chat import run\nrouter.include_router(run)\n',
                       'from .. import routes\nroutes.chat.run()\n',
                       'import app.routes as routes\nroutes.chat.run()\n'):
            with self.subTest(source=source):
                self.assertEqual(inspect_source(source, 'routes/demo.py')[0].rule, 'route-layer')

    def test_repository_cannot_import_network_or_business(self):
        source = 'import httpx\nfrom ..agent_task_service import submit\n'
        self.assertEqual(len(inspect_source(source, 'repositories/demo_repository.py')), 2)
        self.assertFalse(inspect_source('from typing import Callable\nimport sqlite3\n', 'repositories/demo_repository.py'))

    def test_sql_in_new_service_or_extracted_service_has_no_exemption(self):
        source = 'def get(conn):\n return conn.execute("SELECT * FROM tasks")\n'
        findings = inspect_source(source, 'demo_service.py')
        self.assertEqual(len(violations(findings, {})), 1)
        self.assertEqual(len(violations(findings, {'sql-layer': {'demo_service.py': 1}})), 1)
        for path in ('agent_skill_service.py', 'agent_context_service.py'):
            with self.subTest(path=path):
                strict = inspect_source(source, path)
                self.assertEqual(len(violations(strict, {'sql-layer': {path: 99}})), 1)
        self.assertEqual(len(violations(findings + findings, {'sql-layer': {'demo_service.py': 1}})), 2)

    def test_helpers_and_connection_wrappers_have_no_sql_exemption(self):
        for path in ('model_runtime.py', 'context_tools.py', 'routes/demo.py'):
            with self.subTest(path=path):
                findings = inspect_source('db.get_conn().execute("SELECT * FROM tasks")\n', path)
                self.assertEqual({f.rule for f in findings}, {'sql-layer', 'storage-connection'})
        self.assertEqual(inspect_source('sqlite3.connect(path)\n', 'helper.py')[0].rule, 'storage-connection')

    def test_only_connection_factory_pragmas_are_allowed_in_db_facade(self):
        source = ('def get_conn():\n conn = sqlite3.connect(path)\n'
                  ' conn.execute("PRAGMA busy_timeout = 15000")\n'
                  ' conn.execute("PRAGMA foreign_keys = ON")\n return conn\n')
        self.assertFalse(inspect_source(source, 'db.py'))
        added = source.replace(' return conn', ' conn.execute("SELECT * FROM secrets")\n return conn')
        self.assertEqual(inspect_source(added, 'db.py')[0].rule, 'sql-layer')
        self.assertEqual(inspect_source('conn.execute("PRAGMA foreign_keys = ON")\n', 'db.py')[0].rule, 'sql-layer')

    def test_sql_whitespace_comments_and_keywords_are_checked(self):
        queries = ('SELECT\n* FROM tasks', 'SELECT\t* FROM tasks', '-- comment\nSELECT * FROM tasks',
                   '/* header */ SELECT * FROM tasks')
        for query in queries:
            for call in (f'conn.execute({query!r})', f'conn.execute(sql={query!r})'):
                with self.subTest(call=call):
                    self.assertEqual(inspect_source(call, 'agent_task_service.py')[0].rule, 'sql-layer')

    def test_route_dependencies_have_no_budget_exemption(self):
        debt = {'route-layer': {'routes/day.py:app.routes.chat.analyze': 1}}
        original = inspect_source('from .chat import analyze\nanalyze()\n', 'routes/day.py')
        self.assertEqual(len(violations(original, debt)), 1)
        renamed = inspect_source('from .chat import execute\nexecute()\n', 'routes/day.py')
        self.assertEqual(len(violations(renamed, debt)), 1)

    def test_services_and_helpers_cannot_call_routes(self):
        for path in ('daily_diary_service.py', 'helper.py', 'main.py'):
            with self.subTest(path=path):
                findings = inspect_source('from .routes.diary import generate\ngenerate()\n', path)
                self.assertEqual([item.rule for item in violations(findings, {})], ['route-layer'])
                registration = 'from .routes.diary import router\napp.include_router(router)\n'
                self.assertFalse(inspect_source(registration, path))

    def test_analysis_never_imports_or_runs_target_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'demo_service.py').write_text('raise RuntimeError("must not execute")\n', encoding='utf-8')
            self.assertFalse(inspect_tree(root))
            (root / 'broken.py').write_text('def broken(\n', encoding='utf-8')
            self.assertEqual(inspect_tree(root)[0].rule, 'syntax')
        self.assertEqual(inspect_source('return 1\n', 'invalid.py')[0].rule, 'syntax')


if __name__ == '__main__':
    unittest.main()
