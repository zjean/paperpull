r"""Receipt & Statement Downloaders - local control panel.

A tiny FastAPI app that discovers the downloader apps, lists their accounts,
and runs an action (Login / Discover / Pilot / Run All / Resume / Verify),
streaming the live output to the browser. It only ever runs the predefined
per-app commands - nothing from user input is passed to a shell.

Run it:  python -m uvicorn app:app --port 8765   (or use run_gui.bat)
Then open http://127.0.0.1:8765

By default it drives the apps in ../apps. Point it at your existing working
copies instead with the APPS_ROOT environment variable, e.g.:
  set APPS_ROOT=C:\path\to\Receipt and Statement Downloader
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
from fastapi.responses import HTMLResponse, StreamingResponse

# PaperPull targets Python 3.11+ (README, and core/pyproject.toml's
# requires-python). Nothing here declared that, so a reader - or a scanner -
# landing in this file had no way to know which version it is written against.
# Say it once, and fail with a sentence rather than something obscure.
if sys.version_info < (3, 11):
    raise SystemExit(
        "PaperPull needs Python 3.11 or newer; this is "
        f"{sys.version_info.major}.{sys.version_info.minor}.")

HERE = Path(__file__).resolve().parent
try:
    VERSION = (HERE.parent / "VERSION").read_text(encoding="utf-8").strip()
except Exception:
    VERSION = "0.1.0"
APPS_ROOT = Path(os.environ.get("APPS_ROOT", str(HERE.parent / "apps")))

# action -> argparse flags. run_all / resume get --yes so they don't block on a
# confirmation prompt. Login is resolved per-app (open-browser vs login).
ACTIONS = {
    "login":    {"label": "Login",    "flags": ["__LOGIN__"]},
    "discover": {"label": "Discover", "flags": ["--discover"]},
    "pilot":    {"label": "Pilot",    "flags": ["--pilot"]},
    "all":      {"label": "Run All",  "flags": ["--all", "--yes"]},
    "resume":   {"label": "Resume",   "flags": ["--resume", "--yes"]},
    "verify":   {"label": "Verify",   "flags": ["--verify"]},
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
    name = "config.json" if account == "primary" else f"config.{account}.json"
    cfg_path = _config_dir(app_dir) / name
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


def _accounts(app_dir: Path):
    out = []
    for name in _account_names(app_dir):
        sent = _sentinel_for(app_dir, name)
        session = sent.get("session") or {}
        identity_rec = sent.get("identity") or {}
        out.append({
            "name": name,
            "state": session.get("state", ""),
            "last_alive": session.get("last_verified_alive", ""),
            "parked_reason": session.get("parked_reason", ""),
            "identified": bool(identity_rec.get("anchors")),
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
            "accounts": _accounts(d),
            "has_venv": _venv_python(d) is not None,
        }
    return apps


@app.get("/api/apps", dependencies=[Depends(_same_origin_only)])
def api_apps():
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


def _build_cmd(app_meta: dict, account: str, action: str):
    if action not in ACTIONS:
        raise HTTPException(400, "unknown action")
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
    what "Start sitting" walks: sign in, run, tear down, sign in to the
    next - one provider session at a time, most-perishable-first so a
    ten-minute session never queues behind nine that last for days.
    Read-only: it opens no browser and starts nothing.

    The import is guarded for the same reason _busy_holder's is (see _core):
    gui/requirements.txt is all a native install promises to have, and
    paperpull_core lives in a sibling directory the panel does not otherwise
    depend on. Unlike _busy_holder, though, failure here is not something to
    quietly paper over with a default - returning an empty "due": [] would
    read as "nothing is due today", a different and false claim from "the
    panel cannot tell you what is due". So this reports a 503 instead, and
    the page's Start Sitting button treats the two answers differently: an
    empty list means stand down, a failed fetch means something is broken.
    """
    core = _core()
    if core is None:
        raise HTTPException(503, "paperpull_core is not importable here")
    accounts = core.appload.accounts(APPS_ROOT, _config_root())
    today = date.today().isoformat()
    return {"today": today, "due": core.due.plan(accounts, today)}


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


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML.replace("__VERSION__", VERSION)


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>PaperPull</title>
<style>
  :root { color-scheme: light dark; --bg:#0f1115; --panel:#171a21; --fg:#e6e6e6;
          --muted:#98a0ad; --accent:#4c8dff; --line:#262b36; --ok:#3ecf8e; }
  * { box-sizing: border-box; }
  body { margin:0; font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif;
         background:var(--bg); color:var(--fg); display:flex; flex-direction:column; height:100vh; }
  header { padding:18px 22px; border-bottom:1px solid var(--line); }
  header h1 { margin:0; font-size:18px; }
  header h1 .tag { color:var(--muted); font-weight:400; }
  header h1 .ver { color:var(--accent); font-weight:400; font-size:13px; vertical-align:middle; }
  header p { margin:4px 0 0; color:var(--muted); font-size:13px; }
  main { display:grid; grid-template-columns: 320px 1fr; gap:0; flex:1; min-height:0; }
  footer { padding:8px 22px; border-top:1px solid var(--line); font-size:12px;
           color:var(--muted); display:flex; justify-content:space-between; align-items:center; }
  footer a { color:var(--accent); text-decoration:none; }
  footer a:hover { text-decoration:underline; }
  .controls { padding:20px 22px; border-right:1px solid var(--line); overflow:auto; }
  label { display:block; font-size:12px; text-transform:uppercase; letter-spacing:.04em;
          color:var(--muted); margin:16px 0 6px; }
  select { width:100%; padding:9px 10px; background:var(--panel); color:var(--fg);
           border:1px solid var(--line); border-radius:8px; font-size:14px; }
  .actions { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:20px; }
  button { padding:10px; border:1px solid var(--line); border-radius:8px; cursor:pointer;
           background:var(--panel); color:var(--fg); font-size:14px; }
  button:hover { border-color:var(--accent); }
  button.primary { background:var(--accent); border-color:var(--accent); color:#fff; grid-column:1/3; }
  button:disabled { opacity:.5; cursor:not-allowed; }
  .hint { font-size:12px; color:var(--muted); margin-top:16px; }
  a.desktop { display:none; margin-top:14px; padding:10px; text-align:center;
              border:1px solid var(--accent); border-radius:8px; color:var(--accent);
              text-decoration:none; font-size:14px; }
  a.desktop:hover { background:var(--panel); }
  .warn { color:#ffcf6b; }
  .console { background:#0b0d11; margin:0; padding:16px 20px; overflow:auto; flex:1;
             min-height:0; font:13px/1.55 ui-monospace,Consolas,monospace; white-space:pre-wrap; }
  /* Answering a prompt. Present for the whole run, highlighted only while
     something is actually waiting - the apps pause on a sign-out, a security
     challenge, and every receipt the --verify pass offers to relabel. */
  .reply { border-top:1px solid var(--line); padding:10px 20px; background:var(--panel); }
  .reply.pending { border-top:2px solid var(--accent); }
  .reply .prompt { display:block; min-height:1.5em; margin-bottom:8px; color:var(--accent);
                   font:13px/1.5 ui-monospace,Consolas,monospace; white-space:pre-wrap; }
  .reply .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .reply input { flex:1; min-width:160px; padding:9px 10px; background:var(--bg);
                 color:var(--fg); border:1px solid var(--line); border-radius:8px;
                 font:14px/1.4 ui-monospace,Consolas,monospace; }
  .reply input:focus { outline:none; border-color:var(--accent); }
  .reply button { white-space:nowrap; }
  .reply button.stop { margin-left:auto; }
  a.signin { display:none; color:var(--accent); text-decoration:none; font-size:13px; }
  a.signin:hover { text-decoration:underline; }
  .status { padding:8px 20px; border-bottom:1px solid var(--line); font-size:13px; color:var(--muted); }
  .dot { display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--muted); margin-right:8px; }
  .dot.run { background:var(--accent); animation:pulse 1s infinite; }
  .dot.ok { background:var(--ok); } .dot.err { background:#ff5c5c; }
  @keyframes pulse { 50% { opacity:.3; } }
</style>
</head>
<body>
<header>
  <h1>PaperPull <span class="ver">v__VERSION__</span><span class="tag"> — Receipt &amp; Statement Downloader</span></h1>
  <p id="root">control panel</p>
</header>
<main>
  <div class="controls">
    <label for="app">App</label>
    <select id="app"></select>
    <label for="account">Account</label>
    <select id="account"></select>
    <div class="actions" id="actions"></div>
    <button class="primary" id="sit" style="width:100%; margin-top:12px;">▶ Start sitting</button>
    <p class="hint">One sitting walks every <em>due</em> account across every
       app, most perishable session first — sign in when asked, watch it run
       here, then sign in to the next. This is for the providers whose
       session dies before a cron job would ever catch it awake.</p>
    <a class="desktop" id="desktop" target="_blank" rel="noopener">🖥 Open browser desktop ↗</a>
    <p class="hint" id="steps">1. <b>Login</b> opens a browser — sign in yourself and leave it open.<br>
       2. <b>Pilot</b> tests the newest few.<br>
       3. <b>Run All</b> downloads everything you don't already have.</p>
    <p class="hint" style="border-left:3px solid var(--accent); padding-left:10px;">
       ↻ <b>Safe to re-run.</b> Run All and Resume skip any statement or receipt
       you've already downloaded — nothing is ever fetched twice, even if you
       deleted the PDFs after importing them elsewhere.</p>
    <p class="hint">💬 <b>A run can ask you something.</b> If a provider signs you
       out mid-run, or asks you to prove you are human, the run pauses and the
       question appears under the console — fix it in the browser, then press
       Continue. Nothing is lost while it waits.</p>
    <p class="hint warn" id="venvwarn" style="display:none"></p>
  </div>
  <div style="display:flex; flex-direction:column; min-width:0;">
    <div class="status"><span class="dot" id="dot"></span><span id="statustext">idle</span></div>
    <pre class="console" id="console"></pre>
    <div class="reply" id="reply" hidden>
      <span class="prompt" id="prompt"></span>
      <div class="row">
        <input id="answer" autocomplete="off" spellcheck="false"
               placeholder="answer the run — Enter to send">
        <button id="continue">Continue ⏎</button>
        <a class="signin" id="signin" target="_blank" rel="noopener">🖥 Sign in on the browser desktop ↗</a>
        <button class="stop" id="stop">Stop run</button>
      </div>
    </div>
  </div>
</main>
<footer>
  <span>PaperPull v__VERSION__ — read-only, runs locally</span>
  <span>☕ <a href="https://ko-fi.com/rheeloaded" target="_blank" rel="noopener">Support this project on Ko-fi</a></span>
</footer>
<script>
let META = null, es = null, RUN = null, promptTimer = null, promptFrom = 0, stopping = false;
// A sitting walks several accounts, one run at a time. `sitting` is true for
// the whole walk; `sittingAbort` is how Stop (see stopRun) or a closed
// confirm() ends the whole walk rather than just the run in flight.
// `onRunEnd` is the resolver of whichever run's promise is currently
// outstanding - set by startRun, called once by endRun - which is how a
// sitting's `await startRun(...)` wakes up only once the run has actually
// ended, never before.
let sitting = false, sittingAbort = false, onRunEnd = null;
const $ = id => document.getElementById(id);
const actionButtons = () => document.querySelectorAll('#actions button');

async function load() {
  META = await (await fetch('/api/apps')).json();
  $('root').textContent = 'apps root: ' + META.apps_root;
  const appSel = $('app');
  appSel.innerHTML = '';
  const keys = Object.keys(META.apps);
  if (!keys.length) { $('console').textContent = 'No apps found under ' + META.apps_root + '.\nSet APPS_ROOT to your downloaders folder.'; return; }
  for (const k of keys) appSel.append(new Option(META.apps[k].name, k));
  appSel.onchange = onApp;
  if (META.remote_browser) {
    // The browser is not on this machine, so Login cannot open a window here.
    // It attaches to the shared browser and reports whether you are signed in.
    $('steps').innerHTML =
      '1. Sign in on the <b>browser desktop</b>, and leave the tab open there.<br>' +
      '2. <b>Login</b> checks that PaperPull can see that signed-in session.<br>' +
      '3. <b>Pilot</b> tests the newest few, then <b>Run All</b>.';
    if (META.browser_url) { const d = $('desktop'); d.href = META.browser_url; d.style.display = 'block'; }
  }
  const acts = $('actions'); acts.innerHTML = '';
  for (const [k, label] of Object.entries(META.actions)) {
    const b = document.createElement('button');
    b.textContent = label; b.className = (k === 'all') ? 'primary' : '';
    b.onclick = () => run(k);
    acts.append(b);
  }
  onApp();
}
function onApp() {
  const m = META.apps[$('app').value];
  const accSel = $('account'); accSel.innerHTML = '';
  const accountLabel = (a) => {
    if (!a.identified) return a.name + ' · unidentified';
    if (a.state === 'parked') return a.name + ' · needs sign-in';
    if (a.state === 'warm') return a.name + ' · alive ' + (a.last_alive || '').slice(0, 16);
    return a.name;
  };
  for (const a of m.accounts) accSel.append(new Option(accountLabel(a), a.name));
  const warn = $('venvwarn');
  if (!m.has_venv && META.expect_venvs) { warn.style.display='block';
    warn.textContent = '⚠ No .venv in this app yet — run setup.bat there first, or output may show import errors.'; }
  else warn.style.display='none';
}
function setStatus(cls, text) { $('dot').className = 'dot ' + cls; $('statustext').textContent = text; }

// -- the console, and the prompts that appear in it ------------------------
// Output arrives as raw chunks rather than whole lines, because input() writes
// its prompt without a newline. That is also how a prompt is spotted: output
// that stops mid-line and stays that way is an app waiting for an answer.
function append(text) {
  const con = $('console');
  con.textContent += text;
  con.scrollTop = con.scrollHeight;
}
function tailLine() {
  const t = $('console').textContent;
  return t.slice(t.lastIndexOf('\n') + 1);
}
function watchForPrompt() {
  clearTimeout(promptTimer);
  if ($('console').textContent.endsWith('\n')) { clearPrompt(); return; }
  promptTimer = setTimeout(markPrompt, 400);
}
function markPrompt() {
  if (!RUN) return;
  $('reply').classList.add('pending');
  $('prompt').textContent = tailLine().trim() || 'Waiting for an answer.';
  $('answer').focus();
  // A sign-out or a challenge is fixed in the browser itself, which in a
  // container is somewhere else entirely — so say where. Only what the app
  // printed since the LAST answer counts: read further back and the sign-out
  // wording from an earlier pause still matches, and every later prompt (the
  // --verify pass asks once per receipt) wrongly points at the desktop.
  const recent = $('console').textContent.slice(promptFrom);
  const needsBrowser = /signed you out|sign in|challenge|verification|captcha|robot/i.test(recent);
  const link = $('signin');
  if (needsBrowser && META.browser_url) { link.href = META.browser_url; link.style.display = 'inline-block'; }
  else link.style.display = 'none';
}
function clearPrompt() {
  clearTimeout(promptTimer);
  $('reply').classList.remove('pending');
  $('prompt').textContent = '';
  $('signin').style.display = 'none';
}
async function answer() {
  if (!RUN) return;
  const text = $('answer').value;
  const r = await fetch('/api/answer', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({run: RUN, text})});
  if (!r.ok) { append('\n[nothing is waiting for an answer]\n'); clearPrompt(); return; }
  // The run has a pipe, not a terminal, so it never echoes what we sent.
  append(text + '\n');
  promptFrom = $('console').textContent.length;
  $('answer').value = '';
  syncAnswerButton();
  clearPrompt();
}
async function stopRun() {
  if (!RUN) return;
  stopping = true;
  // Mid-sitting, Stop has to end the whole sitting - not just this one run,
  // leaving the loop free to sign the next account in regardless. Otherwise
  // the one button a person reaches for to bail out would not actually bail
  // out of anything but the current account.
  if (sitting) sittingAbort = true;
  setStatus('run', 'stopping…');
  await fetch('/api/stop', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({run: RUN})});
}
function syncAnswerButton() {
  $('continue').textContent = $('answer').value ? 'Send ⏎' : 'Continue ⏎';
}
$('continue').onclick = answer;
$('stop').onclick = stopRun;
$('answer').oninput = syncAnswerButton;
$('answer').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); answer(); } };

