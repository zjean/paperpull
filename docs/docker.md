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

Two containers. The bridge into Chrome's DevTools port is an s6 service inside
the browser rather than a container of its own, so its lifecycle is the
browser's — there is no third thing to remember to restart.

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

If you would rather not grant it, Selkies' own side panel is the way through:
open it, expand **Clipboard**, paste into the **Server Clipboard** box, then
click outside the box. The blur is what sends the text to the session, where a
paste then finds it. Tedious, but it needs no permission.

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

## Limits

- **No stdin.** Same as the native panel: an app that needs to ask something
  mid-run ends instead of hanging. If a session expires during a run, sign in
  again on the desktop and press **Resume** — nothing is lost, because a
  document is only marked done once it is saved.
- **The account holder stays unset.** An app asks for it on a first
  interactive run and cannot ask here, so the index CSV's *Account Holder*
  column stays blank. Put `"owner"` in the app's `config.json` instead.
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
