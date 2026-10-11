"""Restricted data-only Python worker. A separate process is not an OS sandbox.

No imports, file handles, environment, network, reflection or host objects are
exposed to extension code. The host materializes authorized input separately.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import sys

ALLOWED_NODES = {
    'Module', 'FunctionDef', 'arguments', 'arg', 'Return', 'Assign', 'AugAssign',
    'Expr', 'If', 'For', 'While', 'Break', 'Continue', 'Pass', 'Load', 'Store',
    'Name', 'Constant', 'List', 'Tuple', 'Dict', 'Set', 'Subscript', 'Slice',
    'BinOp', 'UnaryOp', 'BoolOp', 'Compare', 'IfExp', 'Call', 'keyword',
    'Attribute', 'ListComp', 'DictComp', 'SetComp', 'GeneratorExp', 'comprehension',
    'JoinedStr', 'FormattedValue', 'Add', 'Sub', 'Mult', 'Div', 'FloorDiv', 'Mod',
    'Not', 'USub', 'UAdd', 'And', 'Or', 'Eq', 'NotEq', 'Lt', 'LtE', 'Gt', 'GtE',
    'In', 'NotIn', 'Is', 'IsNot',
}
METHODS = {'get', 'keys', 'values', 'items', 'append', 'extend', 'count', 'index',
           'strip', 'lstrip', 'rstrip', 'lower', 'upper', 'title', 'capitalize',
           'split', 'splitlines', 'join', 'replace', 'startswith', 'endswith',
           'isdigit', 'isalpha', 'isalnum', 'removeprefix', 'removesuffix', 'sort',
           'reverse', 'copy'}
SAFE_BUILTINS = {name: getattr(__import__('builtins'), name) for name in (
    'len', 'str', 'int', 'float', 'bool', 'list', 'dict', 'tuple', 'set', 'range',
    'enumerate', 'zip', 'sum', 'min', 'max', 'sorted', 'abs', 'round', 'all', 'any',
    'reversed')}


def validate_source(source):
    if not isinstance(source, str) or not source.strip() or len(source) > 100000:
        raise ValueError('Python 工具需要不超过 10 万字符的正文。')
    tree = ast.parse(source, filename='<extension>', mode='exec')
    if len(list(ast.walk(tree))) > 10000:
        raise ValueError('扩展代码结构过大。')
    # Only function definitions at module level, so module execution has no effects.
    if not tree.body or any(not isinstance(node, ast.FunctionDef) for node in tree.body):
        raise ValueError('Python 工具顶层仅允许函数定义。')
    handlers = [node for node in tree.body if node.name == 'handle']
    if len(handlers) != 1 or len(handlers[0].args.args) != 1:
        raise ValueError('入口必须是 def handle(arguments)，且只有一个参数。')
    for node in ast.walk(tree):
        if type(node).__name__ not in ALLOWED_NODES:
            raise ValueError('扩展只支持受限数据处理语法：' + type(node).__name__)
        if isinstance(node, (ast.FunctionDef, ast.arg, ast.Name)):
            name = node.name if isinstance(node, ast.FunctionDef) else node.arg if isinstance(node, ast.arg) else node.id
            if name.startswith('_'):
                raise ValueError('扩展不能访问内部名称。')
        if isinstance(node, ast.FunctionDef) and (node.decorator_list or node.returns or node.args.defaults or node.args.kw_defaults or node.args.kwarg or node.args.vararg or node.args.kwonlyargs):
            raise ValueError('扩展函数不支持装饰器、注解或默认/可变参数。')
        if isinstance(node, ast.arg) and node.annotation:
            raise ValueError('扩展函数不支持注解。')
        if isinstance(node, ast.Attribute) and (node.attr not in METHODS or not isinstance(node.ctx, ast.Load)):
            raise ValueError('扩展不能访问系统对象或内部属性。')
    return tree


def evaluate(source, arguments):
    tree = validate_source(source)
    namespace = {'__builtins__': SAFE_BUILTINS.copy()}
    exec(compile(tree, '<extension>', 'exec'), namespace)
    result = namespace['handle'](arguments)
    if not isinstance(result, dict):
        raise ValueError('扩展结果必须是 JSON 对象。')
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    if len(encoded) > 2 * 1024 * 1024:
        raise ValueError('扩展结果超过 2 MB。')
    return result


def main():
    response_path = None
    try:
        if '--extension-request' in sys.argv and '--extension-response' in sys.argv:
            request_path=Path(sys.argv[sys.argv.index('--extension-request')+1]).resolve()
            response_path=Path(sys.argv[sys.argv.index('--extension-response')+1]).resolve()
            if request_path.name!='request.json' or response_path.name!='response.json' or request_path.parent!=response_path.parent:
                raise ValueError('扩展通信路径无效。')
            if request_path.stat().st_size>4*1024*1024:
                raise ValueError('扩展输入过大。')
            raw=request_path.read_bytes()
        else:
            raw = sys.stdin.buffer.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError('扩展输入过大。')
        request = json.loads(raw.decode('utf-8'))
        result = {'ok': True, 'result': evaluate(request['source'], request['arguments'])}
    except Exception as exc:
        result = {'ok': False, 'error': str(exc)[:1000]}
    payload=json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if response_path is not None:
        with response_path.open('xb') as stream:
            stream.write(payload)
    else:
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()


if __name__ == '__main__':
    main()
