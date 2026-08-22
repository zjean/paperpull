"""tools/schedule.py: the other thing that drives the app CLIs.

It lives in this suite because the scheduler and the panel are twins - same
image, same volumes, same commands, and the same two questions ("which
interpreter?", "which flags?") answered independently until they disagreed.
tools/ carries no suite of its own, and testing the pair side by side is what
makes a divergence visible.

Nothing here starts a real subprocess: `build_command` exists precisely so
the argv can be asserted on without running it, and running it would reach a
real provider. The one exception is `run_one`, the function that actually
calls `subprocess.run` - there, `subprocess.run` itself is replaced with a
stub, so the call can be inspected without a process ever existing.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "gui"))
sys.path.insert(0, str(REPO / "core"))

import app as panel  # noqa: E402


def _schedule():
    """tools/schedule.py, loaded by path - tools/ is not a package."""
    spec = importlib.util.spec_from_file_location(
        "_pp_schedule", REPO / "tools" / "schedule.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


schedule = _schedule()


def _account(app="simyo", account="primary"):
    return {"app": app, "account": account,
            "config": f"/config/{app}/config.json"}


# -- which flags ------------------------------------------------------------


def test_a_scheduled_run_asks_the_provider_what_exists(tmp_path):
    """The Critical. `--resume` selects from the discovery.json an account
    already has on disk and never calls cmd_discover, so a schedule built on
    it could not download a document nobody had seen yet - it had no working
    unattended path at all. `--all` discovers first; the app's own
    already-downloaded memory is what keeps a nightly pass to new documents
    only."""
    cmd = schedule.build_command(_account(), tmp_path,
                                 tmp_path / "simyo_docs.py")
    assert "--all" in cmd
    assert "--resume" not in cmd


def test_a_scheduled_run_answers_the_confirmation_nobody_is_there_to_type():
    """--all prompts for a typed YES, and simyo_docs.py's own --unattended
    guard refuses --all without --yes for exactly that reason."""
    assert "--yes" in schedule.UNATTENDED_FLAGS
    assert "--unattended" in schedule.UNATTENDED_FLAGS


def test_the_scheduler_and_the_panel_run_the_same_action(tmp_path):
    """The two drivers must not drift: whatever the panel's Run All does, a
    scheduled pass does, plus --unattended."""
    cmd = schedule.build_command(_account(), tmp_path,
                                 tmp_path / "simyo_docs.py")
    panel_flags = set(panel.ACTIONS["all"]["flags"])
    assert panel_flags.issubset(set(cmd))
    assert set(cmd) & {"--unattended"} == {"--unattended"}


def test_a_named_account_is_passed_its_own_config(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPERPULL_CONFIG_ROOT", raising=False)
    cmd = schedule.build_command(_account(account="jane"), tmp_path,
                                 tmp_path / "simyo_docs.py")
    assert cmd[-2:] == ["--config", "/config/simyo/config.json"]


def test_a_scheduled_run_cannot_inherit_a_terminal(tmp_path, monkeypatch):
    """run_one used to call subprocess.run with no stdin argument at all,
    which inherits this process's own stdin - a real terminal, if one is
    what started tools/schedule.py natively. That terminal is how a prompt
    (the app's own, or ensure_owner's isatty()-gated one in storage.py) stops
    being unreachable and starts being able to hang a scheduled run forever.
    Asserted on the call rather than on any prompt actually firing: a test
    that could reach a real input() is the wrong test even if it never does,
    because a hang here would hang the whole suite rather than fail it."""
    monkeypatch.delenv("PAPERPULL_CONFIG_ROOT", raising=False)
    # A fake entry script is enough: run_one only reads its text (looking for
    # "--unattended") before ever building or running a command.
    (tmp_path / "fake_docs.py").write_text(
        "ap.add_argument('--unattended')\n", encoding="utf-8")

    captured = {}

    def fake_run(cmd, cwd=None, stdin=None):
        captured["stdin"] = stdin
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(schedule.subprocess, "run", fake_run)
    code = schedule.run_one(_account(app=tmp_path.name), tmp_path.parent)
    assert code == 0
    assert captured["stdin"] is schedule.subprocess.DEVNULL


# -- which interpreter ------------------------------------------------------


def test_an_app_is_launched_on_its_own_venv(tmp_path, monkeypatch):
    """The documented native setup is a venv per app, holding playwright and
    paperpull_core. This used to be `sys.executable` - the scheduler's own
    interpreter, which has neither - while the panel got it right, so every
    native scheduled run died on the app's first import."""
    monkeypatch.delenv("PAPERPULL_CONFIG_ROOT", raising=False)
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").write_text("", encoding="utf-8")

    cmd = schedule.build_command(_account(), tmp_path,
                                 tmp_path / "simyo_docs.py")
    assert cmd[0] == str(bin_dir / "python")
    # And it is the same answer the panel reaches for the same directory.
    assert cmd[0] == panel._python_for(tmp_path)


