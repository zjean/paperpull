"""What an outsider has to work out about an app, and gets one answer to.

Three questions used to be answered independently by gui/app.py and
tools/schedule.py, and every pair drifted: which file to launch, which
interpreter to launch it on, and where this provider's session locks live.
The last one is the dangerous one - two callers computing two different lock
directories never contend for the same slot, so the lock silently guards
nothing.

`accounts()` itself is covered in test_due.py, next to the `due.plan` it
feeds; what belongs here is the derivations.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperpull_core import appload


# -- entry script -----------------------------------------------------------


def test_the_entry_script_is_the_downloader_not_its_neighbours(tmp_path):
    for name in ("storage.py", "simyo_site.py", "conftest.py",
                 "simyo_docs.py"):
        (tmp_path / name).write_text("", encoding="utf-8")
    assert appload.entry_script(tmp_path).name == "simyo_docs.py"


def test_a_receipts_app_is_an_app_too(tmp_path):
    (tmp_path / "gap_receipts.py").write_text("", encoding="utf-8")
    assert appload.entry_script(tmp_path).name == "gap_receipts.py"


def test_a_directory_with_no_downloader_is_not_an_app(tmp_path):
    (tmp_path / "storage.py").write_text("", encoding="utf-8")
    assert appload.entry_script(tmp_path) is None


# -- which interpreter ------------------------------------------------------


def test_an_app_with_its_own_venv_is_launched_on_it(tmp_path):
    """The documented native setup. playwright and paperpull_core are in
    there and nowhere else, so launching on the caller's interpreter fails
    on the app's very first import."""
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").write_text("", encoding="utf-8")
    assert appload.python_for(tmp_path) == str(bin_dir / "python")


def test_an_app_with_no_venv_falls_back_to_the_caller(tmp_path):
    """The container: one image, one interpreter, everything installed."""
    assert appload.python_for(tmp_path) == sys.executable
    assert appload.venv_python(tmp_path) is None


# -- where the locks live ---------------------------------------------------


def test_the_lock_dir_does_not_depend_on_a_per_account_output_dir(tmp_path,
                                                                 monkeypatch):
    """The whole point. `output_dir` is per account and chosen by the user;
    a per-PROVIDER invariant cannot be read off one, which is how the panel
    and the CLI ended up guarding two different directories."""
    monkeypatch.delenv("PAPERPULL_DATA_ROOT", raising=False)
    app_dir = tmp_path / "apps" / "simyo"
    app_dir.mkdir(parents=True)
    assert appload.lock_dir(app_dir) == app_dir / ".locks"


def test_the_data_root_wins_when_it_is_set(tmp_path, monkeypatch):
    """In the container every app's state is on one volume, and that volume
    is the one thing the panel, the scheduler and a one-off `docker compose
    run` all see identically."""
    monkeypatch.setenv("PAPERPULL_DATA_ROOT", str(tmp_path / "data"))
    assert appload.lock_dir(tmp_path / "apps" / "simyo") == \
        tmp_path / "data" / ".locks"


def test_two_providers_sharing_one_lock_dir_do_not_share_a_slot(tmp_path,
                                                               monkeypatch):
    """One directory for every provider is safe because locks._lock_path
    puts the provider's slug in the filename."""
    from paperpull_core import locks

    monkeypatch.setenv("PAPERPULL_DATA_ROOT", str(tmp_path))
    directory = appload.lock_dir(tmp_path / "apps" / "simyo")
    locks.acquire(directory, "simyo", 1, "primary")
    # Would raise ProviderBusy if the slug were not part of the path.
    assert locks.acquire(directory, "youfone", 1, "primary").path.exists()
