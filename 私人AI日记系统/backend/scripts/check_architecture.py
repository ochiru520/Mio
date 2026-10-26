"""Read-only syntax and dependency checks; never import the application.

Business SQL and direct database access belong exclusively to repositories.
Router registration is allowed, calling another router's endpoint is not.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import re


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    detail: str


def import_targets(node: ast.AST, module: str) -> list[tuple[str, str]]:
    if isinstance(node, ast.Import):
        return [(item.name, item.asname or item.name.split('.')[0]) for item in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    package = module.split('.')[:-1]
    if node.level:
        base = package[:len(package) - node.level + 1]
        if node.module:
            base += node.module.split('.')
    else:
        base = (node.module or '').split('.')
    return [('.'.join([*base, item.name]), item.asname or item.name) for item in node.names]


def router_registration_only(tree: ast.AST, name: str, *, direct_router: bool = False) -> bool:
    """Only allow a sibling router import as include_router's first argument."""
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    uses = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == name]
    for use in uses:
        argument = parents.get(use)
        if isinstance(argument, ast.Attribute) and argument.attr == 'router':
            call = parents.get(argument)
        else:
            if not direct_router:
                return False
            argument, call = use, parents.get(use)
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == 'include_router' and call.args and call.args[0] is argument):
            return False
    return bool(uses)


def sql_argument(node: ast.AST) -> bool:
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in {'execute', 'executemany', 'executescript'}):
        return False
    # A variable query is also storage work. Literal non-SQL calls with the same
    # method name do not count (e.g. a command executor's execute('echo ...')).
    argument = node.args[0] if node.args else next(
        (keyword.value for keyword in node.keywords if keyword.arg in {'sql', 'sql_script'}), None
    )
    if argument is None:
        return False
    if isinstance(argument, ast.JoinedStr):
        text = ''.join(part.value for part in argument.values if isinstance(part, ast.Constant))
    elif isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        text = argument.value
    else:
        return True
    text = re.sub(r'/\*.*?\*/|--[^\r\n]*', ' ', text, flags=re.S)
    words = text.split()
    return bool(words) and words[0].upper() in {
        'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER', 'DROP',
        'PRAGMA', 'BEGIN', 'WITH', 'REPLACE', 'VACUUM', 'SAVEPOINT',
    }


def inspect_source(source: str, path: str) -> list[Finding]:
    try:
        tree = ast.parse(source, filename=path)
        compile(tree, path, 'exec')  # Validate semantics such as top-level return; do not execute.
    except SyntaxError as error:
        return [Finding(path, error.lineno or 1, 'syntax', error.msg)]
    module = 'app.' + path.removesuffix('.py').replace('/', '.')
    findings = []
    # The facade owns the connection factory, including these two connection
    # settings. This is not a whole-file SQL exemption.
    connection_factory = set()
    if path == 'db.py':
        for item in tree.body:
            if isinstance(item, ast.FunctionDef) and item.name == 'get_conn':
                connection_factory.update(ast.walk(item))
    for node in ast.walk(tree):
        if not path.startswith('repositories/'):
            if sql_argument(node):
                argument = node.args[0] if node.args else None
                factory_setting = (node in connection_factory and isinstance(argument, ast.Constant)
                                   and argument.value in {'PRAGMA busy_timeout = 15000', 'PRAGMA foreign_keys = ON'})
                if not factory_setting:
                    findings.append(Finding(path, node.lineno, 'sql-layer', 'SQL belongs in repositories'))
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and (node.func.attr == 'get_conn' or (node.func.attr == 'connect'
                         and isinstance(node.func.value, ast.Name) and node.func.value.id == 'sqlite3'))
                    and node not in connection_factory):
                findings.append(Finding(path, node.lineno, 'storage-connection', 'Database access belongs in repositories'))
        for target, name in import_targets(node, module):
            if target == 'app.routes' or target.startswith('app.routes.'):
                direct_router = target.endswith('.router')
                route_module = len(target.split('.')) == 3
                if not ((direct_router or route_module) and router_registration_only(tree, name, direct_router=direct_router)):
                    findings.append(Finding(path, node.lineno, 'route-layer', target))
            if path.startswith('repositories/'):
                parts = target.split('.')
                is_business = target == 'app.routes' or target.startswith('app.routes.') or any(
                    part.endswith('_service') for part in parts
                ) or target in {'app.llm', 'app.tool_registry', 'app.main'}
                is_io = parts[0] in {'requests', 'httpx', 'urllib', 'subprocess', 'webview', 'pyautogui'}
                if is_business or is_io:
                    findings.append(Finding(path, node.lineno, 'repository-layer', target))
    return findings


def inspect_tree(root: Path) -> list[Finding]:
    return [finding for path in sorted(root.rglob('*.py'))
            for finding in inspect_source(path.read_text(encoding='utf-8-sig'), path.relative_to(root).as_posix())]


def violations(findings: list[Finding], budgets: dict[str, dict[str, int]]) -> list[Finding]:
    """Architecture violations cannot be exempted by legacy budgets."""
    return list(findings)


def main() -> int:
    parser = argparse.ArgumentParser(description='检查 Python 语法、路由依赖和存储边界（不启动应用）。')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1] / 'app')
    parser.add_argument('--budget', type=Path, default=Path(__file__).with_name('architecture_debt.json'))
    args = parser.parse_args()
    budget = json.loads(args.budget.read_text(encoding='utf-8-sig'))
    findings = inspect_tree(args.root)
    errors = violations(findings, budget.get('limits', {}))
    for error in errors:
        print(f'{error.path}:{error.line}: [{error.rule}] {error.detail}')
    counts = Counter(finding.rule for finding in findings)
    print(f'Architecture: {len(errors)} errors; business SQL sites: {counts["sql-layer"]}')
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
