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


def test_a_parked_account_checked_today_is_still_due():
    """The motivating case for this whole schedule, and the one the test
    above missed by leaving `checked` empty: warm at 09:00, the ten-minute
    session dies, parked at 09:20. `last_checked_date` is therefore TODAY on
    exactly the account a person most needs to be told about - so parked has
    to be read before the same-day short-circuit, not after it."""
    plan = due.plan([acct("simyo", newest="2026-06-01", checked="2026-08-21",
                          parked=True)], "2026-08-21")
    assert names(plan) == [("simyo", "primary")]


def test_a_parked_account_with_a_fresh_document_is_still_due():
    """Parked outranks the cadence too: nothing about this account will
    change until a human signs in, and only the list they read says so."""
    plan = due.plan([acct("simyo", newest="2026-08-20", parked=True)],
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


def test_a_relative_output_dir_is_resolved_against_the_app(tmp_path):
    """Every one of the 18 shipped config.example.json files says
    "output_dir": ".", which only ever means the app's own directory - that
    is the working directory a downloader is launched with. Read literally
    from a long-lived caller (uvicorn, the scheduler), both state files below
    are looked for somewhere they never are, and both misses look like
    facts: "no documents ever" and "never parked". Every account was then
    always due and none was ever parked."""
    from paperpull_core import appload

    app_dir = tmp_path / "apps" / "testco"
    app_dir.mkdir(parents=True)
    (app_dir / "testco_docs.py").write_text("", encoding="utf-8")
    (app_dir / "config.json").write_text(
        '{"output_dir": "."}', encoding="utf-8")
    (app_dir / "progress.json").write_text(
        '{"a": {"date": "2026-07-01"}}', encoding="utf-8")
    (app_dir / "sentinel.json").write_text(
        '{"session": {"state": "parked", '
        '"last_verified_alive": "2026-07-02T10:00:00"}}', encoding="utf-8")

    rec = appload.accounts(tmp_path / "apps", None)[0]
    assert rec["output_dir"] == str(app_dir)
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


# -- measured cadence --------------------------------------------------------
# `cadence_days` used to be config-only, so an account that had never been
# given one was judged against a flat 31 days: a quarterly provider looked due
# every month, and a weekly one was checked long after the next document
# landed. The history in progress.json already says how often this provider
# issues documents, so it is measured from there and the config still wins.

def _monthly(start_year=2026, start_month=1, count=8, account="primary"):
    """count records, one a month, oldest first."""
    recs = {}
    for i in range(count):
        month = start_month + i
        year = start_year + (month - 1) // 12
        recs["d%d" % i] = {"date": "%04d-%02d-05" % (year, (month - 1) % 12 + 1),
                           "account": account}
    return recs


def test_a_monthly_history_measures_as_monthly():
    got = due.measure_cadence(list(_monthly().values()))
    assert 28 <= got <= 32, got


def test_a_quarterly_history_is_not_judged_as_monthly():
    recs = [{"date": d, "account": "primary"} for d in
            ["2025-03-01", "2025-06-01", "2025-09-01", "2025-12-01",
             "2026-03-01"]]
    got = due.measure_cadence(recs)
    assert 88 <= got <= 95, got
    # The point of measuring: at 31 days this account would have been called
    # due two months early, every quarter.
    assert not due._is_due(
        {"cadence_days": got, "newest_document_date": "2026-03-01",
         "last_checked_date": "", "parked": False},
        __import__("datetime").date(2026, 5, 1))


def test_two_accounts_billed_monthly_do_not_read_as_fortnightly():
    """One install can cover several of the provider's own accounts. Pooling
    their dates halves every gap, and the archive then calls itself due a
    fortnight after a statement arrives."""
    recs = list(_monthly(account="cheque").values())
    mid = {"m%d" % i: {"date": "2026-%02d-20" % (i + 1), "account": "savings"}
           for i in range(8)}
    recs += list(mid.values())
    got = due.measure_cadence(recs)
    assert 28 <= got <= 32, got


def test_too_little_history_measures_nothing():
    """Two documents is one gap, and one gap is an anecdote."""
    assert due.measure_cadence(
        [{"date": "2026-01-05"}, {"date": "2026-02-05"}]) is None


def test_a_receipt_archive_measures_nothing():
    """Receipt apps record `purchase_date`, not `date`, because purchases
    arrive irregularly - a cadence measured off them would be noise."""
    recs = [{"purchase_date": "2026-0%d-01" % i} for i in range(1, 6)]
    assert due.measure_cadence(recs) is None


def test_one_absurd_date_cannot_set_the_cadence():
    """Both values below can come from a provider page: a record dated in
    year 0001 would otherwise measure a cadence of several centuries."""
    recs = [{"date": d} for d in
            ["0001-01-01", "2026-01-05", "2026-02-05", "2026-03-05",
             "2026-04-05"]]
    got = due.measure_cadence(recs)
    assert 28 <= got <= 32, got


def test_a_measured_cadence_reaches_the_account_list(tmp_path):
    """End to end: history on disk, no cadence in the config."""
    from paperpull_core import appload
    import json

    app_dir = tmp_path / "apps" / "testco"
    app_dir.mkdir(parents=True)
    (app_dir / "testco_docs.py").write_text("", encoding="utf-8")
    out = tmp_path / "data" / "testco"
    out.mkdir(parents=True)
    (app_dir / "config.json").write_text(
        '{"output_dir": "%s"}' % out.as_posix(), encoding="utf-8")
    (out / "progress.json").write_text(json.dumps(_monthly()), encoding="utf-8")

    rec = appload.accounts(tmp_path / "apps", None)[0]
    assert 28 <= rec["cadence_days"] <= 32, rec["cadence_days"]


def test_a_cadence_in_the_config_still_wins(tmp_path):
    """Measuring is a fallback for accounts nobody has told. An explicit
    value is a person's answer and outranks the history."""
    from paperpull_core import appload
    import json

    app_dir = tmp_path / "apps" / "testco"
    app_dir.mkdir(parents=True)
    (app_dir / "testco_docs.py").write_text("", encoding="utf-8")
    out = tmp_path / "data" / "testco"
    out.mkdir(parents=True)
    (app_dir / "config.json").write_text(
        '{"output_dir": "%s", "cadence_days": 7}' % out.as_posix(),
        encoding="utf-8")
    (out / "progress.json").write_text(json.dumps(_monthly()), encoding="utf-8")

    rec = appload.accounts(tmp_path / "apps", None)[0]
    assert rec["cadence_days"] == 7


def test_a_zero_in_the_config_is_not_overruled_by_the_history(tmp_path):
    """0 means "always due" and is falsy - the reason this is a test."""
    from paperpull_core import appload
    import json

    app_dir = tmp_path / "apps" / "testco"
    app_dir.mkdir(parents=True)
    (app_dir / "testco_docs.py").write_text("", encoding="utf-8")
    out = tmp_path / "data" / "testco"
    out.mkdir(parents=True)
    (app_dir / "config.json").write_text(
        '{"output_dir": "%s", "cadence_days": 0}' % out.as_posix(),
        encoding="utf-8")
    (out / "progress.json").write_text(json.dumps(_monthly()), encoding="utf-8")

    rec = appload.accounts(tmp_path / "apps", None)[0]
    assert rec["cadence_days"] == 0
