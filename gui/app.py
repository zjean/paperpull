r"""PaperPull - local control panel.

A small FastAPI app that discovers the downloader apps, works out what each
account needs from a person, and runs one action at a time, streaming the live
output to the browser. It only ever runs the predefined per-app commands -
nothing from user input is passed to a shell.

Run it:  python -m uvicorn app:app --port 8765   (or use run_gui.bat)
Then open http://127.0.0.1:8765

By default it drives the apps in ../apps. Point it at your existing working
copies instead with the APPS_ROOT environment variable, e.g.:
  set APPS_ROOT=C:\path\to\Receipt and Statement Downloader

The page itself lives in static/ (index.html, panel.css, panel.js) rather than
in a string in this file. That split is what lets the panel be a real UI - one
that can list every account with its state instead of offering six bare verbs
in a sidebar - without this module growing a five-hundred-line quoted page in
the middle of its request handlers.
"""
from __future__ import annotations

import codecs
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Dict
from urllib.parse import urlsplit

from anyio import to_thread
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

# PaperPull targets Python 3.11+ (README, and core/pyproject.toml's
# requires-python). Nothing here declared that, so a reader - or a scanner -
# landing in this file had no way to know which version it is written against.
# Say it once, and fail with a sentence rather than something obscure.
if sys.version_info < (3, 11):
    raise SystemExit(
        "PaperPull needs Python 3.11 or newer; this is "
        f"{sys.version_info.major}.{sys.version_info.minor}.")

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
try:
    VERSION = (HERE.parent / "VERSION").read_text(encoding="utf-8").strip()
except Exception:
    VERSION = "0.1.0"
APPS_ROOT = Path(os.environ.get("APPS_ROOT", str(HERE.parent / "apps")))

# The actions, and what each one is FOR.
#
# `flags` is the only part that reaches argv. Everything else is text for the
# page, and it lives here rather than in panel.js for one reason: the page
# renders whatever this table says, so an action gained or renamed here shows
# up correctly on screen without a second edit somewhere that could disagree
# with it. The old panel had the labels here and the explanations in a wall of
# paragraphs under the buttons, and the two drifted: the buttons said "Pilot"
# and nothing on screen said what a pilot was.
#
# `blurb` is what the button does, in the second person, from the user's side
# of the screen - never how it is implemented. `step` marks the three actions
# that form the ordinary path through a provider, in order; everything with no
# `step` is a side road and the page files it under "Other things".
#
# `adopt` is the one command that records which account a config's tab belongs
# to, and it exists here because a human-initiated, interactive, once-per-
# account decision is exactly what this panel is for. Without it an upgraded
# install had no way through the identity gate at all: an app that requires
# adopted anchors refuses Pilot, Discover, Run All and Resume until they exist,
# and nothing in the panel could create them. It carries a real command
# (--discover) because --adopt-identity on its own is a modifier, not an
# action, and would only print the help.
ACTIONS = {
    "login": {
        "label": "Sign in",
        "blurb": "Opens this provider in a browser. You sign in there "
                 "yourself and leave the tab open.",
        # With a remote browser there is no window to open here, so Login
        # means something different and has to say so - see _login_flag.
        "blurb_remote": "Checks that PaperPull can see the session you "
                        "signed in on the browser desktop.",
        "step": 1,
        "flags": ["__LOGIN__"],
    },
    "pilot": {
        "label": "Try a few",
        "blurb": "Downloads the newest handful, so you can watch it work "
                 "before committing to the lot.",
        "step": 2,
        "flags": ["--pilot"],
    },
    "all": {
        "label": "Download everything new",
        "blurb": "Downloads every document you do not already have. Safe to "
                 "re-run - nothing is ever fetched twice.",
        "step": 3,
        "flags": ["--all", "--yes"],
    },
    "adopt": {
        "label": "Confirm this account",
        "blurb": "Records which account the signed-in tab belongs to. Once "
                 "per account; runs refuse to file documents until you have.",
        "flags": ["--discover", "--adopt-identity"],
    },
    "discover": {
        "label": "List what is there",
        "blurb": "Asks the provider which documents exist. Downloads nothing.",
        "flags": ["--discover"],
    },
    "resume": {
        "label": "Continue last run",
        "blurb": "Picks an interrupted run up where it stopped, from the list "
                 "it already has.",
        "flags": ["--resume", "--yes"],
    },
    "verify": {
        "label": "Re-check saved files",
        "blurb": "Re-reads the PDFs already on disk. Opens no browser.",
        "flags": ["--verify"],
    },
}
ENTRY_RE = re.compile(r".*_(receipts|docs)\.py$")

app = FastAPI(title="PaperPull")

# The panel runs the apps' commands, so its API must only answer requests that
# originate from the panel page itself (served on localhost). A CSRF attempt
# driven by another website carries an Origin/Referer whose host is that site;
# same-origin requests from the panel carry a localhost host or no such header
# at all. There is no CORS middleware, so cross-origin JS can't read responses
# either - this closes the remaining "trigger a run" vector.
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]", ""}


def _allowed_hosts() -> set:
    """Hosts the panel page may be served from.

    Localhost always counts, which is the whole story for a native install.
    Behind a reverse proxy the page arrives from a real hostname instead, and
    the check above would refuse every request from it - so that hostname has
    to be named explicitly in PAPERPULL_ALLOWED_HOSTS (comma-separated).
    Exact matches only: a suffix rule would admit
    paperpull.example.com.attacker.net.
    """
    extra = os.environ.get("PAPERPULL_ALLOWED_HOSTS", "")
    return _LOCAL_HOSTS | {h.strip().lower() for h in extra.split(",") if h.strip()}


def _same_origin_only(request: Request) -> None:
    allowed = _allowed_hosts()
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if not value:
            continue
        host = (urlsplit(value).hostname or "").lower()
        if host not in allowed:
            raise HTTPException(403, "cross-origin request refused")


CORE_DIR = HERE.parent / "core"


