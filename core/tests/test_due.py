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


def test_a_zero_cadence_beats_the_default():
    daily = acct("signal", newest="2026-08-21", cadence=0)
    assert names(due.plan([daily], "2026-08-21")) == [("signal", "primary")]


def test_accounts_are_read_off_the_real_trees(tmp_path, monkeypatch):
    """A config points at an output_dir; that dir holds the state we read."""
    from paperpull_core import appload

    app_dir = tmp_path / "apps" / "testco"
    app_dir.mkdir(parents=True)
    (app_dir / "testco_docs.py").write_text("", encoding="utf-8")
    out = tmp_path / "data" / "testco"
    (out).mkdir(parents=True)
    (app_dir / "config.json").write_text(
        '{"output_dir": "%s"}' % out.as_posix(), encoding="utf-8")
    (out / "progress.json").write_text(
        '{"a": {"date": "2026-06-01"}, "b": {"date": "2026-07-01"}}',
        encoding="utf-8")
    (out / "sentinel.json").write_text(
        '{"session": {"state": "parked", "last_verified_alive": '
        '"2026-07-02T10:00:00"}}', encoding="utf-8")

    found = appload.accounts(tmp_path / "apps", None)
    assert len(found) == 1
    rec = found[0]
    assert rec["app"] == "testco"
    assert rec["account"] == "primary"
    assert rec["newest_document_date"] == "2026-07-01"
    assert rec["parked"] is True
    assert rec["last_checked_date"] == "2026-07-02"


def _healthy_app(apps_root, name):
    """A minimal, well-formed app+account: entry script, config, progress."""
    app_dir = apps_root / name
    app_dir.mkdir(parents=True)
    (app_dir / f"{name}_docs.py").write_text("", encoding="utf-8")
    out = app_dir / "out"
    out.mkdir()
    (app_dir / "config.json").write_text(
        '{"output_dir": "%s"}' % out.as_posix(), encoding="utf-8")
    (out / "progress.json").write_text(
        '{"a": {"date": "2026-07-01"}}', encoding="utf-8")
    return app_dir


def test_a_non_object_config_is_skipped_without_taking_down_the_listing(
        tmp_path):
    """[1, 2, 3] parses fine as JSON but is not a config - it must not crash
    the whole pass, and the tree's other, healthy app must still be read."""
    from paperpull_core import appload

    apps_root = tmp_path / "apps"
    apps_root.mkdir()
    broken = apps_root / "broken"
    broken.mkdir()
    (broken / "broken_docs.py").write_text("", encoding="utf-8")
    (broken / "config.json").write_text("[1, 2, 3]", encoding="utf-8")
    _healthy_app(apps_root, "healthy")

    found = appload.accounts(apps_root, None)
    assert [r["app"] for r in found] == ["healthy"]


def test_a_non_dict_progress_record_is_skipped_without_taking_down_the_listing(
        tmp_path):
    """A progress.json record whose value is not itself a dict must not crash
    the whole pass, and the tree's other, healthy app must still be read."""
    from paperpull_core import appload

    apps_root = tmp_path / "apps"
    apps_root.mkdir()
    odd = apps_root / "odd"
    odd.mkdir()
    (odd / "odd_docs.py").write_text("", encoding="utf-8")
    out = odd / "out"
    out.mkdir()
    (odd / "config.json").write_text(
        '{"output_dir": "%s"}' % out.as_posix(), encoding="utf-8")
    (out / "progress.json").write_text(
        '{"a": "not-a-dict"}', encoding="utf-8")
    _healthy_app(apps_root, "healthy")

    found = appload.accounts(apps_root, None)
    apps = {r["app"] for r in found}
    assert "healthy" in apps
    # The odd account itself is still reported - it has a readable config and
    # output_dir - just with no newest document date to show for it.
    odd_rec = next(r for r in found if r["app"] == "odd")
    assert odd_rec["newest_document_date"] == ""


def test_accounts_honour_a_separate_config_root(tmp_path):
    """PAPERPULL_CONFIG_ROOT layout: configs live at <root>/<app>/config*.json,
    not beside the app - the layout this Docker fork actually deploys."""
    from paperpull_core import appload

    apps_root = tmp_path / "apps"
    app_dir = apps_root / "testco"
    app_dir.mkdir(parents=True)
    (app_dir / "testco_docs.py").write_text("", encoding="utf-8")

    config_root = tmp_path / "config"
    cfg_dir = config_root / "testco"
    cfg_dir.mkdir(parents=True)
    out = tmp_path / "data" / "testco"
    out.mkdir(parents=True)
    (cfg_dir / "config.json").write_text(
        '{"output_dir": "%s"}' % out.as_posix(), encoding="utf-8")
    (out / "progress.json").write_text(
        '{"a": {"date": "2026-07-01"}}', encoding="utf-8")

    found = appload.accounts(apps_root, config_root)
    assert len(found) == 1
    rec = found[0]
    assert rec["app"] == "testco"
    assert rec["account"] == "primary"
    assert rec["newest_document_date"] == "2026-07-01"
