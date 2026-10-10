"""Source-only deployment helpers; never imported by installed components."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def stable_python(candidates=None):
    """Select a supported interpreter, skipping unavailable or broken candidates."""
    choices = candidates if candidates is not None else (
        '/opt/homebrew/bin/python3', '/usr/local/bin/python3', sys.executable)
    for candidate in choices:
        candidate = str(candidate)
        if not Path(candidate).is_file():
            continue
        try:
            result = subprocess.run([candidate, '-c', 'import sys; print(int(sys.version_info >= (3, 11)))'],
                                    text=True, capture_output=True, timeout=10, check=True)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.stdout.strip() == '1':
            return candidate
    raise RuntimeError('Python 3.11 or newer is required')


def atomic_text(path, text, mode=0o600):
    """Publish deployment text by replacing a fully written temporary file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{path.name}-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def validate_alias(alias, canonical):
    """Reject an occupied alias unless it already resolves to this command."""
    alias, canonical = Path(alias), Path(canonical)
    if os.path.lexists(alias) and (not alias.is_symlink() or alias.resolve() != canonical.resolve()):
        raise RuntimeError(f'Refusing to replace unrelated command alias: {alias}')


def install_alias(alias, canonical):
    """Replace a managed alias atomically with a relative command symlink."""
    alias, canonical = Path(alias), Path(canonical)
    validate_alias(alias, canonical)
    temporary = alias.with_name(f'.{alias.name}.installing-{os.getpid()}')
    temporary.unlink(missing_ok=True)
    try:
        temporary.symlink_to(canonical.name)
        os.replace(temporary, alias)
    finally:
        temporary.unlink(missing_ok=True)
