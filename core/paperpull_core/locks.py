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

O_EXCL guarantees exactly one caller can create a given, currently-absent
path - it says nothing about taking over a path that already exists. A
first version of this module handled that second case with a plain
`unlink()` followed by a fresh `_claim()`, which is a check-then-act race:
one caller's "it looks stale" can be true when read and false by the time
that caller acts on it, because a second caller finished its own steal in
between. `_steal()` closes that by using `os.rename()` - also atomic, and
the thing that actually decides who gets to inspect a given file's bytes -
to take exclusive possession of whatever is at a slot before deciding
anything about it, and putting it back untouched if it turns out to still
be live.
"""
from __future__ import annotations

import json
import os
import socket
import uuid
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

# Longer than any real run and shorter than a working day: a lock older than
# this belongs to something that died.
STALE_AFTER = timedelta(hours=6)


class ProviderBusy(RuntimeError):
    """Another account of this provider is already signed in and running."""


@dataclass(frozen=True)
class Claim:
    """What acquire() and hold() hand back.

    `path` is the lock file; `token` is a one-time value written inside it
    at claim time. Holding the path is not proof of ownership - another
    caller can legitimately take over a stale path, which replaces the file
    at that same location with a new one - so release() must be handed the
    token back, and it walks away without deleting anything if the token on
    disk has already moved on without it.
    """
    path: Path
    token: str


def _lock_path(lock_dir: Path, slug: str, index: int) -> Path:
    return Path(lock_dir) / f"{slug}.{index}.lock"


def _claim(path: Path, holder: str) -> Claim:
    """Create the lock file, or raise FileExistsError, and hand back the
    token that proves this call - and not some later caller who takes the
    same path over - is the rightful holder.

    O_EXCL is what makes this atomic: on every filesystem this runs on,
    exactly one caller can create a given, currently-absent path. flush()
    and fsync() before closing so a concurrent reader never sees a
    half-written file and mistakes a fresh lock for an unreadable, and
    therefore stale, one.
    """
    token = uuid.uuid4().hex
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"holder": holder, "token": token, "pid": os.getpid(),
                   "host": socket.gethostname(),
                   "at": datetime.now().isoformat(timespec="seconds")}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    return Claim(path, token)


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _is_stale(record: dict, now: datetime) -> bool:
    try:
        held_since = datetime.fromisoformat(record["at"])
    except (KeyError, TypeError, ValueError):
        return True      # unreadable: assume nothing is really holding it
    return now - held_since > STALE_AFTER


def _steal(path: Path, holder: str, now: datetime) -> Optional[Claim]:
    """Try to take over a slot that looked occupied a moment ago.

    `os.rename()` is the atomic step, and the whole point of this function:
    exactly one caller can rename a given source path away, so exactly one
    caller ends up holding the file that used to be at `path` and gets to
    decide, from bytes nobody else can also be looking at, whether it is
    really stale. Everyone else either finds `path` already gone (this
    caller, or another one, got there first) or - after this call returns -
    finds a fresh claim already sitting there.

    Returns the new Claim if this call ends up holding the slot. Returns
    None if the slot legitimately belongs to someone else - restoring their
    record first, unchanged, if this call is the one that turns out to have
    grabbed it by mistake.

    That restore has one accepted gap: os.rename() leaves `path` briefly
    absent while the aside copy is being judged. If an unrelated third
    caller's O_EXCL claim lands in that instant, it owns `path` before the
    restore can run - the restore then correctly refuses to overwrite it
    (see the FileExistsError branch below), but the original holder's
    record is gone while that holder is still running, so two sessions of
    one provider can overlap. Closing it needs sequence numbers or a real
    lock, which the standard-library-only constraint here rules out; it is
    accepted because it needs both a holder that outlives STALE_AFTER and a
    third claim landing inside a single syscall, against callers - panel,
    scheduler, terminal - that do not poll tightly. The branch below warns
    when it fires, so it is at least visible rather than silent.
    """
    aside = path.with_name(f"{path.name}.steal-{uuid.uuid4().hex}")
    try:
        os.rename(path, aside)
    except FileNotFoundError:
        # It vanished between our failed claim and this rename: released by
        # its holder, or already carried off by someone else's steal. One
        # clean shot at the now-possibly-empty path; if that also loses,
        # someone else has legitimately taken it.
        try:
            return _claim(path, holder)
        except FileExistsError:
            return None

    record = _read(aside)
    if _is_stale(record, now):
        aside.unlink(missing_ok=True)
        try:
            return _claim(path, holder)
        except FileExistsError:
            # Someone else claimed the path we just emptied, in the instant
            # between our unlink and our claim. They legitimately hold it.
            return None

    # What we captured is not stale: the record that made this caller
    # believe the slot was stale was already out of date by the time this
    # call got exclusive possession of it. Put it back exactly where its
    # rightful holder expects to find it. `os.link`, not `os.rename`,
    # because it fails loudly with FileExistsError if anything has
    # reoccupied `path` in the meantime, instead of silently overwriting it
    # the way a rename onto an existing destination would.
    try:
        os.link(aside, path)
    except FileExistsError:
        # The gap the docstring above accepts: a third caller's own claim
        # filled `path` while we had the original record held aside.
        # Backing off rather than overwriting that fresh claim is correct -
        # but the record we were trying to restore is gone for good, and
        # its holder is still running. Surface that rather than let it pass
        # silently, the same way release() warns on a token mismatch.
        warnings.warn(
            f"lock {path} lost its original holder's record during a "
            f"stale-takeover restore - a different caller claimed the "
            f"slot first, so two sessions of this provider may now be "
            f"running at once",
            RuntimeWarning, stacklevel=2)
    finally:
        aside.unlink(missing_ok=True)
    return None


def acquire(lock_dir, slug: str, capacity: int, holder: str,
            now: Optional[datetime] = None) -> Claim:
    """Take one of this provider's session slots, or raise ProviderBusy."""
    now = now or datetime.now()
    directory = Path(lock_dir)
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(max(1, int(capacity))):
        path = _lock_path(directory, slug, index)
        try:
            return _claim(path, holder)
        except FileExistsError:
            claim = _steal(path, holder, now)
            if claim is not None:
                return claim
            # Either the slot is genuinely occupied (steal restored it) or
            # someone else just filled it; either way this index is spoken
            # for right now, so move on to the next one rather than spin.
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


def release(claim: Claim) -> bool:
    """Give up a slot - but only if it is still this caller's.

    A slot can legitimately be taken over while its original holder is
    still working (that holder just ran unusually long, past STALE_AFTER).
    When that holder eventually calls release(), the path alone can't tell
    it that; the token can. A mismatch - or nothing at all where the lock
    used to be - means this call's slot was already stolen out from under
    it. That is a real operational fact, worth a warning, not a silent
    no-op that quietly deletes whoever holds the slot now.

    Returns True if this call's own lock was removed, False if it walked
    away having found someone else's.
    """
    record = _read(claim.path)
    if record.get("token") != claim.token:
        warnings.warn(
            f"lock {claim.path} was released by a caller whose slot had "
            f"already been taken over; leaving the new holder's lock alone",
            RuntimeWarning, stacklevel=2)
        return False
    claim.path.unlink(missing_ok=True)
    return True


@contextmanager
def hold(lock_dir, slug: str, capacity: int, holder: str):
    claim = acquire(lock_dir, slug, capacity, holder)
    try:
        yield claim
    finally:
        release(claim)
