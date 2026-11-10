"""Verify the vendored frozen strategy without credentials, network, or exchange SDKs.

Ported from Autonomous-Trading-Bot-Crypto@68af4eb scripts/check_strategy_freeze.py.
The original script only called verify_strategy_freeze(). This port also proves that:

1. every vendored file still matches vendor/anchorium_strategy/SHA256SUMS
   (byte-identical to the source commit);
2. the source manifest is the verbatim original (its SHA256 is pinned in the
   scoped manifest);
3. every entry in the scoped manifest equals the source manifest entry, and every
   source entry that is not checked here is listed as excluded with its original
   value, so nothing is dropped silently;
4. the vendored, unchanged verify_strategy_freeze() passes.

Exit code 0 means all checks passed; any failure prints JSON and exits 1.
"""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'vendor' / 'anchorium_strategy'


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_sums():
    failures = []
    lines = (VENDOR / 'SHA256SUMS').read_text().splitlines()
    for line in lines:
        expected, relative = line.split(maxsplit=1)
        path = VENDOR / relative
        if not path.is_file() or sha256(path) != expected:
            failures.append('VENDORED_FILE_CHANGED:' + relative)
    return len(lines), failures


def check_scope():
    failures = []
    scoped = json.loads((VENDOR / 'docs/strategy_freeze.json').read_text())
    source_path = VENDOR / scoped['source_manifest']
    if sha256(source_path) != scoped['source_manifest_sha256']:
        failures.append('SOURCE_MANIFEST_CHANGED')
    source = json.loads(source_path.read_text())
    for key in ('baseline', 'baseline_commit', 'input_convention', 'strategy_exceptions'):
        if scoped[key] != source[key]:
            failures.append('SCOPED_FIELD_DIFFERS:' + key)
    for relative, expected in scoped['files'].items():
        if source['files'].get(relative) != expected:
            failures.append('SCOPED_FILE_DIFFERS:' + relative)
    if set(scoped['files']) != set(source['files']):
        failures.append('SOURCE_FILE_NOT_CHECKED')
    if scoped['methods']:
        failures.append('UNEXPECTED_VENDORED_METHODS')
    if scoped['excluded_methods'] != source['methods']:
        failures.append('EXCLUDED_METHODS_DIFFER_FROM_SOURCE')
    merged = {**scoped['parameters'], **scoped['excluded_parameters']}
    if merged != source['parameters'] or set(scoped['parameters']) & set(scoped['excluded_parameters']):
        failures.append('PARAMETERS_DIFFER_FROM_SOURCE')
    for relative in list(scoped['excluded_methods']) + list(scoped['excluded_parameters']):
        if (VENDOR / relative).exists():
            failures.append('EXCLUDED_MODULE_PRESENT:' + relative)
    counts = {
        'source_parameter_count': sum(map(len, source['parameters'].values())),
        'source_method_count': sum(map(len, source['methods'].values())),
        'excluded_parameter_count': sum(map(len, scoped['excluded_parameters'].values())),
        'excluded_method_count': sum(map(len, scoped['excluded_methods'].values())),
    }
    return counts, failures


def main():
    file_count, failures = check_sums()
    counts, scope_failures = check_scope()
    failures += scope_failures
    result = {'vendored_files_verified': file_count, **counts}
    if not failures:
        sys.path.insert(0, str(VENDOR / 'algo-trading-bot'))
        from strategy_freeze import verify_strategy_freeze
        try:
            result['vendored_verify_strategy_freeze'] = verify_strategy_freeze(VENDOR)
        except Exception as exc:  # noqa: BLE001 - report the exact freeze error
            failures.append(f'{type(exc).__name__}:{exc}')
    result['failures'] = failures
    result['passed'] = not failures
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
