# Running PaperPull in Docker

The panel becomes a web UI you reach from anywhere on your network, and the
sign-in browser becomes a Chrome desktop in its own container that you sign in
to from a browser tab. Nothing else about PaperPull changes: you still sign in
yourself, it still attaches over the DevTools protocol afterwards, and it still
never sees a password.

```
                    your reverse proxy
                    ┌──────────────┴──────────────┐
        paperpull.<you>                    browser.<you>
              │                                  │
     ┌────────▼─────────┐            ┌───────────▼───────────────┐
     │    paperpull     │            │         browser           │
     │ panel  :8765     │            │ Chrome + web desktop      │
     │ the 16 apps      │            │ :3000  (you sign in here) │
     │                  │            │                           │
     │ socat  :9222 ────┼───────────►│ socat  :9223              │
     └──────────────────┘            │   └──► 127.0.0.1:9222     │
              │                      │        Chrome's DevTools  │
              │                      └───────────────────────────┘
      ./config  ./data                  ./browser-profile
                    └── pw-artifacts (shared) ──┘
```

Two containers, plus an optional `scheduler` — the same image as `paperpull`
again, running one unattended pass a day and serving nothing (see
[Scheduling](#scheduling)). The bridge into Chrome's DevTools port is an s6
service inside the browser rather than a container of its own, so its
lifecycle is the browser's — there is nothing extra to remember to restart.

## Setup

You need Docker with Compose, a reverse proxy, and a `proxy` network the proxy
already uses.

```bash
git clone https://github.com/zjean/paperpull.git && cd paperpull
cp .env.example .env
$EDITOR .env          # PUID/PGID, BROWSER_PASSWORD, PAPERPULL_ALLOWED_HOSTS

# Docker creates a missing bind mount as root, and neither container runs as
# root, so these have to exist and belong to PUID:PGID before the first start.
mkdir -p config data browser-profile
sudo chown -R "$(id -u):$(id -g)" config data browser-profile

docker network create proxy     # if you don't have one already
docker compose up -d
```

Set `PUID`/`PGID` to your own `id -u` / `id -g` and everything the stack writes
is readable on the host without `sudo`. Both containers take the same pair,
which is not a convenience — see #4.

Deploying to a real server, including publishing the images and pointing a
proxy at them, is walked through in the [README](../README.md#deploy-it-to-a-server).

Add the two blocks from [`docker/Caddyfile.example`](../docker/Caddyfile.example)
to your Caddyfile. Then:

1. Open **`browser.<you>`**, log in with `BROWSER_USER` / `BROWSER_PASSWORD`.
   You get a real Chrome. Sign in to a provider — 2FA, device approval, bank
   app, DigiD, whatever it takes. Leave the tab open. Pasting a password in
   there needs one browser permission first — see below.
2. Open **`paperpull.<you>`**, pick that app, and press **Login**. It should say
   it connected and can see your documents page.
3. **Pilot**, then **Run All**.

Your PDFs land in `./data/<app>/`, alongside that app's `progress.json` and
index CSV.

### Pasting into the desktop

Out of the box, copying on your machine and pasting in that Chrome pastes
nothing — silently, with no error anywhere. The keyboard is not the problem:
Selkies already remaps ⌘ to Ctrl for macOS clients, so ⌘V arrives as Ctrl+V.
The session's clipboard is simply empty.

Selkies pushes your clipboard into the session only while the browser you are
*viewing* the desktop from reports the `clipboard-read` permission as
`granted` — and it only ever queries that permission, never asks for it.
Chrome's default is `prompt`, and the focus-time `navigator.clipboard.read()`
that would otherwise trigger a prompt is refused outright without one. So the
sync never starts, and both ⌘V and right-click → Paste have nothing to paste.

Grant it once, in the browser you view the desktop *from*:

1. On the desktop tab, click the icon left of the URL → **Site settings** →
   **Clipboard** → **Allow**.
2. Copy something, then click back into the desktop tab. Selkies syncs on the
   tab *regaining focus*, so that click is what actually sends it.
3. Paste inside the desktop. ⌘V, Ctrl+V and right-click → Paste all work now.

The permission is per origin and needs HTTPS, which is another reason the
desktop goes behind the proxy: over plain `http://` on anything but
`localhost`, Chrome offers no Clipboard setting at all and none of this can
work.

If you would rather not grant it, Selkies' own side panel is the way through.
It is hidden by default and there is no menu bar to find it in: press
**Ctrl+Shift+M** (a real Ctrl, even on a Mac), or click the 15px-wide strip at
the *vertical middle of the left edge* of the page. The hotkey is ignored while
the page is in fullscreen, so leave fullscreen first — Ctrl+Shift+F toggles it.

With the panel open, expand **Clipboard**, paste into the **Server Clipboard**
box, then click outside the box. The blur is what sends the text to the
session, where a paste then finds it. Tedious, but it needs no permission.

Either way, know what you are enabling: with the permission granted, everything
you copy while that tab has focus is pushed into the container — see the Docker
section of [SECURITY.md](../SECURITY.md).

### One shared browser

Upstream gives every app its own browser profile and its own debugging port, so
several signed-in windows can sit open at once. Here there is **one** browser
and every app's `cdp_url` points at it. That works because a browser holds many
signed-in sites at the same time, and because each app finds its own tab by URL
rather than grabbing whichever tab is first. Sign in to as many providers as you
like in that one Chrome and leave the tabs open.

For a second person's accounts, add a second `browser` service with its own
`/config` volume and its own bridge port, and point that account's
`config.<name>.json` at it. Drop the file into `./config/<app>/` and the panel
offers that account immediately — it reads the directory on each request, so no
restart is needed.

### First run after upgrading: adopt each account's identity

**Applies to Simyo and Youfone today — the only two apps whose entry script
understands `--adopt-identity`.** The other apps here still file whichever
tab matching the provider's host they find, exactly as they did before this
section existed; this identity gate is rolled out one provider at a time
(see the spec, "Phase 1's identity gate covers Simyo only"), and the panel
only shows the **Adopt identity** button for an app that actually accepts
it. If you don't see the button for some other provider, that isn't a bug —
there is nothing to press yet.

If you already had Simyo or Youfone running before this version, do this
once per account before anything else.

A run of one of these apps now refuses to file documents until it can prove
that the tab it is reading really is the account its config names. One
Chrome holds every provider's session here, and a tab is found by matching
the provider's host — so with two accounts of the same provider signed in, a
run could otherwise read the wrong tab and file its documents under this
config's owner, silently. The proof is a fingerprint of documents the
account is *known* to own, and on an existing install nothing has recorded
one yet, so every action that files a document refuses with `Cannot tell
which account this tab belongs to`.

Recording it is deliberately a thing you do, watching, once — though "once"
is not forever for every provider: Youfone shows only six months of invoice
history (half Simyo's twelve), so an account of it left unrun for six months
or more will have every recorded anchor age out and need this same procedure
again, refusing with `(aged)` in the message until you do.

1. Sign in to the provider on the browser desktop, in **one** tab, and check
   the page really shows the account this config is for.
2. In the panel, pick that app (Simyo or Youfone) and account and press
   **Adopt identity**.
3. Read back what it recorded — the panel lists the anchors under the account
   picker, and it is the one moment those are taken on trust. If they are not
   this account's documents, you adopted the wrong tab: sign in to the right
   account and press it again.

Or from a shell, the same thing (substitute `youfone`/`youfone_docs.py` for a
Youfone account):

```bash
docker compose run --rm paperpull \
    python apps/simyo/simyo_docs.py --discover --adopt-identity \
    --config /config/simyo/config.json
```

It is never done for you, and never done unattended: an unattended run that
cannot prove an identity parks the account and exits 0, so it waits for you
rather than guessing.

### Scheduling

The `scheduler` service is a third, optional container. It runs the same
image as the panel and mounts the same `./config` and `./data`, but it has no
web UI and no Docker socket — it invokes an app's CLI directly, the same way
you would type it yourself, once a day.

What it types is `--unattended --all --yes`. `--all` is not an overreach:
it is the only action that asks the provider what exists, where `--resume`
selects from the `discovery.json` an account already has and so could never
fetch an invoice nobody had seen yet. Each app's own "already downloaded"
memory (`progress.json`) skips what is on disk, so a nightly pass is
discover-plus-anything-new. `--yes` answers the confirmation prompt that is
the only reason `--all` ever needed a person in the room.

It reaches the browser exactly the way the panel does, and for the same
reason: the compose file gives it `command`, not `entrypoint`, so the image's
own `docker/entrypoint.sh` still runs first — the same file that opens the
socat bridge to the browser's DevTools port and seeds `/config` for the
panel. Only once that is done does the entrypoint hand off to
`tools/schedule.py`. Overriding the entrypoint instead would start the
scheduler with no route to the browser at all, silently, since nothing here
would fail until an app actually tried to attach.

It only starts a provider whose session lasts for days
(`session_lifetime_minutes` is `None` in that app's `storage.py`) *and* whose
entry script already understands `--unattended` — detected by reading the
script's own text for the flag, the same trick the panel uses to detect
`--open-browser`. That second condition matters on its own: `--unattended` is
added to an app one provider at a time, in a later change, once someone
actually wants that provider scheduled. Until then the scheduler skips it and
says so on one line — `does not support --unattended yet` — rather than
either crashing or pretending it ran.

**Youfone is the first provider this actually pulls.** It is patient
(`session_lifetime_minutes` is `None` — nobody has confirmed how long a
MyYoufone tab survives idle, so it is treated as "days" until proven
otherwise) and its entry script now understands `--unattended`, so a fresh
install's daily pass runs Youfone's accounts for real. Every other patient
app still lacks the flag today and is skipped with that one-line explanation,
for every account, every day, until a later change adds it there too — which
is correct, not broken, and not the same "runs nothing" state this section
described before Youfone gained it.

**Simyo can never be in that first group.** Its session lasts ten minutes,
which is shorter than a scheduled pass can rely on finding it alive; a
provider that declares a session lifetime is never started here, on any pass,
no matter what flags its script has. Instead it is printed as
`waiting for a person` — the scheduler's way of telling you, and whatever
reads its log, that this account still needs the panel's **Pilot** or
**Run All** with you sitting at the browser desktop.

Set `PAPERPULL_SCHEDULE_HOUR` in `.env` to the local hour (0-23, `TZ` already
set above) you want the daily pass to run. Pick one you are actually awake
for: a run that parks an account or lists one as waiting is only actionable by
a person, and 3am is not when you read logs. A value outside 0-23 is refused
on startup with a sentence, rather than accepted into a comparison that can
never be true — a scheduler that runs forever and does nothing.

**How often an account is even considered** is `cadence_days`, and it goes in
that account's own config — the same file as `output_dir`, i.e.
`./config/<app>/config.json` (or `config.<account>.json` for a second
account):

```json
{
  "output_dir": "/data/simyo",
  "cadence_days": 31
}
```

It is the number of days that must pass after the newest document already
downloaded before this account is due again. The default is 31, which suits a
monthly biller. Widen it for a provider that is annoying to sign in to — a
statement you only need every other month is two sittings a year instead of
twelve — and narrow it for one that posts documents weekly. An account is
always due if it has never run, and a parked account is always listed
regardless, because only a person can un-park it.

The `scheduler` service has no healthcheck: it serves no HTTP, so the image's
own check (which curls the panel) would report it permanently unhealthy.
`docker compose logs scheduler` is what says whether it is working.

To see today's plan without waiting for the schedule, or to check what a
parked account needs:

```bash
docker compose exec scheduler python /app/tools/due.py
```

This lists every due account, most perishable session first, and marks a
parked one `PARKED - needs sign-in`. **A parked account is a state, not a
failure.** It means an unattended run found the session already gone and
exited 0 rather than guessing at a login — nothing crashed, nothing needs
fixing in code. Open the browser desktop, sign back in to that provider, and
the next pass — scheduled or a manual `--once` — picks it up from where it
left off.

#### Notifications

A parked account, or a perishable one that has come due, is invisible unless
you go looking — `tools/due.py` will say so, and `docker compose logs
scheduler` will scroll past it, but neither one taps you on the shoulder. Set
`PAPERPULL_NTFY_URL` in `.env` to a topic URL — e.g.
`https://ntfy.sh/paperpull-8f3c1a9e7b` — and the scheduler will push there
instead of leaving it to whoever next thinks to check. Left empty, which is
the default, nothing is sent; `PAPERPULL_NTFY_TOKEN` is only for a topic that
requires a bearer token, which ntfy.sh topics do not by default.

A message names the provider, the account label, and why, one line per
account, e.g.:

```
PaperPull: 1 needs you

Needs you:
  youfone/primary - no signed-in tab
```

One message per pass, sent only when there is something to say — not one push
per account, and not a daily "all fine" you would learn to ignore within a
week.

What it will **not** notify about, and why:

- **A clean pass, or a quiet one.** Nothing parked, nothing errored, nothing
  due — there is nothing to act on, so there is nothing to send.
- **A run you started yourself**, from the panel's Pilot/Run All or a bare
  `python youfone_docs.py` at a terminal. You are looking at that output
  already; a push about something on your own screen is noise, not a signal.

Before you point this at anything, read the *Notifications* section of
[SECURITY.md](../SECURITY.md#notifications): on ntfy.sh the topic name is the
only thing standing between a stranger and every message you have ever sent,
including which providers you use.

### Restarting things

```bash
docker compose restart browser    # that's all — nothing else needs restarting
```

The panel's socat resolves `browser` per connection, so it reconnects on its
own. Your signed-in sessions survive, because they live in the Chrome profile on
`./browser-profile`, not in the container.

### Verify it works

```bash
docker compose exec paperpull python /app/tools/docker_smoke.py
```

Four checks, ending with a real download across the container boundary. Run it
after any change to the compose file, and after merging upstream.

## Five things Chrome and Playwright do that shape all of this

Each of these was found the hard way, and each fails *silently*. If you change
the compose file or the Dockerfile, this is the list to check against.

### 1. Chrome will not enable remote debugging on a default profile

Chrome 136 and later ignore `--remote-debugging-port` when the profile is the
default user-data directory. It is an anti-cookie-theft measure, and it is
silent: no error, no log line, no `DevToolsActivePort` file, the port simply
never opens.

`linuxserver/chrome` launches Chrome with a bare, valueless `--user-data-dir`,
which counts as the default. So `CHROME_CLI` **must** contain an explicit one:

```
CHROME_CLI=--user-data-dir=/config/pp-profile --remote-debugging-port=9222 ...
```

Remove that and the whole stack stops working with no diagnostic anywhere.

### 2. That port only listens on 127.0.0.1

Chrome ignores `--remote-debugging-address`. The DevTools port is reachable only
from *inside* the browser container, so something in there has to forward it
outward. That is what `svc-cdp-bridge` does — a socat service added by
`docker/browser/Dockerfile` and supervised by the image's s6, listening on 9223
and forwarding to `127.0.0.1:9222`.

It is an s6 service rather than a `/custom-cont-init.d` script because init
scripts are expected to run to completion; a process backgrounded from one is
unsupervised and never comes back if it dies.

It was originally a separate container sharing the browser's network namespace,
which worked but meant restarting the browser stranded it in a namespace that no
longer existed — so the two always had to be restarted together. Inside the
browser, its lifecycle is simply the browser's. `docker compose restart browser`
is the whole story, and the stack smoke test asserts it.

### 3. Chrome rejects a Host header that isn't localhost or a bare IP

Ask for `/json/version` with `Host: browser:9223` and Chrome answers, verbatim:

> Host header is specified and is not an IP address or localhost.

socat is a byte pipe, so it forwards the Host header untouched. That is why
there is a socat on *both* ends rather than one: the panel dials
`http://localhost:9222` — its own local socat — so the header Chrome finally
sees says `localhost:9222`, and every app's `cdp_url` stays exactly what it is
on a native install.

### 4. A download across containers silently loses its bytes

The dangerous one. Attaching with `connect_over_cdp` leaves no Playwright
server on the browser's side, so Playwright cannot stream a finished download
back to us. It assumes the two share a filesystem and copies from a local path.
Cross-container, `download.save_as()` **reports success and writes zero bytes**
— every statement would look like it downloaded, and every PDF would be empty.

Two things fix it, and both are required:

- **`TMPDIR=/pwtmp` on a volume both containers mount.** Playwright's driver
  derives the artifact directory from `TMPDIR` and tells Chrome to write there,
  so pointing both at the same real directory makes the copy a local one.
- **The same uid on both sides.** That artifact directory is created mode
  `0700`, and Chrome — running as the other container's user — has to write into
  it. So `browser`'s `PUID`/`PGID` and `paperpull`'s `user:` read the same two
  variables, and a deployment can pick any uid as long as it picks one. The
  browser's init also hands the shared `/pwtmp` volume to that uid, since a
  named volume otherwise starts out owned by root.

`tools/docker_smoke.py` checks exactly this, by weighing a PDF of known size.

### 5. Chrome will not open a profile another host has locked

Chrome records its profile lock as a symlink, `SingletonLock -> <hostname>-<pid>`.
If that hostname is not its own it cannot tell whether the owning process died,
so it assumes the profile is in use elsewhere and exits — with nothing in any
log, and no window.

A container gets a fresh hostname on every recreate. So a persisted profile
becomes unopenable the *second* time you bring the stack up, which is a
memorable way to lose an afternoon. Hence:

```yaml
hostname: paperpull-browser
```

Pinned, the hostname always matches and Chrome's ordinary stale-PID recovery
does the right thing. The browser image also clears a lock naming a *different*
host at init, so if that pin is ever removed the failure is a log line rather
than silence.

## Operating it

| | |
|---|---|
| Logs | `docker compose logs -f paperpull` |
| One-off run | `docker compose run --rm paperpull python apps/ally/ally_docs.py --discover` |
| Update | `docker compose pull && docker compose up -d` |
| Restart the browser | `docker compose restart browser` |
| Tests | `docker compose exec paperpull python -m pytest core/tests gui/tests -q -p no:cacheprovider` |

Two images publish from CI — `paperpull` (panel and apps) and
`paperpull-browser` (linuxserver/chrome plus the bridge service). `:latest` from
`main`, `:beta` from `develop`, and a `:sha-<short>` on every build so you can
pin back. The browser image also rebuilds weekly, so it tracks new Chrome
releases instead of quietly pinning you to an old one.

### What is on which volume

All of it is owned by `PUID:PGID`, so it is yours to read and back up on the
host without `sudo`.

| Path | Holds | |
|---|---|---|
| `./browser-profile` | the signed-in Chrome profile | **secret** — live session cookies. Back it up; losing it means signing in everywhere again. |
| `./config` | per-app `config.json` | seeded on first run, then yours; never overwritten. The panel passes an absolute `--config`, so nothing is linked into the app directories and they stay read-only. |
| `./data` | PDFs, index CSVs, `progress.json` | **secret** — your statements. Also what makes a re-run delete-safe. |
| `pw-artifacts` | in-flight downloads | disposable |

Nothing here is committable, and `.gitignore` and `.dockerignore` both block
all of it.

### Answering a prompt mid-run

Providers sign you out. Amazon asks whether you are a robot. When that happens
the app stops and asks for a keypress, and natively you give it one in the
console window the launcher opened. There is no such window here, so the panel
is what answers instead: the question appears under the console, with a box to
type in and a **Continue** button for the ones that only want Enter. When the
question is about your session, a link to the browser desktop appears next to
it — go and fix it there, then press Continue. The run picks up mid-flight; it
does not start over.

Three prompts reach you this way. A **sign-out** and a **security challenge**
pause any run. The **`--verify` pass** asks once per receipt whose summary it
is unsure about, and typing a better one renames the PDF and rewrites the index
row — a whole feature that simply had no way to work here before.

Two consequences worth knowing:

- A run that is waiting waits indefinitely; nothing times it out. **Stop run**
  is how you end one, and stopping is safe — a document is only marked done
  once it is saved, so a later run re-fetches nothing.
- Answering is one line per question. A pasted line break is collapsed to a
  space rather than being sent as a second answer to whatever the app asks
  next.

Under the hood the run gets a pipe on stdin, and the panel reads its output as
raw chunks rather than whole lines. Both are necessary: `input()` writes its
prompt with **no trailing newline**, so a line-based reader waits for a line
that never arrives and the page shows a run that started and then went silent.
That is precisely what a mid-run sign-out used to look like.

## Limits

- **The account holder stays unset.** An app asks for it only on a real
  console, and a pipe is not one, so the index CSV's *Account Holder* column
  stays blank. Put `"owner"` in the app's `config.json` instead.
- **Bot-protected providers are untested here.** `walmart` and `verizon` need a
  branded browser, which this stack does have — it is real Google Chrome, not
  the Playwright Chromium. But a Selkies/Wayland desktop fingerprints
  differently than a desktop machine, and whether that passes is unknown.
- **The image is ~490MB, the browser image ~4.5GB.** No `playwright install`
  runs in our image; `connect_over_cdp` needs the driver, not a browser binary.
- **The browser takes a minute or so to be ready** after a cold start: the
  desktop comes up, then labwc autostarts Chrome. Until then `Login` reports it
  cannot connect. That is not an error, just impatience.

## Security

The native design is "localhost only, no credentials anywhere". This is not
that, and it is worth being clear-eyed about the difference — see the Docker
section of [SECURITY.md](../SECURITY.md).
