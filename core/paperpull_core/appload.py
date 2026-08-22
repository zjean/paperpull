"""Read an app's declared facts and its accounts' state, from outside the app.

A scheduler has to know two things no single app can tell it: what every app
declares about itself, and what every account of every app has on disk. Both
are already recorded - the first in each app's `storage.py` SPEC, the second
under each config's `output_dir` - so nothing new is stored to answer this.

Everything an outsider has to work out ABOUT an app lives here, and only
here: which file is its entry script, which interpreter runs it, and where
its session locks live. Each of those was independently rederived by
gui/app.py and tools/schedule.py, and each pair drifted - the panel launched
an app on its own venv while the scheduler used `sys.executable`, and the two
sides computed two different lock directories and so never contended for the
same slot. A rule that has to agree in three places belongs in one of them.

Loading those SPECs takes one piece of care. Every app has a module called
`storage.py`, so a plain `import storage` would hand back whichever one was
imported first for all of them. They are loaded here under distinct module
names via importlib instead. Each one calls `paperpull_core.storage.bind()` on
import, which is global and last-writer-wins - fine, because only the returned
SPEC object is used and nothing here goes on to file a document.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import List, Optional

from . import sentinel

# An app directory is one that has an entry script - gui/app.py's own test for
# "is this a downloader" - not one that happens to have a storage.py. A fake
# or stripped-down app tree (this module's own test, a throwaway fixture) may
# have neither storage.py nor a venv yet and must still be recognised.
ENTRY_RE = re.compile(r".*_(receipts|docs)\.py$")

# Windows puts a venv's interpreter in .venv\Scripts\python.exe; macOS and
# Linux use .venv/bin/python (or bin/python3).
VENV_PYTHONS = ("Scripts/python.exe", "bin/python", "bin/python3")


def entry_script(app_dir) -> Optional[Path]:
    """This app's run orchestrator - `<provider>_docs.py` or `_receipts.py`.

    Public, and deliberately the only answer: whether a directory is an app
    at all, and which file to launch, must be the same question everywhere
    that asks it. Sorted, so an app that somehow carried two matching names
    still resolves to one deterministic script rather than to whatever the
    filesystem happened to hand back first.
    """
    for path in sorted(Path(app_dir).glob("*.py")):
        if ENTRY_RE.match(path.name):
            return path
    return None


def venv_python(app_dir) -> Optional[Path]:
    """This app's own interpreter, if it has one."""
    for rel in VENV_PYTHONS:
        candidate = Path(app_dir) / ".venv" / rel
        if candidate.exists():
            return candidate
    return None


def python_for(app_dir) -> str:
    """The interpreter an app must be launched on.

    The documented native setup is a venv per app (each app's own setup.bat /
    setup.command), holding playwright and paperpull_core. Launching an app on
    the *caller's* interpreter instead - the panel's, or the scheduler's -
    finds neither, so every scheduled run failed on `import playwright` before
    it reached a single line of the app. Falling back to `sys.executable` is
    right only where there is no venv, which is the container: one image, one
    interpreter, everything already installed.
    """
    venv = venv_python(app_dir)
    return str(venv) if venv else sys.executable


def lock_dir(app_dir) -> Path:
    """Where this provider's session slots live.

    Every caller has to land on the same directory or the lock does nothing:
    the panel, the scheduler and a shell all guard a slot the others cannot
    see, and the one failure mode this whole feature exists to prevent - two
    live sessions of a provider that tolerates one - happens silently.

    So it is derived from facts that cannot differ between callers or between
    an app's accounts: `PAPERPULL_DATA_ROOT` when it is set (that is `/data`,
    the container's own state volume), and otherwise the app's own directory,
    which is where a native install already keeps its state.

    It is deliberately NOT derived from `output_dir`. That is a per-account,
    user-chosen path, and a per-provider invariant cannot be read off one: the
    previous rule (`<output_dir>/..`) held only while every account of a
    provider sat in sibling directories, and quietly broke for anyone who
    pointed a second account somewhere nested instead - in exactly the
    multi-account case this is here to serve.

    One directory serving every provider is fine: `locks._lock_path` names
    each file `<slug>.<index>.lock`, so the provider is in the filename and
    two providers never share a slot.
    """
    root = os.environ.get("PAPERPULL_DATA_ROOT", "").strip()
    return (Path(root) if root else Path(app_dir)) / ".locks"


def load_spec(app_dir: Path):
    """The AppSpec an app declares in its own storage.py."""
    app_dir = Path(app_dir)
    storage_py = app_dir / "storage.py"
    if not storage_py.is_file():
        raise FileNotFoundError(storage_py)
    module_name = f"_pp_spec_{app_dir.name}"
    spec = importlib.util.spec_from_file_location(module_name, storage_py)
    module = importlib.util.module_from_spec(spec)
    # The app's storage.py imports its own package-local names, so its
    # directory has to be importable while it executes - and must not be left
    # on sys.path afterwards, or the next app's storage.py would find this
    # one's siblings.
    sys.path.insert(0, str(app_dir))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(app_dir))
        sys.modules.pop(module_name, None)
    return module.SPEC


