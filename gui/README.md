# PaperPull — Control panel (GUI)

A small local web UI that wraps every downloader app: pick an app + account,
click an action, and watch the live output. It only runs the same predefined
commands the `.bat` files do — nothing from the page is passed to a shell.

![PaperPull control panel — pick an app, click Pilot, watch the live output](../docs/control-panel.gif)

## Run it

```bat
run_gui.bat
```

That creates a small venv (FastAPI + uvicorn), starts the server, and opens
<http://127.0.0.1:8765>. It listens on localhost only.

Needs **Python 3.11+**, the same floor as the rest of PaperPull.

Closing the browser tab stops the run it was showing. That is deliberate: a
downloader driving your signed-in browser should not keep going once nothing
is watching it. Nothing is lost — a document is only marked done after it is
saved, so the next run picks up exactly where this one stopped.

Apps run from the panel get no stdin, so nothing can stop and wait for an
answer nobody is able to type. One thing follows from that. An app asks for
the account holder's name on its first run, and it cannot ask here, so that
stays unset and the index CSV's **Account Holder** column stays blank. Set it
by running the app once from a terminal, or by putting `"owner"` in its
`config.json`.

Or manually:

```bat
python -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt
python -m uvicorn app:app --port 8765
```

## In Docker

The panel is the web UI of the container stack — see
[docs/docker.md](../docs/docker.md). Two environment variables change how it
behaves there, and both default to the native behaviour above:

| Variable | Effect |
|---|---|
| `PAPERPULL_ALLOWED_HOSTS` | Extra hostnames the page may be served from, comma-separated. Behind a reverse proxy this is required: without it every request from your panel's hostname is refused as cross-origin. Exact matches; localhost is always allowed. |
| `PAPERPULL_REMOTE_BROWSER` | The sign-in browser is in another container. **Login** then means "attach and report whether I'm signed in" (`--login`) rather than "open a window" (`--open-browser`) — there is no display here to open one on. Also stops the panel warning about missing per-app `.venv`s, since the image runs every app on one interpreter. |
| `PAPERPULL_BROWSER_URL` | Public URL of the browser desktop. Adds an "Open browser desktop" link to the panel. Cosmetic. |
| `PAPERPULL_CONFIG_ROOT` | Read each app's `config*.json` from `<root>/<app>/` instead of the app's own folder, and pass it as an absolute `--config`. In the image the app folders are part of the image, so a config kept there would not survive a rebuild — and not writing into them is what lets the container run as any uid. Accounts are read per request, so a new `config.<name>.json` appears without a restart. |

## Tests

```bash
python -m pytest tests -q
```

`tests/test_remote_browser.py` covers both switches, in both directions — it
fails if the Docker behaviour breaks *or* if native behaviour changes.

## Which apps does it drive?

By default it discovers the apps in `../apps`. To drive your **existing working
copies** instead (with their venvs, configs, and signed-in profiles already set
up), set `APPS_ROOT` first:

```bat
set APPS_ROOT=C:\path\to\Receipt and Statement Downloader
run_gui.bat
```

It finds any subfolder containing an entry script (`*_receipts.py` /
`*_docs.py`), so it works with either the `apps/<slug>` layout or the original
`Provider Name/` folders. Each app runs with its own `.venv` if present.

## Actions

| Button | What it runs |
|--------|--------------|
| **Login** | Opens that app's browser (Chromium, or Edge/Chrome for bot-protected sites) — **you** sign in and leave it open |
| **Discover** | Enumerate available documents (downloads nothing) |
| **Pilot** | Download the newest few as a test |
| **Run All** | Download everything available (`--yes`, no prompt) |
| **Resume** | Continue an interrupted run |
| **Verify** | Re-check the downloaded PDFs |

## Notes & limits

- **Login is human-driven.** The panel opens the browser; you handle sign-in and
  2FA yourself. That's by design — the tools never touch your password.
- If a run hits a mid-run "please sign in again" prompt (e.g. an expired
  session), it can't answer from here — it will end. Just Login again and Resume.
- One app needs its `.venv` set up (run its `setup.bat` once) before the panel
  can run it; the UI warns when a venv is missing.