def _core():
    """paperpull_core's modules, or None if they cannot be loaded.

    Guarded, because gui/requirements.txt is the only thing a native install
    promises to have installed and paperpull_core lives in a sibling directory
    the panel does not otherwise import from. Every caller below decides for
    itself what None means - _python_for degrades, api_due refuses - because
    those are genuinely different answers.

    One place, so `sys.path` is touched once. Two call sites used to insert
    CORE_DIR on every request, and `sys.path` on a panel left open for a week
    grew a copy of that string per click, forever.
    """
    if str(CORE_DIR) not in sys.path:
        sys.path.insert(0, str(CORE_DIR))
    try:
        from paperpull_core import appload, due as due_mod, locks
    except Exception:
        return None
    return SimpleNamespace(appload=appload, due=due_mod, locks=locks)


def _entry_script(app_dir: Path):
    for p in sorted(app_dir.glob("*.py")):
        if ENTRY_RE.match(p.name):
            return p
    return None


def _venv_python(app_dir: Path):
    r"""The app's own interpreter, if it has one - appload.venv_python's answer.

    The rule (Windows puts it in .venv\Scripts\python.exe; macOS and Linux use
    .venv/bin/python) lives in appload because the scheduler needs the same
    answer and, having its own copy, did not have it: it launched every app on
    `sys.executable`, where playwright and paperpull_core are not installed.

    None when the core cannot be loaded, which reads as "no venv" - the
    container's own situation, where one interpreter serves everything.
    """
    core = _core()
    return core.appload.venv_python(app_dir) if core else None


def _python_for(app_dir: Path) -> str:
    venv = _venv_python(app_dir)
    return str(venv) if venv else sys.executable


_TRUTHY = {"1", "true", "yes", "on"}


def _remote_browser() -> bool:
    """Is the sign-in browser in another container?

    Set by the image. It changes exactly two things: what Login does, and
    whether a missing per-app .venv is worth warning about.
    """
    return os.environ.get("PAPERPULL_REMOTE_BROWSER", "").strip().lower() in _TRUTHY


def _supported_actions(script: Path) -> list:
    """Which of ACTIONS this app's entry script actually accepts.

    ACTIONS is one global table, but the flags behind it are not universal:
    `--adopt-identity` exists only on the entry scripts of apps this feature
    has reached (Simyo, then Youfone), and every other app's argparser
    refuses it with "unrecognized arguments" - a real failure, not a no-op.
    Before this, the panel offered "Adopt identity" on all 18 apps regardless,
    because ACTIONS is global and nothing gated it per app.

    Same technique as `_login_flag` just below and tools/schedule.py's
    `_supports_unattended`: read the script's own source rather than keep a
    second table that would drift out of sync with it. An action is offered
    only when every one of its flags is literally present in the script's
    text - except the `__LOGIN__` placeholder, which is not a real flag; it
    is always resolved to something the script supports (`_login_flag`
    itself proves that), so it never disqualifies an action.

    A plain substring search, exactly like `_login_flag`'s - the same
    accepted imprecision: a script that merely mentions a flag in a comment
    would pass. No app does that today, and hiding a button a script does
    truly support would be the worse failure of the two.
    """
    try:
        text = script.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        text = ""
    return [key for key, spec in ACTIONS.items()
            if all(f == "__LOGIN__" or f in text for f in spec["flags"])]


def _login_flag(script: Path) -> str:
    # With a remote browser there is nothing for us to launch: it is already
    # running, and the user signs in on its own desktop. So Login means what
    # --login has always meant - attach over CDP, open the provider's page in
    # that browser, and report whether the session is signed in.
    if _remote_browser():
        return "--login"
    try:
        text = script.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        text = ""
    return "--open-browser" if "--open-browser" in text else "--login"


def _config_root() -> Path:
    """Where the real config files live, if not in the app's own folder.

    In a container the app directories are part of the image, so a config kept
    there would vanish on the next rebuild. PAPERPULL_CONFIG_ROOT points at a
    volume holding <root>/<app>/config*.json instead, and the panel passes an
    absolute --config so nothing has to be linked into the app directory. That
    keeps those directories read-only, which is what lets the container run as
    whatever uid you like rather than the one baked into the image.
    """
    root = os.environ.get("PAPERPULL_CONFIG_ROOT", "").strip()
    return Path(root) if root else None


def _config_dir(app_dir: Path) -> Path:
    """The directory holding this app's config files."""
    root = _config_root()
    return (root / app_dir.name) if root else app_dir


def _account_names(app_dir: Path):
    """Just the labels: 'primary' plus every config.<name>.json."""
    names = ["primary"]
    cfg_dir = _config_dir(app_dir)
    if not cfg_dir.is_dir():
        return names
    for cfg in sorted(cfg_dir.glob("config.*.json")):
        if cfg.name == "config.example.json":
            continue
        names.append(cfg.name[len("config."):-len(".json")])
    return names


def _config_of(app_dir: Path, account: str) -> dict:
    """This account's config as parsed JSON, or {} if there is none.

    A second small read of a file _sentinel_for also opens. Kept separate
    rather than threaded through it because _sentinel_for's whole job is to
    answer "nothing known" for anything it cannot read, and an unreadable
    config has to mean that there too - two callers, two independent failures,
    neither able to break the other.
    """
    try:
        cfg = json.loads(_config_path(app_dir, account)
                         .read_text(encoding="utf-8-sig"))
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _sentinel_for(app_dir: Path, account: str) -> dict:
    """This account's sentinel record, or {} if there is none yet.

    Read as plain JSON on purpose: this is the app list, the one thing the
    panel must always be able to draw, and it must not depend on importing an
    app's code or the core - a native install promises gui/requirements.txt
    and nothing else. paperpull_core.appload resolves the same relative
    output_dir the same way for the scheduler's due list; this stays a
    separate read rather than a call into it because everything in this
    function has to work with no core at all. If the resolution rule ever
    changes, both sides change.
    """
    cfg_path = _config_path(app_dir, account)
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
        out_dir = Path(cfg["output_dir"])
        if not out_dir.is_absolute():
            # Every shipped config.example.json has "output_dir": "." - which
            # is meaningful for the downloader subprocess, launched with
            # cwd=app_dir (see the Popen call below), but this long-running
            # panel process has one cwd of its own (wherever uvicorn started)
            # that is not any app's directory. So a relative path has to be
            # resolved against app_dir, exactly like the subprocess sees it.
            out_dir = app_dir / out_dir
        raw = (out_dir / "sentinel.json").read_text(encoding="utf-8")
        decoded = json.loads(raw)
        return decoded if isinstance(decoded, dict) else {}
    except Exception:
        # No config, no output dir yet, no sentinel, or unreadable: all of
        # which mean "nothing known", never an error the panel should show.
        return {}