def _newest_document_date(progress_json: Path) -> str:
    """The date of the newest document this account has ever recorded."""
    try:
        data = json.loads(progress_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    # Every record this file has ever held is a dict {"date": ...}. A record
    # that is not - a hand-edited file, a partial write - is skipped rather
    # than trusted, the same way an unparsable date already is below.
    dates = [str(rec.get("date") or "").strip()
             for rec in data.values() if isinstance(rec, dict)]
    dates = [d for d in dates if d]
    return max(dates) if dates else ""


def _config_files(app_dir: Path, config_root: Optional[Path]):
    """(account label, config path) for every account of this app."""
    cfg_dir = Path(config_root) / app_dir.name if config_root else app_dir
    if not cfg_dir.is_dir():
        return []
    found = []
    primary = cfg_dir / "config.json"
    if primary.is_file():
        found.append(("primary", primary))
    for path in sorted(cfg_dir.glob("config.*.json")):
        if path.name == "config.example.json":
            continue
        found.append((path.name[len("config."):-len(".json")], path))
    return found


def session_record(output_dir) -> dict:
    """The `session` block of this account's sentinel.json, or {}.

    Shared with tools/schedule.py, which asks the same question about a single
    account the moment its run finishes. It has to ask, because the run's exit
    code cannot answer it: a parked run and a clean run both exit 0, on
    purpose, so that cron alerting stays worth reading.

    Every failure is {} rather than an exception. One account with an
    unreadable sentinel must not end a pass that has fifteen others in it.
    """
    try:
        record = json.loads(
            (Path(output_dir) / "sentinel.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(record, dict):
        return {}
    session = record.get(sentinel.SESSION_KEY) or {}
    return session if isinstance(session, dict) else {}


def accounts(apps_root: Path, config_root: Optional[Path]) -> List[dict]:
    """Every app/account pair, shaped for paperpull_core.due.plan."""
    out: List[dict] = []
    apps_root = Path(apps_root)
    if not apps_root.is_dir():
        return out
    for app_dir in sorted(p for p in apps_root.iterdir() if p.is_dir()):
        if entry_script(app_dir) is None:
            continue
        try:
            spec = load_spec(app_dir)
            lifetime = spec.session_lifetime_minutes
        except Exception:
            # A provider whose spec will not load must not take the whole
            # schedule down; treat it as patient and let its own run fail
            # loudly if someone asks for it.
            lifetime = None
        for label, cfg_path in _config_files(app_dir, config_root):
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                print(f"appload: skipping {cfg_path} - unreadable",
                      file=sys.stderr)
                continue
            if not isinstance(cfg, dict):
                # Valid JSON, wrong shape (a list, a string, ...). This
                # account is dropped from the list entirely, which is a real
                # operational fact worth a line on stderr - a short listing
                # must not look like a complete one.
                print(f"appload: skipping {cfg_path} - not a JSON object",
                      file=sys.stderr)
                continue
            output_dir = Path(cfg.get("output_dir") or "")
            if not output_dir.is_absolute():
                # Every one of the 18 shipped config.example.json files says
                # "output_dir": ".". That is meaningful only to the downloader
                # itself, which is launched with its own app directory as the
                # working directory (gui/app.py's Popen, tools/schedule.py's
                # subprocess.run) - never to this module's callers, which are
                # long-lived processes with a cwd of their own (wherever
                # uvicorn or the scheduler started).
                #
                # Left unresolved, both files read below were looked for
                # somewhere they never are, and both misses look like facts:
                # no progress.json reads as "no documents ever", and no
                # sentinel.json reads as "never parked". So on a native
                # install every account was always due and none was ever
                # parked, while the panel's own account list - which does
                # resolve this - showed the opposite about the same account.
                output_dir = app_dir / output_dir
            session = session_record(output_dir)
            last_alive = str(session.get(sentinel.LAST_ALIVE_KEY) or "")
            out.append({
                "app": app_dir.name,
                "account": label,
                "config": str(cfg_path),
                "output_dir": str(output_dir),
                "session_lifetime_minutes": lifetime,
                "cadence_days": cfg.get("cadence_days"),
                "newest_document_date": _newest_document_date(
                    output_dir / "progress.json"),
                "last_checked_date": last_alive[:10],
                "parked": session.get(sentinel.STATE_KEY) == sentinel.PARKED,
                "parked_reason": str(
                    session.get(sentinel.PARKED_REASON_KEY) or ""),
            })
    return out
