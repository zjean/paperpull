"""One live session per provider, enforced with files.

Some providers allow exactly one signed-in session and end the older one when
a second appears - Simyo does this, server-side, and it is why two Simyo
accounts can never be pulled at the same moment. Making that a scheduling
contract rather than a race is cheaper than discovering it mid-run.

Files rather than memory, because the two places that would otherwise track
it both fail: gui/app.py's `_RUNS` dict does not survive a panel restart, and
neither the panel nor the scheduler sees someone running
`<provider>_docs.py` straight from a terminal. A file in a directory shared by
all of a provider's accounts is visible to all three.

Stale locks are aged out, not pid-checked. A pid means nothing across
containers - the panel, the scheduler and a shell all have their own pid
namespace, so "is 431 alive?" has no shared answer. An hour count does.
"""
from __future__ import annotations

import json
import os
import socket
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

# Longer than any real run and shorter than a working day: a lock older than
# this belongs to something that died.
STALE_AFTER = timedelta(hours=6)


class ProviderBusy(RuntimeError):
    """Another account of this provider is already signed in and running."""


def _lock_path(lock_dir: Path, slug: str, index: int) -> Path:
    return Path(lock_dir) / f"{slug}.{index}.lock"


def _claim(path: Path, holder: str) -> None:
    """Create the lock file, or raise FileExistsError. O_EXCL is the whole
    mechanism: on every filesystem this runs on, exactly one caller wins."""
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"holder": holder, "pid": os.getpid(),
                   "host": socket.gethostname(),
                   "at": datetime.now().isoformat(timespec="seconds")}, handle)


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _is_stale(path: Path, now: datetime) -> bool:
    record = _read(path)
    try:
        held_since = datetime.fromisoformat(record["at"])
    except (KeyError, TypeError, ValueError):
        return True      # unreadable: assume nothing is really holding it
    return now - held_since > STALE_AFTER


def acquire(lock_dir, slug: str, capacity: int, holder: str,
            now: Optional[datetime] = None) -> Path:
    """Take one of this provider's session slots, or raise ProviderBusy."""
    now = now or datetime.now()
    directory = Path(lock_dir)
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(max(1, int(capacity))):
        path = _lock_path(directory, slug, index)
        try:
            _claim(path, holder)
            return path
        except FileExistsError:
            if _is_stale(path, now):
                path.unlink(missing_ok=True)
                try:
                    _claim(path, holder)
                    return path
                except FileExistsError:
                    continue
    busy = ", ".join(h.get("holder", "?") for h in
                     holders(directory, slug, capacity)) or "another run"
    raise ProviderBusy(
        f"{slug}: all {capacity} session slot(s) are in use by {busy}. "
        f"This provider signs the older session out when a second one starts, "
        f"so this run would break both.")


def holders(lock_dir, slug: str, capacity: int) -> List[dict]:
    """Who currently holds this provider's slots."""
    out = []
    for index in range(max(1, int(capacity))):
        path = _lock_path(Path(lock_dir), slug, index)
        if path.exists():
            out.append(_read(path))
    return out


def release(path) -> None:
    Path(path).unlink(missing_ok=True)


@contextmanager
def hold(lock_dir, slug: str, capacity: int, holder: str):
    path = acquire(lock_dir, slug, capacity, holder)
    try:
        yield path
    finally:
        release(path)
