"""Verify the strategy source, management methods and parameter baseline."""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT/'docs/strategy_freeze.json'


class StrategyFreezeError(RuntimeError):
    pass


def method_hash(source, qualified_name):
    nodes = ast.parse(source).body
    for name in qualified_name.split('.'):
        node = next((item for item in nodes if isinstance(item, (ast.ClassDef, ast.FunctionDef,
                    ast.AsyncFunctionDef)) and item.name == name), None)
        if node is None:
            raise StrategyFreezeError('FROZEN_METHOD_MISSING:' + qualified_name)
        nodes = getattr(node, 'body', [])
    return hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()


def literal_parameters(source, names):
    result = {}
    bindings = {}
    def resolve(node):
        if isinstance(node, ast.Name):
            if node.id not in bindings:
                raise ValueError('Unknown constant')
            return bindings[node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            return [resolve(item) for item in node.elts]
        return ast.literal_eval(node)

    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    try:
                        value = resolve(node.value)
                    except (ValueError, TypeError) as exc:
                        if target.id in names:
                            raise StrategyFreezeError('FROZEN_PARAMETER_NOT_LITERAL:' + target.id) from exc
                        continue
                    bindings[target.id] = value
                    if target.id in names:
                        result[target.id] = value
    if result.keys() != set(names):
        raise StrategyFreezeError('FROZEN_PARAMETER_MISSING')
    return result


def verify_strategy_freeze(root=ROOT):
    manifest = json.loads((root/'docs/strategy_freeze.json').read_text())
    failures = []
    for relative, expected in manifest['files'].items():
        if hashlib.sha256((root/relative).read_bytes()).hexdigest() != expected:
            failures.append(relative)
    for relative, methods in manifest['methods'].items():
        source = (root/relative).read_text()
        for name, expected in methods.items():
            if method_hash(source, name) != expected:
                failures.append(relative + ':' + name)
    for relative, expected in manifest['parameters'].items():
        if literal_parameters((root/relative).read_text(), expected.keys()) != expected:
            failures.append(relative + ':strategy_parameters')
    if failures:
        raise StrategyFreezeError('STRATEGY_FREEZE_VIOLATION:' + ','.join(failures))
    return {'baseline': manifest['baseline'], 'file_count': len(manifest['files']),
            'method_count': sum(map(len, manifest['methods'].values())),
            'parameter_count': sum(map(len, manifest['parameters'].values())), 'passed': True}
