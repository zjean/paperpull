"""/api/state: the one request the page draws itself from.

The panel used to answer "which flag do you want to pass to which app?" and
answered it with two dropdowns over eighteen apps. What a person actually
opens it to find out is what needs them - and nothing on screen said: an
account's state was a suffix inside an <option>, so learning it meant
selecting all eighteen apps in turn.

/api/state is what replaced that: one flat, already-ordered list of accounts,
each carrying which bucket it is in and why. The ordering and the bucketing
are computed server-side on purpose - they are a rule about this domain
rather than a drawing decision, they have to be the same for the sidebar and
the detail view next to it, and here they can be tested in Python instead of
in a browser.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as panel  # noqa: E402


# -- the buckets ------------------------------------------------------------


@pytest.mark.parametrize("state,gated,identified,due,expected", [
    ("parked", False, True, False, "signin"),
    ("parked", True, False, True, "signin"),    # parked outranks everything
    ("", True, False, False, "confirm"),
    ("warm", True, True, True, "due"),
    ("warm", False, False, True, "due"),
    ("warm", False, False, False, "ok"),
])
def test_the_bucket_an_account_lands_in(state, gated, identified, due, expected):
    assert panel._needs({"configured": True, "state": state,
                         "identified": identified},
                        gated=gated, due=due) == expected


def test_an_unconfigured_account_is_bucketed_ahead_of_every_other_question():
    """Every other test in _needs is about a file that lives under the
    output_dir the missing config would have named, so none of them can have
    an answer - see _needs. This account used to read as "Nothing to do"."""
    assert panel._needs({"configured": False, "state": "parked",
                         "identified": False}, gated=True, due=True) == "setup"


def test_every_bucket_has_a_stamp_and_a_reason():
    """The page prints both verbatim: the stamp beside the account's name and
    the sentence under it. A bucket with neither would draw a blank badge and
    an empty paragraph, which is how "unidentified" used to appear on sixteen
    apps that had nothing wrong with them."""
    for name, spec in panel.NEEDS.items():
        assert spec["stamp"], name
        assert spec["why"].endswith("."), name


# -- every button says what it does ----------------------------------------
#
# This is the regression this whole redesign exists to prevent. The panel used
# to offer six identically-styled buttons reading Login / Discover / Pilot /
# Run All / Resume / Verify, with the explanation of what any of them meant in
# a wall of paragraphs somewhere underneath - and "Pilot" explained nowhere on
# the page at all. A new action added without a blurb would put the panel
# straight back there, so it fails here instead.


def test_no_action_ships_as_a_bare_verb():
    for key, spec in panel.ACTIONS.items():
        assert spec["label"], key
        blurb = spec["blurb"]
        assert blurb.endswith("."), f"{key}: a blurb is a sentence"
        assert len(blurb.split()) >= 6, f"{key}: {blurb!r} explains nothing"


def test_the_ordinary_path_is_numbered_once_each():
    """`step` is what the page numbers 1, 2, 3 - and it numbers them because
    they are a real sequence (sign in, try a few, take the lot), not as
    decoration. Two actions claiming the same step would print two 2s."""
    steps = sorted(s["step"] for s in panel.ACTIONS.values()
                   if s.get("step") is not None)
    assert steps == list(range(1, len(steps) + 1))


def test_login_says_something_different_when_the_browser_is_elsewhere(monkeypatch):
    """With a remote browser there is no window for the panel to open, so
    "Opens this provider in a browser" would be a plain lie - it attaches to
    the browser you already signed in on. The page used to carry both
    wordings as a hand-written paragraph it rewrote in JS."""
    monkeypatch.setenv("PAPERPULL_REMOTE_BROWSER", "1")
    remote = panel._action_text(remote=True)["login"]["blurb"]
    native = panel._action_text(remote=False)["login"]["blurb"]
    assert remote != native
    assert "browser desktop" in remote


# -- the ordering -----------------------------------------------------------


def _row(app, account="primary", needs="ok", lifetime=None):
    return {"app": app, "account": account, "needs": needs,
            "session_lifetime_minutes": lifetime}


def test_accounts_are_ordered_by_what_can_be_done_about_them():
    """The first row is the one to open, so the page opens it by default.
    Most-actionable first, and within a bucket most-perishable first - the
    same reason paperpull_core.due sorts that way: an account whose session
    dies in ten minutes cannot queue behind nine that last for days."""
    rows = [_row("z", needs="ok"), _row("a", needs="due", lifetime=None),
            _row("b", needs="due", lifetime=10), _row("c", needs="signin"),
            _row("d", needs="confirm")]
    rows.sort(key=lambda r: (
        panel.NEEDS[r["needs"]]["rank"],
        panel._PATIENT if r["session_lifetime_minutes"] is None
        else int(r["session_lifetime_minutes"]),
        r["app"], r["account"]))
    assert [r["app"] for r in rows] == ["c", "d", "b", "a", "z"]


def test_api_state_orders_and_buckets_the_real_apps():
    """End to end against whatever this checkout actually has under
    APPS_ROOT: the list is non-empty, every row carries a known bucket, and
    it comes back in bucket order."""
    state = panel.api_state()
    accounts = state["accounts"]
    if not accounts:
        pytest.skip("no apps under this APPS_ROOT")
    ranks = [panel.NEEDS[a["needs"]]["rank"] for a in accounts]
    assert ranks == sorted(ranks)
    for a in accounts:
        assert a["needs"] in panel.NEEDS
        assert a["supported_actions"], a["app"]


def test_api_state_says_when_it_cannot_tell_what_is_due(monkeypatch):
    """Without paperpull_core there is no due list, and an empty one would
    read as "nothing is due" - a different and false claim from "this panel
    cannot tell you". The page prints a notice for exactly this flag; getting
    it wrong would show every account as "Nothing to do" with no explanation.
    """
    monkeypatch.setattr(panel, "_core", lambda: None)
    state = panel.api_state()
    assert state["due_known"] is False
    assert all(a["needs"] != "due" for a in state["accounts"])


def test_a_missing_core_still_draws_the_account_list(monkeypatch):
    """The account list is the one thing the panel must always manage - a
    native install only promises gui/requirements.txt, and everything the
    core adds here (how perishable a session is, what the newest document
    was) is an enrichment, not a prerequisite."""
    monkeypatch.setattr(panel, "_core", lambda: None)
    state = panel.api_state()
    if not panel.discover_apps():
        pytest.skip("no apps under this APPS_ROOT")
    assert state["accounts"]
    assert all(a["session_lifetime_minutes"] is None
               for a in state["accounts"])


def test_api_state_carries_the_origin_guard():
    """It describes every account on this machine, and the page it feeds can
    start runs, so it is behind the same check as everything else."""
    guarded = {getattr(r, "path", ""): any(
                   d.dependency is panel._same_origin_only
                   for d in getattr(r, "dependencies", []))
               for r in panel.app.routes}
    assert guarded["/api/state"] is True


# -- the page's own files ---------------------------------------------------


def test_the_page_is_three_files_and_all_three_are_served():
    """The page moved out of a string in app.py into static/. A route that
    reads a file that is not there fails at request time, on the one page the
    panel has - so it is checked here, where it is cheap."""
    for name in ("index.html", "panel.css", "panel.js"):
        assert (panel.STATIC / name).is_file(), name


def test_the_page_carries_its_own_icon():
    """Inline, because there is no /favicon.ico route - the browser asks for
    one on every page load and logged a 404 in the console on every one. A
    data: URI also keeps the promise that this page fetches nothing from
    anywhere: a panel driving signed-in financial accounts should make no
    outbound request at all, and an icon file is still a request."""
    assert 'rel="icon"' in panel.HTML
    assert "data:image/svg+xml" in panel.HTML
    # No route serves it, and none should have to.
    assert not any(getattr(r, "path", "") == "/favicon.ico"
                   for r in panel.app.routes)


def test_the_page_asks_for_the_stylesheet_and_the_script_it_needs():
    assert '/panel.css' in panel.HTML
    assert '/panel.js' in panel.HTML


def test_the_version_reaches_the_page():
    """__VERSION__ is substituted at request time; a rename on either side
    would leave the placeholder itself on screen."""
    assert "__VERSION__" in panel.HTML
    assert "__VERSION__" not in panel.index().body.decode()
    assert panel.VERSION in panel.index().body.decode()


def test_an_edited_page_needs_no_restart(tmp_path, monkeypatch):
    """_asset re-reads per request on purpose: the panel is a long-running
    local process and reloading the tab is the natural way to see a change.

    Against a temporary directory rather than the shipped file. The first
    version of this test edited gui/static/panel.css in place and restored it,
    which passed on a laptop and failed in the image with a PermissionError -
    /app is read-only there, and NOT writing into the app directories is
    precisely what lets the container run as whatever uid you like (see
    _config_root). A test may not be the one thing that needs /app writable.

    Two different contents, not one edit, so this fails if the route ever goes
    back to serving an import-time snapshot.
    """
    monkeypatch.setattr(panel, "STATIC", tmp_path)
    (tmp_path / "panel.css").write_text("/* first */\n", encoding="utf-8")
    assert "/* first */" in panel.panel_css().body.decode()
    (tmp_path / "panel.css").write_text("/* second */\n", encoding="utf-8")
    body = panel.panel_css().body.decode()
    assert "/* second */" in body
    assert "/* first */" not in body


def test_the_page_is_not_cached():
    """A stale cached copy of the panel against a restarted server is a
    confusing failure - the page draws itself from /api/state, so an old
    script against a new payload shows wrong facts rather than an error."""
    for response in (panel.index(), panel.panel_css(), panel.panel_js()):
        assert response.headers["Cache-Control"] == "no-store"


# -- what the page is handed about one account -----------------------------


def test_a_row_carries_everything_one_account_needs_drawn():
    """The page makes no second request per account - selecting a row draws
    from what is already in hand - so a field missing here is a blank on
    screen with no error anywhere."""
    accounts = panel.api_state()["accounts"]
    if not accounts:
        pytest.skip("no apps under this APPS_ROOT")
    expected = {"app", "account", "needs", "state", "last_alive",
                "parked_reason", "identity_gated", "identified", "anchors",
                "session_lifetime_minutes", "newest_document_date",
                "last_checked_date", "supported_actions", "has_venv"}
    assert expected <= set(accounts[0])


def test_only_an_app_that_accepts_adopt_identity_is_gated():
    """`identity_gated` is what the page believes about whether this account
    can be confirmed at all, and it is derived from the script's own flags
    rather than from a second list that could drift (see _supported_actions).
    """
    for a in panel.api_state()["accounts"]:
        assert a["identity_gated"] == ("adopt" in a["supported_actions"]), a["app"]


def test_the_anchors_themselves_reach_the_page_not_just_a_boolean():
    """Confirming an identity is the one moment a run takes on trust that the
    tab it can see really is this config's account, so what was recorded has
    to be checkable by the person who recorded it. A boolean cannot be checked
    against anything."""
    rows = panel._account_rows(
        {"fake": {"supported_actions": ["login", "adopt"], "has_venv": True,
                  "accounts": [{"name": "primary", "state": "warm",
                                "last_alive": "", "parked_reason": "",
                                "identified": True,
                                "anchors": [{"id": "202508-1", "date": "2026-08-14"}]}]}},
        core=None)
    assert rows[0]["anchors"] == [{"id": "202508-1", "date": "2026-08-14"}]


def test_the_flat_list_keeps_every_account_of_every_app():
    """One row per app/account pair, not one per app - the multi-account case
    is the whole reason config.<name>.json exists."""
    apps = {
        "one": {"supported_actions": ["login"], "has_venv": True,
                "accounts": [{"name": "primary", "state": "", "last_alive": "",
                              "parked_reason": "", "identified": False,
                              "anchors": []},
                             {"name": "spouse", "state": "", "last_alive": "",
                              "parked_reason": "", "identified": False,
                              "anchors": []}]},
    }
    rows = panel._account_rows(apps, core=None)
    assert {(r["app"], r["account"]) for r in rows} == {
        ("one", "primary"), ("one", "spouse")}


def test_json_serialisable_end_to_end():
    """It goes out as JSON; a Path or a date leaking in would 500 the one
    request the page cannot do without."""
    json.dumps(panel.api_state())
