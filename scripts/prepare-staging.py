"""Run on the dedicated server. Creates secrets once; never prints credentials."""
from pathlib import Path
import secrets
import os

root = Path('/home/admin/huoke-staging')
root.mkdir(mode=0o700, exist_ok=True)
target = root / '.env'
if target.exists():
    print('Existing environment retained.')
else:
    db, operator, reviewer = (secrets.token_urlsafe(36) for _ in range(3))
    lines = [f'DB_PASSWORD={db}', f'DATABASE_URL=postgresql+psycopg://huoke:{db}@postgres:5432/huoke',
        'REDIS_URL=redis://redis:6379/0', f'OPERATOR_TOKEN={operator}', f'REVIEWER_TOKEN={reviewer}',
        'REAL_SEND_ENABLED=false', 'SOURCE_ENABLED=true', 'SOURCE_BATCH_SIZE=30',
        'AI_ROUND_BUDGET=1', 'AI_DAILY_BUDGET=5', 'AI_MONTHLY_BUDGET=100',
        'NODE_IMAGE=public.ecr.aws/docker/library/node:22-bookworm-slim',
        'PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim',
        'POSTGRES_IMAGE=public.ecr.aws/docker/library/postgres:17',
        'REDIS_IMAGE=public.ecr.aws/docker/library/redis:7-alpine']
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    print('Isolated staging environment created; real sending disabled.')
