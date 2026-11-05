"""A dedicated local credential file. Never included in UI settings or snapshots."""
import os
from pathlib import Path
import stat
import tempfile


def location(root):
    return Path(root) / 'credentials' / 'ark.key'


def save(root, key):
    if not isinstance(key, str) or not key.strip() or len(key) > 4096 or any(c in key for c in '\n\r\0'):
        raise ValueError('API Key 格式无效')
    path = location(root)
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError('凭据路径不能包含软链接')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix='.ark-')
    try:
        with os.fdopen(descriptor, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(key.strip()); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        Path(name).unlink(missing_ok=True)
    return status(root)


def load(root):
    path = location(root)
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError('凭据路径不能包含软链接')
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor) as stream:
        s = os.fstat(stream.fileno())
        if not stat.S_ISREG(s.st_mode) or s.st_mode & 0o077 or s.st_uid != os.getuid():
            raise ValueError('凭据文件须归当前用户所有且权限为 600')
        return stream.read(4097).strip() or None


def status(root):
    path = location(root)
    return {'configured': path.is_file() and not path.is_symlink(), 'masked': '••••••••' if path.is_file() else '',
            'revision': path.stat().st_mtime_ns if path.is_file() else 0}
