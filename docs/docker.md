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
     ┌────────▼─────────┐            ┌───────────▼──────────────┐
     │    paperpull     │            │        browser           │
     │ panel :8765      │            │ Chrome + web desktop     │
     │ the 16 apps      │            │ :3000  (you sign in here)│
     │ socat :9222 ─────┼──────┐     │ Chrome's DevTools port   │
     └──────────────────┘      │     │ on 127.0.0.1:9222        │
              │                │     └───────────▲──────────────┘
              │                └────► browser:9223 ──┘
              │                      ┌──────────────────────────┐
              │                      │  cdp-bridge (socat)      │
              │                      │  in browser's netns      │
              │                      └──────────────────────────┘
      ./config  ./data                  ./browser-profile
                    └── pw-artifacts (shared) ──┘
```

## Setup

You need Docker with Compose, a reverse proxy, and a `proxy` network the proxy
already uses.

```bash
git clone git@github.com:zjean/paperpull.git && cd paperpull
cp .env.example .env
$EDITOR .env                    # BROWSER_PASSWORD and PAPERPULL_ALLOWED_HOSTS
docker network create proxy     # if you don't have one already
docker compose up -d
```

Add the two blocks from [`docker/Caddyfile.example`](../docker/Caddyfile.example)
to your Caddyfile. Then:

1. Open **`browser.<you>`**, log in with `BROWSER_USER` / `BROWSER_PASSWORD`.
   You get a real Chrome. Sign in to a provider — 2FA, device approval, bank
   app, DigiD, whatever it takes. Leave the tab open.
2. Open **`paperpull.<you>`**, pick that app, and press **Login**. It should say
   it connected and can see your documents page.
3. **Pilot**, then **Run All**.

Your PDFs land in `./data/<app>/`, alongside that app's `progress.json` and
index CSV.

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
picks the account up on its next load.

### Verify it works

```bash
docker compose exec paperpull python /app/tools/docker_smoke.py
```

Four checks, ending with a real download across the container boundary. Run it
after any change to the compose file, and after merging upstream.

## Four things Chrome and Playwright do that shape all of this

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

Chrome ignores `--remote-debugging-address`. The port is reachable only from
inside the browser container's network namespace — which is why `cdp-bridge`
exists and why it uses `network_mode: service:browser`. That is the only
vantage point from which `127.0.0.1:9222` can be forwarded outward.

Consequence: `cdp-bridge` has no hostname of its own, and restarting `browser`
invalidates its network namespace. Restart the two together:

```bash
docker compose restart browser cdp-bridge
```

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
  it. Hence `PUID=1000` on `browser` and `user: "1000:1000"` on `paperpull`.

`tools/docker_smoke.py` checks exactly this, by weighing a PDF of known size.

## Operating it

| | |
|---|---|
| Logs | `docker compose logs -f paperpull` |
| One-off run | `docker compose run --rm paperpull python apps/ally/ally_docs.py --discover` |
| Update | `docker compose pull && docker compose up -d` |
| Restart the browser | `docker compose restart browser cdp-bridge` (both — see #2) |
| Tests | `docker compose exec paperpull python -m pytest core/tests gui/tests -q -p no:cacheprovider` |

Images publish from CI: `:latest` from `main`, `:beta` from `develop`, and a
`:sha-<short>` on every build so you can pin back.

### What is on which volume

| Path | Holds | |
|---|---|---|
| `./browser-profile` | the signed-in Chrome profile | **secret** — live session cookies. Back it up; losing it means signing in everywhere again. |
| `./config` | per-app `config.json` | seeded on first run, then yours; never overwritten |
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

## Security

The native design is "localhost only, no credentials anywhere". This is not
that, and it is worth being clear-eyed about the difference — see the Docker
section of [SECURITY.md](../SECURITY.md).