def test_the_scheduler_finds_an_entry_script_the_same_way_the_panel_does(
        tmp_path):
    """One rule, one place. The scheduler's own copy preferred *_docs.py over
    *_receipts.py rather than sorting all matches, so a tree the panel and the
    core read one way it could read another."""
    (tmp_path / "gap_receipts.py").write_text("", encoding="utf-8")
    (tmp_path / "storage.py").write_text("", encoding="utf-8")
    from paperpull_core import appload
    assert appload.entry_script(tmp_path) == panel._entry_script(tmp_path)


# -- the schedule hour ------------------------------------------------------


@pytest.mark.parametrize("raw", ["0", "7", "23", " 7 "])
def test_a_real_hour_is_accepted(raw):
    assert 0 <= schedule.schedule_hour(raw) <= 23


@pytest.mark.parametrize("raw", ["25", "-1", "24", "700"])
def test_an_hour_outside_the_day_is_refused(raw):
    """int("25") parses fine and then never equals datetime.now().hour, so the
    daily pass silently never fired: a scheduler running forever, doing
    nothing, saying nothing."""
    with pytest.raises(ValueError):
        schedule.schedule_hour(raw)


@pytest.mark.parametrize("raw", ["", "seven", "7.5", None])
def test_a_malformed_hour_is_refused_with_a_sentence(raw):
    with pytest.raises(ValueError) as e:
        schedule.schedule_hour(raw)
    assert "PAPERPULL_SCHEDULE_HOUR" in str(e.value)


def test_a_bad_hour_exits_two_rather_than_tracebacking(monkeypatch, capsys):
    """Under `restart: unless-stopped` a traceback restart-loops with the
    reason scrolling past. Bad input is exit 2 here as everywhere else."""
    monkeypatch.setenv("PAPERPULL_SCHEDULE_HOUR", "25")
    assert schedule.main(["--once"]) == 2
    assert "PAPERPULL_SCHEDULE_HOUR" in capsys.readouterr().err


# -- what a run meant, and what gets said about it -------------------------

def _park_account(app="youfone", account="primary"):
    return {"app": app, "account": account, "output_dir": "/data/youfone",
            "config": "/config/youfone/config.json"}


def test_a_parked_run_is_something_a_person_must_act_on():
    schedule = _schedule()
    session = {"state": "parked", "parked_reason": "no signed-in tab"}
    assert schedule.outcome(_park_account(), 0, session) == (
        schedule.PARKED, "no signed-in tab")


def test_a_clean_run_says_nothing():
    """The exit code is 0 for both, which is why the session is consulted."""
    schedule = _schedule()
    assert schedule.outcome(_park_account(), 0, {"state": "warm"}) is None


def test_a_parked_run_with_no_reason_still_asks_for_a_person():
    schedule = _schedule()
    kind, why = schedule.outcome(_park_account(), 0, {"state": "parked"})
    assert kind == schedule.PARKED
    assert why


def test_being_terminated_is_not_an_error():
    """143 is the container being stopped, or someone pressing Stop."""
    schedule = _schedule()
    assert schedule.outcome(_park_account(), schedule.TERMINATED_EXIT, {}) is None


def test_a_busy_provider_is_reported():
    """Transient, but if it stops being transient a wedged lock means this
    account silently never runs again - which is what this exists to end."""
    schedule = _schedule()
    kind, why = schedule.outcome(_park_account(), 4, {})
    assert kind == schedule.ERROR
    assert "4" in why


