"""Which account is this tab? The decision, with no browser in sight.

The realistic failure this guards against: two Simyo accounts signed in to the
one shared Chrome, and a run for account A reading account B's tab.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperpull_core import identity


def rec(ident, date=""):
    return {"id": str(ident), "date": date}


def test_anchors_are_the_oldest_three_sorted_and_deduped():
    observed = [rec(105, "2026-05-01"), rec(101, "2026-01-01"),
                rec(103, "2026-03-01"), rec(102, "2026-02-01"),
                rec(101, "2026-01-01")]
    assert identity.pick_anchors(observed) == [
        rec(101, "2026-01-01"), rec(102, "2026-02-01"), rec(103, "2026-03-01")]


def test_two_records_sharing_an_id_collapse_to_one_anchor():
    """Youfone yields TWO documents per invoice - the Factuur and its
    Specificaties (apps/youfone/youfone_site.py, module docstring point 2) -
    and both carry the SAME invoice number and date, so an account's anchor
    records include this exact shape: two entries, same id, same date. The
    dedup here (`{r["id"]: r for r in _clean(records)}`) is keyed on `id`
    alone, so the pair has to collapse to one anchor rather than spending two
    of ANCHOR_COUNT's three slots on a single invoice."""
    observed = [rec(555, "2026-08-14"), rec(555, "2026-08-14")]
    assert identity.pick_anchors(observed) == [rec(555, "2026-08-14")]


def test_nothing_recorded_is_not_a_verdict():
    assert identity.check([], [rec(101, "2026-01-01")]) == identity.UNKNOWN


def test_an_empty_tab_is_not_a_verdict():
    assert identity.check([rec(101, "2026-01-01")], []) == identity.UNKNOWN


def test_all_anchors_present_is_ok():
    anchors = [rec(101, "2026-01-01"), rec(102, "2026-02-01")]
    observed = anchors + [rec(103, "2026-03-01")]
    assert identity.check(anchors, observed) == identity.OK


def test_an_anchor_that_aged_out_of_the_window_is_tolerated():
    """Simyo keeps ~12 months and drops the rest, so old anchors vanish."""
    anchors = [rec(101, "2025-01-01"), rec(102, "2025-02-01")]
    observed = [rec(102, "2025-02-01"), rec(114, "2026-02-01")]
    assert identity.check(anchors, observed) == identity.OK


def test_an_anchor_missing_from_inside_the_window_is_a_mismatch():
    """A document cannot leave the middle of an account's history."""
    anchors = [rec(101, "2026-01-01"), rec(102, "2026-02-01")]
    observed = [rec(102, "2026-02-01"), rec(103, "2026-03-01"),
                rec(100, "2025-12-01")]
    assert identity.check(anchors, observed) == identity.MISMATCH


def test_a_different_account_entirely_is_a_mismatch():
    ours = [rec(101, "2026-01-01"), rec(102, "2026-02-01")]
    theirs = [rec(901, "2026-01-01"), rec(902, "2026-02-01")]
    assert identity.check(ours, theirs) == identity.MISMATCH


def test_every_anchor_aged_out_proves_nothing():
    anchors = [rec(101, "2024-01-01"), rec(102, "2024-02-01")]
    observed = [rec(140, "2026-06-01"), rec(141, "2026-07-01")]
    assert identity.check(anchors, observed) == identity.AGED


def test_an_undated_anchor_that_is_absent_cannot_be_excused():
    assert identity.check([rec(101)], [rec(140, "2026-06-01")]) == identity.MISMATCH


def test_mismatch_refuses_when_adopt_false():
    assert identity.action(identity.MISMATCH, adopt=False) == identity.REFUSE


def test_mismatch_refuses_when_adopt_true():
    assert identity.action(identity.MISMATCH, adopt=True) == identity.REFUSE


def test_ok_proceeds_when_adopt_false():
    assert identity.action(identity.OK, adopt=False) == identity.PROCEED


def test_ok_proceeds_when_adopt_true():
    assert identity.action(identity.OK, adopt=True) == identity.PROCEED


def test_unknown_adopts_when_adopt_true():
    assert identity.action(identity.UNKNOWN, adopt=True) == identity.ADOPT


def test_unknown_refuses_when_adopt_false():
    assert identity.action(identity.UNKNOWN, adopt=False) == identity.REFUSE


def test_aged_adopts_when_adopt_true():
    assert identity.action(identity.AGED, adopt=True) == identity.ADOPT


def test_aged_refuses_when_adopt_false():
    assert identity.action(identity.AGED, adopt=False) == identity.REFUSE