function endRun(cls, text) {
  setStatus(cls, text);
  RUN = null;
  clearPrompt();
  $('reply').hidden = true;
  actionButtons().forEach(b => b.disabled = false);
  // Only re-enable Start Sitting if no sitting is in progress. Between two
  // accounts' runs a sitting is still live (it is inside a blocking
  // confirm() for the next one), and Start Sitting must stay disabled for
  // that whole stretch too - not just while a subprocess is actually
  // running - or a second click there would re-enter startSitting.
  if (!sitting) $('sit').disabled = false;
  if (es) { es.close(); es = null; }
  // This is the one place a run is decided to be over - reached from the
  // server's "done" event and from a lost connection alike (see startRun).
  // Waking a sitting's `await startRun(...)` here, rather than anywhere
  // else, is what stops it from ever signing the next account in while this
  // one's subprocess might still be holding the provider's session slot.
  if (onRunEnd) { const resolve = onRunEnd; onRunEnd = null; resolve(); }
}
function run(action) {
  startRun($('app').value, $('account').value, action);
}
// Starts one run and returns a Promise that resolves once endRun has been
// called for it - i.e. once the server has actually said "done" (or the
// connection was lost), never merely once the request went out. A sitting
// awaits this before touching the next account.
//
// There is no "end" SSE event in this protocol, only "run" (the very first
// frame, carrying the run id) and "done" (the last, carrying the exit code,
// sent right before the server closes the stream - see the `finally` in
// api_run's stream()). onerror below is therefore not the normal
// end-of-run signal, it is what fires if the connection drops with no
// "done" ever having arrived. That is also why endRun always closes `es`
// itself: an EventSource whose stream the *server* ends still auto-reconnects
// unless something on this side calls .close() first.
function startRun(app, account, action) {
  if (es) {
    // With the fixes below (startSitting's synchronous re-entrancy guard,
    // and Start Sitting plus every action button disabled for as long as
    // any run - manual or sitting-driven - is live) every legitimate call
    // site now waits for a previous run to end before starting another, so
    // this should be unreachable. If it ever fires anyway, closing the old
    // stream silently and reassigning `onRunEnd` would abandon whoever was
    // still awaiting it, mid-run, with nothing to show for it - so this is
    // surfaced loudly instead of let it happen quietly.
    console.error('startRun called while a previous run was still live; '
                 + 'closing it now. This should not be reachable.');
    es.close();
  }
  $('console').textContent = '';
  promptFrom = 0; stopping = false;
  $('answer').value = ''; syncAnswerButton(); clearPrompt();
  setStatus('run', `running ${action} — ${app} / ${account}`);
  actionButtons().forEach(b => b.disabled = true);
  // Disabled here too (not just inside startSitting) so a manual run alone
  // - no sitting involved at all - also blocks Start Sitting from being
  // clicked underneath it.
  $('sit').disabled = true;
  es = new EventSource(`/api/run?app=${encodeURIComponent(app)}&account=${encodeURIComponent(account)}&action=${action}`);
  // Arrives before any output, so even a first-line prompt can be answered.
  es.addEventListener('run', e => { RUN = e.data; $('reply').hidden = false; });
  es.onmessage = e => { append(JSON.parse(e.data).t); watchForPrompt(); };
  es.addEventListener('done', e => {
    const code = e.data;
    // A run you stopped yourself did not fail. Terminating it leaves a signal
    // exit code behind, and reporting that as an error is just wrong. The
    // code is what settles the race where a run finished on its own between
    // the click and the request: a clean exit is "finished", not "stopped".
    if (stopping && code !== '0') return endRun('', 'stopped');
    endRun(code === '0' ? 'ok' : 'err', code === '0' ? 'finished' : `exited (code ${code})`);
  });
  es.onerror = () => { if (es) endRun('err', 'connection lost'); };
  return new Promise(resolve => { onRunEnd = resolve; });
}

