"""Check public source files without printing potential secret values."""
from pathlib import Path
import argparse
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DEMO = {f'examples/synthetic_pp_gf/synthetic-{stage}.csv' for stage in ('H1', 'C1', 'H2', 'C2')}
EXCLUDED = {'.git', '__pycache__', '.pytest_cache', '.venv', 'venv', 'outputs', 'data', 'private_data', 'build', 'dist'}
DATA_EXTENSIONS = {'.xlsx', '.xls', '.xlsm', '.pdf', '.png', '.jpg', '.jpeg', '.ngb-sdh', '.csv', '.tsv', '.dat',
                   '.log', '.tmp', '.bak', '.zip', '.pyc', '.pem', '.key', '.pfx', '.p12', '.json'}
RULES = {
    'absolute_local_path': re.compile(r'(?i)(?:\b[A-Z]:[\\/][\w ]|/(?:Users|home)/[^\s/]+/)'),
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'access_token': re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16})'),
    'credential_value': re.compile(r'''(?i)(?:password|api[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=]\s*["'][^"'\s]{8,}["']'''),
    'credential_in_url': re.compile(r'https?://[^\s/:]+:[^\s/@]+@'),
    'email_address': re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
}


def inspect_file(name, data):
    issues = []
    path = Path(name)
    if path.suffix.lower() in DATA_EXTENSIONS and name not in DEMO:
        issues.append((name, 0, 'non_public_file_type'))
    if path.name.startswith('.env') or path.name.startswith('~$') or name == '.streamlit/secrets.toml':
        issues.append((name, 0, 'private_configuration_or_temporary_file'))
    try:
        content = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return issues + [(name, 0, 'unexpected_binary_file')]
    for line_no, line in enumerate(content.splitlines(), 1):
        for label, pattern in RULES.items():
            if pattern.search(line):
                issues.append((name, line_no, label))
    return issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--staged', action='store_true', help='Inspect actual staged blob contents before commit')
    mode.add_argument('--tracked', action='store_true', help='Inspect files in HEAD')
    args = parser.parse_args()
    blobs = {}
    if args.staged or args.tracked:
        def git(*command):
            return subprocess.check_output(['git', *command], cwd=ROOT)
        if args.staged:
            names = git('diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z')
        else:
            names = git('ls-tree', '-r', '--name-only', '-z', 'HEAD')
        for raw in names.split(b'\0'):
            if raw:
                name = raw.decode('utf-8')
                blobs[name] = git('show', (':' if args.staged else 'HEAD:') + name)
    else:
        for path in sorted(ROOT.rglob('*')):
            relative = path.relative_to(ROOT)
            if path.is_file() and not EXCLUDED.intersection(relative.parts) and not any(part.endswith('.egg-info') for part in relative.parts):
                blobs[relative.as_posix()] = path.read_bytes()
    issues = [(name, 0, 'forbidden_private_directory') for name in blobs if EXCLUDED.intersection(Path(name).parts)]
    for name, data in blobs.items():
        issues.extend(inspect_file(name, data))
    present_demo = DEMO.intersection(blobs)
    if not args.staged and present_demo != DEMO:
        issues.append(('examples/synthetic_pp_gf', 0, 'exactly_four_demo_files_required'))
    for name, line_no, rule in issues:
        print(f'{name}:{line_no}: {rule}')
    print(f'Inspected {len(blobs)} public files; {len(issues)} finding(s); {len(present_demo)} synthetic CSVs.')
    return 1 if issues else 0


if __name__ == '__main__':
    sys.exit(main())
