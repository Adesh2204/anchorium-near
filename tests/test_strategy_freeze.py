"""The ported freeze check passes on the vendored tree and fails on any tampering."""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = 'vendor/anchorium_strategy/algo-trading-bot/config.py'


def run_check(root):
    proc = subprocess.run([sys.executable, str(root / 'scripts/check_strategy_freeze.py')],
                          capture_output=True, text=True)
    return proc.returncode, json.loads(proc.stdout)


def copy_tree(tmp_path):
    shutil.copytree(ROOT / 'scripts', tmp_path / 'scripts')
    shutil.copytree(ROOT / 'vendor', tmp_path / 'vendor',
                    ignore=shutil.ignore_patterns('__pycache__'))
    return tmp_path


def regenerate_sums(vendor):
    lines = []
    for line in (vendor / 'SHA256SUMS').read_text().splitlines():
        _, rel = line.split(maxsplit=1)
        lines.append(hashlib.sha256((vendor / rel).read_bytes()).hexdigest() + '  ' + rel)
    (vendor / 'SHA256SUMS').write_text('\n'.join(lines) + '\n')


def test_vendored_strategy_passes():
    code, result = run_check(ROOT)
    assert code == 0 and result['passed'] is True
    assert result['vendored_files_verified'] == 13
    assert result['source_parameter_count'] == 32 and result['source_method_count'] == 8
    assert result['excluded_parameter_count'] == 4 and result['excluded_method_count'] == 8
    assert result['vendored_verify_strategy_freeze']['parameter_count'] == 28
    assert result['vendored_verify_strategy_freeze']['file_count'] == 1


def test_one_byte_change_in_signal_brain_fails(tmp_path):
    root = copy_tree(tmp_path)
    brain = root / 'vendor/anchorium_strategy/algo-trading-bot/signal_brain.py'
    brain.write_bytes(brain.read_bytes() + b' ')
    code, result = run_check(root)
    assert code == 1
    assert 'VENDORED_FILE_CHANGED:algo-trading-bot/signal_brain.py' in result['failures']


def test_parameter_change_fails_even_if_sums_are_regenerated(tmp_path):
    root = copy_tree(tmp_path)
    config = root / CONFIG
    text = config.read_text()
    assert 'EMA_FAST = 9' in text
    config.write_text(text.replace('EMA_FAST = 9', 'EMA_FAST = 8', 1))
    # Rewriting SHA256SUMS too does not help: the frozen parameter values still differ.
    regenerate_sums(root / 'vendor/anchorium_strategy')
    code, result = run_check(root)
    assert code == 1
    assert any('STRATEGY_FREEZE_VIOLATION' in f for f in result['failures'])


def test_dropping_an_excluded_entry_fails(tmp_path):
    root = copy_tree(tmp_path)
    manifest = root / 'vendor/anchorium_strategy/docs/strategy_freeze.json'
    data = json.loads(manifest.read_text())
    data['excluded_methods'].popitem()
    manifest.write_text(json.dumps(data))
    code, result = run_check(root)
    assert code == 1
    assert 'EXCLUDED_METHODS_DIFFER_FROM_SOURCE' in result['failures']


def test_editing_the_source_manifest_fails(tmp_path):
    root = copy_tree(tmp_path)
    source = root / 'vendor/anchorium_strategy/docs/strategy_freeze.source.json'
    source.write_text(source.read_text().replace('"EMA_FAST": 9', '"EMA_FAST": 8'))
    code, result = run_check(root)
    assert code == 1
    assert 'SOURCE_MANIFEST_CHANGED' in result['failures']