// -- the sitting: one account at a time, most perishable session first -----
//
// The scarce resource here is not CPU, it is a person's attention for
// signing in. So this walks /api/due in the order it comes back (due.plan's
// own ordering, most-perishable-session-first) and, for each account, asks
// for a fresh sign-in and then awaits the FULL run - teardown included -
// before ever asking about the next one. Two runs on one provider at once
// would sign each other's session out from under the other; that is the one
// outcome this whole feature exists to prevent.
async function startSitting() {
  // Re-entrancy guard - and it MUST be the very first statement, before any
  // `await` in this function. A second click on Start Sitting calls this
  // function again; JS runs synchronously up to the first await, so
  // `sitting` is already true by the time that second call is dispatched
  // (dispatched, at the earliest, once this call yields at the `await
  // fetch` below). Putting this check anywhere later - after the fetch,
  // after building the queue - leaves exactly that window open: a second
  // invocation would reach confirm()/startRun() for the same account the
  // first invocation is still running, and startRun's `if (es) es.close()`
  // would silently tear down the first invocation's live run and steal its
  // `onRunEnd`, leaving the first `await startRun(...)` never resolved.
  if (sitting) return;
  sitting = true;
  $('sit').disabled = true;
  try {
    const res = await fetch('/api/due');
    if (!res.ok) {
      // A failed fetch (503: paperpull_core is not importable here) is not
      // the same fact as "nobody is due" - it means the panel cannot tell,
      // and must not be read as "stand down".
      alert('Cannot read the due list here.');
      return;
    }
    const {due} = await res.json();
    // A provider with no declared session lifetime can wait for a plain
    // cron job; only a perishable one needs a person sitting down for it.
    const queue = due.filter(a => a.session_lifetime_minutes !== null);
    if (!queue.length) { alert('Nothing needs a person right now.'); return; }
    sittingAbort = false;
    let completed = 0;
    for (const a of queue) {
      if (sittingAbort) break;
      // Sign-in first: a perishable session has to be fresh when the pull
      // runs, and only a person can make it fresh. Declining here - or
      // pressing Stop once the run below has started - ends the whole
      // sitting, not just this one account; `completed` below is what
      // tells the difference between that and finishing the whole queue.
      if (!confirm(`Sign in to ${a.app} (${a.account}) in the browser desktop, `
                 + `in ONE tab. Press OK when you are signed in.`)) break;
      if (sittingAbort) break;
      // 'all', not 'resume'. Resume selects from the discovery.json this
      // account already has on disk and never asks the provider what exists,
      // so a sitting built on it spent the sign-in it had just asked a person
      // for, printed "Nothing to resume", and called itself done. Run All
      // discovers first, and every app's own "already downloaded" memory
      // skips what is on disk - so this is discover-plus-new-only, which is
      // what a sitting was always meant to be.
      await startRun(a.app, a.account, 'all');
      completed++;
    }
    // A sitting cut short - Cancel on a sign-in prompt, or Stop mid-run -
    // is not "done": most of the point of this feature is telling a person
    // what still needs them, and treating a half-finished sitting as
    // complete would say the opposite of that.
    if (completed === queue.length) {
      alert('Sitting done.');
    } else {
      alert(`Sitting stopped after ${completed} of ${queue.length} `
          + `account(s) - ${queue.length - completed} still need a person.`);
    }
  } finally {
    sitting = false;
    $('sit').disabled = false;
  }
}
$('sit').onclick = startSitting;
load();
</script>
</body>
</html>
"""