def _config_path(app_dir: Path, account: str) -> Path:
    name = "config.json" if account == "primary" else f"config.{account}.json"
    return _config_dir(app_dir) / name


def _accounts(app_dir: Path):
    out = []
    for name in _account_names(app_dir):
        sent = _sentinel_for(app_dir, name)
        session = sent.get("session") or {}
        identity_rec = sent.get("identity") or {}
        anchors = identity_rec.get("anchors")
        out.append({
            "name": name,
            # Only `primary` can be missing: every other account label is
            # derived from a config.<name>.json that therefore exists. Without
            # one, no action can run at all - and the panel used to report
            # exactly that account as "Nothing to do", which is the opposite
            # of true. `output_dir` alone decides where documents land, so
            # there is nothing to fall back on.
            "configured": _config_path(app_dir, name).is_file(),
            # Which browser this account attaches to. Not shown - it is only
            # ever compared with the other accounts of the same app, in
            # _account_rows, to catch two of them pointing at one browser.
            "cdp_url": str(_config_of(app_dir, name).get("cdp_url") or "").strip(),
            "state": session.get("state", ""),
            "last_alive": session.get("last_verified_alive", ""),
            "parked_reason": session.get("parked_reason", ""),
            "identified": bool(anchors),
            # The anchors themselves, not just whether there are any. Adopting
            # an identity is the one moment a run takes on trust that the tab
            # it is reading really is this config's account, so the person who
            # did the adopting has to be able to see WHAT was recorded and
            # check it once - a boolean cannot be checked against anything.
            # Nothing private: an anchor is a document number and its date,
            # both already in this account's index CSV.
            "anchors": [{"id": str(a.get("id", "")),
                         "date": str(a.get("date", ""))}
                        for a in anchors if isinstance(a, dict)]
            if isinstance(anchors, list) else [],
        })
    return out


def discover_apps():
    apps = {}
    if not APPS_ROOT.exists():
        return apps
    for d in sorted(APPS_ROOT.iterdir()):
        if not d.is_dir():
            continue
        script = _entry_script(d)
        if not script:
            continue
        apps[d.name] = {
            "name": d.name,
            "dir": str(d),
            "script": script.name,
            "python": _python_for(d),
            "login_flag": _login_flag(script),
            "supported_actions": _supported_actions(script),
            "accounts": _accounts(d),
            "has_venv": _venv_python(d) is not None,
        }
    return apps


def _action_text(remote: bool) -> dict:
    """The action table as the page needs it: text only, no flags.

    `blurb_remote` collapses into `blurb` here rather than being resolved in
    the page, because whether the browser is in another container is a fact
    about this deployment and the page should not have to reason about it
    twice (it already did, in a hand-written three-line "steps" paragraph
    that had to be rewritten in JS for the remote case).
    """
    out = {}
    for key, spec in ACTIONS.items():
        blurb = spec.get("blurb", "")
        if remote and spec.get("blurb_remote"):
            blurb = spec["blurb_remote"]
        out[key] = {"label": spec["label"], "blurb": blurb,
                    "step": spec.get("step")}
    return out


@app.get("/api/apps", dependencies=[Depends(_same_origin_only)])
def api_apps():
    """The app list, unchanged in shape.

    `actions` is still key -> label, a plain string map, because that is what
    it has always been and other readers (tests, anything scripting the
    panel) rely on it. The page uses /api/state below instead, which carries
    the same labels plus the blurb and the ordering it needs to explain them.
    """
    apps = discover_apps()
    remote = _remote_browser()
    return {"apps_root": str(APPS_ROOT),
            "actions": {k: v["label"] for k, v in ACTIONS.items()},
            "apps": apps,
            "remote_browser": remote,
            # Where the user signs in, when that is a browser they reach over
            # the network rather than a window that just opened on their desk.
            "browser_url": os.environ.get("PAPERPULL_BROWSER_URL", "").strip(),
            # In the image every app runs on the one interpreter, so a missing
            # per-app .venv is normal and must not be reported as a problem.
            "expect_venvs": not remote}


# ---------------------------------------------------------------------------
# What needs a person
# ---------------------------------------------------------------------------
#
# The panel used to answer one question - "which flag do you want to pass to
# which app?" - and answered it with two dropdowns over eighteen apps. The
# question a person actually opens it with is "what needs me?", and nothing on
# screen answered that: an account's state was a suffix inside an <option>,
# so learning it meant selecting all eighteen apps in turn.
#
# So the page is built around one flat, ordered list of accounts, each carrying
# what it needs and why. The buckets are computed here rather than in the page
# because they are a real rule about this domain, testable in Python, and
# because the sidebar and the detail view must never disagree about which one
# an account is in.