def test_any_other_non_zero_exit_is_an_error():
    schedule = _schedule()
    assert schedule.outcome(_park_account(), 1, {})[0] == schedule.ERROR


def test_a_quiet_pass_has_nothing_to_send():
    schedule = _schedule()
    assert schedule.digest([], []) is None


def test_the_digest_names_each_account_and_why():
    schedule = _schedule()
    title, body, tags, priority = schedule.digest(
        [("youfone/primary", "no signed-in tab")],
        [("ally/primary", "exited 1")])
    assert "youfone/primary" in body and "no signed-in tab" in body
    assert "ally/primary" in body and "exited 1" in body
    assert "1" in title
    assert tags and priority


def test_the_digest_says_how_many_need_a_person():
    schedule = _schedule()
    title, _, _, _ = schedule.digest(
        [("a/one", "why"), ("b/two", "why")], [])
    assert "2" in title


def test_one_pass_files_each_account_where_it_belongs(monkeypatch):
    """outcome() and digest() are each tested in isolation, and every other
    test here replaces one_pass wholesale - so nothing exercises the wiring
    inside one_pass itself: which list an account's (who, why) actually lands
    in. Swap the two `.append` targets in one_pass and every other test in
    this file still passes while a park is reported as an error and vice
    versa; this is the one test that would catch that."""
    schedule = _schedule()

    clean = {"app": "ally", "account": "primary", "output_dir": "/data/ally",
             "session_lifetime_minutes": None}
    goes_parked = {"app": "youfone", "account": "primary",
                   "output_dir": "/data/youfone",
                   "session_lifetime_minutes": None}
    goes_error = {"app": "simyo", "account": "primary",
                  "output_dir": "/data/simyo",
                  "session_lifetime_minutes": None}
    perishable = {"app": "kpn", "account": "primary",
                  "output_dir": "/data/kpn", "session_lifetime_minutes": 10}
    accounts = [clean, goes_parked, goes_error, perishable]

    monkeypatch.setattr(schedule.appload, "accounts",
                         lambda apps_root, config_root: accounts)
    monkeypatch.setattr(schedule.due, "plan", lambda accounts, today: accounts)

    codes = {"ally": 0, "youfone": 0, "simyo": 1}
    monkeypatch.setattr(schedule, "run_one",
                         lambda account, apps_root: codes[account["app"]])

    sessions = {
        "/data/ally": {"state": "warm"},
        "/data/youfone": {"state": "parked",
                           "parked_reason": "no signed-in tab"},
        "/data/simyo": {},
    }
    monkeypatch.setattr(schedule.appload, "session_record",
                         lambda output_dir: sessions[output_dir])

    parked, errors = schedule.one_pass(Path("/nowhere"), None, "2026-08-22")

    assert ("youfone/primary", "no signed-in tab") in parked
    assert ("simyo/primary", "exited 1") in errors
    assert ("kpn/primary", "needs a person to sign in") in parked
    assert not any(who.startswith("ally/") for who, _ in parked)
    assert not any(who.startswith("ally/") for who, _ in errors)
    assert len(parked) == 2 and len(errors) == 1


def test_a_pass_that_blows_up_still_tells_someone(monkeypatch):
    """Otherwise the one failure that hides every other failure is this
    module's own."""
    schedule = _schedule()
    sent = []
    monkeypatch.setattr(schedule.notify, "send",
                        lambda *a, **k: sent.append((a, k)) or True)
    monkeypatch.setattr(schedule, "one_pass",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    code = schedule.pass_and_notify(Path("/nowhere"), None, "2026-08-22")
    assert code != 0
    assert sent, "a pass that raised sent nothing"
    assert "boom" in str(sent[0])


def test_a_quiet_pass_sends_nothing(monkeypatch):
    schedule = _schedule()
    sent = []
    monkeypatch.setattr(schedule.notify, "send",
                        lambda *a, **k: sent.append((a, k)) or True)
    monkeypatch.setattr(schedule, "one_pass", lambda *a, **k: ([], []))
    assert schedule.pass_and_notify(Path("/nowhere"), None, "2026-08-22") == 0
    assert sent == []
