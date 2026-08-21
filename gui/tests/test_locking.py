"""The panel's half of "may this provider run now?", which has to agree with
the CLI's half exactly.

Two ways it did not, and both made the lock worse than useless:

  * the panel computed its lock directory from `output_dir`'s parent while the
    CLI computed a different one from the same config, so neither could see
    the other's lock and the guard silently guarded nothing;
  * the panel counted every lock file it found, with no staleness rule at all,
    while the CLI takes a lock over after STALE_AFTER. One Stop, or one closed
    browser tab, therefore refused that provider's Run button forever - while a
    terminal three feet away ran the same account fine.

Both are now one call each into paperpull_core, which is what these pin.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as panel  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))

from paperpull_core import appload, locks  # noqa: E402


def _app_with_spec(tmp_path, output_dir: str):
    """A fake app the panel accepts: entry script, an AppSpec it can load,
    and one account whose config carries the given output_dir."""
    app_dir = tmp_path / "apps" / "testco"
    app_dir.mkdir(parents=True)
    (app_dir / "testco_docs.py").write_text("", encoding="utf-8")
    (app_dir / "storage.py").write_text(
        "from pathlib import Path\n"
        "from paperpull_core.spec import AppSpec\n"
        "SPEC = AppSpec(provider='Testco', "
        "project_dir=Path(__file__).resolve().parent)\n",
        encoding="utf-8")
    (app_dir / "config.json").write_text(
        json.dumps({"output_dir": output_dir}), encoding="utf-8")
    return app_dir


@pytest.fixture(autouse=True)
def _no_data_root(monkeypatch):
    """The native layout: no /data volume, so the app's own directory is what
    both sides derive the lock directory from."""
    monkeypatch.delenv("PAPERPULL_DATA_ROOT", raising=False)
    monkeypatch.delenv("PAPERPULL_CONFIG_ROOT", raising=False)


# -- staleness --------------------------------------------------------------


def test_a_live_lock_makes_the_provider_busy(tmp_path):
    """Negative control for the test below: a lock that is genuinely held
    still has to refuse a second run, or nothing here guards anything."""
    app_dir = _app_with_spec(tmp_path, ".")
    locks.acquire(appload.lock_dir(app_dir), "testco", 1, "config.jane.json")
    assert panel._busy_holder(app_dir, "primary") == "config.jane.json"


def test_a_stale_lock_does_not_wedge_the_run_button(tmp_path):
    """The Critical. Nothing releases a slot when a process is killed outright,
    so the CLI ages the lock out after STALE_AFTER and takes it over. The
    panel has to apply the same rule or it refuses a run the CLI would grant,
    and keeps refusing it, because nothing ever deletes the file it reads.
    """
    app_dir = _app_with_spec(tmp_path, ".")
    claim = locks.acquire(appload.lock_dir(app_dir), "testco", 1, "dead")
    long_ago = (datetime.now() - locks.STALE_AFTER
                - timedelta(minutes=1)).isoformat(timespec="seconds")
    record = json.loads(claim.path.read_text(encoding="utf-8"))
    record["at"] = long_ago
    claim.path.write_text(json.dumps(record), encoding="utf-8")

    assert panel._busy_holder(app_dir, "primary") is None
    # And the CLI agrees, which is the point: the same file, the same verdict.
    assert locks.acquire(appload.lock_dir(app_dir), "testco", 1,
                         "live").path.exists()


def test_no_lock_at_all_is_not_busy(tmp_path):
    app_dir = _app_with_spec(tmp_path, ".")
    assert panel._busy_holder(app_dir, "primary") is None


# -- where the lock lives ---------------------------------------------------


@pytest.mark.parametrize("output_dir", [
    "ABSOLUTE",          # replaced below - an absolute path outside the app
    ".",                 # what all 18 shipped config.example.json files say
    "state/testco/out",  # nested, not a sibling: the case that broke Docker too
])
def test_the_panel_looks_where_the_cli_locks_whatever_output_dir_says(
        tmp_path, output_dir):
    """`output_dir` is per account and chosen by the user, so a per-provider
    lock directory can no longer be derived from it - both sides call
    appload.lock_dir(app_dir) instead (gui/app.py's _busy_holder, and
    simyo_docs.py's main). This proves the panel finds the CLI's lock for
    every shape of output_dir, including the two that used to miss it: a
    relative one (the panel resolved it against its own cwd) and a nested one
    (whose parent is not shared with the other accounts).
    """
    app_dir = _app_with_spec(tmp_path, output_dir)
    if output_dir == "ABSOLUTE":
        (app_dir / "config.json").write_text(
            json.dumps({"output_dir": str(tmp_path / "elsewhere")}),
            encoding="utf-8")

    locks.acquire(appload.lock_dir(app_dir), "testco", 1, "config.json")
    assert panel._busy_holder(app_dir, "primary") == "config.json"


def test_the_lock_dir_is_the_app_dir_not_a_config_field(tmp_path):
    """Stated as a fact rather than inferred: nothing about the account's
    config can move this, which is what makes it an invariant the panel, the
    scheduler and a shell can all be trusted to compute alike."""
    app_dir = _app_with_spec(tmp_path, ".")
    assert appload.lock_dir(app_dir) == app_dir / ".locks"
