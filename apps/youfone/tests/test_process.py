"""Two decisions inside process(), and the fourth "a human is needed" case.

Both process() decisions are one line each and both were argued for in a
comment without anything holding them there:

  * `check_session` runs BEFORE `ensure_identity`. A dead session makes
    collect_documents come back empty, which identity.py reads as UNKNOWN -
    the wrong diagnosis ("I cannot tell which account this is") for the wrong
    problem ("you are signed out"), down the wrong exit path.
  * `except Parked: raise` sits before the generic `except Exception`. A park
    is not a per-document failure; swallowed into the failure counter it would
    be re-diagnosed identically for every remaining document, and the run
    would exit non-zero instead of 0.

The third test covers the identity refusal itself, which under --unattended
now parks like the other three human-needed conditions instead of exiting 1.

Ported from apps/simyo/tests/test_process.py. Unlike Simyo, ensure_identity's
`site.collect_documents` call walks Youfone's subscription tabs by clicking
router links rather than making one GET - but that difference is inside
youfone_site.py, not in any of these decisions, so the tests translate as-is.
"""
import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import youfone_docs
from storage import sentinel


def _app(tmp_path, unattended: bool = False) -> youfone_docs.App:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "output_dir": str(tmp_path / "output"),
        "cdp_url": "http://localhost:9999",   # never dialled
    }), encoding="utf-8")
    args = argparse.Namespace(config=str(config_path), unattended=unattended,
                              redownload=False, max_docs=None, dry_run=False,
                              type=None, year=None, start_date=None,
                              end_date=None, adopt_identity=False)
    app = youfone_docs.App(args)
    app.browser = lambda: object()
    return app


# -- the order the page is judged in ---------------------------------------


def test_the_session_is_checked_before_the_identity(tmp_path, monkeypatch):
    """--resume reaches process() without cmd_discover ever having run, so
    whichever of these two comes first is the one that gets to diagnose a
    dead session."""
    app = _app(tmp_path)
    seen = []
    monkeypatch.setattr(app, "page", lambda: object())
    monkeypatch.setattr(app, "check_session", lambda page: seen.append("session"))
    monkeypatch.setattr(app, "ensure_identity",
                        lambda page, docs=None: seen.append("identity"))

    app.process([])

    assert seen == ["session", "identity"]


def test_a_dead_session_on_the_resume_path_parks_rather_than_misreporting(
        tmp_path, monkeypatch):
    """The consequence of that order, unstubbed: the real check_session runs
    first, sees a signed-out page, and parks - so identity.py is never asked
    to explain an empty document list it has no way to explain."""
    app = _app(tmp_path, unattended=True)
    monkeypatch.setattr(app, "page", lambda: object())
    monkeypatch.setattr(youfone_docs.site, "detect_security_challenge",
                        lambda page: "")
    monkeypatch.setattr(youfone_docs.site, "looks_signed_out", lambda page: True)
    monkeypatch.setattr(app, "ensure_identity",
                        lambda page, docs=None: pytest.fail(
                            "identity was asked about a signed-out page"))

    with pytest.raises(youfone_docs.Parked):
        app.process([])
    assert sentinel.session_state(app.sentinel) == sentinel.PARKED


# -- a park is not a per-document failure ----------------------------------


def test_a_park_mid_run_stops_the_run_instead_of_counting_as_a_failure(
        tmp_path, monkeypatch):
    """The session died on document one. Every remaining document would only
    repeat the same diagnosis, and main() turns a Parked into one printed line
    and exit 0 - which the generic handler below it would have turned into a
    failure count and a non-zero exit."""
    app = _app(tmp_path)
    monkeypatch.setattr(app, "page", lambda: object())
    monkeypatch.setattr(app, "check_session", lambda page: None)
    monkeypatch.setattr(app, "ensure_identity", lambda page, docs=None: None)
    monkeypatch.setattr(app, "_delay", lambda factor=1.0: None)

    def die(page, doc, filename):
        raise youfone_docs.Parked("signed out")

    monkeypatch.setattr(app, "download_one", die)
    docs = [youfone_docs.Document(title="Factuur 1", category="Statement",
                                  date="2026-06-01"),
            youfone_docs.Document(title="Factuur 2", category="Statement",
                                  date="2026-07-01")]

    with pytest.raises(youfone_docs.Parked):
        app.process(docs)

    assert app.stats["failed"] == 0


def test_a_real_error_on_one_document_is_still_only_that_document(
        tmp_path, monkeypatch):
    """Negative control: the generic handler still does its job, so the test
    above is about Parked specifically and not about process() no longer
    catching anything."""
    app = _app(tmp_path)
    monkeypatch.setattr(app, "page", lambda: object())
    monkeypatch.setattr(app, "check_session", lambda page: None)
    monkeypatch.setattr(app, "ensure_identity", lambda page, docs=None: None)
    monkeypatch.setattr(app, "_delay", lambda factor=1.0: None)
    monkeypatch.setattr(app, "download_one",
                        lambda page, doc, filename: (_ for _ in ()).throw(
                            RuntimeError("PDF was HTML")))
    docs = [youfone_docs.Document(title="Factuur 1", category="Statement",
                                  date="2026-06-01"),
            youfone_docs.Document(title="Factuur 2", category="Statement",
                                  date="2026-07-01")]

    app.process(docs)      # no exception: both documents were attempted

    assert app.stats["failed"] == 2


# -- the fourth human-needed condition -------------------------------------


def test_an_unprovable_identity_parks_when_unattended(tmp_path):
    """No anchors recorded and nothing observed: identity.check says UNKNOWN
    and identity.action says REFUSE. All four "a human is needed" conditions
    now behave the same way unattended, and this is the only one that can
    never fix itself - nothing a scheduler does will ever adopt an identity -
    so exiting 1 paged someone nightly, forever, and recorded nothing."""
    app = _app(tmp_path, unattended=True)

    with pytest.raises(youfone_docs.Parked):
        app.ensure_identity(page=object(), docs=[])

    assert sentinel.session_state(app.sentinel) == sentinel.PARKED
    assert "identity" in app.sentinel.get(sentinel.SESSION_KEY)["parked_reason"]


def test_an_unprovable_identity_still_refuses_loudly_when_a_human_is_there(
        tmp_path):
    """Interactive runs are untouched: a person can act on this, and the
    message tells them exactly what to run (--adopt-identity, once)."""
    app = _app(tmp_path, unattended=False)

    with pytest.raises(SystemExit):
        app.ensure_identity(page=object(), docs=[])

    assert sentinel.session_state(app.sentinel) != sentinel.PARKED
