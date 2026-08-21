"""Read an app's declared facts and its accounts' state, from outside the app.

A scheduler has to know two things no single app can tell it: what every app
declares about itself, and what every account of every app has on disk. Both
are already recorded - the first in each app's `storage.py` SPEC, the second
under each config's `output_dir` - so nothing new is stored to answer this.

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
import re
import sys
from pathlib import Path
from typing import List, Optional

# An app directory is one that has an entry script - gui/app.py's own test for
# "is this a downloader" - not one that happens to have a storage.py. A fake
# or stripped-down app tree (this module's own test, a throwaway fixture) may
# have neither storage.py nor a venv yet and must still be recognised.
ENTRY_RE = re.compile(r".*_(receipts|docs)\.py$")


def _entry_script(app_dir: Path) -> Optional[Path]:
    for p in sorted(app_dir.glob("*.py")):
        if ENTRY_RE.match(p.name):
            return p
    return None


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
    dates = [str((rec or {}).get("date") or "").strip()
             for rec in (data or {}).values()]
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


def accounts(apps_root: Path, config_root: Optional[Path]) -> List[dict]:
    """Every app/account pair, shaped for paperpull_core.due.plan."""
    out: List[dict] = []
    apps_root = Path(apps_root)
    if not apps_root.is_dir():
        return out
    for app_dir in sorted(p for p in apps_root.iterdir() if p.is_dir()):
        if _entry_script(app_dir) is None:
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
                continue
            output_dir = Path(cfg.get("output_dir") or "")
            session = {}
            try:
                session = (json.loads(
                    (output_dir / "sentinel.json").read_text(encoding="utf-8"))
                    .get("session") or {})
            except (OSError, json.JSONDecodeError):
                session = {}
            last_alive = str(session.get("last_verified_alive") or "")
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
                "parked": session.get("state") == "parked",
            })
    return out
