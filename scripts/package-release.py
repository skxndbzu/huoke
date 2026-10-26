"""Package tracked source + already-built frontend + pinned Linux wheels, never .env."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
files = {name: root / name for name in tracked if name and (root / name).is_file()}
for folder, prefix in [('frontend/dist', 'web'), ('.local/wheels', 'wheels')]:
    contents = [p for p in (root / folder).rglob('*') if p.is_file()]
    if not contents:
        raise SystemExit(f'Missing build output: {folder}')
    for path in contents:
        files[prefix + '/' + path.relative_to(root / folder).as_posix()] = path
manifest = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in sorted(files.items())}
for name in files:
    if name == '.env' or name.startswith('.git/') or name.endswith(('.pem', '.key')):
        raise SystemExit(f'Unexpected sensitive file: {name}')
output = root / '.local/release.tar.gz'
with tarfile.open(output, 'w:gz') as archive:
    for name, path in sorted(files.items()):
        archive.add(path, arcname=name)
    data = json.dumps({'files': manifest}, sort_keys=True, indent=2).encode()
    info = tarfile.TarInfo('BUILD-MANIFEST.json')
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))
print(json.dumps({'artifact': str(output), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'files': len(files)}))
