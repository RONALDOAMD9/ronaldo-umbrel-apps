"""Run by Umbrel's pre-start hook, never by the web server."""
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile

NAMES = ('config.json', 'tofa-tokens.json', 'trakt-tokens.json', 'state.sqlite')
CONTAINER = 'tofa-trakt-sync-1'


def private_write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
    if os.geteuid() == 0:
        os.chown(path, 1000, 1000)


def migrate(legacy_root, target):
    legacy_root, target = Path(legacy_root), Path(target)
    source = legacy_root / 'data'
    if not source.is_dir() or (target / '.legacy-migrated').exists():
        return False
    if any((target / n).exists() for n in ('tofa-tokens.json', 'trakt-tokens.json')):
        return False  # Never overwrite an app that was already connected.
    if legacy_root.is_symlink() or source.is_symlink():
        raise ValueError('Legacy data directory must not be a symbolic link')
    for name in NAMES:
        p = source / name
        if not p.is_file() or p.is_symlink():
            raise ValueError('Legacy data is incomplete or contains symbolic links')
    c = json.loads((source / 'config.json').read_text())
    if not c.get('server_id') or not c.get('trakt_client_id'):
        raise ValueError('Legacy configuration is incomplete')
    for name in ('tofa-tokens.json', 'trakt-tokens.json'):
        t = json.loads((source / name).read_text())
        if not all(t.get(k) for k in ('access_token', 'refresh_token', 'obtained_at')):
            raise ValueError('Legacy authorization is incomplete')
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    stopped = False
    inspect = subprocess.run(['docker', 'inspect', CONTAINER], capture_output=True, text=True)
    if inspect.returncode == 0:
        container = json.loads(inspect.stdout)[0]
        labels = container.get('Config', {}).get('Labels', {}) or {}
        mounts = container.get('Mounts', [])
        if (labels.get('com.docker.compose.project') != 'tofa-trakt' or
            labels.get('com.docker.compose.service') != 'sync' or
            not any(m.get('Destination') == '/data' and Path(m['Source']).resolve() == source.resolve() for m in mounts)):
            raise ValueError('The old container does not match the known standalone bridge')
        if container.get('State', {}).get('Running'):
            subprocess.run(['docker', 'stop', '--time', '20', CONTAINER], check=True, capture_output=True)
            stopped = True
    try:
        content = {name: (source / name).read_bytes() for name in NAMES if name != 'state.sqlite'}
        db = sqlite3.connect(source / 'state.sqlite')
        memory = sqlite3.connect(':memory:')
        db.backup(memory)
        db.close()
        if memory.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Legacy history database is invalid')
        content['state.sqlite'] = memory.serialize()
        memory.close()
        backup = io.BytesIO()
        with tarfile.open(fileobj=backup, mode='w:gz') as tar:
            for name, raw in content.items():
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(raw), 0o600
                tar.addfile(info, io.BytesIO(raw))
        backup_path = target / 'legacy-backup.tar.gz'
        if not backup_path.exists():
            private_write(backup_path, backup.getvalue())
        # The Docker host alias survives LAN address changes on reinstall.
        c['tofa_url'] = 'http://host.docker.internal:33333'
        c['interval_seconds'] = 60
        content['config.json'] = json.dumps(c).encode()
        with tempfile.TemporaryDirectory(prefix='.legacy-', dir=target) as stage:
            for name, raw in content.items():
                p = Path(stage) / name
                private_write(p, raw)
            for name in NAMES:
                os.replace(Path(stage) / name, target / name)
        private_write(target / '.legacy-migrated', b'Imported standalone bridge; previous data preserved.\n')
        if os.geteuid() == 0:
            os.chown(target, 1000, 1000)
        os.chmod(target, 0o700)
        print('Tofa Trakt: standalone authorizations and history migrated; old container stopped.')
        return True
    except Exception:
        if stopped:
            subprocess.run(['docker', 'start', CONTAINER], check=False, capture_output=True)
        raise


if __name__ == '__main__':
    migrate(sys.argv[1], sys.argv[2])
