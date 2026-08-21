"""One live session per provider, enforced on disk.

On disk and not in memory because the panel restarts, and because someone can
always run <provider>_docs.py straight from a terminal - the in-memory _RUNS
dict in gui/app.py sees neither.
"""
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
    assert second.exists()


def test_capacity_two_admits_two_and_refuses_the_third(tmp_path):
    locks.acquire(tmp_path, "amex", 2, "a")
    locks.acquire(tmp_path, "amex", 2, "b")
    with pytest.raises(locks.ProviderBusy):
        locks.acquire(tmp_path, "amex", 2, "c")


def test_different_providers_do_not_block_each_other(tmp_path):
    locks.acquire(tmp_path, "simyo", 1, "primary")
    assert locks.acquire(tmp_path, "youfone", 1, "primary").exists()


def test_a_stale_lock_is_taken_over(tmp_path):
    """A killed container leaves its lock behind; it must not be permanent."""
    locks.acquire(tmp_path, "simyo", 1, "dead")
    later = datetime.now() + locks.STALE_AFTER + timedelta(minutes=1)
    assert locks.acquire(tmp_path, "simyo", 1, "live", now=later).exists()


def test_holders_say_who_is_in_there(tmp_path):
    locks.acquire(tmp_path, "simyo", 1, "jane")
    who = locks.holders(tmp_path, "simyo", 1)
    assert [h["holder"] for h in who] == ["jane"]


def test_the_context_manager_releases_on_the_way_out(tmp_path):
    with locks.hold(tmp_path, "simyo", 1, "primary"):
        with pytest.raises(locks.ProviderBusy):
            locks.acquire(tmp_path, "simyo", 1, "jane")
    assert locks.acquire(tmp_path, "simyo", 1, "jane").exists()


def test_the_context_manager_releases_after_a_crash(tmp_path):
    with pytest.raises(RuntimeError):
        with locks.hold(tmp_path, "simyo", 1, "primary"):
            raise RuntimeError("boom")
    assert locks.acquire(tmp_path, "simyo", 1, "jane").exists()
