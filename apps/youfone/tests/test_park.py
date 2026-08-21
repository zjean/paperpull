"""No signed-in tab, in --unattended mode, parks - it does not fail.

In the Docker layout, "no Youfone tab open" is the single most likely reason
a scheduled run finds nothing: the human simply hasn't signed in yet today.
That is the ordinary case, not a failure, so App.page() parks instead of
raising the SystemExit an interactive run still gets. See App.page() and
App._park() in youfone_docs.py.

No real browser is used here: App.browser() is monkeypatched away (it would
otherwise try a real CDP connection) and site.find_signed_in_page is
monkeypatched to answer "no tab found" - the one fact this test is about.

Ported from apps/simyo/tests/test_park.py.
"""
import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import youfone_docs
from storage import sentinel


def _app(tmp_path, unattended: bool) -> youfone_docs.App:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "output_dir": str(tmp_path / "output"),
        "cdp_url": "http://localhost:9999",  # never dialled: browser() is stubbed out
    }), encoding="utf-8")
    args = argparse.Namespace(config=str(config_path), unattended=unattended)
    app = youfone_docs.App(args)
    app.browser = lambda: object()  # stand-in context; find_signed_in_page ignores it
    return app


def test_unattended_with_no_signed_in_tab_parks(tmp_path, monkeypatch):
    monkeypatch.setattr(youfone_docs.site, "find_signed_in_page", lambda ctx: None)
    app = _app(tmp_path, unattended=True)

    with pytest.raises(youfone_docs.Parked):
        app.page()

    assert sentinel.session_state(app.sentinel) == sentinel.PARKED


def test_interactive_with_no_signed_in_tab_still_raises_systemexit(tmp_path, monkeypatch):
    """The interactive path is untouched: with no one to park for, a missing
    tab is still reported the same way it always was."""
    monkeypatch.setattr(youfone_docs.site, "find_signed_in_page", lambda ctx: None)
    app = _app(tmp_path, unattended=False)

    with pytest.raises(SystemExit):
        app.page()

    assert sentinel.session_state(app.sentinel) != sentinel.PARKED
