"""Read SMTP config from stdin on the project server; never print secrets or enable sending."""
import json
import os
from pathlib import Path
import re
import sys
import tempfile


def update_smtp(path, data):
    allowed = {'SMTP_HOST', 'SMTP_PORT', 'SMTP_MODE', 'SMTP_FORCE_LOGIN',
               'SMTP_USERNAME', 'SMTP_PASSWORD', 'SMTP_FROM'}
    if set(data) != allowed:
        raise ValueError('Exactly the SMTP fields must be supplied')
    data = {key: str(value) for key, value in data.items()}
    if any(not value or re.search(r'[\r\n\x00]', value) for value in data.values()):
        raise ValueError('SMTP values must be nonempty single lines')
    if data['SMTP_MODE'] not in ('ssl', 'starttls') or data['SMTP_FORCE_LOGIN'] not in ('true', 'false'):
        raise ValueError('Verified TLS and a valid AUTH LOGIN option are required')
    if not 1 <= int(data['SMTP_PORT']) <= 65535:
        raise ValueError('Invalid SMTP port')
    original = path.read_text(encoding='utf-8').splitlines()
    current = dict(line.split('=', 1) for line in original if '=' in line and not line.startswith('#'))
    if current.get('REAL_SEND_ENABLED', '').strip().lower() != 'false':
        raise ValueError('SMTP setup requires real sending to remain disabled')
    # dotenv interpolation can otherwise expand a literal ${...} inside credentials.
    if any('$' in value or "'" in value or '\\' in value for value in data.values()):
        raise ValueError('Unsupported dotenv characters; configure via a secret store instead')
    lines = [line for line in original if line.split('=',1)[0] not in allowed]
    # Values are restricted above so dotenv can parse them without quoting.
    lines.extend(f"{key}={value}" for key, value in sorted(data.items()))
    fd, temp = tempfile.mkstemp(prefix='.smtp-env-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write('\n'.join(lines) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


if __name__ == '__main__':
    try:
        update_smtp(Path('/home/admin/huoke-staging/.env'), json.load(sys.stdin))
    except Exception as exc:
        print(json.dumps({'updated': False, 'error': type(exc).__name__}))
        raise SystemExit(1)
    print(json.dumps({'updated': True, 'real_send_enabled': False}))
