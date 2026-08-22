"""The sitting: which accounts are due, and what the panel already knows
about each one before a person ever clicks Login.

due.plan() and appload.accounts() are already reviewed and covered in
core/tests/test_due.py - what belongs here is the panel's own use of them:
/api/due's shape and ordering, and its guarded import degrading to a 503
rather than exploding (see gui/app.py's api_due for why a 503 and not a
quietly-empty "due": []).

Reading _accounts() for this task also turned up a real gap: it has reported
`state`, `last_alive`, `parked_reason` and `identified` since an earlier task,
and nothing anywhere asserted any of their values - every existing test only
ever checked `name` (see test_remote_browser.py's `_names` helper). The tests
below close that.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as panel  # noqa: E402


# -- _accounts(): the fields beyond `name` ----------------------------------


@pytest.fixture(params=["native", "config-root"])
def fake_app(request, tmp_path, monkeypatch):
    """The app directory, under both config layouts the panel supports.

    Parametrised rather than left to the ambient environment, because the two
    layouts are not interchangeable: natively a config sits in the app's own
    directory, but the image sets PAPERPULL_CONFIG_ROOT=/config, so
    `_config_dir` looks somewhere else entirely. A test that writes its config
    into app_dir and leaves the variable alone therefore passes on a laptop
    and fails inside the container it ships in - which is exactly how the four
    _accounts() tests below were green locally and red in CI, reading the
    image's real (sentinel-less) /config/ally/config.json instead of the one
    the test wrote. test_api_due_orders_the_perishable_account_first already
    knew to clear the variable; these did not.
    """
    app_dir = tmp_path / "apps" / "ally"
    app_dir.mkdir(parents=True)
    (app_dir / "ally_docs.py").write_text("x\n", encoding="utf-8")
    if request.param == "native":
        monkeypatch.delenv("PAPERPULL_CONFIG_ROOT", raising=False)
    else:
        root = tmp_path / "config"
        (root / app_dir.name).mkdir(parents=True)
        monkeypatch.setenv("PAPERPULL_CONFIG_ROOT", str(root))
    return app_dir


def _write_config(app_dir, out):
    """Wherever this layout says this app's config lives - which is the point
    of asking the panel rather than assuming app_dir."""
    cfg_dir = panel._config_dir(app_dir)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({"output_dir": str(out)}), encoding="utf-8")


def _write_config_and_sentinel(app_dir, session=None, identity=None):
    """A primary config plus the sentinel.json _accounts() reads for it."""
    out = app_dir / "out"
    out.mkdir()
    _write_config(app_dir, out)
    payload = {}
    if session is not None:
        payload["session"] = session
    if identity is not None:
        payload["identity"] = identity
    (out / "sentinel.json").write_text(json.dumps(payload), encoding="utf-8")


def _primary(accounts):
    return next(a for a in accounts if a["name"] == "primary")


def test_a_warm_identified_account_surfaces_its_session_state(fake_app):
    _write_config_and_sentinel(
        fake_app,
        session={"state": "warm", "last_verified_alive": "2026-08-20T09:30:00"},
        identity={"anchors": ["acct-123"]})

    rec = _primary(panel._accounts(fake_app))
    assert rec["state"] == "warm"
    assert rec["last_alive"] == "2026-08-20T09:30:00"
    assert rec["parked_reason"] == ""
    assert rec["identified"] is True


def test_a_parked_account_surfaces_why_it_is_parked(fake_app):
    _write_config_and_sentinel(
        fake_app,
        session={"state": "parked", "parked_reason": "signed out mid-run"},
        identity={"anchors": ["acct-123"]})

    rec = _primary(panel._accounts(fake_app))
    assert rec["state"] == "parked"
    assert rec["parked_reason"] == "signed out mid-run"
    # Parked is a normal state, not a failure - it is still identified, and
    # still belongs in the list a person sitting down needs to see.
    assert rec["identified"] is True


def test_the_adopted_anchors_themselves_reach_the_panel(fake_app):
    """Not just "identified: true". Adopting an identity is the one moment a
    run takes on trust that the tab it can see is this config's account, and
    the spec's answer to that is that the panel shows what was adopted so a
    person can sanity-check it once - which a boolean cannot support. An
    anchor is a document number and its date, both already in this account's
    index CSV, so nothing new is exposed by showing them.
    """
    _write_config_and_sentinel(
        fake_app,
        session={"state": "warm"},
        identity={"anchors": [{"id": "8412345678", "date": "2026-06-01"},
                              {"id": "8412345679", "date": "2026-07-01"}]})

    rec = _primary(panel._accounts(fake_app))
    assert rec["identified"] is True
    assert rec["anchors"] == [{"id": "8412345678", "date": "2026-06-01"},
                              {"id": "8412345679", "date": "2026-07-01"}]


def test_a_malformed_anchor_record_does_not_break_the_account_list(fake_app):
    """A hand-edited sentinel.json, or a record from a future shape. The
    account still has to be listed - the panel's app list is the one thing
    that must always draw."""
    _write_config_and_sentinel(
        fake_app, session={"state": "warm"}, identity={"anchors": "nonsense"})

    rec = _primary(panel._accounts(fake_app))
    assert rec["anchors"] == []
    # A non-empty string is still *something* recorded, so the account is not
    # reported as never having been adopted.
    assert rec["identified"] is True


def test_an_account_with_no_adopted_identity_is_unidentified(fake_app):
    """No anchors recorded yet - e.g. a fresh sign-in nothing has verified."""
    _write_config_and_sentinel(
        fake_app, session={"state": "warm"}, identity={})

    rec = _primary(panel._accounts(fake_app))
    assert rec["identified"] is False


def test_an_account_with_no_sentinel_yet_reports_nothing_known(fake_app):
    """Before a first run, there is no sentinel.json at all - _accounts()
    must report the account, just with every session field empty/false."""
    _write_config(fake_app, fake_app / "out")

    rec = _primary(panel._accounts(fake_app))
    assert rec == {"name": "primary", "state": "", "last_alive": "",
                    "parked_reason": "", "identified": False, "anchors": []}


# -- /api/due: ordering -----------------------------------------------------


def _write_storage(app_dir, lifetime):
    """A minimal storage.py, just enough for appload.load_spec to read a
    session_lifetime_minutes back out of it."""
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "storage.py").write_text(
        "from pathlib import Path\n"
        "from paperpull_core.spec import AppSpec\n"
        "SPEC = AppSpec(provider='Test', "
        "project_dir=Path(__file__).resolve().parent, "
        f"session_lifetime_minutes={lifetime!r})\n",
        encoding="utf-8")


def _fake_due_app(apps_root, name, lifetime=None):
    """An app that has never been run - appload reports it as due on any
    date, regardless of what "today" happens to be when the test runs."""
    app_dir = apps_root / name
    app_dir.mkdir(parents=True)
    (app_dir / f"{name}_docs.py").write_text("", encoding="utf-8")
    if lifetime is not None:
        _write_storage(app_dir, lifetime)
    out = app_dir / "out"
    out.mkdir()
    (app_dir / "config.json").write_text(
        json.dumps({"output_dir": str(out)}), encoding="utf-8")
    return app_dir


def test_api_due_orders_the_perishable_account_first(monkeypatch, tmp_path):
    apps_root = tmp_path / "apps"
    apps_root.mkdir()
    # Named so that alphabetical order would put the patient one first -
    # the assertion below only passes if due.plan's own ordering, not
    # directory order, is what actually won.
    _fake_due_app(apps_root, "aaa_patient", lifetime=None)
    _fake_due_app(apps_root, "zzz_perishable", lifetime=10)

    monkeypatch.setattr(panel, "APPS_ROOT", apps_root)
    monkeypatch.delenv("PAPERPULL_CONFIG_ROOT", raising=False)

    payload = panel.api_due()
    assert payload["today"] == date.today().isoformat()
    assert [a["app"] for a in payload["due"]] == \
        ["zzz_perishable", "aaa_patient"]


def test_api_due_route_carries_the_origin_guard():
    guarded = {getattr(r, "path", ""): any(
                   d.dependency is panel._same_origin_only
                   for d in getattr(r, "dependencies", []))
               for r in panel.app.routes}
    assert guarded["/api/due"] is True


def test_api_due_degrades_instead_of_exploding_when_core_is_unimportable(
        monkeypatch):
    """paperpull_core really is importable in this dev checkout, so the
    "not installed" case is simulated the standard way: sys.modules[name] =
    None makes the very next import of that name raise ImportError, exactly
    as it would if the package genuinely were missing (a native install
    running from gui/requirements.txt alone). monkeypatch restores whatever
    was there afterwards."""
    monkeypatch.setitem(sys.modules, "paperpull_core", None)
    with pytest.raises(HTTPException) as e:
        panel.api_due()
    assert e.value.status_code == 503
