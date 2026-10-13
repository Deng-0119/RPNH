"""Run exact upstream assertions with only /app output-path relocation.

This is an explicitly adapted scorer, not the full upstream test.sh environment.
It neither solves the task nor installs dependencies. The unchanged original
test file is hash-checked and preserved; all test functions are executed.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path
import traceback

SOURCE_SHA256 = 'a070888930cf829aeca039771260b1386bda990ea805f313468d381ba3608ca5'
OUTPUTS = ('merged_users.parquet', 'conflicts.json')


def verify(source: Path, output_dir: Path):
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError('original verifier bytes differ from fixed upstream')
    outputs = output_dir.resolve()
    changes = []
    class PathsOnly(ast.NodeTransformer):
        def visit_Constant(self, node):
            if isinstance(node.value, str) and node.value in {'/app/' + name for name in OUTPUTS}:
                replacement = str(outputs / node.value.rsplit('/', 1)[1])
                changes.append({'line': node.lineno, 'before': node.value, 'after': replacement})
                return ast.copy_location(ast.Constant(value=replacement), node)
            return node
    tree = PathsOnly().visit(ast.parse(raw.decode(), filename=str(source)))
    if len(changes) != 4:
        raise ValueError('unexpected verifier path layout')
    namespace = {'__name__': 'fixed_upstream_verifier', '__file__': str(source)}
    exec(compile(ast.fix_missing_locations(tree), str(source), 'exec'), namespace)
    tests = [name for name in namespace if name.startswith('test_') and callable(namespace[name])]
    if set(tests) != {'test_output_files_exist', 'test_merged_data_exact_values', 'test_conflict_report_values'}:
        raise ValueError('unexpected original verifier test inventory')
    results = []
    for name in tests:
        try:
            namespace[name]()
            results.append({'test': name, 'status': 'passed'})
        except Exception as exc:
            results.append({'test': name, 'status': 'failed', 'error_type': type(exc).__name__,
                            'error': str(exc), 'traceback': traceback.format_exc()})
    return {'verifier_sha256': SOURCE_SHA256, 'upstream_commit': '2fd12b88aafdd04a52c298e3940bcb189f9766d6',
            'path_only_adaptation': changes, 'tests': results, 'denominator': 3,
            'passed': sum(row['status'] == 'passed' for row in results),
            'original_test_sh_executed': False, 'grader_visibility': 'visible',
            'outputs': [{'path': str(outputs / name), 'sha256': hashlib.sha256((outputs / name).read_bytes()).hexdigest()
                         if (outputs / name).is_file() else None} for name in OUTPUTS]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verifier', type=Path, required=True)
    parser.add_argument('--outputs', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.verifier, args.outputs)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] == result['denominator'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