# Ranked by what a person can do about it, most actionable first. A parked
# account cannot do anything until someone signs in, so it outranks an
# unconfirmed identity (which itself needs a signed-in tab to confirm against).
NEEDS = {
    "signin": {"rank": 0, "stamp": "Sign in",
               "why": "The provider signed this session out. Nothing runs "
                      "until someone signs in again."},
    "confirm": {"rank": 1, "stamp": "Confirm",
                "why": "No identity recorded yet. Runs refuse to file "
                       "documents until you confirm which account this is."},
    "due": {"rank": 2, "stamp": "Due",
            "why": "Enough time has passed since the newest document that "
                   "the next one is plausibly there."},
    "ok": {"rank": 3, "stamp": "Filed",
           "why": "Nothing to do. Checked recently enough."},
    # The two the register does not draw. Both mean "this is not an account
    # you use", and the difference between them is what to do about it.
    #
    # `unused` exists because a config file is NOT evidence that anyone set an
    # account up: the container's entrypoint seeds one for every app in the
    # image on first run, so a Dutch household with two providers got eighteen
    # accounts, sixteen of them US banks it will never open - each reported as
    # a real account, each listed as due, and each offered by Add an account
    # as "already set up".
    "unused": {"rank": 4, "stamp": "Never used",
               "why": "Set up on paper - there is a config.json - but "
                      "PaperPull has never signed in to this account or "
                      "downloaded anything for it. In the container every "
                      "app gets a config whether you want it or not."},
    "setup": {"rank": 5, "stamp": "Not set up",
              "why": "There is no config.json for this account yet. Add an "
                     "account writes one, or copy the app's "
                     "config.example.json to config.json by hand."},
}

# Which buckets mean "not an account you use". The register hides these behind
# one line rather than drawing them; everything else about them is unchanged,
# and /api/state still reports them so the Add an account picker can tell a
# provider you have never touched from one you have.
QUIET = ("unused", "setup")

# Sorts providers that declare no session lifetime after every provider that
# does - the same rule, and the same reason, as paperpull_core.due._PATIENT:
# an account whose session dies in ten minutes cannot queue behind nine whose
# sessions last for days.
_PATIENT = 10 ** 9


def _needs(account: dict, gated: bool, due: bool, used: bool) -> str:
    """Which bucket this account is in. One rule, one place.

    `due` arrives as a bool from the same due.plan the scheduler and
    tools/due.py use, so the panel cannot disagree with them about who is
    waiting. When the core is unimportable there is no such answer, and the
    caller passes False: an account then reads as "Filed", which is honest
    only because the page also says, plainly, that it cannot tell what is due
    here. Silently calling everything "Filed" without that sentence would be
    the lie.

    `used` is whether anything has ever happened for this account - see
    _has_been_used. It is checked LAST of the four, and the order is the whole
    point: an account that needs a person needs one whether or not it has ever
    run. The obvious rule ("hide what has never been used") would have hidden
    the one account this panel's own author was in the middle of setting up,
    because a freshly configured account has never run by definition. Needing
    a person outranks having a history.
    """
    if not account.get("configured", True):
        # Ahead of every other test: a sentinel, a session and an identity all
        # live under the output_dir this missing file would have named, so
        # every other answer here would be about a file that cannot exist.
        return "setup"
    if account["state"] == "parked":
        return "signin"
    if gated and not account["identified"]:
        return "confirm"
    if not used:
        return "unused"
    return "due" if due else "ok"


def _has_been_used(account: dict, row: dict) -> bool:
    """Has anything ever actually happened for this account?

    A config file is not evidence of one. The container's entrypoint writes a
    config.json for every app in the image on its first run, so "a config
    exists" says only that the image booted once.

    Evidence is any of: a session state or a last-seen-alive timestamp
    (someone signed in), an adopted identity (someone confirmed the account),
    or a newest document date (something was downloaded). The first three come
    from the sentinel and need no core; the last one needs it, which is why
    this takes both halves and asks for any of them rather than the best one.
    """
    return bool(account.get("state") or account.get("last_alive")
                or account.get("identified")
                or row.get("newest_document_date")
                or row.get("last_checked_date"))


def _shared_browsers(accounts: list) -> dict:
    """account label -> the other accounts of this app on the same browser.

    Every app finds its tab the same way: the first live page whose URL
    matches the provider's host (simyo_site.find_signed_in_page,
    amex_docs' `amex[0] if amex else ...`, and so on for all eighteen).
    Nothing ties that choice to a config, so with two accounts of ONE provider
    signed in to ONE browser a run can read the other account's tab - and the
    owner stamped on every PDF and every CSV row comes from the config file,
    never from the page (storage.ensure_owner). The documents would be filed
    under the wrong person with nothing on screen saying so.

    paperpull_core.identity exists for exactly this and catches it, but only
    on the two apps whose entry script accepts --adopt-identity; on the other
    sixteen nothing checks at all.

    Natively this cannot happen by accident - each account gets its own
    profile on its own port, which is what add_account.py and this panel's own
    Add an account both arrange. In the container it is the default: there is
    one browser and every app's cdp_url points at it on purpose, which is
    right for two different providers (different hosts, different tabs) and
    wrong for two accounts of one provider.

    An empty cdp_url is not a match with anything: it means this account
    launches its own browser rather than attaching to a shared one.
    """
    by_url = {}
    for acc in accounts:
        url = acc.get("cdp_url") or ""
        if url:
            by_url.setdefault(url, []).append(acc["name"])
    return {name: [n for n in names if n != name]
            for names in by_url.values() if len(names) > 1
            for name in names}


