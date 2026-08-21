"""Which accounts are due a run, most perishable session first.

Reads only what is already on disk: each app's declared facts, each config's
output_dir, each account's progress.json and sentinel.json. Runs nothing,
opens no browser, and needs no credentials.

    python tools/due.py
    PAPERPULL_CONFIG_ROOT=/config python tools/due.py
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "core"))

from paperpull_core import appload, due  # noqa: E402


def main(argv=None) -> int:
    apps_root = Path(os.environ.get("APPS_ROOT", HERE.parent / "apps"))
    root = os.environ.get("PAPERPULL_CONFIG_ROOT", "").strip()
    config_root = Path(root) if root else None

    accounts = appload.accounts(apps_root, config_root)
    today = date.today().isoformat()
    plan = due.plan(accounts, today)

    print(f"{len(plan)} of {len(accounts)} account(s) due on {today}\n")
    if not plan:
        return 0
    print(f"{'app':<14} {'account':<12} {'session':<10} "
          f"{'newest doc':<12} state")
    for a in plan:
        lifetime = a["session_lifetime_minutes"]
        session = "days" if lifetime is None else f"{lifetime} min"
        state = "PARKED - needs sign-in" if a["parked"] else "ok"
        print(f"{a['app']:<14} {a['account']:<12} {session:<10} "
              f"{a['newest_document_date'] or '(never)':<12} {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
