# PaperPull — Control panel (GUI)

A small local web UI over every downloader app. It opens on the account that
most needs you, says what it needs and why, and tells you which action to
press. It only ever runs the same predefined commands the `.bat` files do —
nothing from the page is passed to a shell.

![PaperPull control panel — the register on the left, one account and its next step on the right](../docs/control-panel.png)

## What you are looking at

**The register**, on the left, is every app/account pair you have, on one line
each, grouped by what it needs and ordered so the first row is the one to open:

| Group | Means |
|---|---|
| **Needs a sign-in** | The provider signed this session out. Nothing runs until someone signs in again. |
| **Needs confirming** | No identity recorded yet. Runs refuse to file documents until you confirm which account this is. (Simyo and Youfone only — see below.) |
| **Ready** | Nothing is blocking it. Whether it is worth running *now* is on the account itself and in the masthead's count, not sorted into the list. |

Two kinds of account are **not** in that list, behind a **`N not in use ·
show`** line under it instead:

- **Never used** — there is a `config.json`, but PaperPull has never signed in
  to the account or downloaded anything for it. In the container this is most
  of them: the entrypoint seeds a config for every app in the image, so a
  household with two providers gets eighteen accounts whether it wants them or
  not.
- **Not set up yet** — no `config.json` at all. This is the native default for
  every provider you have not touched.

Being new is never on its own grounds for hiding an account: an account that
needs a sign-in or needs confirming stays in the register however fresh it is.
A freshly configured account has never run *by definition*, and the obvious
rule ("hide what has never been used") would have hidden the very account you
were in the middle of setting up.

Typing in **Filter** searches all of them, hidden ones included, and the
account you have open is always drawn in the list wherever it lives.

**The bench**, on the right, is one account: its session, its identity, its
newest document, when it was last looked at — and then **What now**, the
ordinary path through a provider as numbered steps. Exactly one of them is
ever highlighted, and only when there is a real next step to take. Anything
that is not part of that path folds away under *Other things you can run*.

**The console** sits underneath, a strip until something runs and most of the
page while it does.

**+ Add an account** creates one account's config file — the panel's only
write. Two things go through it, and they are the same operation:

- **a provider with no account yet.** Copies its tracked
  `config.example.json` to `config.json`. Nothing is downloaded and nothing is
  signed in to; the account appears in the register and you sign in from there.
- **a second person's account of a provider you already use.** Copies *your*
  config — including anything you changed in it — and then gives the new
  account its own `output_dir`, its own `profile_dir` inside it, and (natively)
  its own debugging port, so the two never share a download history or a
  signed-in session. Two accounts sharing an `output_dir` share `progress.json`
  and `sentinel.json`, so each would keep overwriting the other's record of
  what it had downloaded. This is the same rule each app's own
  `add_account.py` applies.

  In the container the port is left alone: there is one browser and every
  app's `cdp_url` points at it on purpose. A second person needs a second
  `browser` service, and the panel says so when it creates the file.

The label becomes a filename, so it is refused rather than rewritten: 1–32
characters of lowercase `a-z`, `0-9`, `-` or `_`. An existing config is never
overwritten, and the form says so before you press anything — each provider is
listed with how many accounts it already has, and a label that is taken
disables **Create it** rather than letting the server refuse it.

### Two accounts of one provider, and finding the right tab

Worth knowing, because nothing outside the panel warns about it.

Every app finds its tab the same way: **the first live tab whose address
matches the provider's host** (`simyo_site.find_signed_in_page`, `amex_docs`'
`amex[0] if amex else …`, and the same shape in all eighteen). Nothing ties
that choice to a config. And the account holder stamped on every PDF and every
index-CSV row comes from the config file, never from the page
(`storage.ensure_owner`) — so a run that reads the wrong tab files those
documents under the wrong person.

- **Natively this cannot happen by accident.** Each account gets its own
  `profile_dir` on its own `cdp_url` port, so each has its own browser with its
  own cookie jar, and only one account of a provider is ever signed in per
  browser. "The first matching tab" has exactly one candidate. That is what the
  port stepping in Add an account is for.
- **In Docker it is the default,** because there is one Chrome and every app's
  `cdp_url` points at it. For two *different* providers that is fine and
  intended — different hosts, different tabs. For two accounts of *one*
  provider it is not: `simyo` and `youfone` catch it (the identity gate refuses
  rather than misfiling), and the other sixteen apps do not check at all. The
  fix is a second `browser` service with its own bridge port, with that
  account's `config.<name>.json` pointing at it — see
  [docs/docker.md](../docs/docker.md), *One shared browser*.

The panel flags it either way: any account sharing a browser with another
account of the same provider gets a **shared browser** marker in the register
and the whole explanation on the account itself. It is the one warning here
about something that does not fail — the run succeeds, which is what makes it
worth a warning.

**Start a sitting** walks every account whose session is too perishable for
the scheduler to catch alive, one at a time, most perishable first: it asks
you to sign in, waits, runs, tears down, then asks about the next one. The
queue is on screen the whole time, so you can see what you are in for before
you start and where you are while it runs.

## Run it

```bat
run_gui.bat
```

That creates a small venv (FastAPI + uvicorn), starts the server, and opens
<http://127.0.0.1:8765>. It listens on localhost only.

Needs **Python 3.11+**, the same floor as the rest of PaperPull.

Or manually:

```bat
python -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt
python -m uvicorn app:app --port 8765
```

## The actions

| Step | Button | What it runs |
|---|---|---|
| 1 | **Sign in** | Opens that app's browser (Chromium, or Edge/Chrome for bot-protected sites) — **you** sign in and leave it open |
| 2 | **Confirm this account** | Records which account the signed-in tab belongs to. Simyo and Youfone only; once per account |
| 3 | **Try a few** | Downloads the newest handful, as a test |
| 4 | **Download everything new** | Downloads everything you do not already have (`--yes`, no prompt) |
| — | **List what is there** | Enumerates available documents; downloads nothing |
| — | **Continue last run** | Picks an interrupted run up where it stopped |
| — | **Re-check saved files** | Re-reads the PDFs already on disk; opens no browser |

Only the actions an app's own entry script accepts are offered — `--adopt-identity`
exists on two of the eighteen, and the panel reads each script to find out
rather than keeping a second list that could drift.

## Notes & limits

- **Signing in is yours.** The panel opens the browser; you handle sign-in and
  2FA. That's by design — the tools never touch your password.
- **Closing the browser tab stops the run it was showing.** Deliberate: a
  downloader driving your signed-in browser should not keep going once nothing
  is watching it. Nothing is lost — a document is only marked done after it is
  saved, so the next run picks up exactly where this one stopped.
- **A run can stop and ask you something** — a provider signed you out, a
  provider wants you to prove you are human, or the re-check pass is offering
  to relabel a receipt. The question appears under the console, with a box to
  answer it and **Continue** for the ones that just want a keypress. Fix
  whatever it asked about in the browser first; nothing is lost while it waits,
  and **Stop this run** ends one you would rather not finish.
- **Safe to re-run.** Download everything new and Continue last run skip any
  statement or receipt you already have — nothing is ever fetched twice, even
  if you deleted the PDFs after importing them elsewhere.
- **The account holder stays unset.** What the panel gives a run is a pipe, not
  a terminal, and one thing follows from that: an app asks for the account
  holder's name on its first run only on a real console, so here it does not
  ask at all. The index CSV's **Account Holder** column stays blank until you
  set it by running the app once from a terminal, or by putting `"owner"` in
  its `config.json`.
- **A missing `.venv`** is called out on the account it affects; run that app's
  `setup.bat` once.

## In Docker

The panel is the web UI of the container stack — see
[docs/docker.md](../docs/docker.md). Four environment variables change how it
behaves there, and all default to the native behaviour above:

| Variable | Effect |
|---|---|
| `PAPERPULL_ALLOWED_HOSTS` | Extra hostnames the page may be served from, comma-separated. Behind a reverse proxy this is required: without it every request from your panel's hostname is refused as cross-origin. Exact matches; localhost is always allowed. |
| `PAPERPULL_REMOTE_BROWSER` | The sign-in browser is in another container. **Sign in** then means "attach and report whether I'm signed in" (`--login`) rather than "open a window" (`--open-browser`) — there is no display here to open one on, and the button's own description says so. Also stops the panel warning about missing per-app `.venv`s, since the image runs every app on one interpreter. |
| `PAPERPULL_BROWSER_URL` | Public URL of the browser desktop. Adds a link to it in the masthead, next to a prompt that needs it, and inside a sitting. |
| `PAPERPULL_CONFIG_ROOT` | Read each app's `config*.json` from `<root>/<app>/` instead of the app's own folder, and pass it as an absolute `--config`. In the image the app folders are part of the image, so a config kept there would not survive a rebuild — and not writing into them is what lets the container run as any uid. Accounts are read per request, so a new `config.<name>.json` appears without a restart. |

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

## How it is put together

`app.py` is the backend and nothing else. The page is three files in
`static/` — `index.html`, `panel.css`, `panel.js` — served with `no-store`,
and read per request, so editing the page is a reload rather than a restart.

The page draws itself from **one** request, `GET /api/state`: the flat account
list, already bucketed and ordered, plus every action's label and the sentence
that explains it. Both the bucketing and the wording of the actions live in
`app.py` on purpose — they are rules about this domain, they have to be the
same for the register and the bench beside it, and there they are testable in
Python rather than in a browser.

Everything in `panel.js` that decides what a person is *told* is a pure
function near the top of the file (`sessionText`, `identityText`, `rowNote`,
`pathFor`, `nextStep`). `tests/test_identity_display.py` executes those
functions directly under Node.

| Route | For |
|---|---|
| `GET /api/state` | everything the page draws itself from |
| `POST /api/accounts` | create one account's config — the only route that writes |
| `GET /api/apps` | the older app list, unchanged in shape |
| `GET /api/due` | who is due, most perishable first — the same answer `tools/due.py` prints |
| `GET /api/run` | start one action; server-sent events carry its output |
| `POST /api/answer` | one line to a run blocked on `input()` |
| `POST /api/stop` | end a run |

## Tests

```bash
python -m pytest tests -q
```

`tests/test_remote_browser.py` covers both Docker switches, in both directions
— it fails if the Docker behaviour breaks *or* if native behaviour changes.
`tests/test_state.py` covers the buckets, the ordering, and the rule that no
action may ship as a bare verb with no sentence explaining it.
`tests/test_add_account.py` covers the one writing route: what it refuses (an
app that is not there, a label that would not be a safe filename, an account
that already exists) and what it writes (a second account's own folder,
profile and port).