def _account_rows(apps: dict, core) -> list:
    """Every app/account pair as one flat, ordered list for the page.

    Two sources, joined here: `apps` (this module's own read of each app's
    configs and sentinel - which always works, core or no core) and, when the
    core loads, appload.accounts + due.plan for the facts that need an app's
    declared spec (how perishable its session is) and its progress file (what
    the newest document was). The join key is (app, account).
    """
    extra, due_keys = {}, set()
    if core is not None:
        try:
            rows = core.appload.accounts(APPS_ROOT, _config_root())
            extra = {(r["app"], r["account"]): r for r in rows}
            due_keys = {(r["app"], r["account"])
                        for r in core.due.plan(rows, date.today().isoformat())}
        except Exception:
            # A single unreadable spec or progress file must not cost the page
            # its account list - the one thing it must always be able to draw.
            extra, due_keys = {}, set()

    out = []
    for app_name, meta in apps.items():
        gated = "adopt" in meta["supported_actions"]
        shared = _shared_browsers(meta["accounts"])
        for acc in meta["accounts"]:
            key = (app_name, acc["name"])
            row = extra.get(key, {})
            lifetime = row.get("session_lifetime_minutes")
            used = _has_been_used(acc, row)
            needs = _needs(acc, gated, key in due_keys, used)
            out.append({
                "app": app_name,
                "account": acc["name"],
                "needs": needs,
                # Both reported, because the Add an account picker needs to
                # tell "no config at all" from "a config nobody has used" -
                # the first is a provider to set up, the second is one to sign
                # in to, and offering to create a config that already exists
                # is a 409 dead end.
                "configured": acc["configured"],
                "used": used,
                "state": acc["state"],
                "last_alive": acc["last_alive"],
                "parked_reason": acc["parked_reason"],
                "identity_gated": gated,
                "identified": acc["identified"],
                "anchors": acc["anchors"],
                # None means "this provider never said" - which is exactly
                # what makes it patient enough for a cron job, and is why the
                # page must show the absence rather than a zero.
                "session_lifetime_minutes": lifetime,
                "newest_document_date": row.get("newest_document_date", ""),
                "last_checked_date": row.get("last_checked_date", ""),
                # The other accounts of this app that attach to the same
                # browser this one does - see _shared_browsers. Empty is the
                # normal, safe case.
                "shares_browser_with": shared.get(acc["name"], []),
                "supported_actions": meta["supported_actions"],
                "has_venv": meta["has_venv"],
            })
    out.sort(key=lambda r: (
        NEEDS[r["needs"]]["rank"],
        _PATIENT if r["session_lifetime_minutes"] is None
        else int(r["session_lifetime_minutes"]),
        r["app"], r["account"]))
    return out


@app.get("/api/state", dependencies=[Depends(_same_origin_only)])
def api_state():
    """Everything the page needs to draw itself, in one request.

    One endpoint rather than the page cross-joining /api/apps and /api/due
    itself: the buckets in `accounts` are derived from both, and computing
    them in two places is how a sidebar starts disagreeing with the detail
    view next to it.

    `due_known` is the honest half of the degraded case. Without the core
    there is no due list, and an empty one would read as "nothing is due" -
    a different and false claim from "this panel cannot tell you". The page
    says which of the two it is.
    """
    core = _core()
    apps = discover_apps()
    remote = _remote_browser()
    return {
        "version": VERSION,
        "apps_root": str(APPS_ROOT),
        "remote_browser": remote,
        "browser_url": os.environ.get("PAPERPULL_BROWSER_URL", "").strip(),
        "expect_venvs": not remote,
        "actions": _action_text(remote),
        "needs": NEEDS,
        "quiet": list(QUIET),
        "today": date.today().isoformat(),
        "due_known": core is not None,
        "accounts": _account_rows(apps, core),
    }


def _build_cmd(app_meta: dict, account: str, action: str):
    if action not in ACTIONS:
        raise HTTPException(400, "unknown action")
    # Defense in depth behind the hidden button: the page only offers actions
    # in `supported_actions`, but nothing stops a direct POST from asking for
    # one anyway. Refusing here, before a subprocess is ever started, is what
    # keeps that direct call from reaching argparse's own "unrecognized
    # arguments" failure instead.
    if action not in app_meta.get("supported_actions", []):
        raise HTTPException(400, f"{app_meta['name']} does not support {action!r}")
    if account not in [a["name"] for a in app_meta["accounts"]]:
        raise HTTPException(400, "unknown account")
    flags = []
    for f in ACTIONS[action]["flags"]:
        flags.append(app_meta["login_flag"] if f == "__LOGIN__" else f)
    cmd = [app_meta["python"], app_meta["script"], *flags]
    name = "config.json" if account == "primary" else f"config.{account}.json"
    root = _config_root()
    if root:
        # Absolute, because the app runs with its own directory as the working
        # directory and the file is not in it.
        cmd += ["--config", str(root / app_meta["name"] / name)]
    elif account != "primary":
        # Natively, primary means "the config.json next to the script", which
        # is the default and needs no flag.
        cmd += ["--config", name]
    return cmd


def _busy_holder(app_dir: Path, account: str):
    """Who holds this provider's session slot right now, if anyone.

    Guarded (see _core): if the core can't be loaded this degrades to
    "cannot tell" (None) rather than breaking the Run button for every app
    over one provider's lock code.

    Two things here are deliberately not computed locally. The directory comes
    from appload.lock_dir, which is the same call simyo_docs.py's main() makes,
    so the panel and the CLI cannot land on different, invisible-to-each-other
    lock directories and fail to contend for the same slot - the one failure
    mode that would make this whole feature silently do nothing. And the
    staleness rule comes from locks.live_holders, the same rule locks.acquire
    applies: a lock older than STALE_AFTER belongs to something that died.

    That second one is why this reads live_holders and not holders. Nothing
    releases a slot on SIGKILL or on a container being recreated, and the CLI
    recovers from that by taking the stale lock over - so a panel that counted
    every file it found refused this provider's Run button from the first
    unclean exit until the end of time, while a terminal three feet away could
    run the same account fine. `account` is unused for the same reason the
    lock is: the slot is per provider, and the parameter stays because every
    other check in api_run is per account.
    """
    core = _core()
    if core is None:
        return None
    try:
        spec = core.appload.load_spec(app_dir)
        held = core.locks.live_holders(core.appload.lock_dir(app_dir),
                                       spec.slug, spec.concurrency)
        if len(held) >= spec.concurrency and held:
            return held[0].get("holder") or "another run"
    except Exception:
        return None
    return None


def _lock_exempt(app_meta: dict, action: str) -> bool:
    """Whether this action needs no browser, so must not wait on the lock
    (or be refused because someone else holds it).

    Mirrors simyo_docs.py's main(): `needs_browser = not (args.verify or
    getattr(args, "open_browser", False))`. That check lives in the CLI,
    which the panel must not import (see _busy_holder above), so this is a
    second, small copy of the same rule rather than a shared import - if
    you change one side's exemption, change this one to match, and vice
    versa. It is derived from the action's actual resolved flags, not from
    a hardcoded action-name list, so "login" is only exempt when it
    resolves to --open-browser (a human signing in) and not when it
    resolves to --login (which attaches over CDP and does need the slot).
    """
    if action not in ACTIONS:
        return False
    flags = [app_meta["login_flag"] if f == "__LOGIN__" else f
             for f in ACTIONS[action]["flags"]]
    return "--verify" in flags or "--open-browser" in flags


