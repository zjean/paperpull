"""A scheduled or sat-through run has to be able to download something new.

This is the regression that must never come back. `cmd_resume` selects from
the local `discovery.json` and never asks Simyo what exists - `cmd_discover`
is reached from `cmd_pilot` and `cmd_run` only. So while the scheduler ran
`--unattended --resume` and the panel's sitting ran the `resume` action,
neither could ever fetch an invoice that was not already recorded: the sitting
spent the human's sign-in, printed "Nothing to resume", and said "Sitting
done", and the scheduler had no working unattended path at all.

The tests walk the real chain rather than restating it: tools/schedule.py's own
argv, through this app's own parser, into `_dispatch`, and assert that
`cmd_discover` is reached. Nothing is downloaded and no browser is opened -
`page`, `check_session` and `process` are stubbed, and `_select` returns
nothing.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1]
REPO = APP_DIR.parents[1]
sys.path.insert(0, str(APP_DIR))

import simyo_docs
from storage import sentinel


def _schedule():
    """tools/schedule.py, loaded by path - tools/ is not a package."""
    spec = importlib.util.spec_from_file_location(
        "_pp_schedule", REPO / "tools" / "schedule.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _app(tmp_path, argv):
    """A real App on a throwaway config, and the parsed args behind it.

    No browser is ever dialled: `browser()` is replaced, and every method that
    would touch a page is stubbed by the caller.
    """
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "output_dir": str(tmp_path / "output"),
        "cdp_url": "http://localhost:9999",   # never dialled
    }), encoding="utf-8")
    args = simyo_docs.build_parser().parse_args(
        list(argv) + ["--config", str(config_path)])
    app = simyo_docs.App(args)
    app.browser = lambda: object()
    return app, args


def _trace(app, monkeypatch):
    """Record which of the interesting steps a command actually reaches."""
    seen = []
    page = object()
    monkeypatch.setattr(app, "page", lambda: page)
    monkeypatch.setattr(app, "check_session", lambda p: seen.append("session"))
    monkeypatch.setattr(app, "ensure_identity",
                        lambda p, docs=None: seen.append("identity"))
    monkeypatch.setattr(app, "cmd_discover",
                        lambda quiet=False: (seen.append("discover"), 0)[1])
    monkeypatch.setattr(app, "_select", lambda limit=None: [])
    monkeypatch.setattr(app, "process",
                        lambda docs, dry_run=False: seen.append("process"))
    return seen


# -- the scheduler ----------------------------------------------------------


def test_the_schedulers_own_argv_reaches_discover(tmp_path, monkeypatch):
    """The whole chain, end to end: the flags tools/schedule.py builds, parsed
    by this app, dispatched - and cmd_discover is reached. With
    `--unattended --resume` it was not, on any pass, ever."""
    schedule = _schedule()
    cmd = schedule.build_command(
        {"app": "simyo", "account": "primary", "config": "x"},
        APP_DIR, APP_DIR / "simyo_docs.py")
    flags = [f for f in cmd[2:] if f != "x" and f != "--config"]

    app, args = _app(tmp_path, flags)
    seen = _trace(app, monkeypatch)
    assert simyo_docs._dispatch(app, args) == 0
    assert "discover" in seen


def test_the_schedulers_own_argv_passes_the_unattended_guard(tmp_path):
    """main() refuses a flag combination that could block on a prompt, before
    App(args) exists. The scheduler's argv has to survive that check, or the
    fix above only moves the failure earlier.

    This calls main() itself rather than re-deriving main()'s own boolean
    expression and comparing it to itself, which is what this test used to
    do - a comparison that stays green even if the --all-with-yes admission
    were deleted from main(), because both sides of the assertion would
    change together. Routing a config path that is never created makes
    "admitted" and "refused" tell apart without a browser or a provider:
    a refused combination returns 2 before App(args) is ever built, so the
    missing config is never touched, while an admitted one falls through
    into App(args) and dies on load_config's own SystemExit instead of
    returning 2. The next reader may want to simplify this back to a plain
    boolean check - don't; that is the exact form that stopped catching the
    regression this file exists to prevent."""
    schedule = _schedule()
    cmd = schedule.build_command(
        {"app": "simyo", "account": "primary", "config": "x"},
        APP_DIR, APP_DIR / "simyo_docs.py")
    flags = [f for f in cmd[2:] if f not in ("x", "--config")]
    missing_config = tmp_path / "never-created" / "config.json"

    with pytest.raises(SystemExit):
        simyo_docs.main(flags + ["--config", str(missing_config)])


# -- the sitting's action, and the one it replaced --------------------------


def test_run_all_reaches_discover(tmp_path, monkeypatch):
    """What the panel's sitting now runs."""
    app, args = _app(tmp_path, ["--all", "--yes"])
    seen = _trace(app, monkeypatch)
    simyo_docs._dispatch(app, args)
    assert "discover" in seen


def test_resume_still_does_not_reach_discover(tmp_path, monkeypatch):
    """Documents the defect rather than the fix, so the reason --all was
    chosen stays visible: resume is still a local-only operation, on purpose -
    it is for continuing an interrupted run, not for finding new documents."""
    app, args = _app(tmp_path, ["--resume", "--yes"])
    seen = _trace(app, monkeypatch)
    simyo_docs._dispatch(app, args)
    assert "discover" not in seen


# -- a resume that finds nothing still checked the session -----------------


def test_resume_with_nothing_to_do_still_records_a_live_session(
        tmp_path, monkeypatch):
    """cmd_resume used to return before process(), so check_session never ran,
    so mark_warm never ran, so `last_verified_alive` was never written - and
    due.py kept the account due tomorrow and every day after. The sitting then
    asked a person to sign in daily, forever, to do nothing. A session
    verified alive is a fact whether or not there was work behind it."""
    app, _ = _app(tmp_path, ["--resume", "--yes"])
    monkeypatch.setattr(app, "page", lambda: object())
    monkeypatch.setattr(simyo_docs.site, "detect_security_challenge",
                        lambda page: "")
    monkeypatch.setattr(simyo_docs.site, "looks_signed_out", lambda page: False)

    app.cmd_resume()   # nothing discovered, so nothing to resume

    assert sentinel.session_state(app.sentinel) == sentinel.WARM
    assert sentinel.last_verified_alive(app.sentinel)


def test_a_resume_that_finds_a_dead_session_parks_instead(
        tmp_path, monkeypatch):
    """The other half: liveness is recorded because it was checked, not
    assumed. A dead session on this path has to park, exactly as it does
    inside process()."""
    app, _ = _app(tmp_path, ["--unattended", "--resume", "--yes"])
    monkeypatch.setattr(app, "page", lambda: object())
    monkeypatch.setattr(simyo_docs.site, "detect_security_challenge",
                        lambda page: "")
    monkeypatch.setattr(simyo_docs.site, "looks_signed_out", lambda page: True)

    with pytest.raises(simyo_docs.Parked):
        app.cmd_resume()
    assert sentinel.session_state(app.sentinel) == sentinel.PARKED
