"""The per-account state file that cannot log in to anything."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperpull_core import sentinel
from paperpull_core.storage import JsonStore


def store_at(tmp_path):
    return JsonStore(tmp_path / "sentinel.json")


def test_anchors_round_trip(tmp_path):
    store = store_at(tmp_path)
    anchors = [{"id": "101", "date": "2026-01-01"}]
    sentinel.write_anchors(store, anchors)
    assert sentinel.read_anchors(store_at(tmp_path)) == anchors


def test_no_anchors_yet_reads_as_empty(tmp_path):
    assert sentinel.read_anchors(store_at(tmp_path)) == []


def test_warm_records_when_it_was_verified(tmp_path):
    store = store_at(tmp_path)
    sentinel.mark_warm(store, "2026-08-21T10:00:00")
    fresh = store_at(tmp_path)
    assert sentinel.session_state(fresh) == sentinel.WARM
    assert sentinel.last_verified_alive(fresh) == "2026-08-21T10:00:00"


def test_parking_records_the_reason(tmp_path):
    store = store_at(tmp_path)
    sentinel.park(store, "signed out", "2026-08-21T10:05:00")
    fresh = store_at(tmp_path)
    assert sentinel.session_state(fresh) == sentinel.PARKED
    assert (fresh.get(sentinel.SESSION_KEY) or {})["parked_reason"] == "signed out"


def test_parking_then_warming_clears_the_reason(tmp_path):
    store = store_at(tmp_path)
    sentinel.park(store, "signed out", "2026-08-21T10:05:00")
    sentinel.mark_warm(store, "2026-08-21T11:00:00")
    fresh = store_at(tmp_path)
    assert sentinel.session_state(fresh) == sentinel.WARM
    assert (fresh.get(sentinel.SESSION_KEY) or {})["parked_reason"] == ""


def test_identity_and_session_do_not_overwrite_each_other(tmp_path):
    store = store_at(tmp_path)
    sentinel.write_anchors(store, [{"id": "101", "date": "2026-01-01"}])
    sentinel.park(store, "signed out", "2026-08-21T10:05:00")
    fresh = store_at(tmp_path)
    assert sentinel.read_anchors(fresh) == [{"id": "101", "date": "2026-01-01"}]
    assert sentinel.session_state(fresh) == sentinel.PARKED