@app.get("/api/due", dependencies=[Depends(_same_origin_only)])
def api_due():
    """Which accounts are due, most perishable session first.

    Same inputs and the same ordering `python tools/due.py` would print, so
    the panel and that script never disagree about who is waiting. This is
    what a sitting walks: sign in, run, tear down, sign in to the next - one
    provider session at a time, most-perishable-first so a ten-minute session
    never queues behind nine that last for days. Read-only: it opens no
    browser and starts nothing.

    The import is guarded for the same reason _busy_holder's is (see _core):
    gui/requirements.txt is all a native install promises to have, and
    paperpull_core lives in a sibling directory the panel does not otherwise
    depend on. Unlike _busy_holder, though, failure here is not something to
    quietly paper over with a default - returning an empty "due": [] would
    read as "nothing is due today", a different and false claim from "the
    panel cannot tell you what is due". So this reports a 503 instead, and
    the page's sitting treats the two answers differently: an empty list
    means stand down, a failed fetch means something is broken.
    """
    core = _core()
    if core is None:
        raise HTTPException(503, "paperpull_core is not importable here")
    accounts = core.appload.accounts(APPS_ROOT, _config_root())
    today = date.today().isoformat()
    return {"today": today, "due": core.due.plan(accounts, today)}


# ---------------------------------------------------------------------------
# Adding an account
# ---------------------------------------------------------------------------
#
# Setting an account up meant hand-copying config.example.json to config.json
# and editing it, and until you had, the panel listed that account as one more
# thing wrong with your install - seventeen of eighteen rows on a fresh
# checkout, none of which most people will ever use. So the register hides
# them, and this is the button that turns one into a real account.
#
# It is the only endpoint that writes, and the rule for what it writes is not
# invented here: it is the one every app's own add_account.py already applies
# and the READMEs already promise - "its own profile, port and output folders,
# so no data mixes". Two accounts of one provider sharing an output_dir would
# share progress.json and sentinel.json, so each would keep overwriting the
# other's idea of what it had downloaded and whether it was signed in.

# The label becomes a filename (config.<label>.json) and a directory suffix,
# so it is restricted rather than sanitised: silently rewriting what someone
# typed produces an account under a name they did not choose and cannot guess.
ACCOUNT_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# config.example.json is the template, not an account - _account_names skips
# it, so an account by that name would be created and then never listed.
RESERVED_LABELS = {"example"}

# What each extra account's debugging port is offset by, natively. Upstream
# gives every account its own browser profile on its own port; add_account.py
# uses the same step.
PORT_STEP = 10


def _template_config(app_dir: Path):
    """(the config to base a new account on, whether it is a real account).

    A provider's own config.json first, because a second account of a provider
    you already use should differ from the first only in the ways it has to.
    The tracked config.example.json otherwise, which is the case for a provider
    you have never set up.
    """
    primary = _config_path(app_dir, "primary")
    if primary.is_file():
        return primary, True
    example = app_dir / "config.example.json"
    return (example, False) if example.is_file() else (None, False)


def _new_account_config(app_dir: Path, label: str, existing: int) -> dict:
    """The config to write for a new account of this app.

    `existing` is how many accounts this app already has, and it is what keeps
    two additions from landing on one port: the first extra account steps once,
    the second twice.
    """
    source, from_account = _template_config(app_dir)
    if source is None:
        raise HTTPException(
            400, f"{app_dir.name} ships no config.example.json, so there is "
                 f"no template to copy. Write its config.json by hand.")
    try:
        cfg = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(400, f"cannot read {source.name}: {e}")
    if not isinstance(cfg, dict):
        raise HTTPException(400, f"{source.name} is not a JSON object")

    # `primary` copied straight from the shipped example is the setup this
    # project documents everywhere; there is nothing to move out of the way.
    if label == "primary" and not from_account:
        return cfg

    # Everything below is about not mixing this account's data with another's.
    out = Path(str(cfg.get("output_dir") or "."))
    # "." is every shipped example's value and means "the app's own folder",
    # which has no name to append a label to - so the new account gets a
    # subfolder rather than a renamed sibling.
    out_new = Path(label) if out.name in ("", ".") \
        else out.parent / f"{out.name} - {label}"
    profile = Path(str(cfg.get("profile_dir") or "./browser-profile")).name
    cfg["output_dir"] = str(out_new)
    cfg["profile_dir"] = str(out_new / profile)
    # The account holder's name, which the panel cannot ask for (it hands a
    # run a pipe, and an app only asks for it on a real console). The label is
    # a better guess than the blank it would otherwise keep forever.
    if not str(cfg.get("owner") or "").strip():
        cfg["owner"] = label.replace("-", " ").replace("_", " ").title()

    # Its own debugging port - but only where every app has its own browser.
    # In the container there is exactly one browser and every app's cdp_url
    # points at it on purpose (docs/docker.md, "One shared browser"), so
    # stepping the port here would aim this account at nothing at all. A
    # second person's accounts get a second browser service, and that is a
    # compose-file decision, not one this endpoint can make.
    url = str(cfg.get("cdp_url") or "")
    if url and _config_root() is None:
        m = re.search(r":(\d+)", url)
        if m:
            port = int(m.group(1)) + PORT_STEP * max(existing, 1)
            cfg["cdp_url"] = url[:m.start(1)] + str(port) + url[m.end(1):]
    return cfg


