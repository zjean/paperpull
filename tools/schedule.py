"""Run the accounts that can run themselves; list the ones that cannot.

One loop, no new dependency, and no Docker socket: this runs inside the same
image as the panel and invokes the same app CLIs the panel does.

The split is one declared fact. A provider whose AppSpec leaves
`session_lifetime_minutes` as None holds its session for days, so a scheduled
run will usually find it alive - and if it does not, the run parks and exits 0
and nothing is woken up. A provider that declares a lifetime (Simyo: ten
minutes) can only be pulled while a human is sitting there, so it is never
started here; it is printed, for the panel's sitting and for whatever notifier
you point at this log.

The command is `--unattended --all --yes`, and `--all` is not an overreach.
It is the only action that asks the provider what exists - an app's `--all`
discovers first, while `--resume` selects from the local `discovery.json`
alone and so can never fetch a document nobody has seen yet. A schedule built
on `--resume` therefore spends the sign-in it was given and prints "Nothing to
resume", which is a schedule that can never do its job. `--all` downloads
everything *in scope*, and each app's own "already downloaded" memory
(`progress.json`) skips what is on disk, so a nightly pass is
discover-plus-new-only. `--yes` answers the confirmation prompt ahead of
time, because nobody is here to type it.

A second, narrower gate sits underneath that one: an app can only be started
unattended if its entry script actually understands `--unattended`. That flag
was added to Simyo alone so far - rolling it out to every other app is future
work, tracked one provider at a time, not a blanket change made here. Detected
the same way gui/app.py's `_login_flag` detects `--open-browser`: by reading
the script's own text rather than trusting a table that could drift out of
sync with the code. An app that is patient (no declared lifetime) but does not
yet carry the flag is skipped and named, loudly, rather than silently omitted
- that line is what tells a human which provider to add it to next, and why
today's pass may have run nothing at all.

    python tools/schedule.py --once      # one pass, then exit
    python tools/schedule.py             # loop, one pass per PAPERPULL_SCHEDULE_HOUR
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "core"))

from paperpull_core import appload, due  # noqa: E402

POLL_SECONDS = 600

# What a scheduled run of one account actually is. See the module docstring
# for why --all and not --resume; --yes answers the confirmation prompt that
# is the only reason --all ever needed a person present.
UNATTENDED_FLAGS = ["--unattended", "--all", "--yes"]


def _supports_unattended(script: Path) -> bool:
    """Same trick as gui/app.py's `_login_flag`: read the script's own text
    rather than keep a separate table that could drift out of sync with it."""
    try:
        text = script.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        text = ""
    return "--unattended" in text


def build_command(account: dict, app_dir: Path, script: Path) -> list:
    """The exact argv one scheduled account is run with.

    Split out of run_one so it can be asserted on without starting anything:
    what these flags resolve to inside the app is the difference between a
    schedule that downloads new documents and one that cannot.

    `appload.python_for`, not `sys.executable`: the documented native setup is
    a venv per app, holding playwright and paperpull_core, and this process's
    own interpreter has neither - so every scheduled run died on the app's
    first import. The panel got this right and this did not, which is exactly
    the kind of disagreement appload exists to end.
    """
    cmd = [appload.python_for(app_dir), script.name, *UNATTENDED_FLAGS]
    if account["account"] != "primary" or os.environ.get("PAPERPULL_CONFIG_ROOT"):
        cmd += ["--config", account["config"]]
    return cmd


def run_one(account: dict, apps_root: Path) -> int:
    app_dir = apps_root / account["app"]
    script = appload.entry_script(app_dir)
    if script is None:
        print(f"  {account['app']}: no entry script, skipped")
        return 0
    if not _supports_unattended(script):
        print(f"  {account['app']}/{account['account']}: skipped - "
              f"{script.name} does not support --unattended yet. "
              f"Add it there before this account can run on its own.")
        return 0
    cmd = build_command(account, app_dir, script)
    print(f"  $ {' '.join(cmd)}")
    # stdin=DEVNULL, not inherited. A scheduled run has no human attached by
    # definition, so this process's own terminal - if it has one - must never
    # reach the child. An inherited tty is exactly how a prompt becomes
    # answerable: --unattended is supposed to make every prompt unreachable,
    # but that guard lives inside the app, one layer below this call, and a
    # second, independent prompt (ensure_owner's, gated on isatty() alone -
    # see storage.py) sits underneath it too. Closing stdin here means a
    # scheduled run can never block on either one, regardless of what runs
    # inside the app today or is added to it tomorrow.
    proc = subprocess.run(cmd, cwd=str(app_dir), stdin=subprocess.DEVNULL)
    return proc.returncode


def one_pass(apps_root: Path, config_root, today: str) -> None:
    accounts = appload.accounts(apps_root, config_root)
    plan = due.plan(accounts, today)
    patient = [a for a in plan if a["session_lifetime_minutes"] is None]
    perishable = [a for a in plan if a["session_lifetime_minutes"] is not None]

    print(f"[{datetime.now():%Y-%m-%d %H:%M}] "
          f"{len(plan)} due: {len(patient)} unattended, "
          f"{len(perishable)} need a person")
    for account in patient:
        print(f"  {account['app']}/{account['account']}")
        code = run_one(account, apps_root)
        if code:
            print(f"  ! exited {code}")
    for account in perishable:
        # Deliberately not started. Its session would be dead before a
        # download finished, and a failed attempt teaches the provider's fraud
        # model something about us for nothing.
        print(f"  waiting for a person: {account['app']}/{account['account']} "
              f"(session lasts {account['session_lifetime_minutes']} min)")


def schedule_hour(raw) -> int:
    """The local hour of the daily pass, or a ValueError saying what is wrong.

    Range-checked, not just parsed. `int("25")` succeeds, and the loop below
    compares it against `datetime.now().hour`, which never reaches 25 - so a
    typo produced a scheduler that ran forever, did nothing, and said nothing
    about it. Under `restart: unless-stopped` a bare traceback is not much
    better: it restart-loops with the reason scrolling past. One sentence and
    exit 2 is what a person can act on.
    """
    try:
        hour = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError(f"PAPERPULL_SCHEDULE_HOUR must be a whole number "
                         f"from 0 to 23; got {raw!r}.")
    if not 0 <= hour <= 23:
        raise ValueError(f"PAPERPULL_SCHEDULE_HOUR must be an hour of the day, "
                         f"from 0 to 23; got {hour}.")
    return hour


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true", help="one pass, then exit")
    args = ap.parse_args(argv)

    apps_root = Path(os.environ.get("APPS_ROOT", HERE.parent / "apps"))
    root = os.environ.get("PAPERPULL_CONFIG_ROOT", "").strip()
    config_root = Path(root) if root else None
    # Checked even for --once, which does not use it: a one-off pass is how a
    # person tests their compose file, and that is the moment to be told the
    # hour they set is not one.
    try:
        hour = schedule_hour(os.environ.get("PAPERPULL_SCHEDULE_HOUR", "7"))
    except ValueError as e:
        print(f"!! {e}", file=sys.stderr)
        return 2

    if args.once:
        one_pass(apps_root, config_root, date.today().isoformat())
        return 0

    print(f"Scheduler up. One pass a day at {hour:02d}:00 local time.")
    ran_on = ""
    while True:
        now = datetime.now()
        today = now.date().isoformat()
        if now.hour == hour and ran_on != today:
            one_pass(apps_root, config_root, today)
            ran_on = today
        # A poll rather than a sleep-until: the container can be restarted at
        # any moment, and `ran_on` being in memory means a restart inside the
        # hour would repeat the pass. due.plan's last_checked_date is what
        # actually prevents that, which is why it is read from disk.
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
