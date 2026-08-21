"""One live session per provider, enforced on disk.

On disk and not in memory because the panel restarts, and because someone can
always run <provider>_docs.py straight from a terminal - the in-memory _RUNS
dict in gui/app.py sees neither.
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperpull_core import locks


def test_one_holder_at_capacity_one(tmp_path):
    first = locks.acquire(tmp_path, "simyo", 1, "primary")
    with pytest.raises(locks.ProviderBusy):
        locks.acquire(tmp_path, "simyo", 1, "jane")
    locks.release(first)
    second = locks.acquire(tmp_path, "simyo", 1, "jane")
    assert second.path.exists()


def test_capacity_two_admits_two_and_refuses_the_third(tmp_path):
    locks.acquire(tmp_path, "amex", 2, "a")
    locks.acquire(tmp_path, "amex", 2, "b")
    with pytest.raises(locks.ProviderBusy):
        locks.acquire(tmp_path, "amex", 2, "c")


def test_different_providers_do_not_block_each_other(tmp_path):
    locks.acquire(tmp_path, "simyo", 1, "primary")
    assert locks.acquire(tmp_path, "youfone", 1, "primary").path.exists()


def test_a_stale_lock_is_taken_over(tmp_path):
    """A killed container leaves its lock behind; it must not be permanent."""
    locks.acquire(tmp_path, "simyo", 1, "dead")
    later = datetime.now() + locks.STALE_AFTER + timedelta(minutes=1)
    assert locks.acquire(tmp_path, "simyo", 1, "live", now=later).path.exists()


def test_holders_say_who_is_in_there(tmp_path):
    locks.acquire(tmp_path, "simyo", 1, "jane")
    who = locks.holders(tmp_path, "simyo", 1)
    assert [h["holder"] for h in who] == ["jane"]


def test_the_context_manager_releases_on_the_way_out(tmp_path):
    with locks.hold(tmp_path, "simyo", 1, "primary"):
        with pytest.raises(locks.ProviderBusy):
            locks.acquire(tmp_path, "simyo", 1, "jane")
    assert locks.acquire(tmp_path, "simyo", 1, "jane").path.exists()


def test_the_context_manager_releases_after_a_crash(tmp_path):
    with pytest.raises(RuntimeError):
        with locks.hold(tmp_path, "simyo", 1, "primary"):
            raise RuntimeError("boom")
    assert locks.acquire(tmp_path, "simyo", 1, "jane").path.exists()


def _write_stale_record(path, holder, when):
    """Hand-craft a lock file with an arbitrary 'at' timestamp, bypassing
    acquire() entirely, so a test can force a slot to be stale right now
    without sleeping or moving the clock forward for anyone else."""
    path.write_text(json.dumps({
        "holder": holder, "token": "irrelevant", "pid": 1, "host": "h",
        "at": when.isoformat(timespec="seconds"),
    }), encoding="utf-8")


def test_forced_interleaving_after_a_shared_stale_read_yields_one_holder(tmp_path):
    """The exact bug the review caught: A and B both fail their O_EXCL claim
    against the same stale record. B is driven all the way through its own
    steal-and-reclaim first. Only then does A resume and try to finish
    stealing what it saw a moment ago - which is no longer there; what IS
    there now is B's live claim, and A must not clobber it.

    Forced by calling the module's own primitives in a fixed order, not by
    threads or sleeps - this is deterministic on every run.
    """
    tmp_path.mkdir(exist_ok=True)
    path = locks._lock_path(tmp_path, "simyo", 0)
    stale_at = datetime.now() - locks.STALE_AFTER - timedelta(minutes=1)
    _write_stale_record(path, "dead", stale_at)

    # Both A and B fail their initial O_EXCL claim against "dead"'s record.
    with pytest.raises(FileExistsError):
        locks._claim(path, "a")
    with pytest.raises(FileExistsError):
        locks._claim(path, "b")

    # B runs its whole steal-and-reclaim to completion before A gets to do
    # anything else with what it saw.
    b_claim = locks._steal(path, "b", datetime.now())
    assert b_claim is not None

    # A resumes and tries to finish stealing the record it saw earlier.
    a_claim = locks._steal(path, "a", datetime.now())

    assert a_claim is None
    who = locks.holders(tmp_path, "simyo", 1)
    assert len(who) == 1
    assert who[0]["holder"] == "b"


def test_release_after_being_stolen_does_not_delete_the_new_holder(tmp_path):
    old = locks.acquire(tmp_path, "simyo", 1, "primary")
    later = datetime.now() + locks.STALE_AFTER + timedelta(minutes=1)
    new = locks.acquire(tmp_path, "simyo", 1, "someone-else", now=later)

    with pytest.warns(RuntimeWarning, match="already been taken over"):
        released = locks.release(old)

    assert released is False
    who = locks.holders(tmp_path, "simyo", 1)
    assert len(who) == 1
    assert who[0]["holder"] == "someone-else"
