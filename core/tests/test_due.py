"""Who is worth running now, and in what order.

The ordering is the interesting part: a session with ten minutes of life left
cannot sit behind nine accounts whose sessions last for days.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperpull_core import due


def acct(app, account="primary", lifetime=None, newest="", checked="",
         cadence=None, parked=False):
    return {"app": app, "account": account,
            "session_lifetime_minutes": lifetime,
            "cadence_days": cadence, "newest_document_date": newest,
            "last_checked_date": checked, "parked": parked}


def names(plan):
    return [(a["app"], a["account"]) for a in plan]


def test_an_account_with_a_fresh_document_is_not_due():
    plan = due.plan([acct("simyo", newest="2026-08-15")], "2026-08-21")
    assert plan == []


def test_an_account_whose_next_document_is_overdue_is_due():
    plan = due.plan([acct("simyo", newest="2026-07-01")], "2026-08-21")
    assert names(plan) == [("simyo", "primary")]


def test_an_account_never_run_is_due():
    plan = due.plan([acct("simyo", newest="")], "2026-08-21")
    assert names(plan) == [("simyo", "primary")]


def test_an_account_already_checked_today_is_left_alone():
    plan = due.plan([acct("simyo", newest="", checked="2026-08-21")],
                    "2026-08-21")
    assert plan == []


def test_the_shortest_lived_session_goes_first():
    accounts = [acct("amex", lifetime=None, newest="2026-06-01"),
                acct("simyo", lifetime=10, newest="2026-06-01"),
                acct("ukg", lifetime=60, newest="2026-06-01")]
    assert [a["app"] for a in due.plan(accounts, "2026-08-21")] == \
        ["simyo", "ukg", "amex"]


def test_within_one_provider_the_stalest_account_goes_first():
    accounts = [acct("simyo", "jane", lifetime=10, newest="2026-07-01"),
                acct("simyo", "primary", lifetime=10, newest="2026-05-01")]
    assert names(due.plan(accounts, "2026-08-21")) == \
        [("simyo", "primary"), ("simyo", "jane")]


def test_a_parked_account_is_still_due_because_it_needs_a_human():
    plan = due.plan([acct("simyo", newest="2026-06-01", parked=True)],
                    "2026-08-21")
    assert names(plan) == [("simyo", "primary")]


def test_a_provider_specific_cadence_beats_the_default():
    weekly = acct("gap", newest="2026-08-14", cadence=7)
    assert names(due.plan([weekly], "2026-08-21")) == [("gap", "primary")]


def test_an_unreadable_date_does_not_take_the_scheduler_down():
    plan = due.plan([acct("simyo", newest="not-a-date")], "2026-08-21")
    assert names(plan) == [("simyo", "primary")]
