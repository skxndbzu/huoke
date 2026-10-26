"""Hold a process-owned cross-worktree lock; Ctrl+C releases it, crashes release it too."""
import os
from pathlib import Path
import subprocess
import time

common = Path(subprocess.check_output(['git', 'rev-parse', '--git-common-dir'], text=True).strip()).resolve()
with open(common / 'huoke-iteration.lock', 'a+b') as handle:
    handle.seek(0)
    if os.name == 'nt':
        import msvcrt
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit('Another iteration holds the lock; read-only work only.')
    else:
        import fcntl
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit('Another iteration holds the lock; read-only work only.')
    print(f'Iteration lock acquired by PID {os.getpid()}. Keep this process alive.', flush=True)
    try:
        while True:
            time.sleep(30)
    except KeyboardInterrupt:
        print('Lock released.')