@app.post("/api/accounts", dependencies=[Depends(_same_origin_only)])
async def api_add_account(request: Request):
    """Create one account's config file. The panel's only write."""
    body = await _json_body(request)
    if not isinstance(body, dict):
        raise HTTPException(400, "expected an object")
    app_name = body.get("app")
    label = body.get("account", "primary")
    if not isinstance(app_name, str) or not isinstance(label, str):
        raise HTTPException(400, "app and account must be strings")
    label = label.strip()

    # The app name reaches the filesystem, so it is checked against the apps
    # actually discovered rather than pattern-matched: "../.." is not a
    # discovered app, and neither is anything else that is not there.
    apps = discover_apps()
    if app_name not in apps:
        raise HTTPException(404, f"unknown app {app_name!r}")
    if not ACCOUNT_LABEL_RE.match(label):
        raise HTTPException(
            400, "an account label is 1-32 characters of lowercase a-z, 0-9, "
                 "- or _, starting with a letter or digit. It becomes a "
                 "filename, so it is refused rather than quietly rewritten - "
                 "and lowercase only, because config.Spouse.json and "
                 "config.spouse.json are one file on macOS and Windows and "
                 "two on Linux.")
    if label in RESERVED_LABELS:
        raise HTTPException(400, f"{label!r} is reserved")

    app_dir = Path(apps[app_name]["dir"])
    dest = _config_path(app_dir, label)
    if dest.exists():
        raise HTTPException(409, f"{app_name}/{label} already exists")

    existing = len(apps[app_name]["accounts"])
    cfg = _new_account_config(app_dir, label, existing)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive create, so two clicks racing cannot have the second
        # silently overwrite the first's config - the check above is a
        # courtesy, this is the guarantee.
        with open(dest, "x", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
            fh.write("\n")
    except FileExistsError:
        raise HTTPException(409, f"{app_name}/{label} already exists")
    except OSError as e:
        raise HTTPException(500, f"cannot write {dest}: {e}")

    # Resolved, not as written. Every shipped example says "output_dir": ".",
    # which is meaningful to the downloader (launched with its own directory as
    # the working directory) and meaningless on screen - the page reported that
    # a new account would file its documents "into .".
    written = Path(str(cfg.get("output_dir") or "."))
    resolved = written if written.is_absolute() else (app_dir / written)
    return {"app": app_name, "account": label, "config": str(dest),
            "output_dir": str(resolved),
            "cdp_url": str(cfg.get("cdp_url", "")),
            # Said out loud rather than left for someone to discover: the
            # container has one browser, so a second person's account needs a
            # second browser service and this file pointed at it.
            "shared_browser": _config_root() is not None and label != "primary"}


# ---------------------------------------------------------------------------
# Answering a prompt
# ---------------------------------------------------------------------------
#
# Every app pauses and asks for a keypress in three places: the provider
# signed you out mid-run, the provider is showing a security challenge, and
# the --verify pass offering to relabel a receipt. Natively those are answered
# in the console window the launcher opened. In the container there is no such
# window, so the panel is the only thing that can answer, and a run that
# nobody could answer ended with "no interactive console available".
#
# So a run keeps a live stdin, and this dict is how a later request finds the
# process to write it to. The id is unguessable rather than a counter: it is
# the only thing standing between someone who slipped past the origin check
# and the ability to type into a running downloader.
_RUNS: Dict[str, subprocess.Popen] = {}

# An answer replies to ONE prompt. Long enough for the free-text prompt (a
# receipt summary); everything else is a keypress or "YES".
ANSWER_MAX_CHARS = 1000


def _register_run(proc: subprocess.Popen) -> str:
    run_id = secrets.token_urlsafe(8)
    _RUNS[run_id] = proc
    return run_id


def _answer_line(text: str) -> str:
    r"""One answer, one line.

    A newline inside the answer would end the reply early and leave the rest
    sitting in the pipe, to be read as the answer to whatever the app asks
    NEXT - so "YES\nq" would confirm a full run and then quit the verify pass.
    They are collapsed to spaces rather than refused because the only
    free-text prompt is a receipt summary, where a pasted line break is a typo
    and not an attempt at anything.
    """
    flat = (text or "").replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return flat[:ANSWER_MAX_CHARS]


def _live_run(run_id: str) -> subprocess.Popen:
    """The process behind this id, or 404.

    A finished run keeps its id for a moment - the stream's cleanup is what
    removes it - so "still registered" is not the same as "still listening".
    Writing to a dead process must not look like it worked.
    """
    proc = _RUNS.get(run_id)
    if proc is None or proc.poll() is not None:
        raise HTTPException(404, "no such run")
    return proc


def _send_answer(run_id: str, text: str) -> None:
    """Deliver one line to a run that is blocked on input().

    This is the only user text that reaches a running downloader. It never
    reaches a shell: the command line itself is still built solely from
    ACTIONS, and this goes to a waiting input() and nowhere else.
    """
    proc = _live_run(run_id)
    if proc.stdin is None:
        raise HTTPException(409, "this run is not reading input")
    try:
        proc.stdin.write((_answer_line(text) + "\n").encode("utf-8"))
        proc.stdin.flush()
    except (BrokenPipeError, ValueError, OSError):
        # It exited between the poll() above and the write.
        raise HTTPException(404, "no such run")


def _stop_run(run_id: str) -> None:
    """End a run on request.

    Needed because of the stdin above: an app can now block on a prompt
    indefinitely, and "close the tab" should not be the only way out of that.
    Stopping is safe - downloaded_ok is only written after a document is
    saved, so a re-run resumes and re-fetches nothing.

    terminate() is SIGTERM, and the apps handle it (simyo_docs.py's
    `_stop_on_sigterm`) so that their `finally` blocks run and this provider's
    session slot is released on the way out. Before that, one press of this
    button left the lock file behind and _busy_holder refused the next run for
    six hours - which the page then reported as "connection lost", a wrong
    story about a wrong problem. _busy_holder now ages a stale lock out too,
    so the two fixes cover each other: this is the tidy exit, that is the
    backstop for a process that never got to run any code at all.
    """
    _live_run(run_id).terminate()


def _chunk(text: str) -> str:
    """One SSE frame carrying console output verbatim.

    JSON, because the payload is no longer a whole line: input() writes its
    prompt without a trailing newline, and a run's output can carry newlines
    and carriage returns anywhere. Encoding it keeps the frame single-line
    whatever the app printed.
    """
    return "data: " + json.dumps({"t": text}) + "\n\n"


async def _json_body(request: Request):
    """The request body, or a 400 rather than a 500 for a malformed one."""
    try:
        return await request.json()
    except Exception:
        raise HTTPException(400, "expected a JSON body")


@app.post("/api/answer", dependencies=[Depends(_same_origin_only)])
async def api_answer(request: Request):
    body = await _json_body(request)
    if not isinstance(body, dict):
        raise HTTPException(400, "expected an object")
    run_id = body.get("run")
    text = body.get("text", "")
    if not isinstance(run_id, str) or not isinstance(text, str):
        raise HTTPException(400, "run and text must be strings")
    _send_answer(run_id, text)
    return {"ok": True}


@app.post("/api/stop", dependencies=[Depends(_same_origin_only)])
async def api_stop(request: Request):
    body = await _json_body(request)
    run_id = body.get("run") if isinstance(body, dict) else None
    if not isinstance(run_id, str):
        raise HTTPException(400, "run must be a string")
    _stop_run(run_id)
    return {"ok": True}


@app.get("/api/run", dependencies=[Depends(_same_origin_only)])
def api_run(app: str, account: str = "primary", action: str = "pilot"):
    apps = discover_apps()
    if app not in apps:
        raise HTTPException(404, "unknown app")
    meta = apps[app]
    # Refuse before ever building a command: starting a second Popen for a
    # provider that only tolerates one signed-in session would sign the
    # first one out from under whichever run got there first - the same
    # contract simyo_docs.py's own main() enforces on the CLI side, checked
    # here too because a panel restart is exactly the case a file-backed
    # lock (rather than the in-memory _RUNS dict below) exists to catch.
    #
    # _lock_exempt skips this for the same actions the CLI's own lock skips
    # (--verify, and a Login that resolves to --open-browser): neither
    # touches the shared browser session, so neither should queue behind,
    # or be refused by, a run that is genuinely using it.
    if not _lock_exempt(meta, action):
        busy = _busy_holder(Path(meta["dir"]), account)
        if busy:
            raise HTTPException(409, f"{app} is already running as {busy}. "
                                     f"This provider allows one session at a time.")
    cmd = _build_cmd(meta, account, action)

    # Deliberately an *async* generator. With a plain sync one, Starlette wraps
    # it in iterate_in_threadpool, which never calls .close() on it - so the
    # cleanup below would never run and a closed tab left the downloader going.
    async def stream():
        yield _chunk(f"$ {' '.join(cmd)}\n")
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        try:
            proc = subprocess.Popen(
                cmd, cwd=meta["dir"],
                # A real stdin, so the panel can answer the prompts an app
                # raises mid-run - see "Answering a prompt" above. It is a
                # PIPE and not this server's terminal, which matters: a pipe
                # is not a tty, so ensure_owner() still skips its first-run
                # "Whose account is this?" question and `owner` stays a
                # config-file field here. What a pipe does NOT do is raise
                # EOFError, so an unanswered prompt now waits instead of
                # ending the run - which is why there is a Stop button.
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                # Bytes, not text, and read below in whatever size arrives
                # rather than by line. input() writes its prompt with no
                # trailing newline, so a readline() reader blocks on a line
                # that never comes and the page shows a run that started and
                # then went silent - which is exactly what a sign-out mid-run
                # looked like.
                stderr=subprocess.STDOUT, env=env)
        except Exception as e:
            yield _chunk(f"[failed to start] {e}\n")
            yield "event: done\ndata: 1\n\n"
            return
        run_id = _register_run(proc)
        # Before any output, so the page can answer the very first prompt.
        yield f"event: run\ndata: {run_id}\n\n"
        # Closing the browser tab closes this generator. Without the finally
        # below, the downloader kept running unseen - still driving your
        # signed-in browser over CDP and still writing PDFs and progress.json -
        # with nothing on screen. Worse, believing it had stopped, you could
        # press Run again and put two runs on one progress.json, one CDP port
        # and one output folder. Stopping is safe: downloaded_ok is only set
        # after a document is saved, so a re-run resumes and re-fetches nothing.
        #
        # A chunk can split a multi-byte character, so decoding is incremental.
        # errors="replace" keeps one mangled byte from ending a run that is
        # otherwise fine.
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        fd = proc.stdout.fileno()
        try:
            while True:
                # The read blocks, so it goes to a worker thread rather than
                # stalling the event loop for every other request - including
                # the one carrying the answer this read is waiting for.
                data = await to_thread.run_sync(os.read, fd, 65536)
                if not data:
                    break
                text = decoder.decode(data)
                if text:
                    yield _chunk(text)
            tail = decoder.decode(b"", True)
            if tail:
                yield _chunk(tail)
            code = await to_thread.run_sync(proc.wait)
            yield _chunk("\n")
            yield f"event: done\ndata: {code}\n\n"
        finally:
            _RUNS.pop(run_id, None)
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            # stdin first: a child blocked on a prompt sees EOF and stops
            # rather than sitting in a pipe nobody can write to any more.
            for pipe in (proc.stdin, proc.stdout):
                if pipe:
                    try:
                        pipe.close()
                    except Exception:
                        pass

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


def _asset(name: str) -> str:
    """One file out of static/.

    Read per request rather than cached, so editing the page is a reload
    rather than a restart. Three small local file reads per page load, on a
    panel serving one person on localhost.
    """
    return (STATIC / name).read_text(encoding="utf-8")


# Import-time snapshots, for readers that want the page as data rather than
# over HTTP - the tests, which assert against the real markup and execute the
# real JS. The routes below re-read instead (see _asset), so the two differ
# only for a file edited after import, which is what each caller wants.
HTML = _asset("index.html")
CSS = _asset("panel.css")
JS = _asset("panel.js")

_NO_STORE = {"Cache-Control": "no-store"}


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(_asset("index.html").replace("__VERSION__", VERSION),
                        headers=_NO_STORE)


@app.get("/panel.css")
def panel_css():
    return Response(_asset("panel.css"), media_type="text/css",
                    headers=_NO_STORE)


@app.get("/panel.js")
def panel_js():
    return Response(_asset("panel.js"), media_type="text/javascript",
                    headers=_NO_STORE)
